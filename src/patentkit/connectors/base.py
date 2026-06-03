"""Core data model + source protocol for the Retrieval layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..normalize import CanonicalNumber


@dataclass
class PatentRecord:
    """A normalized patent record, source-agnostic.

    Every field that feeds analysis carries provenance: `source` (which dataset)
    and `source_url` (a citation link). Per the quality requirement (§7.2), no
    downstream claim is made without an attached source.
    """

    canonical: str
    office: str
    number: str
    title: str = ""
    abstract: str = ""
    claims: list[str] = field(default_factory=list)   # claim texts; [0] is claim 1
    assignee: str | None = None
    pub_date: str | None = None
    legal_status: str | None = None
    family_id: str | None = None
    source: str = "unknown"                            # provenance: provider/dataset name
    source_url: str | None = None                      # citation link
    notes: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)            # original payload, for evidence


@runtime_checkable
class PatentSource(Protocol):
    """A data source that can fetch one record for a normalized number."""

    name: str

    def fetch(self, number: CanonicalNumber) -> PatentRecord | None:
        """Return a PatentRecord, or None if this source has no data for it."""
        ...
