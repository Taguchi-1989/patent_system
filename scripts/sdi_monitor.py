"""SDI monitor (M11): run a saved search brief, report ONLY new hits.

    # 初回（ベースライン作成）と2回目以降は同じコマンド:
    py scripts/sdi_monitor.py samples/search_query_SAMPLE.json \
        --from-export <BigQueryコンソールの結果JSON>

    # ライブ（要 google-cloud-bigquery + gcloud ADC）:
    py scripts/sdi_monitor.py mybrief.json --live --project my-gcp-project

State : monitor_state/sdi/<brief name>.json   (committable; Actions cron で回せる)
Output: outputs/sdi_<name>.md（新着のみ・根拠付き / 新着ゼロは「変更なし」明示）
        outputs/sdi_<name>_new.csv（新着のみ、run_pipeline/build_site の入力形式）
"""

import argparse
import datetime
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.search import (                                  # noqa: E402
    apply_semantic,
    build_fetch_sql,
    build_search_sql,
    load_query_spec,
    rank_rows,
)
from patentkit.search.report import candidates_csv              # noqa: E402
from patentkit.search.sdi import (                              # noqa: E402
    load_state,
    render_sdi_report,
    save_state,
    split_new,
    update_state,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT_DIR = os.path.join(ROOT, "outputs")
STATE_DIR = os.path.join(ROOT, "monitor_state", "sdi")


def _load_export(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
    if content.startswith("["):
        return json.loads(content)
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brief", help="search brief JSON (the watch theme)")
    ap.add_argument("--from-export", help="BigQuery console export JSON (zero-install route)")
    ap.add_argument("--live", action="store_true", help="run live via google-cloud-bigquery")
    ap.add_argument("--project", help="GCP project for --live")
    ap.add_argument("--state-dir", default=STATE_DIR, help="theme state dir (default: monitor_state/sdi)")
    ap.add_argument("--out-dir", default=OUT_DIR, help="output dir (default: outputs/)")
    ap.add_argument("--run-date", help="override run date YYYY-MM-DD (for tests/reproduction)")
    ap.add_argument("--semantic", choices=["none", "tfidf"], default="tfidf",
                    help="意味チャネル (既定 tfidf=鍵ゼロ)")
    args = ap.parse_args()

    q = load_query_spec(args.brief)
    if not args.from_export and not args.live:
        sys.exit("SDIは結果が必要です: --from-export <JSON> か --live を指定してください。\n"
                 "SQLだけ欲しい場合は scripts/search_patents.py を使用。")

    if args.live:
        try:
            from google.cloud import bigquery  # noqa: PLC0415
        except ImportError:
            sys.exit("google-cloud-bigquery が未インストールです。鍵ゼロ経路は --from-export。")
        client = bigquery.Client(project=args.project or os.environ.get("GCP_PROJECT_ID"))
        rows = [dict(r) for r in client.query(build_search_sql(q)).result()]
    else:
        rows = _load_export(args.from_export)

    cands = rank_rows(rows, q)
    if args.semantic != "none":
        cands = apply_semantic(cands, rows, q)

    run_date = args.run_date or datetime.date.today().isoformat()
    state_path = os.path.join(args.state_dir, f"{q.name}.json")
    state = load_state(state_path)
    first_run = not state["runs"]
    new, _seen = split_new(cands, state["seen"])
    state = update_state(state, cands, run_date, new_count=len(new), total_rows=len(rows))
    save_state(state_path, state)

    os.makedirs(args.out_dir, exist_ok=True)
    md_path = os.path.join(args.out_dir, f"sdi_{q.name}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_sdi_report(q, new, seen_total=len(state["seen"]),
                                  run_date=run_date, total_hits=len(cands)))
    csv_path = os.path.join(args.out_dir, f"sdi_{q.name}_new.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(candidates_csv(new))
    fetch_path = None
    if new:
        # Search rows carry no claims (cost) — emit the claims-included fetch
        # SQL for the new hits so the FTO handoff has claim text.
        fetch_path = os.path.join(args.out_dir, f"sdi_{q.name}_fetch.sql")
        with open(fetch_path, "w", encoding="utf-8") as f:
            f.write(build_fetch_sql([c.publication_number for c in new],
                                    label=f"sdi:{q.name} new hits"))

    label = "初回ベースライン" if first_run else "差分"
    print(f"[{label}] ヒット {len(cands)} 件 / 新着 {len(new)} 件 / 既知合計 {len(state['seen'])} 件")
    print(f"[saved] {md_path}")
    print(f"[saved] {csv_path}")
    print(f"[state] {state_path}")
    if new:
        print(f"[saved] {fetch_path}")
        print("新着あり → fetch SQLをコンソールで実行(claims込み) → JSON保存 →")
        print(f"    py scripts/build_site.py {csv_path} --source bq-export "
              "--export <その結果JSON> --spec <自社仕様> でFTOトリアージへ")


if __name__ == "__main__":
    main()
