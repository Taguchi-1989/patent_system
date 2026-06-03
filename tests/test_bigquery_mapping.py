"""Tests for the BigQuery row -> PatentRecord mapper (no live BigQuery needed).

    py tests/test_bigquery_mapping.py      (or: py -m pytest tests/test_bigquery_mapping.py -q)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.connectors import record_from_bq_row  # noqa: E402

# A row shaped like `patents-public-data.patents.publications`.
SAMPLE_ROW = {
    "publication_number": "US-10123456-B2",
    "country_code": "US",
    "kind_code": "B2",
    "application_number": "US-201612345-A",
    "publication_date": 20210914,
    "family_id": "FAM-0001",
    "title_localized": [
        {"text": "ワイヤレス給電", "language": "ja"},
        {"text": "Wireless power transfer", "language": "en"},
    ],
    "abstract_localized": [{"text": "A wireless charging system.", "language": "en"}],
    "claims_localized": [{
        "text": "1. A wireless power apparatus comprising a transmitter coil.\n"
                "2. The apparatus of claim 1, wherein the coil is planar.",
        "language": "en",
    }],
    "assignee_harmonized": [{"name": "Sample Wireless Corp.", "country_code": "US"}],
}


def test_basic_fields():
    rec = record_from_bq_row(SAMPLE_ROW)
    assert rec.canonical == "US-10123456-B2"
    assert rec.office == "US"
    assert rec.number == "10123456"
    assert rec.family_id == "FAM-0001"
    assert rec.assignee == "Sample Wireless Corp."


def test_prefers_english_localized_text():
    rec = record_from_bq_row(SAMPLE_ROW)
    assert rec.title == "Wireless power transfer"  # 'en' chosen over 'ja'


def test_date_is_formatted():
    rec = record_from_bq_row(SAMPLE_ROW)
    assert rec.pub_date == "2021-09-14"


def test_claims_split_and_flagged():
    rec = record_from_bq_row(SAMPLE_ROW)
    assert len(rec.claims) == 2
    assert rec.claims[0].startswith("1.")
    assert rec.claims[1].startswith("2.")
    assert any("split heuristically" in n for n in rec.notes)


def test_provenance_and_honest_limits():
    rec = record_from_bq_row(SAMPLE_ROW)
    assert rec.source_url == "https://patents.google.com/patent/US-10123456-B2"
    assert "BigQuery" in rec.source
    assert rec.legal_status is None
    assert any("legal status not present" in n for n in rec.notes)


def test_missing_fields_dont_crash():
    rec = record_from_bq_row({"publication_number": "EP-1234567-B1"})
    assert rec.office == "EP"
    assert rec.number == "1234567"
    assert rec.claims == []
    assert rec.assignee is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        else:
            passed += 1
            print(f"ok   {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
