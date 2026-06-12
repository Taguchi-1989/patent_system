"""Semantic re-rank (M10) — catch what keyword search misses, transparently.

Keyword matching is the deterministic anchor (precision); this module adds a
RECALL channel that scores each hit's title+abstract against the search brief
by meaning, then fuses the two — the same two-channel philosophy as the FTO
scorer: agreement raises confidence, divergence flags 要確認 instead of being
averaged away.

Embedder ladder (keyless first):
  - TF-IDF cosine (default) : pure stdlib, deterministic, reproducible. Handles
    Japanese via CJK character bigrams (no tokenizer dependency).
  - OpenAICompatibleEmbedder: optional API embeddings (Azure OpenAI / GitHub
    Models), same env-seam pattern as analyze/llm_judge. Lazy import, never
    required for the keyless path.

The semantic score never silently overrides the keyword anchor: both numbers
are kept on the Candidate, the fused value is their mean, and a large gap
between channels appends a needs_review note naming both values.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Protocol

from .query import SearchQuery
from .rank import Candidate

_WORD_RE = re.compile(r"[a-z0-9]{2,}")
_CJK_RE = re.compile(r"[぀-ヿ一-鿿]+")
_DIVERGENCE = 0.5          # |keyword − semantic| above this → 要確認


def tokenize(text: str) -> list[str]:
    """Lowercase latin words + CJK character bigrams (tokenizer-free JP)."""
    low = (text or "").lower()
    toks = _WORD_RE.findall(low)
    for run in _CJK_RE.findall(low):
        toks.extend([run[i:i + 2] for i in range(len(run) - 1)] or [run])
    return toks


def tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    """Length-normalized TF-IDF vectors over this corpus (deterministic)."""
    token_lists = [tokenize(t) for t in texts]
    df = Counter()
    for toks in token_lists:
        df.update(set(toks))
    n = len(texts)
    vecs: list[dict[str, float]] = []
    for toks in token_lists:
        tf = Counter(toks)
        vec = {t: c * (math.log((n + 1) / (df[t] + 1)) + 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vecs.append({t: v / norm for t, v in vec.items()})
    return vecs


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


class Embedder(Protocol):
    """Seam for real embedding models. embed() returns one vector per text."""

    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedder:
    """API embeddings via the openai package (Azure OpenAI / GitHub Models)."""

    def __init__(self, client, model: str, name: str = "api-embedder"):
        self._client = client
        self.model = model
        self.name = name

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        # API may return out of order; index field is authoritative.
        out: list[list[float]] = [[] for _ in texts]
        for item in resp.data:
            out[item.index] = list(item.embedding)
        return out


def make_embedder_from_env(provider: str = "auto") -> OpenAICompatibleEmbedder | None:
    """Embedder from env, or None (→ keyless TF-IDF). Mirrors make_llm_judge_from_env.

    azure : AZURE_OPENAI_ENDPOINT/_API_KEY + AZURE_OPENAI_EMBED_DEPLOYMENT
    github: GITHUB_MODELS_TOKEN or GITHUB_TOKEN (model openai/text-embedding-3-small)
    """
    if provider in ("azure", "auto"):
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT")
        if endpoint and api_key and deployment:
            from openai import AzureOpenAI  # noqa: PLC0415 — optional dep
            client = AzureOpenAI(
                azure_endpoint=endpoint, api_key=api_key,
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
            return OpenAICompatibleEmbedder(client, deployment, name="azure-embedder")
        if provider == "azure":
            return None
    if provider in ("github", "auto"):
        token = os.environ.get("GITHUB_MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            from openai import OpenAI  # noqa: PLC0415 — optional dep
            client = OpenAI(base_url="https://models.github.ai/inference", api_key=token)
            return OpenAICompatibleEmbedder(
                client, os.environ.get("GITHUB_MODELS_EMBED_MODEL", "openai/text-embedding-3-small"),
                name="github-embedder",
            )
    return None


def _vec_cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def brief_query_text(q: SearchQuery) -> str:
    """The semantic query: free-text description if given, else the keywords."""
    if getattr(q, "description", ""):
        return q.description
    return " ".join(k for group in q.keywords for k in group) or q.purpose


def semantic_scores(query_text: str, doc_texts: list[str],
                    embedder: Embedder | None = None) -> list[float]:
    """Similarity of each doc to the query, 0..1. Keyless default = TF-IDF."""
    if not doc_texts:
        return []
    if embedder is None:
        vecs = tfidf_vectors([query_text, *doc_texts])
        return [max(0.0, cosine(vecs[0], v)) for v in vecs[1:]]
    embedded = embedder.embed([query_text, *doc_texts])
    return [max(0.0, _vec_cosine(embedded[0], v)) for v in embedded[1:]]


def apply_semantic(cands: list[Candidate], rows: list[dict], q: SearchQuery,
                   embedder: Embedder | None = None) -> list[Candidate]:
    """Add the semantic channel and re-sort by the fused value.

    Both channel values stay visible on each Candidate; divergence between the
    normalized keyword anchor and the semantic score appends a 要確認 note.
    """
    from .rank import _texts  # noqa: PLC0415 — shared row-text helper

    by_pub = {r.get("publication_number"): r for r in rows}
    docs: list[str] = []
    for c in cands:
        row = by_pub.get(c.publication_number, {})
        docs.append(" ".join(_texts(row.get("title_localized"))
                             + _texts(row.get("abstract_localized"))) or c.title)
    sems = semantic_scores(brief_query_text(q), docs, embedder=embedder)

    # Both channels are normalized WITHIN this result set (best hit = 1.0):
    # raw TF-IDF cosines on short texts sit around 0.2–0.4, so comparing them
    # to the keyword anchor on absolute scale would flag everything as 乖離.
    max_kw = max((c.score for c in cands), default=0) or 1
    max_sem = max(sems, default=0.0) or 1.0
    for c, sem in zip(cands, sems):
        kw_norm = c.score / max_kw
        sem_norm = sem / max_sem
        c.semantic = round(sem_norm, 3)
        c.combined = round((kw_norm + sem_norm) / 2, 3)
        if abs(kw_norm - sem_norm) > _DIVERGENCE:
            c.needs_review = True
            c.notes.append(
                f"チャネル乖離: 決定論(キーワード)={kw_norm:.2f} と 意味={sem_norm:.2f} が解離 — "
                "言い換え/別ドメインの可能性、本文で確認"
            )
    cands.sort(key=lambda c: (-(c.combined or 0.0), c.publication_number))
    return cands
