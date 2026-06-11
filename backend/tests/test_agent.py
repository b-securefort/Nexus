"""Tests for approval state machine."""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from app.agent.approvals import (
    create_pending_approval,
    resolve_approval,
    get_pending_approval_for_conversation,
    _approval_events,
    _approval_results,
)
from app.db.models import PendingApproval


class TestApprovalStateMachine:
    """Test approval state transitions."""

    def test_create_pending(self, db_session):
        approval = create_pending_approval(
            session=db_session,
            conversation_id=1,
            user_oid="user-1",
            tool_name="execute_script",
            tool_args_json='{"command": "ls"}',
            reason="List files",
        )
        assert approval.status == "pending"
        assert approval.tool_name == "execute_script"
        assert approval.id in _approval_events

    def test_approve(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "execute_script", '{}', "test"
        )
        resolved = resolve_approval(db_session, approval.id, "approve")
        assert resolved is True

        # Check DB state
        row = db_session.get(PendingApproval, approval.id)
        assert row.status == "approved"
        assert row.resolved_at is not None

    def test_deny(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "execute_script", '{}', "test"
        )
        resolved = resolve_approval(db_session, approval.id, "deny")
        assert resolved is True

        row = db_session.get(PendingApproval, approval.id)
        assert row.status == "denied"

    def test_cannot_resolve_twice(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "execute_script", '{}', "test"
        )
        resolve_approval(db_session, approval.id, "approve")
        result = resolve_approval(db_session, approval.id, "deny")
        assert result is False  # Already resolved

    def test_get_pending_for_conversation(self, db_session):
        create_pending_approval(
            db_session, 1, "user-1", "execute_script", '{}', "test"
        )
        pending = get_pending_approval_for_conversation(db_session, 1)
        assert pending is not None
        assert pending.status == "pending"

    def test_no_pending_after_resolve(self, db_session):
        approval = create_pending_approval(
            db_session, 1, "user-1", "execute_script", '{}', "test"
        )
        resolve_approval(db_session, approval.id, "approve")
        pending = get_pending_approval_for_conversation(db_session, 1)
        assert pending is None


class TestSystemPromptComposition:
    """Test system prompt composition."""

    def test_compose_system_prompt(self):
        from app.agent.orchestrator import _compose_system_prompt
        from app.skills.models import Skill
        from app.auth.models import User

        skill = Skill(
            id="shared:test",
            name="test",
            display_name="Test",
            description="",
            system_prompt="You are a test assistant.",
            tools=[],
        )
        user = User(oid="u1", email="test@test.com", display_name="Test User")

        prompt, retrieved_ids, segments = _compose_system_prompt(skill, user)
        assert "You are a test assistant." in prompt
        assert "Test User" in prompt
        assert "test@test.com" in prompt
        assert isinstance(retrieved_ids, list)
        # Structural segments back the context-usage gauge.
        assert "System prompt" in segments
        assert "Knowledge base" in segments
        assert "You are a test assistant." in segments["System prompt"]


class TestStaleRenderGuard:
    """A failed PNG export must not attach the PREVIOUS iteration's image —
    the model would vision-review (and the user would be shown) a picture that
    doesn't match the .drawio that was just written."""

    def _setup(self, tmp_path, monkeypatch, *, png_older: bool):
        import os
        import time
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "output"
        out.mkdir()
        png = out / "arch.png"
        drawio = out / "arch.drawio"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
        drawio.write_text("<mxfile/>")
        now = time.time()
        if png_older:
            os.utime(png, (now - 60, now - 60))     # png from a previous run
            os.utime(drawio, (now, now))            # source just rewritten
        else:
            os.utime(drawio, (now - 60, now - 60))
            os.utime(png, (now, now))               # fresh render
        return {"filename": "arch"}

    def test_fresh_render_attaches(self, tmp_path, monkeypatch):
        from app.agent.orchestrator import (
            _attachment_for_rendered_png, _build_render_review_message,
        )
        args = self._setup(tmp_path, monkeypatch, png_older=False)
        assert _attachment_for_rendered_png(args, "generate_structured_diagram") is not None
        assert _build_render_review_message(args, "generate_structured_diagram") is not None

    def test_stale_render_is_skipped(self, tmp_path, monkeypatch):
        from app.agent.orchestrator import (
            _attachment_for_rendered_png, _build_render_review_message,
        )
        args = self._setup(tmp_path, monkeypatch, png_older=True)
        assert _attachment_for_rendered_png(args, "generate_structured_diagram") is None
        assert _build_render_review_message(args, "generate_structured_diagram") is None

    def test_no_drawio_source_never_stale(self, tmp_path, monkeypatch):
        """generate_python_diagram has no .drawio intermediate — its PNG can't
        be judged against a source and must keep attaching."""
        import os
        import time
        from app.agent.orchestrator import _attachment_for_rendered_png
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "output"
        out.mkdir()
        png = out / "flow.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
        os.utime(png, (time.time() - 3600, time.time() - 3600))
        assert _attachment_for_rendered_png({"filename": "flow"}, "generate_python_diagram") is not None


