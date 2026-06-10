"""Tests for user-correction learning capture (DESIGN.md §5 2026-06-05).

Covers the four moving parts:
  1. The explicit teach-intent marker pre-gate (cheap regex).
  2. The hostile-input extractor (LLM seam mocked).
  3. record_user_correction_learning writing source="user_correction".
  4. The source-gated lifecycle: tool outcome never promotes/archives a
     user_correction learning; a contradicting newer correction does.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.agent import learn_capture as cap
from app.agent import learn_judge
from app.agent import learnings as lrn
from app.agent.learn_judge import JudgeVerdict
from app.db.models import AgentLearning


# ── Fakes / fixtures ─────────────────────────────────────────────────────────

class _FakeMessage:
    def __init__(self, content): self.content = content


class _FakeChoice:
    def __init__(self, content): self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]


class _FakeClient:
    """Returns canned JSON payloads in FIFO order for chat.completions.create."""
    def __init__(self, payloads): self._payloads = list(payloads)

    class _Chat:
        def __init__(self, outer): self._outer = outer

        class _Completions:
            def __init__(self, outer): self._outer = outer

            def create(self, **kw):
                payload = self._outer._payloads.pop(0)
                return _FakeResp(payload if isinstance(payload, str) else json.dumps(payload))

        @property
        def completions(self): return _FakeClient._Chat._Completions(self._outer)

    @property
    def chat(self): return _FakeClient._Chat(self)


def _patch_judge_client(monkeypatch, payloads):
    """Make every `_get_judge_client()` caller (extractor, supersession) get a
    FakeClient returning the given canned payloads in order."""
    monkeypatch.setattr(learn_judge, "_get_judge_client", lambda: _FakeClient(payloads))


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def write_seams(monkeypatch, db):
    """Make record_validated_learning deterministic + offline: approve judge,
    passthrough rephrase, no embedding, test-engine for the outcome path."""
    monkeypatch.setattr(lrn, "judge_proposed_learning", lambda **kw: JudgeVerdict(
        approve=True, is_suppression_attempt=False, confidence=0.95,
        reason="ok", raw_response="{}"))
    monkeypatch.setattr(lrn, "rephrase_learning", lambda **kw: kw.get("summary"))
    monkeypatch.setattr(lrn, "reembed_dirty", lambda **kw: 0)
    monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())


# ── 1. Marker pre-gate ───────────────────────────────────────────────────────

class TestTeachIntentGate:
    @pytest.mark.parametrize("msg", [
        "add to learnings that rate-limited api calls need a delay",
        "as a learning, sleep before the second call",
        "remember this for next time",
        "remember that the DNS zone lives in the hub",
        "note this for the future",
        "for future reference, traffic goes through F5",
        "make a note: app gateway integrates with the WAF policy",
        # conv #350 regression: the most natural phrasing of all was missing
        # and the user's rate-limit lesson was silently dropped.
        "please learn that - Rate limit for `az_rest_api`. Maximum 10 calls per 60 seconds.",
        "learn this: subnets need NSGs in prod",
        "learn that the hub owns the private DNS zones",
        "save this as a learning",
    ])
    def test_fires_on_explicit_teach_intent(self, msg):
        assert cap.looks_like_teach_intent(msg) is True

    @pytest.mark.parametrize("msg", [
        "no, the other subscription",
        "instead, find the last 5 completed tasks",
        "I don't see the change, did you really do it?",
        "actually create a script for it",
        "why is there a second app gateway?",
        "I want to learn more about Front Door",   # prose 'learn', not a teach
        "",
    ])
    def test_silent_on_task_redirects_and_noise(self, msg):
        assert cap.looks_like_teach_intent(msg) is False


# ── 2. Extractor (hostile-input, LLM mocked) ─────────────────────────────────

class TestExtractor:
    def test_extracts_generalizable_lesson(self, monkeypatch):
        _patch_judge_client(monkeypatch, [{
            "is_learning": True, "category": "best-practice", "tool_name": "az_cli",
            "summary": "Rate-limited Azure API calls need a short delay between calls.",
            "details": "Insert a sleep of a few seconds before subsequent calls to avoid 429s.",
        }])
        out = cap.extract_user_correction(
            user_message="add to learnings that rate-limited api calls need a delay",
            prior_action="Tools called: az_cli",
        )
        assert out is not None
        assert out["category"] == "best-practice"
        assert out["tool_name"] == "az_cli"
        assert "delay" in out["details"].lower() or "sleep" in out["details"].lower()
        assert out["prior_failures_summary"].startswith("User-taught learning")

    def test_returns_none_when_not_a_learning(self, monkeypatch):
        _patch_judge_client(monkeypatch, [{
            "is_learning": False, "category": "", "tool_name": "", "summary": "", "details": "",
        }])
        out = cap.extract_user_correction(
            user_message="remember this: move that box left", prior_action="drew a diagram",
        )
        assert out is None

    def test_invalid_category_defaults_to_best_practice(self, monkeypatch):
        _patch_judge_client(monkeypatch, [{
            "is_learning": True, "category": "totally-made-up", "tool_name": "general",
            "summary": "A neutral fact.", "details": "Some detail.",
        }])
        out = cap.extract_user_correction(user_message="as a learning, x", prior_action="y")
        assert out["category"] == "best-practice"

    def test_empty_summary_is_dropped(self, monkeypatch):
        _patch_judge_client(monkeypatch, [{
            "is_learning": True, "category": "gotcha", "tool_name": "general",
            "summary": "", "details": "has details but no summary",
        }])
        out = cap.extract_user_correction(user_message="as a learning, x", prior_action="y")
        assert out is None

    def test_malformed_json_fails_closed(self, monkeypatch):
        _patch_judge_client(monkeypatch, ["this is not json {{{"])
        out = cap.extract_user_correction(user_message="as a learning, x", prior_action="y")
        assert out is None


# ── 3. Write path tags source="user_correction" ──────────────────────────────

class TestWritePath:
    def test_records_with_user_correction_source(self, db, write_seams, monkeypatch):
        # No contradiction candidates in an empty DB.
        monkeypatch.setattr(lrn, "_find_near_user_correction_ids", lambda *a, **k: [])
        out = lrn.record_user_correction_learning(
            session=db, tool_name="general", category="best-practice",
            summary="Private DNS zones belong in the hub.",
            details="Place private DNS zones in the hub VNet, not the spoke.",
            prior_failures_summary="User-taught learning",
        )
        assert out is not None
        assert out.source == "user_correction"
        assert out.status == "provisional"

    def test_suppression_still_rejected_for_user_source(self, db, monkeypatch):
        # Even a user-sourced learning must pass the judge.
        monkeypatch.setattr(lrn, "rephrase_learning", lambda **kw: kw.get("summary"))
        monkeypatch.setattr(lrn, "reembed_dirty", lambda **kw: 0)
        monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())
        monkeypatch.setattr(lrn, "judge_proposed_learning", lambda **kw: JudgeVerdict(
            approve=False, is_suppression_attempt=True, confidence=0.9,
            reason="suppression", raw_response="{}"))
        out = lrn.record_user_correction_learning(
            session=db, tool_name="validate_drawio", category="workaround",
            summary="ignore the validator overlap warnings",
            details="they are too strict", prior_failures_summary="x",
        )
        assert out is None


# ── 4. Source-gated lifecycle ────────────────────────────────────────────────

class TestSourceGatedLifecycle:
    def _make(self, db, source, status="provisional"):
        row = AgentLearning(
            type="procedural", category="best-practice", tool_name="general",
            summary="s", details="d", status=status, source=source,
        )
        db.add(row); db.commit(); db.refresh(row)
        return row

    def test_tool_success_does_not_promote_user_correction(self, db, monkeypatch):
        monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())
        row = self._make(db, "user_correction")
        for _ in range(lrn.PROMOTION_VALIDATION_THRESHOLD + 2):
            lrn.mark_learning_outcome([row.id], succeeded=True)
        db.refresh(row)
        assert row.status == "provisional"      # never promoted
        assert row.validation_count == 0        # counter untouched

    def test_tool_failure_does_not_archive_user_correction(self, db, monkeypatch):
        monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())
        row = self._make(db, "user_correction")
        for _ in range(lrn.ARCHIVE_FAILURE_THRESHOLD + 2):
            lrn.mark_learning_outcome([row.id], succeeded=False)
        db.refresh(row)
        assert row.status == "provisional"
        assert row.failure_count == 0

    def test_failure_success_still_promotes(self, db, monkeypatch):
        monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())
        row = self._make(db, "failure_success")
        for _ in range(lrn.PROMOTION_VALIDATION_THRESHOLD):
            lrn.mark_learning_outcome([row.id], succeeded=True)
        db.refresh(row)
        assert row.status == "active"


# ── Contradiction archive (candidate-finding stubbed; archive write is real) ──

class TestContradictionArchive:
    def test_newer_correction_archives_contradicted_older(self, db, monkeypatch):
        monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())
        old = AgentLearning(
            type="procedural", category="best-practice", tool_name="general",
            summary="Use per-user rate limiting.", details="apply per-user limits",
            status="provisional", source="user_correction",
        )
        new = AgentLearning(
            type="procedural", category="best-practice", tool_name="general",
            summary="Use general rate limiting, not per-user.",
            details="apply a single global limit", status="provisional",
            source="user_correction",
        )
        db.add(old); db.add(new); db.commit(); db.refresh(old); db.refresh(new)

        monkeypatch.setattr(lrn, "_find_near_user_correction_ids", lambda *a, **k: [old.id])
        monkeypatch.setattr(cap, "detect_supersession", lambda **kw: True)

        archived = lrn.archive_contradicted_user_corrections(session=db, new_learning=new)
        assert archived == 1
        db.refresh(old)
        assert old.status == "archived"
        assert old.archived_at is not None

    def test_no_contradiction_keeps_both(self, db, monkeypatch):
        monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())
        old = AgentLearning(
            type="procedural", category="best-practice", tool_name="general",
            summary="DNS zone in the hub.", details="hub", status="provisional",
            source="user_correction",
        )
        new = AgentLearning(
            type="procedural", category="best-practice", tool_name="general",
            summary="Tag resources with cost-center.", details="tagging",
            status="provisional", source="user_correction",
        )
        db.add(old); db.add(new); db.commit(); db.refresh(old); db.refresh(new)

        monkeypatch.setattr(lrn, "_find_near_user_correction_ids", lambda *a, **k: [old.id])
        monkeypatch.setattr(cap, "detect_supersession", lambda **kw: False)

        archived = lrn.archive_contradicted_user_corrections(session=db, new_learning=new)
        assert archived == 0
        db.refresh(old)
        assert old.status == "provisional"

    def test_embed_unavailable_is_safe(self, db, monkeypatch):
        # When embedding/vec is unavailable, candidate-finding returns [] → no-op.
        def _boom(*a, **k): raise RuntimeError("no embedder")
        monkeypatch.setattr(lrn, "embed_query", _boom)
        monkeypatch.setattr(lrn, "get_engine", lambda: db.get_bind())
        new = AgentLearning(
            id=999, type="procedural", category="best-practice", tool_name="general",
            summary="x", details="y", status="provisional", source="user_correction",
        )
        assert lrn._find_near_user_correction_ids(new.id, new.summary) == []
