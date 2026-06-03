"""SnapshotStore: persist PatentRecord snapshots as content-hashed JSON files.

On-disk layout:
    {store_directory}/{canonical}/{fetched_at}_{content_hash[:12]}.json

Each patent gets its own sub-directory named by canonical number.
Each snapshot is one JSON file named with ISO timestamp (colons replaced by
hyphens for Windows path safety) plus the first 12 hex chars of the content
hash for human readability.  Filenames sort lexicographically = chronologically.

CRITICAL: change detection is CONTENT-HASH based, not timestamp based.
Two saves of identical content => SaveResult(changed=False).
Timestamps are stored for history ordering only.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..connectors.base import PatentRecord

# ---------------------------------------------------------------------------
# Fields that are MEANINGFUL to a patent's content and therefore included in
# the content hash.  Volatile/noise fields are excluded:
#   - raw         : bulky original payload; irrelevant to content change
#   - source      : provider name can change without changing the patent
#   - source_url  : same as above
#   - notes       : internal processing annotations, not patent content
#
# NOTE: if new content fields are added to PatentRecord in future milestones
# (e.g. citations when INPADOC data becomes available), update this tuple AND
# the diff classification in state/diff.py simultaneously.
# ---------------------------------------------------------------------------
_HASHED_FIELDS: tuple[str, ...] = (
    "abstract",
    "assignee",
    "canonical",
    "claims",
    "family_id",
    "legal_status",
    "number",
    "office",
    "pub_date",
    "title",
)


def _content_hash(record_dict: dict) -> str:
    """Return SHA-256 hex digest of the meaningful patent content fields.

    The serialisation is deterministic: sort_keys=True + compact separators
    + ensure_ascii=False ensures the same bytes on every machine and run.
    """
    filtered = {k: record_dict.get(k) for k in sorted(_HASHED_FIELDS)}
    payload = json.dumps(filtered, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class Snapshot:
    """An immutable point-in-time record of a patent's state."""

    canonical: str
    fetched_at: str        # ISO-8601 UTC; used for ordering only
    content_hash: str      # SHA-256 hex (64 chars); used for equality
    record: dict           # full PatentRecord as a plain dict


@dataclass
class SaveResult:
    """Result of a save() call."""

    changed: bool               # True if content_hash differs from latest
    is_new: bool                # True if no prior snapshot existed
    snapshot: Snapshot          # the current snapshot (existing if unchanged)
    previous: Snapshot | None   # the previous latest snapshot, or None


class SnapshotStore:
    """File-backed store that persists PatentRecord snapshots as JSON.

    Parameters
    ----------
    directory:
        Root directory for all snapshot files.  Created on first use.
    """

    def __init__(self, directory: str | os.PathLike) -> None:
        self.directory = Path(directory)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, record: "PatentRecord") -> SaveResult:
        """Persist a snapshot if content changed; otherwise return existing.

        Identical content (same hash) does NOT write a new file, keeping the
        store clean and making unchanged detection O(1).
        """
        record_dict = self._to_dict(record)
        canonical = record_dict.get("canonical", "")
        new_hash = _content_hash(record_dict)

        prior = self.latest(canonical)

        if prior is not None and prior.content_hash == new_hash:
            # Content is identical — return existing snapshot unchanged.
            return SaveResult(
                changed=False,
                is_new=False,
                snapshot=prior,
                previous=prior,
            )

        # Write new snapshot file.
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
        filename = f"{fetched_at}_{new_hash[:12]}.json"

        patent_dir = self.directory / self._safe_dirname(canonical)
        patent_dir.mkdir(parents=True, exist_ok=True)

        snapshot = Snapshot(
            canonical=canonical,
            fetched_at=fetched_at,
            content_hash=new_hash,
            record=record_dict,
        )
        snap_path = patent_dir / filename
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "canonical": snapshot.canonical,
                    "fetched_at": snapshot.fetched_at,
                    "content_hash": snapshot.content_hash,
                    "record": snapshot.record,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        return SaveResult(
            changed=(prior is not None),   # changed only if there WAS a prior
            is_new=(prior is None),
            snapshot=snapshot,
            previous=prior,
        )

    def latest(self, canonical: str) -> Snapshot | None:
        """Return the most recent snapshot for *canonical*, or None."""
        files = self._sorted_files(canonical)
        if not files:
            return None
        return self._load(files[-1])

    def history(self, canonical: str) -> list[Snapshot]:
        """Return all snapshots for *canonical* in chronological order (oldest first)."""
        return [self._load(f) for f in self._sorted_files(canonical)]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_dict(record: "PatentRecord") -> dict:
        """Convert a PatentRecord to a plain dict.

        Works for both dataclass instances and plain dicts (for testability).
        """
        if isinstance(record, dict):
            return record
        try:
            from dataclasses import asdict as _asdict
            return _asdict(record)
        except TypeError:
            # Fallback: convert via __dict__ if it's not a proper dataclass.
            return vars(record)

    @staticmethod
    def _safe_dirname(canonical: str) -> str:
        """Replace characters that are invalid in Windows directory names."""
        # Forward/backward slashes, colons, and wildcards are problematic.
        return canonical.replace("/", "_").replace("\\", "_").replace(":", "_")

    def _sorted_files(self, canonical: str) -> list[Path]:
        """Return snapshot files for *canonical* sorted chronologically."""
        patent_dir = self.directory / self._safe_dirname(canonical)
        if not patent_dir.exists():
            return []
        return sorted(patent_dir.glob("*.json"))

    @staticmethod
    def _load(path: Path) -> Snapshot:
        """Load a Snapshot from a JSON file."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return Snapshot(
            canonical=data["canonical"],
            fetched_at=data["fetched_at"],
            content_hash=data["content_hash"],
            record=data["record"],
        )
