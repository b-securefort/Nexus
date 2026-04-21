"""
End-to-end chat test v2 — sends 20 diverse messages via the API.
Uses threaded SSE reading + auto-approval to handle approval-requiring tools.
Run with: python e2e_chat_test.py
"""

import json
import sys
import time
import threading
import httpx

BASE_URL = "http://localhost:8002"
HEADERS = {"X-Dev-User": "dev-user"}
REST_HEADERS = {"X-Dev-User": "dev-user"}

# 20 test scenarios
TEST_MESSAGES = [
    # 1-3: KB queries
    ("shared:chat-with-kb", "What information is in the knowledge base?"),
    ("shared:chat-with-kb", "Search the KB for deployment instructions"),
    ("shared:kb-searcher", "List all available KB files"),
    # 4-5: Azure resource queries (Resource Graph — no approval)
    ("shared:architect", "How many subscriptions do I have in Azure?"),
    ("shared:architect", "List all resource groups in my Azure environment"),
    # 6-7: Commands requiring approval (az_cli / run_shell)
    ("shared:chat-with-kb", "What is my current Azure CLI account? Use az cli to check."),
    ("shared:chat-with-kb", "What is the current date and time on this machine? Run a shell command."),
    # 8: Cost query (may need approval for az_cli)
    ("shared:architect", "Show me the cost summary for last month"),
    # 9-10: Resource Graph KQL
    ("shared:architect", "Count all VMs by location using Resource Graph"),
    ("shared:architect", "Find all storage accounts using Resource Graph query"),
    # 11: MS docs lookup
    ("shared:chat-with-kb", "How do I create an Azure Function using az CLI?"),
    # 12: Learnings
    ("shared:chat-with-kb", "Check if there are any recorded learnings"),
    # 13: Multi-step
    ("shared:architect", "What Azure services are deployed in my environment? Give me a summary."),
    # 14: Error handling — bad command requiring approval
    ("shared:chat-with-kb", "Run this exact az cli command: az nonexistent-command --help"),
    # 15: Skills question
    ("shared:kb-searcher", "What skills are available in the system?"),
    # 16: VM query
    ("shared:architect", "Show me all VMs that are currently running"),
    # 17: Simple chat
    ("shared:chat-with-kb", "Hello, what can you help me with?"),
    # 18: Follow-up (reuses last conversation)
    (None, "Tell me more about that"),
    # 19: Empty message — should be rejected by validation
    ("shared:chat-with-kb", ""),
    # 20: Short valid message
    ("shared:chat-with-kb", "What is Azure DevOps?"),
]


