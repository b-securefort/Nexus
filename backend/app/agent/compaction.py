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
# Sized so a single tool-heavy turn (up to 15 iterations × ~3 messages) plus
# several earlier turns fit — rows beyond this cap are dropped UNsummarized,
# so it must comfortably exceed the compaction trigger point.
MAX_HISTORY_MESSAGES = 200
# Target size of the recent verbatim window (in messages). Scaffolding
# older than this gets compressed into outcome bullets.
RECENT_KEEP_COUNT = 40
# Compaction triggers on TOKEN budget, not chars/message-count. The old
# thresholds (30 messages / 12K chars ≈ 3K tokens) compacted almost every
# tool-using conversation from turn two onward — the agent ran on lossy
# 800-char summaries while >95% of the model's window sat empty. History may
# now occupy this fraction of the chat model's context window before any
# compression happens; the rest is headroom for system prompt, KB index,
# tool schemas, and the next completion.
COMPACT_THRESHOLD_FRACTION = 0.5
# Test/operator override (absolute tokens). None → fraction of the window.
COMPACT_THRESHOLD_TOKENS: int | None = None
# User messages with text content longer than this get a high-quality LLM
# summary (cached on Message.text_summary). Latest user message is exempt.
# ~5K tokens: below that a verbatim paste is affordable and strictly better.
USER_PASTE_THRESHOLD = 20_000
# Durable cumulative-summary cache cap (chars). On overflow we LLM-recompress
# (lossy), so the cap is generous — recompression should be rare, not a
# per-turn meat grinder.
CUMULATIVE_SUMMARY_MAX_CHARS = 12_000

_SUMMARY_PREFIX = "[Summary of earlier conversation]\n"
_SCAFFOLD_PREFIX = "[Outcomes from intermediate tool work]\n"


# ── Estimation helpers ───────────────────────────────────────────────────

# Conservative per-image token estimate (vision input at "auto" detail).
_IMAGE_TOKEN_ESTIMATE = 800


def _estimate_tokens(messages: list[dict], model: str) -> int:
    """tiktoken-based footprint estimate of the loaded history. Drives the
    compaction trigger, so it uses real token counts (via token_usage) rather
    than a chars/4 heuristic — a 4x error here either strangles the context
    or blows the window."""
    from app.agent.token_usage import count_tokens

    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += count_tokens(c, model)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += count_tokens(part.get("text", ""), model)
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    total += _IMAGE_TOKEN_ESTIMATE
        if m.get("tool_calls"):
            total += count_tokens(json.dumps(m["tool_calls"]), model)
    return total


def _compact_threshold_tokens() -> int:
    """History token budget before compaction kicks in."""
    if COMPACT_THRESHOLD_TOKENS is not None:
        return COMPACT_THRESHOLD_TOKENS
    return int(get_settings().chat_context_window * COMPACT_THRESHOLD_FRACTION)


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


def _extract_ask_user_qa(content) -> str | None:
    """If `content` is an `ask_user` tool-result envelope, return a compact
    verbatim record of the question(s) and the option(s) the user chose;
    otherwise None.

    An `ask_user` answer is a real USER DECISION, not disposable scaffolding —
    but the OpenAI API forces it to live as a `tool`-role message (it answers
    the assistant's tool_call), so it would otherwise be swept into the lossy
    scaffold summarizer and the specific choice could be paraphrased away. We
    pull it out deterministically and preserve it verbatim instead, so the
    agent never re-asks something the user already answered.

    Envelope shape (set by the orchestrator):
        {"status": "success", "tool": "ask_user",
         "data": {"status": "answered", "answers": [{"question", "selected", "notes"?}, ...]}}
    """
    if not isinstance(content, str):
        return None
    try:
        env = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(env, dict) or env.get("tool") != "ask_user":
        return None
    data = env.get("data")
    if not isinstance(data, dict):
        return None
    answers = data.get("answers")
    if not isinstance(answers, list) or not answers:
        return None

    lines = ["[User answered clarifying question(s)]"]
    for a in answers:
        if not isinstance(a, dict):
            continue
        question = str(a.get("question", "")).strip()
        selected = a.get("selected")
        if isinstance(selected, list):
            chosen = ", ".join(str(s).strip() for s in selected if str(s).strip())
        else:
            chosen = str(selected).strip()
        notes = str(a.get("notes", "")).strip()
        line = f'- Q: "{question}" → chose: {chosen or "(no selection)"}'
        if notes:
            line += f" (note: {notes})"
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else None


