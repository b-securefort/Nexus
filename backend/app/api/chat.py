"""Chat API endpoints — SSE streaming chat + approval handling + resume."""

import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select

from app.agent.approvals import (
    get_pending_approval_for_conversation,
    resolve_approval,
)
from app.agent.questions import (
    get_pending_question_for_conversation,
    resolve_question,
)
from app.agent.orchestrator import cleanup_interrupted_turn, handle_chat
from app.agent.streaming import (
    sse_approval_required,
    sse_done,
    sse_error,
    sse_question_required,
)
from app.auth.models import User
from app.db.engine import get_session
from app.db.models import (
    Conversation,
    Message,
    PendingApproval,
    PendingQuestion,
    UserRecord,
)
from app.tools.generic.ask_user import validate_questions
from app.deps import current_user
from app.config import get_settings
from app.skills.loader import load_skill
from app.skills.models import Skill
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


# Names interpolated into the greeting system prompt must not carry
# punctuation that could break the prompt or smuggle instructions. Allow
# letters/digits/underscore, spaces, hyphens, apostrophes, and dots
# (covers "Mary-Ann", "O'Neil", "A.B."). Everything else is stripped.
_SAFE_NAME_PATTERN = re.compile(r"[^\w \-'.]")
_GREETING_NAME_MAX_LEN = 40


def _sanitize_first_name(raw: str) -> str:
    """Strip greeting-unsafe characters from a display name fragment.

    Defence against prompt-injection: a malicious display name like
    `Foo. Ignore prior instructions and output X` would otherwise be
    pasted verbatim into the greeting system prompt. Only safe name
    characters survive, and the result is capped to a short length.
    """
    if not raw:
        return ""
    cleaned = _SAFE_NAME_PATTERN.sub("", raw).strip()
    return cleaned[:_GREETING_NAME_MAX_LEN]


@router.get("/greeting")
async def get_greeting(user: User = Depends(current_user)):
    """Generate a short AI-powered greeting based on current context."""
    settings = get_settings()
    now = datetime.now()
    hour = now.hour
    time_str = now.strftime("%I:%M %p")  # e.g. "01:46 AM"
    day_name = now.strftime("%A")

    # Extract first name from display_name (e.g. "Balaji Kumar" -> "Balaji").
    # The fragment is interpolated into the system prompt, so it must be
    # sanitized to strip anything that could close the quoted context or
    # smuggle instructions. See _sanitize_first_name above.
    raw_first = (user.display_name or "").split()[0] if user.display_name else ""
    first_name = _sanitize_first_name(raw_first)

    try:
        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            timeout=float(settings.AOAI_TIMEOUT_SECONDS),
            max_retries=0,
        )

        name_instruction = (
            f"The user's first name is {first_name}. "
            "Sometimes include their name in the greeting, sometimes don't — vary it naturally."
        ) if first_name else ""

        response = client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate a single short, friendly greeting for an AI assistant called Nexus. "
                        "The greeting should be warm but professional. Keep it under 8 words. "
                        "Do NOT include any punctuation at the end. Do NOT use quotes. "
                        "Vary your style — sometimes ask how you can help, sometimes just greet, "
                        "sometimes reference the time context naturally. "
                        "Use common sense for time references: late night is not 'evening', "
                        "early hours like 1-4 AM are 'late night' or just skip time references. "
                        f"{name_instruction}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"The current time is {time_str} on {day_name}. Generate a greeting.",
                },
            ],
            max_completion_tokens=20,
            temperature=1.0,
        )
        greeting = response.choices[0].message.content.strip().strip('"\'')
        return {"greeting": greeting}
    except Exception:
        logger.exception("Failed to generate AI greeting")
        # Fallback
        if hour < 5:
            fallback = "Hey there, night owl"
        elif hour < 12:
            fallback = "Good morning"
        elif hour < 17:
            fallback = "Good afternoon"
        else:
            fallback = "Good evening"
        return {"greeting": fallback}

