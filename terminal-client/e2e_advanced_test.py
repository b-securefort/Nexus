"""
Advanced E2E tests — 10 multi-step chained tool call scenarios.

Each test requires the agent to:
  1. Call tool A to get information
  2. Use that output as context/input for tool B
  3. Synthesize a complex result

This exercises the agent's ability to chain tools, pass data between steps,
and produce composite answers.

Run with: python e2e_advanced_test.py
"""

import json
import sys
import time
import threading
import httpx

BASE_URL = "http://localhost:8002"
HEADERS = {"X-Dev-User": "dev-user"}

# 10 advanced chained-call scenarios
ADVANCED_TESTS = [
    # 1. Resource Graph → Az CLI: Find resources, then drill into one
    {
        "name": "RG→CLI: Find VMs then get details",
        "skill_id": "shared:architect",
        "message": (
            "First use Resource Graph to find all virtual machines. "
            "Then pick the first VM from the results and use az cli to get its "
            "detailed instance view (az vm get-instance-view). Show me both the "
            "list and the detailed status."
        ),
        "expected_tools": ["az_resource_graph", "az_cli"],
        "min_tools": 2,
    },
    # 2. KB Search → Resource Graph: Cross-reference docs with live infra
    {
        "name": "KB→RG: Cross-ref docs with live infra",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Search the knowledge base for any documented resource groups or "
            "environments. Then use Azure Resource Graph to verify which of those "
            "documented resource groups actually exist in Azure. Give me a comparison "
            "showing what's documented vs what's live."
        ),
        "expected_tools": ["search_kb", "az_resource_graph"],
        "min_tools": 2,
    },
    # 3. Resource Graph → Resource Graph: Two-phase query
    {
        "name": "RG→RG: Find storage then check encryption",
        "skill_id": "shared:architect",
        "message": (
            "Use Resource Graph to find all storage accounts with their names, "
            "locations, and SKUs. Then run a second Resource Graph query to check "
            "the encryption settings and access tiers for those same storage accounts. "
            "Combine both results into a single summary table."
        ),
        "expected_tools": ["az_resource_graph"],
        "min_tools": 2,
    },
    # 4. MS Docs → KB Search: Research then check internal docs
    {
        "name": "Docs→KB: Research best practices then check KB",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "First look up Microsoft docs for Azure App Service deployment best "
            "practices. Then search our knowledge base for any internal deployment "
            "instructions or guidelines we have. Compare the official recommendations "
            "with what our KB says — highlight any gaps or differences."
        ),
        "expected_tools": ["fetch_ms_docs", "search_kb"],
        "min_tools": 2,
    },
    # 5. Resource Graph → Az CLI → Summarize: Three-step chain
    {
        "name": "RG→CLI→Summary: Find NSGs then show rules",
        "skill_id": "shared:architect",
        "message": (
            "Step 1: Use Resource Graph to find all Network Security Groups. "
            "Step 2: For the first NSG found, use az cli to list its security rules "
            "(az network nsg rule list). "
            "Step 3: Summarize the security posture — are there any overly permissive "
            "rules (like allowing all inbound from any source)?"
        ),
        "expected_tools": ["az_resource_graph", "az_cli"],
        "min_tools": 2,
    },
    # 6. Shell → Az CLI: Get system info then correlate with Azure
    {
        "name": "Shell→CLI: System info then Azure context",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "First run a shell command to get the current system hostname and IP. "
            "Then use az cli to show the current Azure account and subscription. "
            "Tell me if this machine appears to be connected to Azure and which "
            "subscription is active."
        ),
        "expected_tools": ["run_shell", "az_cli"],
        "min_tools": 2,
    },
    # 7. KB Read → KB Search → Synthesize: Deep KB exploration
    {
        "name": "KB read→search: Deep knowledge synthesis",
        "skill_id": "shared:kb-searcher",
        "message": (
            "First search the KB for all files related to 'deployment'. "
            "Then read the most relevant file fully. "
            "Finally summarize the key deployment steps and any prerequisites "
            "mentioned across all KB sources."
        ),
        "expected_tools": ["search_kb", "read_kb_file"],
        "min_tools": 2,
    },
    # 8. Resource Graph → MS Docs: Find issues then research solutions
    {
        "name": "RG→Docs: Audit resources then research",
        "skill_id": "shared:architect",
        "message": (
            "Use Resource Graph to find any resources that are in a non-healthy "
            "provisioning state (not 'Succeeded'). Then look up Microsoft documentation "
            "for how to troubleshoot the specific resource type that has issues. "
            "If all resources are healthy, find resources without tags instead and "
            "look up tagging best practices."
        ),
        "expected_tools": ["az_resource_graph", "fetch_ms_docs"],
        "min_tools": 2,
    },
    # 9. Az CLI → Resource Graph → Compare: CLI vs RG data comparison
    {
        "name": "CLI→RG: Compare two data sources",
        "skill_id": "shared:architect",
        "message": (
            "Use az cli to list all resource groups (az group list). "
            "Then use Resource Graph to also query all resource groups. "
            "Compare the results from both methods — do they return the same "
            "data? Which one gives more detail? Show me side by side."
        ),
        "expected_tools": ["az_cli", "az_resource_graph"],
        "min_tools": 2,
    },
    # 10. Learnings → KB → Tools: Full reasoning chain
    {
        "name": "Learn→KB→RG: Prior knowledge + docs + live",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Check the learnings file for any known issues or gotchas. "
            "Then search the KB for any related documentation about those issues. "
            "Finally, use Resource Graph to check if any of the known issues currently "
            "apply to our live Azure environment. Give me a status report."
        ),
        "expected_tools": ["read_learnings", "search_kb", "az_resource_graph"],
        "min_tools": 3,
    },
]


