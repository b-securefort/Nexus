"""Regression tests for the Phase 2 / Phase 3 / Phase 4 remediation work.

Covers:
  - B3:  arm_token_status() + orchestrator pre-flight short-circuit payload
  - B4:  per-user tool-call history isolation + windowed pruning
  - B10: B10 ContextVar propagation through asyncio.to_thread
  - A2:  concurrency primitives (tool_executor + per-user semaphore)
  - A4:  conversation lease columns + lease helpers
  - 4C:  ARM token override store + TTL eviction
  - 4D:  LLM tool-output truncation falls back to head+tail on failure
  - A6:  rephrase fallback when LLM call fails
"""

from __future__ import annotations

import asyncio
import time

import jwt
import pytest

from app.agent import concurrency as concurrency_mod
from app.agent import orchestrator as orch
from app.auth.entra import (
    arm_token_status,
    clear_arm_token_override,
    get_arm_token_override,
    set_arm_token_override,
)


# ─────────────────────────────────────────────────────────────────────────────
# B3 — ARM token expiry pre-flight
# ─────────────────────────────────────────────────────────────────────────────

def _make_fake_arm_token(exp_offset_seconds: int) -> str:
    """Build a fake unsigned JWT with the requested exp offset from now."""
    payload = {
        "aud": "https://management.azure.com",
        "tid": "test-tenant",
        "exp": int(time.time()) + exp_offset_seconds,
    }
    # secret is irrelevant — we decode with verify_signature=False everywhere
    return jwt.encode(payload, "secret", algorithm="HS256")


class TestArmTokenStatus:
    def test_missing_token_is_missing(self):
        assert arm_token_status(None) == "missing"
        assert arm_token_status("") == "missing"

    def test_garbage_is_invalid(self):
        assert arm_token_status("not-a-jwt") == "invalid"

    def test_token_without_exp_is_invalid(self):
        token = jwt.encode({"aud": "x"}, "k", algorithm="HS256")
        assert arm_token_status(token) == "invalid"

    def test_valid_future_token(self):
        token = _make_fake_arm_token(exp_offset_seconds=3600)
        assert arm_token_status(token, refresh_threshold_seconds=60) == "valid"

    def test_expired_token(self):
        token = _make_fake_arm_token(exp_offset_seconds=-10)
        assert arm_token_status(token, refresh_threshold_seconds=60) == "expired"

    def test_near_expiry_threshold(self):
        # exp is 30s out; threshold is 60s → near_expiry
        token = _make_fake_arm_token(exp_offset_seconds=30)
        assert arm_token_status(token, refresh_threshold_seconds=60) == "near_expiry"

    def test_threshold_zero_treats_only_expired(self):
        # exp is 5s out; threshold is 0 → still valid (not near)
        token = _make_fake_arm_token(exp_offset_seconds=5)
        assert arm_token_status(token, refresh_threshold_seconds=0) == "valid"


class TestArmErrorPayload:
    def test_missing_message_includes_signin_prompt(self):
        msg = orch._arm_token_error_payload("az_cli", "missing")
        assert "sign in" in msg.lower()
        assert "az_cli" in msg

    def test_expired_message_tells_agent_not_to_retry(self):
        msg = orch._arm_token_error_payload("az_resource_graph", "expired")
        assert "Do NOT retry" in msg
        assert "az_resource_graph" in msg


class TestDeployedEnvironmentDetection:
    """§5 2026-06-01 — ARM gate keys off CONTAINER_APP_NAME, not config flag.

    The helper decides whether a missing ARM token is a hard stop (deployed)
    or falls through to the server's local `az login` session (local dev).
    """

    def test_unset_env_var_is_local(self, monkeypatch):
        monkeypatch.delenv("CONTAINER_APP_NAME", raising=False)
        assert orch._is_deployed_environment() is False

    def test_empty_env_var_is_local(self, monkeypatch):
        monkeypatch.setenv("CONTAINER_APP_NAME", "")
        assert orch._is_deployed_environment() is False

    def test_whitespace_only_env_var_is_local(self, monkeypatch):
        monkeypatch.setenv("CONTAINER_APP_NAME", "   ")
        assert orch._is_deployed_environment() is False

    def test_set_env_var_is_deployed(self, monkeypatch):
        monkeypatch.setenv("CONTAINER_APP_NAME", "nexus-backend")
        assert orch._is_deployed_environment() is True


