"""Field-level diff engine for PatentRecord snapshots (requirements §6.4).

Monitors the fields that matter for patent status tracking:
  - legal_status     : simple equality — if it changes, alert
  - claims           : positional diff — detect added/removed/changed claims
  - family_id        : simple equality — family reassignment is significant

Generic fields (title, abstract, assignee, pub_date, office, number) are also
diffed as "changed" when they differ.

NOT YET TRACKED (requirements §6.4 mentions these but they are not present in
the current PatentRecord model — do not invent data):
  - citations / 引用         : requires INPADOC/OPS citation data, not yet integrated
  - prosecution documents / 追加書類 : requires USPTO PAIR / PATENTSCOPE, not yet integrated
  - continuation applications / 継続出願 : requires USPTO family graph, not yet integrated

These will be added in a future milestone when the corresponding data sources
are available.  See NOT_YET_TRACKED below for the importable constant.

NOTE: if PatentRecord gains new content fields (e.g. citations), update:
  1. state/store.py  _HASHED_FIELDS tuple
  2. diff.py         _SPECIAL_FIELDS and _GENERIC_FIELDS sets below
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# NOT_YET_TRACKED — honest §6.4 disclosure (P-NO-GUESS)
# ---------------------------------------------------------------------------
NOT_YET_TRACKED: list[str] = [
    "citations (引用): requires INPADOC/OPS citation-linkage data, not yet integrated in PatentRecord",
    "prosecution documents (追加書類): requires USPTO PAIR / PATENTSCOPE examination history, not yet integrated",
    "continuation applications (継続出願): requires USPTO continuation-family graph, not yet integrated",
]

# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------

# Fields with custom diff logic.
_SPECIAL_FIELDS = {"legal_status", "claims", "family_id"}

# Scalar fields diffed with simple equality.
_GENERIC_FIELDS = {"title", "abstract", "assignee", "pub_date", "office", "number"}

# Fields intentionally excluded from diff (noise / provenance).
# raw, source, source_url, notes, canonical are not patent content.
_EXCLUDED_FIELDS = {"raw", "source", "source_url", "notes", "canonical"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FieldChange:
    """A single field-level change between two snapshots.

    Attributes
    ----------
    field:
        Field name, e.g. ``"legal_status"`` or ``"claims[2]"``.
    before:
        Previous value (None for kind="added").
    after:
        New value (None for kind="removed").
    kind:
        One of ``"changed"``, ``"added"``, ``"removed"``.
    """

    field: str
    before: Any
    after: Any
    kind: str  # "changed" | "added" | "removed"


@dataclass
class RecordDiff:
    """All field-level changes between two versions of a patent record.

    Attributes
    ----------
    canonical:
        The patent canonical number this diff relates to.
    changes:
        List of individual field changes (may be empty if nothing changed).
    """

    canonical: str
    changes: list[FieldChange] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True if there is at least one change."""
        return len(self.changes) > 0

    def by_field(self) -> dict[str, list[FieldChange]]:
        """Group changes by base field name.

        ``claims[0]``, ``claims[1]``, etc. are all grouped under ``"claims"``.
        """
        groups: dict[str, list[FieldChange]] = {}
        for change in self.changes:
            base = change.field.split("[")[0]
            groups.setdefault(base, []).append(change)
        return groups


# ---------------------------------------------------------------------------
# Main diff function
# ---------------------------------------------------------------------------

def diff_records(old: dict, new: dict) -> RecordDiff:
    """Compute a field-level diff between two PatentRecord dicts.

    Parameters
    ----------
    old:
        Dict representation of the previous PatentRecord snapshot.
    new:
        Dict representation of the current PatentRecord snapshot.

    Returns
    -------
    RecordDiff
        Contains all FieldChange entries.  ``RecordDiff.changed`` is False
        when the records are semantically identical (same content fields).
    """
    canonical = new.get("canonical") or old.get("canonical") or ""
    changes: list[FieldChange] = []

    # --- legal_status: simple equality ---
    changes.extend(_diff_scalar("legal_status", old, new))

    # --- claims: positional index-based diff ---
    # Conservative / honest: we compare by position. An inserted claim in
    # the middle shows as multiple changes rather than one insertion — this
    # avoids false-positive matching (P-NO-GUESS).
    old_claims: list[str] = old.get("claims") or []
    new_claims: list[str] = new.get("claims") or []
    max_len = max(len(old_claims), len(new_claims))
    for i in range(max_len):
        if i < len(old_claims) and i < len(new_claims):
            if old_claims[i] != new_claims[i]:
                changes.append(FieldChange(
                    field=f"claims[{i}]",
                    before=old_claims[i],
                    after=new_claims[i],
                    kind="changed",
                ))
        elif i >= len(old_claims):
            # New claim appended
            changes.append(FieldChange(
                field=f"claims[{i}]",
                before=None,
                after=new_claims[i],
                kind="added",
            ))
        else:
            # Old claim removed
            changes.append(FieldChange(
                field=f"claims[{i}]",
                before=old_claims[i],
                after=None,
                kind="removed",
            ))

    # --- family_id: simple equality ---
    changes.extend(_diff_scalar("family_id", old, new))

    # --- generic scalar fields ---
    for f_name in sorted(_GENERIC_FIELDS):
        changes.extend(_diff_scalar(f_name, old, new))

    return RecordDiff(canonical=canonical, changes=changes)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _diff_scalar(field_name: str, old: dict, new: dict) -> list[FieldChange]:
    """Diff a single scalar field between old and new dicts."""
    old_val = old.get(field_name)
    new_val = new.get(field_name)
    if old_val != new_val:
        return [FieldChange(field=field_name, before=old_val, after=new_val, kind="changed")]
    return []
