"""Forensic audit log of approval-gated tool executions (DESIGN.md §5 2026-06-15).

One append-only `tool_executions` row per *terminal* approval-gated tool attempt.
Fail-open by contract: a failed write logs a WARNING and never propagates, because
this is a forensic aid for incident reviewers (the 1-2 `superadmin` users), not a
control the tool execution depends on. The orchestrator owns the single call site
(the gated-execute terminal outcome); there is no agent-facing tool and — by
design — no update or delete path. The only deleter is the time-based
`_audit_log_prune` sweeper in main.py.

The write uses its own short-lived session so it is fully decoupled from the chat
turn's transaction: a turn rollback can't lose an audit row, and an audit-DB
hiccup can't poison the turn.
"""

import logging
from typing import Optional

from app.db.engine import get_session
from app.db.models import ToolExecution

logger = logging.getLogger(__name__)

# Coarse, append-only outcome vocabulary — kept in lockstep with
# ToolExecution.outcome and DESIGN.md. Deliberately NOT the fine-grained error
# taxonomy: the #23 question is "who ran what, and did it take", not "why did it
# fail". `blocked` = a safety-prefix rejection (§5 2026-05-15); `integrity_failed`
# = the #20 approve→execute fingerprint mismatch.
AUDIT_OUTCOMES = ("success", "error", "denied", "blocked", "integrity_failed")


def record_tool_execution(
    *,
    user_oid: str,
    user_email: str,
    conversation_id: Optional[int],
    tool_name: str,
    rendered_command: str,
    outcome: str,
    review_fingerprint: Optional[str] = None,
    risk_level: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Append one immutable audit row. Fail-open — never raises.

    `rendered_command` MUST be the masked human-card render (`render_for_human`),
    never raw args, so secrets are not persisted here in plaintext (§5 2026-06-13).
    """
    try:
        with get_session() as session:
            session.add(
                ToolExecution(
                    user_oid=user_oid or "anonymous",
                    user_email=user_email or "",
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    rendered_command=rendered_command,
                    review_fingerprint=review_fingerprint,
                    outcome=outcome,
                    risk_level=risk_level,
                    reason=reason,
                )
            )
            session.commit()
    except Exception as e:  # noqa: BLE001 — audit write must never break a turn
        logger.warning(
            "Audit log write failed for tool=%s conv=%s (fail-open): %s",
            tool_name, conversation_id, str(e)[:200],
        )
