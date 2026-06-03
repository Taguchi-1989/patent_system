"""FallbackSource and RetryingSource — ordered source chain with failure trail.

Implements §7.2 (record failure reason; retry or offer an alternative route):
  - FallbackSource tries each source in order; the first hit short-circuits.
  - On miss or error, the trail records WHY each source failed, keyed by
    canonical number. Consumers can inspect self.trail to see provenance.
  - On all-miss / all-error, returns None — NO fabrication (P-NO-GUESS).
  - RetryingSource wraps any PatentSource and retries on exception, recording
    each attempt in attempt_log. Raises the last exception on exhaustion so
    FallbackSource can catch it and record "error: <reason>" in the trail.

All stdlib; no third-party dependencies.
"""

from __future__ import annotations

from ..normalize import CanonicalNumber
from .base import PatentRecord, PatentSource


class FallbackSource:
    """Ordered list of PatentSources; first hit short-circuits, trail records all outcomes.

    Args:
        sources: Ordered list of PatentSource implementations to try, highest
                 priority first (e.g. [FixtureSource, BigQueryExportSource]).

    Attributes:
        trail: dict[canonical_str, list[tuple[source_name, outcome]]]
               outcome values: "hit", "miss", "error: <reason>"
               Only sources actually tried before a hit (or all sources on
               all-miss) appear in the list. This satisfies §7.2.
    """

    name = "fallback"

    def __init__(self, sources: list[PatentSource]) -> None:
        self.sources = list(sources)
        self.trail: dict[str, list[tuple[str, str]]] = {}

    def fetch(self, number: CanonicalNumber) -> PatentRecord | None:
        """Try each source in order; return first hit or None on all-miss.

        Records the outcome for every source actually tried in self.trail.
        A hit short-circuits — sources after the hit are NOT called and do
        NOT appear in the trail (they were never tried).
        """
        canonical = number.canonical
        entries: list[tuple[str, str]] = []

        for source in self.sources:
            try:
                result = source.fetch(number)
            except Exception as exc:  # noqa: BLE001
                entries.append((source.name, f"error: {exc}"))
                continue

            if result is not None:
                entries.append((source.name, "hit"))
                self.trail[canonical] = entries
                return result  # short-circuit — stop here

            entries.append((source.name, "miss"))

        # All sources tried and none returned a record.
        self.trail[canonical] = entries
        return None  # P-NO-GUESS: never fabricate


class RetryingSource:
    """Wraps a PatentSource and retries on exception up to `attempts` times.

    Records each attempt in attempt_log: dict[canonical_str, list[tuple[int, str]]]
    where each entry is (attempt_number, "ok" | "error: <reason>").

    On final failure, raises the last exception so that FallbackSource can
    catch it and record "error: <reason>" in the trail.

    Args:
        source: The PatentSource to wrap.
        attempts: Maximum number of attempts (default 2).
    """

    def __init__(self, source: PatentSource, attempts: int = 2) -> None:
        self.source = source
        self.attempts = max(1, attempts)
        self.attempt_log: dict[str, list[tuple[int, str]]] = {}

    @property
    def name(self) -> str:
        return self.source.name

    def fetch(self, number: CanonicalNumber) -> PatentRecord | None:
        """Try up to self.attempts times; return result or raise on exhaustion."""
        canonical = number.canonical
        log: list[tuple[int, str]] = []
        last_exc: Exception | None = None

        for attempt in range(1, self.attempts + 1):
            try:
                result = self.source.fetch(number)
                log.append((attempt, "ok"))
                self.attempt_log[canonical] = log
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.append((attempt, f"error: {exc}"))

        self.attempt_log[canonical] = log
        # Raise last exception so FallbackSource records "error: <reason>".
        raise last_exc  # type: ignore[misc]
