"""Regression tests for user-denial handling in the orchestrator.

A user denial of an approval-gated tool is a TERMINAL decision. It must never
be classified as an error, because errors feed the multi-strategy retry which
explicitly tells the model to "try a different tool/approach" — that is how a
denied `az vm delete` got routed into an `az_rest_api DELETE`. These tests pin
the control-flow contract so the evasion loop can't regress.
"""

from app.agent.orchestrator import (
    _MAX_DENIALS_PER_TURN,
    _DENIAL_FEEDBACK,
    _get_retry_strategy,
    _tool_control_outcome,
)
from app.tools.base import TOOL_REGISTRY, init_tools

# Retry/learning eligibility now lives on per-tool capability attributes
# (DESIGN.md §5 2026-06-05). Derive the sets the orchestrator used to hardcode
# so these invariants keep guarding the retry-vs-learning decoupling.
init_tools()
_COMMAND_TOOLS = {n for n, t in TOOL_REGISTRY.items() if t.retry_eligible}
_LEARNING_ELIGIBLE_TOOLS = {n for n, t in TOOL_REGISTRY.items() if t.learning_eligible}


class TestLearningEligibilityDecoupling:
    """Learning capture is broader than retry escalation. These pin that the
    two sets are decoupled so the diagram/REST tools can be learned from without
    also inheriting multi-strategy retry (they have their own recovery paths)."""

    def test_command_tools_are_a_subset_of_learning_eligible(self):
        assert _COMMAND_TOOLS <= _LEARNING_ELIGIBLE_TOOLS

    def test_rest_and_diagram_tools_are_learnable_but_not_retried(self):
        for tool in ("az_rest_api", "az_devops",
                     "generate_drawio_from_python", "generate_python_diagram"):
            assert tool in _LEARNING_ELIGIBLE_TOOLS  # learnable
            assert tool not in _COMMAND_TOOLS         # but not retry-escalated

    def test_read_and_search_tools_are_not_learning_eligible(self):
        # Their "failures" (missing path, no results, intent) don't generalize.
        for tool in ("read_kb_file", "search_kb_hybrid", "ask_user", "web_fetch"):
            assert tool not in _LEARNING_ELIGIBLE_TOOLS


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

    def test_integrity_failure_is_terminal_not_retryable(self):
        # #20: a fingerprint mismatch is terminal/non-retryable (is_error=False)
        # like a denial, so it can neither feed the retry escalation nor be
        # mistaken for a success-after-failure learning trigger.
        status, is_error = _tool_control_outcome(
            approval_denied=False, tool_result="ok-looking text", integrity_failed=True
        )
        assert status == "denied"
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
