"""Deterministic pipeline contract + self-validating gates (自校パイプライン).

WHY THIS EXISTS
---------------
The rest of patentkit is a collection of pure functions. This module pins the
*shape* of how they compose into one pipeline, and makes that pipeline
**self-checking**: every stage declares invariant "gates" that are verified
against its own output on every run. A run therefore *proves* its guarantees
(P-NO-GUESS / provenance / determinism / disclaimer) instead of trusting them.

This is the "決定論的に動かせるところ＋自校" piece:
  - 形を決める  (shape)      → the ordered STAGES contract below.
  - 自校できる  (self-check) → run_selfcheck() executes the deterministic
                               backbone and asserts every gate, returning a
                               machine-readable PipelineReport.

THE DETERMINISM BOUNDARY
------------------------
DETERMINISTIC — pure code, no LLM, no key, byte-reproducible over a frozen
source (FixtureSource / a saved BigQuery export / a downloaded bulk XML):

    normalize → fetch(frozen source) → summarize → compare(HeuristicJudge)
              → snapshot/diff → render

AGENT (the one non-deterministic seam) — a Judge backed by an LLM, swapped in
at compare() through the Judge protocol (analyze/compare.py). It needs no API
key of its own: the agent-in-the-loop (Claude Code) is the judge. Everything
*around* that seam stays deterministic, so the self-check can gate CI.

The self-check runs the DETERMINISTIC backbone only (HeuristicJudge), so its
result is reproducible. The agent seam is documented in STAGES but not
exercised here — there is nothing deterministic to assert about a judgment call.

Pure stdlib on purpose (matches normalize/): runs in a bare Colab cell.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from enum import Enum

from ..analyze.compare import ComparisonResult, HeuristicJudge, Verdict, compare
from ..analyze.summarize import PatentSummary, summarize
from ..connectors.base import PatentRecord
from ..connectors.fixture import FixtureSource
from ..export.markdown import DISCLAIMER, render_report
from ..normalize import CanonicalNumber, normalize
from ..state import SnapshotStore


# ===========================================================================
# Stage shape (the "形")
# ===========================================================================

class StageKind(str, Enum):
    """How a stage produces its output.

    DETERMINISTIC — same input ⇒ byte-identical output, no model, no key.
    AGENT         — a judgment seam where an LLM-backed implementation may be
                    swapped in. Not reproducible; not gated by the self-check.
    """

    DETERMINISTIC = "deterministic"
    AGENT = "agent"


@dataclass(frozen=True)
class Stage:
    """One declarative stage of the pipeline contract.

    This is metadata only — the executable wiring lives in run_selfcheck().
    Keeping the shape declarative lets the report, the docs, and the runner all
    read from a single source of truth.
    """

    id: str
    title: str
    kind: StageKind
    description: str
    gate_ids: tuple[str, ...] = ()      # gates asserted after this stage
    agent_seam: str | None = None       # where/how an LLM may plug in, if any


# The canonical pipeline shape. Order is the execution order.
STAGES: tuple[Stage, ...] = (
    Stage(
        id="ingest",
        title="1. Ingestion / 正準化",
        kind=StageKind.DETERMINISTIC,
        description=(
            "messy number strings → CanonicalNumber. Pure stdlib. Ambiguous "
            "input lowers confidence and raises needs_review (P-NO-GUESS)."
        ),
        gate_ids=("G-NORMALIZE-REVIEW",),
    ),
    Stage(
        id="retrieve",
        title="2. Retrieval / 取得",
        kind=StageKind.DETERMINISTIC,
        description=(
            "CanonicalNumber → PatentRecord via the PatentSource protocol. "
            "Deterministic over a FROZEN source (fixture / saved bq-export / "
            "bulk XML). A live BigQuery query is the exception: network, not "
            "reproducible — freeze it to an export before gating."
        ),
        gate_ids=("G-PROVENANCE",),
    ),
    Stage(
        id="summarize",
        title="3. Analysis / 抽出要約",
        kind=StageKind.DETERMINISTIC,
        description=(
            "PatentRecord → PatentSummary + heuristic claim-element split. "
            "Labelled heuristic so the semantic pass treats it as a starting "
            "point to verify, not ground truth."
        ),
    ),
    Stage(
        id="compare",
        title="4. Analysis / 意味比較 (MATCH·MISSING·UNCLEAR)",
        kind=StageKind.AGENT,
        description=(
            "target spec × claim elements → per-element verdict with evidence "
            "span + confidence. The shipped HeuristicJudge is deterministic and "
            "is what the self-check asserts; an LLM Judge swaps in here."
        ),
        gate_ids=("G-NO-GUESS", "G-EVIDENCE-SUBSTRING", "G-UNCLEAR-REVIEW"),
        agent_seam=(
            "Judge protocol (analyze/compare.py): compare(spec, summary, "
            "judge=<LLM Judge>). P-NO-GUESS is enforced structurally in "
            "ElementVerdict.__post_init__, so ANY judge — heuristic or LLM — "
            "cannot emit an ungrounded MATCH."
        ),
    ),
    Stage(
        id="snapshot",
        title="5. State / Snapshot + Diff",
        kind=StageKind.DETERMINISTIC,
        description=(
            "PatentRecord → content-hashed snapshot; re-runs diff field-by-"
            "field. Change detection is content-hash based, not timestamp "
            "based: identical content ⇒ no new snapshot."
        ),
        gate_ids=("G-SNAPSHOT-STABLE",),
    ),
    Stage(
        id="present",
        title="6. Presentation / 出力",
        kind=StageKind.DETERMINISTIC,
        description=(
            "summaries (+ comparisons) → Markdown / HTML. Every report carries "
            "the mandatory disclaimer and per-item provenance."
        ),
        gate_ids=("G-DISCLAIMER",),
    ),
)

# A cross-cutting gate that is not owned by a single stage: it re-runs the whole
# deterministic backbone and asserts the two runs are byte-identical.
CROSSCUTTING_GATE_IDS: tuple[str, ...] = ("G-DETERMINISM",)


# ===========================================================================
# Artifacts threaded through the pipeline
# ===========================================================================

@dataclass
class PipelineArtifacts:
    """Everything the deterministic backbone produces for one run."""

    raw_numbers: list[str]
    normalized: list[CanonicalNumber]
    records: list[PatentRecord]
    summaries: list[PatentSummary]
    target_spec: str | None
    comparisons: list[ComparisonResult] | None
    report_md: str
    not_found: list[str]

    def fingerprint(self) -> str:
        """Deterministic SHA-256 over the *reproducible* artifacts.

        Excludes anything non-reproducible (snapshot timestamps) and the bulky
        ``raw`` payload. Two runs of the deterministic backbone on the same
        frozen input must yield the same fingerprint — that is G-DETERMINISM.
        """
        payload = {
            "normalized": [
                {"canonical": n.canonical, "confidence": round(n.confidence, 6),
                 "needs_review": n.needs_review, "office": n.office.value}
                for n in self.normalized
            ],
            "records": [
                {k: getattr(r, k) for k in (
                    "canonical", "office", "number", "title", "abstract",
                    "claims", "assignee", "pub_date", "legal_status",
                    "family_id", "source", "source_url")}
                for r in self.records
            ],
            "summaries": [
                {"canonical": s.canonical, "one_line": s.one_line,
                 "elements": (s.breakdown.elements if s.breakdown else [])}
                for s in self.summaries
            ],
            "comparisons": [
                {"canonical": c.patent_canonical,
                 "verdicts": [
                     {"element": v.element, "verdict": v.verdict.value,
                      "evidence_span": v.evidence_span,
                      "confidence": round(v.confidence, 6)}
                     for v in c.verdicts]}
                for c in (self.comparisons or [])
            ],
            "report_md": self.report_md,
            "not_found": self.not_found,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ===========================================================================
# Gate results / reports
# ===========================================================================

@dataclass
class GateResult:
    """Outcome of one invariant check."""

    gate: str          # gate id, e.g. "G-NO-GUESS"
    title: str         # one-line human description of the invariant
    passed: bool
    checked: int       # how many items were inspected (transparency)
    detail: str        # on failure: the offending item; on pass: a summary

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"[{mark}] {self.gate:<20} ({self.checked:>3} checked) {self.detail}"


@dataclass
class StageReport:
    """Per-stage roll-up: the stage metadata + its gate results."""

    stage: Stage
    produced: int                              # item count this stage emitted
    gates: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(g.passed for g in self.gates)


@dataclass
class PipelineReport:
    """The full self-check result."""

    stage_reports: list[StageReport]
    crosscutting: list[GateResult]
    fingerprint: str

    @property
    def gates(self) -> list[GateResult]:
        out: list[GateResult] = []
        for sr in self.stage_reports:
            out.extend(sr.gates)
        out.extend(self.crosscutting)
        return out

    @property
    def ok(self) -> bool:
        """True iff every gate passed = the deterministic pipeline self-verifies."""
        return all(g.passed for g in self.gates)

    def to_dict(self) -> dict:
        """JSON-serialisable form (for CI artifacts / dashboards)."""
        return {
            "ok": self.ok,
            "fingerprint": self.fingerprint,
            "stages": [
                {
                    "id": sr.stage.id,
                    "title": sr.stage.title,
                    "kind": sr.stage.kind.value,
                    "produced": sr.produced,
                    "agent_seam": sr.stage.agent_seam,
                    "gates": [
                        {"gate": g.gate, "title": g.title, "passed": g.passed,
                         "checked": g.checked, "detail": g.detail}
                        for g in sr.gates
                    ],
                }
                for sr in self.stage_reports
            ],
            "crosscutting": [
                {"gate": g.gate, "title": g.title, "passed": g.passed,
                 "checked": g.checked, "detail": g.detail}
                for g in self.crosscutting
            ],
        }


# ===========================================================================
# Gates — pure, importable, individually testable
# ===========================================================================
#
# Each gate encodes an invariant already documented elsewhere in the codebase
# and asserts it mechanically. Gates never raise on a violation: they return a
# GateResult(passed=False) carrying the offending item, so the runner can show
# every failure at once rather than dying on the first.

def gate_normalize_review(art: PipelineArtifacts) -> GateResult:
    """G-NORMALIZE-REVIEW — the needs_review flag is consistent with confidence.

    P-NO-GUESS: an ambiguous normalization (confidence < 0.85 or UNKNOWN
    office) MUST raise needs_review. This checks the flag is never silently
    dropped (normalize/numbers.py CanonicalNumber.needs_review).
    """
    bad: list[str] = []
    for n in art.normalized:
        ambiguous = n.confidence < 0.85 or n.office.value == "UNKNOWN"
        if ambiguous != n.needs_review:
            bad.append(f"{n.raw!r} (conf={n.confidence}, review={n.needs_review})")
    return GateResult(
        gate="G-NORMALIZE-REVIEW",
        title="ambiguous normalization is flagged needs_review",
        passed=not bad,
        checked=len(art.normalized),
        detail=("all consistent" if not bad else f"inconsistent: {bad}"),
    )


def gate_provenance(art: PipelineArtifacts) -> GateResult:
    """G-PROVENANCE — every record carries a non-empty source (§7.2).

    No downstream claim may be made without an attached source.
    """
    bad = [r.canonical for r in art.records if not (r.source and r.source.strip())]
    return GateResult(
        gate="G-PROVENANCE",
        title="every record has a non-empty source",
        passed=not bad,
        checked=len(art.records),
        detail=("all sourced" if not bad else f"missing source: {bad}"),
    )


def gate_no_guess(art: PipelineArtifacts) -> GateResult:
    """G-NO-GUESS — no MATCH verdict without an evidence span.

    The headline P-NO-GUESS guarantee, enforced structurally in
    ElementVerdict.__post_init__. The gate verifies it end-to-end.
    """
    verdicts = _all_verdicts(art)
    bad = [
        f"{c}:{v.element[:40]!r}"
        for (c, v) in verdicts
        if v.verdict is Verdict.MATCH and not v.evidence_span
    ]
    return GateResult(
        gate="G-NO-GUESS",
        title="no MATCH without an evidence span",
        passed=not bad,
        checked=sum(1 for (_, v) in verdicts if v.verdict is Verdict.MATCH),
        detail=("no ungrounded MATCH" if not bad else f"ungrounded MATCH: {bad}"),
    )


def gate_evidence_substring(art: PipelineArtifacts) -> GateResult:
    """G-EVIDENCE-SUBSTRING — every evidence span is a literal substring of the spec.

    An evidence span that is not actually in the target spec would be a
    fabricated citation. _best_evidence_span() guarantees a substring; the gate
    proves the guarantee held for this run.
    """
    if art.target_spec is None:
        return GateResult("G-EVIDENCE-SUBSTRING",
                          "evidence spans are literal substrings of the spec",
                          passed=True, checked=0,
                          detail="no spec supplied; comparison stage skipped")
    spec = art.target_spec
    verdicts = _all_verdicts(art)
    bad = [
        f"{c}:{v.evidence_span[:40]!r}"
        for (c, v) in verdicts
        if v.evidence_span and v.evidence_span not in spec
    ]
    checked = sum(1 for (_, v) in verdicts if v.evidence_span)
    return GateResult(
        gate="G-EVIDENCE-SUBSTRING",
        title="evidence spans are literal substrings of the spec",
        passed=not bad,
        checked=checked,
        detail=("all spans verbatim" if not bad else f"fabricated span: {bad}"),
    )


def gate_unclear_review(art: PipelineArtifacts) -> GateResult:
    """G-UNCLEAR-REVIEW — every UNCLEAR verdict is queued for human review.

    UNCLEAR is the escalation path; it must always set needs_review so nothing
    ambiguous is silently auto-confirmed.
    """
    verdicts = _all_verdicts(art)
    bad = [
        f"{c}:{v.element[:40]!r}"
        for (c, v) in verdicts
        if v.verdict is Verdict.UNCLEAR and not v.needs_review
    ]
    return GateResult(
        gate="G-UNCLEAR-REVIEW",
        title="every UNCLEAR verdict is flagged needs_review",
        passed=not bad,
        checked=sum(1 for (_, v) in verdicts if v.verdict is Verdict.UNCLEAR),
        detail=("all escalated" if not bad else f"un-escalated UNCLEAR: {bad}"),
    )


def gate_disclaimer(art: PipelineArtifacts) -> GateResult:
    """G-DISCLAIMER — the rendered report carries the mandatory disclaimer (§6.5)."""
    # Compare on the disclaimer body, ignoring the Markdown blockquote marker so
    # the gate also holds for the HTML/plain renders that strip "> **注記**".
    needle = DISCLAIMER.replace("> **注記**: ", "").replace("**", "")
    present = needle[:30] in art.report_md or DISCLAIMER in art.report_md
    return GateResult(
        gate="G-DISCLAIMER",
        title="output carries the mandatory disclaimer",
        passed=present,
        checked=1,
        detail=("disclaimer present" if present else "disclaimer MISSING from report"),
    )


def gate_snapshot_stable() -> GateResult:
    """G-SNAPSHOT-STABLE — saving identical content twice detects no change.

    Proves the content-hash equality contract of SnapshotStore: re-fetching an
    unchanged patent must NOT register as a change (otherwise every monitor run
    would cry wolf). Runs in an isolated temp store so it has no side effects.
    """
    rec = PatentRecord(
        canonical="US-SELFCHECK-0",
        office="US",
        number="SELFCHECK0",
        title="snapshot stability probe",
        claims=["a sole claim, fixed."],
        source="selfcheck",
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = SnapshotStore(tmp)
        first = store.save(rec)
        second = store.save(rec)
    ok = first.is_new and not second.changed and not second.is_new
    return GateResult(
        gate="G-SNAPSHOT-STABLE",
        title="re-saving identical content is detected as unchanged",
        passed=ok,
        checked=2,
        detail=("identical save ⇒ changed=False"
                if ok else
                f"unstable: first(new={first.is_new}) "
                f"second(changed={second.changed},new={second.is_new})"),
    )


def gate_determinism(fp_a: str, fp_b: str) -> GateResult:
    """G-DETERMINISM — two runs of the backbone produce identical artifacts.

    The headline 自校 gate: it re-runs the entire deterministic pipeline and
    asserts byte-identical fingerprints. If anything non-deterministic sneaks
    into the deterministic path, this fails.
    """
    ok = fp_a == fp_b
    return GateResult(
        gate="G-DETERMINISM",
        title="two runs of the deterministic backbone are byte-identical",
        passed=ok,
        checked=2,
        detail=(f"fingerprint stable ({fp_a[:12]}…)"
                if ok else
                f"DIVERGED: {fp_a[:12]}… != {fp_b[:12]}…"),
    )


def _all_verdicts(art: PipelineArtifacts):
    """Flatten comparisons to (canonical, ElementVerdict) pairs."""
    out = []
    for c in (art.comparisons or []):
        for v in c.verdicts:
            out.append((c.patent_canonical, v))
    return out


# ===========================================================================
# Runner — executes the deterministic backbone once and self-checks it
# ===========================================================================

def run_backbone(
    raw_numbers: list[str],
    target_spec: str | None,
    source=None,
) -> PipelineArtifacts:
    """Run the deterministic backbone end-to-end (no gates, no side effects).

    Uses HeuristicJudge for compare() — the deterministic default. Pass a frozen
    source (defaults to FixtureSource) so the result is reproducible.
    """
    if source is None:
        source = FixtureSource()

    normalized = [normalize(n) for n in raw_numbers]
    if hasattr(source, "prefetch"):
        source.prefetch([n.canonical for n in normalized])

    records: list[PatentRecord] = []
    summaries: list[PatentSummary] = []
    not_found: list[str] = []
    for n in normalized:
        rec = source.fetch(n)
        if rec is None:
            not_found.append(n.canonical or n.raw)
            continue
        records.append(rec)
        summaries.append(summarize(rec))

    comparisons: list[ComparisonResult] | None = None
    if target_spec is not None:
        comparisons = [
            compare(target_spec, s, judge=HeuristicJudge()) for s in summaries
        ]

    report_md = render_report(summaries, records, not_found, comparisons=comparisons)

    return PipelineArtifacts(
        raw_numbers=list(raw_numbers),
        normalized=normalized,
        records=records,
        summaries=summaries,
        target_spec=target_spec,
        comparisons=comparisons,
        report_md=report_md,
        not_found=not_found,
    )


def run_selfcheck(
    raw_numbers: list[str],
    target_spec: str | None = None,
    source_factory=None,
) -> PipelineReport:
    """Run the deterministic backbone and assert every contract gate.

    Args:
        raw_numbers: messy patent-number strings (the pipeline input).
        target_spec: optional target-spec text; enables the comparison gates.
        source_factory: optional zero-arg callable returning a fresh PatentSource
            for each run. Defaults to FixtureSource. A *factory* (not a single
            instance) is required so the two determinism runs are independent.

    Returns:
        A PipelineReport. ``report.ok`` is True iff every gate passed.
    """
    if source_factory is None:
        source_factory = FixtureSource

    art = run_backbone(raw_numbers, target_spec, source=source_factory())
    art_b = run_backbone(raw_numbers, target_spec, source=source_factory())

    # Gates owned by stages.
    gates_by_id = {
        "G-NORMALIZE-REVIEW": lambda: gate_normalize_review(art),
        "G-PROVENANCE": lambda: gate_provenance(art),
        "G-NO-GUESS": lambda: gate_no_guess(art),
        "G-EVIDENCE-SUBSTRING": lambda: gate_evidence_substring(art),
        "G-UNCLEAR-REVIEW": lambda: gate_unclear_review(art),
        "G-DISCLAIMER": lambda: gate_disclaimer(art),
        "G-SNAPSHOT-STABLE": lambda: gate_snapshot_stable(),
    }

    produced_by_stage = {
        "ingest": len(art.normalized),
        "retrieve": len(art.records),
        "summarize": len(art.summaries),
        "compare": len(art.comparisons or []),
        "snapshot": len(art.records),
        "present": 1,
    }

    stage_reports: list[StageReport] = []
    for stage in STAGES:
        gate_results = [gates_by_id[gid]() for gid in stage.gate_ids]
        stage_reports.append(StageReport(
            stage=stage,
            produced=produced_by_stage.get(stage.id, 0),
            gates=gate_results,
        ))

    crosscutting = [gate_determinism(art.fingerprint(), art_b.fingerprint())]

    return PipelineReport(
        stage_reports=stage_reports,
        crosscutting=crosscutting,
        fingerprint=art.fingerprint(),
    )
