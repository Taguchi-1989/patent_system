"""Tests for canonical patent-number normalization.

Run from repo root:  py -m pytest tests/test_numbers.py -q
(or without pytest:   py tests/test_numbers.py)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.normalize import DocType, Office, normalize  # noqa: E402


def test_us_grant_with_separators():
    n = normalize("US 10,123,456 B2")
    assert n.office is Office.US
    assert n.number == "10123456"
    assert n.kind == "B2"
    assert n.doc_type is DocType.GRANT
    assert n.canonical == "US-10123456-B2"
    assert not n.needs_review


def test_us_grant_bare_prefix():
    n = normalize("US7654321")
    assert n.office is Office.US
    assert n.number == "7654321"
    assert n.doc_type is DocType.GRANT


def test_us_application_publication():
    n = normalize("US20210123456A1")
    assert n.office is Office.US
    assert n.doc_type is DocType.PUBLICATION
    assert n.number == "20210123456"
    assert n.kind == "A1"


def test_ep_grant():
    n = normalize("EP 1 234 567 B1")
    assert n.office is Office.EP
    assert n.number == "1234567"
    assert n.kind == "B1"
    assert n.doc_type is DocType.GRANT


def test_wo_publication_slash():
    n = normalize("WO2020/123456A1")
    assert n.office is Office.WO
    assert n.number == "2020123456"
    assert n.kind == "A1"
    assert n.doc_type is DocType.PUBLICATION


def test_jp_kokai_western():
    n = normalize("特開2003-123456")
    assert n.office is Office.JP
    assert n.doc_type is DocType.PUBLICATION
    assert n.kind == "A"
    assert n.number == "2003123456"


def test_jp_latin_western():
    n = normalize("JP2003123456A")
    assert n.office is Office.JP
    assert n.number == "2003123456"
    assert n.kind == "A"


def test_jp_era_heisei_is_flagged():
    n = normalize("特開平10-123456")
    assert n.office is Office.JP
    # Heisei 10 -> 1998
    assert n.number == "1998123456"
    assert n.needs_review  # era conversion must not be presented as certain
    assert any("Western year 1998" in note for note in n.notes)


def test_jp_grant_serial_is_flagged():
    n = normalize("特許第4123456号")
    assert n.office is Office.JP
    assert n.doc_type is DocType.GRANT
    assert n.number == "4123456"
    assert n.needs_review
    assert any("no year is encoded" in note for note in n.notes)


def test_unknown_office_is_flagged_not_guessed():
    n = normalize("123456789")
    assert n.office is Office.UNKNOWN
    assert n.needs_review
    assert n.notes  # must explain why


def test_empty_input():
    n = normalize("")
    assert n.office is Office.UNKNOWN
    assert n.confidence == 0.0


if __name__ == "__main__":
    # Allow running without pytest installed.
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
