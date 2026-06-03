"""Regression quality gate for HeuristicJudge accuracy.

Ensures future changes cannot silently degrade comparison quality.

Threshold rationale:
  - Measured accuracy on the 6-case golden set: 12/18 = 66.7% (0.667)
  - Margin: 0.067 (7 percentage points) — absorbs minor golden-label
    sensitivity, element-split variance, and heuristic edge-case drift
    without masking real regressions.
  - THRESHOLD = 0.60 (floor(0.667 - 0.067) rounded to 2 dp).
    Any regression that drops accuracy below 60% triggers this gate.

Run from repo root:
    py -m pytest tests/test_eval_regression.py -q
    py tests/test_eval_regression.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Import metric functions from the upgraded eval script.
from eval_compare import (  # noqa: E402
    LABELS,
    load_golden_cases,
    run_golden_eval,
)
from patentkit.analyze.compare import Verdict  # noqa: E402

# ---------------------------------------------------------------------------
# Regression threshold
# Measured baseline (6-case golden set): 12/18 = 66.7%
# Margin: 7 percentage points (absorbs label subjectivity + heuristic variance)
# ---------------------------------------------------------------------------
THRESHOLD = 0.60


# ---------------------------------------------------------------------------
# Test 1: accuracy above threshold
# ---------------------------------------------------------------------------

def test_accuracy_above_threshold():
    """Overall accuracy must stay >= THRESHOLD to prevent silent regressions."""
    cases = load_golden_cases()
    assert len(cases) >= 5, (
        f"Golden set too small ({len(cases)} cases); expected at least 5. "
        "Was the golden directory accidentally truncated?"
    )

    _, metrics = run_golden_eval(cases)
    acc = metrics.accuracy

    assert acc >= THRESHOLD, (
        f"HeuristicJudge accuracy regressed: {acc:.3f} < threshold {THRESHOLD}. "
        f"Measured baseline was 0.667 (12/18). Check recent changes to compare.py "
        f"or golden label definitions."
    )


# ---------------------------------------------------------------------------
# Test 2: P-NO-GUESS — no fabricated MATCH (every MATCH must have evidence)
# ---------------------------------------------------------------------------

def test_no_fabricated_match():
    """Every MATCH verdict must carry a non-empty evidence_span (P-NO-GUESS).

    Re-runs the eval pipeline and inspects actual ElementVerdict objects
    directly (not via VerdictPairs), checking the Layer-2 structural guarantee
    that ElementVerdict.__post_init__ enforces.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from patentkit.analyze.compare import HeuristicJudge, compare  # noqa: E402
    from patentkit.analyze.summarize import summarize               # noqa: E402
    from eval_compare import build_record                            # noqa: E402

    cases = load_golden_cases()
    for case in cases:
        rec = build_record(case["patent"])
        summary = summarize(rec)
        result = compare(case["target_spec"], summary, judge=HeuristicJudge())
        for ev in result.verdicts:
            if ev.verdict is Verdict.MATCH:
                assert ev.evidence_span, (
                    f"P-NO-GUESS violation: MATCH verdict with empty evidence_span "
                    f"in case '{case.get('name')}', element: {ev.element!r}"
                )


# ---------------------------------------------------------------------------
# Test 3: confusion matrix is structurally complete
# ---------------------------------------------------------------------------

def test_confusion_matrix_complete():
    """All 9 (expected, actual) cells must be present and sum to total_pairs."""
    cases = load_golden_cases()
    _, metrics = run_golden_eval(cases)

    confusion = metrics.confusion
    total_pairs = metrics.total_pairs

    # All 9 cells must be present.
    for exp in LABELS:
        for act in LABELS:
            assert (exp, act) in confusion, (
                f"Confusion matrix missing cell ({exp!r}, {act!r})"
            )

    # Cell counts must sum to total_pairs.
    cell_sum = sum(confusion.values())
    assert cell_sum == total_pairs, (
        f"Confusion matrix cells sum to {cell_sum}, expected {total_pairs}"
    )


# ---------------------------------------------------------------------------
# __main__ runner (run without pytest)
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
