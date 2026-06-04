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
/* ------------------------------------------------------------------ reset */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* ----------------------------------------------------------- design tokens */
:root {
  --ink:        #1a1e24;
  --ink-mid:    #4a5260;
  --ink-muted:  #7a8494;
  --paper:      #f7f6f3;
  --surface:    #ffffff;
  --border:     #e2e0db;
  --border-md:  #cdc9c2;
  --accent:     #2b5be0;
  --accent-dk:  #1e42a8;

  --match-bg:   #e6f5ec;
  --match-fg:   #0d5c2e;
  --match-border: #6fcf97;

  --missing-bg: #fef0ef;
  --missing-fg: #9b2020;
  --missing-border: #f4877e;

  --unclear-bg: #fef8ea;
  --unclear-fg: #7a5500;
  --unclear-border: #f6cc5b;

  --radius-sm:  4px;
  --radius:     8px;
  --radius-lg:  12px;

  --shadow-sm:  0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
  --shadow:     0 4px 12px rgba(0,0,0,.08), 0 1px 3px rgba(0,0,0,.04);
  --shadow-lg:  0 8px 24px rgba(0,0,0,.10), 0 2px 6px rgba(0,0,0,.05);
}

/* ------------------------------------------------------------------ base */
html { font-size: 15px; }
body {
  font-family: "Hiragino Kaku Gothic ProN", "Hiragino Sans", "Meiryo",
               "Yu Gothic UI", "MS PGothic", -apple-system, BlinkMacSystemFont,
               "Segoe UI", sans-serif;
  line-height: 1.65;
  color: var(--ink);
  background: var(--paper);
  -webkit-font-smoothing: antialiased;
}

/* -------------------------------------------------------------- site header */
.site-header {
  background: var(--ink);
  color: #fff;
  padding: 0 20px;
  height: 52px;
  display: flex;
  align-items: center;
  gap: 12px;
  position: sticky;
  top: 0;
  z-index: 100;
  box-shadow: 0 2px 8px rgba(0,0,0,.25);
}
.site-header__logo {
  font-size: .72em;
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--accent);
  background: rgba(43,91,224,.18);
  padding: 3px 9px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(43,91,224,.35);
  white-space: nowrap;
}
.site-header__title {
  font-size: .92em;
  font-weight: 500;
  color: rgba(255,255,255,.7);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.site-header__sep { flex: 1; }
.site-header__back {
  font-size: .8em;
  color: rgba(255,255,255,.55);
  text-decoration: none;
  padding: 5px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(255,255,255,.18);
  transition: background .15s, color .15s;
  white-space: nowrap;
}
.site-header__back:hover {
  background: rgba(255,255,255,.1);
  color: #fff;
}

/* -------------------------------------------------------------- layout */
.container {
  max-width: 1060px;
  margin: 0 auto;
  padding: 32px 20px 72px;
}

/* -------------------------------------------------------------- typography */
h1 { font-size: 1.55em; font-weight: 700; color: var(--ink); line-height: 1.3; }
h2 {
  font-size: 1.0em;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--ink-mid);
  margin-top: 40px;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
h3 { font-size: .95em; font-weight: 600; color: var(--ink); margin-top: 20px; margin-bottom: 8px; }
h4 { font-size: .88em; font-weight: 600; color: var(--ink-mid); margin-bottom: 6px; }
p  { margin: 8px 0; }

a  { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
em { color: var(--ink-muted); }

/* -------------------------------------------------------------- cards */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 20px 24px;
  margin: 12px 0;
}

/* -------------------------------------------------------------- page-level hero (detail) */
.hero {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: 28px 32px;
  margin-bottom: 8px;
}
.hero__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
.hero__number {
  font-size: .82em;
  font-weight: 700;
  letter-spacing: .08em;
  color: var(--accent-dk);
  background: rgba(43,91,224,.07);
  padding: 3px 10px;
  border-radius: 100px;
  border: 1px solid rgba(43,91,224,.2);
}
.hero__title {
  font-size: 1.45em;
  font-weight: 700;
  color: var(--ink);
  line-height: 1.35;
  margin: 0 0 4px;
}
.hero__subtitle {
  font-size: .85em;
  color: var(--ink-muted);
}

/* -------------------------------------------------------------- badges & pills */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 9px;
  border-radius: 100px;
  font-size: .78em;
  font-weight: 700;
  letter-spacing: .04em;
  border: 1px solid transparent;
  white-space: nowrap;
}
.badge-MATCH   { background: var(--match-bg);   color: var(--match-fg);   border-color: var(--match-border); }
.badge-MISSING { background: var(--missing-bg); color: var(--missing-fg); border-color: var(--missing-border); }
.badge-UNCLEAR { background: var(--unclear-bg); color: var(--unclear-fg); border-color: var(--unclear-border); }

