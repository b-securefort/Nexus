"""Tests for the forensic audit log (DESIGN.md §5 2026-06-15, hardening #23).

Covers the load-bearing invariants of the `tool_executions` append-only log:
  - the row survives deletion of its conversation (no cascade — the audited
    operator can't erase evidence by deleting their chat);
  - the write helper is fail-open (a broken DB never breaks a turn);
  - `superadmin` mirrors architect's access and `require_superadmin` is
    fail-closed under real auth but passes through under DEV_AUTH_BYPASS.
"""

from contextlib import contextmanager

import pytest
from sqlmodel import select

from app.db.models import Conversation, ToolExecution
from tests.conftest import AUTH_HEADERS


# ─────────────────────────────────────────────────────────────────────────────
# Survival: the row outlives its conversation (no cascading FK)
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_row_survives_conversation_delete(db_session):
    """conversation_id is a plain nullable column, NOT a cascading FK, so deleting
    the conversation (which an audited user can do to their own chat) must leave
    the audit row standing — that's the whole forensic point."""
    conv = Conversation(
        user_oid="u-1", title="t", skill_id="chat-with-kb", skill_snapshot_json="{}",
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    db_session.add(ToolExecution(
        user_oid="u-1", user_email="u1@x", conversation_id=conv.id,
        tool_name="az_cli", rendered_command="az group delete -n rg",
        outcome="success", risk_level="destructive", reason="cleanup",
    ))
    db_session.commit()

    db_session.delete(conv)
    db_session.commit()

    rows = db_session.exec(select(ToolExecution)).all()
    assert len(rows) == 1
    assert rows[0].conversation_id == conv.id  # dangling pointer kept, row intact
    assert rows[0].rendered_command == "az group delete -n rg"


# ─────────────────────────────────────────────────────────────────────────────
# Write helper: happy path + fail-open
# ─────────────────────────────────────────────────────────────────────────────

def test_record_tool_execution_writes_row(db_session, monkeypatch):
    from app.agent import audit

    @contextmanager
    def fake_session():
        yield db_session

    monkeypatch.setattr(audit, "get_session", fake_session)
    audit.record_tool_execution(
        user_oid="u-2", user_email="u2@x", conversation_id=7,
        tool_name="execute_script", rendered_command="execute script foo.ps1",
        outcome="error", review_fingerprint="abc123", risk_level="caution",
        reason="run cleanup",
    )
    row = db_session.exec(select(ToolExecution)).one()
    assert row.user_oid == "u-2"
    assert row.outcome == "error"
    assert row.review_fingerprint == "abc123"


def test_record_tool_execution_is_fail_open(monkeypatch):
    """A DB failure during the audit write must NOT propagate — it is a forensic
    aid, never a gate on the tool the user already approved."""
    from app.agent import audit

    @contextmanager
    def boom():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(audit, "get_session", boom)
    # Must return None and swallow the error.
    assert audit.record_tool_execution(
        user_oid="u", user_email="e", conversation_id=None,
        tool_name="az_cli", rendered_command="az ...", outcome="success",
    ) is None


# ─────────────────────────────────────────────────────────────────────────────
# RBAC: superadmin mirrors architect; require_superadmin gate
# ─────────────────────────────────────────────────────────────────────────────

def test_superadmin_mirrors_architect_access():
    from app.auth.rbac import DEFAULT_ACCESS_MAP

    arch = DEFAULT_ACCESS_MAP["architect"]
    su = DEFAULT_ACCESS_MAP["superadmin"]
    assert su["skills"] == arch["skills"]
    assert su["tools"] == arch["tools"]
    # Distinct list objects so a later mutation of one tier can't leak into the other.
    assert su["tools"] is not arch["tools"]


@pytest.mark.asyncio
async def test_audit_endpoint_dev_bypass_allows(client):
    """conftest runs DEV_AUTH_BYPASS=true, so the dev user (no roles) passes the
    require_superadmin gate and gets a list back."""
    resp = await client.get("/api/audit", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_require_superadmin_rejects_architect(monkeypatch):
    """Under real auth, even an architect is rejected — the reviewer tier must
    outrank the operators it audits."""
    from fastapi import HTTPException
    from app.auth.models import User
    from app.config import get_settings
    from app.deps import require_superadmin

    monkeypatch.setattr(get_settings(), "DEV_AUTH_BYPASS", False, raising=False)
    architect = User(oid="u-a", email="a@x", display_name="Arch", roles=["architect"])
    with pytest.raises(HTTPException) as exc:
        await require_superadmin(user=architect)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_superadmin_accepts_superadmin(monkeypatch):
    from app.auth.models import User
    from app.config import get_settings
    from app.deps import require_superadmin

    monkeypatch.setattr(get_settings(), "DEV_AUTH_BYPASS", False, raising=False)
    su = User(oid="u-s", email="s@x", display_name="SU", roles=["superadmin"])
    assert await require_superadmin(user=su) is su
