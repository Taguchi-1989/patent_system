"""Tests for FallbackSource and RetryingSource.

Covers:
  - order_respected: sources are tried in given order
  - hit_short_circuits: after a hit, remaining sources are NOT called
  - all_miss_returns_none: all-miss returns None (no fabrication)
  - all_error_returns_none: all-error returns None
  - trail_records_miss_reasons: trail includes "miss" entries with source names
  - trail_records_error_reasons: trail includes "error: ..." entries with reasons
  - retry_success: RetryingSource succeeds on second attempt
  - retry_exhausted_raises: RetryingSource raises on exhaustion; FallbackSource records error

Run from repo root:
    py -m pytest tests/test_fallback.py -q
    py tests/test_fallback.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.connectors.fallback import FallbackSource, RetryingSource  # noqa: E402
from patentkit.connectors.base import PatentRecord                         # noqa: E402
from patentkit.normalize import normalize                                   # noqa: E402


# ---------------------------------------------------------------------------
# Fake in-memory PatentSource implementations for testing
# ---------------------------------------------------------------------------

class _HitSource:
    """Always returns a record (a hit)."""
    def __init__(self, name: str = "hit_source") -> None:
        self.name = name
        self.call_count = 0

    def fetch(self, number):
        self.call_count += 1
        return PatentRecord(
            canonical=number.canonical,
            office="US",
            number=number.number,
            title="Fake Patent",
            source=self.name,
            source_url=None,
        )


class _MissSource:
    """Always returns None (a miss)."""
    def __init__(self, name: str = "miss_source") -> None:
        self.name = name
        self.call_count = 0

    def fetch(self, number):
        self.call_count += 1
        return None


class _ErrorSource:
    """Always raises an exception."""
    def __init__(self, name: str = "error_source", msg: str = "network failure") -> None:
        self.name = name
        self.msg = msg
        self.call_count = 0

    def fetch(self, number):
        self.call_count += 1
        raise RuntimeError(self.msg)


class _FlakySource:
    """Fails on the first N calls then succeeds."""
    def __init__(self, name: str = "flaky_source", fail_times: int = 1) -> None:
        self.name = name
        self.fail_times = fail_times
        self.call_count = 0

    def fetch(self, number):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise ConnectionError(f"attempt {self.call_count} failed")
        return PatentRecord(
            canonical=number.canonical,
            office="US",
            number=number.number,
            title="Flaky Patent",
            source=self.name,
            source_url=None,
        )


_NUM = normalize("US-10123456-B2")


# ---------------------------------------------------------------------------
# Test 1: order_respected
# ---------------------------------------------------------------------------

def test_order_respected():
    """Sources are tried in given order; first non-None result is returned."""
    miss = _MissSource("first_miss")
    hit = _HitSource("second_hit")
    third = _HitSource("third_never_called")

    fb = FallbackSource([miss, hit, third])
    result = fb.fetch(_NUM)

    assert result is not None
    assert miss.call_count == 1, "first source must be tried"
    assert hit.call_count == 1, "second source must be tried"
    assert third.call_count == 0, "third source must NOT be called after a hit"
    assert result.source == "second_hit"


# ---------------------------------------------------------------------------
# Test 2: hit_short_circuits
# ---------------------------------------------------------------------------

def test_hit_short_circuits():
    """After the first hit, remaining sources are never called."""
    hit = _HitSource("first_hit")
    never = _HitSource("never_called")

    fb = FallbackSource([hit, never])
    result = fb.fetch(_NUM)

    assert result is not None
    assert hit.call_count == 1
    assert never.call_count == 0
    # Trail should only record the hit source, not the never-called one.
    trail = fb.trail[_NUM.canonical]
    assert len(trail) == 1
    assert trail[0] == ("first_hit", "hit")


# ---------------------------------------------------------------------------
# Test 3: all_miss_returns_none
# ---------------------------------------------------------------------------

def test_all_miss_returns_none():
    """All-miss returns None; no record is fabricated (P-NO-GUESS)."""
    fb = FallbackSource([_MissSource("a"), _MissSource("b")])
    result = fb.fetch(_NUM)

    assert result is None
    trail = fb.trail[_NUM.canonical]
    assert trail == [("a", "miss"), ("b", "miss")]


# ---------------------------------------------------------------------------
# Test 4: all_error_returns_none
# ---------------------------------------------------------------------------

def test_all_error_returns_none():
    """All-error returns None; no exception propagates."""
    fb = FallbackSource([_ErrorSource("e1", "timeout"), _ErrorSource("e2", "dns error")])
    result = fb.fetch(_NUM)

    assert result is None
    trail = fb.trail[_NUM.canonical]
    assert len(trail) == 2
    assert trail[0][0] == "e1"
    assert trail[0][1].startswith("error: ")
    assert "timeout" in trail[0][1]
    assert trail[1][0] == "e2"
    assert "dns error" in trail[1][1]


# ---------------------------------------------------------------------------
# Test 5: trail_records_miss_reasons (section §7.2)
# ---------------------------------------------------------------------------

def test_trail_records_miss_reasons():
    """Trail accurately records miss entries with source names (§7.2)."""
    sources = [_MissSource("src_a"), _MissSource("src_b"), _HitSource("src_c")]
    fb = FallbackSource(sources)
    result = fb.fetch(_NUM)

    assert result is not None
    trail = fb.trail[_NUM.canonical]
    assert trail[0] == ("src_a", "miss")
    assert trail[1] == ("src_b", "miss")
    assert trail[2] == ("src_c", "hit")


# ---------------------------------------------------------------------------
# Test 6: retry_success (RetryingSource succeeds on second attempt)
# ---------------------------------------------------------------------------

def test_retry_success():
    """RetryingSource records each attempt and returns result when retry succeeds."""
    flaky = _FlakySource("flaky", fail_times=1)
    retrying = RetryingSource(flaky, attempts=3)

    result = retrying.fetch(_NUM)

    assert result is not None
    assert flaky.call_count == 2
    log = retrying.attempt_log[_NUM.canonical]
    assert len(log) == 2
    assert log[0][1].startswith("error: ")
    assert log[1][1] == "ok"


# ---------------------------------------------------------------------------
# Test 7: retry_exhausted -> FallbackSource records error in trail
# ---------------------------------------------------------------------------

def test_retry_exhausted_fallback_records_error():
    """RetryingSource raises on exhaustion; FallbackSource catches it and records in trail."""
    always_fails = _ErrorSource("always_fail", "persistent error")
    retrying = RetryingSource(always_fails, attempts=2)
    hit = _HitSource("backup_hit")

    fb = FallbackSource([retrying, hit])
    result = fb.fetch(_NUM)

    # Should fall through to backup_hit.
    assert result is not None
    assert result.source == "backup_hit"
    trail = fb.trail[_NUM.canonical]
    assert len(trail) == 2
    assert trail[0][0] == "always_fail"  # RetryingSource.name delegates to wrapped source
    assert trail[0][1].startswith("error: ")
    assert "persistent error" in trail[0][1]
    assert trail[1] == ("backup_hit", "hit")


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