.office-badge {
  display: inline-block;
  white-space: nowrap;
  font-size: .73em;
  font-weight: 800;
  letter-spacing: .1em;
  color: var(--ink-mid);
  background: var(--paper);
  border: 1px solid var(--border-md);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}
.status-badge {
  display: inline-block;
  font-size: .73em;
  font-weight: 600;
  color: var(--ink-mid);
  background: var(--paper);
  border: 1px solid var(--border);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

/* -------------------------------------------------------------- verdict summary cards (detail) */
.verdict-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin: 16px 0 24px;
}
.verdict-card {
  border-radius: var(--radius);
  padding: 18px 20px;
  text-align: center;
  border: 1px solid;
}
.verdict-card--MATCH   { background: var(--match-bg);   border-color: var(--match-border); }
.verdict-card--MISSING { background: var(--missing-bg); border-color: var(--missing-border); }
.verdict-card--UNCLEAR { background: var(--unclear-bg); border-color: var(--unclear-border); }
.verdict-card__count {
  font-size: 2.4em;
  font-weight: 800;
  line-height: 1;
  margin-bottom: 4px;
}
.verdict-card--MATCH   .verdict-card__count { color: var(--match-fg); }
.verdict-card--MISSING .verdict-card__count { color: var(--missing-fg); }
.verdict-card--UNCLEAR .verdict-card__count { color: var(--unclear-fg); }
.verdict-card__label {
  font-size: .72em;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.verdict-card--MATCH   .verdict-card__label { color: var(--match-fg); }
.verdict-card--MISSING .verdict-card__label { color: var(--missing-fg); }
.verdict-card--UNCLEAR .verdict-card__label { color: var(--unclear-fg); }

/* -------------------------------------------------------------- index verdict mini-bar */
.mini-verdict {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}
.mini-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 7px;
  border-radius: 100px;
  font-size: .76em;
  font-weight: 700;
  border: 1px solid transparent;
  white-space: nowrap;
}
.mini-pill--MATCH   { background: var(--match-bg);   color: var(--match-fg);   border-color: var(--match-border); }
.mini-pill--MISSING { background: var(--missing-bg); color: var(--missing-fg); border-color: var(--missing-border); }
.mini-pill--UNCLEAR { background: var(--unclear-bg); color: var(--unclear-fg); border-color: var(--unclear-border); }

/* -------------------------------------------------------------- tables */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: .875em;
  margin: 0;
  background: var(--surface);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border);
}
.data-table thead tr {
  background: #f0ede8;
}
.data-table th {
  padding: 10px 14px;
  text-align: left;
  font-size: .78em;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--ink-mid);
  border-bottom: 1px solid var(--border-md);
  white-space: nowrap;
}
.data-table td {
  padding: 10px 14px;
  vertical-align: top;
  word-break: break-word;
  border-bottom: 1px solid var(--border);
  color: var(--ink);
}
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover td { background: rgba(43,91,224,.03); }

/* verdict cell backgrounds */
td.verdict-MATCH   { background: rgba(111,207,151,.12); }
td.verdict-MISSING { background: rgba(244,135,126,.10); }
td.verdict-UNCLEAR { background: rgba(246,204,91,.12);  }

/* index table: hoverable rows with subtle left accent on hover */
.index-table tbody tr { cursor: default; }
.index-table tbody tr:hover td { background: rgba(43,91,224,.04); }
.index-table tbody tr:hover td:first-child {
  box-shadow: inset 3px 0 0 var(--accent);
}

