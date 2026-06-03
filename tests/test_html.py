"""Unit tests for src/patentkit/export/html.py.

Covers:
  - render_index contains §12 fields (番号/タイトル/庁/状態/請求項数/更新日)
  - render_detail contains all four sections
  - HTML injection test: title/abstract with <script> tag must appear escaped
  - DISCLAIMER present on both pages
  - UNCLEAR verdict rendered with amber background class and escalation list
  - Diff history rendering
  - No-history fallback
  - render_index/render_detail importable from patentkit.export

Run from repo root:
    py -m pytest tests/test_html.py -q
    py tests/test_html.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.analyze.compare import (    # noqa: E402
    ComparisonResult,
    ElementVerdict,
    Verdict,
)
from patentkit.analyze.summarize import ClaimBreakdown, PatentSummary  # noqa: E402
from patentkit.connectors.base import PatentRecord                      # noqa: E402
from patentkit.export import render_detail, render_index                # noqa: E402
from patentkit.export.markdown import DISCLAIMER                        # noqa: E402
from patentkit.state.diff import FieldChange, RecordDiff               # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: build sample objects in-memory (no I/O)
# ---------------------------------------------------------------------------

def _make_record(
    canonical: str = "US-10123456-B2",
    title: str = "Wireless Power Transfer Device",
    abstract: str = "A device for wireless power transfer using inductive coupling.",
    legal_status: str = "ACTIVE",
    claims: list[str] | None = None,
    assignee: str = "ACME Corp",
    pub_date: str = "2021-09-14",
    source_url: str = "https://patents.google.com/patent/US10123456B2",
) -> PatentRecord:
    if claims is None:
        claims = [
            "1. A wireless power apparatus comprising: a transmitter coil; "
            "a position sensor configured to detect a receiver position; "
            "and a controller configured to adjust a drive signal."
        ]
    return PatentRecord(
        canonical=canonical,
        office="US",
        number="10123456",
        title=title,
        abstract=abstract,
        claims=claims,
        assignee=assignee,
        pub_date=pub_date,
        legal_status=legal_status,
        family_id="FAM-001",
        source="bq-export",
        source_url=source_url,
    )


def _make_summary(record: PatentRecord) -> PatentSummary:
    return PatentSummary(
        canonical=record.canonical,
        title=record.title,
        one_line=f"{record.office} | {record.number} | {record.assignee} | {record.pub_date}",
        claim_count=len(record.claims),
        independent_claim=record.claims[0] if record.claims else "",
        breakdown=ClaimBreakdown(
            claim_no=1,
            text=record.claims[0] if record.claims else "",
            elements=[
                "a transmitter coil",
                "a position sensor configured to detect a receiver position",
                "a controller configured to adjust a drive signal",
            ],
            heuristic=True,
        ) if record.claims else None,
        source=record.source,
        source_url=record.source_url,
    )


def _make_comparison(record: PatentRecord) -> ComparisonResult:
    return ComparisonResult(
        patent_canonical=record.canonical,
        target_spec_title="Wireless Charger Target Specification",
        verdicts=[
            ElementVerdict(
                element="a transmitter coil",
                verdict=Verdict.MATCH,
                evidence_span="includes a transmitter coil for wireless power",
                confidence=0.85,
                rationale="tokens matched: [coil, transmitter]",
            ),
            ElementVerdict(
                element="a position sensor configured to detect a receiver position",
                verdict=Verdict.UNCLEAR,
                evidence_span="detects receiver location approximately",
                confidence=0.45,
                rationale="partial overlap; sensor token matched but position unclear",
                needs_review=True,
            ),
            ElementVerdict(
                element="a controller configured to adjust a drive signal",
                verdict=Verdict.MISSING,
                evidence_span="",
                confidence=1.0,
                rationale="no key terms found in spec",
                needs_review=False,
            ),
        ],
        source="bq-export",
        source_url="https://patents.google.com/patent/US10123456B2",
    )


def _make_history() -> list[tuple[str, RecordDiff]]:
    """Build a sample history with one change (legal_status)."""
    diff = RecordDiff(
        canonical="US-10123456-B2",
        changes=[
            FieldChange(
                field="legal_status",
                before="PENDING",
                after="ACTIVE",
                kind="changed",
            )
        ],
    )
    return [("2024-01-15T10-30-00-000000Z", diff)]


# ---------------------------------------------------------------------------
# Test 1: render_index contains §12 fields
# ---------------------------------------------------------------------------

def test_render_index_section12_fields():
    rec = _make_record()
    summary = _make_summary(rec)
    html = render_index([rec], [summary])

    assert "<!DOCTYPE html>" in html
    assert "<html" in html
    assert "US-10123456-B2" in html        # 番号
    assert "Wireless Power Transfer" in html  # タイトル
    assert "US" in html                    # 庁
    assert "ACTIVE" in html               # 状態
    assert "1" in html                     # 請求項数
    assert "2021-09-14" in html           # 更新日

    # Must have a link to the detail page
    assert 'href="patents/US_10123456_B2.html"' in html or \
           'href="patents/US-10123456-B2.html"' in html or \
           "patents/" in html


def test_render_index_with_comparisons():
    rec = _make_record()
    summary = _make_summary(rec)
    comp = _make_comparison(rec)
    html = render_index([rec], [summary], comparisons=[comp])

    # Verdict count columns should appear
    assert "MATCH" in html
    assert "MISSING" in html
    assert "UNCLEAR" in html
    # Badge spans
    assert "badge-MATCH" in html
    assert "badge-UNCLEAR" in html


# ---------------------------------------------------------------------------
# Test 2: render_detail contains all four sections
# ---------------------------------------------------------------------------

def test_render_detail_all_sections():
    rec = _make_record()
    summary = _make_summary(rec)
    comp = _make_comparison(rec)
    history = _make_history()
    html = render_detail(rec, summary, comparison=comp, history=history)

    assert "<!DOCTYPE html>" in html

    # Section 1: bibliographic info
    assert "書誌情報" in html
    assert "US-10123456-B2" in html
    assert "ACME Corp" in html
    assert "2021-09-14" in html
    assert "patents.google.com" in html   # source_url appears somewhere

    # Section 2: summary
    assert "要約" in html
    assert "wireless power transfer" in html.lower()
    assert "ヒューリスティック" in html    # heuristic label
    assert "heuristic" in html.lower()

    # Section 3: comparison table
    assert "比較結果" in html
    assert "Wireless Charger Target Specification" in html
    assert "verdict-MATCH" in html
    assert "verdict-UNCLEAR" in html
    assert "verdict-MISSING" in html

    # Section 4: history
    assert "変更履歴" in html
    assert "legal_status" in html
    assert "PENDING" in html
    assert "ACTIVE" in html


# ---------------------------------------------------------------------------
# Test 3: HTML injection — <script> must be escaped, NOT rendered as raw tag
# ---------------------------------------------------------------------------

def test_html_injection_escaping():
    """Critical security test: untrusted patent data must be escaped."""
    malicious_title = "<script>alert(1)</script>"
    malicious_abstract = '<img src=x onerror=alert(1)>'
    malicious_claim = "1. A device; <b>bold claim</b>; wherein onerror=alert(2)"

    rec = _make_record(
        title=malicious_title,
        abstract=malicious_abstract,
        claims=[malicious_claim],
        canonical="US-99999999-A1",
    )
    summary = _make_summary(rec)

    # Test render_index
    index_html = render_index([rec], [summary])
    assert "<script>" not in index_html, (
        "Raw <script> tag found in render_index output — injection not escaped!"
    )
    assert "<b>" not in index_html, "Raw <b> tag found in render_index"
    assert "&lt;script&gt;" in index_html, (
        "Expected &lt;script&gt; escaped form not found in render_index"
    )

    # Test render_detail
    detail_html = render_detail(rec, summary)
    assert "<script>" not in detail_html, (
        "Raw <script> tag found in render_detail output — injection not escaped!"
    )
    assert "&lt;script&gt;" in detail_html, (
        "Expected &lt;script&gt; escaped form not found in render_detail"
    )
    # <img src=x onerror=alert(1)> must be escaped: the raw < and > must not appear
    # as actual tags. html.escape turns < into &lt; and > into &gt;.
    assert "<img" not in detail_html, (
        "Raw <img> tag from abstract found unescaped in render_detail"
    )


# ---------------------------------------------------------------------------
# Test 4: DISCLAIMER present on both pages
# ---------------------------------------------------------------------------

def test_disclaimer_present_on_both_pages():
    rec = _make_record()
    summary = _make_summary(rec)

    index_html = render_index([rec], [summary])
    detail_html = render_detail(rec, summary)

    # DISCLAIMER constant (stripped of Markdown markers) must appear on both.
    # We check for the key phrase from the DISCLAIMER constant.
    disclaimer_key = "AIによる支援結果"
    assert disclaimer_key in index_html, (
        f"DISCLAIMER key phrase '{disclaimer_key}' not found in index page"
    )
    assert disclaimer_key in detail_html, (
        f"DISCLAIMER key phrase '{disclaimer_key}' not found in detail page"
    )

    # Also verify the disclaimer CSS class is used
    assert "disclaimer" in index_html
    assert "disclaimer" in detail_html


# ---------------------------------------------------------------------------
# Test 5: UNCLEAR verdict rendered with visual distinction and escalation list
# ---------------------------------------------------------------------------

def test_unclear_verdict_rendered_and_flagged():
    rec = _make_record()
    summary = _make_summary(rec)
    comp = _make_comparison(rec)
    html = render_detail(rec, summary, comparison=comp)

    # UNCLEAR rows must have the amber CSS class
    assert "verdict-UNCLEAR" in html

    # Badge for UNCLEAR must appear
    assert "badge-UNCLEAR" in html

    # Escalation box must be present
    assert "escalation-box" in html
    assert "要人手確認" in html

    # The specific UNCLEAR element must appear in the escalation list
    assert "position sensor" in html


# ---------------------------------------------------------------------------
# Test 6: Diff history rendering
# ---------------------------------------------------------------------------

def test_history_rendering():
    rec = _make_record()
    summary = _make_summary(rec)
    history = _make_history()
    html = render_detail(rec, summary, history=history)

    assert "変更履歴" in html
    assert "2024-01-15" in html          # timestamp
    assert "legal_status" in html        # field name
    assert "PENDING" in html             # before value
    assert "ACTIVE" in html              # after value
    assert "変更" in html                # kind_ja for "changed"
    # "履歴なし" must NOT appear when there is history
    assert "履歴なし" not in html


# ---------------------------------------------------------------------------
# Test 7: No-history fallback
# ---------------------------------------------------------------------------

def test_no_history_fallback():
    rec = _make_record()
    summary = _make_summary(rec)

    # Pass None explicitly
    html_none = render_detail(rec, summary, history=None)
    assert "履歴なし" in html_none

    # Pass empty list
    html_empty = render_detail(rec, summary, history=[])
    assert "履歴なし" in html_empty


# ---------------------------------------------------------------------------
# Test 8: render_index links point to per-patent detail files
# ---------------------------------------------------------------------------

def test_index_links_to_detail_files():
    rec1 = _make_record(canonical="US-10111111-B2", title="Patent One")
    rec2 = _make_record(canonical="EP-2345678-A1", title="Patent Two")
    summaries = [_make_summary(rec1), _make_summary(rec2)]
    html = render_index([rec1, rec2], summaries)

    # Links must use the safe filename derived from canonical.
    # _safe_filename preserves hyphens (valid in filenames/URLs); only
    # characters truly unsafe for Windows paths (/ \ : * ? < > " |) are replaced.
    from patentkit.export.html import _safe_filename
    assert f"patents/{_safe_filename('US-10111111-B2')}.html" in html
    assert f"patents/{_safe_filename('EP-2345678-A1')}.html" in html


# ---------------------------------------------------------------------------
# __main__ runner (allow running without pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"ok   {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
