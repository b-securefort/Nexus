"""
Conversation history compaction.

Token economics in this app are wildly asymmetric: user messages are short
intent anchors (~20-200 tokens each), tool outputs are long scaffolding
(thousands of tokens each). So this module compacts asymmetrically too:

  * EVERY user message in the window is preserved verbatim, regardless of
    age, with two compression exceptions:
    - Long pastes (> USER_PASTE_THRESHOLD chars) get a high-quality LLM
      summary that keeps intent, names, IDs, paths, error codes.
    - Image attachments on older user messages are replaced with a cached
      vision-LLM description; only the MOST RECENT image-bearing user
      message keeps its actual images for the vision model to read.
    Both exceptions are skipped for the *latest* user message overall, so
    the agent always sees the live ask in full.

  * Assistant + tool messages (the "scaffolding") between user messages
    in the older portion are collapsed into per-gap outcome bullets via a
    single synthetic assistant message. Recent scaffolding stays verbatim.

The Conversation.summary_text cache stores a high-level cumulative outcome
summary so re-summarization is only paid when new scaffolding accumulates.
Per-message text_summary / image_summary caches store one-time compression
results so a given old message is only compressed once across the
conversation's lifetime.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from openai import AzureOpenAI
from sqlmodel import Session, select

from app.agent.circuit_breaker import check as cb_check, record_failure as cb_failure, record_success as cb_success
from app.config import get_settings
from app.db.models import Conversation, Message

logger = logging.getLogger(__name__)

# Outer cap on how many rows past summary_through_message_id we ever pull.
MAX_HISTORY_MESSAGES = 50
# Target size of the recent verbatim window (in messages). Scaffolding
# older than this gets compressed into outcome bullets.
RECENT_KEEP_COUNT = 15
# Compaction triggers when EITHER threshold is exceeded.
COMPACT_THRESHOLD_MESSAGES = 30
COMPACT_THRESHOLD_CHARS = 12_000
# User messages with text content longer than this get a high-quality LLM
# summary (cached on Message.text_summary). Latest user message is exempt.
USER_PASTE_THRESHOLD = 3_000

_SUMMARY_PREFIX = "[Summary of earlier conversation]\n"
_SCAFFOLD_PREFIX = "[Outcomes from intermediate tool work]\n"


# ── Estimation helpers ───────────────────────────────────────────────────

def _estimate_chars(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", ""))
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    # Conservative estimate for an inlined image
                    total += 2_000
        if m.get("tool_calls"):
            total += len(json.dumps(m["tool_calls"]))
    return total


# ── LLM-backed compression primitives ────────────────────────────────────

def _summarize_long_paste(
    text: str, client: AzureOpenAI, deployment: str
) -> str:
    """High-quality summary of a long user paste — preserves intent,
    specific names/IDs/paths/error codes/parameters. Keeps the *meaning*
    rather than paraphrasing the prose."""
    system_prompt = (
        "You compress a single long user message into a concise summary "
        "while preserving: (1) the user's intent / what they're asking, "
        "(2) ALL specific names, IDs, paths, resource references, error "
        "codes, parameters, and (3) key technical details. "
        "Do not paraphrase intent — keep the user's wording for any direct "
        "ask. Output ONLY the summary, no preamble. Keep under 800 characters."
    )
    settings = get_settings()
    cb_check()
    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=300,
            temperature=0,
            timeout=float(settings.AOAI_TIMEOUT_SECONDS),
        )
        cb_success()
    except Exception:
        cb_failure()
        raise
    return (resp.choices[0].message.content or "").strip()


def _describe_image(
    image_path: Path,
    content_type: str,
    client: AzureOpenAI,
    deployment: str,
) -> str:
    """Vision-LLM call returning a precise description of an image — text,
    structure, diagram nodes, visible details. Used to replace the image
    bytes in older user messages."""
    if not image_path.is_file():
        return f"[image {image_path.name}: file not found]"
    try:
        data = image_path.read_bytes()
    except OSError:
        return f"[image {image_path.name}: read error]"
    if not data:
        return f"[image {image_path.name}: empty file]"

    b64 = base64.b64encode(data).decode("ascii")
    settings = get_settings()
    cb_check()
    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Describe this image precisely so it can stand in for the "
                        "image in a future chat history. Capture: any visible text, "
                        "diagram structure (nodes/connections/labels), key visual "
                        "elements, technical details. Be specific. No preamble. "
                        "Keep under 600 characters."
                    )},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{content_type};base64,{b64}",
                        "detail": "auto",
                    }},
                ],
            }],
            max_completion_tokens=250,
            temperature=0,
            timeout=float(settings.AOAI_TIMEOUT_SECONDS),
        )
        cb_success()
    except Exception:
        cb_failure()
        raise
    return (resp.choices[0].message.content or "").strip()


# ── Per-row compression with cache ───────────────────────────────────────

def _get_or_create_text_summary(
    row: Message,
    session: Session,
    client: AzureOpenAI,
    deployment: str,
    *,
    deferred: list[Callable[[], None]] | None = None,
) -> str:
    """Return a summary of the message text.

    Cache hit  → return immediately (no LLM call).
    Cache miss + deferred provided → schedule the LLM call as a background
      job, return truncated raw for this turn. The cached summary will be
      picked up on the next turn.
    Cache miss + no deferred list → fall back to synchronous LLM call
      (used outside the critical path, e.g. tests).
    """
    if row.text_summary:
        return row.text_summary

    if deferred is not None:
        # Defer the LLM call — use raw truncated content for this turn.
        # The closure captures the session; SQLAlchemy sessions are reusable
        # after close, so the background task will re-acquire a connection
        # from the pool even if the original request is already done.
        msg_id = row.id
        raw = row.content or ""

        def _do_text_summary(
            _session=session, _msg_id=msg_id, _raw=raw
        ) -> None:
            try:
                summary = _summarize_long_paste(_raw, client, deployment)
            except Exception as exc:
                logger.warning("BG text_summary failed for msg %s: %s", _msg_id, exc)
                return
            if not summary:
                return
            bg_row = _session.get(Message, _msg_id)
            if bg_row and not bg_row.text_summary:
                bg_row.text_summary = summary
                _session.add(bg_row)
                _session.commit()
                logger.info(
                    "BG cached text_summary for msg %s (%d → %d chars)",
                    _msg_id, len(_raw), len(summary),
                )

        deferred.append(_do_text_summary)
        return raw[:USER_PASTE_THRESHOLD] + " …[summarizing in background]"

    # Synchronous fallback (no deferred list supplied)
    try:
        summary = _summarize_long_paste(row.content or "", client, deployment)
    except Exception as e:
        logger.warning("Long-paste summary failed for msg %s: %s", row.id, e)
        return (row.content or "")[:USER_PASTE_THRESHOLD] + " …[truncated]"
    if not summary:
        return (row.content or "")[:USER_PASTE_THRESHOLD] + " …[truncated]"
    row.text_summary = summary
    session.add(row)
    session.commit()
    logger.info("Cached text_summary for msg %s (%d → %d chars)", row.id, len(row.content or ""), len(summary))
    return summary


def _get_or_create_image_summary(
    row: Message,
    session: Session,
    client: AzureOpenAI,
    deployment: str,
    *,
    deferred: list[Callable[[], None]] | None = None,
) -> str:
    """Return a vision-LLM description of the message's image attachments.

    Cache hit  → return immediately (no LLM call).
    Cache miss + deferred provided → schedule the vision call as a background
      job, return an empty placeholder for this turn. The cached description
      will be picked up on the next turn.
    Cache miss + no deferred list → synchronous LLM call (fallback/tests).
    """
    if row.image_summary:
        return row.image_summary

    settings = get_settings()
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    try:
        attachments = json.loads(row.attachments_json or "[]")
    except json.JSONDecodeError:
        attachments = []

    if not attachments:
        return ""

    if deferred is not None:
        # Defer the vision LLM call — return empty for this turn.
        # The session is captured and reused by the background task.
        msg_id = row.id
        snapshot_atts = list(attachments)  # capture for closure

        def _do_image_summary(
            _session=session, _msg_id=msg_id, _atts=snapshot_atts,
            _upload_dir=upload_dir,
        ) -> None:
            descriptions: list[str] = []
            for att in _atts:
                fname = att.get("filename", "")
                ctype = att.get("content_type", "image/png")
                try:
                    desc = _describe_image(_upload_dir / fname, ctype, client, deployment)
                except Exception as exc:
                    logger.warning("BG image description failed for %s: %s", fname, exc)
                    desc = "description unavailable"
                descriptions.append(f"- {fname}: {desc}")
            summary = "\n".join(descriptions)
            if not summary:
                return
            bg_row = _session.get(Message, _msg_id)
            if bg_row and not bg_row.image_summary:
                bg_row.image_summary = summary
                _session.add(bg_row)
                _session.commit()
                logger.info(
                    "BG cached image_summary for msg %s (%d attachments)",
                    _msg_id, len(_atts),
                )

        deferred.append(_do_image_summary)
        return ""  # placeholder — summary populated on next turn

    # Synchronous fallback
    descriptions: list[str] = []
    for att in attachments:
        filename = att.get("filename", "")
        content_type = att.get("content_type", "image/png")
        try:
            desc = _describe_image(
                upload_dir / filename, content_type, client, deployment,
            )
        except Exception as e:
            logger.warning("Image description failed for %s: %s", filename, e)
            desc = "description unavailable"
        descriptions.append(f"- {filename}: {desc}")

    summary = "\n".join(descriptions)
    row.image_summary = summary
    session.add(row)
    session.commit()
    logger.info("Cached image_summary for msg %s (%d attachments)", row.id, len(attachments))
    return summary


# ── Multipart content for messages with live images ──────────────────────

def _build_content_with_images(text: str, attachments_json: str) -> list[dict]:
    """Build OpenAI multi-part content from text + stored image attachments."""
    settings = get_settings()
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})

    try:
        attachments = json.loads(attachments_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse attachments_json in compaction loader")
        return parts or [{"type": "text", "text": text}]

    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    for att in attachments:
        filename = att.get("filename", "")
        content_type = att.get("content_type", "image/png")
        file_path = upload_dir / filename
        if not file_path.is_file():
            continue
        try:
            data = file_path.read_bytes()
        except OSError:
            continue
        if not data:
            continue
        b64 = base64.b64encode(data).decode("ascii")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{content_type};base64,{b64}", "detail": "auto"},
        })

    return parts if parts else [{"type": "text", "text": text}]


# ── Row → message conversion ─────────────────────────────────────────────

def _row_to_message(
    row: Message,
    session: Session,
    client: AzureOpenAI,
    deployment: str,
    *,
    is_latest_user: bool,
    is_latest_image_owner: bool,
    deferred: list[Callable[[], None]] | None = None,
) -> dict:
    """Convert a DB row into an OpenAI message dict, applying compression
    rules for older user messages (long pastes → text_summary, images →
    image_summary). The latest user message and the latest image-bearing
    user message are always rendered in full."""
    msg: dict = {"role": row.role, "content": row.content}

    if row.role == "assistant" and row.tool_calls_json:
        try:
            tcs = json.loads(row.tool_calls_json)
            if tcs:
                msg["tool_calls"] = tcs
        except json.JSONDecodeError:
            pass

    if row.role == "tool":
        msg["tool_call_id"] = row.tool_call_id or ""
        return msg

    if row.role != "user":
        return msg

    # User-message-specific compression
    text = row.content or ""
    if not is_latest_user and len(text) > USER_PASTE_THRESHOLD:
        text = _get_or_create_text_summary(row, session, client, deployment, deferred=deferred)

    if row.attachments_json:
        if is_latest_image_owner:
            msg["content"] = _build_content_with_images(text, row.attachments_json)
        else:
            img_summary = _get_or_create_image_summary(row, session, client, deployment, deferred=deferred)
            if img_summary:
                msg["content"] = f"{text}\n\n[Previously attached image(s):]\n{img_summary}"
            else:
                msg["content"] = text
    else:
        msg["content"] = text

    return msg


# ── Orphan cleanup ───────────────────────────────────────────────────────

def _clean_orphans(messages: list[dict]) -> list[dict]:
    """Drop tool-role messages whose assistant parent isn't present, and
    strip tool_calls from assistant messages whose responses are missing."""
    valid_tc_ids: set[str] = set()
    answered_tc_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant" and "tool_calls" in m:
            for tc in m["tool_calls"]:
                tc_id = tc.get("id") or ""
                if tc_id:
                    valid_tc_ids.add(tc_id)
        if m.get("role") == "tool":
            tc_id = m.get("tool_call_id", "")
            if tc_id:
                answered_tc_ids.add(tc_id)

    cleaned: list[dict] = []
    for m in messages:
        if m.get("role") == "tool":
            if m.get("tool_call_id", "") not in valid_tc_ids:
                continue
        if m.get("role") == "assistant" and "tool_calls" in m:
            kept = [
                tc for tc in m["tool_calls"]
                if (tc.get("id") or "") in answered_tc_ids
            ]
            if not kept:
                m = {k: v for k, v in m.items() if k != "tool_calls"}
            else:
                m = {**m, "tool_calls": kept}
        cleaned.append(m)
    return cleaned


# ── Scaffolding-between-user-messages compression ────────────────────────

def _render_scaffold_for_summary(scaffold: list[dict]) -> str:
    lines = []
    for m in scaffold:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                (p.get("text", "[image]") if p.get("type") == "text" else "[image]")
                for p in content if isinstance(p, dict)
            )
        if m.get("tool_calls"):
            names = ", ".join(
                tc.get("function", {}).get("name", "?") for tc in m["tool_calls"]
            )
            content = f"{content} [called: {names}]"
        if len(content) > 2000:
            content = content[:2000] + " …[truncated for summary input]"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _summarize_scaffold(
    scaffold: list[dict],
    client: AzureOpenAI,
    deployment: str,
) -> str:
    """Compress a run of assistant + tool messages into outcome bullets."""
    rendered = _render_scaffold_for_summary(scaffold)
    system_prompt = (
        "You compress a sequence of assistant reasoning and tool calls into "
        "OUTCOME BULLETS. Capture: (1) which resources/files were inspected "
        "and the conclusion (e.g. 'queried RG X, found 3 storage accounts'), "
        "(2) decisions or facts established, (3) errors hit and how they were "
        "resolved, (4) the final state at the end of this segment. "
        "Be specific (resource names, IDs, paths). Drop chatter. "
        "Output ONLY the bullet list, no preamble. Keep under 800 characters."
    )
    settings = get_settings()
    cb_check()
    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": rendered},
            ],
            max_completion_tokens=350,
            temperature=0,
            timeout=float(settings.AOAI_TIMEOUT_SECONDS),
        )
        cb_success()
    except Exception:
        cb_failure()
        raise
    return (resp.choices[0].message.content or "").strip()


def _compress_older_scaffolding(
    older: list[dict],
    client: AzureOpenAI,
    deployment: str,
) -> list[dict]:
    """Walk older messages: preserve user messages verbatim, collapse each
    run of assistant + tool messages between user messages into one
    synthetic assistant outcome message."""
    if not older:
        return []

    result: list[dict] = []
    scaffold: list[dict] = []

    def flush():
        if not scaffold:
            return
        try:
            summary = _summarize_scaffold(scaffold, client, deployment)
        except Exception as e:
            logger.warning("Scaffold summarize failed: %s — keeping verbatim", e)
            result.extend(scaffold)
            scaffold.clear()
            return
        if summary:
            result.append({
                "role": "assistant",
                "content": _SCAFFOLD_PREFIX + summary,
            })
        else:
            # Empty summary — fall back to verbatim so we don't lose the gap entirely
            result.extend(scaffold)
        scaffold.clear()

    for m in older:
        if m.get("role") == "user":
            flush()
            result.append(m)
        else:
            scaffold.append(m)
    flush()
    return result


# ── Misc helpers ─────────────────────────────────────────────────────────

def _with_summary_prefix(
    summary_text: Optional[str], messages: list[dict]
) -> list[dict]:
    if not summary_text:
        return messages
    return [
        {"role": "assistant", "content": _SUMMARY_PREFIX + summary_text}
    ] + messages


def _identify_latest_image_row_id(rows: list[Message]) -> Optional[int]:
    latest: Optional[Message] = None
    for r in rows:
        if r.role == "user" and r.attachments_json:
            try:
                atts = json.loads(r.attachments_json)
            except json.JSONDecodeError:
                continue
            if atts:
                if latest is None or r.created_at > latest.created_at:
                    latest = r
    return latest.id if latest else None


def _identify_latest_user_row_id(rows: list[Message]) -> Optional[int]:
    latest: Optional[Message] = None
    for r in rows:
        if r.role == "user":
            if latest is None or r.created_at > latest.created_at:
                latest = r
    return latest.id if latest else None


# ── Public entrypoints ───────────────────────────────────────────────────

def load_compacted_history(
    session: Session,
    conversation_id: int,
    client: AzureOpenAI,
    deployment: str,
) -> tuple[list[dict], list[Callable[[], None]]]:
    """Load conversation history with asymmetric compaction.

    All user messages within the window are preserved (long pastes summarized,
    older images described). Scaffolding between user messages in the older
    portion is collapsed into outcome bullets; recent scaffolding stays
    verbatim. Cached summary prepended when present.

    Returns a ``(messages, deferred)`` tuple.  ``deferred`` is a list of
    zero-argument callables that perform LLM summarisation for cache misses
    encountered this turn.  The caller should schedule them as background
    tasks after the main response is delivered so they don't add latency to
    the current turn.  The cached summaries are picked up on the next turn.
    """
    deferred: list[Callable[[], None]] = []

    conv = session.get(Conversation, conversation_id)
    if conv is None:
        return [], deferred

    summary_through = conv.summary_through_message_id or 0

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.id > summary_through)
        .order_by(Message.created_at.desc())  # type: ignore[arg-type]
        .limit(MAX_HISTORY_MESSAGES)
    )
    rows = list(session.exec(stmt).all())
    rows.reverse()

    if not rows:
        return _with_summary_prefix(conv.summary_text, []), deferred

    latest_image_owner_id = _identify_latest_image_row_id(rows)
    latest_user_id = _identify_latest_user_row_id(rows)

    # Convert rows to messages with per-row compression rules applied
    id_messages: list[tuple[int, dict]] = []
    for r in rows:
        if r.id is None:
            continue
        msg = _row_to_message(
            r, session, client, deployment,
            is_latest_user=(r.id == latest_user_id),
            is_latest_image_owner=(r.id == latest_image_owner_id),
            deferred=deferred,
        )
        id_messages.append((int(r.id), msg))

    messages = [m for _, m in id_messages]
    messages = _clean_orphans(messages)

    over_count = len(messages) > COMPACT_THRESHOLD_MESSAGES
    over_chars = _estimate_chars(messages) > COMPACT_THRESHOLD_CHARS

    if not (over_count or over_chars):
        return _with_summary_prefix(conv.summary_text, messages), deferred

    # Determine recent window size — keep at least RECENT_KEEP_COUNT
    # messages verbatim. With many small messages we keep the count cap;
    # with few but huge we still want to compress, so summarize older half.
    if over_count:
        keep_n = RECENT_KEEP_COUNT
    else:
        keep_n = max(1, len(messages) // 2)

    split_idx = max(0, len(id_messages) - keep_n)
    if split_idx == 0:
        return _with_summary_prefix(conv.summary_text, messages), deferred

    older_id_msgs = id_messages[:split_idx]
    recent_id_msgs = id_messages[split_idx:]

    older = [m for _, m in older_id_msgs]
    recent = [m for _, m in recent_id_msgs]

    # Compress older: user msgs preserved, scaffolding between them summarized
    compressed_older = _compress_older_scaffolding(older, client, deployment)

    final = _with_summary_prefix(conv.summary_text, compressed_older + recent)
    # Final orphan sweep — scaffolding summarization removes assistant
    # tool_calls, so any tool responses still in recent whose parents got
    # folded into a summary need to be dropped.
    final = _clean_orphans(final)

    # Cumulative outcome cache: extend (or set) the high-level summary so
    # the next turn doesn't re-summarize the same older scaffolding.
    cumulative_outcomes = "\n".join(
        m.get("content", "").replace(_SCAFFOLD_PREFIX, "")
        for m in compressed_older
        if m.get("role") == "assistant"
        and isinstance(m.get("content"), str)
        and m["content"].startswith(_SCAFFOLD_PREFIX)
    ).strip()

    if cumulative_outcomes:
        prior = conv.summary_text or ""
        # Cap cumulative cache at ~3 KB so it stays compact across many turns
        combined = (prior + "\n" + cumulative_outcomes).strip()
        if len(combined) > 3000:
            try:
                combined = _summarize_long_paste(combined, client, deployment)
            except Exception as e:
                logger.warning("Cumulative summary recompression failed: %s", e)
                combined = combined[-3000:]
        conv.summary_text = combined

    if older_id_msgs:
        conv.summary_through_message_id = max(mid for mid, _ in older_id_msgs)

    session.add(conv)
    session.commit()

    logger.info(
        "Compacted %d older msgs (preserved user msgs + outcome bullets); "
        "kept %d recent verbatim; summary_through_message_id=%s",
        len(older), len(recent), conv.summary_through_message_id,
    )

    return final, deferred


def get_original_task(session: Session, conversation_id: int) -> str:
    """Return the very first user message in the conversation, or empty
    string if none exists yet. Used to pin the original ask in the system
    prompt so it survives compaction."""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.role == "user")
        .order_by(Message.created_at.asc())  # type: ignore[arg-type]
        .limit(1)
    )
    row = session.exec(stmt).first()
    return row.content if row else ""
