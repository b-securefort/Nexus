"""Token accounting for the context-usage gauge.

The gauge is a **context-window occupancy** indicator — "how full is the model's
window right now" — not a cumulative token-spend meter. See DESIGN.md §5
2026-06-05 "Context-usage gauge: occupancy, not spend".

The API `usage` object only exposes three totals (prompt / completion / cached),
which can't tell you *what* is filling the window. To get the structural
breakdown (system prompt vs tools vs KB vs learnings vs messages) we locally
tokenize each prompt segment with tiktoken, then scale the per-segment counts so
they sum to the API-reported `prompt_tokens`. The API total stays authoritative
(tiktoken is an estimate for Azure GPT models and ignores message-envelope
overhead); the local counts only apportion that total into useful categories.

Completion tokens are deliberately absent: output is not context occupancy.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Known base-model → context window. Azure deployment names often embed the base
# model name, so we match by substring. Falls back to the configured default
# when nothing matches, so a new deployment never silently reports 0.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5.4": 128_000,
    "gpt-5": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4-32k": 32_768,
    "gpt-4": 8_192,
    "gpt-35-turbo-16k": 16_384,
    "gpt-35-turbo": 16_384,
}


def context_window_for_model(model: str, default: int) -> int:
    """Resolve the context window for a model/deployment name.

    Substring match against known base models so the denominator tracks the
    actual model instead of a hardcoded constant. `default` (the configured
    value) is the fallback for unknown deployments.
    """
    m = (model or "").lower()
    for key, window in _MODEL_CONTEXT_WINDOWS.items():
        if key in m:
            return window
    return default


@lru_cache(maxsize=8)
def _encoding_for(model: str):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        # Azure deployment names (and newer models) aren't in tiktoken's
        # registry. o200k_base is the GPT-4o / GPT-5 family tokenizer and the
        # right default for our deployments.
        return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str, model: str) -> int:
    """Token count for a string. Degrades to a ~4-chars/token estimate if
    tiktoken is unavailable, so the gauge never hard-fails on a missing dep."""
    if not text:
        return 0
    try:
        return len(_encoding_for(model).encode(text))
    except Exception as e:
        logger.debug("tiktoken unavailable, using char estimate: %s", e)
        return max(1, len(text) // 4)


def _messages_text(messages: list[dict]) -> str:
    """Flatten chat history into countable text. Approximate: string content,
    text parts of multimodal content, and serialized tool-call payloads all
    contribute; image bytes are skipped (they're not text tokens)."""
    chunks: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    chunks.append(part.get("text", ""))
        if m.get("tool_calls"):
            chunks.append(json.dumps(m["tool_calls"]))
    return "\n".join(chunks)


def build_segments(
    *,
    system_segments: dict[str, str],
    tool_schemas: list[dict] | None,
    messages: list[dict],
    model: str,
    prompt_tokens: int,
) -> list[dict]:
    """Build the reconciled, input-side occupancy breakdown.

    `system_segments` is an ordered {display_label: text} map of the composed
    system prompt's parts. `Tools` and `Messages` are appended. Each segment is
    tokenized locally, then all are scaled so they sum exactly to
    `prompt_tokens` (the authoritative API total). Empty segments are dropped.

    Returns an ordered list of `{"label": str, "tokens": int}`.
    """
    raw: list[tuple[str, int]] = [
        (label, count_tokens(text, model)) for label, text in system_segments.items()
    ]
    raw.append(("Tools", count_tokens(json.dumps(tool_schemas) if tool_schemas else "", model)))
    raw.append(("Messages", count_tokens(_messages_text(messages), model)))

    raw_total = sum(t for _, t in raw)
    if raw_total <= 0 or prompt_tokens <= 0:
        # No basis to apportion — return whatever non-empty raw counts we have.
        return [{"label": label, "tokens": t} for label, t in raw if t > 0]

    scaled: list[dict] = []
    running = 0
    for label, t in raw:
        tok = round(t / raw_total * prompt_tokens)
        scaled.append({"label": label, "tokens": tok})
        running += tok

    # Push the rounding remainder onto the largest segment so the breakdown
    # sums exactly to the authoritative prompt_tokens.
    drift = prompt_tokens - running
    if scaled and drift:
        max(scaled, key=lambda s: s["tokens"])["tokens"] += drift

    return [s for s in scaled if s["tokens"] > 0]
