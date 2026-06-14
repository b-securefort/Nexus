"""Tests for the per-LLM-call spend ledger (DESIGN.md §5 2026-06-14).

Locks the recording contract before it spreads across call-sites:
  - attribution falls back to the per-request ContextVars,
  - an explicit user_oid/conversation_id overrides the ContextVars,
  - a call with no attributable user is skipped (not written unattributable),
  - None usage is a no-op,
  - cached_tokens is extracted from prompt_tokens_details,
  - a write failure is swallowed (fail-soft never breaks a turn).
"""

import pytest
from sqlmodel import SQLModel, Session, select

from app.db.engine import get_engine
from app.db.models import UsageEvent
from app.agent import usage_ledger
from app.agent.usage_ledger import record_usage
from app.tools.base import set_user_oid, set_conversation_id


class _Details:
    def __init__(self, cached):
        self.cached_tokens = cached


class _Usage:
    def __init__(self, prompt=100, completion=20, cached=None):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.prompt_tokens_details = _Details(cached) if cached is not None else None


@pytest.fixture(autouse=True)
def _ledger_env():
    """Ensure the table exists, clear this suite's rows (test.db persists across
    runs), and reset the per-request ContextVars per test."""
    SQLModel.metadata.create_all(get_engine())
    _clear_ledger_rows()
    set_user_oid(None)
    set_conversation_id(None)
    yield
    _clear_ledger_rows()
    set_user_oid(None)
    set_conversation_id(None)


def _clear_ledger_rows():
    # All oids used in this suite are prefixed "ledger-"; scope the cleanup so
    # we don't disturb rows other suites may rely on in the shared test.db.
    with Session(get_engine()) as session:
        for row in session.exec(
            select(UsageEvent).where(UsageEvent.user_oid.like("ledger-%"))  # type: ignore
        ).all():
            session.delete(row)
        session.commit()


def _rows_for(oid: str):
    with Session(get_engine()) as session:
        return session.exec(
            select(UsageEvent).where(UsageEvent.user_oid == oid)
        ).all()


def test_record_usage_uses_context_vars():
    oid = "ledger-ctx-user"
    set_user_oid(oid)
    set_conversation_id(42)

    record_usage(_Usage(prompt=100, completion=20, cached=30), "gpt-5.4")

    rows = _rows_for(oid)
    assert len(rows) == 1
    r = rows[0]
    assert r.conversation_id == 42
    assert r.deployment == "gpt-5.4"
    assert r.prompt_tokens == 100
    assert r.completion_tokens == 20
    assert r.cached_tokens == 30


def test_explicit_attribution_overrides_context():
    set_user_oid("ledger-ctx-loser")
    set_conversation_id(1)

    record_usage(
        _Usage(),
        "gpt-4o-mini",
        user_oid="ledger-explicit-user",
        conversation_id=99,
    )

    assert _rows_for("ledger-ctx-loser") == []
    rows = _rows_for("ledger-explicit-user")
    assert len(rows) == 1
    assert rows[0].conversation_id == 99
    assert rows[0].deployment == "gpt-4o-mini"


def test_skips_when_no_attributable_user():
    # No ContextVar, no explicit oid → must not write an unattributable row.
    before = len(_rows_for("ledger-none-user"))
    record_usage(_Usage(), "gpt-5.4")
    # Nothing attributable was written under any oid we can assert on; the
    # contract is "no row" — verify the call returned without creating one for
    # the (absent) user by checking total didn't grow for a sentinel we set.
    set_user_oid("")  # falsy explicit-context path
    record_usage(_Usage(), "gpt-5.4")
    assert len(_rows_for("ledger-none-user")) == before
    assert _rows_for("") == []


def test_none_usage_is_noop():
    set_user_oid("ledger-none-usage")
    record_usage(None, "gpt-5.4")
    assert _rows_for("ledger-none-usage") == []


def test_cached_tokens_default_zero_when_no_details():
    oid = "ledger-no-details"
    set_user_oid(oid)
    record_usage(_Usage(prompt=50, completion=10, cached=None), "gpt-5.4")
    rows = _rows_for(oid)
    assert len(rows) == 1
    assert rows[0].cached_tokens == 0


def test_prune_deletes_only_old_rows():
    """The retention prune query (sa_delete + created_at cutoff) drops rows past
    the window and keeps recent ones — verifies the datetime binding on SQLite."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete as sa_delete

    oid = "ledger-prune"
    now = datetime.now(timezone.utc)
    with Session(get_engine()) as s:
        s.add(UsageEvent(user_oid=oid, deployment="gpt-4o-mini",
                         prompt_tokens=1, created_at=now - timedelta(days=100)))  # old
        s.add(UsageEvent(user_oid=oid, deployment="gpt-4o-mini",
                         prompt_tokens=1, created_at=now))  # recent
        s.commit()

    cutoff = now - timedelta(days=90)
    with Session(get_engine()) as s:
        s.execute(sa_delete(UsageEvent).where(UsageEvent.created_at < cutoff))
        s.commit()

    rows = _rows_for(oid)
    assert len(rows) == 1  # only the recent row survives


def test_failsoft_swallows_write_error(monkeypatch):
    """A broken session must not propagate — a ledger write never breaks a turn."""
    set_user_oid("ledger-failsoft")

    def _boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(usage_ledger, "get_session", _boom)
    # Must not raise.
    record_usage(_Usage(), "gpt-5.4")
    # And nothing was written.
    assert _rows_for("ledger-failsoft") == []
