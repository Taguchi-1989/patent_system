"""Tests for the deterministic pipeline contract + self-check gates.

Two halves:
  1. The happy path — run_selfcheck() over the real sample passes every gate,
     and the backbone is genuinely deterministic (stable fingerprint).
  2. Each gate's FAILURE branch — fed a deliberately broken artifact, the gate
     must report passed=False. A self-check that can never fail is worthless,
     so we prove each gate actually catches its violation.

Run from repo root:
    py -m pytest tests/test_pipeline_contract.py -q
    py tests/test_pipeline_contract.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.analyze.compare import Verdict                       # noqa: E402
from patentkit.connectors import FixtureSource                      # noqa: E402
from patentkit.connectors.base import PatentRecord                  # noqa: E402
from patentkit.normalize import normalize                           # noqa: E402
from patentkit.pipeline import STAGES, StageKind, run_selfcheck     # noqa: E402
from patentkit.pipeline.contract import (                           # noqa: E402
    CROSSCUTTING_GATE_IDS,
    PipelineArtifacts,
    gate_determinism,
    gate_disclaimer,
    gate_evidence_substring,
    gate_no_guess,
    gate_normalize_review,
    gate_provenance,
    gate_snapshot_stable,
    gate_unclear_review,
    run_backbone,
)

# Numbers drawn from samples/public_patent_numbers.csv: 2 fixture hits + flagged
# JP forms (which exercise G-NORMALIZE-REVIEW) + not-found numbers.
SAMPLE_NUMBERS = [
    "US10123456B2",
    "EP1234567B1",
    "特開平10-123456",     # Heisei era — flagged needs_review on normalize
    "特許第4123456号",      # JP grant serial — flagged
    "WO2020/123456A1",     # not in fixtures
]

SAMPLE_SPEC = (
    "Wireless charger. The device includes a transmitter coil and a position "
    "sensor. A controller adjusts the drive signal based on the position sensor "
    "output."
)


def _empty_artifacts(**overrides) -> PipelineArtifacts:
    """A minimal PipelineArtifacts; override only the fields a gate inspects."""
    base = dict(
        raw_numbers=[], normalized=[], records=[], summaries=[],
        target_spec=None, comparisons=None, report_md="", not_found=[],
    )
    base.update(overrides)
    return PipelineArtifacts(**base)


def _fake_comparison(canonical, verdicts):
    return SimpleNamespace(patent_canonical=canonical, verdicts=verdicts)


def _fake_verdict(verdict, evidence_span="", element="an element", needs_review=False):
    return SimpleNamespace(
        verdict=verdict, evidence_span=evidence_span,
        element=element, needs_review=needs_review,
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

def test_selfcheck_sample_passes():
    """The real backbone over the sample passes every gate."""
    report = run_selfcheck(SAMPLE_NUMBERS, target_spec=SAMPLE_SPEC,
                           source_factory=FixtureSource)
    failures = [g.gate for g in report.gates if not g.passed]
    assert report.ok, f"gates failed: {failures}"
    # 7 stage-owned gates + 1 cross-cutting = 8 distinct gates.
    assert len(report.gates) == 8


def test_backbone_is_deterministic():
    """Two independent runs produce byte-identical fingerprints."""
    a = run_backbone(SAMPLE_NUMBERS, SAMPLE_SPEC, source=FixtureSource())
    b = run_backbone(SAMPLE_NUMBERS, SAMPLE_SPEC, source=FixtureSource())
    assert a.fingerprint() == b.fingerprint()
    assert gate_determinism(a.fingerprint(), b.fingerprint()).passed


def test_report_to_dict_is_serializable():
    import json
    report = run_selfcheck(SAMPLE_NUMBERS, target_spec=SAMPLE_SPEC,
                           source_factory=FixtureSource)
    d = report.to_dict()
    assert d["ok"] is True
    assert d["fingerprint"] == report.fingerprint
    json.dumps(d, ensure_ascii=False)  # must not raise


def test_stage_contract_shape():
    """The shape is well-formed: ids unique, exactly one agent seam, gates resolve."""
    ids = [s.id for s in STAGES]
    assert ids == ["ingest", "retrieve", "summarize", "compare", "snapshot", "present"]
    assert len(set(ids)) == len(ids)

    agent_stages = [s for s in STAGES if s.kind is StageKind.AGENT]
    assert len(agent_stages) == 1
    assert agent_stages[0].id == "compare"
    assert agent_stages[0].agent_seam  # the seam is documented

    # Every gate id referenced by a stage is a real gate exercised by run_selfcheck.
    known = {
        "G-NORMALIZE-REVIEW", "G-PROVENANCE", "G-NO-GUESS",
        "G-EVIDENCE-SUBSTRING", "G-UNCLEAR-REVIEW", "G-DISCLAIMER",
        "G-SNAPSHOT-STABLE",
    }
    referenced = {gid for s in STAGES for gid in s.gate_ids}
    assert referenced == known
    assert CROSSCUTTING_GATE_IDS == ("G-DETERMINISM",)


# ---------------------------------------------------------------------------
# 2. Each gate catches its violation
# ---------------------------------------------------------------------------

def test_gate_normalize_review_passes_on_real_input():
    normalized = [normalize(n) for n in SAMPLE_NUMBERS]
    assert gate_normalize_review(_empty_artifacts(normalized=normalized)).passed


def test_gate_normalize_review_detects_dropped_flag():
    """A high-uncertainty number whose needs_review was forced off must fail."""
    bad = SimpleNamespace(raw="x", canonical="JP-1-A", confidence=0.5,
                          needs_review=False, office=SimpleNamespace(value="JP"))
    res = gate_normalize_review(_empty_artifacts(normalized=[bad]))
    assert not res.passed


def test_gate_provenance_detects_missing_source():
    good = PatentRecord(canonical="US-1", office="US", number="1", source="fixture")
    bad = PatentRecord(canonical="US-2", office="US", number="2", source="")
    assert gate_provenance(_empty_artifacts(records=[good])).passed
    assert not gate_provenance(_empty_artifacts(records=[good, bad])).passed


def test_gate_no_guess_detects_fabricated_match():
    fabricated = _fake_comparison("US-1", [_fake_verdict(Verdict.MATCH, evidence_span="")])
    grounded = _fake_comparison("US-1", [_fake_verdict(Verdict.MATCH, evidence_span="coil")])
    assert not gate_no_guess(_empty_artifacts(comparisons=[fabricated])).passed
    assert gate_no_guess(_empty_artifacts(comparisons=[grounded])).passed


def test_gate_evidence_substring_detects_fabricated_span():
    spec = "the device includes a transmitter coil"
    real = _fake_comparison("US-1", [_fake_verdict(Verdict.MATCH, evidence_span="transmitter coil")])
    fake = _fake_comparison("US-1", [_fake_verdict(Verdict.MATCH, evidence_span="a flux capacitor")])
    assert gate_evidence_substring(
        _empty_artifacts(target_spec=spec, comparisons=[real])).passed
    assert not gate_evidence_substring(
        _empty_artifacts(target_spec=spec, comparisons=[fake])).passed


def test_gate_evidence_substring_skips_without_spec():
    res = gate_evidence_substring(_empty_artifacts(target_spec=None))
    assert res.passed and res.checked == 0


def test_gate_unclear_review_detects_unescalated():
    escalated = _fake_comparison("US-1", [_fake_verdict(Verdict.UNCLEAR, needs_review=True)])
    silent = _fake_comparison("US-1", [_fake_verdict(Verdict.UNCLEAR, needs_review=False)])
    assert gate_unclear_review(_empty_artifacts(comparisons=[escalated])).passed
    assert not gate_unclear_review(_empty_artifacts(comparisons=[silent])).passed


def test_gate_disclaimer_detects_missing():
    from patentkit.export.markdown import DISCLAIMER
    assert gate_disclaimer(_empty_artifacts(report_md=DISCLAIMER + "\n...")).passed
    assert not gate_disclaimer(_empty_artifacts(report_md="no disclaimer here")).passed


def test_gate_snapshot_stable():
    assert gate_snapshot_stable().passed


# ---------------------------------------------------------------------------
# __main__ runner (run without pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"ok   {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