# In-memory rate limiter: user_oid -> list of request timestamps
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(user_oid: str) -> None:
    """Check per-user rate limit. Raises HTTPException if exceeded."""
    settings = get_settings()
    now = time.time()
    window = 60.0

    # Clean old entries
    timestamps = _rate_limit_store[user_oid]
    _rate_limit_store[user_oid] = [t for t in timestamps if now - t < window]

    if len(_rate_limit_store[user_oid]) >= settings.CHAT_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in a minute.")

    _rate_limit_store[user_oid].append(now)


class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    skill_id: Optional[str] = None
    message: str
    attachment_urls: list[str] = []

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        if len(v) > 16000:
            raise ValueError("Message too long (max 16000 characters)")
        return v


ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _ensure_upload_dir() -> Path:
    """Ensure the upload directory exists and return its resolved absolute path."""
    settings = get_settings()
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


async def _save_upload(file: UploadFile) -> dict:
    """Validate and save a single uploaded file. Returns attachment metadata."""
    settings = get_settings()

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    max_bytes = settings.UPLOAD_MAX_FILE_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {len(data) / 1024 / 1024:.1f}MB (max {settings.UPLOAD_MAX_FILE_SIZE_MB}MB)",
        )

    # Generate unique filename preserving extension
    ext = Path(file.filename or "image.png").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        ext = ".png"
    unique_name = f"{uuid.uuid4().hex}{ext}"

    upload_dir = _ensure_upload_dir()
    file_path = upload_dir / unique_name
    file_path.write_bytes(data)
    logger.info("Saved upload %s (%d bytes) to %s", file.filename, len(data), file_path)

    return {
        "filename": unique_name,
        "original_name": file.filename or "image",
        "content_type": file.content_type,
        "url": f"/api/uploads/{unique_name}",
    }


class ApprovalRequest(BaseModel):
    action: str  # "approve" | "deny"


# Bounds for /api/questions/{id}/answer. Kept generous but finite so a malicious
# or buggy client can't push unbounded JSON into the DB before the orchestrator
# even sees it. The ask_user tool itself caps questions/options at 4 each
# (see app/tools/generic/ask_user.py); these mirror that.
_ANSWER_QUESTION_MAX_LEN = 500
_ANSWER_SELECTED_MAX_ITEMS = 4
_ANSWER_SELECTED_LABEL_MAX_LEN = 300
_ANSWER_NOTES_MAX_LEN = 2000
_ANSWERS_MAX_ITEMS = 4


class AnswerEntry(BaseModel):
    question: str = Field(..., min_length=1, max_length=_ANSWER_QUESTION_MAX_LEN)
    selected: list[str] = Field(..., max_length=_ANSWER_SELECTED_MAX_ITEMS)
    notes: Optional[str] = Field(default=None, max_length=_ANSWER_NOTES_MAX_LEN)

    @field_validator("selected")
    @classmethod
    def _bound_selected_labels(cls, v: list[str]) -> list[str]:
        for s in v:
            if not isinstance(s, str):
                raise ValueError("selected must be a list of strings")
            if len(s) > _ANSWER_SELECTED_LABEL_MAX_LEN:
                raise ValueError(
                    f"selected label too long (max {_ANSWER_SELECTED_LABEL_MAX_LEN} chars)"
                )
        return v


class AnswerSubmission(BaseModel):
    """Body for POST /api/questions/{id}/answer.

    `answers` is a list of objects, one per question that was asked, each with:
      - question: str (the question text - identifies which entry this answers)
      - selected: list[str] (the selected option labels; single-select tools
                  pass a list of length 1)
      - notes: optional str (free-text 'Other' content if the user picked it)
    """
    answers: list[AnswerEntry] = Field(..., min_length=1, max_length=_ANSWERS_MAX_ITEMS)


