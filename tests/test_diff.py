"""Tests for src/patentkit/state/diff.py (diff_records, FieldChange, RecordDiff).

Covers:
  - Identical records => RecordDiff.changed is False
  - legal_status change detected as kind="changed"
  - Claim added detected as kind="added"
  - Claim removed detected as kind="removed"
  - Claim text modified detected as kind="changed"
  - family_id change detected
  - Generic field change detected (title, abstract, etc.)
  - Excluded fields (raw, source, source_url, notes) not diffed
  - NOT_YET_TRACKED constant is importable and non-empty
  - by_field() groups claim changes under "claims"

Run from repo root:
    py -m pytest tests/test_diff.py -q
    py tests/test_diff.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.state.diff import (  # noqa: E402
    FieldChange,
    NOT_YET_TRACKED,
    RecordDiff,
    diff_records,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rec(
    canonical: str = "US-10123456-B2",
    legal_status: str = "ACTIVE",
    claims: list[str] | None = None,
    family_id: str = "FAM-0001",
    title: str = "Test Patent",
    abstract: str = "An abstract.",
    assignee: str = "ACME Corp",
    pub_date: str = "2021-09-14",
    **extra,
) -> dict:
    return {
        "canonical": canonical,
        "office": "US",
        "number": "10123456",
        "title": title,
        "abstract": abstract,
        "claims": claims if claims is not None else ["1. A method."],
        "assignee": assignee,
        "pub_date": pub_date,
        "legal_status": legal_status,
        "family_id": family_id,
        "source": "test",
        "source_url": "https://example.com/test",
        "notes": [],
        "raw": {"orig": "data"},
        **extra,
    }


# ---------------------------------------------------------------------------
# Test 1: Identical records => not changed
# ---------------------------------------------------------------------------

def test_identical_records_not_changed():
    old = make_rec()
    new = make_rec()
    result = diff_records(old, new)
    assert isinstance(result, RecordDiff)
    assert result.changed is False
    assert result.changes == []


# ---------------------------------------------------------------------------
# Test 2: legal_status change detected
# ---------------------------------------------------------------------------

def test_legal_status_change_detected():
    old = make_rec(legal_status="ACTIVE")
    new = make_rec(legal_status="LAPSED")
    result = diff_records(old, new)

    assert result.changed is True
    legal_changes = [c for c in result.changes if c.field == "legal_status"]
    assert len(legal_changes) == 1
    assert legal_changes[0].kind == "changed"
    assert legal_changes[0].before == "ACTIVE"
    assert legal_changes[0].after == "LAPSED"


# ---------------------------------------------------------------------------
# Test 3: Claim added
# ---------------------------------------------------------------------------

def test_claim_added_detected():
    old = make_rec(claims=["1. A method."])
    new = make_rec(claims=["1. A method.", "2. The method of claim 1, further comprising X."])
    result = diff_records(old, new)

    assert result.changed is True
    added = [c for c in result.changes if c.kind == "added"]
    assert len(added) == 1
    assert added[0].field == "claims[1]"
    assert added[0].before is None
    assert "2. The method" in added[0].after


# ---------------------------------------------------------------------------
# Test 4: Claim removed
# ---------------------------------------------------------------------------

def test_claim_removed_detected():
    old = make_rec(claims=["1. A method.", "2. Dependent claim."])
    new = make_rec(claims=["1. A method."])
    result = diff_records(old, new)

    assert result.changed is True
    removed = [c for c in result.changes if c.kind == "removed"]
    assert len(removed) == 1
    assert removed[0].field == "claims[1]"
    assert removed[0].after is None
    assert "Dependent claim" in removed[0].before


# ---------------------------------------------------------------------------
# Test 5: Claim text modified
# ---------------------------------------------------------------------------

def test_claim_modified_detected():
    old = make_rec(claims=["1. A method comprising step A."])
    new = make_rec(claims=["1. A method comprising step A and step B."])
    result = diff_records(old, new)

    assert result.changed is True
    changed_claims = [c for c in result.changes if c.field == "claims[0]" and c.kind == "changed"]
    assert len(changed_claims) == 1


# ---------------------------------------------------------------------------
# Test 6: family_id change detected
# ---------------------------------------------------------------------------

def test_family_id_change_detected():
    old = make_rec(family_id="FAM-0001")
    new = make_rec(family_id="FAM-9999")
    result = diff_records(old, new)

    assert result.changed is True
    fam_changes = [c for c in result.changes if c.field == "family_id"]
    assert len(fam_changes) == 1
    assert fam_changes[0].kind == "changed"
    assert fam_changes[0].before == "FAM-0001"
    assert fam_changes[0].after == "FAM-9999"


# ---------------------------------------------------------------------------
# Test 7: Generic field change detected (title)
# ---------------------------------------------------------------------------

def test_generic_field_change_detected():
    old = make_rec(title="Original Title")
    new = make_rec(title="Updated Title")
    result = diff_records(old, new)

    assert result.changed is True
    title_changes = [c for c in result.changes if c.field == "title"]
    assert len(title_changes) == 1
    assert title_changes[0].kind == "changed"


# ---------------------------------------------------------------------------
# Test 8: Excluded fields (raw, source, source_url, notes) not diffed
# ---------------------------------------------------------------------------

def test_excluded_fields_not_diffed():
    old = make_rec()
    new = make_rec()
    # Modify excluded fields only
    new["raw"] = {"completely": "different"}
    new["source"] = "bq-export"
    new["source_url"] = "https://other.example.com"
    new["notes"] = ["a new note"]

    result = diff_records(old, new)
    assert result.changed is False
    # Ensure none of the excluded fields appear in changes
    excluded_fields = {"raw", "source", "source_url", "notes"}
    for change in result.changes:
        assert change.field not in excluded_fields


# ---------------------------------------------------------------------------
# Test 9: NOT_YET_TRACKED constant is importable and non-empty
# ---------------------------------------------------------------------------

def test_not_yet_tracked_constant():
    assert isinstance(NOT_YET_TRACKED, list)
    assert len(NOT_YET_TRACKED) >= 3, "Must document at least citations, prosecution docs, continuations"
    # Check that at least one entry mentions each of the three categories.
    joined = " ".join(NOT_YET_TRACKED).lower()
    assert "citation" in joined or "引用" in joined
    assert "prosecution" in joined or "追加書類" in joined
    assert "continuation" in joined or "継続" in joined


# ---------------------------------------------------------------------------
# Test 10: by_field() groups claim changes under "claims"
# ---------------------------------------------------------------------------

def test_by_field_groups_claims():
    old = make_rec(claims=["1. Claim one.", "2. Claim two."])
    new = make_rec(claims=["1. Claim one modified.", "2. Claim two.", "3. Claim three."])
    result = diff_records(old, new)

    assert result.changed is True
    by_field = result.by_field()
    assert "claims" in by_field
    # claims[0] changed, claims[2] added => 2 entries under "claims"
    assert len(by_field["claims"]) == 2


# ---------------------------------------------------------------------------
# __main__ runner (run without pytest)
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
