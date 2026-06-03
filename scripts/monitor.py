"""Continuous-monitoring script: fetch -> snapshot -> diff -> report.

Usage examples:
    py scripts/monitor.py --source bq-export --export samples/bq_export_SAMPLE.json
    py scripts/monitor.py --source fixture
    py scripts/monitor.py --source bq-export --export results.json --store cache/snapshots

Outputs:
    outputs/diff_report.md    — Markdown diff report with DISCLAIMER and provenance
    console                   — one-line summary: fetched N | new X | changed Y | unchanged Z

Windows cp932 safety: stdout is reconfigured to UTF-8 before any non-ASCII output.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

# Reconfigure stdout to UTF-8 first (Windows cp932 console safety).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.connectors import (              # noqa: E402
    BigQueryExportSource,
    BigQuerySource,
    FixtureSource,
)
from patentkit.export import render_diff_report  # noqa: E402
from patentkit.normalize import normalize        # noqa: E402
from patentkit.state import SnapshotStore        # noqa: E402
from patentkit.state.diff import diff_records    # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEFAULT_CSV = os.path.join(ROOT, "samples", "public_patent_numbers.csv")
DEFAULT_STORE = os.path.join(ROOT, "cache", "snapshots")
OUT_PATH = os.path.join(ROOT, "outputs", "diff_report.md")


def build_source(args):
    """Build a PatentSource from parsed CLI arguments (same logic as run_pipeline.py)."""
    if args.source == "fixture":
        return FixtureSource()
    if args.source == "bq-export":
        if not args.export:
            sys.exit("--source bq-export requires --export <path to exported JSON>")
        return BigQueryExportSource(args.export)
    if args.source == "bq":
        return BigQuerySource(project=args.project)
    sys.exit(f"unknown source: {args.source}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Fetch patents, save snapshots, and report changes."
    )
    p.add_argument("csv", nargs="?", default=DEFAULT_CSV,
                   help="CSV of patent numbers to monitor")
    p.add_argument("--source", choices=["fixture", "bq-export", "bq"], default="fixture",
                   help="data source to use")
    p.add_argument("--export",
                   help="path to BigQuery-console-exported JSON (for --source bq-export)")
    p.add_argument("--project",
                   help="GCP project id (for --source bq)")
    p.add_argument("--store", default=DEFAULT_STORE,
                   help="snapshot store directory (default: cache/snapshots)")
    args = p.parse_args()

    # 1. Ingestion
    with open(args.csv, newline="", encoding="utf-8") as f:
        raw_numbers = [(r.get("number") or "").strip() for r in csv.DictReader(f)]
    raw_numbers = [n for n in raw_numbers if n]
    normalized = [normalize(n) for n in raw_numbers]

    # 2. Retrieval
    source = build_source(args)
    if hasattr(source, "prefetch"):
        source.prefetch([n.canonical for n in normalized])

    records = []
    for n in normalized:
        rec = source.fetch(n)
        if rec is not None:
            records.append(rec)

    # 3. Snapshot + diff
    store = SnapshotStore(args.store)
    new_count = 0
    changed_count = 0
    unchanged_count = 0
    changed_diffs = []
    all_diffs = []

    for rec in records:
        result = store.save(rec)
        if result.is_new:
            new_count += 1
        elif result.changed:
            changed_count += 1
            if result.previous is not None:
                d = diff_records(result.previous.record, result.snapshot.record)
                changed_diffs.append(d)
                all_diffs.append(d)
            else:
                from patentkit.state.diff import RecordDiff
                all_diffs.append(RecordDiff(canonical=rec.canonical))
        else:
            unchanged_count += 1
            from patentkit.state.diff import RecordDiff
            all_diffs.append(RecordDiff(canonical=rec.canonical))

    # 4. Write diff report
    report = render_diff_report(all_diffs, source_label=args.source)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(report)

    # 5. Print summary
    total = len(records)
    print(
        f"fetched {total} | new {new_count} | changed {changed_count} | unchanged {unchanged_count}"
    )
    print(f"diff report written to: {os.path.relpath(OUT_PATH, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
