"""Analysis layer.

Split by key-dependency on purpose:
  - summarize.py : DETERMINISTIC, no LLM, no key. Bibliographic summary +
    heuristic claim-element split. Runs anywhere.
  - compare.py   : M4 semantic comparison. Verdict enum, ElementVerdict,
    ComparisonResult, Judge Protocol, HeuristicJudge (keyless), compare().
    LLM-powered Judge can be swapped in by the agent-in-the-loop with no
    other code changes.
"""

from .summarize import ClaimBreakdown, PatentSummary, summarize
from .compare import (
    Verdict,
    ElementVerdict,
    ComparisonResult,
    Judge,
    HeuristicJudge,
    compare,
)
from .score import (
    ElementCoverage,
    PatentScore,
    Proposal,
    LenientJudge,
    score_patent,
    score_all,
    triage_sort_key,
)

__all__ = [
    "ClaimBreakdown",
    "PatentSummary",
    "summarize",
    "Verdict",
    "ElementVerdict",
    "ComparisonResult",
    "Judge",
    "HeuristicJudge",
    "compare",
    "ElementCoverage",
    "PatentScore",
    "Proposal",
    "LenientJudge",
    "score_patent",
    "score_all",
    "triage_sort_key",
]
