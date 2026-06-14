"""Tests for the spend read path (DESIGN.md §5 2026-06-14): pricing, weekly
window, debt-only carryover, cap resolution, and the /api/usage/me endpoint."""

from datetime import datetime, timedelta, timezone

import pytest

from app.agent import spend
from app.agent.spend import (
    cost_usd,
    week_start,
    compute_remaining,
    resolve_cap,
    remaining_for_user,
)
from app.db.models import UsageEvent, UserRecord
from tests.conftest import AUTH_HEADERS


# ── pricing ──────────────────────────────────────────────────────────────────

def test_cost_usd_prompt_and_completion():
    # gpt-4o-mini: prompt 0.15 / cached 0.075 / completion 0.60 per 1M
    assert cost_usd(1_000_000, 0, 0, "gpt-4o-mini") == pytest.approx(0.15)
    assert cost_usd(0, 0, 1_000_000, "gpt-4o-mini") == pytest.approx(0.60)


def test_cost_usd_cached_is_subset_of_prompt():
    # All prompt tokens cached → billed at the cheaper cached rate, none fresh.
    assert cost_usd(1_000_000, 1_000_000, 0, "gpt-4o-mini") == pytest.approx(0.075)
    # cached clamped to prompt
    assert cost_usd(100, 999, 0, "gpt-4o-mini") == cost_usd(100, 100, 0, "gpt-4o-mini")


def test_cost_usd_high_tier_substring_match():
    # "gpt-5.4" matches the "gpt-5" default → prompt 2.5 per 1M
    assert cost_usd(1_000_000, 0, 0, "gpt-5.4") == pytest.approx(2.50)


def test_cost_usd_unknown_deployment_uses_fallback():
    assert cost_usd(1_000_000, 0, 0, "some-mystery-model") == pytest.approx(2.50)


# ── weekly window ────────────────────────────────────────────────────────────

def test_week_start_is_monday_midnight_utc():
    now = datetime(2026, 6, 14, 15, 30, tzinfo=timezone.utc)
    ws = week_start(now)
    assert ws.weekday() == 0  # Monday
    assert (ws.hour, ws.minute, ws.second, ws.microsecond) == (0, 0, 0, 0)
    assert ws <= now
    assert now - ws < timedelta(days=7)


# ── remaining / carryover ────────────────────────────────────────────────────

def _add(session, oid, dep, prompt, completion, when):
    session.add(UsageEvent(
        user_oid=oid, conversation_id=1, deployment=dep,
        prompt_tokens=prompt, cached_tokens=0, completion_tokens=completion,
        created_at=when,
    ))
    session.commit()


def test_remaining_basic_spend(db_session):
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    ws = week_start(now)
    # 2M completion on gpt-4o-mini this week = 2 * 0.60 = $1.20
    _add(db_session, "u1", "gpt-4o-mini", 0, 2_000_000, ws + timedelta(hours=1))
    r = compute_remaining(db_session, "u1", cap_usd=10.0, now=now)
    assert r["spent_this_week_usd"] == pytest.approx(1.20)
    assert r["carryover_debt_usd"] == 0.0
    assert r["remaining_usd"] == pytest.approx(8.80)
    assert 0.0 <= r["remaining_fraction"] <= 1.0


def test_debt_carryover_reduces_this_week(db_session):
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    ws = week_start(now)
    prev = ws - timedelta(days=7)
    cap = 1.0
    # Last week: 3M completion = $1.80 → $0.80 over a $1.00 cap.
    _add(db_session, "u2", "gpt-4o-mini", 0, 3_000_000, prev + timedelta(hours=1))
    # This week: $0.30 spent.
    _add(db_session, "u2", "gpt-4o-mini", 0, 500_000, ws + timedelta(hours=1))
    r = compute_remaining(db_session, "u2", cap_usd=cap, now=now)
    assert r["carryover_debt_usd"] == pytest.approx(0.80)
    assert r["spent_this_week_usd"] == pytest.approx(0.30)
    # remaining = 1.00 - 0.80 - 0.30 = -0.10 (bounded overspend can go negative)
    assert r["remaining_usd"] == pytest.approx(-0.10)
    assert r["remaining_fraction"] == 0.0  # clamped for the bar


def test_no_surplus_rollover(db_session):
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    ws = week_start(now)
    prev = ws - timedelta(days=7)
    cap = 10.0
    # Last week underspent ($0.60) — must NOT add budget to this week.
    _add(db_session, "u3", "gpt-4o-mini", 0, 1_000_000, prev + timedelta(hours=1))
    _add(db_session, "u3", "gpt-4o-mini", 0, 1_000_000, ws + timedelta(hours=1))
    r = compute_remaining(db_session, "u3", cap_usd=cap, now=now)
    assert r["carryover_debt_usd"] == 0.0
    assert r["remaining_usd"] == pytest.approx(9.40)  # 10 - 0.60, no bonus


# ── cap resolution ───────────────────────────────────────────────────────────

def test_resolve_cap_user_override_wins(monkeypatch):
    assert resolve_cap(7.5, ["architect"]) == 7.5


def test_resolve_cap_role_then_default(monkeypatch):
    s = spend.get_settings()
    monkeypatch.setattr(s, "USAGE_ROLE_CAPS_JSON", '{"architect": 50, "engineer": 25}')
    monkeypatch.setattr(s, "USAGE_WEEKLY_CAP_USD_DEFAULT", 20.0)
    assert resolve_cap(None, ["engineer", "architect"]) == 50.0  # highest match
    assert resolve_cap(None, ["nobody"]) == 20.0  # falls to default


def test_remaining_for_user_reads_user_cap(db_session):
    db_session.add(UserRecord(oid="u4", email="u4@x", display_name="U4", credit_cap_usd=3.0))
    db_session.commit()
    r = remaining_for_user(db_session, "u4", roles=[])
    assert r["cap_usd"] == 3.0


# ── endpoint ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usage_me_endpoint(client):
    resp = await client.get("/api/usage/me", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["cap_usd"] == 20.0  # USAGE_WEEKLY_CAP_USD_DEFAULT
    assert "remaining_usd" in body and "week_resets_at" in body
    assert 0.0 <= body["remaining_fraction"] <= 1.0


@pytest.mark.asyncio
async def test_usage_me_disabled(client, monkeypatch):
    s = spend.get_settings()
    monkeypatch.setattr(s, "USAGE_CAP_ENABLED", False)
    resp = await client.get("/api/usage/me", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
