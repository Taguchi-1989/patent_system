"""Deterministic local ranking of search rows -> candidate list.

This is the discovery counterpart of the scoring strict channel: reproducible
keyword-anchor scoring, verbatim evidence snippets (machine-checked substrings
of the source text), and P-NO-GUESS flags instead of silent guesses.

Rows are BigQuery `patents.publications` rows (live or console-export JSON) —
the same shape `connectors.bigquery` consumes, so one mental model.

Scoring (per row):
  - each keyword GROUP that hits the title  -> +3 (title is the densest signal)
  - each keyword GROUP that hits only the abstract -> +1
  - each CPC prefix hit -> +2
  - groups_hit < groups_total -> needs_review (SQL should enforce all groups;
    a partial row means the source filter and this ranker disagree — flag it)

Family dedupe keeps the best-scoring member and records what was collapsed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .query import SearchQuery

_SNIPPET = 60  # chars of context on each side of a match


@dataclass
class Candidate:
    """One ranked discovery hit, with verbatim evidence."""

    publication_number: str
    score: int
    title: str = ""
    country: str = ""
    pub_date: str | None = None
    family_id: str | None = None
    groups_hit: int = 0
    groups_total: int = 0
    matched_terms: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)   # verbatim snippets
    needs_review: bool = False
    notes: list[str] = field(default_factory=list)


def _texts(arr) -> list[str]:
    return [item.get("text") or "" for item in (arr or []) if isinstance(item, dict)]


def _snippet(text: str, idx: int, length: int) -> str:
    start = max(0, idx - _SNIPPET)
    end = min(len(text), idx + length + _SNIPPET)
    return text[start:end].strip()


def _find_term(texts: list[str], terms: list[str]) -> tuple[str, str] | None:
    """First (term, verbatim snippet) hit of any term in any text, else None."""
    for text in texts:
        low = text.lower()
        for term in terms:
            idx = low.find(term.strip().lower())
            if idx >= 0:
                return term, _snippet(text, idx, len(term))
    return None


def _fmt_date(d) -> str | None:
    if d in (None, "", 0, "0"):
        return None
    s = str(d)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def score_row(row: dict, q: SearchQuery) -> Candidate:
    """Score one publications row against the search brief (pure, reproducible)."""
    titles = _texts(row.get("title_localized"))
    abstracts = _texts(row.get("abstract_localized"))
    score = 0
    groups_hit = 0
    matched: list[str] = []
    evidence: list[str] = []
    notes: list[str] = []

    for group in q.keywords:
        hit = _find_term(titles, group)
        if hit:
            score += 3
            groups_hit += 1
            matched.append(hit[0])
            evidence.append(f"title: “{hit[1]}”")
            continue
        hit = _find_term(abstracts, group)
        if hit:
            score += 1
            groups_hit += 1
            matched.append(hit[0])
            evidence.append(f"abstract: “{hit[1]}”")

    codes = [c.get("code") or "" for c in (row.get("cpc") or []) if isinstance(c, dict)]
    for prefix in q.cpc:
        hits = sorted({c for c in codes if c.upper().startswith(prefix.upper())})
        if hits:
            score += 2
            evidence.append(f"cpc: {hits[0]}")

    needs_review = False
    if q.keywords and groups_hit < len(q.keywords):
        needs_review = True
        notes.append(
            f"only {groups_hit}/{len(q.keywords)} keyword groups found in title/abstract "
            "(may match in claims only) — verify before triage"
        )

    return Candidate(
        publication_number=row.get("publication_number") or "",
        score=score,
        title=titles[0] if titles else "",
        country=row.get("country_code") or "",
        pub_date=_fmt_date(row.get("publication_date")),
        family_id=str(row["family_id"]) if row.get("family_id") not in (None, "") else None,
        groups_hit=groups_hit,
        groups_total=len(q.keywords),
        matched_terms=matched,
        evidence=evidence,
        needs_review=needs_review,
        notes=notes,
    )


def dedupe_by_family(cands: list[Candidate]) -> list[Candidate]:
    """Keep the best-scoring member per family; record what was collapsed."""
    by_family: dict[str, Candidate] = {}
    singles: list[Candidate] = []
    collapsed: dict[str, list[str]] = {}
    for c in cands:
        if not c.family_id:
            singles.append(c)
            continue
        cur = by_family.get(c.family_id)
        if cur is None:
            by_family[c.family_id] = c
        else:
            keep, drop = (cur, c) if (cur.score, cur.publication_number) >= (c.score, c.publication_number) else (c, cur)
            by_family[c.family_id] = keep
            collapsed.setdefault(c.family_id, []).append(drop.publication_number)
    for fam, dropped in collapsed.items():
        by_family[fam].notes.append(
            f"family {fam}: collapsed {len(dropped)} sibling(s): {', '.join(sorted(dropped))}"
        )
    return list(by_family.values()) + singles


def rank_rows(rows: list[dict], q: SearchQuery) -> list[Candidate]:
    """Score, family-dedupe, and sort (score desc, then number for stability)."""
    cands = [score_row(r, q) for r in rows if r.get("publication_number")]
    cands = dedupe_by_family(cands)
    cands.sort(key=lambda c: (-c.score, c.publication_number))
    return cands
