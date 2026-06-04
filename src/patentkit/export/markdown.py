"""Render summaries + a comparison table to NotebookLM-friendly Markdown.

Every report carries the mandatory disclaimer (§6.5) and per-item provenance
(§7.2). The comparison table here reports only FACTUAL, source-backed fields
(office, status, claim count, family). The semantic MATCH/MISSING/UNCLEAR
column is added by render_report() when comparisons are provided.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..analyze import PatentSummary
from ..analyze.compare import ComparisonResult, Verdict
from ..connectors.base import PatentRecord

if TYPE_CHECKING:
    from ..analyze.score import PatentScore

DISCLAIMER = (
    "> **注記**: 本レポートはAIによる支援結果であり、侵害の有無や法的結論を確定するもの"
    "ではありません。最終判断は弁理士・弁護士等の専門家確認を前提とします。"
)

_BAND_JA = {"HIGH": "高", "MEDIUM": "中", "LOW": "低", "UNKNOWN": "不明"}


def _screening_table(scores: "list[PatentScore]") -> str:
    """Triage table sorted high-risk first: 番号 | リスク | 推定カバレッジ | 最弱要素 | 欠落 | 要確認."""
    from ..analyze.score import triage_sort_key

    head = "| 抵触リスク | 番号 | 推定カバレッジ | 推定レンジ | 最弱要素 | 欠落 | 要確認 |"
    sep = "|---|---|---|---|---|---|---|"
    rows = [head, sep]
    for s in sorted(scores, key=triage_sort_key):
        band = _BAND_JA.get(s.risk_band, s.risk_band)
        rows.append(
            f"| **{band}** | {s.canonical} | {s.coverage_pct:.0f}% | "
            f"{s.band_low:.0f}–{s.band_high:.0f}% | {s.min_coverage_pct:.0f}% | "
            f"{s.gap_count}/{s.n_elements} | {s.review_count}/{s.n_elements} |"
        )
    return "\n".join(rows)


def _cell(text: str, limit: int = 140) -> str:
    """Sanitize a string for a Markdown table cell (escape pipes, collapse newlines)."""
    t = " ".join((text or "").split())
    if len(t) > limit:
        t = t[: limit - 1] + "…"
    return t.replace("|", "｜")


_COV_BAND_JA = {"covered": "カバー", "partial": "一部", "gap": "欠落"}


def _claimchart_section(scores: "list[PatentScore]") -> str:
    """Per-patent claim chart (対比表) + proposals, grounded in verbatim quotes.

    Every spec cell is a verbatim substring of the target spec; proposals cite
    the same verbatim basis (or honestly state absence) — no generated prose.
    """
    lines = ["## クレームチャート・対比表（根拠は逐語引用）", ""]
    for s in scores:
        if not s.elements:
            continue
        lines.append(f"### {s.canonical} — 抵触リスク **{_BAND_JA.get(s.risk_band, s.risk_band)}**"
                     f"（推定カバレッジ {s.coverage_pct:.0f}%）")
        lines.append("")
        lines.append("| # | 請求項要素（本文） | 対象仕様の対応記載（逐語引用） | 判定 | 一致語 |")
        lines.append("|---|---|---|---|---|")
        for i, c in enumerate(s.elements, 1):
            quote = f"「{_cell(c.evidence_span)}」" if c.evidence_span else "_対応記載なし（欠落）_"
            terms = "、".join(c.matched_terms) if c.matched_terms else "—"
            band = _COV_BAND_JA.get(c.band, c.band)
            lines.append(
                f"| {i} | {_cell(c.element)} | {quote} | {band}({c.p_coverage:.0%}) | {_cell(terms, 60)} |"
            )
        lines.append("")
        if s.proposals:
            lines.append("**提案・次アクション**")
            lines.append("")
            for p in s.proposals:
                lines.append(f"- **[{p.category}]** {p.text}")
                if p.basis:
                    lines.append(f"  - 根拠（仕様より逐語引用）: 「{_cell(p.basis)}」")
            lines.append("")
    return "\n".join(lines)


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
    scores: "list[PatentScore] | None" = None,
) -> str:
    parts = ["# 特許調査サマリ（自動生成・初稿）", "", DISCLAIMER, ""]

    # --- M8: FTO triage screening table (present only when scores given) ---
    if scores:
        parts.append("## スクリーニング（FTO 抵触リスク・高い順）")
        parts.append("")
        parts.append(f"> 対象仕様: **{scores[0].target_spec_title}**　"
                     "／ 推定カバレッジ = 決定論チャネル×LLMチャネルの融合、"
                     "レンジ = 2チャネルの一致度(SN比)による信頼幅。")
        parts.append("")
        parts.append(_screening_table(scores))
        parts.append("")
        parts.append(_claimchart_section(scores))
        parts.append("")

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
