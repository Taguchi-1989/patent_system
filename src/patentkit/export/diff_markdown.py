"""Render a field-level diff report as NotebookLM-friendly Markdown.

Every report carries the mandatory DISCLAIMER (§6.5) and honest disclosure
of NOT_YET_TRACKED fields (P-NO-GUESS requirement).

Sections:
  1. Header + DISCLAIMER
  2. Provenance block (source label, generation timestamp)
  3. NOT_YET_TRACKED disclosure
  4. Per-patent change tables (one section per RecordDiff with .changed == True)
  5. Auto-generated 差分通知文 (notification draft) summarising all changes
"""

from __future__ import annotations

from datetime import datetime, timezone

from .markdown import DISCLAIMER
from ..state.diff import NOT_YET_TRACKED, RecordDiff


def render_diff_report(
    diffs: list[RecordDiff],
    source_label: str | None = None,
) -> str:
    """Render a Markdown diff report for a list of RecordDiff objects.

    Only records with ``RecordDiff.changed == True`` are shown in the per-patent
    tables.  Unchanged records are mentioned in the summary count only.

    Parameters
    ----------
    diffs:
        All RecordDiff objects from a monitoring run.
    source_label:
        Human-readable label for the data source (e.g. ``"bq-export"``).

    Returns
    -------
    str
        Full Markdown string including DISCLAIMER and provenance.
    """
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = [d for d in diffs if d.changed]
    unchanged_count = len(diffs) - len(changed)

    parts: list[str] = []

    # 1. Header + DISCLAIMER
    parts.append("# 特許差分レポート（自動生成・初稿）")
    parts.append("")
    parts.append(DISCLAIMER)
    parts.append("")

    # 2. Provenance
    parts.append("## レポート情報")
    parts.append("")
    if source_label:
        parts.append(f"- **データソース**: {source_label}")
    parts.append(f"- **生成日時 (UTC)**: {generated_at}")
    parts.append(f"- **監視対象**: {len(diffs)} 件  "
                 f"（変更あり: **{len(changed)}** 件, 変更なし: {unchanged_count} 件）")
    parts.append("")

    # 3. NOT_YET_TRACKED disclosure (P-NO-GUESS honesty requirement)
    parts.append("## 未追跡項目（§6.4 差分対象外）")
    parts.append("")
    parts.append("以下の監視項目は現在の PatentRecord モデルに含まれておらず、差分対象外です。"
                 "データソース統合後に追加予定です。")
    parts.append("")
    for item in NOT_YET_TRACKED:
        parts.append(f"- {item}")
    parts.append("")

    # 4. Per-patent change tables
    if changed:
        parts.append("## 変更案件の差分詳細")
        parts.append("")
        for diff in changed:
            parts.append(f"### {diff.canonical}")
            parts.append("")
            parts.append(_change_table(diff))
            parts.append("")
            # Claim-positional-diff caveat
            by_field = diff.by_field()
            if "claims" in by_field:
                parts.append(
                    "> **注**: 請求項差分はインデックス（位置）ベースで比較しています。"
                    "請求項が中間に挿入された場合は複数の変更として表示される場合があります（P-NO-GUESS）。"
                )
                parts.append("")
    else:
        parts.append("## 変更案件の差分詳細")
        parts.append("")
        parts.append("_変更が検出された案件はありません。_")
        parts.append("")

    # 5. 差分通知文 (notification draft)
    parts.append("## 差分通知文（自動生成・要確認）")
    parts.append("")
    parts.append(_notification_draft(diffs, changed, generated_at, source_label))
    parts.append("")

    return "\n".join(parts)


def _change_table(diff: RecordDiff) -> str:
    """Render a Markdown table of all changes for one patent."""
    head = "| フィールド | 種別 | 変更前 | 変更後 |"
    sep  = "|---|---|---|---|"
    rows = [head, sep]
    for change in diff.changes:
        before_str = _cell(change.before)
        after_str  = _cell(change.after)
        kind_ja = {"changed": "変更", "added": "追加", "removed": "削除"}.get(
            change.kind, change.kind
        )
        rows.append(f"| `{change.field}` | {kind_ja} | {before_str} | {after_str} |")
    return "\n".join(rows)


def _cell(value: object) -> str:
    """Format a field value for display in a Markdown table cell."""
    if value is None:
        return "—"
    text = str(value)
    # Truncate long texts (e.g. full claim text) for table readability.
    if len(text) > 120:
        text = text[:117] + "…"
    # Escape pipe characters to avoid breaking table formatting.
    return text.replace("|", "｜")


def _notification_draft(
    diffs: list[RecordDiff],
    changed: list[RecordDiff],
    generated_at: str,
    source_label: str | None,
) -> str:
    """Generate a short plain-Japanese notification draft."""
    lines: list[str] = []
    source_text = f"（{source_label}）" if source_label else ""
    lines.append(f"監視対象 {len(diffs)} 件のうち、{generated_at}{source_text} 時点で以下の変更が検出されました。")
    lines.append("")
    if not changed:
        lines.append("変更なし。全案件のステータスは前回取得時と同一です。")
        return "\n".join(lines)

    for diff in changed:
        by_field = diff.by_field()
        field_summaries: list[str] = []
        if "legal_status" in by_field:
            fc = by_field["legal_status"][0]
            field_summaries.append(f"法的状態: {fc.before} → {fc.after}")
        if "claims" in by_field:
            n_claims = len(by_field["claims"])
            field_summaries.append(f"請求項変更 {n_claims} 箇所")
        if "family_id" in by_field:
            fc = by_field["family_id"][0]
            field_summaries.append(f"ファミリーID: {fc.before} → {fc.after}")
        other = [
            k for k in by_field
            if k not in {"legal_status", "claims", "family_id"}
        ]
        if other:
            field_summaries.append(f"その他変更: {', '.join(other)}")
        summary = "、".join(field_summaries) if field_summaries else "（詳細は上表参照）"
        lines.append(f"- **{diff.canonical}**: {summary}")

    lines.append("")
    lines.append("_本通知文は自動生成の初稿です。内容を確認のうえ適宜修正してください。_")
    return "\n".join(lines)
