"""Azure OpenAI を「頭脳」として Judge プロトコルに差し込むLLMチャネル.

WHY THIS EXISTS
---------------
score.py の融合スコアは 2 チャネル設計:
  strict = 決定論アンカー(HeuristicJudge) / recall = 意味チャネル(差し替え可)
本モジュールはその recall チャネルに **Azure OpenAI** を差し込む `AzureOpenAIJudge`
を提供する。「API キーをどこかに刺す」のではなく、既存の `Judge` プロトコル
（analyze/compare.py）を満たす **コード上のシーム**として差し込むので、
`build_channels(make_azure_judge_from_env())` 一発でパイプライン全体が
LLM を頭脳に切り替わる。鍵不要の既定（LenientJudge）はそのまま温存。

設定は環境変数（.env 可、コミットしない）:
    AZURE_OPENAI_ENDPOINT      例: https://<resource>.openai.azure.com
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_DEPLOYMENT    チャットモデルのデプロイ名（Azure では model= に渡す）
    AZURE_OPENAI_API_VERSION   既定: 2024-10-21（リソースが対応する版に合わせる）

P-NO-GUESS の維持（最重要）
---------------------------
LLM は自由に文章を作れるので、ハルシネーション対策を **コード側で強制**する:
  - モデルには「根拠は仕様からの逐語コピーのみ」を指示。
  - 返ってきた evidence_span が target_spec の **実在の部分文字列でなければ空にする**
    （捏造引用を機械的に破棄）。
  - 空の根拠で MATCH は ElementVerdict.__post_init__ が UNCLEAR に降格。
よって LLM を使っても「根拠なき断定」は構造的に出せない。

`openai` は **任意依存**（requirements-llm.txt）。import はこのモジュール内のみ。
テストは client を注入できるので openai 無し・ネット無しで走る。
"""

from __future__ import annotations

import json
import os
import re

from .compare import ElementVerdict, Verdict

_DEFAULT_API_VERSION = "2024-10-21"
_DEFAULT_MAX_SPEC_CHARS = 20000   # cost/context guard; validation still uses full spec

_SYSTEM_PROMPT = (
    "You are a patent freedom-to-operate (FTO) analyst. Decide whether a TARGET "
    "SPECIFICATION discloses/covers a single CLAIM ELEMENT. Be conservative. "
    "Ground every MATCH by quoting a VERBATIM span copied EXACTLY (character for "
    "character) from the specification — never paraphrase, never invent text. "
    "Respond with a single JSON object and nothing else."
)

_USER_TEMPLATE = """CLAIM (context):
{claim_context}

CLAIM ELEMENT to evaluate:
{element}

TARGET SPECIFICATION:
{spec}

Return ONLY this JSON object:
{{
  "verdict": "MATCH" | "MISSING" | "UNCLEAR",
  "evidence_span": "<verbatim substring copied EXACTLY from the specification, or empty string>",
  "confidence": <number 0.0-1.0 = probability the specification covers this element>,
  "rationale": "<one short sentence in Japanese>"
}}

Rules:
- MATCH only if the specification clearly covers the element AND you provide a non-empty verbatim evidence_span.
- MISSING if the element is absent from the specification; evidence_span must be "".
- UNCLEAR if partial/ambiguous.
- evidence_span MUST be an exact substring of the specification (copy-paste), or "".
"""


def _make_azure_client(endpoint: str, api_key: str, api_version: str):
    """Construct an openai.AzureOpenAI client (imports the optional dependency)."""
    try:
        from openai import AzureOpenAI
    except ImportError as exc:  # pragma: no cover - exercised only without openai
        raise RuntimeError(
            "AzureOpenAIJudge requires the 'openai' package. "
            "Install it: pip install -r requirements-llm.txt"
        ) from exc
    return AzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=endpoint,
    )


def _extract_json(text: str) -> dict:
    """Parse the first JSON object out of a model response (tolerates code fences)."""
    if not text:
        return {}
    cleaned = text.strip()
    # Strip ```json ... ``` fences if present.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass
    # Fallback: grab the outermost {...}.
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, TypeError):
            return {}
    return {}


