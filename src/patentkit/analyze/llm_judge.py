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


class AzureOpenAIJudge:
    """LLM-backed Judge (Azure OpenAI) implementing the Judge protocol.

    Drop-in for the score.py "recall"/semantic channel. P-NO-GUESS is enforced
    here in code (verbatim-substring validation) AND structurally in
    ElementVerdict.__post_init__.
    """

    def __init__(
        self,
        *,
        deployment: str,
        client=None,
        endpoint: str | None = None,
        api_key: str | None = None,
        api_version: str = _DEFAULT_API_VERSION,
        temperature: float = 0.0,
        max_spec_chars: int = _DEFAULT_MAX_SPEC_CHARS,
    ) -> None:
        self.deployment = deployment
        self.temperature = temperature
        self.max_spec_chars = max_spec_chars
        if client is not None:
            self._client = client            # injected (tests / custom)
        else:
            if not (endpoint and api_key):
                raise ValueError(
                    "AzureOpenAIJudge needs endpoint+api_key (or an injected client)."
                )
            self._client = _make_azure_client(endpoint, api_key, api_version)

    # -- protocol --------------------------------------------------------
    def judge(self, element: str, target_spec: str, claim_context: str) -> ElementVerdict:
        spec_for_model = target_spec[: self.max_spec_chars]
        prompt = _USER_TEMPLATE.format(
            claim_context=claim_context or "(none)",
            element=element,
            spec=spec_for_model,
        )
        try:
            resp = self._client.chat.completions.create(
                model=self.deployment,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            content = resp.choices[0].message.content
        except Exception as exc:  # network/parse/etc — never fabricate, escalate.
            return ElementVerdict(
                element=element, verdict=Verdict.UNCLEAR, evidence_span="",
                confidence=0.0,
                rationale=f"[LLM:azure error] {type(exc).__name__}: {exc}",
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
        prefix = "[LLM:azure]"
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


def make_azure_judge_from_env(load_dotenv: bool = True) -> AzureOpenAIJudge | None:
    """Build an AzureOpenAIJudge from environment variables.

    Returns None if Azure is not configured (so callers can fall back to the
    keyless LenientJudge gracefully). Raises only if openai is missing AND
    config is present (i.e. the user clearly intended to use it).
    """
    if load_dotenv:
        try:
            from dotenv import load_dotenv as _ld
            _ld()
        except Exception:
            pass

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", _DEFAULT_API_VERSION).strip()

    if not (endpoint and api_key and deployment):
        return None

    return AzureOpenAIJudge(
        deployment=deployment,
        endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )
