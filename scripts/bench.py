"""Pipeline latency benchmark: normalize / fetch / summarize / compare.

Runs each pipeline stage on every record in samples/bq_export_SAMPLE.json,
prints per-stage median latency (ms) and total record count.

Also includes a pure token-cost ESTIMATOR function (stdlib only):
  estimate_tokens(text) -> int   (heuristic: words * 1.3)
  estimate_token_cost(n_tokens, rate_per_1k=0.003) -> float (USD)

These are documented as ESTIMATES for planning the future LLM-judge path —
they use a words*1.3 approximation, not a real tokenizer. Actual charges
will differ.

Exit code: always 0.

    py scripts/bench.py
    py scripts/bench.py --samples path/to/export.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.analyze.compare import HeuristicJudge, compare  # noqa: E402
from patentkit.analyze.summarize import summarize               # noqa: E402
from patentkit.connectors import record_from_bq_row             # noqa: E402
from patentkit.normalize import normalize                        # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEFAULT_SAMPLE = os.path.join(ROOT, "samples", "bq_export_SAMPLE.json")


def _record_from_row(row: dict):
    """Wrap record_from_bq_row; returns None only if row has no publication_number."""
    if not row.get("publication_number"):
        return None
    return record_from_bq_row(row)

# A simple target spec used to exercise the compare stage.
_BENCH_SPEC = (
    "Benchmark target specification for latency measurement. "
    "This device comprises a sensor, a controller, and a coil."
)


# ---------------------------------------------------------------------------
# Pure estimator functions (importable, no side effects)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Estimate token count from text using a words * 1.3 heuristic.

    ESTIMATE ONLY — uses a naive word-count approximation, not a real
    tokenizer. Actual LLM token counts will differ (typically ±20%).
    Suitable for planning and cost projection only.
    """
    if not text:
        return 0
    words = len(re.findall(r"\S+", text))
    return int(words * 1.3)


def estimate_token_cost(n_tokens: int, rate_per_1k: float = 0.003) -> float:
    """Estimate USD cost for n_tokens at rate_per_1k dollars per 1 000 tokens.

    ESTIMATE ONLY — uses the estimate_tokens heuristic and a default rate of
    $0.003 / 1k tokens (a planning placeholder; real model pricing varies).
    Not an actual charge; intended for the future LLM-judge path projection.

    Args:
        n_tokens: Number of tokens (e.g., from estimate_tokens()).
        rate_per_1k: Cost per 1 000 tokens in USD (default 0.003).

    Returns:
        Estimated cost in USD as a float.
    """
    return (n_tokens / 1000.0) * rate_per_1k


# ---------------------------------------------------------------------------
# Latency helpers
# ---------------------------------------------------------------------------

def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2.0
    return s[mid]


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Pipeline stage latency benchmark")
    p.add_argument(
        "--samples",
        default=DEFAULT_SAMPLE,
        help="Path to BigQuery-console-exported JSON sample file",
    )
    args = p.parse_args()

    samples_path = os.path.normpath(args.samples)
    if not os.path.isfile(samples_path):
        print(f"ERROR: sample file not found: {samples_path}")
        return 0

    print(f"Benchmark: {samples_path}")
    print()

    # Load raw rows.
    with open(samples_path, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        rows = [rows]

    n_records = len(rows)
    print(f"Records: {n_records}")
    print()

    # Stage timing lists (ms).
    t_normalize: list[float] = []
    t_fetch: list[float] = []
    t_summarize: list[float] = []
    t_compare: list[float] = []

    judge = HeuristicJudge()

    for row in rows:
        pub_number = row.get("publication_number", "")
        if not pub_number:
            continue

        # Stage 1: normalize (measure cost of canonicalization)
        t0 = time.perf_counter()
        cn = normalize(pub_number)
        t_normalize.append((time.perf_counter() - t0) * 1000)

        # Stage 2: fetch (measure record_from_bq_row mapping, the hot path
        # for bq-export since the in-memory lookup is O(1) dict access)
        t0 = time.perf_counter()
        rec = _record_from_row(row)
        t_fetch.append((time.perf_counter() - t0) * 1000)

        if rec is None:
            continue

        # Stage 3: summarize
        t0 = time.perf_counter()
        summary = summarize(rec)
        t_summarize.append((time.perf_counter() - t0) * 1000)

        # Stage 4: compare
        t0 = time.perf_counter()
        compare(_BENCH_SPEC, summary, judge=judge)
        t_compare.append((time.perf_counter() - t0) * 1000)

    # Print per-stage latency table.
    print(f"{'Stage':<15} {'Median (ms)':>13} {'Count':>7}")
    print("-" * 38)
    stages = [
        ("normalize", t_normalize),
        ("fetch", t_fetch),
        ("summarize", t_summarize),
        ("compare", t_compare),
    ]
    for stage_name, times in stages:
        med = _median(times)
        print(f"  {stage_name:<13} {med:>13.3f} {len(times):>7}")

    # Token cost estimate for compare stage input.
    total_tokens = 0
    for row in rows:
        # Approximate tokens for a typical compare call: spec + claims.
        claims_text = ""
        for cl in row.get("claims_localized", []):
            claims_text += cl.get("text", "")
        combined = _BENCH_SPEC + " " + claims_text
        total_tokens += estimate_tokens(combined)

    avg_tokens = total_tokens // max(1, n_records)
    cost_est = estimate_token_cost(total_tokens)

    print()
    print("Token cost estimate (ESTIMATE — heuristic word*1.3, not a real charge):")
    print(f"  Total estimated tokens (all records): {total_tokens}")
    print(f"  Average estimated tokens per record:  {avg_tokens}")
    print(f"  Estimated cost at $0.003/1k tokens:   ${cost_est:.6f}")
    print()
    print("Note: these figures use words*1.3 approximation for planning the")
    print("      future LLM-judge path. Real tokenizer counts will differ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
