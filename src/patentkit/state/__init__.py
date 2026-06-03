"""State/Snapshot layer — M3.

Public exports:
    Snapshot      — immutable point-in-time record of a patent's state
    SaveResult    — outcome of SnapshotStore.save()
    SnapshotStore — file-backed store with save / latest / history
"""

from __future__ import annotations

from .store import SaveResult, Snapshot, SnapshotStore

__all__ = ["SaveResult", "Snapshot", "SnapshotStore"]
