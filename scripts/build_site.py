"""Build a static HTML site from the patent pipeline output.

Usage:
    py scripts/build_site.py                              # FixtureSource
    py scripts/build_site.py --source bq-export --export samples/bq_export_SAMPLE.json
    py scripts/build_site.py --source bq-export --export samples/bq_export_SAMPLE.json \\
        --spec samples/target_spec_SAMPLE.md
    py scripts/build_site.py --source bq-export --export results.json \\
        --spec spec.md --store cache/snapshots

Writes:
    site/index.html
    site/patents/<safe-canonical>.html

Prints:
    "site written: N pages -> site/index.html"

Pipeline: normalize -> fetch -> summarize -> (if --spec) compare ->
          (if --store has history) build per-patent diff history -> HTML.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

# Windows cp932 console safety: reconfigure before any non-ASCII output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.analyze import summarize                    # noqa: E402
from patentkit.analyze.score import score_all              # noqa: E402
from patentkit.connectors import (                         # noqa: E402
    BigQueryExportSource,
    BigQuerySource,
    FixtureSource,
)
from patentkit.export.html import render_detail, render_index, _safe_filename  # noqa: E402
from patentkit.normalize import normalize                  # noqa: E402
from patentkit.state import SnapshotStore                  # noqa: E402
from patentkit.state.diff import diff_records              # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEFAULT_CSV = os.path.join(ROOT, "samples", "public_patent_numbers.csv")
DEFAULT_STORE = os.path.join(ROOT, "cache", "snapshots")
SITE_DIR = os.path.join(ROOT, "site")


def build_source(args):
    """Construct a PatentSource from parsed CLI arguments (mirrors run_pipeline.py)."""
    if args.source == "fixture":
        if getattr(args, "fixtures_dir", None):
            return FixtureSource(directory=args.fixtures_dir)
        return FixtureSource()
    if args.source == "bq-export":
        if not args.export:
            sys.exit("--source bq-export requires --export <path to exported JSON>")
        return BigQueryExportSource(args.export)
    if args.source == "bq":
        return BigQuerySource(project=args.project)
    sys.exit(f"unknown source: {args.source}")


def _build_history(store: SnapshotStore, canonical: str):
    """Return (fetched_at, RecordDiff) pairs for *canonical*, or None.

    Returns None if:
    - the store directory does not exist, or
    - fewer than 2 snapshots exist (no diff computable).

    The list may contain pairs with .changed == False (no actual changes
    between adjacent snapshots); render_detail() handles that case.
    """
    try:
        snapshots = store.history(canonical)
    except Exception:
        return None

    if len(snapshots) < 2:
        return None

    pairs = []
    for i in range(len(snapshots) - 1):
        diff = diff_records(snapshots[i].record, snapshots[i + 1].record)
        pairs.append((snapshots[i + 1].fetched_at, diff))
    return pairs if pairs else None


def main() -> int:
    p = argparse.ArgumentParser(
        description="Build a static HTML patent survey site."
    )
    p.add_argument("csv", nargs="?", default=DEFAULT_CSV,
                   help="CSV of patent numbers (default: samples/public_patent_numbers.csv)")
    p.add_argument("--source", choices=["fixture", "bq-export", "bq"], default="fixture",
                   help="data source")
    p.add_argument("--export",
                   help="path to BigQuery-exported JSON (for --source bq-export)")
    p.add_argument("--project",
                   help="GCP project id (for --source bq)")
    p.add_argument("--spec",
                   help="path to target spec file (.md or .txt) for FTO triage scoring")
    p.add_argument("--fixtures-dir",
                   help="directory of fixture JSON (for --source fixture; e.g. samples/demo_fixtures)")
    p.add_argument("--store", default=DEFAULT_STORE,
                   help="snapshot store directory for diff history (default: cache/snapshots)")
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

    records, summaries, not_found = [], [], []
    for n in normalized:
        rec = source.fetch(n)
        if rec is None:
            not_found.append(n.canonical or n.raw)
            continue
        records.append(rec)
        summaries.append(summarize(rec))

    # 3. FTO triage scoring (only when --spec is provided).
    #    The score (decision×LLM fusion) supersedes the legacy MATCH/MISSING table
    #    as the headline view, so the demo passes scores — not comparisons — to the
    #    renderers. compare() stays imported for callers that still want it.
    scores = None
    score_map: dict[str, object] = {}
    if args.spec:
        with open(args.spec, encoding="utf-8") as sf:
            target_spec = sf.read()
        scores = score_all(target_spec, summaries)
        for s in scores:
            score_map[s.canonical] = s

    # 4. Diff history (gracefully skip if store doesn't exist or no snapshots).
    store_exists = os.path.isdir(args.store)
    if store_exists:
        store = SnapshotStore(args.store)
    else:
        store = None

    # 5. Write static site.
    patents_dir = os.path.join(SITE_DIR, "patents")
    os.makedirs(patents_dir, exist_ok=True)

    # 5a. Detail pages
    detail_pages_written = 0
    for rec, summary in zip(records, summaries):
        canonical = rec.canonical
        sc = score_map.get(canonical) if score_map else None
        history = _build_history(store, canonical) if store else None

        html_content = render_detail(rec, summary, history=history, score=sc)

        safe_fn = _safe_filename(canonical)
        out_path = os.path.join(patents_dir, f"{safe_fn}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        detail_pages_written += 1

    # 5b. Index page
    index_html = render_index(records, summaries, scores=scores)
    index_path = os.path.join(SITE_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html)

    total_pages = detail_pages_written + 1  # +1 for index
    print(f"site written: {total_pages} pages -> site/index.html")
    if not_found:
        print(f"  not found: {len(not_found)} number(s) had no record in this source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
