"""Pipeline contract layer — pins the deterministic pipeline shape and makes it
self-validating.

Public exports:
    STAGES            — the ordered, declarative pipeline shape (the "形").
    StageKind         — DETERMINISTIC vs AGENT classification.
    run_selfcheck     — run the deterministic backbone and assert every gate.
    run_backbone      — run the backbone only (no gates), returning artifacts.
    PipelineReport    — the machine-readable self-check result.
    PipelineArtifacts — what one backbone run produces.

See contract.py for the determinism boundary and the gate catalogue.
"""

from __future__ import annotations

from .contract import (
    CROSSCUTTING_GATE_IDS,
    GateResult,
    PipelineArtifacts,
    PipelineReport,
    STAGES,
    Stage,
    StageKind,
    StageReport,
    run_backbone,
    run_selfcheck,
)

__all__ = [
    "STAGES",
    "Stage",
    "StageKind",
    "StageReport",
    "GateResult",
    "PipelineArtifacts",
    "PipelineReport",
    "CROSSCUTTING_GATE_IDS",
    "run_backbone",
    "run_selfcheck",
]
