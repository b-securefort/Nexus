"""
Unit tests for app.kb.reranker.

Mocks Azure OpenAI so no network calls are made. Covers happy path,
parsing of various LLM output shapes (raw array, fenced, object-wrapped,
prose-prefixed), score clamping, confidence thresholding, and graceful
fallback to RRF order on API or parse failure.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.kb.reranker import _parse_scores, rerank_hits
from app.kb.vector_store import SearchHit


# ── Fixtures ────────────────────────────────────────────────────────────────

def _mk_hit(path: str, score: float = 0.02, sources_hit: int = 1) -> SearchHit:
    return SearchHit(
        kb_path=path,
        chunk_idx=0,
        heading=f"Heading for {path}",
        snippet=f"Snippet text for {path}",
        text=f"Full chunk text for {path}",
        source_url=None,
        score=score,
        sources_hit=sources_hit,
        vec_distance=0.5,
        confidence="medium",
        rerank_score=None,
    )


def _mock_completion(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.content = content
    return resp


def _patch_settings(**overrides):
    """Returns a context manager patching get_settings() with the given overrides."""
    settings = MagicMock()
    settings.AZURE_OPENAI_ENDPOINT = "https://x"
    settings.AZURE_OPENAI_API_KEY = "k"
    settings.AZURE_OPENAI_API_VERSION = "v"
    settings.AZURE_OPENAI_DEPLOYMENT = "gpt"
    settings.AOAI_TIMEOUT_SECONDS = 30
    settings.KB_RERANK_ENABLED = True
    settings.KB_RERANK_TOP_K = 10
    settings.KB_RERANK_HIGH_THRESHOLD = 0.70
    settings.KB_RERANK_MEDIUM_THRESHOLD = 0.40
    for k, v in overrides.items():
        setattr(settings, k, v)
    return patch("app.kb.reranker.get_settings", return_value=settings)


# ── _parse_scores ───────────────────────────────────────────────────────────

class TestParseScores:
    def test_clean_array(self):
        raw = '[{"index": 1, "score": 0.9}, {"index": 2, "score": 0.3}]'
        assert _parse_scores(raw, 2) == [0.9, 0.3]

    def test_with_code_fence(self):
        raw = '```json\n[{"index": 1, "score": 0.8}]\n```'
        assert _parse_scores(raw, 1) == [0.8]

    def test_with_prose_prefix(self):
        raw = 'Here are the scores:\n[{"index": 1, "score": 0.5}]'
        assert _parse_scores(raw, 1) == [0.5]

    def test_clamps_above_one(self):
        raw = '[{"index": 1, "score": 1.5}]'
        assert _parse_scores(raw, 1) == [1.0]

    def test_clamps_below_zero(self):
        raw = '[{"index": 1, "score": -0.2}]'
        assert _parse_scores(raw, 1) == [0.0]

    def test_missing_indices_become_zero(self):
        raw = '[{"index": 1, "score": 0.9}, {"index": 3, "score": 0.5}]'
        # Index 2 was not scored; should default to 0.0.
        assert _parse_scores(raw, 3) == [0.9, 0.0, 0.5]

    def test_garbage_returns_none(self):
        assert _parse_scores("not json at all", 1) is None

    def test_empty_array_returns_none(self):
        assert _parse_scores("[]", 1) is None

    def test_non_array_returns_none(self):
        assert _parse_scores('{"foo": "bar"}', 1) is None

    def test_skips_malformed_items(self):
        raw = '[{"index": 1, "score": 0.7}, {"bad": "shape"}, {"index": 3, "score": 0.2}]'
        assert _parse_scores(raw, 3) == [0.7, 0.0, 0.2]

    def test_ignores_out_of_range_index(self):
        raw = '[{"index": 99, "score": 0.9}]'
        assert _parse_scores(raw, 2) is None  # nothing usable


# ── rerank_hits ─────────────────────────────────────────────────────────────

class TestRerankHits:
    def test_returns_empty_for_empty_hits(self):
        assert rerank_hits("query", []) == []

    def test_returns_empty_query_unchanged(self):
        hits = [_mk_hit("a.md")]
        assert rerank_hits("   ", hits) is hits

    def test_disabled_returns_unchanged(self):
        hits = [_mk_hit("a.md"), _mk_hit("b.md")]
        with _patch_settings(KB_RERANK_ENABLED=False):
            result = rerank_hits("query", hits)
        assert result is hits
        assert all(h.rerank_score is None for h in hits)

    def test_happy_path_reorders_by_score(self):
        hits = [_mk_hit("low.md"), _mk_hit("high.md"), _mk_hit("mid.md")]
        # Judge says: position 1 (low.md) is 0.2, position 2 (high.md) is 0.9, position 3 (mid.md) is 0.5
        resp = _mock_completion('[{"index":1,"score":0.2},{"index":2,"score":0.9},{"index":3,"score":0.5}]')
        with _patch_settings(), patch("app.kb.reranker.AzureOpenAI") as MockClient:
            MockClient.return_value.chat.completions.create.return_value = resp
            result = rerank_hits("query", hits)
        assert [h.kb_path for h in result] == ["high.md", "mid.md", "low.md"]
        assert result[0].rerank_score == 0.9
        assert result[0].confidence == "high"
        assert result[1].confidence == "medium"
        assert result[2].confidence == "low"

    def test_api_error_falls_back_to_input_order(self):
        hits = [_mk_hit("a.md", score=0.03), _mk_hit("b.md", score=0.02)]
        with _patch_settings(), patch("app.kb.reranker.AzureOpenAI") as MockClient:
            MockClient.return_value.chat.completions.create.side_effect = RuntimeError("boom")
            result = rerank_hits("query", hits)
        assert result is hits
        # rerank_score must remain None so callers know rerank didn't apply.
        assert all(h.rerank_score is None for h in hits)

    def test_unparseable_response_falls_back(self):
        hits = [_mk_hit("a.md"), _mk_hit("b.md")]
        resp = _mock_completion("the model decided to write prose instead of JSON")
        with _patch_settings(), patch("app.kb.reranker.AzureOpenAI") as MockClient:
            MockClient.return_value.chat.completions.create.return_value = resp
            result = rerank_hits("query", hits)
        assert result is hits

    def test_object_wrapped_response_is_unwrapped(self):
        hits = [_mk_hit("a.md"), _mk_hit("b.md")]
        # Some chat deployments wrap JSON outputs in {"scores": [...]}
        resp = _mock_completion('{"scores":[{"index":1,"score":0.8},{"index":2,"score":0.3}]}')
        with _patch_settings(), patch("app.kb.reranker.AzureOpenAI") as MockClient:
            MockClient.return_value.chat.completions.create.return_value = resp
            result = rerank_hits("query", hits)
        assert result[0].kb_path == "a.md"
        assert result[0].rerank_score == 0.8
        assert result[1].rerank_score == 0.3

    def test_only_top_k_are_judged(self):
        # 5 hits but top_k=2 — only first 2 are scored, rest pass through.
        hits = [_mk_hit(f"{i}.md") for i in range(5)]
        # Judge inverts top-2.
        resp = _mock_completion('[{"index":1,"score":0.2},{"index":2,"score":0.9}]')
        with _patch_settings(KB_RERANK_TOP_K=2), patch("app.kb.reranker.AzureOpenAI") as MockClient:
            MockClient.return_value.chat.completions.create.return_value = resp
            result = rerank_hits("query", hits)
        # Top-2 swapped, tail (2,3,4) preserved.
        assert [h.kb_path for h in result] == ["1.md", "0.md", "2.md", "3.md", "4.md"]
        # Hits beyond top_k should not have rerank_score.
        assert result[2].rerank_score is None

    def test_confidence_threshold_boundaries(self):
        hits = [_mk_hit("a.md"), _mk_hit("b.md"), _mk_hit("c.md")]
        # Scores exactly at the thresholds.
        resp = _mock_completion(
            '[{"index":1,"score":0.70},{"index":2,"score":0.40},{"index":3,"score":0.39}]'
        )
        with _patch_settings(), patch("app.kb.reranker.AzureOpenAI") as MockClient:
            MockClient.return_value.chat.completions.create.return_value = resp
            result = rerank_hits("query", hits)
        # Sorted by score; check the assigned confidence regardless of order.
        by_path = {h.kb_path: h for h in result}
        assert by_path["a.md"].confidence == "high"     # 0.70 >= 0.70
        assert by_path["b.md"].confidence == "medium"   # 0.40 >= 0.40
        assert by_path["c.md"].confidence == "low"      # 0.39 < 0.40
