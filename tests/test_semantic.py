"""Semantic re-rank (M10): TF-IDF channel, fusion, divergence flags, embedder seam."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.search import (
    SearchQuery,
    apply_semantic,
    make_embedder_from_env,
    rank_rows,
    semantic_scores,
)
from patentkit.search.semantic import (
    OpenAICompatibleEmbedder,
    brief_query_text,
    cosine,
    tfidf_vectors,
    tokenize,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
EXPORT = os.path.join(ROOT, "samples", "search_export_SAMPLE.json")


def _rows():
    with open(EXPORT, encoding="utf-8") as f:
        return json.load(f)


def _q(**kw) -> SearchQuery:
    base = dict(keywords=[["wireless power", "wireless charger"],
                          ["foreign object detection", "FOD"]])
    base.update(kw)
    return SearchQuery(**base)


# ---- tokenizer / vectors ---------------------------------------------------

def test_tokenize_latin_and_cjk_bigrams():
    toks = tokenize("Wireless 充電装置 pad")
    assert "wireless" in toks and "pad" in toks
    assert "充電" in toks and "電装" in toks and "装置" in toks   # CJK bigrams


def test_tfidf_cosine_similar_beats_dissimilar():
    docs = [
        "wireless power transfer with foreign object detection",
        "wireless charger detects a foreign metal object on the surface",
        "camera lens autofocus actuator module",
    ]
    vecs = tfidf_vectors(docs)
    assert cosine(vecs[0], vecs[1]) > cosine(vecs[0], vecs[2])


def test_semantic_scores_deterministic_and_bounded():
    query = "wireless charging pad detecting metal objects"
    docs = ["wireless charging pad with metal object detection", "autofocus lens"]
    s1 = semantic_scores(query, docs)
    s2 = semantic_scores(query, docs)
    assert s1 == s2                       # reproducible
    assert all(0.0 <= s <= 1.0 for s in s1)
    assert s1[0] > s1[1]


# ---- fusion over real candidates -------------------------------------------

def test_apply_semantic_adds_channels_and_resorts():
    q = _q(description="A wireless charging pad that detects foreign metal "
                       "objects and suspends inductive power transfer.")
    cands = rank_rows(_rows(), q)
    cands = apply_semantic(cands, _rows(), q)
    assert all(c.semantic is not None and c.combined is not None for c in cands)
    combined = [c.combined for c in cands]
    assert combined == sorted(combined, reverse=True)
    # the FOD wireless-charger family should outrank the thermal-pad patent
    nums = [c.publication_number for c in cands]
    assert nums.index("US-9500000-B2") < nums.index("US-11000001-B2")


def test_divergence_flags_needs_review():
    # High keyword score but a semantically unrelated description → 乖離.
    q = _q(description="織機の杼替え機構における緯糸切断装置")  # unrelated JP text
    cands = rank_rows(_rows(), q)
    cands = apply_semantic(cands, _rows(), q)
    top_kw = max(cands, key=lambda c: c.score)
    assert top_kw.needs_review
    assert any("チャネル乖離" in n for n in top_kw.notes)


def test_brief_query_text_prefers_description():
    q = _q(description="free text wins")
    assert brief_query_text(q) == "free text wins"
    assert "wireless power" in brief_query_text(_q())


# ---- embedder seam ----------------------------------------------------------

class _FakeEmbeddingItem:
    def __init__(self, index, embedding):
        self.index, self.embedding = index, embedding


class _FakeClient:
    """Mimics openai client.embeddings.create, returns out-of-order data."""

    class _E:
        @staticmethod
        def create(model, input):  # noqa: A002 — mirrors the API signature
            vecs = {
                0: [1.0, 0.0],     # query
                1: [0.0, 1.0],     # orthogonal doc
                2: [1.0, 0.0],     # identical doc
            }
            data = [_FakeEmbeddingItem(i, vecs[i]) for i in reversed(range(len(input)))]
            return type("R", (), {"data": data})()

    embeddings = _E()


def test_api_embedder_orders_by_index_and_scores():
    emb = OpenAICompatibleEmbedder(_FakeClient(), model="fake")
    scores = semantic_scores("q", ["different", "same"], embedder=emb)
    assert scores[1] > scores[0]
    assert abs(scores[1] - 1.0) < 1e-9


def test_make_embedder_from_env_none_when_unset(monkeypatch):
    for var in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
                "AZURE_OPENAI_EMBED_DEPLOYMENT", "GITHUB_MODELS_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert make_embedder_from_env("auto") is None
    assert make_embedder_from_env("azure") is None
