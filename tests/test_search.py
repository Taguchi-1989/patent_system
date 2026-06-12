"""Discovery layer (M8): SQL builder, ranking, family dedupe, CSV/report."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from patentkit.search import (
    SearchQuery,
    build_fetch_sql,
    build_search_sql,
    dedupe_by_family,
    load_query_spec,
    rank_rows,
)
from patentkit.search.rank import score_row
from patentkit.search.report import candidates_csv, render_search_report

ROOT = os.path.join(os.path.dirname(__file__), "..")
BRIEF = os.path.join(ROOT, "samples", "search_query_SAMPLE.json")
EXPORT = os.path.join(ROOT, "samples", "search_export_SAMPLE.json")


def _q(**kw) -> SearchQuery:
    base = dict(keywords=[["wireless power", "wireless charger"],
                          ["foreign object detection", "FOD"]])
    base.update(kw)
    return SearchQuery(**base)


def _rows() -> list[dict]:
    with open(EXPORT, encoding="utf-8") as f:
        return json.load(f)


# ---- query spec ----------------------------------------------------------

def test_load_sample_brief():
    q = load_query_spec(BRIEF)
    assert q.name == "wireless-power-fod"
    assert len(q.keywords) == 2
    assert q.cpc == ["H02J50"]


def test_empty_brief_rejected():
    with pytest.raises(ValueError, match="at least one"):
        SearchQuery().validate()


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "brief.json"
    p.write_text('{"keywords": [["x"]], "klassifikation": ["A"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keys"):
        load_query_spec(str(p))


def test_blank_keyword_rejected():
    with pytest.raises(ValueError, match="blank keyword"):
        _q(keywords=[["ok"], [" "]]).validate()


# ---- SQL builder ---------------------------------------------------------

def test_sql_contains_all_filters():
    q = _q(cpc=["H02J50"], countries=["us", "EP"], date_from="2015-01-01",
           date_to="2024-12-31", assignees=["Example Power"], limit=50)
    sql = build_search_sql(q)
    assert "country_code IN ('US', 'EP')" in sql
    assert "publication_date >= 20150101" in sql
    assert "publication_date <= 20241231" in sql
    assert "STARTS_WITH(c.code, 'H02J50')" in sql
    assert "REGEXP_CONTAINS(LOWER(a.name)" in sql and "example" in sql.lower()
    assert sql.rstrip().endswith("LIMIT 50")
    # one AND-block per keyword group
    assert sql.count("REGEXP_CONTAINS(LOWER(x.text)") == 4  # 2 groups x (title+abstract)


def test_sql_or_within_group_and_escaping():
    sql = build_search_sql(_q(keywords=[["foo (bar)", "o'neill"]]))
    assert "\\\\(bar\\\\)" in sql           # regex-escaped paren, backslash doubled for SQL
    assert "o\\'neill" in sql               # SQL-escaped quote
    assert "|" in sql                       # OR within group


def test_sql_claims_column_only_when_requested():
    assert "claims_localized" not in build_search_sql(_q())
    assert "claims_localized" in build_search_sql(_q(search_claims=True))


def test_sql_date_validation():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        build_search_sql(_q(date_from="2015/01/01"))


def test_fetch_sql_includes_claims_for_fto_handoff():
    # The search SELECT omits claims (cost); the handoff fetch MUST have them,
    # otherwise the bq-export path feeds claim-less records into FTO scoring.
    sql = build_fetch_sql(["US-9500000-B2", "EP-3000000-A1"])
    assert "claims_localized" in sql
    assert "'US-9500000-B2'" in sql and "'EP-3000000-A1'" in sql
    assert "publication_number IN" in sql
    with pytest.raises(ValueError, match="no candidate"):
        build_fetch_sql([])


# ---- ranking -------------------------------------------------------------

def test_score_title_beats_abstract():
    q = _q()
    title_hit = score_row(_rows()[0], q)      # both concepts in title
    abs_hit = score_row(_rows()[2], q)        # FOD only in abstract
    assert title_hit.score > abs_hit.score
    assert title_hit.groups_hit == 2


def test_evidence_is_verbatim_substring():
    q = _q()
    c = score_row(_rows()[0], q)
    row = _rows()[0]
    source_text = row["title_localized"][0]["text"] + " " + row["abstract_localized"][0]["text"]
    for ev in c.evidence:
        if ev.startswith(("title:", "abstract:")):
            snippet = ev.split("“", 1)[1].rstrip("”")
            assert snippet in source_text


def test_partial_concept_coverage_flags_needs_review():
    q = _q()
    c = score_row(_rows()[3], q)              # thermal pad: no FOD concept
    assert c.needs_review
    assert c.groups_hit < c.groups_total
    assert any("verify" in n for n in c.notes)


def test_family_dedupe_keeps_best_and_records_sibling():
    q = _q()
    cands = rank_rows(_rows(), q)
    nums = [c.publication_number for c in cands]
    assert "US-9500000-B2" in nums            # higher score kept
    assert "EP-3000000-A1" not in nums        # same family collapsed
    kept = next(c for c in cands if c.publication_number == "US-9500000-B2")
    assert any("EP-3000000-A1" in n for n in kept.notes)


def test_rank_is_sorted_and_stable():
    cands = rank_rows(_rows(), _q())
    scores = [c.score for c in cands]
    assert scores == sorted(scores, reverse=True)


def test_dedupe_keeps_rows_without_family():
    cands = dedupe_by_family([
        type(rank_rows(_rows(), _q())[0])(publication_number="US-1-A1", score=1),
    ])
    assert len(cands) == 1


# ---- outputs -------------------------------------------------------------

def test_candidates_csv_is_pipeline_input_format():
    q = load_query_spec(BRIEF)
    out = candidates_csv(rank_rows(_rows(), q))
    lines = out.strip().splitlines()
    assert lines[0] == "number,note"
    assert lines[1].startswith("US-")         # best hit first
    assert "score=" in lines[1]


def test_search_report_mentions_sql_and_flags():
    q = load_query_spec(BRIEF)
    cands = rank_rows(_rows(), q)
    md = render_search_report(q, cands, total_rows=len(_rows()))
    assert "```sql" in md
    assert "patents-public-data.patents.publications" in md
    assert "要確認" in md                      # the thermal row is flagged
    assert "candidates.csv" in md              # next-step pointer
