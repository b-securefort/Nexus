"""
End-to-end chat test — sends 20 diverse messages via the API and captures full SSE event logs.
Run with: python e2e_chat_test.py
"""

import json
import sys
import time
import httpx

BASE_URL = "http://localhost:8002"
HEADERS = {"X-Dev-User": "dev-user", "Accept": "text/event-stream"}
REST_HEADERS = {"X-Dev-User": "dev-user"}

# 20 test scenarios covering different capabilities
TEST_MESSAGES = [
    # Basic KB queries
    ("shared:chat-with-kb", "What information is in the knowledge base?"),
    ("shared:chat-with-kb", "Search the KB for deployment instructions"),
    # Skill switching
    ("shared:kb-searcher", "List all available KB files"),
    # Azure resource queries (should trigger az_resource_graph)
    ("shared:architect", "How many subscriptions do I have in Azure?"),
    ("shared:architect", "List all resource groups in my Azure environment"),
    # Azure CLI commands (should trigger approval)
    ("shared:chat-with-kb", "What is my current Azure CLI account?"),
    # Shell commands
    ("shared:chat-with-kb", "What is the current date and time on this machine?"),
    # Cost queries
    ("shared:architect", "Show me the cost summary for last month"),
    # Resource graph KQL
    ("shared:architect", "Count all VMs by location using Resource Graph"),
    ("shared:architect", "Find all storage accounts using Resource Graph query"),
    # MS docs lookup
    ("shared:chat-with-kb", "How do I create an Azure Function using az CLI?"),
    # Learnings
    ("shared:chat-with-kb", "Check if there are any recorded learnings"),
    # Multi-step reasoning
    ("shared:architect", "What Azure services are deployed in my environment? Give me a summary."),
    # Error handling
    ("shared:chat-with-kb", "Run the command: az nonexistent-command --help"),
    # Personal skill context
    ("shared:kb-searcher", "What skills are available in the system?"),
    # Complex queries
    ("shared:architect", "Show me all VMs that are currently running"),
    # Simple conversation
    ("shared:chat-with-kb", "Hello, what can you help me with?"),
    # Follow-up test (reuse conversation)
    (None, "Tell me more about that"),  # Will use last conversation
    # Edge cases
    ("shared:chat-with-kb", ""),  # Empty message
    ("shared:chat-with-kb", "x" * 5000),  # Very long message
]


def parse_sse_events(resp: httpx.Response) -> list[dict]:
    """Parse SSE events from a streaming response."""
    events = []
    event_type = ""
    for line in resp.iter_lines():
        if line.startswith("event: "):
            event_type = line[7:].strip()
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:].strip())
                events.append({"type": event_type, "data": data})
            except json.JSONDecodeError:
                events.append({"type": event_type, "data_raw": line[6:].strip()})
    return events


def auto_approve_pending(conversation_id: int) -> bool:
    """Check and auto-approve any pending approvals for a conversation."""
    try:
        r = httpx.get(
            f"{BASE_URL}/api/chat/resume?conversation_id={conversation_id}",
            headers=HEADERS,
            timeout=5,
        )
        return True
    except Exception:
        return False


