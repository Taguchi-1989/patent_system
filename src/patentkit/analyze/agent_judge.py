"""サブスクのエージェント（Claude Code / Copilot）を「頭脳」にする経路.

二つの「頭脳の動かし方」のうち、こちらは **APIキー不要・定額サブスク**の経路:
LLM API を叩く代わりに、**いま動いているエージェント（Claude Code / GitHub Copilot
など）が黒子で判定**し、その結果（verdict＋逐語根拠）をワークシートJSONに書き込む。
本モジュールはそれを `Judge` プロトコルとして読み戻すだけ。これがこのリポジトリ本来の
「黒子エージェント」構想（README/architecture）の実体。

ワークフロー:
  1. `build_worksheet()` / `write_worksheet()` で「請求項要素＋対象仕様」をJSONに出力。
  2. エージェント（Claude Code 等）がそのJSONを読み、各要素に verdict / evidence_span
     （仕様からの**逐語コピー**）/ confidence / rationale を埋める。APIキーは使わない。
  3. `make_agent_judge_from_file()` で読み戻し、`build_channels()` の recall チャネルに差す。

P-NO-GUESS は API 経路と**同一の検証**（compare.coerce_verdict）で守る:
逐語非一致の引用は破棄され、根拠なき MATCH は UNCLEAR に降格する。
依存ゼロ（純標準ライブラリ）。
"""

from __future__ import annotations

import json

from .compare import ElementVerdict, Verdict, coerce_verdict
from .summarize import PatentSummary

_INSTRUCTIONS = (
    "あなた（Claude Code / Copilot 等のエージェント）が黒子の判定者です。"
    "各 element について、target_spec が請求項要素をカバーするか判定し、verdict "
    "(MATCH/MISSING/UNCLEAR)、evidence_span（target_spec からの逐語コピー、無ければ空）、"
    "confidence(0-1)、rationale を埋めてください。evidence_span は必ず target_spec の"
    "逐語部分文字列にすること（创作・言い換え禁止）。APIキーは不要です。"
)


class AgentJudge:
    """Judge backed by an agent-filled worksheet (no API key).

    verdicts_by_element maps a claim-element string to a payload dict
    {verdict, evidence_span, confidence, rationale}. Unfilled / unknown
    elements return UNCLEAR(needs_review) — never a guess.
    """

    def __init__(self, verdicts_by_element: dict[str, dict]) -> None:
        self.verdicts_by_element = verdicts_by_element

    def judge(self, element: str, target_spec: str, claim_context: str) -> ElementVerdict:
        entry = self.verdicts_by_element.get(element)
        if entry is None:
            entry = self.verdicts_by_element.get(element.strip())
        if entry is None or not str(entry.get("verdict", "")).strip():
            return ElementVerdict(
                element=element, verdict=Verdict.UNCLEAR, evidence_span="",
                confidence=0.0,
                rationale="[agent] 判定未入力（ワークシートに verdict がありません）",
                needs_review=True,
            )
        # Same P-NO-GUESS guard as the API path.
        return coerce_verdict(element, target_spec, entry, label="agent")


def make_agent_judge_from_file(path: str) -> AgentJudge:
    """Load an AgentJudge from a (possibly agent-filled) worksheet JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    elements = data.get("elements", data) if isinstance(data, dict) else data
    by_element: dict[str, dict] = {}
    for e in elements or []:
        key = (e.get("element") or "").strip()
        if key:
            by_element[key] = e
    return AgentJudge(by_element)


def build_worksheet(target_spec: str, summaries: list[PatentSummary]) -> dict:
    """Build the worksheet an agent fills in (claim elements + the full spec)."""
    title = next(
        (ln.strip().lstrip("#").strip() for ln in target_spec.splitlines() if ln.strip()),
        "(no title)",
    )[:80] or "(no title)"

    elements: list[dict] = []
    for s in summaries:
        els = s.breakdown.elements if (s.breakdown and s.breakdown.elements) else []
        for el in els:
            elements.append({
                "canonical": s.canonical,
                "element": el,
                # ↓ agent fills these:
                "verdict": "",          # MATCH / MISSING / UNCLEAR
                "evidence_span": "",    # target_spec からの逐語コピー（無ければ空）
                "confidence": 0.0,      # 0-1
                "rationale": "",
            })

    return {
        "instructions": _INSTRUCTIONS,
        "target_spec_title": title,
        "target_spec": target_spec,
        "elements": elements,
    }


def write_worksheet(path: str, target_spec: str, summaries: list[PatentSummary]) -> int:
    """Write the worksheet JSON. Returns the number of elements to judge."""
    sheet = build_worksheet(target_spec, summaries)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sheet, f, ensure_ascii=False, indent=2)
    return len(sheet["elements"])
