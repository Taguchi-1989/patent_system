"""Semantic claim-element comparison: MATCH / MISSING / UNCLEAR.

This module is the M4 product core. It is KEYLESS by design:
  - The Judge protocol allows any implementation to be swapped in.
  - The shipped HeuristicJudge is conservative (token/keyword overlap).
  - An LLM-powered Judge can be provided by the agent-in-the-loop with no
    other code changes.

P-NO-GUESS guarantee (two layers):
  Layer 1 — HeuristicJudge: never emits MATCH without a non-empty
            evidence_span or with overlap_ratio < 0.6.
  Layer 2 — ElementVerdict.__post_init__: mechanically forces any MATCH
            without evidence or with confidence < 0.3 to UNCLEAR.

This means ungrounded verdicts cannot exist in the system regardless of
which Judge implementation is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from .summarize import PatentSummary


# ---------------------------------------------------------------------------
# Verdict enum
# ---------------------------------------------------------------------------

class Verdict(Enum):
    MATCH = "MATCH"
    MISSING = "MISSING"
    UNCLEAR = "UNCLEAR"


# ---------------------------------------------------------------------------
# ElementVerdict — the atomic result unit
# ---------------------------------------------------------------------------

@dataclass
class ElementVerdict:
    """Per-element comparison result.

    P-NO-GUESS is enforced in __post_init__: a MATCH without an evidence span
    or with confidence < 0.3 is automatically demoted to UNCLEAR.
    """

    element: str           # claim element text from ClaimBreakdown.elements
    verdict: Verdict       # MATCH / MISSING / UNCLEAR
    evidence_span: str     # actual substring of target_spec; "" for MISSING
    confidence: float      # [0.0, 1.0]
    rationale: str         # human-readable with token lists and overlap ratio
    needs_review: bool = False

    def __post_init__(self) -> None:
        # Layer-2 P-NO-GUESS enforcement: make it structurally impossible for
        # ANY Judge (including future custom ones) to emit an ungrounded verdict.
        if self.verdict is Verdict.MATCH and not self.evidence_span:
            self.verdict = Verdict.UNCLEAR
            self.needs_review = True
            self.rationale += " [forced UNCLEAR: no evidence span]"
        elif self.verdict is Verdict.MATCH and self.confidence < 0.3:
            self.verdict = Verdict.UNCLEAR
            self.needs_review = True
            self.rationale += " [forced UNCLEAR: low confidence]"
        elif self.verdict is Verdict.MISSING and self.evidence_span:
            # MISSING means "absent"; pointing to overlapping evidence is
            # self-contradictory, so demote to UNCLEAR for human review.
            self.verdict = Verdict.UNCLEAR
            self.needs_review = True
            self.rationale += " [forced UNCLEAR: MISSING contradicted by evidence span]"

        # Always set needs_review when verdict is UNCLEAR (including after forcing).
        if self.verdict is Verdict.UNCLEAR:
            self.needs_review = True


# ---------------------------------------------------------------------------
# coerce_verdict — build a validated ElementVerdict from a loose payload
# ---------------------------------------------------------------------------

def coerce_verdict(
    element: str,
    target_spec: str,
    payload: dict | None,
    label: str = "",
) -> ElementVerdict:
    """Turn a loosely-typed payload into an ElementVerdict, enforcing P-NO-GUESS.

    The payload may come from ANY judgment source that is not the deterministic
    HeuristicJudge — an LLM API (OpenAI/Azure/GitHub Models) OR a subscription
    agent (Claude Code / Copilot) filling a worksheet. Wherever it comes from,
    the SAME guard runs: any ``evidence_span`` that is not a verbatim substring
    of ``target_spec`` is discarded, so a fabricated quote can never survive; an
    ungrounded MATCH is then demoted to UNCLEAR in ElementVerdict.__post_init__.

    Expected payload keys: verdict (MATCH/MISSING/UNCLEAR), evidence_span,
    confidence (0-1), rationale.
    """
    data = payload or {}
    raw = str(data.get("verdict", "")).strip().upper()
    verdict = {
        "MATCH": Verdict.MATCH,
        "MISSING": Verdict.MISSING,
        "UNCLEAR": Verdict.UNCLEAR,
    }.get(raw, Verdict.UNCLEAR)

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    evidence = str(data.get("evidence_span") or "")
    fabricated = bool(evidence) and evidence not in target_spec
    if fabricated:
        evidence = ""
    if verdict is Verdict.MISSING:
        confidence = 1.0 if not evidence else confidence
        evidence = ""

    rationale = str(data.get("rationale") or "").strip()
    prefix = f"[{label}]" if label else ""
    if fabricated:
        prefix = (prefix + " " if prefix else "") + "[捏造引用を破棄: 仕様に逐語一致せず]"
    rationale = f"{prefix} {rationale}".strip()

    return ElementVerdict(
        element=element,
        verdict=verdict,
        evidence_span=evidence,
        confidence=confidence,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# ComparisonResult
# ---------------------------------------------------------------------------

@dataclass
class ComparisonResult:
    """All per-element verdicts for one patent against one target spec."""

    patent_canonical: str
    target_spec_title: str          # first line of spec, truncated to 80 chars
    verdicts: list[ElementVerdict]
    source: str                     # provenance of patent data
    source_url: str | None

    def unclear(self) -> list[ElementVerdict]:
        """Return verdicts that need human review (escalation list)."""
        return [v for v in self.verdicts if v.needs_review]

    def counts(self) -> dict[str, int]:
        """Return a dict of verdict name -> count."""
        result: dict[str, int] = {
            Verdict.MATCH.value: 0,
            Verdict.MISSING.value: 0,
            Verdict.UNCLEAR.value: 0,
        }
        for v in self.verdicts:
            result[v.verdict.value] += 1
        return result


# ---------------------------------------------------------------------------
# Judge Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Judge(Protocol):
    """Semantic judgment protocol.

    Any class that implements judge() satisfies this protocol and can be
    passed to compare() without other code changes. The agent-in-the-loop
    can provide a stronger implementation; the HeuristicJudge ships as the
    keyless default.
    """

    def judge(
        self,
        element: str,
        target_spec: str,
        claim_context: str,
    ) -> ElementVerdict:
        """Judge whether target_spec covers the claim element.

        Args:
            element: The claim element text to evaluate.
            target_spec: Full text of the target technical specification.
            claim_context: Full text of the original claim (surrounding context).

        Returns:
            ElementVerdict with verdict, evidence span, confidence, rationale.
        """
        ...


# ---------------------------------------------------------------------------
# HeuristicJudge — keyless, conservative, evidence-bearing
# ---------------------------------------------------------------------------

# Tuning constants — module-level for easy override in tests/subclasses.
_MATCH_OVERLAP_THRESHOLD = 0.6      # minimum overlap_ratio for MATCH
_MATCH_MIN_TOKENS = 2               # minimum matched tokens for MATCH
_MAX_EVIDENCE_CHARS = 200           # max chars for a combined evidence span

# Stopwords: common patent claim words that do not distinguish elements.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "to", "in", "for", "and", "or", "is", "are",
    "with", "by", "from", "at", "on", "that", "which", "said", "wherein",
    "comprising", "configured", "arranged", "having", "being", "method",
    "apparatus", "system", "device", "according", "claim", "further",
    "first", "second", "third", "one", "each", "between",
})


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, split on whitespace, remove stopwords."""
    lowered = text.lower()
    stripped = re.sub(r"[^\w\s]", " ", lowered)
    words = stripped.split()
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _best_evidence_span(matched_tokens: set[str], target_spec: str) -> str:
    """Find the sentence(s) in target_spec that contain the most matched tokens.

    GUARANTEE: returned span components are actual substrings of target_spec,
    and any multi-sentence span preserves document order.
    """
    if not matched_tokens:
        return ""

    # Split target_spec into candidate sentences/lines, keeping document order.
    raw_sentences: list[str] = []
    for line in target_spec.splitlines():
        line = line.strip()
        if not line:
            continue
        # Further split on ". " within a line.
        for part in re.split(r"\.\s+", line):
            part = part.strip()
            if part:
                raw_sentences.append(part)

    if not raw_sentences:
        return ""

    # Score each sentence by how many matched tokens it contains.
    # Keep (index, score, sentence) to preserve document order later.
    scored: list[tuple[int, int, str]] = []
    for idx, sent in enumerate(raw_sentences):
        sent_tokens = _tokenize(sent)
        score = len(matched_tokens & sent_tokens)
        if score > 0:
            scored.append((idx, score, sent))

    if not scored:
        return ""

    # Find the best sentence by score (keep first occurrence if tie).
    best_entry = max(scored, key=lambda x: x[1])
    best_idx, best_score, best = best_entry

    # Verify the best sentence is actually in the spec.
    if best not in target_spec:
        # Fall back to a raw substring search for the first matched token.
        for tok in sorted(matched_tokens):
            idx = target_spec.lower().find(tok)
            if idx != -1:
                start = max(0, idx - 20)
                end = min(len(target_spec), idx + 80)
                return target_spec[start:end]
        return ""

    # Return the best single sentence as the evidence span.
    # A combined multi-sentence span would not be a literal substring of target_spec
    # (the " ... " separator is not in the original text), which would violate
    # the P-NO-GUESS evidence guarantee. We use a single sentence.
    return best[:_MAX_EVIDENCE_CHARS]


