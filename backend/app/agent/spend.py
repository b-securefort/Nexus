"""Per-user weekly spend computation over the usage ledger (DESIGN.md §5 2026-06-14).

The READ side of the spend cap. Turns `usage_events` rows (tokens + deployment)
into dollars via a price table at read time — nothing dollar-valued is stored, so
a price change or a deployment-tier swap never restates history (the embed_model
lesson, §5 2026-05-15).

`remaining` is two windowed SUMs, debt-only, no reset job:

    cap − max(0, last_week_spend − cap) − this_week_spend

The fixed weekly window is Monday 00:00 UTC. Underspend never rolls forward;
overspend in week N reduces the budget of week N+1 by exactly the overspend
(one-week lookback). This module computes; it does not enforce.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlmodel import Session, func, select

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import UsageEvent, UserRecord

logger = logging.getLogger(__name__)

# Built-in price defaults, USD per 1,000,000 tokens, matched by substring of the
# deployment name (longest key first). Layered under USAGE_PRICE_TABLE_JSON,
# which is keyed by exact deployment name and wins. `cached` is the cheaper rate
# for the cached subset of prompt tokens.
_DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"prompt": 0.15, "cached": 0.075, "completion": 0.60},
    "gpt-4o": {"prompt": 2.50, "cached": 1.25, "completion": 10.0},
    "gpt-5": {"prompt": 2.50, "cached": 0.25, "completion": 10.0},
}
# An unknown deployment is priced at the high-tier rate rather than free — better
# to over-count an unpriced model than to let spend leak out of the cap.
_FALLBACK_PRICE = {"prompt": 2.50, "cached": 0.25, "completion": 10.0}


def _parse_json_obj(raw: str, what: str) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        logger.warning("spend: %s is not a JSON object; ignoring", what)
    except Exception as e:
        logger.warning("spend: failed to parse %s: %s", what, e)
    return {}


def _price_for(deployment: str) -> dict[str, float]:
    overrides = _parse_json_obj(
        get_settings().USAGE_PRICE_TABLE_JSON, "USAGE_PRICE_TABLE_JSON"
    )
    if deployment in overrides and isinstance(overrides[deployment], dict):
        return overrides[deployment]
    dl = (deployment or "").lower()
    for key in sorted(_DEFAULT_PRICES, key=len, reverse=True):
        if key in dl:
            return _DEFAULT_PRICES[key]
    return _FALLBACK_PRICE


def cost_usd(prompt_tokens: int, cached_tokens: int, completion_tokens: int, deployment: str) -> float:
    """Dollar cost of one (group of) usage row(s). cached is a subset of prompt."""
    price = _price_for(deployment)
    cached = max(0, min(int(cached_tokens or 0), int(prompt_tokens or 0)))
    fresh_prompt = int(prompt_tokens or 0) - cached
    completion = int(completion_tokens or 0)
    return (
        fresh_prompt * float(price.get("prompt", _FALLBACK_PRICE["prompt"]))
        + cached * float(price.get("cached", _FALLBACK_PRICE["cached"]))
        + completion * float(price.get("completion", _FALLBACK_PRICE["completion"]))
    ) / 1_000_000.0


def week_start(now: datetime) -> datetime:
    """Monday 00:00 UTC of the week containing `now`."""
    now = now.astimezone(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=now.weekday())


def spend_in_window(session: Session, user_oid: str, start: datetime, end: datetime) -> float:
    """USD spent by a user in [start, end). One GROUP BY per deployment keeps
    the per-deployment pricing exact while collapsing thousands of rows to a few."""
    rows = session.exec(
        select(
            UsageEvent.deployment,
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cached_tokens), 0),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
        )
        .where(UsageEvent.user_oid == user_oid)
        .where(UsageEvent.created_at >= start)
        .where(UsageEvent.created_at < end)
        .group_by(UsageEvent.deployment)
    ).all()
    return sum(cost_usd(p, c, comp, dep) for dep, p, c, comp in rows)


def _parse_role_caps() -> dict[str, float]:
    raw = _parse_json_obj(get_settings().USAGE_ROLE_CAPS_JSON, "USAGE_ROLE_CAPS_JSON")
    out: dict[str, float] = {}
    for role, val in raw.items():
        try:
            out[str(role)] = float(val)
        except Exception:
            logger.warning("spend: bad role-cap value for %r; skipping", role)
    return out


def resolve_cap(user_cap_usd: Optional[float], roles: Iterable[str]) -> float:
    """A per-user value wins; else the highest matching role cap; else the default."""
    if user_cap_usd is not None:
        return float(user_cap_usd)
    role_caps = _parse_role_caps()
    matched = [role_caps[r] for r in roles if r in role_caps]
    if matched:
        return max(matched)
    return float(get_settings().USAGE_WEEKLY_CAP_USD_DEFAULT)


def compute_remaining(session: Session, user_oid: str, cap_usd: float, *, now: Optional[datetime] = None) -> dict:
    """Return the budget breakdown for a user given an already-resolved cap.

    Keys: cap_usd, spent_this_week_usd, carryover_debt_usd, remaining_usd,
    remaining_fraction, week_resets_at (ISO).
    """
    now = now or datetime.now(timezone.utc)
    ws = week_start(now)
    next_ws = ws + timedelta(days=7)
    prev_ws = ws - timedelta(days=7)

    this_week = spend_in_window(session, user_oid, ws, next_ws)
    last_week = spend_in_window(session, user_oid, prev_ws, ws)
    carryover_debt = max(0.0, last_week - cap_usd)
    remaining = cap_usd - carryover_debt - this_week
    frac = (remaining / cap_usd) if cap_usd > 0 else 0.0

    return {
        "cap_usd": round(cap_usd, 4),
        "spent_this_week_usd": round(this_week, 4),
        "carryover_debt_usd": round(carryover_debt, 4),
        "remaining_usd": round(remaining, 4),
        # Clamp the display fraction to [0, 1]; remaining_usd can go negative
        # (bounded overspend) but a progress bar shouldn't.
        "remaining_fraction": max(0.0, min(1.0, frac)),
        "week_resets_at": next_ws.isoformat(),
    }


def remaining_for_user(session: Session, user_oid: str, roles: Iterable[str], *, now: Optional[datetime] = None) -> dict:
    """Resolve the cap from the users table + roles, then compute the breakdown."""
    user = session.exec(select(UserRecord).where(UserRecord.oid == user_oid)).first()
    user_cap = user.credit_cap_usd if user is not None else None
    cap = resolve_cap(user_cap, roles)
    return compute_remaining(session, user_oid, cap, now=now)


def check_over_cap(user_oid: str, roles: Iterable[str], *, now: Optional[datetime] = None) -> Optional[dict]:
    """Enforcement check: return the budget breakdown if the user is over cap, else None.

    Returns None when the feature or enforcement is disabled — so callers can
    treat None as "proceed". Opens its own session so it sees ledger rows
    committed earlier in the same turn (record_usage commits per call),
    independent of the caller's transaction/snapshot.
    """
    s = get_settings()
    if not s.USAGE_CAP_ENABLED or not s.USAGE_CAP_ENFORCED:
        return None
    with get_session() as session:
        b = remaining_for_user(session, user_oid, roles, now=now)
    return b if b["remaining_usd"] <= 0 else None
