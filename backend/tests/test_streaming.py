"""Tests for SSE streaming helpers."""

import json
from app.agent.streaming import (
    sse_event,
    sse_token,
    sse_tool_call_start,
    sse_approval_required,
    sse_tool_result,
    sse_message_saved,
    sse_done,
    sse_error,
)


class TestSSEFormatting:
    def test_sse_event_format(self):
        result = sse_event("test", {"key": "value"})
        assert result.startswith("event: test\n")
        assert "data: " in result
        assert result.endswith("\n\n")
        data_line = result.split("\n")[1]
        parsed = json.loads(data_line.replace("data: ", ""))
        assert parsed["key"] == "value"

    def test_sse_token(self):
        result = sse_token("hello")
        assert "event: token" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["text"] == "hello"

    def test_sse_token_special_chars(self):
        result = sse_token('line1\nline2\t"quoted"')
        data = json.loads(result.split("data: ")[1].strip())
        assert data["text"] == 'line1\nline2\t"quoted"'

    def test_sse_tool_call_start(self):
        result = sse_tool_call_start("call-1", "read_kb_file", {"path": "test.md"})
        assert "event: tool_call_start" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["call_id"] == "call-1"
        assert data["name"] == "read_kb_file"
        assert data["args"]["path"] == "test.md"

    def test_sse_approval_required(self):
        result = sse_approval_required("ap-1", "run_shell", {"command": "ls"}, "List files")
        assert "event: approval_required" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["approval_id"] == "ap-1"
        assert data["tool_name"] == "run_shell"
        assert data["reason"] == "List files"

    def test_sse_tool_result(self):
        result = sse_tool_result("call-1", "search_kb", "found 3 results")
        assert "event: tool_result" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["content"] == "found 3 results"

    def test_sse_message_saved(self):
        result = sse_message_saved(42, "assistant")
        data = json.loads(result.split("data: ")[1].strip())
        assert data["message_id"] == 42
        assert data["role"] == "assistant"

    def test_sse_done(self):
        result = sse_done(7)
        data = json.loads(result.split("data: ")[1].strip())
        assert data["conversation_id"] == 7

    def test_sse_error(self):
        result = sse_error("Something went wrong")
        assert "event: error" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["message"] == "Something went wrong"
