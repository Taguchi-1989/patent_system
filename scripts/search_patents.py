"""Discovery (M8): search brief -> SQL -> ranked candidate list.

    # 1. Emit console-pasteable SQL from a search brief (zero keys, zero install)
    py scripts/search_patents.py samples/search_query_SAMPLE.json

    # 2. Rank a BigQuery console export (JSON) into candidates + search log
    py scripts/search_patents.py samples/search_query_SAMPLE.json \
        --from-export samples/search_export_SAMPLE.json

    # 3. Live (optional): needs google-cloud-bigquery + gcloud ADC login
    py scripts/search_patents.py mybrief.json --live --project my-gcp-project

Outputs (mode 2/3): outputs/candidates.csv (feeds run_pipeline/build_site
directly) and outputs/search_report.md (reproducible search log).
"""

import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.search import (                                              # noqa: E402
    apply_semantic,
    build_search_sql,
    load_query_spec,
    make_embedder_from_env,
    rank_rows,
)
from patentkit.search.report import candidates_csv, render_search_report    # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT_DIR = os.path.join(ROOT, "outputs")


def _load_export(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
    if content.startswith("["):
        return json.loads(content)
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def _run_live(sql: str, project: str | None) -> list[dict]:
    try:
        from google.cloud import bigquery  # noqa: PLC0415
    except ImportError:
        sys.exit(
            "google-cloud-bigquery が未インストールです。`pip install google-cloud-bigquery` "
            "するか、鍵ゼロ経路（SQLをコンソールで実行 → JSON保存 → --from-export）を使ってください。"
        )
    client = bigquery.Client(project=project or os.environ.get("GCP_PROJECT_ID"))
    return [dict(row) for row in client.query(sql).result()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brief", help="search brief JSON (see samples/search_query_SAMPLE.json)")
    ap.add_argument("--from-export", help="BigQuery console export JSON to rank (zero-install route)")
    ap.add_argument("--live", action="store_true", help="run the query live via google-cloud-bigquery")
    ap.add_argument("--project", help="GCP project for --live (default: GCP_PROJECT_ID env)")
    ap.add_argument("--out-dir", default=OUT_DIR, help="output directory (default: outputs/)")
    ap.add_argument("--semantic", choices=["none", "tfidf", "azure", "github"], default="tfidf",
                    help="意味チャネル(recall): tfidf = 鍵ゼロTF-IDF (既定), "
                         "azure/github = APIエンベッダ, none = キーワードのみ")
    args = ap.parse_args()

    q = load_query_spec(args.brief)
    sql = build_search_sql(q)

    os.makedirs(args.out_dir, exist_ok=True)
    sql_path = os.path.join(args.out_dir, "search.sql")
    with open(sql_path, "w", encoding="utf-8") as f:
        f.write(sql)

    if not args.from_export and not args.live:
        print(sql)
        print(f"[saved] {sql_path}")
        print("次: BigQueryコンソールで実行 → 結果をJSON保存 → --from-export <file> で再実行")
        return

    rows = _run_live(sql, args.project) if args.live else _load_export(args.from_export)
    cands = rank_rows(rows, q)

    if args.semantic != "none":
        embedder = None
        if args.semantic in ("azure", "github"):
            embedder = make_embedder_from_env(provider=args.semantic)
            if embedder is None:
                sys.exit(f"--semantic {args.semantic} の鍵/設定が見つかりません"
                         "（Azure: AZURE_OPENAI_* + AZURE_OPENAI_EMBED_DEPLOYMENT, "
                         "GitHub: GITHUB_MODELS_TOKEN）。鍵ゼロなら --semantic tfidf。")
            print(f"semantic channel: {embedder.name} (model={embedder.model})")
        cands = apply_semantic(cands, rows, q, embedder=embedder)

    csv_path = os.path.join(args.out_dir, "candidates.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(candidates_csv(cands))
    md_path = os.path.join(args.out_dir, "search_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_search_report(q, cands, total_rows=len(rows)))

    flagged = sum(1 for c in cands if c.needs_review)
    print(f"{len(rows)} 行 → 候補 {len(cands)} 件（要確認 {flagged}）")
    print(f"[saved] {csv_path}")
    print(f"[saved] {md_path}")
    print("次: candidates.csv を build_site.py / run_pipeline.py へそのまま入力できます")


if __name__ == "__main__":
    main()
