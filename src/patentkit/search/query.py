"""Search brief -> BigQuery SQL (pure functions, no live dependencies).

The query spec is a small JSON file (a "search brief"):

    {
      "name": "wireless-power-fod",
      "purpose": "FTO screening for wireless charger product",
      "keywords": [["wireless power", "inductive charging"],
                   ["foreign object detection", "FOD"]],
      "cpc": ["H02J50"],
      "assignees": [],
      "countries": ["US", "EP"],
      "date_from": "2015-01-01",
      "date_to": null,
      "limit": 200,
      "search_claims": false
    }

Semantics follow professional search-formula practice: OR within a keyword
group, AND across groups (each group is one concept of the invention).
CPC/assignee/country/date are additional AND filters.

The generated SQL inlines literals so it can be pasted into the BigQuery
console as-is (zero-install route). All user text is escaped; keywords are
regex-escaped and matched case-insensitively.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_TABLE = "`patents-public-data.patents.publications`"


@dataclass
class SearchQuery:
    """A validated search brief. AND across keyword groups, OR within."""

    name: str = "search"
    purpose: str = ""
    report_type: str = ""     # "prior-art" | "fto" | "sdi" | "" (generic) — M11
    description: str = ""     # free-text invention description (semantic query, M10)
    keywords: list[list[str]] = field(default_factory=list)
    cpc: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    limit: int = 200
    search_claims: bool = False

    def validate(self) -> None:
        if not self.keywords and not self.cpc and not self.assignees:
            raise ValueError(
                "search brief needs at least one of keywords/cpc/assignees — "
                "refusing to scan the whole table without a filter"
            )
        for g in self.keywords:
            if not g or not all(isinstance(k, str) and k.strip() for k in g):
                raise ValueError(f"empty keyword group or blank keyword: {g!r}")
        if self.limit < 1 or self.limit > 5000:
            raise ValueError("limit must be 1..5000")


def load_query_spec(path: str) -> SearchQuery:
    """Load and validate a JSON search brief."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    known = {f_.name for f_ in SearchQuery.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown keys in search brief: {sorted(unknown)}")
    q = SearchQuery(**{k: v for k, v in raw.items() if v is not None})
    q.validate()
    return q


def _date_int(d: str) -> int:
    """'YYYY-MM-DD' or 'YYYYMMDD' -> int yyyymmdd (table stores INT64 dates)."""
    s = d.replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"date must be YYYY-MM-DD: {d!r}")
    return int(s)


def _sql_str(s: str) -> str:
    """Escape a string for a single-quoted SQL literal."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _group_regex(group: list[str]) -> str:
    """OR-group of keywords -> one case-insensitive regex (keywords escaped)."""
    return "|".join(re.escape(k.strip().lower()) for k in group)


def _text_match(regex: str, search_claims: bool) -> str:
    """Match one concept regex against title/abstract (and optionally claims)."""
    fields = ["title_localized", "abstract_localized"]
    if search_claims:
        fields.append("claims_localized")
    lit = _sql_str(regex)
    parts = [
        f"EXISTS(SELECT 1 FROM UNNEST({f}) AS x WHERE REGEXP_CONTAINS(LOWER(x.text), {lit}))"
        for f in fields
    ]
    return "(" + "\n       OR ".join(parts) + ")"


def build_search_sql(q: SearchQuery) -> str:
    """Render the search brief as console-pasteable BigQuery SQL."""
    q.validate()
    conds: list[str] = []
    if q.countries:
        conds.append("country_code IN (" + ", ".join(_sql_str(c.upper()) for c in q.countries) + ")")
    if q.date_from:
        conds.append(f"publication_date >= {_date_int(q.date_from)}")
    if q.date_to:
        conds.append(f"publication_date <= {_date_int(q.date_to)}")
    for group in q.keywords:
        conds.append(_text_match(_group_regex(group), q.search_claims))
    if q.cpc:
        cpc_parts = " OR ".join(
            f"STARTS_WITH(c.code, {_sql_str(p.upper())})" for p in q.cpc
        )
        conds.append(f"EXISTS(SELECT 1 FROM UNNEST(cpc) AS c WHERE {cpc_parts})")
    if q.assignees:
        a_parts = " OR ".join(
            f"REGEXP_CONTAINS(LOWER(a.name), {_sql_str(re.escape(a.strip().lower()))})"
            for a in q.assignees
        )
        conds.append(f"EXISTS(SELECT 1 FROM UNNEST(assignee_harmonized) AS a WHERE {a_parts})")

    where = "\n  AND ".join(conds)
    cost_note = (
        "-- COST NOTE: text columns are large and the table is not clustered on them;\n"
        "-- every run scans the selected columns. search_claims=true adds the (much\n"
        "-- larger) claims column. Sandbox free tier = 1 TB processed/month.\n"
    )
    header = (
        f"-- Search brief: {q.name}\n"
        + (f"-- Purpose: {q.purpose}\n" if q.purpose else "")
        + "-- Generated by patentkit.search (paste into the BigQuery console as-is,\n"
        "-- then Save results -> JSON and feed it to scripts/search_patents.py).\n"
        + cost_note
    )
    return (
        header
        + "SELECT\n"
        "  publication_number,\n"
        "  family_id,\n"
        "  country_code,\n"
        "  publication_date,\n"
        "  title_localized,\n"
        "  abstract_localized,\n"
        "  cpc,\n"
        "  assignee_harmonized\n"
        f"FROM {_TABLE}\n"
        f"WHERE {where}\n"
        f"LIMIT {q.limit}\n"
    )
