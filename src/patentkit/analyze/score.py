"""FTO 抵触リスク・スコアリング層（決定論チャネル × LLMチャネル の融合）.

WHY THIS EXISTS
---------------
M4 の compare.py は「請求項要素ごとの 3 値（MATCH/MISSING/UNCLEAR）」を出すが、
スクリーニング担当者が一覧から *パッと優先順位を付ける* には、特許 1 件ごとの
**1 つの数字（%）** が要る。本モジュールはそれを出す。

設計の肝は「1 本の数字に混ぜ込まない」こと:

  決定論チャネル (strict)  … 既存 HeuristicJudge。語彙の重なり。再現可能なアンカー。
  LLM チャネル   (recall)  … 言い換え・上位概念も拾う意味判定。リコールの底上げ。
                            プロトタイプでは決定論的な LenientJudge を差し込むが、
                            本番では Judge プロトコルを満たす LLM Judge をそのまま
                            channels に渡すだけで置き換わる（compare.py と同じ口）。

  融合           p = mean(p_strict, p_recall)            ← 値そのもの
  SN 比          confidence = 1 - |p_strict - p_recall|  ← 2 チャネルの一致度
  ノイズ管理     乖離が大きい / 中間被覆の要素は自動で needs_review（要確認）へ

FTO（自由実施・抵触可能性）の集約は **全要素ルール**に従う:
請求項は *全要素* がカバーされて初めて文言侵害が成立する。よって 1 要素でも明確に
欠落（gap）すれば、全体の抵触リスクは大きく下がる。集約は加重平均（被覆%の見せ方）
＋「最弱要素」と「gap 件数」（全要素ゲート）の二本立てでバンドを決める。

P-NO-GUESS は維持:
  - 被覆確率は Judge の verdict から導出（MISSING→0、それ以外→confidence）。
  - "covered" バンドの要素は根拠スパンが空なら partial+needs_review に降格。
  - 請求項要素が無い特許は band=UNKNOWN で needs_review。

純標準ライブラリ・決定論的（両チャネルが決定論的なら 2 回の実行はバイト一致）。
しきい値は校正可能（golden set での calibration を想定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compare import (
    ElementVerdict,
    HeuristicJudge,
    Judge,
    Verdict,
    _best_evidence_span,
    _tokenize,
)
from .summarize import PatentSummary


# ===========================================================================
# 校正可能なしきい値（calibration knobs）
# ===========================================================================
# これらは golden set に対する calibration で調整する対象。デフォルトは保守的。

COVERED_THRESHOLD = 0.60   # この被覆確率以上の要素は「カバー済み」
GAP_THRESHOLD = 0.30       # この被覆確率未満の要素は「欠落(gap)」= 全要素ルールの穴
AGREEMENT_THRESHOLD = 0.60 # 2 チャネルの一致度がこれ未満なら要確認（SN 低）

# バンド（FTO 抵触リスク）の決定境界。
HIGH_COVERAGE = 0.70       # 全要素カバー かつ 平均被覆がこれ以上 → 高リスク
MEDIUM_COVERAGE = 0.45     # gap が高々 1 件 かつ 平均被覆がこれ以上 → 中リスク

# 信頼幅（バンド半幅）の最大値（%ポイント）。一致度が低いほど広がる。
MAX_BAND_HALF_WIDTH = 25.0


# ===========================================================================
# 要素ごとの被覆（融合結果）
# ===========================================================================

@dataclass
class ElementCoverage:
    """1 請求項要素の融合被覆結果。

    channels には各チャネルの被覆確率を残す（透明性: なぜこの値か）。
    """

    element: str
    p_coverage: float                 # 融合被覆確率 [0,1]
    confidence: float                 # SN 比 = 1 - |p_strict - p_recall|  [0,1]
    channels: dict[str, float]        # {"strict": p1, "recall": p2}
    evidence_span: str                # 根拠スパン（アンカー側を優先・仕様の逐語部分文字列）
    band: str                         # "covered" / "partial" / "gap"
    needs_review: bool
    rationale: str
    matched_terms: list[str] = field(default_factory=list)  # 一致した語（両側ハイライト用）

    def to_dict(self) -> dict:
        return {
            "element": self.element,
            "p_coverage": round(self.p_coverage, 6),
            "confidence": round(self.confidence, 6),
            "channels": {k: round(v, 6) for k, v in sorted(self.channels.items())},
            "evidence_span": self.evidence_span,
            "band": self.band,
            "needs_review": self.needs_review,
            "rationale": self.rationale,
            "matched_terms": list(self.matched_terms),
        }


# ===========================================================================
# 提案（推奨アクション）— すべて引用 or 「不在」を明示し、生成文を足さない
# ===========================================================================

@dataclass
class Proposal:
    """1 つの推奨アクション。

    basis は対象仕様の **逐語引用**（= 捏造不能）。欠落（不在）に基づく提案は
    basis を空にして「対応記載が無い」ことを正直に示す（P-NO-GUESS）。
    """

    category: str    # 例: "精査" / "防御・設計回避" / "解釈確認" / "優先度"
    text: str
    basis: str = ""  # 仕様の逐語引用、または "" (不在に基づく場合)

    def to_dict(self) -> dict:
        return {"category": self.category, "text": self.text, "basis": self.basis}


# ===========================================================================
# 特許 1 件のスコア
# ===========================================================================

@dataclass
class PatentScore:
    """特許 1 件 × 対象仕様 の FTO 抵触リスク・スコア（screening の単位）。"""

    canonical: str
    target_spec_title: str
    coverage_pct: float               # 見出しの % = 加重平均被覆 (0-100)
    confidence_pct: float             # 平均一致度 (0-100) → 信頼幅の根拠
    band_low: float                   # coverage_pct - 半幅 (0-100)
    band_high: float                  # coverage_pct + 半幅 (0-100)
    risk_band: str                    # "HIGH" / "MEDIUM" / "LOW" / "UNKNOWN"
    min_coverage_pct: float           # 最弱要素の被覆% (全要素ゲート)
    gap_count: int                    # 明確に欠落した要素数
    n_elements: int
    review_count: int                 # 要確認に落ちた要素数
    elements: list[ElementCoverage] = field(default_factory=list)
    rationale: str = ""               # バンドの人間向け一行説明
    proposals: list[Proposal] = field(default_factory=list)  # 推奨アクション（引用付き）
    source: str = ""
    source_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "target_spec_title": self.target_spec_title,
            "coverage_pct": round(self.coverage_pct, 4),
            "confidence_pct": round(self.confidence_pct, 4),
            "band_low": round(self.band_low, 4),
            "band_high": round(self.band_high, 4),
            "risk_band": self.risk_band,
            "min_coverage_pct": round(self.min_coverage_pct, 4),
            "gap_count": self.gap_count,
            "n_elements": self.n_elements,
            "review_count": self.review_count,
            "rationale": self.rationale,
            "source": self.source,
            "source_url": self.source_url,
            "elements": [e.to_dict() for e in self.elements],
            "proposals": [p.to_dict() for p in self.proposals],
        }


# ===========================================================================
# LenientJudge — recall 寄りの決定論チャネル（LLM チャネルの差し替え地点）
# ===========================================================================

def _stem(tok: str) -> str:
    """ごく軽い語幹化（決定論的）。複数形・活用の揺れを吸収して recall を上げる。"""
    for suf in ("ies", "ing", "ed", "es", "s", "er", "ly", "tion", "ation"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


class LenientJudge:
    """recall 重視の決定論 Judge。語幹化＋部分一致で言い換えを拾いにいく。

    HeuristicJudge（precision アンカー）より緩い。両者の差が SN 比になる。
    本番では、この channel を Judge プロトコルを満たす LLM Judge に差し替える。
    """

    MATCH_THRESHOLD = 0.40
    MIN_TOKENS = 1

    def judge(self, element: str, target_spec: str, claim_context: str) -> ElementVerdict:
        element_tokens = _tokenize(element)
        if not element_tokens:
            return ElementVerdict(
                element=element, verdict=Verdict.UNCLEAR, evidence_span="",
                confidence=0.0, rationale="no key terms (lenient)", needs_review=True,
            )

        spec_tokens = _tokenize(target_spec)
        spec_stems = {_stem(t) for t in spec_tokens}

        # 語幹一致 or 双方向の部分文字列一致（len>=4 の語幹）でマッチとみなす。
        matched_surface: set[str] = set()
        for tok in element_tokens:
            st = _stem(tok)
            if st in spec_stems:
                matched_surface.add(tok)
                continue
            if len(st) >= 4 and any(
                (st in s or s in st) for s in spec_stems if len(s) >= 4
            ):
                matched_surface.add(tok)

        n_matched = len(matched_surface)
        n_total = len(element_tokens)
        overlap = n_matched / n_total

        # 根拠スパンは「元の表層トークン ∩ 仕様トークン」で探す（実在部分文字列保証）。
        evidence = _best_evidence_span(matched_surface & spec_tokens, target_spec)

        if n_matched == 0:
            return ElementVerdict(
                element=element, verdict=Verdict.MISSING, evidence_span="",
                confidence=1.0, rationale="no lenient overlap", needs_review=False,
            )
        if overlap >= self.MATCH_THRESHOLD and n_matched >= self.MIN_TOKENS:
            return ElementVerdict(
                element=element, verdict=Verdict.MATCH, evidence_span=evidence,
                confidence=overlap,
                rationale=f"lenient overlap {n_matched}/{n_total}={overlap:.2f}",
                needs_review=False,
            )
        return ElementVerdict(
            element=element, verdict=Verdict.UNCLEAR, evidence_span=evidence,
            confidence=overlap,
            rationale=f"lenient partial {n_matched}/{n_total}={overlap:.2f}",
            needs_review=True,
        )


# ===========================================================================
# 被覆確率の導出 + 融合
# ===========================================================================

def _coverage_of(v: ElementVerdict) -> float:
    """Judge の verdict から被覆確率 [0,1] を導出する。

    MISSING は被覆 0。それ以外は confidence（= 重なり率）を被覆シグナルとして使う。
    """
    if v.verdict is Verdict.MISSING:
        return 0.0
    return max(0.0, min(1.0, v.confidence))


def _fuse_element(
    element: str,
    verdicts: dict[str, ElementVerdict],
    matched_terms: list[str] | None = None,
) -> ElementCoverage:
    """各チャネルの verdict を 1 要素の融合被覆にまとめる。"""
    channels = {name: _coverage_of(v) for name, v in verdicts.items()}
    ps = list(channels.values())
    p = sum(ps) / len(ps)

    # SN 比 = チャネル間の一致度（2 チャネル時は 1 - |差|、単一チャネルは confidence 不明＝低め）。
    if len(ps) >= 2:
        spread = max(ps) - min(ps)
        confidence = 1.0 - spread
    else:
        confidence = 0.5  # 単一チャネルでは一致度が測れない → 中庸（幅広め）

    # 根拠スパンはアンカー（strict）を優先、無ければ他チャネル。
    evidence = ""
    for name in ("strict",) + tuple(n for n in verdicts if n != "strict"):
        ev = verdicts.get(name)
        if ev is not None and ev.evidence_span:
            evidence = ev.evidence_span
            break

    band = "covered" if p >= COVERED_THRESHOLD else ("gap" if p < GAP_THRESHOLD else "partial")

    # P-NO-GUESS: 根拠スパンの無い "covered" は partial+要確認 に降格。
    forced = ""
    if band == "covered" and not evidence:
        band = "partial"
        forced = " [P-NO-GUESS: 根拠スパン無しのため partial に降格]"

    needs_review = (
        band == "partial"
        or confidence < AGREEMENT_THRESHOLD
    )

    parts = [f"{name}={channels[name]:.0%}" for name in sorted(channels)]
    rationale = f"被覆 {p:.0%}（{', '.join(parts)}）一致度 {confidence:.0%}{forced}"

    return ElementCoverage(
        element=element,
        p_coverage=p,
        confidence=confidence,
        channels=channels,
        evidence_span=evidence,
        band=band,
        needs_review=needs_review,
        rationale=rationale,
        matched_terms=sorted(matched_terms or []),
    )


# ===========================================================================
# 提案（推奨アクション）の組み立て — 引用 or 不在のみ、生成文を足さない
# ===========================================================================

def _short(text: str, n: int = 64) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _build_proposals(
    covers: list[ElementCoverage],
    coverage: float,
    gap_count: int,
    risk_band: str,
) -> list[Proposal]:
    """スコアと要素被覆から推奨アクションを決定論的に導出する。

    すべて (a) 仕様の逐語引用、または (b) 「対応記載が無い」という不在の明示、
    のいずれかに根拠づける。生成的な要約文は足さない（ハルシネーション回避）。
    """
    proposals: list[Proposal] = []

    # バンド見出しの提案。
    if risk_band == "HIGH" and covers:
        weakest = min(covers, key=lambda c: c.p_coverage)
        proposals.append(Proposal(
            category="精査",
            text=(
                "独立請求項の全要素が相応にカバーされています。文言侵害の精査・"
                f"弁理士レビューを推奨。特に最弱要素「{_short(weakest.element)}」"
                f"（被覆 {weakest.p_coverage:.0%}）の用語解釈を重点確認してください。"
            ),
            basis=weakest.evidence_span,
        ))
    elif risk_band == "MEDIUM":
        proposals.append(Proposal(
            category="要確認",
            text=(
                f"{len(covers)} 要素中 {gap_count} 件が手薄です。均等論・設計差の"
                "検討余地があり、下記の手薄・欠落要素を確認してください。"
            ),
        ))
    elif risk_band == "LOW":
        proposals.append(Proposal(
            category="優先度",
            text=(
                "明確な欠落要素があり、全要素ルール上は文言侵害の可能性が低めです。"
                "優先度は下げつつ、欠落要素について均等論の観点のみ確認してください。"
            ),
        ))

    # 欠落要素 → 防御・設計回避の起点（不在に基づくので basis は空）。
    for c in covers:
        if c.band == "gap":
            proposals.append(Proposal(
                category="防御・設計回避",
                text=(
                    f"請求項要素「{_short(c.element)}」は対象仕様に対応記載が"
                    f"見当たりません（被覆 {c.p_coverage:.0%}）。全要素ルール上、"
                    "非侵害の論拠／設計回避の起点になり得ます。逆に、相手仕様に"
                    "該当記載が本当に無いか本文で再確認してください。"
                ),
                basis="",
            ))

    # 部分一致（gap 以外で要確認）→ 解釈確認（仕様の逐語引用つき）。
    for c in covers:
        if c.needs_review and c.band != "gap":
            terms = "、".join(c.matched_terms) if c.matched_terms else "—"
            proposals.append(Proposal(
                category="解釈確認",
                text=(
                    f"請求項要素「{_short(c.element)}」は部分一致（被覆 "
                    f"{c.p_coverage:.0%}、一致語: {terms}）。下記の仕様記載が該当する"
                    "可能性があります。用語の同義性・上位概念の解釈を専門家確認して"
                    "ください。"
                ),
                basis=c.evidence_span,
            ))

    return proposals


# ===========================================================================
# バンド決定（全要素ルール）
# ===========================================================================

def _decide_band(coverage: float, gap_count: int, n_elements: int) -> tuple[str, str]:
    """平均被覆・gap 件数から FTO 抵触リスクのバンドと一行説明を返す。"""
    if n_elements == 0:
        return "UNKNOWN", "請求項要素が取得できず、スコア算出不可。要人手取得。"

    if gap_count == 0 and coverage >= HIGH_COVERAGE:
        return "HIGH", (
            f"独立請求項の全 {n_elements} 要素が相応にカバー（明確な欠落なし）。"
            "全要素ルール上、文言侵害の可能性あり。弁理士確認を推奨。"
        )
    if gap_count <= 1 and coverage >= MEDIUM_COVERAGE:
        return "MEDIUM", (
            f"{n_elements} 要素中 {gap_count} 件が手薄。均等論・設計差の検討余地あり。要確認。"
        )
    return "LOW", (
        f"{gap_count} 件の要素が明確に欠落。全要素ルール上、文言侵害の可能性は低い。"
    )


# ===========================================================================
# エントリポイント
# ===========================================================================

def default_channels() -> dict[str, Judge]:
    """既定の 2 チャネル。strict=決定論アンカー、recall=LLM チャネルの差し替え地点。"""
    return {"strict": HeuristicJudge(), "recall": LenientJudge()}


def score_patent(
    target_spec: str,
    summary: PatentSummary,
    channels: dict[str, Judge] | None = None,
) -> PatentScore:
    """対象仕様 × 特許 の FTO 抵触リスク・スコアを算出する。

    Args:
        target_spec: 自社仕様の全文。
        summary: summarize() の出力（独立請求項の要素分解を使う）。
        channels: 名前→Judge の辞書。既定は {"strict": Heuristic, "recall": Lenient}。
            本番では "recall"（または新キー）に LLM Judge を渡すだけで融合に乗る。

    Returns:
        PatentScore。
    """
    if channels is None:
        channels = default_channels()

    spec_title = next(
        (ln.strip().lstrip("#").strip() for ln in target_spec.splitlines() if ln.strip()),
        "(no title)",
    )[:80] or "(no title)"

    elements_src = (
        summary.breakdown.elements
        if (summary.breakdown and summary.breakdown.elements)
        else []
    )
    claim_context = summary.independent_claim
    spec_tokens = _tokenize(target_spec)   # computed once for matched-term highlighting

    covers: list[ElementCoverage] = []
    for element in elements_src:
        verdicts = {
            name: judge.judge(element, target_spec, claim_context)
            for name, judge in channels.items()
        }
        # Terms present in BOTH the claim element and the spec — drives the
        # dual-side highlighting ("where/how it hits"). Surface tokens only,
        # so they are real words in both texts (no fabricated overlap).
        matched_terms = sorted(_tokenize(element) & spec_tokens)
        covers.append(_fuse_element(element, verdicts, matched_terms=matched_terms))

    n = len(covers)
    if n == 0:
        return PatentScore(
            canonical=summary.canonical,
            target_spec_title=spec_title,
            coverage_pct=0.0, confidence_pct=0.0,
            band_low=0.0, band_high=0.0,
            risk_band="UNKNOWN",
            min_coverage_pct=0.0, gap_count=0, n_elements=0, review_count=0,
            elements=[],
            rationale="請求項要素が取得できず、スコア算出不可。要人手取得。",
            proposals=[Proposal(
                category="取得",
                text="独立請求項のテキストが取得できていません。別ソース（BigQuery / "
                     "USPTO 一括）での再取得、または手入力での補完を検討してください。",
            )],
            source=summary.source, source_url=summary.source_url,
        )

    coverage = sum(c.p_coverage for c in covers) / n          # 加重は当面均一
    min_cov = min(c.p_coverage for c in covers)
    mean_conf = sum(c.confidence for c in covers) / n
    gap_count = sum(1 for c in covers if c.p_coverage < GAP_THRESHOLD)
    review_count = sum(1 for c in covers if c.needs_review)

    # 信頼幅: 一致度が低いほど広い。gap が多いほど少し広げる。
    half = MAX_BAND_HALF_WIDTH * (1.0 - mean_conf)
    half += min(gap_count, n) / max(n, 1) * 5.0
    half = min(half, MAX_BAND_HALF_WIDTH)

    coverage_pct = coverage * 100.0
    band_low = max(0.0, coverage_pct - half)
    band_high = min(100.0, coverage_pct + half)

    risk_band, rationale = _decide_band(coverage, gap_count, n)
    proposals = _build_proposals(covers, coverage, gap_count, risk_band)

    return PatentScore(
        canonical=summary.canonical,
        target_spec_title=spec_title,
        coverage_pct=coverage_pct,
        confidence_pct=mean_conf * 100.0,
        band_low=band_low,
        band_high=band_high,
        risk_band=risk_band,
        min_coverage_pct=min_cov * 100.0,
        gap_count=gap_count,
        n_elements=n,
        review_count=review_count,
        elements=covers,
        rationale=rationale,
        proposals=proposals,
        source=summary.source,
        source_url=summary.source_url,
    )


def score_all(
    target_spec: str,
    summaries: list[PatentSummary],
    channels: dict[str, Judge] | None = None,
) -> list[PatentScore]:
    """複数特許をまとめてスコア。channels は使い回す（毎回再構築しない）。"""
    if channels is None:
        channels = default_channels()
    return [score_patent(target_spec, s, channels=channels) for s in summaries]


# 一覧の並べ替えに使う順序キー（リスク優先 → 被覆% 降順 → 番号）。
_BAND_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}


def triage_sort_key(score: PatentScore):
    """トリアージ一覧の並べ替えキー。高リスク・高被覆が上に来る。"""
    return (_BAND_RANK.get(score.risk_band, 9), -score.coverage_pct, score.canonical)