def run_test(idx: int, skill_id: str | None, message: str, last_conv_id: int | None) -> dict:
    """Run a single chat test with threaded SSE reading and auto-approval."""
    result = {
        "test_num": idx + 1,
        "skill_id": skill_id or "(reuse)",
        "message": message[:100] + ("..." if len(message) > 100 else ""),
        "events": [],
        "errors": [],
        "tool_calls": [],
        "approvals_auto_resolved": 0,
        "assistant_text": "",
        "conversation_id": None,
        "duration_ms": 0,
        "status": "unknown",
    }

    # Handle empty message
    if not message.strip():
        body: dict = {"message": message, "skill_id": skill_id}
        try:
            r = httpx.post(
                f"{BASE_URL}/api/chat",
                json=body,
                headers=REST_HEADERS,
                timeout=10,
            )
            if r.status_code == 422:
                result["status"] = "validation_ok"
                result["errors"].append(f"Correctly rejected: {r.status_code}")
            else:
                result["status"] = "unexpected"
                result["errors"].append(f"Expected 422, got {r.status_code}")
        except Exception as e:
            result["errors"].append(str(e))
            result["status"] = "exception"
        return result

    body = {"message": message}
    if last_conv_id and not skill_id:
        body["conversation_id"] = last_conv_id
    elif skill_id:
        body["skill_id"] = skill_id

    # Shared state for the SSE reader thread
    lock = threading.Lock()
    pending_approvals: list[dict] = []
    stream_done = threading.Event()

    start = time.time()

    def read_sse_stream(resp: httpx.Response):
        """Read SSE events in a background thread."""
        event_type = ""
        try:
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:].strip())
                    except json.JSONDecodeError:
                        continue

                    with lock:
                        result["events"].append(event_type)

                    if event_type == "token":
                        with lock:
                            result["assistant_text"] += data.get("text", "")

                    elif event_type == "tool_call_start":
                        with lock:
                            result["tool_calls"].append({
                                "name": data.get("name"),
                                "call_id": data.get("call_id"),
                            })

                    elif event_type == "approval_required":
                        with lock:
                            pending_approvals.append(data)

                    elif event_type == "tool_result":
                        content = data.get("content", "")
                        is_err = (
                            content.startswith("Error")
                            or "Exit code: 1" in content
                            or "Exit code: 2" in content
                        )
                        if is_err:
                            with lock:
                                result["errors"].append(
                                    f"Tool {data.get('name', '?')} error: {content[:200]}"
                                )

                    elif event_type == "done":
                        with lock:
                            result["conversation_id"] = data.get("conversation_id")

                    elif event_type == "error":
                        with lock:
                            result["errors"].append(data.get("message", "unknown"))
        except Exception as e:
            with lock:
                result["errors"].append(f"SSE read error: {e}")
        finally:
            stream_done.set()

    try:
        client = httpx.Client(
            base_url=BASE_URL,
            headers={**HEADERS, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(connect=10, read=180, write=10, pool=10),
        )

        resp = client.send(
            client.build_request("POST", "/api/chat", json=body),
            stream=True,
        )
        if resp.status_code != 200:
            result["errors"].append(f"HTTP {resp.status_code}: {resp.read().decode()[:300]}")
            result["status"] = "http_error"
            resp.close()
            client.close()
            return result

        # Start SSE reader in background thread
        reader_thread = threading.Thread(target=read_sse_stream, args=(resp,), daemon=True)
        reader_thread.start()

        # Poll for approvals and auto-resolve them
        max_wait = 90  # seconds per test
        poll_start = time.time()
        while not stream_done.is_set() and (time.time() - poll_start) < max_wait:
            stream_done.wait(timeout=0.5)

            # Check for pending approvals
            with lock:
                to_approve = list(pending_approvals)
                pending_approvals.clear()

            for appr in to_approve:
                aid = appr.get("approval_id")
                if aid:
                    try:
                        ar = httpx.post(
                            f"{BASE_URL}/api/approvals/{aid}",
                            json={"action": "approve"},
                            headers=REST_HEADERS,
                            timeout=10,
                        )
                        if ar.status_code == 200:
                            with lock:
                                result["approvals_auto_resolved"] += 1
                    except Exception as e:
                        with lock:
                            result["errors"].append(f"Approval failed: {e}")

        reader_thread.join(timeout=5)
        resp.close()
        client.close()

        result["duration_ms"] = int((time.time() - start) * 1000)

        with lock:
            if result["errors"] and not result["assistant_text"] and not result["tool_calls"]:
                result["status"] = "errors"
            elif result["assistant_text"] or result["tool_calls"]:
                result["status"] = "ok"
            else:
                result["status"] = "empty_response"

    except Exception as e:
        result["errors"].append(str(e))
        result["status"] = "exception"
        result["duration_ms"] = int((time.time() - start) * 1000)

    return result


def main():
    print("=" * 70)
    print("NEXUS E2E CHAT TEST v2 — 20 scenarios (with auto-approval)")
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
        # Health-check before each test (skip for the empty-message validation test)
        if message.strip():
            try:
                hc = httpx.get(f"{BASE_URL}/healthz", timeout=5)
                if hc.status_code != 200:
                    print(f"  [!] Backend unhealthy (HTTP {hc.status_code}), waiting 5s...")
                    time.sleep(5)
            except Exception:
                print("  [!] Backend unreachable, waiting 5s...")
                time.sleep(5)

        label = message[:60] if message else "(empty)"
        print(f"  [{i+1:2d}/20] Testing: {label}...", end=" ", flush=True)

        r = run_test(i, skill_id, message, last_conv_id)
        results.append(r)

        if r.get("conversation_id"):
            last_conv_id = r["conversation_id"]

        status = r["status"]
        dur = r["duration_ms"]
        n_tools = len(r["tool_calls"])
        n_approved = r["approvals_auto_resolved"]
        n_errors = len(r["errors"])

        icon = "✓" if status in ("ok", "validation_ok") else "✗"
        extra = f" approved:{n_approved}" if n_approved else ""
        print(f"{icon} {status} | {dur}ms | tools:{n_tools}{extra} errors:{n_errors}")

        if r["errors"]:
            for err in r["errors"][:3]:
                print(f"         └─ {err[:130]}")

    # Write full report
    report_path = "e2e_results_v2.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results written to {report_path}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ok = sum(1 for r in results if r["status"] == "ok")
    val_ok = sum(1 for r in results if r["status"] == "validation_ok")
    errs = sum(1 for r in results if r["status"] in ("errors", "exception", "http_error"))
    empty = sum(1 for r in results if r["status"] == "empty_response")
    print(f"  OK: {ok}  |  Validation OK: {val_ok}  |  Errors: {errs}  |  Empty: {empty}")

    total_approved = sum(r["approvals_auto_resolved"] for r in results)
    print(f"  Total approvals auto-resolved: {total_approved}")

    all_tools = []
    for r in results:
        all_tools.extend([t["name"] for t in r["tool_calls"]])
    if all_tools:
        from collections import Counter
        print(f"  Tools used: {dict(Counter(all_tools))}")

    active = [r for r in results if r["status"] not in ("validation_ok",)]
    if active:
        avg_dur = sum(r["duration_ms"] for r in active) / len(active)
        print(f"  Avg duration: {avg_dur:.0f}ms")

    all_errors = []
    for r in results:
        for e in r["errors"]:
            if "Correctly rejected" not in e:
                all_errors.append((r["test_num"], e))
    if all_errors:
        print(f"\n  All errors ({len(all_errors)}):")
        for num, err in all_errors:
            print(f"    Test {num}: {err[:150]}")
    else:
        print("\n  No errors!")


if __name__ == "__main__":
    main()
