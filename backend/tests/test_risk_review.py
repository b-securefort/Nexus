"""Tests for the advisory approval risk review (DESIGN.md §5 2026-06-04)."""

import types

import pytest

from app.agent import risk_review
from app.agent.risk_review import (
    CAUTION,
    DESTRUCTIVE,
    SAFE,
    assess_risk,
    deterministic_floor,
    _shell_floor,
)
from app.config import get_settings


# ── Deterministic floor ───────────────────────────────────────────────────────


class TestDeterministicFloor:
    def test_az_delete_is_destructive(self):
        assert deterministic_floor("az_cli", {"args": ["group", "delete", "-n", "rg1"]}) == DESTRUCTIVE

    def test_az_purge_is_destructive(self):
        assert deterministic_floor("az_cli", {"args": ["keyvault", "purge", "-n", "kv"]}) == DESTRUCTIVE

    def test_az_list_is_safe(self):
        assert deterministic_floor("az_cli", {"args": ["group", "list"]}) == SAFE

    def test_az_show_is_safe(self):
        assert deterministic_floor("az_cli", {"args": ["vm", "show", "-n", "x", "-g", "rg"]}) == SAFE

    def test_az_create_is_caution(self):
        assert deterministic_floor("az_cli", {"args": ["group", "create", "-n", "rg", "-l", "eastus"]}) == CAUTION

    def test_destructive_token_after_global_flags_still_caught(self):
        # global flags must not be usable as a shield (mirrors _is_blocked behaviour)
        floor = deterministic_floor("az_cli", {"args": ["--only-show-errors", "group", "delete", "-n", "rg"]})
        assert floor == DESTRUCTIVE

    def test_rest_delete_is_destructive(self):
        assert deterministic_floor("az_rest_api", {"method": "DELETE", "url": "/x"}) == DESTRUCTIVE

    def test_rest_get_is_safe(self):
        assert deterministic_floor("az_rest_api", {"method": "GET", "url": "/x"}) == SAFE

    def test_rest_patch_is_caution(self):
        assert deterministic_floor("az_rest_api", {"method": "PATCH", "url": "/x"}) == CAUTION

    def test_unknown_tool_is_caution(self):
        assert deterministic_floor("some_new_tool", {}) == CAUTION


class TestShellFloor:
    def test_unreadable_body_is_caution(self):
        assert _shell_floor(None) == CAUTION

    def test_benign_script_is_caution_not_safe(self):
        assert _shell_floor("Get-AzResourceGroup | Format-Table") == CAUTION

    @pytest.mark.parametrize("body", [
        "rm -rf /tmp/x",
        "Remove-Item -Path . -Recurse -Force",
        "DROP TABLE users;",
        "dd if=/dev/zero of=/dev/sda",
    ])
    def test_destructive_bodies(self, body):
        assert _shell_floor(body) == DESTRUCTIVE


# ── assess_risk: gating, escalate-only, fail-closed ───────────────────────────


def _fake_response(content: str):
    msg = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=msg)
    return types.SimpleNamespace(choices=[choice])


class _FakeClient:
    def __init__(self, content=None, raises=False):
        self._content = content
        self._raises = raises
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._raises:
            raise RuntimeError("boom")
        return _fake_response(self._content)


class TestAssessRisk:
    def test_disabled_returns_floor_only(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", False)
        v = assess_risk("az_cli", {"args": ["group", "delete", "-n", "rg"]})
        assert v.risk_level == DESTRUCTIVE
        assert v.description is None
        assert v.source == "floor"

    def test_llm_can_escalate_above_floor(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(
            risk_review, "_get_client",
            lambda: _FakeClient('{"risk": "destructive", "description": "Deletes all RGs"}'),
        )
        # floor for `group list` is safe; the LLM escalates to destructive
        v = assess_risk("az_cli", {"args": ["group", "list"]})
        assert v.risk_level == DESTRUCTIVE
        assert v.description == "Deletes all RGs"
        assert v.source == "llm"

    def test_llm_cannot_downgrade_below_floor(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(
            risk_review, "_get_client",
            lambda: _FakeClient('{"risk": "safe", "description": "just a delete"}'),
        )
        # floor for `group delete` is destructive; a model "safe" must not win
        v = assess_risk("az_cli", {"args": ["group", "delete", "-n", "rg"]})
        assert v.risk_level == DESTRUCTIVE

    def test_failclosed_to_at_least_caution(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(risk_review, "_get_client", lambda: _FakeClient(raises=True))
        v = assess_risk("az_cli", {"args": ["group", "list"]})  # floor=safe
        assert v.risk_level == CAUTION  # never "safe" on review failure
        assert v.source == "fallback"
        assert v.description and "unavailable" in v.description.lower()

    def test_failclosed_keeps_destructive_floor(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "RISK_REVIEW_ENABLED", True)
        monkeypatch.setattr(risk_review, "_get_client", lambda: _FakeClient(raises=True))
        v = assess_risk("az_cli", {"args": ["group", "delete", "-n", "rg"]})
        assert v.risk_level == DESTRUCTIVE
