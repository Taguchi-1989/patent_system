"""Run the deterministic pipeline contract and self-check every gate (自校).

This is the deterministic, reproducible gate for the keyless backbone. It runs
the whole pipeline (normalize → fetch → summarize → compare(HeuristicJudge) →
snapshot → render) twice and asserts a catalogue of invariants:

    G-NORMALIZE-REVIEW   ambiguous numbers are flagged needs_review
    G-PROVENANCE         every record carries a source (§7.2)
    G-NO-GUESS           no MATCH without an evidence span (P-NO-GUESS)
    G-EVIDENCE-SUBSTRING evidence spans are literal substrings of the spec
    G-UNCLEAR-REVIEW     every UNCLEAR is escalated for human review
    G-DISCLAIMER         output carries the mandatory disclaimer (§6.5)
    G-SNAPSHOT-STABLE    re-saving identical content detects no change
    G-DETERMINISM        two runs are byte-identical

Usage:
    py scripts/pipeline_selfcheck.py                       # fixture + sample spec
    py scripts/pipeline_selfcheck.py --no-spec             # skip comparison gates
    py scripts/pipeline_selfcheck.py mylist.csv --spec spec.md
    py scripts/pipeline_selfcheck.py --json                # machine-readable

Exit code: 0 if every gate passed, 1 otherwise. This makes it a real CI gate —
unlike eval_compare.py (measurement), the self-check is pass/fail.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.connectors import FixtureSource           # noqa: E402
from patentkit.pipeline import STAGES, run_selfcheck     # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEFAULT_CSV = os.path.join(ROOT, "samples", "public_patent_numbers.csv")
DEFAULT_SPEC = os.path.join(ROOT, "samples", "target_spec_SAMPLE.md")


def _read_numbers(csv_path: str) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [(r.get("number") or "").strip() for r in csv.DictReader(f)]
    return [n for n in rows if n]


def _print_human(report) -> None:
    # Shape banner: which stages are deterministic vs the agent seam.
    print("Pipeline shape (形) — deterministic backbone + one agent seam")
    print("-" * 72)
    for stage in STAGES:
        kind = "AGENT" if stage.kind.value == "agent" else "det. "
        print(f"  [{kind}] {stage.title}")
        if stage.agent_seam:
            print(f"          ↳ agent seam: {stage.agent_seam.splitlines()[0]}")
    print()

    print("Self-check gates (自校)")
    print("-" * 72)
    for sr in report.stage_reports:
        if not sr.gates:
            continue
        print(f"  {sr.stage.title}  (produced {sr.produced})")
        for g in sr.gates:
            print(f"    {g.line()}")
    print("  cross-cutting")
    for g in report.crosscutting:
        print(f"    {g.line()}")

    print()
    passed = sum(1 for g in report.gates if g.passed)
    total = len(report.gates)
    verdict = "OK — pipeline self-verifies" if report.ok else "FAILED"
    print(f"{verdict}: {passed}/{total} gates passed")
    print(f"deterministic fingerprint: {report.fingerprint[:16]}…")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv", nargs="?", default=DEFAULT_CSV,
                   help="CSV of patent numbers (default: samples/public_patent_numbers.csv)")
    p.add_argument("--spec", default=DEFAULT_SPEC,
                   help="target-spec file enabling the comparison gates")
    p.add_argument("--no-spec", action="store_true",
                   help="skip the comparison stage (G-EVIDENCE-SUBSTRING etc. become N/A)")
    p.add_argument("--json", action="store_true",
                   help="emit the report as JSON (for CI artifacts)")
    args = p.parse_args()

    raw_numbers = _read_numbers(args.csv)

    target_spec = None
    if not args.no_spec:
        if not os.path.isfile(args.spec):
            print(f"WARNING: spec not found ({args.spec}); running without comparison gates",
                  file=sys.stderr)
        else:
            with open(args.spec, encoding="utf-8") as f:
                target_spec = f.read()

    report = run_selfcheck(raw_numbers, target_spec=target_spec,
                           source_factory=FixtureSource)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
