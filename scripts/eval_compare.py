"""Evaluate HeuristicJudge accuracy against golden test cases.

    py scripts/eval_compare.py

Loads every .json file in tests/golden/, runs HeuristicJudge via compare(),
measures per-element agreement vs golden labels, and prints an honest score.
The baseline may score modestly — that is expected and acceptable. This eval
exists to measure the gap that an LLM-powered Judge would close.

Exit code: always 0 (eval is measurement, not a gate).
"""

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.analyze.compare import HeuristicJudge, Verdict, compare  # noqa: E402
from patentkit.analyze.summarize import summarize                         # noqa: E402
from patentkit.connectors.base import PatentRecord                        # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "golden")


def load_golden_cases() -> list[dict]:
    cases = []
    golden_dir = os.path.normpath(GOLDEN_DIR)
    for fname in sorted(os.listdir(golden_dir)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(golden_dir, fname), encoding="utf-8") as f:
            cases.append(json.load(f))
    return cases


def build_record(patent_dict: dict) -> PatentRecord:
    """Build a minimal PatentRecord from the golden case patent field."""
    canonical = patent_dict.get("canonical", "UNKNOWN")
    # Parse office and number from canonical (e.g. "US-10123456-B2").
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


def main() -> int:
    cases = load_golden_cases()
    if not cases:
        print(f"No golden cases found in {GOLDEN_DIR}")
        return 0

    total_elements = 0
    total_agreed = 0

    for case in cases:
        name = case.get("name", "unknown")
        target_spec = case["target_spec"]
        patent_dict = case["patent"]
        expected_verdicts = case.get("expected_verdicts", [])

        print(f"\n{'='*60}")
        print(f"Case: {name}")
        print(f"Patent: {patent_dict.get('canonical', '?')}")
        print(f"{'='*60}")

        rec = build_record(patent_dict)
        summary = summarize(rec)
        result = compare(target_spec, summary, judge=HeuristicJudge())

        # Print the per-element table header.
        print(f"{'Element substring':<35} {'Expected':<10} {'Actual':<10} {'Match?'}")
        print("-" * 70)

        case_agreed = 0
        case_total = 0

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

            case_agreed += int(agreed)
            case_total += 1
            match_str = "ok" if agreed else "DIFF"
            print(f"  {element_substr:<33} {expected_label:<10} {actual_label:<10} {match_str}")

        total_agreed += case_agreed
        total_elements += case_total
        print(f"\nCase score: {case_agreed}/{case_total}")

        # Also print all actual verdicts for transparency.
        print("\nAll actual verdicts produced:")
        for v in result.verdicts:
            short_elem = v.element[:60]
            span_preview = v.evidence_span[:60] if v.evidence_span else "(none)"
            print(f"  [{v.verdict.value:<7}] conf={v.confidence:.2f}  elem={short_elem!r}")
            print(f"           span={span_preview!r}")

    print(f"\n{'='*60}")
    print(f"HeuristicJudge baseline accuracy: {total_agreed}/{total_elements} "
          f"({100*total_agreed/total_elements:.0f}%)" if total_elements else "No elements evaluated.")
    print(
        "\nThis measures the gap an LLM-powered Judge would close. "
        "Modest baseline accuracy is expected and acceptable."
    )
    print("The eval framework is now in place for measuring future Judge improvements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
