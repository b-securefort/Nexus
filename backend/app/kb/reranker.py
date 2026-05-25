"""
LLM-judge re-ranker for hybrid KB search.

Takes the top-K RRF candidates from `hybrid_search` and asks the chat
deployment to score each chunk's relevance to the query on a 0.0-1.0 scale.
The rerank score is calibrated to relevance (not to vector geometry), so
confidence thresholds derived from it transfer across corpora — unlike raw
cosine distance, which is corpus-dependent.

Design notes:
- Synchronous. Single LLM call per search; cost ~1300 input + 200 output
  tokens for a typical top-10 rerank against gpt-5.4-mini.
- Graceful fallback: any parse/API failure returns the input hits unchanged,
  preserving the existing RRF ordering and the distance-based confidence
  signal as a backstop.
- The judge sees only the chunk snippet (first 400 chars per chunk, what
  hybrid_search already returns). No additional DB reads.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from openai import AzureOpenAI

from app.agent.circuit_breaker import (
    CircuitOpenError,
    check as cb_check,
    record_failure as cb_record_failure,
    record_success as cb_record_success,
)
from app.config import get_settings

if TYPE_CHECKING:
    from app.kb.vector_store import SearchHit

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are a search relevance judge. For each document chunk, decide how "
    "well it answers the user's search query, on a continuous 0.0-1.0 scale:\n"
    "  1.0  - directly contains the answer\n"
    "  0.7-0.9 - highly relevant, contains key supporting info\n"
    "  0.4-0.6 - tangentially related, mentions the topic but doesn't answer\n"
    "  0.1-0.3 - same broad domain, not actually useful for this query\n"
    "  0.0  - unrelated\n"
    "Be strict. Most chunks in a result set are not direct answers; the "
    "scores should reflect that.\n\n"
    "Return a JSON object with this exact shape, containing one entry per "
    "chunk in the order given:\n"
    '  {"scores": [{"index": 1, "score": 0.0}, {"index": 2, "score": 0.0}, ...]}\n'
    "Do NOT return a bare array. Do NOT return a single object. The top-level "
    "must be {\"scores\": [...]}. Include every chunk."
)


def _get_client() -> tuple[AzureOpenAI, str]:
    s = get_settings()
    client = AzureOpenAI(
        azure_endpoint=s.AZURE_OPENAI_ENDPOINT,
        api_key=s.AZURE_OPENAI_API_KEY,
        api_version=s.AZURE_OPENAI_API_VERSION,
        timeout=s.AOAI_TIMEOUT_SECONDS,
    )
    return client, s.AZURE_OPENAI_DEPLOYMENT


_JUDGE_CONTEXT_CHARS = 2000  # per chunk; chunks longer than this get tail-truncated


def _build_user_message(query: str, hits: list["SearchHit"]) -> str:
    chunk_lines = []
    for i, h in enumerate(hits, start=1):
        heading = (h.heading or "(no heading)")[:120]
        # Use the full chunk text (not the 400-char preview snippet) so the
        # judge can see content past the first paragraph. Without this the
        # judge scores 0.0 for chunks whose key sentence sits past char 400.
        full = (h.text or h.snippet or "").strip()
        if len(full) > _JUDGE_CONTEXT_CHARS:
            full = full[:_JUDGE_CONTEXT_CHARS] + "..."
        chunk_lines.append(f"[{i}] {h.kb_path}  >>  {heading}\n{full}")
    chunks_block = "\n\n---\n\n".join(chunk_lines)
    return f"QUERY: {query}\n\nCHUNKS:\n{chunks_block}"


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_scores(raw: str, n_expected: int) -> list[float] | None:
    """Parse the judge's JSON output into a list of scores aligned with input order.

    Returns None on any parse failure so the caller can fall back to RRF order.
    Tolerates: surrounding code fences, leading "json", missing indices, scores
    outside [0,1] (clamped), extra fields.
    """
    # Strip code fences and "json" preamble.
    s = raw.strip().strip("`").strip()
    if s.lower().startswith("json"):
        s = s[4:].strip()
    # If the model wrapped the array in prose, extract the array literal.
    m = _JSON_ARRAY_RE.search(s)
    if not m:
        logger.warning("rerank parse: no JSON array found in %r", raw[:200])
        return None
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.warning("rerank parse: JSON decode failed (%s) in %r", e, raw[:200])
        return None

    if not isinstance(items, list):
        return None

    scores: list[float] = [0.0] * n_expected
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        score = item.get("score")
        if not isinstance(idx, int) or not isinstance(score, (int, float)):
            continue
        zero_based = idx - 1
        if 0 <= zero_based < n_expected and zero_based not in seen:
            scores[zero_based] = max(0.0, min(1.0, float(score)))
            seen.add(zero_based)

    if not seen:
        logger.warning("rerank parse: zero usable score entries in %r", raw[:200])
        return None
    return scores


def rerank_hits(query: str, hits: list["SearchHit"]) -> list["SearchHit"]:
    """Score each hit with an LLM judge and return hits sorted best-first.

    Mutates each hit's `rerank_score` and `confidence` in place. On any
    failure the input list is returned unchanged (RRF order preserved).
    """
    if not hits or not query.strip():
        return hits

    settings = get_settings()
    if not settings.KB_RERANK_ENABLED:
        return hits

    top_k = min(settings.KB_RERANK_TOP_K, len(hits))
    judged = hits[:top_k]
    rest = hits[top_k:]

    # Participate in the shared AOAI circuit breaker (see §5 2026-05-21).
    # When AOAI is collectively known-bad across the four other call sites,
    # this short-circuits the rerank call without burning the SDK timeout.
    # When rerank itself fails, it contributes to the shared failure counter.
    try:
        cb_check()
        client, deployment = _get_client()
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(query, judged)},
            ],
            # gpt-5.4+ deployments require max_completion_tokens; older
            # models accepted max_tokens. We pass the new name.
            max_completion_tokens=400,
            response_format={"type": "json_object"},
        )
        cb_record_success()
    except CircuitOpenError:
        logger.info("KB rerank skipped: AOAI circuit breaker open — using RRF order")
        return hits
    except Exception as e:
        cb_record_failure()
        logger.warning("KB rerank LLM call failed (%s) - falling back to RRF order", e)
        return hits

    # Some endpoints don't honour response_format and return prose; try both
    # the array-direct shape and a wrapped object that contains the array.
    raw = resp.choices[0].message.content or ""
    if raw.lstrip().startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                # Common wrapping keys we tolerate.
                for key in ("scores", "results", "items", "ranking", "data"):
                    if key in obj and isinstance(obj[key], list):
                        raw = json.dumps(obj[key])
                        break
        except json.JSONDecodeError:
            pass

    scores = _parse_scores(raw, n_expected=len(judged))
    if scores is None:
        return hits

    # Assign scores + confidence; sort the judged segment, append the rest.
    high_thr = settings.KB_RERANK_HIGH_THRESHOLD
    med_thr = settings.KB_RERANK_MEDIUM_THRESHOLD
    for hit, score in zip(judged, scores):
        hit.rerank_score = score
        hit.confidence = (
            "high" if score >= high_thr
            else "medium" if score >= med_thr
            else "low"
        )

    judged.sort(key=lambda h: h.rerank_score if h.rerank_score is not None else -1, reverse=True)
    return judged + rest