def _compress_older_scaffolding(
    older: list[dict],
    client: AzureOpenAI,
    deployment: str,
) -> list[dict]:
    """Walk older messages: preserve user messages verbatim, collapse each
    run of assistant + tool messages between user messages into one
    synthetic assistant outcome message. Answers to `ask_user` prompts inside
    the scaffolding are pulled out and preserved verbatim (they're user
    decisions, not disposable tool chatter)."""
    if not older:
        return []

    result: list[dict] = []
    scaffold: list[dict] = []

    def flush():
        if not scaffold:
            return
        # Preserve any ask_user answers verbatim — they're user decisions the
        # lossy summarizer must not paraphrase away.
        qa_lines = [
            qa for m in scaffold
            if (qa := _extract_ask_user_qa(m.get("content"))) is not None
        ]
        try:
            summary = _summarize_scaffold(scaffold, client, deployment)
        except Exception as e:
            logger.warning("Scaffold summarize failed: %s — keeping verbatim", e)
            result.extend(scaffold)
            scaffold.clear()
            return
        body_parts = qa_lines + ([summary] if summary else [])
        if body_parts:
            result.append({
                "role": "assistant",
                "content": _SCAFFOLD_PREFIX + "\n".join(body_parts),
            })
        else:
            # Nothing salvaged — fall back to verbatim so we don't lose the gap.
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

def _content_to_text(content) -> str:
    """Flatten a message's `content` to plain text. Strings pass through;
    multipart content (text + image_url parts) keeps the text and marks
    images with a placeholder. Used when persisting older user messages into
    the durable summary cache."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            (p.get("text", "[image]") if p.get("type") == "text" else "[image]")
            for p in content
            if isinstance(p, dict)
        )
    return ""


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

    if _estimate_tokens(messages, deployment) <= _compact_threshold_tokens():
        return _with_summary_prefix(conv.summary_text, messages), deferred

    # Over the token budget. Keep the most recent RECENT_KEEP_COUNT messages
    # verbatim; with fewer-but-huge messages still compress the older half.
    if len(messages) > RECENT_KEEP_COUNT:
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

    # Cumulative durable cache: carry the older portion forward across turns.
    #
    # CRITICAL: this MUST include the older USER messages verbatim, not just the
    # assistant scaffold bullets. The watermark (summary_through_message_id) set
    # below advances past every older row — including user messages — so the
    # next turn's query (`Message.id > summary_through`) will never reload them.
    # Anything not captured here is dropped from ALL future turns. The previous
    # version cached only the scaffold bullets, so every intermediate user
    # ask/answer (including answers to the agent's own ask_user prompts, stored
    # as tool→summarized scaffolding) silently vanished once it fell behind the
    # watermark — which made the agent re-ask things the user had already
    # answered. Preserve user turns in order, interleaved with the outcome
    # bullets, so the durable summary reflects the real older conversation.
    cumulative_parts: list[str] = []
    for m in compressed_older:
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            text = _content_to_text(content).strip()
            if text:
                cumulative_parts.append(f"User said: {text}")
        elif (
            role == "assistant"
            and isinstance(content, str)
            and content.startswith(_SCAFFOLD_PREFIX)
        ):
            cumulative_parts.append(content.replace(_SCAFFOLD_PREFIX, ""))
    cumulative_outcomes = "\n".join(cumulative_parts).strip()

    if cumulative_outcomes:
        prior = conv.summary_text or ""
        # Cap the cumulative cache. Recompression is lossy (LLM squeeze), so
        # the cap is generous — full originals remain recoverable from the DB
        # via the search_conversation tool.
        combined = (prior + "\n" + cumulative_outcomes).strip()
        if len(combined) > CUMULATIVE_SUMMARY_MAX_CHARS:
            try:
                combined = _summarize_long_paste(combined, client, deployment)
            except Exception as e:
                logger.warning("Cumulative summary recompression failed: %s", e)
                combined = combined[-CUMULATIVE_SUMMARY_MAX_CHARS:]
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
