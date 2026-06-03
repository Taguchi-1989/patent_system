"""Static HTML export for the M5 UI milestone.

Pure functions that build self-contained HTML strings with inline CSS.
ALL dynamic/external text is passed through _esc() (html.escape) before
insertion into any markup — no raw f-string injection of patent data.

Functions:
    render_index(records, summaries, comparisons=None) -> str
        The LIST page with §12 fields.
    render_detail(record, summary, comparison=None, history=None) -> str
        The DETAIL page with bibliography, summary, comparison, and diff history.

Both pages include the DISCLAIMER footer (imported from export.markdown).
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from .markdown import DISCLAIMER

if TYPE_CHECKING:
    from ..connectors.base import PatentRecord
    from ..analyze.summarize import PatentSummary
    from ..analyze.compare import ComparisonResult
    from ..state.diff import RecordDiff

# ---------------------------------------------------------------------------
# Shared CSS (embedded inline in every page)
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 15px;
    line-height: 1.6;
    color: #333;
    background: #fafafa;
    margin: 0;
    padding: 0;
}
.container {
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px 20px 60px;
}
h1 { font-size: 1.6em; color: #1a1a1a; margin-bottom: 4px; }
h2 { font-size: 1.2em; color: #333; border-bottom: 2px solid #ddd; padding-bottom: 4px; margin-top: 32px; }
h3 { font-size: 1.05em; color: #555; margin-top: 20px; }
a { color: #0066cc; text-decoration: none; }
a:hover { text-decoration: underline; }
p { margin: 8px 0; }
.record-count { color: #666; font-size: 0.9em; margin-bottom: 16px; }
/* Tables */
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9em;
    margin: 12px 0;
}
th {
    background: #f0f0f0;
    border: 1px solid #ccc;
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
    white-space: nowrap;
}
td {
    border: 1px solid #ddd;
    padding: 6px 10px;
    vertical-align: top;
    word-break: break-word;
}
tr:nth-child(even) td { background: #f9f9f9; }
tr:hover td { background: #f0f6ff; }
/* Verdict badge spans */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.82em;
    font-weight: 700;
    letter-spacing: 0.03em;
}
.badge-MATCH    { background: #d4edda; color: #155724; }
.badge-MISSING  { background: #f8d7da; color: #721c24; }
.badge-UNCLEAR  { background: #fff3cd; color: #856404; }
/* Verdict cell backgrounds in detail table */
td.verdict-MATCH    { background: #d4edda; }
td.verdict-MISSING  { background: #f8d7da; }
td.verdict-UNCLEAR  { background: #fff3cd; font-weight: 600; }
/* Escalation / review box */
.escalation-box {
    border: 1px solid #ffc107;
    background: #fffdf0;
    border-radius: 4px;
    padding: 12px 16px;
    margin: 16px 0;
}
.escalation-box h3 { color: #856404; margin-top: 0; }
/* Summary count bar */
.count-bar { margin: 8px 0 12px; }
.count-bar span { margin-right: 8px; }
/* Definition list style for bibliographic info */
dl { margin: 8px 0; }
dt { font-weight: 600; color: #555; float: left; width: 120px; clear: left; padding: 3px 0; }
dd { margin-left: 130px; padding: 3px 0; }
dd:after { content: ""; display: table; clear: both; }
/* Section callout for heuristic label */
.heuristic-note {
    font-size: 0.83em;
    color: #888;
    font-style: italic;
    margin-bottom: 6px;
}
/* History sub-section */
.history-entry {
    border-left: 3px solid #ccc;
    padding-left: 14px;
    margin: 14px 0;
}
.history-entry h4 { margin: 0 0 6px; font-size: 0.95em; color: #555; }
/* Back link */
.back-link { margin-bottom: 16px; font-size: 0.9em; }
/* Disclaimer footer */
.disclaimer {
    margin-top: 48px;
    padding: 12px 16px;
    background: #f4f4f4;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-size: 0.83em;
    color: #555;
    line-height: 1.5;
}
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _esc(text: object) -> str:
    """Escape any dynamic/external text for safe HTML insertion.

    Returns '--' for None or empty string (em-dash placeholder).
    Calls html.escape with quote=True to also escape attribute values.
    """
    if text is None:
        return "—"   # em-dash
    s = str(text).strip()
    if not s:
        return "—"
    return html.escape(s, quote=True)


def _esc_raw(text: object) -> str:
    """Like _esc but returns '' for None/empty (used where no fallback needed)."""
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


def _safe_href(url: object) -> str:
    """Return an escaped href ONLY for http(s) URLs; '' otherwise.

    Blocks javascript:, data:, and other script-bearing schemes that
    html.escape() alone would NOT neutralize in an href context. Patent data
    — including user-provided BigQuery JSON exports — is untrusted external
    input, so source_url must be scheme-allowlisted before becoming a link.
    """
    if url is None:
        return ""
    s = str(url).strip()
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return html.escape(s, quote=True)
    return ""


def _safe_filename(canonical: str) -> str:
    """Convert a canonical patent number to a safe filename (no extension).

    Must exactly match the link generation logic in render_index() and the
    file-writing logic in build_site.py. A mismatch breaks navigation.
    """
    # Replace characters invalid in Windows filenames / URL path segments.
    safe = canonical.replace("/", "_").replace("\\", "_").replace(":", "_")
    safe = safe.replace(" ", "_").replace("*", "_").replace("?", "_")
    safe = safe.replace("<", "_").replace(">", "_").replace('"', "_")
    safe = safe.replace("|", "_")
    return safe


def _page_wrapper(title: str, body: str, back_link: str | None = None) -> str:
    """Wrap body HTML in a complete HTML5 document with inline CSS and DISCLAIMER.

    Parameters
    ----------
    title:
        Page <title> (will be escaped).
    body:
        Inner HTML content (assumed already-escaped where needed).
    back_link:
        Optional href for a "< 一覧へ戻る" back link rendered before the body.
    """
    back_html = ""
    if back_link:
        back_html = (
            f'<div class="back-link"><a href="{_esc_raw(back_link)}">'
            f'&larr; 一覧へ戻る</a></div>\n'
        )
    # DISCLAIMER text is a trusted constant imported from markdown.py, not user data.
    # We strip the Markdown bold markers (** ... **) for plain HTML display.
    disclaimer_text = html.escape(
        DISCLAIMER
        .replace("> **注記**: ", "注記: ")
        .replace("**", ""),
        quote=False,
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="container">\n'
        + back_html
        + body
        + f'\n<div class="disclaimer">{disclaimer_text}</div>\n'
        "</div>\n"
        "</body>\n"
        "</html>"
    )


def _verdict_badge(verdict_value: str) -> str:
    """Return an HTML badge span for a verdict value string."""
    return (
        f'<span class="badge badge-{html.escape(verdict_value)}">'
        f'{html.escape(verdict_value)}</span>'
    )


# ---------------------------------------------------------------------------
# render_index
# ---------------------------------------------------------------------------

def render_index(
    records: "list[PatentRecord]",
    summaries: "list[PatentSummary]",
    comparisons: "list[ComparisonResult] | None" = None,
) -> str:
    """Render the LIST page (index.html).

    Columns (§12): 番号 / タイトル / 庁 / 状態 / 請求項数 / 更新日
    When comparisons provided: + MATCH / MISSING / UNCLEAR counts.

    All dynamic text is escaped via _esc(). External links are NEVER
    generated for href attributes from raw patent data — only safe_filename
    is used for relative links to detail pages.

    Parameters
    ----------
    records:
        List of PatentRecord objects (or dicts).
    summaries:
        Corresponding PatentSummary objects (same order).
    comparisons:
        Optional list of ComparisonResult — enables verdict count columns.
    """
    # Build lookup dicts keyed by canonical.
    summary_map: dict[str, object] = {s.canonical: s for s in summaries}
    comparison_map: dict[str, object] = {}
    if comparisons:
        for c in comparisons:
            comparison_map[c.patent_canonical] = c

    show_counts = bool(comparisons)

    # Table header
    th_cells = [
        "<th>番号</th>",
        "<th>タイトル</th>",
        "<th>庁</th>",
        "<th>状態</th>",
        "<th>請求項数</th>",
        "<th>更新日</th>",
    ]
    if show_counts:
        th_cells += ["<th>MATCH</th>", "<th>MISSING</th>", "<th>UNCLEAR</th>"]

    rows_html: list[str] = []
    for rec in records:
        # Support both dataclass instances and plain dicts.
        if isinstance(rec, dict):
            canonical = rec.get("canonical", "")
            title = rec.get("title", "")
            office = rec.get("office", "")
            legal_status = rec.get("legal_status")
            claims = rec.get("claims") or []
            pub_date = rec.get("pub_date")
        else:
            canonical = rec.canonical
            title = rec.title
            office = rec.office
            legal_status = rec.legal_status
            claims = rec.claims
            pub_date = rec.pub_date

        safe_fn = _safe_filename(canonical)
        link_href = f"patents/{safe_fn}.html"
        claim_count = len(claims)

        cells = [
            f'<td><a href="{html.escape(link_href)}">{_esc(canonical)}</a></td>',
            f"<td>{_esc(title[:80] if title else '')}</td>",
            f"<td>{_esc(office)}</td>",
            f"<td>{_esc(legal_status)}</td>",
            f"<td>{html.escape(str(claim_count))}</td>",
            f"<td>{_esc(pub_date)}</td>",
        ]

        if show_counts:
            comp = comparison_map.get(canonical)
            if comp is not None:
                from ..analyze.compare import Verdict
                c = comp.counts()
                match_n = c.get(Verdict.MATCH.value, 0)
                missing_n = c.get(Verdict.MISSING.value, 0)
                unclear_n = c.get(Verdict.UNCLEAR.value, 0)
                cells.append(
                    f'<td>{_verdict_badge("MATCH")} {html.escape(str(match_n))}</td>'
                )
                cells.append(
                    f'<td>{_verdict_badge("MISSING")} {html.escape(str(missing_n))}</td>'
                )
                cells.append(
                    f'<td>{_verdict_badge("UNCLEAR")} {html.escape(str(unclear_n))}</td>'
                )
            else:
                cells += ["<td>—</td>", "<td>—</td>", "<td>—</td>"]

        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    thead = "<tr>" + "".join(th_cells) + "</tr>"
    tbody = "\n".join(rows_html) if rows_html else (
        '<tr><td colspan="9" style="text-align:center;color:#999;">'
        '取得できた案件がありません。</td></tr>'
    )

    body = (
        "<h1>Patent Survey Index</h1>\n"
        f'<p class="record-count">{html.escape(str(len(records)))} 件</p>\n'
        "<table>\n"
        f"<thead>{thead}</thead>\n"
        f"<tbody>\n{tbody}\n</tbody>\n"
        "</table>\n"
    )

    return _page_wrapper("Patent Survey Index", body)


# ---------------------------------------------------------------------------
# render_detail
# ---------------------------------------------------------------------------

def render_detail(
    record: "PatentRecord",
    summary: "PatentSummary",
    comparison: "ComparisonResult | None" = None,
    history: "list[tuple[str, RecordDiff]] | None" = None,
) -> str:
    """Render the DETAIL page for one patent.

    Sections:
      1. 書誌情報 (bibliographic info with source/source_url)
      2. 要約 (abstract + claim-element breakdown, labelled heuristic)
      3. 比較結果 (MATCH/MISSING/UNCLEAR table + escalation list, if comparison given)
      4. 変更履歴 (diff history, or '履歴なし')

    Parameters
    ----------
    record:
        PatentRecord (dataclass or dict).
    summary:
        PatentSummary for this record.
    comparison:
        Optional ComparisonResult (M4 semantic comparison).
    history:
        Optional list of (fetched_at, RecordDiff) pairs from build_site.py.
        None or empty list renders '履歴なし'.
    """
    # Support both dataclass instances and plain dicts for record.
    if isinstance(record, dict):
        canonical = record.get("canonical", "")
        office = record.get("office", "")
        number = record.get("number", "")
        title = record.get("title", "")
        abstract = record.get("abstract", "")
        assignee = record.get("assignee")
        pub_date = record.get("pub_date")
        legal_status = record.get("legal_status")
        family_id = record.get("family_id")
        source = record.get("source", "")
        source_url = record.get("source_url")
    else:
        canonical = record.canonical
        office = record.office
        number = record.number
        title = record.title
        abstract = record.abstract
        assignee = record.assignee
        pub_date = record.pub_date
        legal_status = record.legal_status
        family_id = record.family_id
        source = record.source
        source_url = record.source_url

    parts: list[str] = []
    parts.append(f"<h1>{_esc(canonical)} — {_esc(title)}</h1>\n")

    # -----------------------------------------------------------------------
    # Section 1: 書誌情報
    # -----------------------------------------------------------------------
    parts.append("<h2>1. 書誌情報</h2>\n<dl>\n")
    parts.append(f"  <dt>番号</dt><dd>{_esc(canonical)}</dd>\n")
    parts.append(f"  <dt>庁</dt><dd>{_esc(office)}</dd>\n")
    parts.append(f"  <dt>タイトル</dt><dd>{_esc(title)}</dd>\n")
    parts.append(f"  <dt>出願人</dt><dd>{_esc(assignee)}</dd>\n")
    parts.append(f"  <dt>公開日</dt><dd>{_esc(pub_date)}</dd>\n")
    parts.append(f"  <dt>法的状態</dt><dd>{_esc(legal_status)}</dd>\n")
    parts.append(f"  <dt>ファミリーID</dt><dd>{_esc(family_id)}</dd>\n")

    # Source with optional hyperlink — link only for http(s); display text escaped.
    safe_url = _safe_href(source_url)
    if safe_url:
        source_html = (
            f'<a href="{safe_url}" target="_blank" rel="noopener">'
            f'{_esc(source_url)}</a>'
            f" ({_esc(source)})"
        )
    elif source_url:
        # Non-http scheme: show as escaped plain text, never as a clickable link.
        source_html = f"{_esc(source_url)} ({_esc(source)})"
    else:
        source_html = _esc(source)
    parts.append(f"  <dt>出典</dt><dd>{source_html}</dd>\n")
    parts.append("</dl>\n")

    # -----------------------------------------------------------------------
    # Section 2: 要約
    # -----------------------------------------------------------------------
    parts.append("<h2>2. 要約</h2>\n")
    parts.append(f"<p><strong>一行書誌:</strong> {_esc(summary.one_line)}</p>\n")
    if abstract:
        parts.append(f"<p><strong>アブストラクト:</strong></p>\n<p>{_esc(abstract)}</p>\n")
    else:
        parts.append("<p><em>アブストラクトなし</em></p>\n")

    if summary.breakdown and summary.breakdown.elements:
        parts.append(
            f"<p><strong>独立請求項 (Claim {html.escape(str(summary.breakdown.claim_no))}) "
            f"の要素分解:</strong></p>\n"
        )
        parts.append(
            '<p class="heuristic-note">'
            "(ヒューリスティック分割・要検証 / heuristic, unverified)</p>\n"
        )
        parts.append("<ol>\n")
        for el in summary.breakdown.elements:
            parts.append(f"  <li>{_esc(el)}</li>\n")
        parts.append("</ol>\n")
    else:
        parts.append("<p><em>請求項テキストなし</em></p>\n")

    if summary.notes:
        parts.append("<ul>\n")
        for note in summary.notes:
            parts.append(f"  <li><em>{_esc(note)}</em></li>\n")
        parts.append("</ul>\n")

    # -----------------------------------------------------------------------
    # Section 3: 比較結果 (only when comparison is provided)
    # -----------------------------------------------------------------------
    if comparison is not None:
        from ..analyze.compare import Verdict

        parts.append("<h2>3. 比較結果（意味判定）</h2>\n")
        parts.append(
            f"<p><strong>対象仕様:</strong> {_esc(comparison.target_spec_title)}</p>\n"
        )
        _comp_href = _safe_href(comparison.source_url)
        parts.append(
            f"<p><em>出典: {_esc(comparison.source)}"
            + (f" (<a href=\"{_comp_href}\" target=\"_blank\" "
               f"rel=\"noopener\">{_esc(comparison.source_url)}</a>)"
               if _comp_href else
               (f" ({_esc(comparison.source_url)})" if comparison.source_url else ""))
            + "</em></p>\n"
        )

        # Count summary bar
        c = comparison.counts()
        match_n = c.get(Verdict.MATCH.value, 0)
        missing_n = c.get(Verdict.MISSING.value, 0)
        unclear_n = c.get(Verdict.UNCLEAR.value, 0)
        parts.append('<div class="count-bar">\n')
        parts.append(
            f"  {_verdict_badge('MATCH')} "
            f'<span>{html.escape(str(match_n))}</span>'
            f"  {_verdict_badge('MISSING')} "
            f'<span>{html.escape(str(missing_n))}</span>'
            f"  {_verdict_badge('UNCLEAR')} "
            f'<span>{html.escape(str(unclear_n))}</span>'
        )
        parts.append("\n</div>\n")

        # Verdict table
        parts.append("<table>\n")
        parts.append(
            "<thead><tr>"
            "<th>請求項要素</th>"
            "<th>判定</th>"
            "<th>根拠スパン</th>"
            "<th>信頼度</th>"
            "</tr></thead>\n"
            "<tbody>\n"
        )
        for v in comparison.verdicts:
            verdict_val = v.verdict.value
            verdict_css_class = f"verdict-{html.escape(verdict_val)}"
            elem_display = v.element[:100] if v.element else ""
            span_display = v.evidence_span[:150] if v.evidence_span else ""
            conf_display = f"{v.confidence:.2f}"
            parts.append(
                f'<tr>'
                f"<td>{_esc(elem_display)}</td>"
                f'<td class="{verdict_css_class}">{_verdict_badge(verdict_val)}</td>'
                f"<td>{_esc(span_display) if span_display else '<em>—</em>'}</td>"
                f"<td>{html.escape(conf_display)}</td>"
                f"</tr>\n"
            )
        parts.append("</tbody>\n</table>\n")

        # Escalation list (UNCLEAR items for human review)
        unclear_verdicts = comparison.unclear()
        if unclear_verdicts:
            parts.append('<div class="escalation-box">\n')
            parts.append(
                "<h3>要人手確認リスト (UNCLEAR)</h3>\n"
                "<p>以下の要素は自動判定が不十分です。専門家による確認が必要です。</p>\n"
                "<ul>\n"
            )
            for v in unclear_verdicts:
                span_text = v.evidence_span[:120] if v.evidence_span else "（なし）"
                rationale_text = v.rationale[:200] if v.rationale else ""
                parts.append(
                    f"  <li>\n"
                    f"    <strong>要素:</strong> {_esc(v.element[:120])}<br>\n"
                    f"    <strong>根拠スパン:</strong> {_esc(span_text)}<br>\n"
                    f"    <strong>信頼度:</strong> {html.escape(f'{v.confidence:.2f}')}<br>\n"
                    f"    <strong>理由:</strong> {_esc(rationale_text)}\n"
                    f"  </li>\n"
                )
            parts.append("</ul>\n</div>\n")

    # -----------------------------------------------------------------------
    # Section 4: 変更履歴
    # -----------------------------------------------------------------------
    parts.append("<h2>4. 変更履歴</h2>\n")

    if not history:
        parts.append("<p><em>履歴なし (初回取得のみ)</em></p>\n")
    else:
        # Filter to pairs where there is actually a diff.
        changed_pairs = [(ts, d) for (ts, d) in history if d.changed]
        if not changed_pairs:
            parts.append("<p><em>変更なし (全スナップショット同一)</em></p>\n")
        else:
            for fetched_at, diff in changed_pairs:
                parts.append(f'<div class="history-entry">\n')
                parts.append(
                    f"<h4>変更日時: {_esc(fetched_at)}</h4>\n"
                    "<table>\n"
                    "<thead><tr>"
                    "<th>フィールド</th>"
                    "<th>種別</th>"
                    "<th>変更前</th>"
                    "<th>変更後</th>"
                    "</tr></thead>\n"
                    "<tbody>\n"
                )
                has_claims_changes = False
                kind_map = {"changed": "変更", "added": "追加", "removed": "削除"}
                for change in diff.changes:
                    if change.field.startswith("claims"):
                        has_claims_changes = True
                    # Use the raw kind here; line below escapes exactly once.
                    kind_ja = kind_map.get(change.kind, change.kind)
                    before_str = _esc(str(change.before)[:120]) if change.before is not None else "<em>—</em>"
                    after_str = _esc(str(change.after)[:120]) if change.after is not None else "<em>—</em>"
                    parts.append(
                        f"<tr>"
                        f"<td>{_esc(change.field)}</td>"
                        f"<td>{html.escape(kind_ja)}</td>"
                        f"<td>{before_str}</td>"
                        f"<td>{after_str}</td>"
                        f"</tr>\n"
                    )
                parts.append("</tbody>\n</table>\n")
                if has_claims_changes:
                    parts.append(
                        "<p><em>注: 請求項差分はインデックス（位置）ベースで比較しています。"
                        "請求項が中間に挿入された場合は複数の変更として表示される場合があります"
                        "（P-NO-GUESS）。</em></p>\n"
                    )
                parts.append("</div>\n")

    body = "".join(parts)
    return _page_wrapper(f"{canonical} — {title}", body, back_link="../index.html")