def run_advanced_test(test: dict) -> dict:
    """Run a single advanced test with threaded SSE + auto-approval."""
    result = {
        "name": test["name"],
        "skill_id": test["skill_id"],
        "message": test["message"][:120] + "...",
        "events": [],
        "errors": [],
        "tool_calls": [],
        "tool_sequence": [],
        "approvals_auto_resolved": 0,
        "assistant_text": "",
        "conversation_id": None,
        "duration_ms": 0,
        "status": "unknown",
        "expected_tools": test["expected_tools"],
        "min_tools": test["min_tools"],
        "chaining_ok": False,
    }

    body = {"message": test["message"], "skill_id": test["skill_id"]}
    lock = threading.Lock()
    pending_approvals: list[dict] = []
    stream_done = threading.Event()
    start = time.time()

    def read_sse_stream(resp: httpx.Response):
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
                            name = data.get("name", "?")
                            result["tool_calls"].append({
                                "name": name,
                                "call_id": data.get("call_id"),
                                "args": data.get("args", {}),
                            })
                            result["tool_sequence"].append(name)
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
                                    f"Tool {data.get('name', '?')}: {content[:200]}"
                                )
                    elif event_type == "done":
                        with lock:
                            result["conversation_id"] = data.get("conversation_id")
                    elif event_type == "error":
                        with lock:
                            result["errors"].append(data.get("message", "unknown"))
        except Exception as e:
            with lock:
                result["errors"].append(f"SSE: {e}")
        finally:
            stream_done.set()

    try:
        client = httpx.Client(
            base_url=BASE_URL,
            headers={**HEADERS, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
        )
        resp = client.send(
            client.build_request("POST", "/api/chat", json=body),
            stream=True,
        )
        if resp.status_code != 200:
            result["errors"].append(f"HTTP {resp.status_code}")
            result["status"] = "http_error"
            resp.close()
            client.close()
            return result

        reader = threading.Thread(target=read_sse_stream, args=(resp,), daemon=True)
        reader.start()

        poll_start = time.time()
        while not stream_done.is_set() and (time.time() - poll_start) < 120:
            stream_done.wait(timeout=0.5)
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
                            headers=HEADERS,
                            timeout=10,
                        )
                        if ar.status_code == 200:
                            with lock:
                                result["approvals_auto_resolved"] += 1
                    except Exception as e:
                        with lock:
                            result["errors"].append(f"Approval: {e}")

        reader.join(timeout=5)
        resp.close()
        client.close()
    except Exception as e:
        result["errors"].append(str(e))
        result["status"] = "exception"
        result["duration_ms"] = int((time.time() - start) * 1000)
        return result

    result["duration_ms"] = int((time.time() - start) * 1000)

    # Evaluate chaining quality
    with lock:
        unique_tools = set(result["tool_sequence"])
        expected_set = set(test["expected_tools"])
        tools_hit = expected_set & unique_tools
        total_calls = len(result["tool_calls"])

        result["chaining_ok"] = (
            total_calls >= test["min_tools"]
            and len(tools_hit) >= len(expected_set)
        )

        if result["assistant_text"] and total_calls >= test["min_tools"]:
            result["status"] = "ok"
        elif result["assistant_text"] and total_calls > 0:
            result["status"] = "partial"  # got response but not enough chaining
        elif result["assistant_text"]:
            result["status"] = "no_tools"  # answered without tools
        else:
            result["status"] = "empty"

    return result


