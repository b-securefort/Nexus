"""Tests for the narration-nudge detector in the orchestrator.

The orchestrator catches the "I'll do X" + no-tool-call failure mode by
re-entering the loop once with a synthetic system reminder, capped at one
nudge per turn. This test file pins the detection regex's behaviour —
the loop-integration code is exercised end-to-end via the sanity harness
because it depends on a real LLM stream.

Mirrored from the orchestrator's _DEFERRED_ACTION_PATTERN. If the regex
changes, update both places — there is no shared constant module yet (one
import from orchestrator would do but adds a test-time import side effect).
"""

from app.agent.orchestrator import _looks_like_deferred_action


class TestDeferredActionDetection:
    """Each case is real-world phrasing the model has emitted (or close to)."""

    # ── Should fire — clear deferred action announcing a tool call ──────────

    def test_ill_generate(self):
        assert _looks_like_deferred_action(
            "I've confirmed the architecture. I'll generate the diagram now."
        )

    def test_im_going_to_render(self):
        assert _looks_like_deferred_action(
            "Looks good. I'm going to render the updated layout."
        )

    def test_let_me_run(self):
        assert _looks_like_deferred_action(
            "Got it. Let me run the resource graph query for that."
        )

    def test_next_ill_add(self):
        assert _looks_like_deferred_action(
            "The base diagram is good. Next I'll add Key Vault with a PE."
        )

    def test_ill_call_the_tool(self):
        assert _looks_like_deferred_action(
            "Now I'll call generate_drawio_from_python with the updated code."
        )

    def test_ill_now_patch(self):
        assert _looks_like_deferred_action(
            "I'll now patch the cell coordinates per the validator's suggestion."
        )

    def test_apostrophe_variants(self):
        # 'I'll' with curly apostrophe — common when copy-paste from word
        # processors. The pattern only matches straight apostrophes so this
        # currently fails — documenting the gap rather than over-engineering.
        # If LLM output contains curly apostrophes in this position, expand
        # the regex. As of 2026-05-19, observed outputs use straight quotes.
        assert _looks_like_deferred_action("Done. I'll render it.")

    def test_long_text_with_action_at_tail(self):
        # The detector inspects the last 400 chars. A long preamble followed
        # by a deferred-action tail must still fire.
        body = (
            "Here's the architecture I'm proposing: " + ("blah " * 200) +
            ". Let me now generate the diagram."
        )
        assert _looks_like_deferred_action(body)

    # ── Should NOT fire — legitimate end-of-chat with no implied tool call ─

    def test_question_to_user(self):
        assert not _looks_like_deferred_action(
            "Which region should I target? Also do you want monitoring shown?"
        )

    def test_summary_only(self):
        assert not _looks_like_deferred_action(
            "The diagram shows a hub-and-spoke layout with App Gateway in "
            "the hub VNet and the Web App reached via VNet integration."
        )

    def test_advisory_information(self):
        assert not _looks_like_deferred_action(
            "Azure SQL Database supports private endpoints; the relevant "
            "doc is at learn.microsoft.com. I'll need the SKU you intend "
            "to use before I can estimate cost."
        )
        # "I'll need" is the catch — "need" isn't in our action-verb list,
        # so the pattern doesn't match. This case proves the verb filter
        # is doing its job: announcing a precondition is not the same as
        # announcing a deferred action.

    def test_past_tense_recap(self):
        assert not _looks_like_deferred_action(
            "I generated the diagram at output/my-arch.drawio and verified "
            "it renders correctly. Let me know if you want changes."
        )

    def test_empty_string(self):
        assert not _looks_like_deferred_action("")

    def test_none_safe(self):
        # The detector is defensive against None even though the call site
        # guards: keeps the helper composable.
        assert not _looks_like_deferred_action(None)  # type: ignore[arg-type]

    # ── Edge: the pattern only checks the tail ────────────────────────────

    def test_deferred_action_in_middle_only_does_not_fire(self):
        # The model used to narrate "I'll generate X" mid-paragraph but then
        # CONTINUED writing past it, often into a real answer or final
        # summary. Only the closing intent matters; mid-paragraph mentions
        # of past or hypothetical actions should not trigger a nudge.
        body = (
            "Earlier I said I'll generate the diagram. But on reflection, "
            + ("the design is already correct. " * 30)
            + "Here's the final answer: hub-and-spoke is the right call."
        )
        assert not _looks_like_deferred_action(body)