class HeuristicJudge:
    """Conservative token-overlap judge. No LLM, no API key required.

    Returns MATCH only when >= 60% of the element's key tokens appear in the
    spec AND at least 2 tokens match. Returns MISSING when zero overlap.
    Returns UNCLEAR for everything in between.

    This is intentionally conservative: the eval measures the gap honestly.
    An LLM-powered Judge implementing the same protocol will close the gap.
    """

    def judge(
        self,
        element: str,
        target_spec: str,
        claim_context: str,
    ) -> ElementVerdict:
        # Step 1: tokenize both sides.
        element_tokens = _tokenize(element)
        spec_tokens = _tokenize(target_spec)

        if not element_tokens:
            return ElementVerdict(
                element=element,
                verdict=Verdict.UNCLEAR,
                evidence_span="",
                confidence=0.0,
                rationale="element has no key terms after stopword filtering",
                needs_review=True,
            )

        # Step 2: compute overlap.
        matched_tokens = element_tokens & spec_tokens
        overlap_ratio = len(matched_tokens) / len(element_tokens)
        n_matched = len(matched_tokens)
        n_total = len(element_tokens)

        # Step 3: find evidence span (only when there is overlap).
        evidence_span = _best_evidence_span(matched_tokens, target_spec) if matched_tokens else ""

        # Step 4: decide verdict.
        if n_matched == 0:
            # MISSING: no key terms found at all.
            return ElementVerdict(
                element=element,
                verdict=Verdict.MISSING,
                evidence_span="",
                confidence=1.0,
                rationale=(
                    f"no key terms found in spec: [{', '.join(sorted(element_tokens))}]"
                ),
                needs_review=False,
            )

        if overlap_ratio >= _MATCH_OVERLAP_THRESHOLD and n_matched >= _MATCH_MIN_TOKENS:
            # MATCH: sufficient overlap with a real evidence span.
            return ElementVerdict(
                element=element,
                verdict=Verdict.MATCH,
                evidence_span=evidence_span,
                confidence=overlap_ratio,
                rationale=(
                    f"tokens matched: [{', '.join(sorted(matched_tokens))}] "
                    f"({n_matched}/{n_total} = {overlap_ratio:.2f})"
                ),
                needs_review=False,
            )

        # UNCLEAR: partial overlap, insufficient for confident MATCH.
        return ElementVerdict(
            element=element,
            verdict=Verdict.UNCLEAR,
            evidence_span=evidence_span,
            confidence=overlap_ratio,
            rationale=(
                f"partial overlap ({n_matched}/{n_total} = {overlap_ratio:.2f}), "
                f"insufficient for confident match; "
                f"matched: [{', '.join(sorted(matched_tokens))}]"
            ),
            needs_review=True,
        )


