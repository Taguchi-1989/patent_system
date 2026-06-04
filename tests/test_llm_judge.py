"""Unit tests for src/patentkit/analyze/llm_judge.py (Azure OpenAI brain).

No network, no `openai` install required: we inject a FAKE client whose
chat.completions.create() returns canned JSON. This lets us test the parsing
and — most importantly — the P-NO-GUESS guard (a fabricated quote that is NOT a
verbatim substring of the spec must be discarded, demoting MATCH to UNCLEAR).

Run from repo root:
    py -m pytest tests/test_llm_judge.py -q
    py tests/test_llm_judge.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from patentkit.analyze.compare import Verdict                       # noqa: E402
from patentkit.analyze.llm_judge import (                           # noqa: E402
    AzureOpenAIJudge,
    GitHubModelsJudge,
    make_azure_judge_from_env,
    make_github_models_judge_from_env,
    make_llm_judge_from_env,
)
from patentkit.analyze.score import build_channels, score_patent    # noqa: E402
from patentkit.analyze.summarize import ClaimBreakdown, PatentSummary  # noqa: E402


SPEC = (
    "The device includes a transmitter coil operating at resonance.\n"
    "A position sensor detects the receiver position with high accuracy.\n"
)


# --------------------------------------------------------------------------
# Fake Azure client: chat.completions.create() returns a canned content string.
# --------------------------------------------------------------------------

class _Msg:
    def __init__(self, content): self.content = content

class _Choice:
    def __init__(self, content): self.message = _Msg(content)

class _Resp:
    def __init__(self, content): self.choices = [_Choice(content)]

class _FakeCompletions:
    def __init__(self, payloads): self._payloads = payloads; self.calls = 0
    def create(self, **kwargs):
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        return _Resp(payload)

class _FakeChat:
    def __init__(self, payloads): self.completions = _FakeCompletions(payloads)

class FakeClient:
    """Mimics the openai.AzureOpenAI client surface used by AzureOpenAIJudge."""
    def __init__(self, payloads): self.chat = _FakeChat(payloads)


def _judge_with(payload: dict) -> AzureOpenAIJudge:
    return AzureOpenAIJudge(deployment="test", client=FakeClient([json.dumps(payload)]))


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_match_with_verbatim_span():
    span = "a transmitter coil operating at resonance"
    judge = _judge_with({
        "verdict": "MATCH", "evidence_span": span,
        "confidence": 0.9, "rationale": "コイルが一致",
    })
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.verdict is Verdict.MATCH
    assert v.evidence_span == span
    assert v.evidence_span in SPEC          # verbatim
    assert 0.0 <= v.confidence <= 1.0


def test_fabricated_span_is_discarded_and_demoted():
    """A quote NOT present in the spec must be dropped -> MATCH demoted to UNCLEAR."""
    judge = _judge_with({
        "verdict": "MATCH",
        "evidence_span": "a quantum flux capacitor regulating the warp core",  # not in spec
        "confidence": 0.95, "rationale": "捏造",
    })
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.evidence_span == ""            # fabricated quote discarded
    assert v.verdict is Verdict.UNCLEAR     # __post_init__ demotes ungrounded MATCH
    assert v.needs_review is True
    assert "捏造引用を破棄" in v.rationale


def test_missing_has_zero_coverage_semantics():
    judge = _judge_with({
        "verdict": "MISSING", "evidence_span": "",
        "confidence": 0.0, "rationale": "記載なし",
    })
    v = judge.judge("a flux capacitor", SPEC, "")
    assert v.verdict is Verdict.MISSING
    assert v.evidence_span == ""


def test_network_error_escalates_not_fabricates():
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs): raise RuntimeError("boom")
    judge = AzureOpenAIJudge(deployment="test", client=_Boom())
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.verdict is Verdict.UNCLEAR
    assert v.evidence_span == ""
    assert v.needs_review is True


def test_make_from_env_returns_none_when_unconfigured(monkeypatch):
    for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(k, raising=False)
    assert make_azure_judge_from_env(load_dotenv=False) is None


def test_github_judge_with_injected_client():
    """GitHub Models path: same logic, OpenAI-compatible client injected."""
    judge = GitHubModelsJudge(
        model="openai/gpt-4o-mini",
        client=FakeClient([json.dumps({
            "verdict": "MATCH",
            "evidence_span": "a transmitter coil operating at resonance",
            "confidence": 0.88, "rationale": "ok",
        })]),
    )
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.verdict is Verdict.MATCH
    assert v.evidence_span in SPEC
    assert judge.model == "openai/gpt-4o-mini"


def test_github_from_env_none_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_MODELS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert make_github_models_judge_from_env(load_dotenv=False) is None


def test_provider_dispatch_none_and_unconfigured(monkeypatch):
    for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
              "GITHUB_MODELS_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert make_llm_judge_from_env("none", load_dotenv=False) is None
    assert make_llm_judge_from_env("auto", load_dotenv=False) is None
    assert make_llm_judge_from_env("azure", load_dotenv=False) is None
    assert make_llm_judge_from_env("github", load_dotenv=False) is None


def test_json_mode_retry_when_provider_rejects_response_format():
    """If a provider rejects response_format, _create() retries without it."""
    payload = json.dumps({
        "verdict": "MATCH",
        "evidence_span": "a transmitter coil operating at resonance",
        "confidence": 0.8, "rationale": "ok",
    })

    class _PickyCompletions:
        def __init__(self): self.calls = 0
        def create(self, **kwargs):
            self.calls += 1
            if "response_format" in kwargs:
                raise TypeError("this model does not support response_format")
            return _Resp(payload)

    class _PickyChat:
        def __init__(self): self.completions = _PickyCompletions()

    class _PickyClient:
        def __init__(self): self.chat = _PickyChat()

    client = _PickyClient()
    judge = GitHubModelsJudge(model="openai/gpt-4o-mini", client=client)
    v = judge.judge("a transmitter coil", SPEC, "")
    assert v.verdict is Verdict.MATCH
    assert client.chat.completions.calls == 2     # first w/ json mode failed, retried plain


def test_plugs_into_scoring_channels():
    """The Azure judge drops into build_channels() as the 'recall' (LLM) channel."""
    judge = _judge_with({
        "verdict": "MATCH",
        "evidence_span": "a transmitter coil operating at resonance",
        "confidence": 0.9, "rationale": "ok",
    })
    summary = PatentSummary(
        canonical="US-1-A1", title="t", one_line="o", claim_count=1,
        independent_claim="a transmitter coil",
        breakdown=ClaimBreakdown(claim_no=1, text="a transmitter coil",
                                 elements=["a transmitter coil"]),
        source="fixture", source_url=None,
    )
    channels = build_channels(judge)
    assert set(channels) == {"strict", "recall"}
    score = score_patent(SPEC, summary, channels=channels)
    assert score.n_elements == 1
    # recall channel value comes from the (fake) LLM judge.
    assert "recall" in score.elements[0].channels


if __name__ == "__main__":
    # Minimal pytest-free runner (skips the monkeypatch test, which needs pytest).
    fns = [
        (k, v) for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
        and "monkeypatch" not in v.__code__.co_varnames
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
    print(f"\n{passed}/{len(fns)} passed (monkeypatch test runs under pytest only)")
    sys.exit(0 if passed == len(fns) else 1)
