"""Tests for the USPTO Bulk Data connector (no real download required).

    py tests/test_bulk_uspto.py      (or: py -m pytest tests/test_bulk_uspto.py -q)

The sample fixture at src/patentkit/connectors/fixtures/uspto_grant_sample.xml
contains TWO concatenated <us-patent-grant> documents with realistic ICE DTD
structure.  All tests use that local file — no network access occurs.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.connectors.bulk_uspto import (  # noqa: E402
    BulkDataSource,
    parse_uspto_grant_xml,
    week_url_for_date,
)
from patentkit.normalize import normalize  # noqa: E402

# Absolute path to the sample fixture
_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..", "src", "patentkit", "connectors", "fixtures", "uspto_grant_sample.xml",
)
_FIXTURE = os.path.abspath(_FIXTURE)


def _load_fixture_records():
    """Parse the sample fixture and return the list of PatentRecords."""
    with open(_FIXTURE, "rb") as f:
        data = f.read()
    return parse_uspto_grant_xml(data)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

def test_parse_yields_two_records():
    """The concatenated sample XML produces exactly 2 PatentRecords."""
    records = _load_fixture_records()
    assert len(records) == 2, f"Expected 2 records, got {len(records)}"


def test_canonical_numbers_are_correct():
    """Both records have the expected canonical US-NNNNNNN-KK format."""
    records = _load_fixture_records()
    canonicals = {r.canonical for r in records}
    assert "US-10123456-B2" in canonicals, f"Missing US-10123456-B2 in {canonicals}"
    assert "US-9876543-B1" in canonicals, f"Missing US-9876543-B1 in {canonicals}"


def test_first_record_fields():
    """The US-10123456-B2 record has correct title, assignee, office, and number."""
    records = _load_fixture_records()
    rec = next(r for r in records if r.canonical == "US-10123456-B2")
    assert rec.office == "US"
    assert rec.number == "10123456"
    assert "wireless" in rec.title.lower(), f"Unexpected title: {rec.title!r}"
    assert rec.assignee is not None and "Acme" in rec.assignee, (
        f"Unexpected assignee: {rec.assignee!r}"
    )
    assert rec.pub_date == "2018-10-02"


def test_second_record_fields():
    """The US-9876543-B1 record has correct title, assignee, office, and number."""
    records = _load_fixture_records()
    rec = next(r for r in records if r.canonical == "US-9876543-B1")
    assert rec.office == "US"
    assert rec.number == "9876543"
    assert "alignment" in rec.title.lower() or "receiver" in rec.title.lower(), (
        f"Unexpected title: {rec.title!r}"
    )
    assert rec.assignee is not None and "QuantumCharge" in rec.assignee, (
        f"Unexpected assignee: {rec.assignee!r}"
    )
    assert rec.pub_date == "2018-01-02"


def test_claims_are_nonempty():
    """Both records have non-empty claims lists."""
    records = _load_fixture_records()
    for rec in records:
        assert len(rec.claims) > 0, (
            f"{rec.canonical}: expected non-empty claims, got {rec.claims!r}"
        )


def test_abstract_collected_across_descendants():
    """Abstracts are collected using itertext (spans <p>, <b>, etc.)."""
    records = _load_fixture_records()
    rec_b2 = next(r for r in records if r.canonical == "US-10123456-B2")
    rec_b1 = next(r for r in records if r.canonical == "US-9876543-B1")
    # Both abstracts should be non-empty
    assert len(rec_b2.abstract) > 20, f"Abstract too short: {rec_b2.abstract!r}"
    assert len(rec_b1.abstract) > 20, f"Abstract too short: {rec_b1.abstract!r}"
    # The B1 abstract contains a <b> tag with text; itertext must collect it
    assert "magnetic field sensors" in rec_b1.abstract.lower() or \
           "magnetic" in rec_b1.abstract.lower(), (
        f"Expected 'magnetic' in abstract: {rec_b1.abstract!r}"
    )


def test_provenance_fields():
    """Source and source_url are set correctly (no fabrication)."""
    records = _load_fixture_records()
    for rec in records:
        assert rec.source == "USPTO Bulk Data (Patent Grant Full-Text XML)", (
            f"{rec.canonical}: unexpected source {rec.source!r}"
        )
        assert rec.source_url is not None, f"{rec.canonical}: source_url is None"
        assert "patents.google.com" in rec.source_url, (
            f"{rec.canonical}: unexpected source_url {rec.source_url!r}"
        )


# ---------------------------------------------------------------------------
# week_url_for_date tests
# ---------------------------------------------------------------------------

def test_week_url_for_date_date_object():
    """week_url_for_date accepts a datetime.date and builds the expected URL."""
    d = date(2018, 10, 2)
    url = week_url_for_date(d)
    assert url == "https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/2018/ipg181002.zip", (
        f"Unexpected URL: {url!r}"
    )


def test_week_url_for_date_iso_string():
    """week_url_for_date also accepts an ISO-format string."""
    url = week_url_for_date("2021-09-14")
    assert "2021" in url
    assert "ipg210914.zip" in url, f"Unexpected URL: {url!r}"


# ---------------------------------------------------------------------------
# BulkDataSource tests
# ---------------------------------------------------------------------------

def test_bulk_data_source_fetch_indexed_number():
    """BulkDataSource.fetch() returns the correct record for an indexed number."""
    src = BulkDataSource(local_files=[_FIXTURE])
    cn = normalize("US10123456B2")
    rec = src.fetch(cn)
    assert rec is not None, "Expected a PatentRecord for US-10123456-B2"
    assert rec.canonical == "US-10123456-B2"
    assert len(rec.claims) > 0


def test_bulk_data_source_fetch_second_number():
    """BulkDataSource.fetch() also works for the second patent in the fixture."""
    src = BulkDataSource(local_files=[_FIXTURE])
    cn = normalize("US9876543B1")
    rec = src.fetch(cn)
    assert rec is not None, "Expected a PatentRecord for US-9876543-B1"
    assert rec.canonical == "US-9876543-B1"


def test_bulk_data_source_fetch_unindexed_returns_none():
    """BulkDataSource.fetch() returns None (not a fabricated record) for an
    unindexed number.  This exercises the honest limitation: no record is
    made up when the patent is not in any indexed file."""
    src = BulkDataSource(local_files=[_FIXTURE])
    cn = normalize("US99999999B2")
    result = src.fetch(cn)
    assert result is None, (
        f"Expected None for unindexed number, got {result!r}"
    )


def test_bulk_data_source_empty_no_files():
    """BulkDataSource with no files returns None for any lookup."""
    src = BulkDataSource()
    cn = normalize("US10123456B2")
    assert src.fetch(cn) is None


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
