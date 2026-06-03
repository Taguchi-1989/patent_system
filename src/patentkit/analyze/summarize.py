"""Deterministic (no-LLM, no-key) patent summarization and claim-element split.

This does everything that can be done WITHOUT a language model, so the keyless
pipeline produces maximum value on its own. The semantic step that genuinely
needs judgment — mapping claim elements to a target spec as MATCH/MISSING/
UNCLEAR — is deliberately left to the agent-in-the-loop (Claude Code), which
needs no separate LLM API key.

The claim-element split is a HEURISTIC (splits on common claim delimiters). Per
P-NO-GUESS it is labelled as heuristic so the semantic pass treats it as a
starting point to verify, not ground truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..connectors.base import PatentRecord


@dataclass
class ClaimBreakdown:
    claim_no: int
    text: str
    elements: list[str]            # heuristically split elements
    heuristic: bool = True         # always True here; not LLM-verified


@dataclass
class PatentSummary:
    canonical: str
    title: str
    one_line: str                  # bibliographic one-liner
    claim_count: int
    independent_claim: str         # text of claim 1 (best-effort)
    breakdown: ClaimBreakdown | None
    source: str                    # provenance
    source_url: str | None
    notes: list[str] = field(default_factory=list)


# Common boundaries between claim elements (after the preamble).
_ELEMENT_SPLIT = re.compile(r";\s*|\bwherein\b|\bcomprising\b|\bconfigured to\b|\barranged to\b", re.IGNORECASE)


def split_claim_elements(claim_text: str) -> list[str]:
    """Heuristically break a claim into elements. NOT a legal parse."""
    # Drop a leading claim number ("1. ...") that some sources prepend.
    text = re.sub(r"^\s*\d{1,3}\s*\.\s*", "", claim_text)
    # Drop the preamble up to the first ':' if present (e.g. "... comprising:").
    body = text.split(":", 1)[1] if ":" in text else text
    parts = [p.strip(" ,.;") for p in _ELEMENT_SPLIT.split(body)]
    return [p for p in parts if len(p) > 3]


def summarize(rec: PatentRecord) -> PatentSummary:
    bits = [rec.office, rec.number]
    if rec.assignee:
        bits.append(rec.assignee)
    if rec.pub_date:
        bits.append(rec.pub_date)
    if rec.legal_status:
        bits.append(rec.legal_status)
    one_line = " | ".join(b for b in bits if b)

    independent = rec.claims[0] if rec.claims else ""
    breakdown = None
    if independent:
        breakdown = ClaimBreakdown(
            claim_no=1,
            text=independent,
            elements=split_claim_elements(independent),
        )

    notes = list(rec.notes)
    if not rec.claims:
        notes.append("no claim text available from this source")

    return PatentSummary(
        canonical=rec.canonical,
        title=rec.title,
        one_line=one_line,
        claim_count=len(rec.claims),
        independent_claim=independent,
        breakdown=breakdown,
        source=rec.source,
        source_url=rec.source_url,
        notes=notes,
    )
