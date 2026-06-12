"""Discovery layer (M8): find candidate patent numbers from a search brief.

Turns a query spec (keywords x CPC x assignee x period) into:
  - a BigQuery SQL string for the zero-install console route, and
  - a deterministic local ranking of exported/live rows into a candidate list
    that feeds the existing pipeline (number,note CSV).

Same KEYLESS-first split as the Retrieval layer: SQL-in-console + export JSON
needs no install; the live route reuses google-cloud-bigquery lazily.
"""

from .query import SearchQuery, build_search_sql, load_query_spec
from .rank import Candidate, dedupe_by_family, rank_rows
from .semantic import apply_semantic, make_embedder_from_env, semantic_scores

__all__ = [
    "SearchQuery",
    "build_search_sql",
    "load_query_spec",
    "Candidate",
    "rank_rows",
    "dedupe_by_family",
    "apply_semantic",
    "semantic_scores",
    "make_embedder_from_env",
]
