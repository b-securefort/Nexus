"""Tests for the context-usage gauge token accounting (app/agent/token_usage.py)."""
from app.agent.token_usage import (
    build_segments,
    context_window_for_model,
    count_tokens,
)


class TestContextWindowForModel:
    def test_known_model_substring_match(self):
        assert context_window_for_model("gpt-5.4-mini", default=1) == 128_000
        assert context_window_for_model("my-gpt-4.1-deploy", default=1) == 1_047_576

    def test_unknown_model_falls_back_to_default(self):
        assert context_window_for_model("llama-3-70b", default=99_999) == 99_999

    def test_empty_model_falls_back(self):
        assert context_window_for_model("", default=42) == 42


class TestCountTokens:
    def test_empty_is_zero(self):
        assert count_tokens("", "gpt-5.4-mini") == 0

    def test_nonempty_is_positive(self):
        assert count_tokens("hello world", "gpt-5.4-mini") > 0


class TestBuildSegments:
    def _payload(self, prompt_tokens):
        return build_segments(
            system_segments={
                "System prompt": "You are a helpful assistant. " * 50,
                "Knowledge base": "kb index entry " * 100,
            },
            tool_schemas=[{"type": "function", "function": {"name": "az_cli", "parameters": {}}}],
            messages=[
                {"role": "user", "content": "deploy an aks cluster"},
                {"role": "assistant", "content": "Sure, here's how. " * 40},
            ],
            model="gpt-5.4-mini",
            prompt_tokens=prompt_tokens,
        )

    def test_segments_sum_exactly_to_prompt_tokens(self):
        segs = self._payload(10_000)
        assert sum(s["tokens"] for s in segs) == 10_000

    def test_includes_tools_and_messages_categories(self):
        labels = {s["label"] for s in self._payload(10_000)}
        assert "Tools" in labels
        assert "Messages" in labels
        assert "System prompt" in labels

    def test_completion_never_appears(self):
        # The gauge is occupancy-only; output tokens must not be a category.
        labels = {s["label"] for s in self._payload(10_000)}
        assert "Completion" not in labels

    def test_empty_segments_dropped(self):
        segs = build_segments(
            system_segments={"System prompt": "hi", "Knowledge base": ""},
            tool_schemas=None,
            messages=[],
            model="gpt-5.4-mini",
            prompt_tokens=500,
        )
        # Empty KB / no tools / no messages should not produce zero-token rows.
        assert all(s["tokens"] > 0 for s in segs)

    def test_zero_prompt_tokens_does_not_crash(self):
        # Defensive: API reported no usage. Should not raise or divide by zero.
        segs = self._payload(0)
        assert isinstance(segs, list)
