"""End-to-end pipeline: CSV -> normalize -> fetch -> summarize -> Markdown.

    py scripts/run_pipeline.py                              # FixtureSource (zero keys)
    py scripts/run_pipeline.py --source bq-export --export results.json
    py scripts/run_pipeline.py --source bq --project my-gcp-project
    py scripts/run_pipeline.py mylist.csv --source fixture

The pipeline depends only on the PatentSource protocol — switching --source is
the ONLY change needed to go from local samples to real keyless data
(BigQuery). Output is written to outputs/report.md (git-ignored).
"""

import argparse
import csv
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.analyze import summarize                                    # noqa: E402
from patentkit.analyze.compare import compare                              # noqa: E402
from patentkit.analyze.score import build_channels, score_all             # noqa: E402
from patentkit.analyze.llm_judge import make_azure_judge_from_env         # noqa: E402
from patentkit.connectors import (                                         # noqa: E402
    BigQueryExportSource,
    BigQuerySource,
    BulkDataSource,
    FixtureSource,
)
from patentkit.export import render_report                                 # noqa: E402
from patentkit.normalize import normalize                                  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEFAULT_CSV = os.path.join(ROOT, "samples", "public_patent_numbers.csv")
OUT_PATH = os.path.join(ROOT, "outputs", "report.md")


def _build_score_channels(llm: str):
    """Build the scoring channels. 'azure' plugs Azure OpenAI into the semantic
    channel as the brain; 'none' (default) returns None = keyless LenientJudge."""
    if llm == "azure":
        judge = make_azure_judge_from_env()
        if judge is None:
            sys.exit(
                "--llm azure requires AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / "
                "AZURE_OPENAI_DEPLOYMENT (set them in .env). See .env.example."
            )
        print(f"LLM channel: Azure OpenAI (deployment={judge.deployment})")
        return build_channels(judge)
    return None


def build_source(args):
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
    if args.source == "bulk":
        if not args.bulk_files:
            sys.exit("--source bulk requires --bulk-files <path.xml|.zip> (repeatable)")
        return BulkDataSource(local_files=args.bulk_files)
    sys.exit(f"unknown source: {args.source}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("csv", nargs="?", default=DEFAULT_CSV)
    p.add_argument("--source", choices=["fixture", "bq-export", "bq", "bulk"], default="fixture")
    p.add_argument("--export", help="path to BigQuery-console-exported JSON (for --source bq-export)")
    p.add_argument("--project", help="GCP project id (for --source bq)")
    p.add_argument("--bulk-files", nargs="+", help="USPTO bulk XML/ZIP file(s) (for --source bulk)")
    p.add_argument("--spec", help="path to target spec file (.md or .txt) for semantic comparison + FTO scoring")
    p.add_argument("--fixtures-dir", help="directory of fixture JSON (for --source fixture; e.g. samples/demo_fixtures)")
    p.add_argument("--llm", choices=["none", "azure"], default="none",
                   help="LLM brain for the semantic channel: 'azure' uses Azure OpenAI "
                        "(env AZURE_OPENAI_*); 'none' = keyless LenientJudge (default)")
    args = p.parse_args()

    # 1. Ingestion
    with open(args.csv, newline="", encoding="utf-8") as f:
        raw_numbers = [(r.get("number") or "").strip() for r in csv.DictReader(f)]
    raw_numbers = [n for n in raw_numbers if n]
    normalized = [normalize(n) for n in raw_numbers]

    # 2. Retrieval (source-agnostic; batch via prefetch when supported)
    source = build_source(args)
    if hasattr(source, "prefetch"):
        source.prefetch([n.canonical for n in normalized])

    records, summaries, not_found, review = [], [], [], []
    for n in normalized:
        if n.needs_review:
            review.append(n.canonical or n.raw)
        rec = source.fetch(n)
        if rec is None:
            not_found.append(n.canonical or n.raw)
            continue
        records.append(rec)
        summaries.append(summarize(rec))  # 3. Analysis (deterministic, no LLM)

    # 3b. Semantic comparison + FTO triage scoring (only when --spec is provided)
    comparisons = None
    scores = None
    if args.spec:
        with open(args.spec, encoding="utf-8") as sf:
            target_spec = sf.read()
        comparisons = [compare(target_spec, s) for s in summaries]
        channels = _build_score_channels(args.llm)
        scores = score_all(target_spec, summaries, channels=channels)

    # 4. Presentation
    report = render_report(summaries, records, not_found,
                           comparisons=comparisons, scores=scores)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Ingested {len(raw_numbers)} numbers via source='{source.name}'")
    print(f"  fetched : {len(records)}")
    print(f"  not found: {len(not_found)}  (no record in this source)")
    print(f"  flagged for review (normalization): {len(review)}")
    print(f"\nReport written to: {os.path.relpath(OUT_PATH, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
