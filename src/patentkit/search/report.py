"""Render discovery results: search log (Markdown) + candidates CSV.

The Markdown is a search-formula record (サーチ式記録) in the professional
sense: brief, executed SQL, hit counts, and a ranked table with verbatim
evidence — so a later reader can reproduce and audit the search.

The CSV is `number,note` — the exact input format of run_pipeline/build_site,
so discovery feeds the existing FTO pipeline with no glue.
"""

from __future__ import annotations

import csv
import io

from .query import SearchQuery, build_search_sql
from .rank import Candidate


def candidates_csv(cands: list[Candidate]) -> str:
    """Render candidates as the pipeline's number,note input CSV."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["number", "note"])
    for c in cands:
        note = f"score={c.score} ({c.groups_hit}/{c.groups_total} concepts)"
        if c.matched_terms:
            note += " " + "/".join(c.matched_terms)
        if c.needs_review:
            note += " [needs_review]"
        w.writerow([c.publication_number, note])
    return buf.getvalue()


def render_search_report(q: SearchQuery, cands: list[Candidate], total_rows: int) -> str:
    """Markdown search log: reproducible brief + SQL + ranked evidence table."""
    lines: list[str] = []
    lines.append(f"# 調査ログ: {q.name}")
    lines.append("")
    if q.purpose:
        lines.append(f"**目的**: {q.purpose}")
        lines.append("")
    lines.append("## サーチ式（検索条件の記録）")
    lines.append("")
    for i, group in enumerate(q.keywords, 1):
        lines.append(f"- 概念{i}（OR）: " + " / ".join(f"`{k}`" for k in group))
    if q.cpc:
        lines.append("- CPC（前方一致）: " + ", ".join(f"`{c}`" for c in q.cpc))
    if q.assignees:
        lines.append("- 出願人: " + ", ".join(q.assignees))
    if q.countries:
        lines.append("- 国: " + ", ".join(q.countries))
    if q.date_from or q.date_to:
        lines.append(f"- 公開日: {q.date_from or '…'} 〜 {q.date_to or '…'}")
    lines.append(f"- 上限: {q.limit} 件 / クレーム本文検索: {'あり' if q.search_claims else 'なし'}")
    lines.append("")
    lines.append("## 実行SQL（再現用）")
    lines.append("")
    lines.append("```sql")
    lines.append(build_search_sql(q).rstrip())
    lines.append("```")
    lines.append("")
    flagged = sum(1 for c in cands if c.needs_review)
    lines.append("## 結果")
    lines.append("")
    lines.append(
        f"取得 {total_rows} 行 → ファミリー集約後 **{len(cands)} 件**"
        + (f"（うち要確認 {flagged} 件）" if flagged else "")
    )
    lines.append("")
    lines.append("| # | 番号 | スコア | 概念 | タイトル | 根拠（逐語） |")
    lines.append("|---|---|---|---|---|---|")
    for i, c in enumerate(cands, 1):
        flag = " ⚠" if c.needs_review else ""
        ev = "<br>".join(c.evidence) if c.evidence else "—"
        title = (c.title[:80] + "…") if len(c.title) > 80 else c.title
        lines.append(
            f"| {i} | [{c.publication_number}](https://patents.google.com/patent/"
            f"{c.publication_number.replace('-', '')}) | {c.score}{flag} | "
            f"{c.groups_hit}/{c.groups_total} | {title} | {ev} |"
        )
    lines.append("")
    notes = [n for c in cands for n in c.notes]
    if notes:
        lines.append("## 注記（P-NO-GUESS）")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "次の一手: `outputs/candidates.csv` をそのまま既存パイプラインへ — "
        "`py scripts/build_site.py outputs/candidates.csv --source bq-export --export <結果JSON> --spec <自社仕様>`"
    )
    lines.append("")
    return "\n".join(lines)