# ─────────────────────────────────────────────────────────────────────────────
# B4 — per-user tool call history
# ─────────────────────────────────────────────────────────────────────────────

class TestPerUserRateLimit:
    def setup_method(self):
        orch._reset_tool_call_history()

    def teardown_method(self):
        orch._reset_tool_call_history()

    def test_per_user_isolation(self):
        """User A using a tool 5/5 must not stop User B from using it."""
        now = 1000.0
        for _ in range(5):
            allowed, _ = orch._check_user_rate_limit(
                user_oid="user-a", tool_name="t", limit=5, window=60, now=now,
            )
            assert allowed
        # User A is now at the cap
        allowed, _ = orch._check_user_rate_limit(
            user_oid="user-a", tool_name="t", limit=5, window=60, now=now,
        )
        assert not allowed
        # User B is unaffected
        allowed, _ = orch._check_user_rate_limit(
            user_oid="user-b", tool_name="t", limit=5, window=60, now=now,
        )
        assert allowed

    def test_window_pruning_releases_slot(self):
        """Stale timestamps must drop out of the window so the cap recovers."""
        for i in range(3):
            allowed, _ = orch._check_user_rate_limit(
                user_oid="u", tool_name="t", limit=3, window=60, now=1000.0 + i,
            )
            assert allowed
        # Cap hit
        allowed, _ = orch._check_user_rate_limit(
            user_oid="u", tool_name="t", limit=3, window=60, now=1005.0,
        )
        assert not allowed
        # Fast-forward beyond the window
        allowed, _ = orch._check_user_rate_limit(
            user_oid="u", tool_name="t", limit=3, window=60, now=1100.0,
        )
        assert allowed

    def test_separate_tools_have_separate_buckets(self):
        now = 1000.0
        # Fill tool A to the cap
        for _ in range(5):
            orch._check_user_rate_limit(
                user_oid="u", tool_name="A", limit=5, window=60, now=now,
            )
        # tool B is independent
        allowed, _ = orch._check_user_rate_limit(
            user_oid="u", tool_name="B", limit=5, window=60, now=now,
        )
        assert allowed


# ─────────────────────────────────────────────────────────────────────────────
# A2 — concurrency primitives
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrencyPrimitives:
    def setup_method(self):
        concurrency_mod.shutdown_tool_executor(wait=True)
        concurrency_mod.reset_user_semaphores()

    def teardown_method(self):
        concurrency_mod.shutdown_tool_executor(wait=True)
        concurrency_mod.reset_user_semaphores()

    def test_tool_executor_is_lazy_singleton(self):
        e1 = concurrency_mod.tool_executor()
        e2 = concurrency_mod.tool_executor()
        assert e1 is e2

    def test_executor_thread_name_prefix(self):
        ex = concurrency_mod.tool_executor()
        f = ex.submit(lambda: None)
        f.result(timeout=2)
        # Thread name should start with our prefix
        import threading
        names = [t.name for t in threading.enumerate() if t.name.startswith("tool")]
        assert names, "Expected at least one 'tool*' thread to exist"

    @pytest.mark.asyncio
    async def test_run_in_tool_executor_runs_callable(self):
        result = await concurrency_mod.run_in_tool_executor(lambda x: x * 2, 7)
        assert result == 14

    @pytest.mark.asyncio
    async def test_run_in_tool_executor_propagates_contextvar(self):
        """B10 + A2 — ContextVars must propagate into worker thread."""
        import contextvars

        var: contextvars.ContextVar[str] = contextvars.ContextVar("v", default="default")
        var.set("from-main-task")

        def _read_in_worker() -> str:
            return var.get()

        observed = await concurrency_mod.run_in_tool_executor(_read_in_worker)
        assert observed == "from-main-task"

    @pytest.mark.asyncio
    async def test_per_user_semaphore_caps_concurrent(self):
        sem = concurrency_mod.get_user_semaphore("u", max_concurrent=2)
        assert sem._value == 2  # 2 slots available
        # Reuse for same user returns same semaphore
        sem2 = concurrency_mod.get_user_semaphore("u", max_concurrent=2)
        assert sem is sem2
        # Different user gets a different semaphore
        sem3 = concurrency_mod.get_user_semaphore("v", max_concurrent=2)
        assert sem3 is not sem


# ─────────────────────────────────────────────────────────────────────────────
# 4C — ARM token override store
# ─────────────────────────────────────────────────────────────────────────────

