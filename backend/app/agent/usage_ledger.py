"""Append-only per-LLM-call spend ledger (DESIGN.md §5 2026-06-14).

This module is the *recording* half of the per-user weekly spend cap. It writes
one `usage_events` row per Azure OpenAI completions call so per-user spend can be
summed and capped later. Enforcement (the pre-flight + per-iteration cap gate)
and dollar derivation (the price table) read these rows but live elsewhere — this
file knows nothing about caps or prices, only how to persist a usage record.

Two contracts:

1. **Fail-soft.** A ledger write must never break a chat turn. Every failure is
   swallowed with a warning — an unrecorded row means slightly under-counted
   spend, which is strictly better than a 500 on the user's turn.

2. **Records every completion.** The recording site is each completions call,
   right after the circuit breaker records success — the single point every
   completion (main loop *and* aux: compaction, judge, rephrase, risk review,
   rerank) passes through (§5 2026-06-14, building on §5 2026-05-21). Aux calls
   sit deep in the stack with no `user` in scope, so `user_oid` /
   `conversation_id` default to the per-request ContextVars set in `handle_chat`.
   A call with no attributable user (startup / background work) is skipped rather
   than written as an unattributable row the cap could never act on.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.db.engine import get_session
from app.db.models import UsageEvent
from app.tools.base import get_conversation_id, get_user_oid

logger = logging.getLogger(__name__)


def record_usage(
    usage: Any,
    deployment: str,
    *,
    user_oid: Optional[str] = None,
    conversation_id: Optional[int] = None,
) -> None:
    """Append one `usage_events` row for a completed LLM call. Never raises.

    Args:
        usage: the OpenAI SDK usage object (``response.usage`` or the final
            stream chunk's ``.usage``). ``None`` is tolerated and skipped.
        deployment: the resolved deployment name (high vs base tier) so the
            read-time price table can weight the row correctly.
        user_oid / conversation_id: explicit attribution; when omitted they fall
            back to the per-request ContextVars set at the top of the turn.
    """
    if usage is None:
        return
    try:
        oid = user_oid if user_oid is not None else get_user_oid()
        if not oid:
            logger.debug("usage_ledger: no user_oid in context; skipping record")
            return
        conv = conversation_id if conversation_id is not None else get_conversation_id()

        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = int(getattr(details, "cached_tokens", 0) or 0)

        with get_session() as session:
            session.add(
                UsageEvent(
                    user_oid=oid,
                    conversation_id=conv,
                    deployment=deployment,
                    prompt_tokens=prompt,
                    cached_tokens=cached,
                    completion_tokens=completion,
                )
            )
            session.commit()
    except Exception as e:  # fail-soft — a ledger write never breaks a turn
        logger.warning(
            "usage_ledger: failed to record usage: %s", str(e).split("\n")[0]
        )