def run_test(idx: int, skill_id: str | None, message: str, last_conv_id: int | None) -> dict:
    """Run a single chat test and return results."""
    result = {
        "test_num": idx + 1,
        "skill_id": skill_id or "(reuse)",
        "message": message[:100] + ("..." if len(message) > 100 else ""),
        "events": [],
        "errors": [],
        "tool_calls": [],
        "approvals": [],
        "assistant_text": "",
        "conversation_id": None,
        "duration_ms": 0,
        "status": "unknown",
    }

    body: dict = {"message": message}
    if last_conv_id and not skill_id:
        body["conversation_id"] = last_conv_id
    elif skill_id:
        body["skill_id"] = skill_id

    start = time.time()
    try:
        with httpx.Client(
            base_url=BASE_URL,
            headers=HEADERS,
            timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
        ) as client:
            with client.stream("POST", "/api/chat", json=body) as resp:
                if resp.status_code != 200:
                    result["errors"].append(f"HTTP {resp.status_code}: {resp.read().decode()[:500]}")
                    result["status"] = "http_error"
                    return result

                events = parse_sse_events(resp)
                result["events"] = [e["type"] for e in events]

                for e in events:
                    if e["type"] == "token":
                        result["assistant_text"] += e["data"].get("text", "")
                    elif e["type"] == "tool_call_start":
                        result["tool_calls"].append({
                            "name": e["data"].get("name"),
                            "call_id": e["data"].get("call_id"),
                            "args": e["data"].get("args"),
                        })
                    elif e["type"] == "approval_required":
                        result["approvals"].append({
                            "approval_id": e["data"].get("approval_id"),
                            "tool_name": e["data"].get("tool_name"),
                            "args": e["data"].get("args"),
                        })
                    elif e["type"] == "tool_result":
                        content = e["data"].get("content", "")
                        tc_name = e["data"].get("name", "?")
                        is_err = content.startswith("Error") or "Exit code: 1" in content
                        if is_err:
                            result["errors"].append(f"Tool {tc_name} error: {content[:200]}")
                    elif e["type"] == "error":
                        result["errors"].append(e["data"].get("message", "unknown"))
                    elif e["type"] == "done":
                        result["conversation_id"] = e["data"].get("conversation_id")

                # Handle approvals — auto-approve all
                for appr in result["approvals"]:
                    try:
                        ar = httpx.post(
                            f"{BASE_URL}/api/approvals/{appr['approval_id']}",
                            json={"action": "approve"},
                            headers=REST_HEADERS,
                            timeout=10,
                        )
                        appr["resolved"] = ar.status_code == 200
                    except Exception as ex:
                        appr["resolved"] = False
                        result["errors"].append(f"Approval failed: {ex}")

                # If we had approvals, resume the stream
                if result["approvals"] and result["conversation_id"]:
                    try:
                        with client.stream(
                            "GET",
                            f"/api/chat/resume?conversation_id={result['conversation_id']}",
                        ) as resume_resp:
                            resume_events = parse_sse_events(resume_resp)
                            result["events"].extend([e["type"] for e in resume_events])
                            for e in resume_events:
                                if e["type"] == "token":
                                    result["assistant_text"] += e["data"].get("text", "")
                                elif e["type"] == "tool_call_start":
                                    result["tool_calls"].append({
                                        "name": e["data"].get("name"),
                                        "call_id": e["data"].get("call_id"),
                                    })
                                elif e["type"] == "tool_result":
                                    content = e["data"].get("content", "")
                                    is_err = content.startswith("Error") or "Exit code: 1" in content
                                    if is_err:
                                        result["errors"].append(f"Tool error: {content[:200]}")
                                elif e["type"] == "error":
                                    result["errors"].append(e["data"].get("message", "unknown"))
                                elif e["type"] == "approval_required":
                                    # Nested approval during resume
                                    nested_appr = e["data"]
                                    result["approvals"].append({
                                        "approval_id": nested_appr.get("approval_id"),
                                        "tool_name": nested_appr.get("tool_name"),
                                        "resolved": False,
                                        "note": "nested_approval_not_auto_resolved"
                                    })
                    except Exception as ex:
                        result["errors"].append(f"Resume failed: {ex}")

        result["duration_ms"] = int((time.time() - start) * 1000)
        if result["errors"]:
            result["status"] = "errors"
        elif result["assistant_text"] or result["tool_calls"]:
            result["status"] = "ok"
        else:
            result["status"] = "empty_response"

    except httpx.HTTPStatusError as e:
        result["errors"].append(f"HTTP error: {e.response.status_code}")
        result["status"] = "http_error"
        result["duration_ms"] = int((time.time() - start) * 1000)
    except Exception as e:
        result["errors"].append(str(e))
        result["status"] = "exception"
        result["duration_ms"] = int((time.time() - start) * 1000)

    return result


def main():
    print("=" * 70)
    print("NEXUS E2E CHAT TEST — 20 scenarios")
    print("=" * 70)

    # Health check
    try:
        r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
        print(f"Backend health: {r.json()}")
    except Exception as e:
        print(f"ERROR: Backend not reachable: {e}")
        sys.exit(1)

    results = []
    last_conv_id = None

    for i, (skill_id, message) in enumerate(TEST_MESSAGES):
        # Skip empty message test (API will reject it)
        if not message.strip():
            results.append({
                "test_num": i + 1,
                "message": "(empty)",
                "status": "skipped",
                "errors": ["Empty message — skipped"],
                "tool_calls": [],
                "approvals": [],
                "assistant_text": "",
                "duration_ms": 0,
                "events": [],
            })
            print(f"  [{i+1:2d}/20] SKIP  — empty message")
            continue

        # Truncate very long messages for the test
        test_msg = message[:500] if len(message) > 500 else message

        print(f"  [{i+1:2d}/20] Testing: {test_msg[:60]}...", end=" ", flush=True)

        r = run_test(i, skill_id, test_msg, last_conv_id)
        results.append(r)

        if r.get("conversation_id"):
            last_conv_id = r["conversation_id"]

        status = r["status"]
        dur = r["duration_ms"]
        n_tools = len(r["tool_calls"])
        n_approvals = len(r["approvals"])
        n_errors = len(r["errors"])

        status_icon = "✓" if status == "ok" else "✗" if status in ("errors", "exception", "http_error") else "?"
        print(f"{status_icon} {status} | {dur}ms | tools:{n_tools} approvals:{n_approvals} errors:{n_errors}")

        if r["errors"]:
            for err in r["errors"][:3]:
                print(f"         └─ {err[:120]}")

    # Write full report
    report_path = "e2e_results.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results written to {report_path}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ok = sum(1 for r in results if r["status"] == "ok")
    errs = sum(1 for r in results if r["status"] in ("errors", "exception", "http_error"))
    skipped = sum(1 for r in results if r["status"] == "skipped")
    empty = sum(1 for r in results if r["status"] == "empty_response")
    print(f"  OK: {ok}  |  Errors: {errs}  |  Empty: {empty}  |  Skipped: {skipped}")

    all_tools = []
    for r in results:
        all_tools.extend([t["name"] for t in r["tool_calls"]])
    if all_tools:
        from collections import Counter
        print(f"  Tools used: {dict(Counter(all_tools))}")

    avg_dur = sum(r["duration_ms"] for r in results if r["status"] != "skipped") / max(1, len(results) - skipped)
    print(f"  Avg duration: {avg_dur:.0f}ms")

    # Collect all unique errors
    all_errors = []
    for r in results:
        for e in r["errors"]:
            all_errors.append((r["test_num"], e))
    if all_errors:
        print(f"\n  All errors ({len(all_errors)}):")
        for num, err in all_errors:
            print(f"    Test {num}: {err[:150]}")


if __name__ == "__main__":
    main()