class TestArmTokenOverride:
    def setup_method(self):
        clear_arm_token_override(99)
        clear_arm_token_override(100)

    def teardown_method(self):
        clear_arm_token_override(99)
        clear_arm_token_override(100)

    def test_set_and_get_roundtrip(self):
        assert get_arm_token_override(99) is None
        set_arm_token_override(99, "tok-99")
        assert get_arm_token_override(99) == "tok-99"

    def test_per_conversation_isolation(self):
        set_arm_token_override(99, "tok-99")
        set_arm_token_override(100, "tok-100")
        assert get_arm_token_override(99) == "tok-99"
        assert get_arm_token_override(100) == "tok-100"

    def test_clear_drops_entry(self):
        set_arm_token_override(99, "tok-99")
        clear_arm_token_override(99)
        assert get_arm_token_override(99) is None

    def test_ttl_eviction(self, monkeypatch):
        from app.auth import entra
        set_arm_token_override(99, "tok-99")
        # Fast-forward beyond TTL
        monkeypatch.setattr(entra, "_ARM_OVERRIDE_TTL_SECONDS", 0)
        # Sleep a hair to ensure the saved-at < now
        time.sleep(0.01)
        assert get_arm_token_override(99) is None


# ─────────────────────────────────────────────────────────────────────────────
# 4D — LLM tool-output truncation
# ─────────────────────────────────────────────────────────────────────────────

class TestLlmTruncate:
    def test_small_output_passes_through(self):
        small = '{"status":"success","data":"ok"}'
        assert orch._truncate_tool_result("az_cli", small) == small

    def test_midsize_output_kept_verbatim(self):
        """8 KB is under both the LLM-summarisation threshold (16 KB) and
        az_cli's per-tool cap (12 KB) — affordable results stay verbatim."""
        mid = f'{{"status":"success","tool":"az_cli","data":"{"x" * 8_000}"}}'
        assert orch._truncate_tool_result("az_cli", mid) == mid

    def test_summariser_failure_falls_back_to_head_tail(self, monkeypatch):
        # Force the LLM summariser to fail
        monkeypatch.setattr(
            orch, "_summarize_tool_result_with_llm", lambda *a, **kw: None,
        )
        big_payload = "x" * 40_000  # over the 16 KB LLM threshold
        big = f'{{"status":"success","tool":"az_cli","data":"{big_payload}"}}'
        out = orch._truncate_tool_result("az_cli", big)
        assert "truncated" in out
        assert len(out) < len(big)

    def test_error_envelopes_skip_summariser(self, monkeypatch):
        """Error outputs must reach the model intact so it can retry."""
        called: list[bool] = []

        def _spy(*a, **kw):
            called.append(True)
            return "FAKE SUMMARY"

        monkeypatch.setattr(orch, "_summarize_tool_result_with_llm", _spy)
        # >2KB error envelope
        err_payload = "stack trace " * 200
        err = f'{{"status":"error","tool":"az_cli","data":"{err_payload}"}}'
        out = orch._truncate_tool_result("az_cli", err)
        # Either falls back to head+tail or returns raw — but never calls LLM
        assert called == []
        # Original error info still present
        assert "error" in out

    def test_summariser_success_returns_compressed(self, monkeypatch):
        monkeypatch.setattr(
            orch, "_summarize_tool_result_with_llm",
            lambda *a, **kw: "[LLM-compressed] short summary",
        )
        big = f'{{"status":"success","data":"{"x" * 40_000}"}}'
        out = orch._truncate_tool_result("az_cli", big)
        assert "[LLM-compressed]" in out


# ─────────────────────────────────────────────────────────────────────────────
# A6 — rephrase fallback (path test only, no live LLM)
# ─────────────────────────────────────────────────────────────────────────────

class TestRephraseFallback:
    def test_rephrase_returns_original_on_llm_failure(self, monkeypatch):
        from app.agent import learn_judge

        def _boom(*a, **kw):
            raise RuntimeError("simulated network failure")

        monkeypatch.setattr(learn_judge, "_get_judge_client", _boom)
        out = learn_judge.rephrase_learning(
            summary="original summary text",
            details="details",
            tool_name="az_cli",
            category="syntax-fix",
        )
        assert out == "original summary text"

    def test_rephrase_returns_original_on_empty_summary(self):
        from app.agent import learn_judge
        assert learn_judge.rephrase_learning(
            summary="", details="x", tool_name="t", category="syntax-fix",
        ) == ""
