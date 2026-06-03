"""Render summaries + a comparison table to NotebookLM-friendly Markdown.

Every report carries the mandatory disclaimer (§6.5) and per-item provenance
(§7.2). The comparison table here reports only FACTUAL, source-backed fields
(office, status, claim count, family). The semantic MATCH/MISSING/UNCLEAR
column is added by render_report() when comparisons are provided.
"""

from __future__ import annotations

from ..analyze import PatentSummary
from ..analyze.compare import ComparisonResult, Verdict
from ..connectors.base import PatentRecord

DISCLAIMER = (
    "> **注記**: 本レポートはAIによる支援結果であり、侵害の有無や法的結論を確定するもの"
    "ではありません。最終判断は弁理士・弁護士等の専門家確認を前提とします。"
)


def _comparison_table(records: list[PatentRecord]) -> str:
    head = "| 番号 | 庁 | タイトル | 状態 | 請求項数 | ファミリー | 出典 |"
    sep = "|---|---|---|---|---|---|---|"
    rows = [head, sep]
    for r in records:
        rows.append(
            f"| {r.canonical} | {r.office} | {r.title or '—'} | "
            f"{r.legal_status or '—'} | {len(r.claims)} | {r.family_id or '—'} | {r.source} |"
        )
    return "\n".join(rows)


def _patent_section(summary: PatentSummary) -> str:
    lines = [f"### {summary.canonical} — {summary.title or '(no title)'}", ""]
    lines.append(f"- **書誌**: {summary.one_line}")
    lines.append(f"- **出典**: {summary.source}"
                 + (f" ({summary.source_url})" if summary.source_url else ""))
    lines.append(f"- **請求項数**: {summary.claim_count}")
    if summary.breakdown:
        lines.append(f"- **独立請求項（claim {summary.breakdown.claim_no}）の要素分解"
                     "（ヒューリスティック・要検証）**:")
        for i, el in enumerate(summary.breakdown.elements, 1):
            lines.append(f"  {i}. {el}")
    for note in summary.notes:
        lines.append(f"- _note_: {note}")
    return "\n".join(lines)


def _semantic_table(result: ComparisonResult) -> str:
    """Render a per-element semantic comparison table for one patent.

    Columns: 請求項要素 | 判定 | 根拠スパン | 信頼度
    All evidence spans are actual substrings of the target spec (P-NO-GUESS).
    """
    head = "| 請求項要素 | 判定 | 根拠スパン | 信頼度 |"
    sep = "|---|---|---|---|"
    rows = [head, sep]
    for v in result.verdicts:
        # Truncate long elements and spans for table readability.
        elem = v.element[:80].replace("|", "｜")
        span = (v.evidence_span[:100] + "…" if len(v.evidence_span) > 100
                else v.evidence_span).replace("|", "｜") or "—"
        conf = f"{v.confidence:.2f}"
        rows.append(f"| {elem} | {v.verdict.value} | {span} | {conf} |")
    return "\n".join(rows)


def _escalation_section(results: list[ComparisonResult]) -> str:
    """Render the UNCLEAR → 要人手確認 escalation section."""
    lines = ["## UNCLEAR → 要人手確認リスト", ""]
    any_unclear = False
    for result in results:
        unclear = result.unclear()
        if not unclear:
            continue
        any_unclear = True
        lines.append(f"### {result.patent_canonical}")
        lines.append("")
        for v in unclear:
            lines.append(f"- **要素**: {v.element[:120]}")
            lines.append(f"  - 根拠スパン: {v.evidence_span[:120] or '（なし）'}")
            lines.append(f"  - 信頼度: {v.confidence:.2f}")
            lines.append(f"  - 理由: {v.rationale[:200]}")
            lines.append("")
    if not any_unclear:
        lines.append("_UNCLEAR 判定はありません。全要素が MATCH または MISSING でした。_")
        lines.append("")
    return "\n".join(lines)


def render_report(
    summaries: list[PatentSummary],
    records: list[PatentRecord],
    not_found: list[str] | None = None,
    comparisons: list[ComparisonResult] | None = None,
) -> str:
    parts = ["# 特許調査サマリ（自動生成・初稿）", "", DISCLAIMER, ""]
    parts.append("## 比較表（事実フィールドのみ）")
    parts.append("")
    parts.append(_comparison_table(records) if records else "_取得できた案件がありません。_")
    parts.append("")
    if comparisons is None:
        parts.append("> MATCH / MISSING / UNCLEAR の意味判定はLLM意味解析（エージェント実施）"
                     "の工程で付与します。本表はソース由来の事実のみを載せています。")
    parts.append("")
    parts.append("## 案件別サマリ")
    parts.append("")
    for s in summaries:
        parts.append(_patent_section(s))
        parts.append("")

    # --- M4: Semantic comparison tables (present only when comparisons given) ---
    if comparisons:
        # Build lookup by canonical for pairing with summaries.
        parts.append("## 意味比較（MATCH / MISSING / UNCLEAR）")
        parts.append("")
        spec_title = comparisons[0].target_spec_title
        parts.append(f"> 対象仕様: **{spec_title}**")
        parts.append("")
        for result in comparisons:
            c_counts = result.counts()
            parts.append(
                f"### {result.patent_canonical} — "
                f"MATCH: {c_counts[Verdict.MATCH.value]}, "
                f"MISSING: {c_counts[Verdict.MISSING.value]}, "
                f"UNCLEAR: {c_counts[Verdict.UNCLEAR.value]}"
            )
            parts.append(f"_出典: {result.source}"
                         + (f" ({result.source_url})" if result.source_url else "") + "_")
            parts.append("")
            parts.append(_semantic_table(result))
            parts.append("")
        parts.append(_escalation_section(comparisons))

    if not_found:
        parts.append("## 取得できなかった番号")
        parts.append("")
        for nf in not_found:
            parts.append(f"- {nf} — このデータ源に該当レコードなし（別ソース/手段を検討）")
        parts.append("")
    return "\n".join(parts)