def main():
    print("=" * 74)
    print("NEXUS ADVANCED E2E — 10 chained multi-tool scenarios")
    print("=" * 74)

    try:
        r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
        print(f"Backend: {r.json()}\n")
    except Exception as e:
        print(f"ERROR: Backend not reachable: {e}")
        sys.exit(1)

    results = []

    for i, test in enumerate(ADVANCED_TESTS):
        print(f"  [{i+1:2d}/10] {test['name']}")
        print(f"         expects: {' → '.join(test['expected_tools'])} (min {test['min_tools']} calls)")
        print(f"         ", end="", flush=True)

        # Health check
        try:
            hc = httpx.get(f"{BASE_URL}/healthz", timeout=5)
            if hc.status_code != 200:
                print("[!] Backend unhealthy, waiting 5s...")
                time.sleep(5)
        except Exception:
            print("[!] Backend unreachable, waiting 5s...")
            time.sleep(5)

        r = run_advanced_test(test)
        results.append(r)

        # Report
        seq = " → ".join(r["tool_sequence"]) if r["tool_sequence"] else "(none)"
        chain_icon = "✓" if r["chaining_ok"] else "✗"
        status_icon = "✓" if r["status"] == "ok" else ("~" if r["status"] == "partial" else "✗")
        approv = f" approved:{r['approvals_auto_resolved']}" if r["approvals_auto_resolved"] else ""

        print(
            f"{status_icon} {r['status']} | chain:{chain_icon} | "
            f"{r['duration_ms']}ms | tools:{len(r['tool_calls'])}{approv}"
        )
        print(f"         sequence: {seq}")

        if r["errors"]:
            for err in r["errors"][:2]:
                print(f"         └─ {err[:140]}")

        # Show snippet of assistant response
        snippet = r["assistant_text"].replace("\n", " ")[:150]
        if snippet:
            print(f"         response: {snippet}...")
        print()

    # Write full report
    with open("e2e_advanced_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("Full results → e2e_advanced_results.json\n")

    # Summary
    print("=" * 74)
    print("SUMMARY")
    print("=" * 74)
    ok = sum(1 for r in results if r["status"] == "ok")
    partial = sum(1 for r in results if r["status"] == "partial")
    no_tools = sum(1 for r in results if r["status"] == "no_tools")
    errs = sum(1 for r in results if r["status"] in ("empty", "exception", "http_error"))
    print(f"  OK: {ok}  |  Partial: {partial}  |  No-tools: {no_tools}  |  Errors: {errs}")

    chain_ok = sum(1 for r in results if r["chaining_ok"])
    print(f"  Chaining correct: {chain_ok}/10")

    total_approved = sum(r["approvals_auto_resolved"] for r in results)
    print(f"  Approvals auto-resolved: {total_approved}")

    all_tools = []
    for r in results:
        all_tools.extend(r["tool_sequence"])
    if all_tools:
        from collections import Counter
        print(f"  Tool usage: {dict(Counter(all_tools))}")

    active = [r for r in results if r["status"] not in ("http_error",)]
    if active:
        avg = sum(r["duration_ms"] for r in active) / len(active)
        print(f"  Avg duration: {avg:.0f}ms")

    # Chaining detail
    print(f"\n  Chaining detail:")
    for r in results:
        icon = "✓" if r["chaining_ok"] else "✗"
        expected = ", ".join(r["expected_tools"])
        actual = ", ".join(set(r["tool_sequence"])) if r["tool_sequence"] else "(none)"
        print(f"    {icon} {r['name']}: expected [{expected}] got [{actual}]")

    # Behavioral observations
    print(f"\n  Behavioral notes:")
    for r in results:
        if r["status"] == "no_tools":
            print(f"    ⚠ {r['name']}: Agent answered without calling any tools")
        elif r["status"] == "partial":
            print(f"    ⚠ {r['name']}: Chaining incomplete — only {len(r['tool_calls'])} calls")
        elif not r["chaining_ok"] and r["status"] == "ok":
            expected = set(r["expected_tools"])
            actual = set(r["tool_sequence"])
            missing = expected - actual
            if missing:
                print(f"    ⚠ {r['name']}: Missing expected tools: {missing}")


if __name__ == "__main__":
    main()
