"""Demo (no API key needed): normalize a CSV of messy patent numbers.

Usage:
    py scripts/normalize_csv.py                       # uses samples/public_patent_numbers.csv
    py scripts/normalize_csv.py path/to/numbers.csv   # any CSV with a 'number' column

This is the Ingestion layer running end-to-end on its own. It demonstrates the
P-NO-GUESS principle: ambiguous inputs are flagged (REVIEW), not silently guessed.
"""

import csv
import os
import sys

# Windows consoles default to cp932 (Shift-JIS) and cannot encode kanji or
# symbols; force UTF-8 so Japanese patent numbers print correctly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.normalize import normalize  # noqa: E402

DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "..", "samples", "public_patent_numbers.csv")


def main(path: str) -> int:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    header = f"{'RAW':<20} {'OFFICE':<7} {'CANONICAL':<22} {'TYPE':<12} {'CONF':>4}  REVIEW"
    print(header)
    print("-" * len(header))

    review_count = 0
    for row in rows:
        raw = (row.get("number") or "").strip()
        if not raw:
            continue
        n = normalize(raw)
        flag = "<-- REVIEW" if n.needs_review else ""
        if n.needs_review:
            review_count += 1
        print(f"{raw:<20} {n.office.value:<7} {n.canonical:<22} "
              f"{n.doc_type.value:<12} {n.confidence:>4.2f}  {flag}")
        for note in n.notes:
            print(f"{'':<20} └─ {note}")

    print("-" * len(header))
    print(f"{len(rows)} rows, {review_count} flagged for human review.")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    raise SystemExit(main(target))
