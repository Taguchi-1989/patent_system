"""BigQuery (Google Patents Public Data) source — the primary KEYLESS route.

Two access modes share one mapper (`record_from_bq_row`):
  - BigQueryExportSource : reads rows exported from the BigQuery *console* as
    JSON. ZERO install, only a Google login. Best first step.
  - BigQuerySource       : live query via google-cloud-bigquery (lazy import).
    Needs `gcloud auth application-default login` (free, no card). For automation.

Dataset: `patents-public-data.patents.publications`. Its `publication_number`
(e.g. "US-10123456-B2") matches patentkit's canonical number format directly.

Honest limits: this dataset has no legal-status field (deferred to INPADOC/OPS),
and the claims arrive as a single text blob that we split heuristically (flagged).
"""

from __future__ import annotations

import json
import os
import re

from ..normalize import CanonicalNumber
from .base import PatentRecord

_GP_URL = "https://patents.google.com/patent/{pub}"
_DEFAULT_SQL = os.path.join(os.path.dirname(__file__), "..", "..", "..", "sql", "publications_by_number.sql")


def _pick_localized(arr, prefer=("en",)) -> str:
    """patents.publications localizes text as [{text, language, ...}]."""
    if not arr:
        return ""
    for lang in prefer:
        for item in arr:
            if (item.get("language") or "").lower() == lang:
                return item.get("text") or ""
    return arr[0].get("text") or ""


def _split_claims(text: str) -> list[str]:
    """Best-effort split of a single claims blob into individual claims.

    Heuristic: a claim starts at a line that begins with "<number>.". Flagged as
    heuristic by the caller (P-NO-GUESS) so the semantic step verifies boundaries.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?m)^\s*(?=\d{1,3}\s*\.)", text) if p.strip()]
    return parts if len(parts) > 1 else [text]


def _fmt_date(d) -> str | None:
    """patents.publications dates are INT64 yyyymmdd; normalize to YYYY-MM-DD."""
    if d in (None, "", 0, "0"):
        return None
    s = str(d)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def record_from_bq_row(row: dict) -> PatentRecord:
    """Map one BigQuery `patents.publications` row to a PatentRecord."""
    pub = row.get("publication_number") or ""
    segs = pub.split("-")
    office = segs[0] if segs and segs[0] else (row.get("country_code") or "")
    number = segs[1] if len(segs) > 1 else ""

    claims = _split_claims(_pick_localized(row.get("claims_localized")))
    assignees = row.get("assignee_harmonized") or []
    assignee = assignees[0].get("name") if assignees and isinstance(assignees[0], dict) else None

    notes: list[str] = []
    if len(claims) > 1:
        notes.append("claims split heuristically from a single text blob; verify boundaries")
    notes.append("legal status not present in patents.publications (use --legal fixture|ops to enrich)")
    if office == "JP":
        notes.append(
            "JP原文・経過情報は J-PlatPat（https://www.j-platpat.inpit.go.jp/）の"
            f"番号検索で「{number}」を確認（深いリンクは不安定なため番号で案内）"
        )

    return PatentRecord(
        canonical=pub,
        office=office,
        number=number,
        title=_pick_localized(row.get("title_localized")),
        abstract=_pick_localized(row.get("abstract_localized")),
        claims=claims,
        assignee=assignee,
        pub_date=_fmt_date(row.get("publication_date")),
        legal_status=None,
        family_id=row.get("family_id"),
        source="Google Patents Public Data (BigQuery: patents.publications)",
        source_url=_GP_URL.format(pub=pub) if pub else None,
        notes=notes,
        raw=row,
    )


class BigQueryExportSource:
    """Reads rows exported from the BigQuery console (JSON array or NDJSON)."""

    name = "bq-export"

    def __init__(self, path: str):
        self.path = path
        self._by_pub: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return
        rows = json.loads(content) if content.startswith("[") else [
            json.loads(line) for line in content.splitlines() if line.strip()
        ]
        for r in rows:
            pub = r.get("publication_number")
            if pub:
                self._by_pub[pub] = r

    def fetch(self, number: CanonicalNumber) -> PatentRecord | None:
        row = self._by_pub.get(number.canonical)
        return record_from_bq_row(row) if row is not None else None


class BigQuerySource:
    """Live BigQuery query. Batches all numbers into ONE query via prefetch()."""

    name = "bq"

    def __init__(self, project: str | None = None):
        self.project = project or os.environ.get("GCP_PROJECT_ID")
        self._cache: dict[str, dict] = {}

    def _client(self):
        try:
            from google.cloud import bigquery  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "google-cloud-bigquery not installed. Either `pip install google-cloud-bigquery` "
                "and run `gcloud auth application-default login`, or use the zero-install path: "
                "BigQueryExportSource (run sql/publications_by_number.sql in the console and export JSON)."
            ) from e
        return bigquery

    def prefetch(self, canonicals: list[str]) -> None:
        """Run one query for all numbers; cache rows by publication_number."""
        if not canonicals:
            return
        bigquery = self._client()
        client = bigquery.Client(project=self.project)
        query = (
            "SELECT publication_number, country_code, kind_code, application_number, "
            "publication_date, filing_date, grant_date, family_id, title_localized, "
            "abstract_localized, claims_localized, assignee_harmonized "
            "FROM `patents-public-data.patents.publications` "
            "WHERE publication_number IN UNNEST(@numbers)"
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("numbers", "STRING", canonicals)]
        )
        for row in client.query(query, job_config=job_config).result():
            d = dict(row)
            if d.get("publication_number"):
                self._cache[d["publication_number"]] = d

    def fetch(self, number: CanonicalNumber) -> PatentRecord | None:
        if number.canonical not in self._cache:
            self.prefetch([number.canonical])
        row = self._cache.get(number.canonical)
        return record_from_bq_row(row) if row is not None else None