/* -------------------------------------------------------------- evidence quote */
.evidence-span {
  display: block;
  font-size: .82em;
  color: var(--ink-mid);
  border-left: 3px solid var(--border-md);
  padding: 3px 10px;
  margin-top: 4px;
  font-style: italic;
  background: var(--paper);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.evidence-span--MATCH   { border-left-color: var(--match-border); }
.evidence-span--MISSING { border-left-color: var(--missing-border); }
.evidence-span--UNCLEAR { border-left-color: var(--unclear-border); }

/* -------------------------------------------------------------- confidence bar */
.conf-bar {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.conf-bar__track {
  display: inline-block;
  width: 48px;
  height: 5px;
  background: var(--border);
  border-radius: 100px;
  overflow: hidden;
  vertical-align: middle;
}
.conf-bar__fill {
  display: block;
  height: 100%;
  border-radius: 100px;
  background: var(--accent);
  transition: width .3s;
}
.conf-bar__fill--MATCH   { background: var(--match-fg);   }
.conf-bar__fill--MISSING { background: var(--missing-fg); }
.conf-bar__fill--UNCLEAR { background: var(--unclear-fg); }
.conf-bar__num {
  font-size: .78em;
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}

/* -------------------------------------------------------------- escalation box */
.escalation-box {
  background: var(--unclear-bg);
  border: 1px solid var(--unclear-border);
  border-left: 4px solid #e6a817;
  border-radius: var(--radius);
  padding: 18px 22px;
  margin: 20px 0;
}
.escalation-box h3 {
  color: var(--unclear-fg);
  font-size: .88em;
  letter-spacing: .05em;
  text-transform: uppercase;
  margin-top: 0;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.escalation-box h3::before {
  content: "!";
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #e6a817;
  color: #fff;
  font-size: .75em;
  font-weight: 900;
  flex-shrink: 0;
}
.escalation-box p {
  font-size: .85em;
  color: var(--unclear-fg);
  margin-bottom: 12px;
}
.escalation-item {
  background: rgba(255,255,255,.55);
  border: 1px solid var(--unclear-border);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  margin: 8px 0;
  font-size: .85em;
}
.escalation-item__label {
  font-size: .74em;
  font-weight: 700;
  letter-spacing: .05em;
  text-transform: uppercase;
  color: var(--unclear-fg);
  margin-bottom: 2px;
}
.escalation-item__value {
  color: var(--ink);
  margin-bottom: 6px;
}

/* -------------------------------------------------------------- bib dl */
.bib-grid {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 16px;
  font-size: .9em;
}
.bib-grid dt {
  font-size: .78em;
  font-weight: 700;
  letter-spacing: .05em;
  text-transform: uppercase;
  color: var(--ink-muted);
  padding: 5px 0;
  white-space: nowrap;
  align-self: start;
}
.bib-grid dd {
  padding: 5px 0;
  color: var(--ink);
  border-bottom: 1px solid var(--border);
  align-self: start;
  word-break: break-word;
}
.bib-grid dd:last-child { border-bottom: none; }

/* -------------------------------------------------------------- summary section */
.summary-block {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  box-shadow: var(--shadow-sm);
}
.summary-one-line {
  font-size: .88em;
  color: var(--ink-mid);
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.abstract-text {
  font-size: .9em;
  color: var(--ink);
  line-height: 1.7;
}
.heuristic-note {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: .75em;
  color: var(--ink-muted);
  font-style: italic;
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: 100px;
  padding: 2px 10px;
  margin: 10px 0 6px;
}
.claims-list {
  margin: 8px 0 0 0;
  padding-left: 0;
  list-style: none;
  counter-reset: claim-counter;
}
.claims-list li {
  counter-increment: claim-counter;
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  font-size: .88em;
}
.claims-list li:nth-child(odd) { background: var(--paper); }
.claims-list li::before {
  content: counter(claim-counter);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--ink-mid);
  color: #fff;
  font-size: .7em;
  font-weight: 700;
  flex-shrink: 0;
  margin-top: 1px;
}

/* -------------------------------------------------------------- history */
.history-entry {
  border-left: 3px solid var(--border-md);
  padding-left: 16px;
  margin: 16px 0;
}
.history-entry:hover { border-left-color: var(--accent); }
.history-entry h4 {
  font-size: .83em;
  color: var(--ink-muted);
  font-weight: 500;
  margin-bottom: 8px;
}
.history-entry h4 strong {
  color: var(--ink);
  font-weight: 700;
}

/* -------------------------------------------------------------- record count */
.record-count {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: .8em;
  color: var(--ink-muted);
  margin: 8px 0 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 100px;
  padding: 3px 12px;
}


/* -------------------------------------------------------------- empty state */
.empty-state {
  text-align: center;
  padding: 48px 24px;
  color: var(--ink-muted);
  font-size: .92em;
}

/* -------------------------------------------------------------- page title row */
.page-title-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: baseline;
  margin-bottom: 4px;
}

/* -------------------------------------------------------------- section label */
.section-num {
  display: inline-block;
  font-size: .7em;
  font-weight: 700;
  letter-spacing: .08em;
  color: var(--accent);
  background: rgba(43,91,224,.08);
  border: 1px solid rgba(43,91,224,.2);
  border-radius: var(--radius-sm);
  padding: 1px 7px;
  margin-right: 4px;
  vertical-align: middle;
}

/* -------------------------------------------------------------- disclaimer */
.disclaimer {
  margin-top: 56px;
  padding: 14px 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-md);
  border-radius: var(--radius);
  font-size: .8em;
  color: var(--ink-muted);
  line-height: 1.6;
  box-shadow: var(--shadow-sm);
}
.disclaimer strong { color: var(--ink-mid); }

/* -------------------------------------------------------------- responsive */
@media (max-width: 680px) {
  .container { padding: 20px 14px 48px; }
  .hero { padding: 20px 18px; }
  .verdict-summary { grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .verdict-card { padding: 12px 10px; }
  .verdict-card__count { font-size: 1.8em; }
  .bib-grid { grid-template-columns: 1fr; }
  .bib-grid dt { border-bottom: none; padding-bottom: 0; }
  .bib-grid dd { border-bottom: 1px solid var(--border); padding-top: 2px; }
  h2 { margin-top: 28px; }
  .data-table { font-size: .8em; }
  .data-table th, .data-table td { padding: 7px 9px; }
}

/* -------------------------------------------------------------- print */
@media print {
  .site-header { display: none; }
  .search-wrap { display: none; }
  body { background: #fff; }
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


def _conf_bar(confidence: float, verdict_val: str) -> str:
    """Return a small progress-bar + numeric display for a confidence value."""
    pct = int(round(min(max(confidence, 0.0), 1.0) * 100))
    v = html.escape(verdict_val)
    return (
        f'<span class="conf-bar">'
        f'<span class="conf-bar__track">'
        f'<span class="conf-bar__fill conf-bar__fill--{v}" style="width:{pct}%"></span>'
        f'</span>'
        f'<span class="conf-bar__num">{pct}%</span>'
        f'</span>'
    )


def _page_wrapper(title: str, body: str, back_link: str | None = None) -> str:
    """Wrap body HTML in a complete HTML5 document with inline CSS and DISCLAIMER.

    Parameters
    ----------
    title:
        Page <title> (will be escaped).
    body:
        Inner HTML content (assumed already-escaped where needed).
    back_link:
        Optional href for a back link rendered in the sticky header.
    """
    back_html = ""
    if back_link:
        back_html = (
            f'<a class="site-header__back" href="{_esc_raw(back_link)}">'
            f'&larr; 一覧へ戻る</a>'
        )

    # DISCLAIMER text is a trusted constant imported from markdown.py, not user data.
    # We strip the Markdown bold markers (** ... **) for plain HTML display.
    # Strip the Markdown blockquote + the "注記:" prefix (the footer adds its own
    # <strong>注記:</strong> label) and the bold markers, to avoid a doubled label.
    disclaimer_text = html.escape(
        DISCLAIMER
        .replace("> **注記**: ", "")
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
        '<header class="site-header">\n'
        '  <span class="site-header__logo">PatentKit</span>\n'
        f'  <span class="site-header__title">{_esc(title)}</span>\n'
        '  <span class="site-header__sep"></span>\n'
        + (back_html + "\n" if back_html else "")
        + '</header>\n'
        '<div class="container">\n'
        + body
        + f'\n<div class="disclaimer"><strong>注記:</strong> {disclaimer_text}</div>\n'
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
        th_cells.append("<th>判定サマリ</th>")

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

        office_badge = (
            f'<span class="office-badge">{_esc(office)}</span>'
            if office else "—"
        )
        status_badge = (
            f'<span class="status-badge">{_esc(legal_status)}</span>'
            if legal_status else '<span style="color:var(--ink-muted)">—</span>'
        )

        cells = [
            f'<td><a href="{html.escape(link_href)}" style="font-weight:600;letter-spacing:.02em;">'
            f'{_esc(canonical)}</a></td>',
            f"<td>{_esc(title[:80] if title else '')}</td>",
            f"<td>{office_badge}</td>",
            f"<td>{status_badge}</td>",
            f'<td style="text-align:center;font-variant-numeric:tabular-nums;">{html.escape(str(claim_count))}</td>',
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
                mini_bar = (
                    f'<div class="mini-verdict">'
                    f'<span class="mini-pill mini-pill--MATCH badge-MATCH">'
                    f'<span class="badge badge-MATCH">MATCH</span> {html.escape(str(match_n))}</span>'
                    f'<span class="mini-pill mini-pill--MISSING badge-MISSING">'
                    f'<span class="badge badge-MISSING">MISSING</span> {html.escape(str(missing_n))}</span>'
                    f'<span class="mini-pill mini-pill--UNCLEAR badge-UNCLEAR">'
                    f'<span class="badge badge-UNCLEAR">UNCLEAR</span> {html.escape(str(unclear_n))}</span>'
                    f'</div>'
                )
                cells.append(f"<td>{mini_bar}</td>")
            else:
                cells.append('<td style="color:var(--ink-muted);text-align:center;">—</td>')

        rows_html.append(
            "<tr>"
            + "".join(cells)
            + "</tr>"
        )

    thead = "<tr>" + "".join(th_cells) + "</tr>"
    tbody = "\n".join(rows_html) if rows_html else (
        '<tr><td colspan="9"><div class="empty-state">取得できた案件がありません。</div></td></tr>'
    )

    body = (
        '<div class="page-title-row">\n'
        '<h1>特許調査インデックス</h1>\n'
        '</div>\n'
        f'<p class="record-count">{html.escape(str(len(records)))} 件取得済み</p>\n'
        + f'<table class="data-table index-table">\n'
        f"<thead>{thead}</thead>\n"
        f"<tbody>\n{tbody}\n</tbody>\n"
        "</table>\n"
    )

    return _page_wrapper("特許調査インデックス — PatentKit", body)


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

    # -----------------------------------------------------------------------
    # Hero header
    # -----------------------------------------------------------------------
    office_badge_html = (
        f' <span class="office-badge">{_esc(office)}</span>'
        if office else ""
    )
    status_badge_html = (
        f' <span class="status-badge">{_esc(legal_status)}</span>'
        if legal_status else ""
    )

    parts.append(
        '<div class="hero">\n'
        '  <div class="hero__meta">\n'
        f'    <span class="hero__number">{_esc(canonical)}</span>\n'
        + (f'    {office_badge_html}\n' if office_badge_html else '')
        + (f'    {status_badge_html}\n' if status_badge_html else '')
        + '  </div>\n'
        f'  <h1 class="hero__title">{_esc(title) if title else _esc(canonical)}</h1>\n'
    )
    if assignee:
        parts.append(f'  <p class="hero__subtitle">{_esc(assignee)}</p>\n')
    parts.append('</div>\n')

    # -----------------------------------------------------------------------
    # Section 1: 書誌情報
    # -----------------------------------------------------------------------
    parts.append(
        f'<h2><span class="section-num">01</span>書誌情報</h2>\n'
        '<div class="card">\n'
        '<dl class="bib-grid">\n'
    )
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
    parts.append("</dl>\n</div>\n")

    # -----------------------------------------------------------------------
    # Section 2: 要約
    # -----------------------------------------------------------------------
    parts.append(f'<h2><span class="section-num">02</span>要約</h2>\n')
    parts.append('<div class="summary-block">\n')
    parts.append(f'<p class="summary-one-line">{_esc(summary.one_line)}</p>\n')

    if abstract:
        parts.append(
            f'<p class="abstract-text">'
            f'<strong style="font-size:.78em;letter-spacing:.05em;text-transform:uppercase;'
            f'color:var(--ink-muted);">アブストラクト</strong><br>'
            f'{_esc(abstract)}'
            f'</p>\n'
        )
    else:
        parts.append('<p><em>アブストラクトなし</em></p>\n')

    if summary.breakdown and summary.breakdown.elements:
        parts.append(
            f'<p style="margin-top:16px;"><strong style="font-size:.82em;letter-spacing:.04em;'
            f'text-transform:uppercase;color:var(--ink-mid);">'
            f'独立請求項（Claim {html.escape(str(summary.breakdown.claim_no))}）の要素分解'
            f'</strong></p>\n'
        )
        parts.append(
            '<span class="heuristic-note">'
            '&#x26A0;&#xFE0F; ヒューリスティック分割・要検証 (heuristic, unverified)'
            '</span>\n'
        )
        parts.append('<ol class="claims-list">\n')
        for el in summary.breakdown.elements:
            parts.append(f"  <li>{_esc(el)}</li>\n")
        parts.append("</ol>\n")
    else:
        parts.append('<p><em>請求項テキストなし</em></p>\n')

    if summary.notes:
        parts.append(
            '<ul style="margin-top:12px;padding-left:20px;font-size:.85em;color:var(--ink-muted);">\n'
        )
        for note in summary.notes:
            parts.append(f"  <li>{_esc(note)}</li>\n")
        parts.append("</ul>\n")

    parts.append("</div>\n")

    # -----------------------------------------------------------------------
    # Section 3: 比較結果 (only when comparison is provided)
    # -----------------------------------------------------------------------
    if comparison is not None:
        from ..analyze.compare import Verdict

        parts.append(f'<h2><span class="section-num">03</span>比較結果（意味判定）</h2>\n')
        parts.append(
            f'<p style="font-size:.88em;color:var(--ink-mid);margin-bottom:6px;">'
            f'<strong>対象仕様:</strong> {_esc(comparison.target_spec_title)}</p>\n'
        )
        _comp_href = _safe_href(comparison.source_url)
        parts.append(
            f'<p style="font-size:.82em;color:var(--ink-muted);margin-bottom:16px;">'
            f'出典: {_esc(comparison.source)}'
            + (f' (<a href="{_comp_href}" target="_blank" rel="noopener">'
               f'{_esc(comparison.source_url)}</a>)'
               if _comp_href else
               (f' ({_esc(comparison.source_url)})' if comparison.source_url else ''))
            + '</p>\n'
        )

        # Prominent verdict summary cards
        c = comparison.counts()
        match_n = c.get(Verdict.MATCH.value, 0)
        missing_n = c.get(Verdict.MISSING.value, 0)
        unclear_n = c.get(Verdict.UNCLEAR.value, 0)

        parts.append('<div class="verdict-summary">\n')
        for vname, vcount in (("MATCH", match_n), ("MISSING", missing_n), ("UNCLEAR", unclear_n)):
            parts.append(
                f'<div class="verdict-card verdict-card--{html.escape(vname)}">\n'
                f'  <div class="verdict-card__count">{html.escape(str(vcount))}</div>\n'
                f'  <div class="verdict-card__label">{html.escape(vname)}</div>\n'
                f'</div>\n'
            )
        parts.append('</div>\n')

        # Verdict table
        parts.append(f'<table class="data-table">\n')
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
            conf_html = _conf_bar(v.confidence, verdict_val)

            if span_display:
                span_html = (
                    f'<span class="evidence-span evidence-span--{html.escape(verdict_val)}">'
                    f'{_esc(span_display)}'
                    f'</span>'
                )
            else:
                span_html = '<em style="color:var(--ink-muted);">—</em>'

            parts.append(
                f'<tr>'
                f'<td style="max-width:280px;">{_esc(elem_display)}</td>'
                f'<td class="{verdict_css_class}">{_verdict_badge(verdict_val)}</td>'
                f'<td>{span_html}</td>'
                f'<td>{conf_html}</td>'
                f'</tr>\n'
            )
        parts.append("</tbody>\n</table>\n")

        # Escalation list (UNCLEAR items for human review)
        unclear_verdicts = comparison.unclear()
        if unclear_verdicts:
            parts.append('<div class="escalation-box">\n')
            parts.append(
                "<h3>要人手確認リスト（UNCLEAR）</h3>\n"
                "<p>以下の要素は自動判定が不十分です。専門家による確認が必要です。</p>\n"
            )
            for v in unclear_verdicts:
                span_text = v.evidence_span[:120] if v.evidence_span else "（なし）"
                rationale_text = v.rationale[:200] if v.rationale else ""
                parts.append(
                    f'<div class="escalation-item">\n'
                    f'  <div class="escalation-item__label">要素</div>\n'
                    f'  <div class="escalation-item__value">{_esc(v.element[:120])}</div>\n'
                    f'  <div class="escalation-item__label">根拠スパン</div>\n'
                    f'  <div class="escalation-item__value">'
                    f'<em style="color:var(--ink-mid);">{_esc(span_text)}</em>'
                    f'  </div>\n'
                    f'  <div class="escalation-item__label">信頼度 / 理由</div>\n'
                    f'  <div class="escalation-item__value">'
                    f'{html.escape(f"{v.confidence:.2f}")} — {_esc(rationale_text)}'
                    f'  </div>\n'
                    f'</div>\n'
                )
            parts.append('</div>\n')

    # -----------------------------------------------------------------------
    # Section 4: 変更履歴
    # -----------------------------------------------------------------------
    parts.append(f'<h2><span class="section-num">04</span>変更履歴</h2>\n')

    if not history:
        parts.append(
            '<div class="card" style="color:var(--ink-muted);font-size:.9em;">'
            '<em>履歴なし (初回取得のみ)</em>'
            '</div>\n'
        )
    else:
        # Filter to pairs where there is actually a diff.
        changed_pairs = [(ts, d) for (ts, d) in history if d.changed]
        if not changed_pairs:
            parts.append(
                '<div class="card" style="color:var(--ink-muted);font-size:.9em;">'
                '<em>変更なし (全スナップショット同一)</em>'
                '</div>\n'
            )
        else:
            for fetched_at, diff in changed_pairs:
                parts.append(f'<div class="history-entry">\n')
                parts.append(
                    f"<h4>変更日時: <strong>{_esc(fetched_at)}</strong></h4>\n"
                )
                parts.append(
                    f'<table class="data-table" style="margin-bottom:6px;">\n'
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
                    kind_ja = kind_map.get(change.kind, change.kind)
                    before_str = _esc(str(change.before)[:120]) if change.before is not None else "<em>—</em>"
                    after_str = _esc(str(change.after)[:120]) if change.after is not None else "<em>—</em>"
                    parts.append(
                        f"<tr>"
                        f"<td><code style='font-size:.82em;background:var(--paper);padding:1px 5px;border-radius:3px;'>{_esc(change.field)}</code></td>"
                        f"<td>{html.escape(kind_ja)}</td>"
                        f"<td style='color:var(--missing-fg);'>{before_str}</td>"
                        f"<td style='color:var(--match-fg);'>{after_str}</td>"
                        f"</tr>\n"
                    )
                parts.append("</tbody>\n</table>\n")
                if has_claims_changes:
                    parts.append(
                        '<p style="font-size:.8em;color:var(--ink-muted);margin-top:4px;">'
                        "<em>注: 請求項差分はインデックス（位置）ベースで比較しています。"
                        "請求項が中間に挿入された場合は複数の変更として表示される場合があります"
                        "（P-NO-GUESS）。</em></p>\n"
                    )
                parts.append("</div>\n")

    body = "".join(parts)
    return _page_wrapper(f"{canonical} — {title}", body, back_link="../index.html")
