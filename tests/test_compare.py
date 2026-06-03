"""Unit tests for src/patentkit/analyze/compare.py.

Covers:
  - ElementVerdict.__post_init__ P-NO-GUESS guards
  - HeuristicJudge verdicts (MATCH, MISSING, UNCLEAR)
  - compare() integration (with and without claims)
  - Evidence span is an actual substring of target_spec
  - counts() correctness
  - Judge protocol is runtime_checkable

Run from repo root:
    py -m pytest tests/test_compare.py -q
    py tests/test_compare.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.analyze.compare import (          # noqa: E402
    ComparisonResult,
    ElementVerdict,
    HeuristicJudge,
    Judge,
    Verdict,
    compare,
)
from patentkit.analyze.summarize import summarize # noqa: E402
from patentkit.connectors.base import PatentRecord # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_record(claims: list[str], canonical: str = "US-10123456-B2") -> PatentRecord:
    parts = canonical.split("-")
    return PatentRecord(
        canonical=canonical,
        office=parts[0] if parts else "US",
        number=parts[1] if len(parts) > 1 else "0",
        title="Test Patent",
        claims=claims,
        source="test",
        source_url="https://example.com/test",
    )


WIRELESS_CLAIM = (
    "1. A wireless power transfer apparatus comprising a transmitter coil; "
    "a position sensor configured to detect a position of a receiver coil; "
    "and a controller configured to adjust a drive signal based on the detected position."
)

WIRELESS_SPEC = (
    "Wireless Charger Spec\n"
    "This device includes a transmitter coil for wireless power transfer. "
    "A position sensor detects the receiver coil position. "
    "A controller adjusts the drive signal based on detected position."
)

UNRELATED_SPEC = (
    "Software Version Control Specification\n"
    "This system uses directed acyclic graphs of commits. "
    "Each commit stores a SHA-256 hash of file contents and author metadata."
)


# ---------------------------------------------------------------------------
# Test 1: ElementVerdict.__post_init__ forces MATCH without evidence to UNCLEAR
# ---------------------------------------------------------------------------

def test_post_init_forces_match_without_span_to_unclear():
    v = ElementVerdict(
        element="some element",
        verdict=Verdict.MATCH,
        evidence_span="",   # empty span — P-NO-GUESS must demote
        confidence=0.9,
        rationale="fabricated match",
    )
    assert v.verdict is Verdict.UNCLEAR
    assert v.needs_review is True
    assert "forced UNCLEAR: no evidence span" in v.rationale


# ---------------------------------------------------------------------------
# Test 2: ElementVerdict.__post_init__ forces low-confidence MATCH to UNCLEAR
# ---------------------------------------------------------------------------

def test_post_init_forces_low_confidence_match_to_unclear():
    v = ElementVerdict(
        element="some element",
        verdict=Verdict.MATCH,
        evidence_span="some real span from the spec",
        confidence=0.1,    # below 0.3 threshold
        rationale="weak match",
    )
    assert v.verdict is Verdict.UNCLEAR
    assert v.needs_review is True
    assert "forced UNCLEAR: low confidence" in v.rationale


# ---------------------------------------------------------------------------
# Test 3: Valid MATCH (sufficient evidence and confidence) is not demoted
# ---------------------------------------------------------------------------

def test_post_init_valid_match_passes_through():
    v = ElementVerdict(
        element="transmitter coil",
        verdict=Verdict.MATCH,
        evidence_span="includes a transmitter coil for wireless power",
        confidence=0.75,
        rationale="tokens matched: [coil, transmitter]",
    )
    assert v.verdict is Verdict.MATCH
    assert v.needs_review is False


# ---------------------------------------------------------------------------
# Test 4: HeuristicJudge returns MATCH for high-overlap element
# ---------------------------------------------------------------------------

def test_heuristic_match_high_overlap():
    judge = HeuristicJudge()
    spec = "This device includes a transmitter coil for wireless power transfer."
    v = judge.judge(
        element="transmitter coil for wireless power",
        target_spec=spec,
        claim_context=WIRELESS_CLAIM,
    )
    assert v.verdict is Verdict.MATCH
    assert v.evidence_span  # must not be empty
    assert v.evidence_span in spec  # must be an actual substring
    assert v.confidence >= 0.6


# ---------------------------------------------------------------------------
# Test 5: HeuristicJudge returns MISSING for zero-overlap element
# ---------------------------------------------------------------------------

def test_heuristic_missing_zero_overlap():
    judge = HeuristicJudge()
    v = judge.judge(
        element="transmitter coil position sensor drive signal",
        target_spec=UNRELATED_SPEC,
        claim_context=WIRELESS_CLAIM,
    )
    assert v.verdict is Verdict.MISSING
    assert v.evidence_span == ""
    assert v.confidence == 1.0


# ---------------------------------------------------------------------------
# Test 6: HeuristicJudge returns UNCLEAR for partial overlap
# ---------------------------------------------------------------------------

def test_heuristic_unclear_partial_overlap():
    judge = HeuristicJudge()
    # Spec mentions "coil" but not the full element vocabulary.
    partial_spec = "This product uses a coil for energy transfer."
    v = judge.judge(
        element="transmitter coil position sensor drive signal frequency adjustment",
        target_spec=partial_spec,
        claim_context=WIRELESS_CLAIM,
    )
    assert v.verdict is Verdict.UNCLEAR
    assert v.needs_review is True
    # Confidence should reflect partial overlap (> 0 and < 0.6).
    assert 0.0 < v.confidence < 0.6


# ---------------------------------------------------------------------------
# Test 7: Evidence span is an actual substring of target_spec
# ---------------------------------------------------------------------------

def test_evidence_span_is_real_substring():
    judge = HeuristicJudge()
    spec = WIRELESS_SPEC
    v = judge.judge(
        element="transmitter coil",
        target_spec=spec,
        claim_context=WIRELESS_CLAIM,
    )
    if v.evidence_span:
        assert v.evidence_span in spec, (
            f"evidence_span {v.evidence_span!r} is not a substring of target_spec"
        )


# ---------------------------------------------------------------------------
# Test 8: compare() integration produces ComparisonResult
# ---------------------------------------------------------------------------

def test_compare_integration_with_claims():
    rec = make_record([WIRELESS_CLAIM])
    summary = summarize(rec)
    result = compare(WIRELESS_SPEC, summary)

    assert isinstance(result, ComparisonResult)
    assert result.patent_canonical == "US-10123456-B2"
    assert len(result.verdicts) > 0
    # All evidence spans that are non-empty must be actual substrings.
    for v in result.verdicts:
        if v.evidence_span:
            assert v.evidence_span in WIRELESS_SPEC, (
                f"evidence_span {v.evidence_span!r} not in spec"
            )
        if v.verdict is Verdict.UNCLEAR:
            assert v.needs_review is True


# ---------------------------------------------------------------------------
# Test 9: compare() with no claim text returns placeholder UNCLEAR
# ---------------------------------------------------------------------------

def test_compare_no_claims_returns_placeholder():
    rec = make_record([])  # no claims
    summary = summarize(rec)
    result = compare("Some target spec text.", summary)

    assert len(result.verdicts) == 1
    placeholder = result.verdicts[0]
    assert placeholder.verdict is Verdict.UNCLEAR
    assert placeholder.needs_review is True
    assert "no claim elements available" in placeholder.element.lower() or \
           "no independent claim" in placeholder.rationale.lower()


# ---------------------------------------------------------------------------
# Test 10: counts() returns correct verdict counts
# ---------------------------------------------------------------------------

def test_comparison_result_counts():
    verdicts = [
        ElementVerdict("e1", Verdict.MATCH, "span text here abc", 0.8, "ok"),
        ElementVerdict("e2", Verdict.MISSING, "", 1.0, "missing"),
        ElementVerdict("e3", Verdict.MISSING, "", 1.0, "missing"),
        ElementVerdict("e4", Verdict.UNCLEAR, "partial span text", 0.4, "unclear"),
    ]
    result = ComparisonResult(
        patent_canonical="US-XXXX",
        target_spec_title="Test Spec",
        verdicts=verdicts,
        source="test",
        source_url=None,
    )
    c = result.counts()
    assert c[Verdict.MATCH.value] == 1
    assert c[Verdict.MISSING.value] == 2
    assert c[Verdict.UNCLEAR.value] == 1

    unclear_list = result.unclear()
    assert len(unclear_list) == 1
    assert unclear_list[0].verdict is Verdict.UNCLEAR


# ---------------------------------------------------------------------------
# Test 11: Judge protocol is runtime_checkable
# ---------------------------------------------------------------------------

def test_judge_protocol_is_runtime_checkable():
    judge = HeuristicJudge()
    assert isinstance(judge, Judge), "HeuristicJudge must satisfy the Judge protocol"

    # A class with the wrong signature does NOT satisfy the protocol.
    class NotAJudge:
        def unrelated_method(self):
            pass

    assert not isinstance(NotAJudge(), Judge)


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