# GitHub Models (OpenAI-compatible) — works with a GitHub token (PAT with
# models:read), which GitHub Copilot subscribers / GitHub users already have.
_GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference"
_DEFAULT_GH_MODEL = "openai/gpt-4o-mini"


def _maybe_dotenv(load: bool) -> None:
    if not load:
        return
    try:
        from dotenv import load_dotenv as _ld
        _ld()
    except Exception:
        pass


def _make_openai_client(base_url: str, api_key: str):
    """Construct a base openai.OpenAI client pointed at any OpenAI-compatible API."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - exercised only without openai
        raise RuntimeError(
            "This LLM channel requires the 'openai' package. "
            "Install it: pip install -r requirements-llm.txt"
        ) from exc
    return OpenAI(base_url=base_url, api_key=api_key)


class OpenAICompatibleJudge:
    """LLM-backed Judge over ANY OpenAI-compatible chat API.

    One implementation serves Azure OpenAI, GitHub Models (Copilot token), and
    vanilla OpenAI — only client construction differs (see subclasses/factories).
    Implements the Judge protocol; drop-in for score.py's "recall" channel.

    P-NO-GUESS is enforced here in code (verbatim-substring validation) AND
    structurally in ElementVerdict.__post_init__, so even a hallucinating model
    cannot emit an ungrounded MATCH.
    """

    def __init__(
        self,
        *,
        client,
        model: str,
        json_mode: bool = True,
        temperature: float = 0.0,
        max_spec_chars: int = _DEFAULT_MAX_SPEC_CHARS,
    ) -> None:
        self._client = client
        self.model = model
        self.json_mode = json_mode
        self.temperature = temperature
        self.max_spec_chars = max_spec_chars

    # -- one chat call, tolerant of providers without JSON mode ----------
    def _create(self, messages):
        base = dict(model=self.model, temperature=self.temperature, messages=messages)
        if not self.json_mode:
            return self._client.chat.completions.create(**base)
        try:
            return self._client.chat.completions.create(
                response_format={"type": "json_object"}, **base
            )
        except Exception as exc:
            s = str(exc).lower()
            # Some providers/models reject response_format — retry plain (the
            # prompt already demands JSON and _extract_json tolerates prose).
            if "response_format" in s or "json" in s or "not support" in s:
                return self._client.chat.completions.create(**base)
            raise

    # -- protocol --------------------------------------------------------
    def judge(self, element: str, target_spec: str, claim_context: str) -> ElementVerdict:
        spec_for_model = target_spec[: self.max_spec_chars]
        prompt = _USER_TEMPLATE.format(
            claim_context=claim_context or "(none)",
            element=element,
            spec=spec_for_model,
        )
        try:
            resp = self._create([
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            content = resp.choices[0].message.content
        except Exception as exc:  # network/parse/etc — never fabricate, escalate.
            return ElementVerdict(
                element=element, verdict=Verdict.UNCLEAR, evidence_span="",
                confidence=0.0,
                rationale=f"[LLM error] {type(exc).__name__}: {exc}",
                needs_review=True,
            )

        return self._verdict_from_payload(element, target_spec, _extract_json(content))

    # -- response → ElementVerdict (with P-NO-GUESS validation) -----------
    def _verdict_from_payload(self, element: str, target_spec: str, data: dict) -> ElementVerdict:
        raw_verdict = str(data.get("verdict", "")).strip().upper()
        verdict = {
            "MATCH": Verdict.MATCH,
            "MISSING": Verdict.MISSING,
            "UNCLEAR": Verdict.UNCLEAR,
        }.get(raw_verdict, Verdict.UNCLEAR)

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        evidence = str(data.get("evidence_span") or "")
        # P-NO-GUESS: discard any quote that is not a verbatim substring of the spec.
        fabricated = bool(evidence) and evidence not in target_spec
        if fabricated:
            evidence = ""

        if verdict is Verdict.MISSING:
            confidence = 1.0 if not evidence else confidence  # confident absence
            evidence = ""

        rationale = str(data.get("rationale") or "").strip()
        prefix = "[LLM]"
        if fabricated:
            prefix += " [捏造引用を破棄: 仕様に逐語一致せず]"
        rationale = f"{prefix} {rationale}".strip()

        # __post_init__ demotes any MATCH that ended up without an evidence span.
        return ElementVerdict(
            element=element,
            verdict=verdict,
            evidence_span=evidence,
            confidence=confidence,
            rationale=rationale,
        )


class AzureOpenAIJudge(OpenAICompatibleJudge):
    """OpenAICompatibleJudge backed by Azure OpenAI. `deployment` == model."""

    def __init__(
        self,
        *,
        deployment: str,
        client=None,
        endpoint: str | None = None,
        api_key: str | None = None,
        api_version: str = _DEFAULT_API_VERSION,
        **kw,
    ) -> None:
        if client is None:
            if not (endpoint and api_key):
                raise ValueError(
                    "AzureOpenAIJudge needs endpoint+api_key (or an injected client)."
                )
            client = _make_azure_client(endpoint, api_key, api_version)
        super().__init__(client=client, model=deployment, **kw)

    @property
    def deployment(self) -> str:   # back-compat alias
        return self.model


class GitHubModelsJudge(OpenAICompatibleJudge):
    """OpenAICompatibleJudge backed by GitHub Models (OpenAI-compatible).

    Auth is a GitHub token (fine-grained PAT with models:read, or the Actions
    GITHUB_TOKEN granted `models: read`). This is the "GitHub / Copilot" path.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_GH_MODEL,
        token: str | None = None,
        base_url: str = _GITHUB_MODELS_ENDPOINT,
        client=None,
        **kw,
    ) -> None:
        if client is None:
            if not token:
                raise ValueError("GitHubModelsJudge needs a token (or an injected client).")
            client = _make_openai_client(base_url, token)
        super().__init__(client=client, model=model, **kw)


