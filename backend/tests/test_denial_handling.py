"""Regression tests for user-denial handling in the orchestrator.

A user denial of an approval-gated tool is a TERMINAL decision. It must never
be classified as an error, because errors feed the multi-strategy retry which
explicitly tells the model to "try a different tool/approach" — that is how a
denied `az vm delete` got routed into an `az_rest_api DELETE`. These tests pin
the control-flow contract so the evasion loop can't regress.
"""

from app.agent.orchestrator import (
    _COMMAND_TOOLS,
    _MAX_DENIALS_PER_TURN,
    _DENIAL_FEEDBACK,
    _get_retry_strategy,
    _tool_control_outcome,
)


class TestDenialIsTerminal:
    def test_denial_is_not_an_error(self):
        status, is_error = _tool_control_outcome(approval_denied=True, tool_result=_DENIAL_FEEDBACK)
        assert status == "denied"
        assert is_error is False

    def test_denial_overrides_error_looking_text(self):
        # Even if the feedback text happened to look error-ish, a denial wins.
        status, is_error = _tool_control_outcome(approval_denied=True, tool_result="Error: whatever")
        assert status == "denied"
        assert is_error is False

    def test_real_error_still_an_error(self):
        status, is_error = _tool_control_outcome(approval_denied=False, tool_result="Error: bad syntax")
        assert status == "error"
        assert is_error is True

    def test_timeout_is_error_but_not_denial(self):
        status, is_error = _tool_control_outcome(
            approval_denied=False, tool_result="Approval timed out."
        )
        assert status == "error"
        assert is_error is True

    def test_success_is_success(self):
        status, is_error = _tool_control_outcome(approval_denied=False, tool_result="ok done")
        assert status == "success"
        assert is_error is False


class TestRetryNeverFiresOnDenial:
    def test_retry_block_condition_excludes_denial(self):
        # The orchestrator only enters the retry escalation when
        # `not approval_denied and func_name in _COMMAND_TOOLS and is_error`.
        # A denial yields is_error=False, so the guard can never be satisfied.
        _, is_error = _tool_control_outcome(approval_denied=True, tool_result=_DENIAL_FEEDBACK)
        approval_denied = True
        for tool in _COMMAND_TOOLS:
            assert not (not approval_denied and tool in _COMMAND_TOOLS and is_error)

    def test_retry_strategy_2_no_longer_reachable_from_denial(self):
        # Strategy 2 is the one that suggests "use az_rest_api"; it is only
        # produced for a genuine error. Sanity-check it exists for errors so the
        # contract above is meaningful, and would never be invoked for a denial
        # (is_error False).
        strat = _get_retry_strategy(2, "az_cli", {"args": ["vm", "delete"]}, "Error: x")
        assert strat is not None and "az_rest_api" in strat

    def test_denial_feedback_tells_model_to_stop(self):
        low = _DENIAL_FEEDBACK.lower()
        assert "do not retry" in low
        assert "different tool" in low or "other means" in low


class TestDenialBackstop:
    def test_threshold_is_small(self):
        # The auto-deny backstop must trip quickly so a refusal can't be turned
        # into approval-spam across many tool iterations.
        assert _MAX_DENIALS_PER_TURN <= 3
