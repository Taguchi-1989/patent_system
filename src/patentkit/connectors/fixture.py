"""FixtureSource — a zero-dependency, zero-key patent source backed by local
JSON files. Lets the entire pipeline run end-to-end with no network and no
credentials, for development, tests, and demos.

A real keyless source (BigQuery Sandbox / USPTO bulk) implements the same
`fetch()` signature and drops in without any pipeline change.
"""

from __future__ import annotations

import json
import os

from ..normalize import CanonicalNumber
from .base import PatentRecord

_DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class FixtureSource:
    name = "fixture"

    def __init__(self, directory: str = _DEFAULT_DIR):
        self.directory = directory
        self._by_canonical: dict[str, dict] = {}
        self._by_number: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.isdir(self.directory):
            return
        for fn in sorted(os.listdir(self.directory)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(self.directory, fn), encoding="utf-8") as f:
                data = json.load(f)
            if data.get("canonical"):
                self._by_canonical[data["canonical"]] = data
            if data.get("number"):
                self._by_number[f'{data.get("office", "")}:{data["number"]}'] = data

    def fetch(self, number: CanonicalNumber) -> PatentRecord | None:
        data = self._by_canonical.get(number.canonical)
        if data is None:
            data = self._by_number.get(f"{number.office.value}:{number.number}")
        if data is None:
            return None
        return PatentRecord(
            canonical=data.get("canonical", number.canonical),
            office=data.get("office", number.office.value),
            number=data.get("number", number.number),
            title=data.get("title", ""),
            abstract=data.get("abstract", ""),
            claims=data.get("claims", []),
            assignee=data.get("assignee"),
            pub_date=data.get("pub_date"),
            legal_status=data.get("legal_status"),
            family_id=data.get("family_id"),
            source=data.get("source", "FIXTURE (local sample data, not a live fetch)"),
            source_url=data.get("source_url"),
            notes=data.get("notes", []),
            raw=data,
        )