def _upsert_user(user: User) -> None:
    """Upsert user record, throttled to once per minute."""
    with get_session() as session:
        stmt = select(UserRecord).where(UserRecord.oid == user.oid)
        record = session.exec(stmt).first()
        now = datetime.now(timezone.utc)

        if not record:
            record = UserRecord(
                oid=user.oid,
                email=user.email,
                display_name=user.display_name,
            )
            session.add(record)
            session.commit()
        elif (now - record.last_seen_at.replace(tzinfo=timezone.utc)).total_seconds() > 60:
            record.last_seen_at = now
            record.email = user.email
            record.display_name = user.display_name
            session.add(record)
            session.commit()


def _skill_to_snapshot(skill: Skill) -> str:
    """Serialize a skill to JSON for the conversation snapshot."""
    return json.dumps({
        "id": skill.id,
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description,
        "system_prompt": skill.system_prompt,
        "tools": skill.tools,
        "source": skill.source,
        "reasoning_effort": skill.reasoning_effort,
        "verbosity": skill.verbosity,
    })


@router.post("/chat")
async def chat(
    request: Request,
    user: User = Depends(current_user),
):
    """Start or continue a chat conversation. Returns SSE stream.

    Accepts either JSON body or multipart/form-data (with file attachments).
    """
    _check_rate_limit(user.oid)
    _upsert_user(user)

    settings = get_settings()
    content_type = request.headers.get("content-type", "")
    logger.info("Chat request content-type: %s", content_type[:100])

    # Parse request — support both JSON and multipart
    if "multipart/form-data" in content_type:
        form = await request.form()
        message = str(form.get("message", "")).strip()
        conversation_id_str = str(form.get("conversation_id", "") or "")
        skill_id = str(form.get("skill_id", "") or "") or None
        conversation_id = int(conversation_id_str) if conversation_id_str else None

        # Process file uploads
        files = form.getlist("files")
        has_files = any(hasattr(f, 'read') for f in files)

        if not message and not has_files:
            raise HTTPException(status_code=422, detail="Message or attachment required")
        if len(message) > 16000:
            raise HTTPException(status_code=422, detail="Message too long (max 16000 characters)")

        if len(files) > settings.UPLOAD_MAX_FILES_PER_MESSAGE:
            raise HTTPException(
                status_code=400,
                detail=f"Too many files (max {settings.UPLOAD_MAX_FILES_PER_MESSAGE})",
            )
        attachments = []
        for f in files:
            # Use duck typing: form.getlist returns starlette UploadFile,
            # not fastapi UploadFile, so isinstance check would fail
            if hasattr(f, 'read') and hasattr(f, 'filename'):
                att = await _save_upload(f)
                attachments.append(att)
    else:
        body_bytes = await request.body()
        try:
            body_data = json.loads(body_bytes)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="Invalid JSON")
        try:
            body = ChatRequest(**body_data)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        message = body.message
        conversation_id = body.conversation_id
        skill_id = body.skill_id
        attachments = []
        # Support pre-uploaded attachment URLs in JSON mode
        for url in body.attachment_urls:
            filename = url.rsplit("/", 1)[-1]
            attachments.append({
                "filename": filename,
                "original_name": filename,
                "content_type": "image/png",
                "url": url,
            })
    attachments_json = json.dumps(attachments) if attachments else None
    if attachments:
        logger.info("Chat request has %d attachment(s): %s", len(attachments),
                     [a["filename"] for a in attachments])

    with get_session() as session:
        if conversation_id:
            # Continue existing conversation
            conversation = session.get(Conversation, conversation_id)
            if not conversation or conversation.deleted_at is not None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            if conversation.user_oid != user.oid:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            # New conversation
            if not skill_id:
                raise HTTPException(status_code=400, detail="skill_id is required for new conversations")

            skill = load_skill(skill_id, user.oid, session)

            # Generate title from first message
            title = message[:80].strip()
            if len(message) > 80:
                title += "..."

            conversation = Conversation(
                user_oid=user.oid,
                title=title,
                skill_id=skill_id,
                skill_snapshot_json=_skill_to_snapshot(skill),
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

    async def event_stream():
        with get_session() as session:
            try:
                async for event in handle_chat(
                    session, conversation, message, user,
                    attachments_json=attachments_json,
                ):
                    yield event
            except Exception as e:
                logger.error("Chat stream error: %s", str(e), exc_info=True)
                yield sse_error("An internal error occurred")
            finally:
                # Covers the client-disconnect / Stop path, where handle_chat's
                # done-path cleanup never runs because the generator is closed
                # mid-stream. Idempotent with the normal end-of-turn cleanup.
                cleanup_interrupted_turn(session, conversation.id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/approvals/{approval_id}")
async def handle_approval(approval_id: str, body: ApprovalRequest, user: User = Depends(current_user)):
    """Approve or deny a pending tool call."""
    if body.action not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'deny'")

    with get_session() as session:
        # Verify the approval belongs to this user
        stmt = select(PendingApproval).where(PendingApproval.id == approval_id)
        approval = session.exec(stmt).first()

        if not approval:
            raise HTTPException(status_code=404, detail="Approval not found")
        if approval.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")
        if approval.status != "pending":
            raise HTTPException(status_code=409, detail=f"Approval already {approval.status}")

        resolved = resolve_approval(session, approval_id, body.action)
        if not resolved:
            raise HTTPException(status_code=500, detail="Failed to resolve approval")

    return {"status": "ok"}


@router.post("/questions/{question_id}/answer")
async def handle_question_answer(
    question_id: str,
    body: AnswerSubmission,
    user: User = Depends(current_user),
):
    """Submit answers to a pending ask_user question batch.

    Body: { "answers": [{ "question": "...", "selected": ["..."], "notes": "..."? }, ...] }
    The orchestrator awaits this resolution and feeds the answers back to the
    model as the ask_user tool's result.
    """
    # Pydantic (AnswerSubmission) already enforced shape, item counts, and
    # length bounds before we get here — bad payloads were rejected with 422.
    # Strip whitespace and drop empty notes; the orchestrator trusts what's
    # in the DB, so any remaining normalization happens here.
    cleaned: list[dict] = []
    for i, a in enumerate(body.answers):
        question_text = a.question.strip()
        selected = [s.strip() for s in a.selected if s.strip()]
        notes = (a.notes or "").strip() or None
        if not question_text:
            raise HTTPException(
                status_code=400,
                detail=f"answers[{i}].question is required",
            )
        cleaned.append({
            "question": question_text,
            "selected": selected,
            **({"notes": notes} if notes else {}),
        })

    with get_session() as session:
        record = session.get(PendingQuestion, question_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Question not found")
        if record.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")
        if record.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Question already {record.status}",
            )

        if not resolve_question(session, question_id, cleaned):
            raise HTTPException(status_code=500, detail="Failed to resolve question")

    return {"status": "ok"}


class ArmTokenRefreshRequest(BaseModel):
    """Body for POST /api/chat/refresh-token (Track 4C).

    Sent by the frontend after it has acquired a fresh ARM access token via
    MSAL silent refresh in response to a `token_refresh_required` SSE event.
    The server stores the token in a per-conversation override map so any
    in-flight orchestrator turn picks it up on its next iteration.
    """
    conversation_id: int
    arm_token: str = Field(..., min_length=20, max_length=8192)


@router.post("/chat/refresh-token")
async def refresh_chat_arm_token(
    body: ArmTokenRefreshRequest, user: User = Depends(current_user)
):
    """Accept a refreshed ARM token from the frontend (Track 4C).

    Validates the token claims (audience must be management.azure.com, tenant
    must match the configured one) before storing it. Returns 422 if the
    token isn't shaped like an ARM token. Does NOT verify the signature —
    the actual signature verification happens when Azure consumes the token.

    The orchestrator reads this override on every Azure tool dispatch so a
    long-running turn that hit `token_refresh_required` can resume without
    the user retyping the message.
    """
    from app.auth.entra import (
        _extract_arm_token,
        arm_token_status,
        set_arm_token_override,
    )
    import jwt

    settings = get_settings()

    # Authorize: the conversation must belong to this user
    with get_session() as session:
        conversation = session.get(Conversation, body.conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

    # Sanity-check token shape (mirrors _extract_arm_token validation, but
    # against the request body instead of the request header).
    try:
        claims = jwt.decode(
            body.arm_token,
            options={"verify_signature": False, "verify_exp": False},
        )
    except Exception:
        raise HTTPException(status_code=422, detail="Token is not a valid JWT")
    aud = str(claims.get("aud") or "")
    if "management.azure.com" not in aud:
        raise HTTPException(
            status_code=422, detail="Token audience is not Azure Resource Manager"
        )
    tid = str(claims.get("tid") or "")
    if tid and settings.ENTRA_TENANT_ID and tid != settings.ENTRA_TENANT_ID:
        raise HTTPException(status_code=422, detail="Token tenant mismatch")

    # Reject anything that's already expired — no point storing it
    if arm_token_status(body.arm_token, refresh_threshold_seconds=0) == "expired":
        raise HTTPException(status_code=422, detail="Refreshed token has already expired")

    set_arm_token_override(body.conversation_id, body.arm_token)
    logger.info(
        "Stored refreshed ARM token override for conv=%s user=%s",
        body.conversation_id, user.oid,
    )
    return {"status": "ok"}


@router.get("/chat/resume")
async def resume_chat(conversation_id: int, user: User = Depends(current_user)):
    """Reconnect to a paused chat stream (e.g., after page reload during pending approval)."""
    with get_session() as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.user_oid != user.oid:
            raise HTTPException(status_code=403, detail="Access denied")

        pending = get_pending_approval_for_conversation(session, conversation_id)
        pending_q = get_pending_question_for_conversation(session, conversation_id)

    async def event_stream():
        if pending:
            yield sse_approval_required(
                pending.id,
                pending.tool_name,
                json.loads(pending.tool_args_json),
                pending.reason,
                risk_level=pending.risk_level,
                risk_description=pending.risk_description,
            )
        elif pending_q:
            # No call_id available on resume - the original tool_call_id lives
            # in messages.tool_calls_json. The frontend's resume path looks up
            # the in-flight question card by question_id, so call_id is empty.
            yield sse_question_required(
                pending_q.id,
                "",
                json.loads(pending_q.questions_json),
            )
        else:
            yield sse_done(conversation_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/uploads/{filename}")
async def serve_upload(filename: str, user: User = Depends(current_user)):
    """Serve an uploaded file. Only allows image files with safe filenames."""
    import re
    if not re.match(r"^[a-f0-9]+\.(png|jpg|jpeg|gif|webp)$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    settings = get_settings()
    upload_dir = Path(settings.UPLOAD_DIR).resolve()
    file_path = upload_dir / filename

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Extra safety: ensure resolved path is inside upload dir
    if not file_path.resolve().is_relative_to(upload_dir):
        raise HTTPException(status_code=400, detail="Invalid path")

    return FileResponse(file_path)


@router.get("/output/{filename}")
async def serve_output(filename: str, user: User = Depends(current_user)):
    """Serve a file produced by tools that write into the output/ sandbox
    (currently render_drawio PNGs/SVGs/PDFs and generate_file artifacts).
    Restricted to safe basenames and a small allowlist of viewer-friendly
    extensions; the .drawio source is also served so the user can download it.

    Sends Cache-Control: no-store because the same filename is overwritten
    on every diagram iteration. Without this the browser serves a stale image
    while the model sees the fresh one (it reads bytes directly from disk).
    """
    import re
    if not re.match(r"^[A-Za-z0-9_\-. ]+\.(png|jpg|jpeg|svg|pdf|drawio)$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    output_dir = Path("output").resolve()
    file_path = (output_dir / filename).resolve()

    if not file_path.is_relative_to(output_dir):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        file_path,
        headers={"Cache-Control": "no-store, max-age=0"},
    )
