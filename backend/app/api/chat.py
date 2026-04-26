"""Chat API endpoints — SSE streaming chat + approval handling + resume."""

import json
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, field_validator
from sqlmodel import select

from app.agent.approvals import (
    get_pending_approval_for_conversation,
    resolve_approval,
)
from app.agent.orchestrator import handle_chat
from app.agent.streaming import sse_approval_required, sse_done, sse_error
from app.auth.models import User
from app.db.engine import get_session
from app.db.models import Conversation, Message, PendingApproval, UserRecord
from app.deps import current_user
from app.config import get_settings
from app.skills.loader import load_skill
from app.skills.models import Skill
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/greeting")
async def get_greeting(user: User = Depends(current_user)):
    """Generate a short AI-powered greeting based on current context."""
    settings = get_settings()
    now = datetime.now()
    hour = now.hour
    time_str = now.strftime("%I:%M %p")  # e.g. "01:46 AM"
    day_name = now.strftime("%A")

    # Extract first name from display_name (e.g. "Balaji Kumar" -> "Balaji")
    first_name = (user.display_name or "").split()[0] if user.display_name else ""

    try:
        client = AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
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

    async def event_stream():
        if pending:
            yield sse_approval_required(
                pending.id,
                pending.tool_name,
                json.loads(pending.tool_args_json),
                pending.reason,
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
