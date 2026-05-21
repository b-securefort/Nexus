"""Tests for conversation compaction, tool-result truncation, and
original-task pinning under the user-preserving strategy."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.agent import compaction
from app.agent.compaction import (
    _clean_orphans,
    _compress_older_scaffolding,
    _estimate_chars,
    _identify_latest_image_row_id,
    _identify_latest_user_row_id,
    _row_to_message,
    get_original_task,
    load_compacted_history,
)
from app.agent.orchestrator import (
    _compose_system_prompt,
    _strip_retry_messages_for_tool,
    _truncate_tool_result,
)
from app.auth.models import User
from app.db.models import Conversation, Message
from app.skills.models import Skill


# ── Helpers ───────────────────────────────────────────────────────────────

def _seed_conversation(session, user_oid="dev-user", skill_id="chat-with-kb"):
    skill_snapshot = json.dumps({
        "id": skill_id, "name": skill_id, "display_name": skill_id,
        "description": "", "system_prompt": "", "tools": [], "source": "shared",
    })
    conv = Conversation(
        user_oid=user_oid, title="test", skill_id=skill_id,
        skill_snapshot_json=skill_snapshot,
    )
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv


def _add_message(
    session, conv_id, role, content, *,
    tool_calls=None, tool_call_id=None, attachments=None, when=None,
):
    msg = Message(
        conversation_id=conv_id, role=role, content=content,
        tool_calls_json=json.dumps(tool_calls) if tool_calls else None,
        tool_call_id=tool_call_id,
        attachments_json=json.dumps(attachments) if attachments else None,
    )
    if when is not None:
        msg.created_at = when
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


def _build_mock_client(*, scaffold_summary="- did stuff", paste_summary="- compressed paste", image_desc="- 2 nodes, 1 edge"):
    """Mock that returns different content based on the system prompt the
    caller used — so a single client mock can serve scaffold / paste / image
    compression in a single test."""
    client = MagicMock()

    def fake_create(**kwargs):
        msgs = kwargs.get("messages", [])
        system = next((m["content"] for m in msgs if m["role"] == "system"), "")
        completion = MagicMock()
        if "OUTCOME BULLETS" in system:
            content = scaffold_summary
        elif "long user message" in system or "compress a single" in system:
            content = paste_summary
        else:
            # Vision call uses no system message; the user content lists the
            # describe instruction.
            content = image_desc
        completion.choices = [MagicMock(message=MagicMock(content=content))]
        return completion

    client.chat.completions.create.side_effect = fake_create
    return client


# ── get_original_task ────────────────────────────────────────────────────

def test_original_task_returns_first_user_message(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    _add_message(db_session, conv.id, "user", "first ask", when=base)
    _add_message(db_session, conv.id, "assistant", "ack", when=base + timedelta(seconds=1))
    _add_message(db_session, conv.id, "user", "second ask", when=base + timedelta(seconds=2))
    assert get_original_task(db_session, conv.id) == "first ask"


def test_original_task_empty_if_no_user_messages(db_session):
    conv = _seed_conversation(db_session)
    _add_message(db_session, conv.id, "assistant", "hello")
    assert get_original_task(db_session, conv.id) == ""


# ── _compose_system_prompt original-task pin ─────────────────────────────

def test_system_prompt_includes_original_task_block():
    skill = Skill(
        id="s", name="s", display_name="s", description="",
        system_prompt="BASE", tools=[], source="shared",
    )
    user = User(oid="o", email="e@x", display_name="Dev")
    prompt, _ = _compose_system_prompt(skill, user, original_task="deploy aks cluster")
    assert "[Original task from user" in prompt
    assert "deploy aks cluster" in prompt


def test_system_prompt_omits_block_when_no_original_task():
    skill = Skill(
        id="s", name="s", display_name="s", description="",
        system_prompt="BASE", tools=[], source="shared",
    )
    user = User(oid="o", email="e@x", display_name="Dev")
    prompt, _ = _compose_system_prompt(skill, user, original_task="")
    assert "[Original task from user" not in prompt


def test_system_prompt_truncates_huge_original_task():
    skill = Skill(
        id="s", name="s", display_name="s", description="",
        system_prompt="BASE", tools=[], source="shared",
    )
    user = User(oid="o", email="e@x", display_name="Dev")
    huge = "x" * 5000
    prompt, _ = _compose_system_prompt(skill, user, original_task=huge)
    assert "…[truncated]" in prompt
    pin_section = prompt.split("[Original task from user")[1]
    assert pin_section.count("x") <= 2000


# ── load_compacted_history: below threshold (no compaction) ──────────────

def test_load_compacted_returns_as_is_when_under_threshold(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    for i in range(5):
        _add_message(db_session, conv.id, "user", f"msg {i}", when=base + timedelta(seconds=i))

    client = _build_mock_client()
    out, _ = load_compacted_history(db_session, conv.id, client, "gpt-test")

    assert len(out) == 5
    assert out[0]["content"] == "msg 0"
    assert out[-1]["content"] == "msg 4"
    client.chat.completions.create.assert_not_called()


def test_load_compacted_prepends_cached_summary_when_under_threshold(db_session):
    conv = _seed_conversation(db_session)
    conv.summary_text = "- old context bullet"
    db_session.add(conv)
    db_session.commit()

    base = datetime.now(timezone.utc)
    _add_message(db_session, conv.id, "user", "recent ask", when=base)

    client = _build_mock_client()
    out, _ = load_compacted_history(db_session, conv.id, client, "gpt-test")

    assert out[0]["role"] == "assistant"
    assert "Summary of earlier conversation" in out[0]["content"]
    assert "- old context bullet" in out[0]["content"]
    assert out[1]["content"] == "recent ask"
    client.chat.completions.create.assert_not_called()


# ── load_compacted_history: user-preserving compaction ───────────────────

def test_compaction_preserves_all_user_messages(db_session):
    """The core invariant: every user message in the window stays verbatim,
    no matter how old. Only scaffolding between user messages gets compressed."""
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)

    # 5 user messages interleaved with assistant + tool scaffolding,
    # for 35 messages total (over the 30-message threshold)
    msg_idx = 0
    user_texts = []
    for turn in range(5):
        u_text = f"user turn {turn}"
        user_texts.append(u_text)
        _add_message(db_session, conv.id, "user", u_text,
                     when=base + timedelta(seconds=msg_idx))
        msg_idx += 1
        # 6 scaffolding messages per turn (assistant + tool pairs)
        for j in range(3):
            tc_id = f"tc-{turn}-{j}"
            _add_message(
                db_session, conv.id, "assistant", f"thinking {turn}.{j}",
                tool_calls=[{"id": tc_id, "type": "function",
                             "function": {"name": "az_cli", "arguments": "{}"}}],
                when=base + timedelta(seconds=msg_idx),
            )
            msg_idx += 1
            _add_message(
                db_session, conv.id, "tool", f"result {turn}.{j}",
                tool_call_id=tc_id, when=base + timedelta(seconds=msg_idx),
            )
            msg_idx += 1

    client = _build_mock_client(scaffold_summary="- compressed scaffolding")
    out, _ = load_compacted_history(db_session, conv.id, client, "gpt-test")

    # Every user message must appear verbatim somewhere in the output
    contents = []
    for m in out:
        c = m.get("content")
        if isinstance(c, str):
            contents.append(c)
    flat = "\n".join(contents)
    for u_text in user_texts:
        assert u_text in flat, f"missing user msg: {u_text}"

    # Scaffolding compression actually happened (scaffold summary call made)
    create_calls = client.chat.completions.create.call_args_list
    scaffold_calls = [
        c for c in create_calls
        if any("OUTCOME BULLETS" in m.get("content", "")
               for m in c.kwargs.get("messages", [])
               if m.get("role") == "system")
    ]
    assert len(scaffold_calls) >= 1
    assert "compressed scaffolding" in flat


def test_compaction_keeps_recent_scaffolding_verbatim(db_session):
    """Recent (last RECENT_KEEP_COUNT) messages stay verbatim, even tool calls."""
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)

    # 35 messages — first 20 will be "older", last 15 stay verbatim
    msg_idx = 0
    for turn in range(5):
        _add_message(db_session, conv.id, "user", f"turn {turn}",
                     when=base + timedelta(seconds=msg_idx))
        msg_idx += 1
        for j in range(3):
            tc_id = f"tc-{turn}-{j}"
            _add_message(
                db_session, conv.id, "assistant", f"think {turn}.{j}",
                tool_calls=[{"id": tc_id, "type": "function",
                             "function": {"name": "az_cli", "arguments": "{}"}}],
                when=base + timedelta(seconds=msg_idx),
            )
            msg_idx += 1
            # Mark recent results with a distinctive token so we can assert
            # they survived as-is.
            marker = "RECENT_VERBATIM" if turn >= 3 else "older"
            _add_message(
                db_session, conv.id, "tool", f"{marker} result {turn}.{j}",
                tool_call_id=tc_id, when=base + timedelta(seconds=msg_idx),
            )
            msg_idx += 1

    client = _build_mock_client()
    out, _ = load_compacted_history(db_session, conv.id, client, "gpt-test")

    # At least one RECENT_VERBATIM tool message survived intact
    flat = "\n".join(
        m.get("content", "") for m in out if isinstance(m.get("content"), str)
    )
    assert "RECENT_VERBATIM" in flat


def test_compaction_persists_summary_and_advances_boundary(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    for i in range(35):
        role = "user" if i % 2 == 0 else "assistant"
        _add_message(db_session, conv.id, role, f"m{i}",
                     when=base + timedelta(seconds=i))

    client = _build_mock_client(scaffold_summary="- outcome 1")
    load_compacted_history(db_session, conv.id, client, "gpt-test")

    db_session.refresh(conv)
    assert conv.summary_text is not None
    assert conv.summary_through_message_id is not None
    assert conv.summary_through_message_id > 0


def test_compaction_falls_back_to_uncompacted_if_scaffold_summarizer_fails(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    for i in range(35):
        role = "user" if i % 2 == 0 else "assistant"
        _add_message(db_session, conv.id, role, f"m {i}",
                     when=base + timedelta(seconds=i))

    failing_client = MagicMock()
    failing_client.chat.completions.create.side_effect = RuntimeError("boom")

    out, _ = load_compacted_history(db_session, conv.id, failing_client, "gpt-test")
    # All originals still present somewhere (verbatim fallback)
    flat = "\n".join(
        m.get("content", "") for m in out if isinstance(m.get("content"), str)
    )
    for i in range(35):
        assert f"m {i}" in flat


# ── Long user paste compression ──────────────────────────────────────────

def test_long_user_paste_summarized_when_not_latest(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    long_paste = "stack-trace-line " * 250  # ~4250 chars, over threshold
    _add_message(db_session, conv.id, "user", long_paste, when=base)
    # 34 short scaffolding messages so we cross the count threshold and
    # the long paste ends up in 'older' portion
    for i in range(34):
        role = "assistant" if i % 2 == 0 else "user"
        _add_message(db_session, conv.id, role, f"m{i}",
                     when=base + timedelta(seconds=i + 1))

    client = _build_mock_client(paste_summary="- compressed long paste")
    _, deferred = load_compacted_history(db_session, conv.id, client, "gpt-test")
    # Execute deferred background summarisation work synchronously in the test
    for fn in deferred:
        fn()

    # Reload the first (long-paste) message — should have text_summary cached.
    # Expire the session's identity map so the re-get picks up the committed write.
    db_session.expire_all()
    long_row = db_session.get(Message, 1)
    assert long_row is not None
    assert long_row.text_summary is not None
    assert "compressed long paste" in long_row.text_summary


def test_long_user_paste_NOT_summarized_when_latest(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    # Older short user msg + lots of scaffolding + latest long paste
    _add_message(db_session, conv.id, "user", "early ask", when=base)
    for i in range(34):
        _add_message(db_session, conv.id, "assistant", f"m{i}",
                     when=base + timedelta(seconds=i + 1))
    long_paste = "config-line " * 400  # ~4800 chars
    latest = _add_message(
        db_session, conv.id, "user", long_paste,
        when=base + timedelta(seconds=200),
    )

    client = _build_mock_client(paste_summary="- compressed")
    load_compacted_history(db_session, conv.id, client, "gpt-test")

    db_session.refresh(latest)
    assert latest.text_summary is None  # never compressed — it's the latest


def test_long_user_paste_summary_reused_from_cache(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    long_paste = "x" * 5000
    row = _add_message(db_session, conv.id, "user", long_paste, when=base)
    row.text_summary = "- already cached summary"
    db_session.add(row)
    db_session.commit()

    # 34 scaffolding messages so the long paste lands in 'older'
    for i in range(34):
        role = "assistant" if i % 2 == 0 else "user"
        _add_message(db_session, conv.id, role, f"m{i}",
                     when=base + timedelta(seconds=i + 1))

    client = _build_mock_client(paste_summary="- FRESH NEW SUMMARY")
    out, _ = load_compacted_history(db_session, conv.id, client, "gpt-test")

    flat = "\n".join(
        m.get("content", "") for m in out if isinstance(m.get("content"), str)
    )
    assert "already cached summary" in flat
    assert "FRESH NEW SUMMARY" not in flat
    # No paste-summary LLM call was made
    paste_calls = [
        c for c in client.chat.completions.create.call_args_list
        if any(
            "compress a single" in m.get("content", "")
            for m in c.kwargs.get("messages", []) if m.get("role") == "system"
        )
    ]
    assert paste_calls == []


# ── Image compression ────────────────────────────────────────────────────

def test_image_summary_used_for_older_image_owner(db_session, tmp_path, monkeypatch):
    """Older user message with image attachment gets its image_summary
    inlined as text; the actual image bytes are NOT sent."""
    # Point UPLOAD_DIR at a tmp path with a dummy image
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    # Reset the Settings singleton so the new UPLOAD_DIR is picked up
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)
    img_path = tmp_path / "diagram.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")

    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    older_with_image = _add_message(
        db_session, conv.id, "user", "look at this:",
        attachments=[{"filename": "diagram.png", "content_type": "image/png"}],
        when=base,
    )
    # Later: lots of scaffolding + a NEWER image-bearing user message
    for i in range(20):
        _add_message(db_session, conv.id, "assistant", f"m{i}",
                     when=base + timedelta(seconds=i + 1))
    newer_with_image = _add_message(
        db_session, conv.id, "user", "and this:",
        attachments=[{"filename": "diagram.png", "content_type": "image/png"}],
        when=base + timedelta(seconds=100),
    )
    # Make sure we cross the message threshold
    for i in range(15):
        _add_message(db_session, conv.id, "assistant", f"n{i}",
                     when=base + timedelta(seconds=200 + i))

    client = _build_mock_client(image_desc="- diagram nodes A,B,C")
    out, deferred = load_compacted_history(db_session, conv.id, client, "gpt-test")
    # Execute deferred background image-description work synchronously in the test
    for fn in deferred:
        fn()
    # Expire identity map so refresh() picks up the committed writes
    db_session.expire_all()

    db_session.refresh(older_with_image)
    db_session.refresh(newer_with_image)

    # Older image gets its summary cached and inlined as text
    assert older_with_image.image_summary is not None
    assert "diagram nodes A,B,C" in older_with_image.image_summary

    # Newer (latest) image owner stays with no image_summary cache
    assert newer_with_image.image_summary is None


def test_latest_image_owner_keeps_image_url_in_multipart(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)
    img_path = tmp_path / "pic.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\nbytes")

    conv = _seed_conversation(db_session)
    _add_message(
        db_session, conv.id, "user", "look:",
        attachments=[{"filename": "pic.png", "content_type": "image/png"}],
    )
    client = _build_mock_client()
    out, _ = load_compacted_history(db_session, conv.id, client, "gpt-test")

    # The latest user message gets multipart content with an image_url part
    latest_msg = out[-1]
    assert latest_msg["role"] == "user"
    assert isinstance(latest_msg["content"], list)
    types = [p.get("type") for p in latest_msg["content"] if isinstance(p, dict)]
    assert "image_url" in types


# ── _identify_latest_* helpers ───────────────────────────────────────────

def test_identify_latest_image_row_id_picks_most_recent_image_owner(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    r1 = _add_message(
        db_session, conv.id, "user", "early",
        attachments=[{"filename": "a.png", "content_type": "image/png"}],
        when=base,
    )
    _add_message(db_session, conv.id, "user", "no-image", when=base + timedelta(seconds=1))
    r3 = _add_message(
        db_session, conv.id, "user", "later",
        attachments=[{"filename": "b.png", "content_type": "image/png"}],
        when=base + timedelta(seconds=2),
    )
    # Re-fetch as objects (the helper takes Message rows, not dicts)
    rows = [
        db_session.get(Message, r1.id),
        db_session.get(Message, r3.id),
    ]
    assert _identify_latest_image_row_id(rows) == r3.id


def test_identify_latest_image_returns_none_when_no_attachments(db_session):
    conv = _seed_conversation(db_session)
    r1 = _add_message(db_session, conv.id, "user", "hi")
    assert _identify_latest_image_row_id([db_session.get(Message, r1.id)]) is None


# ── Orphan cleaning ───────────────────────────────────────────────────────

def test_clean_orphans_drops_tool_without_assistant_parent():
    messages = [
        {"role": "tool", "content": "orphan", "tool_call_id": "missing"},
        {"role": "user", "content": "hi"},
    ]
    cleaned = _clean_orphans(messages)
    assert len(cleaned) == 1
    assert cleaned[0]["content"] == "hi"


def test_clean_orphans_strips_unanswered_tool_calls_from_assistant():
    messages = [
        {"role": "assistant", "content": "thinking",
         "tool_calls": [{"id": "tc1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "user", "content": "next"},
    ]
    cleaned = _clean_orphans(messages)
    assert "tool_calls" not in cleaned[0] or cleaned[0]["tool_calls"] == []


# ── _compress_older_scaffolding ──────────────────────────────────────────

def test_compress_older_scaffolding_preserves_user_messages():
    older = [
        {"role": "user", "content": "ask A"},
        {"role": "assistant", "content": "thinking",
         "tool_calls": [{"id": "tc1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "content": "result", "tool_call_id": "tc1"},
        {"role": "user", "content": "ask B"},
        {"role": "assistant", "content": "thinking 2",
         "tool_calls": [{"id": "tc2", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "content": "result 2", "tool_call_id": "tc2"},
    ]
    client = _build_mock_client(scaffold_summary="- bullet")
    out = _compress_older_scaffolding(older, client, "gpt-test")

    # Both user messages preserved verbatim, in order
    user_msgs = [m for m in out if m.get("role") == "user"]
    assert len(user_msgs) == 2
    assert user_msgs[0]["content"] == "ask A"
    assert user_msgs[1]["content"] == "ask B"

    # Scaffolding became outcome bullets — 2 synthetic assistant msgs
    bullets = [
        m for m in out
        if m.get("role") == "assistant"
        and isinstance(m.get("content"), str)
        and m["content"].startswith("[Outcomes from intermediate tool work]")
    ]
    assert len(bullets) == 2


def test_compress_older_scaffolding_no_user_messages():
    older = [
        {"role": "assistant", "content": "thinking",
         "tool_calls": [{"id": "tc1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "content": "result", "tool_call_id": "tc1"},
    ]
    client = _build_mock_client(scaffold_summary="- one bullet")
    out = _compress_older_scaffolding(older, client, "gpt-test")
    assert len(out) == 1
    assert out[0]["role"] == "assistant"
    assert out[0]["content"].startswith("[Outcomes from intermediate tool work]")


# ── _estimate_chars ───────────────────────────────────────────────────────

def test_estimate_chars_counts_text_and_multipart():
    msgs = [
        {"role": "user", "content": "abcd"},
        {"role": "user", "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]},
    ]
    assert _estimate_chars(msgs) == 2009  # 4 + 5 + 2000


# ── _truncate_tool_result ────────────────────────────────────────────────

def test_truncate_tool_result_below_limit_unchanged():
    payload = json.dumps({"status": "success", "tool": "az_cli", "data": "ok"})
    assert _truncate_tool_result("az_cli", payload) == payload


def test_truncate_tool_result_above_limit_keeps_head_and_tail():
    big = json.dumps({"status": "success", "tool": "az_cli", "data": "x" * 10000})
    out = _truncate_tool_result("az_cli", big)
    assert "[truncated" in out
    assert len(out) < len(big)
    assert "full output in DB" in out


def test_truncate_tool_result_strips_drawio_xml_field():
    big_xml = "<mxGraphModel>" + ("a" * 8000) + "</mxGraphModel>"
    envelope = json.dumps({
        "status": "success", "tool": "validate_drawio",
        "data": {"verdict": "PASSED", "xml": big_xml},
    })
    out = _truncate_tool_result("validate_drawio", envelope)
    parsed = json.loads(out)
    assert parsed["data"]["verdict"] == "PASSED"
    assert "XML omitted" in parsed["data"]["xml"]


def test_truncate_tool_result_unknown_tool_returns_unchanged():
    payload = "x" * 20000
    assert _truncate_tool_result("ask_user", payload) == payload


# ── _strip_retry_messages_for_tool ───────────────────────────────────────

def test_strip_retry_messages_removes_matching_tool():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "[RETRY STRATEGY 1/3 — Fix syntax] The `az_cli` call failed..."},
        {"role": "assistant", "content": "retrying"},
        {"role": "system", "content": "[RETRY STRATEGY 2/3 — Different approach] `az_cli` has now failed..."},
    ]
    removed = _strip_retry_messages_for_tool(msgs, "az_cli")
    assert removed == 2
    assert len(msgs) == 2


def test_strip_retry_messages_leaves_other_tools_alone():
    msgs = [
        {"role": "system", "content": "[RETRY STRATEGY 1/3 — Fix syntax] The `az_cli` call failed..."},
        {"role": "system", "content": "[RETRY STRATEGY 1/3 — Fix syntax] The `run_shell` call failed..."},
    ]
    removed = _strip_retry_messages_for_tool(msgs, "az_cli")
    assert removed == 1
    assert "run_shell" in msgs[0]["content"]


def test_strip_retry_messages_no_op_when_none_present():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    removed = _strip_retry_messages_for_tool(msgs, "az_cli")
    assert removed == 0
    assert len(msgs) == 2


# ── _row_to_message ───────────────────────────────────────────────────────

def test_row_to_message_assistant_with_tool_calls(db_session):
    conv = _seed_conversation(db_session)
    row = _add_message(
        db_session, conv.id, "assistant", "calling",
        tool_calls=[{"id": "tc1", "type": "function",
                     "function": {"name": "az_cli", "arguments": "{}"}}],
    )
    msg = _row_to_message(
        row, db_session, MagicMock(), "gpt-test",
        is_latest_user=False, is_latest_image_owner=False,
    )
    assert msg["role"] == "assistant"
    assert msg["content"] == "calling"
    assert msg["tool_calls"][0]["id"] == "tc1"


def test_row_to_message_tool_role_carries_call_id(db_session):
    conv = _seed_conversation(db_session)
    row = _add_message(db_session, conv.id, "tool", "out", tool_call_id="tc1")
    msg = _row_to_message(
        row, db_session, MagicMock(), "gpt-test",
        is_latest_user=False, is_latest_image_owner=False,
    )
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "tc1"


def test_row_to_message_short_user_text_kept_verbatim(db_session):
    conv = _seed_conversation(db_session)
    row = _add_message(db_session, conv.id, "user", "hi")
    msg = _row_to_message(
        row, db_session, MagicMock(), "gpt-test",
        is_latest_user=False, is_latest_image_owner=False,
    )
    assert msg["content"] == "hi"


# ── End-to-end: cache avoids re-summarizing ──────────────────────────────

def test_compaction_cache_avoids_resummarizing_on_next_turn(db_session):
    conv = _seed_conversation(db_session)
    base = datetime.now(timezone.utc)
    msg_idx = 0
    for turn in range(5):
        _add_message(db_session, conv.id, "user", f"u{turn}",
                     when=base + timedelta(seconds=msg_idx))
        msg_idx += 1
        for j in range(3):
            tc_id = f"tc-{turn}-{j}"
            _add_message(
                db_session, conv.id, "assistant", f"a{turn}{j}",
                tool_calls=[{"id": tc_id, "type": "function",
                             "function": {"name": "f", "arguments": "{}"}}],
                when=base + timedelta(seconds=msg_idx),
            )
            msg_idx += 1
            _add_message(
                db_session, conv.id, "tool", f"r{turn}{j}",
                tool_call_id=tc_id, when=base + timedelta(seconds=msg_idx),
            )
            msg_idx += 1

    client = _build_mock_client(scaffold_summary="- bullet")
    load_compacted_history(db_session, conv.id, client, "gpt-test")
    first_calls = client.chat.completions.create.call_count
    assert first_calls >= 1

    db_session.refresh(conv)
    boundary = conv.summary_through_message_id
    assert boundary is not None and boundary > 0

    # Second turn: one new message, well under threshold → no new compaction
    _add_message(db_session, conv.id, "user", "follow-up",
                 when=base + timedelta(seconds=500))
    load_compacted_history(db_session, conv.id, client, "gpt-test")

    # No additional summarizer calls
    assert client.chat.completions.create.call_count == first_calls
