"""SDI (Selective Dissemination of Information) — 定期監視テーマ (M11).

A saved search brief becomes a WATCH: run the same search periodically, keep a
seen-set of publication numbers per theme, and report ONLY what is new since
the last run. This connects the discovery layer (M8/M10) to the monitoring
loop (M6 GitHub Actions cron).

State is a small JSON file per theme ({"seen": {pub: first_seen_date}, "runs":
[...]}) — text-diffable, committable, no database. All set logic is in pure
functions so it tests without filesystem or clock.
"""

from __future__ import annotations

import json
import os

from .query import SearchQuery
from .rank import Candidate

_GP = "https://patents.google.com/patent/{n}"


def load_state(path: str) -> dict:
    """Load a theme state file; a missing file is an empty (first-run) state."""
    if not os.path.exists(path):
        return {"seen": {}, "runs": []}
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("seen", {})
    state.setdefault("runs", [])
    return state


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def split_new(cands: list[Candidate], seen: dict[str, str]) -> tuple[list[Candidate], list[Candidate]]:
    """(new, already_seen) by publication number. Pure."""
    new = [c for c in cands if c.publication_number not in seen]
    old = [c for c in cands if c.publication_number in seen]
    return new, old


def update_state(state: dict, cands: list[Candidate], run_date: str,
                 new_count: int, total_rows: int) -> dict:
    """Record this run and absorb all current hits into the seen-set. Pure-ish."""
    for c in cands:
        state["seen"].setdefault(c.publication_number, run_date)
    state["runs"].append({"date": run_date, "hits": len(cands),
                          "new": new_count, "rows": total_rows})
    return state


def render_sdi_report(q: SearchQuery, new: list[Candidate], seen_total: int,
                      run_date: str, total_hits: int) -> str:
    """新着のみのSDIレポート。新着ゼロなら「変更なし」を明示（沈黙しない）。"""
    lines: list[str] = []
    lines.append(f"# SDI監視: {q.name}（{run_date}）")
    lines.append("")
    if q.purpose:
        lines.append(f"**テーマ**: {q.purpose}")
        lines.append("")
    lines.append(f"今回ヒット {total_hits} 件 / 既知 {seen_total} 件 / **新着 {len(new)} 件**")
    lines.append("")
    if not new:
        lines.append("**変更なし** — 前回以降、このサーチ式に新しい候補はありません。")
        lines.append("")
        return "\n".join(lines)
    lines.append("| # | 番号 | 決定論 | 意味 | 概念 | タイトル | 根拠（逐語） |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(new, 1):
        flag = " ⚠" if c.needs_review else ""
        sem = f"{c.semantic:.2f}" if c.semantic is not None else "—"
        ev = "<br>".join(c.evidence) if c.evidence else "—"
        title = (c.title[:80] + "…") if len(c.title) > 80 else c.title
        lines.append(
            f"| {i} | [{c.publication_number}]({_GP.format(n=c.publication_number.replace('-', ''))}) | "
            f"{c.score}{flag} | {sem} | {c.groups_hit}/{c.groups_total} | {title} | {ev} |"
        )
    lines.append("")
    lines.append("次の一手: 監視結果にはクレーム本文が無いため、同時生成の "
                 "`sdi_<テーマ>_fetch.sql`（claims込み・新着のみ）をコンソールで実行 → JSON保存 → "
                 "`py scripts/build_site.py sdi_<テーマ>_new.csv --source bq-export "
                 "--export <その結果JSON> --spec <自社仕様>` でFTOトリアージ。")
    lines.append("")
    return "\n".join(lines)
