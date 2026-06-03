"""Retrieval layer: pluggable patent data sources (Provider pattern).

The pipeline depends only on the `PatentSource` protocol, never on a concrete
source. This is the key design choice that lets the whole system run end-to-end
with ZERO API keys today (via FixtureSource) and light up with real data later
by swapping in a keyless source — no pipeline changes.

Source friction ladder (lowest first):
  FixtureSource   — local sample files; zero deps, zero network. Dev/test/demo.
  BulkDataSource  — USPTO bulk XML download; NO account, NO key. (US grants)
  BigQuerySource  — Google Patents on BigQuery Sandbox; Google login only,
                    no credit card. Worldwide biblio + US full text.
  OdpSource/OpsSource — key-gated APIs; deferred (ODP needs ID.me; OPS needs app reg).
"""

from .base import PatentRecord, PatentSource
from .bigquery import BigQueryExportSource, BigQuerySource, record_from_bq_row
from .bulk_uspto import BulkDataSource, parse_uspto_grant_xml, week_url_for_date
from .fixture import FixtureSource

__all__ = [
    "PatentRecord",
    "PatentSource",
    "FixtureSource",
    "BigQuerySource",
    "BigQueryExportSource",
    "record_from_bq_row",
    "BulkDataSource",
    "parse_uspto_grant_xml",
    "week_url_for_date",
]
