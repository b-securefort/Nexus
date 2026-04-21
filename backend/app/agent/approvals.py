"""
Approval state machine for tool calls requiring user approval.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import PendingApproval

logger = logging.getLogger(__name__)

# In-memory events for approval resolution
_approval_events: dict[str, asyncio.Event] = {}
_approval_results: dict[str, str] = {}  # approval_id -> status


def create_pending_approval(
    session: Session,
    conversation_id: int,
    user_oid: str,
    tool_name: str,
    tool_args_json: str,
    reason: str,
) -> PendingApproval:
    """Create a new pending approval and register an asyncio event."""
    approval_id = str(uuid.uuid4())

    approval = PendingApproval(
        id=approval_id,
        conversation_id=conversation_id,
        user_oid=user_oid,
        tool_name=tool_name,
        tool_args_json=tool_args_json,
        reason=reason,
        status="pending",
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)

    # Register event
    _approval_events[approval_id] = asyncio.Event()

    return approval


async def wait_for_approval(approval_id: str) -> str:
    """
    Wait for an approval to be resolved. Returns the status: 'approved', 'denied', or 'expired'.
    """
    settings = get_settings()
    timeout = settings.TOOL_APPROVAL_TIMEOUT_SECONDS

    event = _approval_events.get(approval_id)
    if not event:
        return "expired"

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        _approval_results[approval_id] = "expired"

    result = _approval_results.pop(approval_id, "expired")
    _approval_events.pop(approval_id, None)
    return result


def resolve_approval(session: Session, approval_id: str, action: str) -> bool:
    """
    Resolve a pending approval. action is 'approve' or 'deny'.
    Returns True if the approval was found and resolved.
    """
    stmt = select(PendingApproval).where(PendingApproval.id == approval_id)
    approval = session.exec(stmt).first()

    if not approval or approval.status != "pending":
        return False

    status = "approved" if action == "approve" else "denied"
    approval.status = status
    approval.resolved_at = datetime.now(timezone.utc)
    session.add(approval)
    session.commit()

    # Set the result and signal the event
    _approval_results[approval_id] = status
    event = _approval_events.get(approval_id)
    if event:
        event.set()

    logger.info("Approval %s resolved as %s", approval_id, status)
    return True


def get_pending_approval_for_conversation(session: Session, conversation_id: int) -> PendingApproval | None:
    """Get a pending approval for a conversation, if any."""
    stmt = (
        select(PendingApproval)
        .where(PendingApproval.conversation_id == conversation_id)
        .where(PendingApproval.status == "pending")
    )
    return session.exec(stmt).first()


async def expire_stale_approvals(session: Session) -> None:
    """Mark stale pending approvals as expired. Run periodically."""
    settings = get_settings()
    timeout = settings.TOOL_APPROVAL_TIMEOUT_SECONDS
    now = datetime.now(timezone.utc)

    stmt = select(PendingApproval).where(PendingApproval.status == "pending")
    approvals = session.exec(stmt).all()

    for approval in approvals:
        created = approval.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (now - created).total_seconds()
        if age > timeout:
            approval.status = "expired"
            approval.resolved_at = now
            session.add(approval)

            # Signal any waiting event
            _approval_results[approval.id] = "expired"
            event = _approval_events.get(approval.id)
            if event:
                event.set()

            logger.info("Approval %s expired (age: %.0fs)", approval.id, age)

    session.commit()
