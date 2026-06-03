"""Tests for src/patentkit/state/store.py (SnapshotStore, Snapshot, SaveResult).

Covers:
  - First save is_new=True
  - Identical re-save: changed=False, is_new=False
  - Modified record: changed=True
  - history() returns snapshots in chronological order
  - latest() returns the most recent snapshot
  - Content hash excludes raw and notes fields
  - Content hash excludes source / source_url fields

Run from repo root:
    py -m pytest tests/test_state.py -q
    py tests/test_state.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.state import SaveResult, Snapshot, SnapshotStore  # noqa: E402
from patentkit.state.store import _content_hash                   # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_record(
    canonical: str = "US-10123456-B2",
    legal_status: str = "ACTIVE",
    title: str = "Test Patent",
    claims: list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Return a plain dict that mimics a PatentRecord."""
    r = {
        "canonical": canonical,
        "office": "US",
        "number": "10123456",
        "title": title,
        "abstract": "An abstract.",
        "claims": claims if claims is not None else ["1. A method."],
        "assignee": "ACME Corp",
        "pub_date": "2021-09-14",
        "legal_status": legal_status,
        "family_id": "FAM-0001",
        "source": "test",
        "source_url": "https://example.com/test",
        "notes": [],
        "raw": {"original": "payload"},
    }
    if extra:
        r.update(extra)
    return r


# ---------------------------------------------------------------------------
# Test 1: First save is_new=True
# ---------------------------------------------------------------------------

def test_first_save_is_new():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SnapshotStore(tmpdir)
        rec = make_record()
        result = store.save(rec)

        assert isinstance(result, SaveResult)
        assert result.is_new is True
        assert result.changed is False
        assert isinstance(result.snapshot, Snapshot)
        assert result.previous is None


# ---------------------------------------------------------------------------
# Test 2: Identical re-save => changed=False, is_new=False
# ---------------------------------------------------------------------------

def test_identical_resave_is_unchanged():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SnapshotStore(tmpdir)
        rec = make_record()

        first = store.save(rec)
        assert first.is_new is True

        second = store.save(rec)
        assert second.changed is False
        assert second.is_new is False
        # Same snapshot returned (no new file written)
        assert second.snapshot.content_hash == first.snapshot.content_hash


# ---------------------------------------------------------------------------
# Test 3: Modified record => changed=True
# ---------------------------------------------------------------------------

def test_modified_record_is_changed():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SnapshotStore(tmpdir)
        rec = make_record(legal_status="ACTIVE")
        store.save(rec)

        rec_modified = make_record(legal_status="LAPSED")
        result = store.save(rec_modified)

        assert result.changed is True
        assert result.is_new is False
        assert result.previous is not None


# ---------------------------------------------------------------------------
# Test 4: history() returns snapshots in chronological order
# ---------------------------------------------------------------------------

def test_history_is_chronological():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SnapshotStore(tmpdir)

        store.save(make_record(legal_status="ACTIVE"))
        # Need a slight delay so the filename timestamps sort correctly.
        time.sleep(0.02)
        store.save(make_record(legal_status="LAPSED"))
        time.sleep(0.02)
        store.save(make_record(legal_status="EXPIRED"))

        snaps = store.history("US-10123456-B2")
        assert len(snaps) == 3
        # Filenames are ISO timestamps, so lexicographic order = chronological.
        assert snaps[0].record["legal_status"] == "ACTIVE"
        assert snaps[1].record["legal_status"] == "LAPSED"
        assert snaps[2].record["legal_status"] == "EXPIRED"


# ---------------------------------------------------------------------------
# Test 5: latest() returns the most recent snapshot
# ---------------------------------------------------------------------------

def test_latest_returns_most_recent():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SnapshotStore(tmpdir)

        store.save(make_record(legal_status="ACTIVE"))
        time.sleep(0.02)
        store.save(make_record(legal_status="LAPSED"))

        latest = store.latest("US-10123456-B2")
        assert latest is not None
        assert latest.record["legal_status"] == "LAPSED"


# ---------------------------------------------------------------------------
# Test 6: Content hash excludes raw and notes
# ---------------------------------------------------------------------------

def test_hash_excludes_raw_and_notes():
    rec_a = make_record(extra={"raw": {"big": "payload"}, "notes": []})
    rec_b = make_record(extra={"raw": {"different": "raw data"}, "notes": ["a note added"]})

    # Different raw/notes must NOT change the hash.
    assert _content_hash(rec_a) == _content_hash(rec_b)


# ---------------------------------------------------------------------------
# Test 7: Content hash excludes source and source_url
# ---------------------------------------------------------------------------

def test_hash_excludes_source_fields():
    rec_a = make_record(extra={"source": "fixture", "source_url": None})
    rec_b = make_record(extra={"source": "bq-export", "source_url": "https://patents.google.com/X"})

    # Different source provider must NOT change the hash.
    assert _content_hash(rec_a) == _content_hash(rec_b)


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