# ---------------------------------------------------------------------------
# compare() entry point
# ---------------------------------------------------------------------------

def compare(
    target_spec: str,
    summary: PatentSummary,
    judge: Judge | None = None,
) -> ComparisonResult:
    """Compare a target spec against the independent-claim elements in summary.

    Uses claim-1 elements from summary.breakdown (produced by summarize()).
    When no breakdown exists (no claim text available), returns an empty verdict
    list with a note in the rationale.

    Args:
        target_spec: Full text of the target technical specification.
        summary: PatentSummary produced by summarize().
        judge: A Judge implementation; defaults to HeuristicJudge.

    Returns:
        ComparisonResult with per-element verdicts.
    """
    if judge is None:
        judge = HeuristicJudge()

    # Extract target spec title (first non-empty line, stripped of a leading
    # Markdown heading marker, truncated to 80 chars).
    first_line = next(
        (line.strip().lstrip("#").strip() for line in target_spec.splitlines() if line.strip()),
        "(no title)",
    )
    spec_title = first_line[:80] or "(no title)"

    verdicts: list[ElementVerdict] = []

    if summary.breakdown is None or not summary.breakdown.elements:
        # No claim elements available; return an UNCLEAR verdict as a placeholder.
        verdicts.append(ElementVerdict(
            element="(no claim elements available)",
            verdict=Verdict.UNCLEAR,
            evidence_span="",
            confidence=0.0,
            rationale="no independent claim text was available from this source",
            needs_review=True,
        ))
    else:
        claim_context = summary.independent_claim
        for element in summary.breakdown.elements:
            verdict = judge.judge(
                element=element,
                target_spec=target_spec,
                claim_context=claim_context,
            )
            verdicts.append(verdict)

    return ComparisonResult(
        patent_canonical=summary.canonical,
        target_spec_title=spec_title,
        verdicts=verdicts,
        source=summary.source,
        source_url=summary.source_url,
    )