class TestReviewConvergenceGovernor:
    """The render-review message must escalate with the per-file render count
    (conv #355: 21 successful renders, each reviewed into 'actual problems',
    until the iteration cap killed the turn)."""

    def _fresh_render_args(self, tmp_path, monkeypatch) -> dict:
        monkeypatch.chdir(tmp_path)
        out = tmp_path / "output"
        out.mkdir()
        (out / "arch.png").write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
        (out / "arch.drawio").write_text("<mxfile/>")
        return {"filename": "arch"}

    def _text(self, msg: dict) -> str:
        return msg["content"][0]["text"]

    def test_early_renders_use_open_review(self, tmp_path, monkeypatch):
        from app.agent.orchestrator import _build_render_review_message
        args = self._fresh_render_args(tmp_path, monkeypatch)
        msg = _build_render_review_message(args, "generate_structured_diagram",
                                           render_count=1)
        assert "Review it against what was agreed" in self._text(msg)

    def test_soft_cap_demands_semantic_only(self, tmp_path, monkeypatch):
        from app.agent.orchestrator import _build_render_review_message
        args = self._fresh_render_args(tmp_path, monkeypatch)
        msg = _build_render_review_message(args, "generate_structured_diagram",
                                           render_count=3)
        text = self._text(msg)
        assert "diminishing returns" in text
        assert "ONE global fix" in text

    def test_hard_cap_instructs_stop(self, tmp_path, monkeypatch):
        from app.agent.orchestrator import _build_render_review_message
        args = self._fresh_render_args(tmp_path, monkeypatch)
        msg = _build_render_review_message(args, "generate_structured_diagram",
                                           render_count=5)
        assert "STOP iterating" in self._text(msg)

    def test_governor_text_still_matches_stale_drop_prefix(self, tmp_path, monkeypatch):
        """Escalated review messages must still be purged by
        _drop_stale_render_reviews when a newer render supersedes them."""
        from app.agent.orchestrator import (
            _build_render_review_message, _drop_stale_render_reviews,
        )
        args = self._fresh_render_args(tmp_path, monkeypatch)
        old = _build_render_review_message(args, "generate_structured_diagram",
                                           render_count=3)
        new = _build_render_review_message(args, "generate_structured_diagram",
                                           render_count=4)
        messages = [old]
        assert _drop_stale_render_reviews(messages, new) == 1
        assert messages == []


class TestRateLimitRetry:
    """A 429 on the main chat stream must retry with backoff, not kill the turn."""

    def _rate_limit_error(self):
        import httpx
        from openai import RateLimitError
        resp = httpx.Response(
            429, headers={"retry-after": "0"},
            request=httpx.Request("POST", "http://test"),
        )
        return RateLimitError("Too Many Requests", response=resp, body=None)

    def test_retries_then_succeeds(self, monkeypatch):
        from app.agent import orchestrator as orch
        monkeypatch.setattr(orch.time, "sleep", lambda s: None)
        calls = {"n": 0}

        class FakeCompletions:
            def create(self, **kw):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise TestRateLimitRetry._rate_limit_error(TestRateLimitRetry())
                return "STREAM"

        class FakeClient:
            chat = type("C", (), {"completions": FakeCompletions()})()

        out = orch._create_stream_with_429_retry(FakeClient(), {})
        assert out == "STREAM"
        assert calls["n"] == 3

    def test_exhausted_retries_raise(self, monkeypatch):
        from openai import RateLimitError
        from app.agent import orchestrator as orch
        monkeypatch.setattr(orch.time, "sleep", lambda s: None)
        helper = self

        class FakeCompletions:
            def create(self, **kw):
                raise helper._rate_limit_error()

        class FakeClient:
            chat = type("C", (), {"completions": FakeCompletions()})()

        import pytest as _pytest
        with _pytest.raises(RateLimitError):
            orch._create_stream_with_429_retry(FakeClient(), {})

    def test_non_429_propagates_immediately(self, monkeypatch):
        from app.agent import orchestrator as orch
        calls = {"n": 0}

        class FakeCompletions:
            def create(self, **kw):
                calls["n"] += 1
                raise ValueError("boom")

        class FakeClient:
            chat = type("C", (), {"completions": FakeCompletions()})()

        import pytest as _pytest
        with _pytest.raises(ValueError):
            orch._create_stream_with_429_retry(FakeClient(), {})
        assert calls["n"] == 1

    def test_retry_after_header_respected_and_capped(self):
        from app.agent.orchestrator import _retry_after_seconds
        err = self._rate_limit_error()
        assert _retry_after_seconds(err) == 0.0
        err.response.headers = {"retry-after": "120"}
        assert _retry_after_seconds(err) == 30.0     # capped
        err.response.headers = {}
        assert _retry_after_seconds(err) == 5.0      # default


class TestStaleReviewDrop:
    """Superseded render-review images must not accumulate across iterations."""

    def _review(self, name: str, marker: str) -> dict:
        return {"role": "user", "content": [
            {"type": "text", "text": f"Rendered image of {name}. Review it ({marker})."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]}

    def test_drops_older_review_of_same_file(self):
        from app.agent.orchestrator import _drop_stale_render_reviews
        messages = [
            {"role": "user", "content": "draw it"},
            self._review("arch.drawio", "old"),
            {"role": "assistant", "content": "ok"},
        ]
        removed = _drop_stale_render_reviews(messages, self._review("arch.drawio", "new"))
        assert removed == 1
        assert len(messages) == 2
        assert all("old" not in str(m.get("content")) for m in messages)

    def test_keeps_reviews_of_other_files_and_normal_messages(self):
        from app.agent.orchestrator import _drop_stale_render_reviews
        messages = [
            self._review("other.drawio", "keep"),
            {"role": "user", "content": [{"type": "text", "text": "look at this:"},
                                         {"type": "image_url", "image_url": {"url": "x"}}]},
        ]
        removed = _drop_stale_render_reviews(messages, self._review("arch.drawio", "new"))
        assert removed == 0
        assert len(messages) == 2
