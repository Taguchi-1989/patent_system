"""Unit tests for the subscription-agent judging path (analyze/agent_judge.py).

The agent (Claude Code / Copilot) fills a worksheet; AgentJudge reads it back.
No API key, no network. P-NO-GUESS is the SAME guard as the API path
(compare.coerce_verdict): a non-verbatim quote is discarded -> MATCH demoted.

Run from repo root:
    py -m pytest tests/test_agent_judge.py -q
    py tests/test_agent_judge.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.analyze.agent_judge import (              # noqa: E402
    AgentJudge,
    build_worksheet,
    make_agent_judge_from_file,
    write_worksheet,
)
from patentkit.analyze.compare import Verdict             # noqa: E402
from patentkit.analyze.score import build_channels, score_patent  # noqa: E402
from patentkit.analyze.summarize import ClaimBreakdown, PatentSummary  # noqa: E402


SPEC = (
    "The device includes a transmitter coil operating at resonance.\n"
    "A position sensor detects the receiver position with high accuracy.\n"
)
_ELS = ["a transmitter coil", "a flux capacitor"]


def _summary() -> PatentSummary:
    claim = "An apparatus comprising: " + "; ".join(_ELS) + "."
    return PatentSummary(
        canonical="US-1-A1", title="t", one_line="o", claim_count=1,
        independent_claim=claim,
        breakdown=ClaimBreakdown(claim_no=1, text=claim, elements=_ELS),
        source="fixture", source_url=None,
    )


def test_worksheet_shape():
    sheet = build_worksheet(SPEC, [_summary()])
    assert "instructions" in sheet and "target_spec" in sheet
    assert sheet["target_spec"] == SPEC
    assert len(sheet["elements"]) == len(_ELS)
    for e in sheet["elements"]:
        assert set(e) >= {"canonical", "element", "verdict", "evidence_span", "confidence", "rationale"}


def test_agent_verdict_match_verbatim():
    judge = AgentJudge({
        "a transmitter coil": {
            "verdict": "MATCH",
            "evidence_span": "a transmitter coil operating at resonance",
            "confidence": 0.9, "rationale": "ok",
        }
    })
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.verdict is Verdict.MATCH
    assert v.evidence_span in SPEC


def test_agent_fabricated_span_discarded():
    judge = AgentJudge({
        "a transmitter coil": {
            "verdict": "MATCH",
            "evidence_span": "a warp core regulating antimatter",   # not in SPEC
            "confidence": 0.99, "rationale": "捏造",
        }
    })
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.evidence_span == ""
    assert v.verdict is Verdict.UNCLEAR
    assert v.needs_review is True


def test_agent_unfilled_element_is_unclear():
    judge = AgentJudge({})   # nothing filled
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.verdict is Verdict.UNCLEAR
    assert v.needs_review is True
    assert "未入力" in v.rationale


def test_agent_plugs_into_scoring_channels():
    judge = AgentJudge({
        "a transmitter coil": {
            "verdict": "MATCH",
            "evidence_span": "a transmitter coil operating at resonance",
            "confidence": 0.9, "rationale": "ok",
        },
        # 'a flux capacitor' intentionally left unfilled -> UNCLEAR
    })
    channels = build_channels(judge)
    assert set(channels) == {"strict", "recall"}
    score = score_patent(SPEC, _summary(), channels=channels)
    assert score.n_elements == 2
    assert "recall" in score.elements[0].channels


def test_worksheet_file_roundtrip(tmp_path):
    path = str(tmp_path / "work.json")
    n = write_worksheet(path, SPEC, [_summary()])
    assert n == len(_ELS)
    # Simulate the agent filling one element verbatim.
    data = json.load(open(path, encoding="utf-8"))
    data["elements"][0].update({
        "verdict": "MATCH",
        "evidence_span": "a transmitter coil operating at resonance",
        "confidence": 0.9, "rationale": "filled by agent",
    })
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    judge = make_agent_judge_from_file(path)
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.verdict is Verdict.MATCH and v.evidence_span in SPEC


if __name__ == "__main__":
    fns = [
        (k, v) for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
        and "tmp_path" not in v.__code__.co_varnames
    ]
    passed = 0
    for name, fn in fns:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
        except Exception as e:
            print(f"ERROR {name}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"ok   {name}")
    print(f"\n{passed}/{len(fns)} passed (tmp_path test runs under pytest only)")
    sys.exit(0 if passed == len(fns) else 1)
