"""Evaluate HeuristicJudge accuracy against golden test cases.

    py scripts/eval_compare.py

Loads every .json file in tests/golden/, runs HeuristicJudge via compare(),
measures per-element agreement vs golden labels, and prints:
  - Per-case detail table
  - 3x3 confusion matrix (rows=expected, columns=actual)
  - Per-verdict precision and recall
  - Overall accuracy

The metric functions (VerdictPair, CaseResult, OverallMetrics,
build_confusion_matrix, precision_recall, overall_accuracy, run_golden_eval)
are importable pure functions so tests/test_eval_regression.py can call them
directly without re-implementing.

Honest caveat: this is a keyless heuristic baseline. An LLM-powered Judge
closes the gap. Report real numbers (no inflation).

Exit code: always 0 (eval is measurement, not a gate).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.analyze.compare import HeuristicJudge, Verdict, compare  # noqa: E402
from patentkit.analyze.summarize import summarize                         # noqa: E402
from patentkit.connectors.base import PatentRecord                        # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "golden")

# Canonical label ordering used throughout metrics.
LABELS: list[str] = ["MATCH", "MISSING", "UNCLEAR"]


# ---------------------------------------------------------------------------
# Pure data classes (importable)
# ---------------------------------------------------------------------------

@dataclass
class VerdictPair:
    """One golden-label vs actual-verdict comparison for a single element."""
    element_substring: str
    expected: str           # "MATCH" | "MISSING" | "UNCLEAR"
    actual: str             # same, or "(not found)" when element not matched
    agreed: bool


@dataclass
class CaseResult:
    """All element verdict pairs for one golden case."""
    name: str
    canonical: str
    pairs: list[VerdictPair] = field(default_factory=list)
    agreed: int = 0
    total: int = 0


@dataclass
class OverallMetrics:
    """Aggregated metrics across all golden cases."""
    accuracy: float
    confusion: dict[tuple[str, str], int]   # (expected, actual) -> count
    per_label: dict[str, tuple[float, float]]   # label -> (precision, recall)
    total_pairs: int


# ---------------------------------------------------------------------------
# Pure metric functions (importable, no side effects)
# ---------------------------------------------------------------------------

def build_confusion_matrix(pairs: list[VerdictPair]) -> dict[tuple[str, str], int]:
    """Build a 3x3 confusion matrix from a list of VerdictPairs.

    Rows are expected labels; columns are actual labels.
    When actual == "(not found)", the element was missing from the heuristic
    claim split — treated as (expected, "MISSING") because it is effectively
    a non-detection from the eval perspective.

    Returns all 9 (expected, actual) cells, initialised to 0.
    """
    matrix: dict[tuple[str, str], int] = {
        (exp, act): 0
        for exp in LABELS
        for act in LABELS
    }
    for pair in pairs:
        exp = pair.expected
        act = pair.actual if pair.actual != "(not found)" else "MISSING"
        if exp in LABELS and act in LABELS:
            matrix[(exp, act)] += 1
    return matrix


def precision_recall(
    confusion: dict[tuple[str, str], int],
    label: str,
) -> tuple[float, float]:
    """Return (precision, recall) for one label given a confusion matrix.

    - TP: predicted as `label` AND expected is `label`.
    - FP: predicted as `label` but expected is NOT `label`.
    - FN: expected is `label` but predicted as NOT `label`.
    """
    tp = confusion.get((label, label), 0)
    fp = sum(confusion.get((other, label), 0) for other in LABELS if other != label)
    fn = sum(confusion.get((label, other), 0) for other in LABELS if other != label)
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return (prec, rec)


def overall_accuracy(pairs: list[VerdictPair]) -> float:
    """Overall fraction of pairs where expected == actual (ignoring (not found))."""
    if not pairs:
        return 0.0
    return sum(1 for p in pairs if p.agreed) / len(pairs)


# ---------------------------------------------------------------------------
# Record builder and verdict matcher (importable helpers)
# ---------------------------------------------------------------------------

def build_record(patent_dict: dict) -> PatentRecord:
    """Build a minimal PatentRecord from the golden case patent field."""
    canonical = patent_dict.get("canonical", "UNKNOWN")
    parts = canonical.split("-")
    office = parts[0] if parts else "UNKNOWN"
    number = parts[1] if len(parts) > 1 else canonical
    return PatentRecord(
        canonical=canonical,
        office=office,
        number=number,
        title=patent_dict.get("title", ""),
        claims=patent_dict.get("claims", []),
        source="golden_test",
        source_url=None,
    )


def find_verdict_for_substring(element_substring: str, verdicts) -> object | None:
    """Find the actual ElementVerdict whose element text contains element_substring."""
    for v in verdicts:
        if element_substring.lower() in v.element.lower():
            return v
    return None


def load_golden_cases(golden_dir: str | None = None) -> list[dict]:
    """Load all .json files from the golden directory. Returns list of case dicts."""
    dir_path = os.path.normpath(golden_dir or GOLDEN_DIR)
    cases: list[dict] = []
    for fname in sorted(os.listdir(dir_path)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(dir_path, fname), encoding="utf-8") as f:
            cases.append(json.load(f))
    return cases


# ---------------------------------------------------------------------------
# Core eval runner (importable)
# ---------------------------------------------------------------------------

def run_golden_eval(
    cases: list[dict],
) -> tuple[list[CaseResult], OverallMetrics]:
    """Run HeuristicJudge comparison over all golden cases and return metrics.

    Args:
        cases: List of golden-case dicts (from load_golden_cases()).

    Returns:
        (case_results, overall_metrics) where case_results is one CaseResult
        per case and overall_metrics aggregates all element pairs.
    """
    all_pairs: list[VerdictPair] = []
    case_results: list[CaseResult] = []

    for case in cases:
        name = case.get("name", "unknown")
        target_spec = case["target_spec"]
        patent_dict = case["patent"]
        expected_verdicts = case.get("expected_verdicts", [])

        rec = build_record(patent_dict)
        summary = summarize(rec)
        result = compare(target_spec, summary, judge=HeuristicJudge())

        cr = CaseResult(
            name=name,
            canonical=patent_dict.get("canonical", "?"),
        )

        for ev in expected_verdicts:
            element_substr = ev["element_substring"]
            expected_label = ev["expected"]
            actual_v = find_verdict_for_substring(element_substr, result.verdicts)

            if actual_v is None:
                actual_label = "(not found)"
                agreed = False
            else:
                actual_label = actual_v.verdict.value
                agreed = actual_label == expected_label

            pair = VerdictPair(
                element_substring=element_substr,
                expected=expected_label,
                actual=actual_label,
                agreed=agreed,
            )
            cr.pairs.append(pair)
            all_pairs.append(pair)

        cr.agreed = sum(1 for p in cr.pairs if p.agreed)
        cr.total = len(cr.pairs)
        case_results.append(cr)

    # Build aggregated metrics.
    confusion = build_confusion_matrix(all_pairs)
    per_label = {label: precision_recall(confusion, label) for label in LABELS}
    acc = overall_accuracy(all_pairs)
    metrics = OverallMetrics(
        accuracy=acc,
        confusion=confusion,
        per_label=per_label,
        total_pairs=len(all_pairs),
    )
    return case_results, metrics


# ---------------------------------------------------------------------------
# CLI output (only runs when invoked directly)
# ---------------------------------------------------------------------------

def _print_confusion_matrix(confusion: dict[tuple[str, str], int]) -> None:
    """Print 3x3 confusion matrix: rows=expected, columns=actual."""
    col_w = 10
    header = f"{'':>12}" + "".join(f"{a:>{col_w}}" for a in LABELS)
    print(header)
    print("  " + "-" * (10 + col_w * len(LABELS)))
    for exp in LABELS:
        row = f"  exp {exp:<7}" + "".join(
            f"{confusion.get((exp, act), 0):>{col_w}}" for act in LABELS
        )
        print(row)


def main() -> int:
    cases = load_golden_cases()
    if not cases:
        print(f"No golden cases found in {GOLDEN_DIR}")
        return 0

    case_results, metrics = run_golden_eval(cases)

    # Per-case detail.
    for cr in case_results:
        print(f"\n{'='*60}")
        print(f"Case: {cr.name}")
        print(f"Patent: {cr.canonical}")
        print(f"{'='*60}")
        print(f"{'Element substring':<35} {'Expected':<10} {'Actual':<10} {'Match?'}")
        print("-" * 70)
        for pair in cr.pairs:
            match_str = "ok" if pair.agreed else "DIFF"
            print(
                f"  {pair.element_substring:<33} "
                f"{pair.expected:<10} "
                f"{pair.actual:<10} "
                f"{match_str}"
            )
        print(f"\nCase score: {cr.agreed}/{cr.total}")

    # 3x3 Confusion matrix.
    total = metrics.total_pairs
    acc = metrics.accuracy
    agreed = int(round(acc * total))

    print(f"\n{'='*60}")
    print("3x3 CONFUSION MATRIX  (rows=expected, columns=actual)")
    print(f"{'='*60}")
    print("  Note: '(not found)' elements are bucketed into MISSING column.")
    _print_confusion_matrix(metrics.confusion)

    # Per-verdict precision / recall.
    print(f"\n{'='*60}")
    print(f"{'Label':<10} {'Precision':>10} {'Recall':>10}")
    print("-" * 32)
    for label in LABELS:
        prec, rec = metrics.per_label[label]
        print(f"  {label:<8} {prec:>10.2f} {rec:>10.2f}")

    # Overall accuracy.
    print(f"\n{'='*60}")
    print(
        f"HeuristicJudge overall accuracy: {agreed}/{total} "
        f"({100*acc:.1f}%)"
    )
    print(
        "\nKeyless heuristic baseline — real numbers, not inflated. "
        "An LLM-powered Judge closes the gap. "
        "See tests/test_eval_regression.py for the regression gate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