# ---------------------------------------------------------------------------
# Env-driven factories — return None when unconfigured (graceful keyless fallback)
# ---------------------------------------------------------------------------

def make_azure_judge_from_env(load_dotenv: bool = True) -> AzureOpenAIJudge | None:
    """Build an AzureOpenAIJudge from AZURE_OPENAI_* env. None if unconfigured."""
    _maybe_dotenv(load_dotenv)
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", _DEFAULT_API_VERSION).strip()
    if not (endpoint and api_key and deployment):
        return None
    return AzureOpenAIJudge(
        deployment=deployment, endpoint=endpoint, api_key=api_key, api_version=api_version,
    )


def make_github_models_judge_from_env(load_dotenv: bool = True) -> GitHubModelsJudge | None:
    """Build a GitHubModelsJudge from a GitHub token. None if no token present.

    Token resolution: GITHUB_MODELS_TOKEN, else GITHUB_TOKEN (Actions default).
    Model: GITHUB_MODELS_MODEL (default openai/gpt-4o-mini). Endpoint override:
    GITHUB_MODELS_ENDPOINT.
    """
    _maybe_dotenv(load_dotenv)
    token = (os.environ.get("GITHUB_MODELS_TOKEN")
             or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        return None
    model = os.environ.get("GITHUB_MODELS_MODEL", _DEFAULT_GH_MODEL).strip() or _DEFAULT_GH_MODEL
    base_url = os.environ.get("GITHUB_MODELS_ENDPOINT", _GITHUB_MODELS_ENDPOINT).strip() or _GITHUB_MODELS_ENDPOINT
    return GitHubModelsJudge(model=model, token=token, base_url=base_url)


def make_llm_judge_from_env(provider: str = "auto", load_dotenv: bool = True):
    """Dispatch to a provider factory by name. Returns a Judge or None.

    provider:
      "none"          → None (keyless)
      "azure"         → Azure OpenAI (AZURE_OPENAI_*)
      "github"/"copilot" → GitHub Models (GITHUB_MODELS_TOKEN / GITHUB_TOKEN)
      "auto"          → Azure if configured, else GitHub, else None
    """
    p = (provider or "auto").lower()
    if p in ("none", "off", ""):
        return None
    if p in ("azure", "azure-openai", "aoai"):
        return make_azure_judge_from_env(load_dotenv)
    if p in ("github", "github-models", "gh", "copilot"):
        return make_github_models_judge_from_env(load_dotenv)
    if p == "auto":
        return (make_azure_judge_from_env(load_dotenv)
                or make_github_models_judge_from_env(load_dotenv))
    return None
