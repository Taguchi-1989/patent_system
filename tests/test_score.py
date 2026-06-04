"""Unit tests for src/patentkit/analyze/score.py (FTO triage scoring).

Covers:
  - full-coverage spec  -> HIGH band, gap_count == 0
  - unrelated spec       -> LOW band, low coverage, gaps
  - no claim elements    -> UNKNOWN band
  - determinism          -> two runs produce identical to_dict()
  - numeric bounds       -> all pct in [0,100]; band_low <= coverage <= band_high
  - P-NO-GUESS           -> every "covered" element carries an evidence span
  - coverage derivation  -> MISSING verdict -> coverage 0
  - triage ordering       -> HIGH sorts before LOW
  - LenientJudge recall   -> catches a stemmed token the strict judge misses

Run from repo root:
    py -m pytest tests/test_score.py -q
    py tests/test_score.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.analyze.score import (        # noqa: E402
    LenientJudge,
    PatentScore,
    score_patent,
    triage_sort_key,
)
from patentkit.analyze.summarize import ClaimBreakdown, PatentSummary  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ELEMENTS = [
    "a transmitter coil",
    "a position sensor to detect a receiver position",
    "a controller to adjust a drive signal",
]

SPEC_FULL = (
    "Target product specification.\n"
    "The device includes a transmitter coil operating at resonance.\n"
    "A position sensor detects the receiver position with high accuracy.\n"
    "A controller adjusts the drive signal frequency based on the position.\n"
)

SPEC_UNRELATED = (
    "Bicycle frame specification.\n"
    "The product is a bicycle with two wheels, a steel frame, a chain, and a leather seat.\n"
)


def _summary(elements: list[str] | None, canonical: str = "US-1-A1") -> PatentSummary:
    claim = (
        "An apparatus comprising: " + "; ".join(elements) + "."
        if elements else ""
    )
    breakdown = (
        ClaimBreakdown(claim_no=1, text=claim, elements=elements)
        if elements else None
    )
    return PatentSummary(
        canonical=canonical,
        title="probe",
        one_line="US | 1 | probe",
        claim_count=1 if elements else 0,
        independent_claim=claim,
        breakdown=breakdown,
        source="fixture",
        source_url=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_full_coverage_high():
    score = score_patent(SPEC_FULL, _summary(_ELEMENTS))
    assert score.risk_band == "HIGH", f"expected HIGH, got {score.risk_band}"
    assert score.gap_count == 0, f"expected no gaps, got {score.gap_count}"
    assert score.coverage_pct >= 70.0, f"coverage too low: {score.coverage_pct}"
    assert score.n_elements == len(_ELEMENTS)


def test_unrelated_low():
    score = score_patent(SPEC_UNRELATED, _summary(_ELEMENTS))
    assert score.risk_band == "LOW", f"expected LOW, got {score.risk_band}"
    assert score.gap_count >= 1, "unrelated spec should produce gaps"
    assert score.coverage_pct < 45.0, f"coverage too high: {score.coverage_pct}"


def test_no_elements_unknown():
    score = score_patent(SPEC_FULL, _summary(None))
    assert score.risk_band == "UNKNOWN"
    assert score.n_elements == 0
    assert score.coverage_pct == 0.0
    assert score.elements == []


def test_determinism():
    a = score_patent(SPEC_FULL, _summary(_ELEMENTS))
    b = score_patent(SPEC_FULL, _summary(_ELEMENTS))
    assert a.to_dict() == b.to_dict(), "scoring must be byte-reproducible"


def test_numeric_bounds():
    for spec in (SPEC_FULL, SPEC_UNRELATED):
        score = score_patent(spec, _summary(_ELEMENTS))
        assert 0.0 <= score.coverage_pct <= 100.0
        assert 0.0 <= score.confidence_pct <= 100.0
        assert 0.0 <= score.band_low <= score.coverage_pct + 1e-9
        assert score.coverage_pct - 1e-9 <= score.band_high <= 100.0
        for c in score.elements:
            assert 0.0 <= c.p_coverage <= 1.0
            assert 0.0 <= c.confidence <= 1.0


def test_p_no_guess_covered_has_evidence():
    """Every element in the 'covered' band must carry a real evidence span."""
    score = score_patent(SPEC_FULL, _summary(_ELEMENTS))
    for c in score.elements:
        if c.band == "covered":
            assert c.evidence_span, (
                f"covered element without evidence span: {c.element!r}"
            )
            # And the span must be a literal substring of the spec.
            assert c.evidence_span in SPEC_FULL, "evidence span not a literal substring"


def test_missing_yields_zero_coverage():
    score = score_patent(SPEC_UNRELATED, _summary(_ELEMENTS))
    # At least one element should be a hard gap (p == 0) against the bicycle spec.
    assert any(c.p_coverage == 0.0 for c in score.elements)
    assert score.min_coverage_pct == 0.0


def test_triage_sort_high_before_low():
    high = score_patent(SPEC_FULL, _summary(_ELEMENTS, canonical="US-HIGH-A1"))
    low = score_patent(SPEC_UNRELATED, _summary(_ELEMENTS, canonical="US-LOW-A1"))
    ordered = sorted([low, high], key=triage_sort_key)
    assert ordered[0].canonical == "US-HIGH-A1", "HIGH risk must sort first"


def test_lenient_judge_recall_beats_strict_on_stem():
    """LenientJudge should match a morphological variant the strict judge misses."""
    from patentkit.analyze.compare import HeuristicJudge, Verdict

    element = "adjusting a transmitter"
    spec = "The controller performs adjustment of the transmitters."
    strict = HeuristicJudge().judge(element, spec, "")
    lenient = LenientJudge().judge(element, spec, "")
    # strict needs exact tokens (adjusting/transmitter) -> low overlap;
    # lenient stems adjusting->adjust, adjustment->adjust; transmitter(s) -> match.
    assert lenient.confidence >= strict.confidence
    assert lenient.verdict in (Verdict.MATCH, Verdict.UNCLEAR)


def test_to_dict_shape():
    score = score_patent(SPEC_FULL, _summary(_ELEMENTS))
    d = score.to_dict()
    assert isinstance(score, PatentScore)
    for key in ("canonical", "coverage_pct", "risk_band", "gap_count",
                "n_elements", "elements", "band_low", "band_high", "proposals"):
        assert key in d, f"missing key in to_dict: {key}"
    assert isinstance(d["elements"], list)
    assert isinstance(d["proposals"], list)


def test_matched_terms_populated():
    """Covered/partial elements expose the terms shared with the spec (highlighting)."""
    score = score_patent(SPEC_FULL, _summary(_ELEMENTS))
    hit = [c for c in score.elements if c.p_coverage > 0]
    assert hit, "expected at least one element with coverage"
    for c in hit:
        assert c.matched_terms, f"matched_terms empty for covered element {c.element!r}"
        # Every matched term must appear in BOTH the element and the spec (no fabrication).
        for term in c.matched_terms:
            assert term.lower() in c.element.lower()
            assert term.lower() in SPEC_FULL.lower()


def test_proposals_grounded_or_absent():
    """Anti-hallucination invariant: a proposal's basis is a verbatim spec substring."""
    for spec in (SPEC_FULL, SPEC_UNRELATED):
        score = score_patent(spec, _summary(_ELEMENTS))
        assert score.proposals, "expected at least one proposal"
        for p in score.proposals:
            assert p.text, "proposal text must be non-empty"
            if p.basis:
                assert p.basis in spec, (
                    f"proposal basis is not a verbatim substring of the spec: {p.basis!r}"
                )


def test_high_band_emits_audit_proposal():
    score = score_patent(SPEC_FULL, _summary(_ELEMENTS))
    cats = {p.category for p in score.proposals}
    assert "精査" in cats, f"HIGH band should propose 精査; got {cats}"


def test_low_band_emits_defense_proposal():
    score = score_patent(SPEC_UNRELATED, _summary(_ELEMENTS))
    cats = {p.category for p in score.proposals}
    # Unrelated spec -> gaps -> defense/design-around proposals.
    assert "防御・設計回避" in cats, f"gaps should propose 防御・設計回避; got {cats}"


# ---------------------------------------------------------------------------
# __main__ runner (allow running without pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"ok   {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
