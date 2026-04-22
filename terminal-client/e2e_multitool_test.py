"""
Multi-tool Integration E2E Tests — 20 scenarios exercising new tools.

Each test targets specific multi-tool chains across the 18 available tools,
focusing on the 10 new tools (az_cost_query, az_monitor_logs, az_rest_api,
generate_file, az_devops, az_policy_check, az_advisor, network_test,
diagram_gen, web_fetch) and how they interact with each other and the
original 8 tools.

Run with: python e2e_multitool_test.py
Requires: Backend running on localhost:8002
"""

import json
import shutil
import sys
import time
import threading
from pathlib import Path
import httpx

BASE_URL = "http://localhost:8002"
HEADERS = {"X-Dev-User": "dev-user"}

TESTS = [
    # ── 1. Cost + Resource Graph: Correlate spend with resource inventory ──
    {
        "name": "Cost + RG: Spending vs resources",
        "skill_id": "shared:architect",
        "message": (
            "I want to understand our cloud spend. First use az_cost_query to get "
            "this month's cost breakdown by resource group. Then use az_resource_graph "
            "to count how many resources exist in each resource group. Combine both "
            "results and tell me the cost-per-resource for each group."
        ),
        "expected_tools": ["az_cost_query", "az_resource_graph"],
        "min_tools": 2,
    },
    # ── 2. Monitor + Advisor: Logs + recommendations crossref ──
    {
        "name": "Monitor + Advisor: Health audit",
        "skill_id": "shared:architect",
        "message": (
            "Perform a health audit: First check Azure Advisor for any High-impact "
            "recommendations. Then use az_monitor_logs to query the AzureActivity log "
            "for any failed operations in the last 24 hours. Cross-reference the "
            "advisor recommendations with actual failures and summarize actionable items."
        ),
        "expected_tools": ["az_advisor", "az_monitor_logs"],
        "min_tools": 2,
    },
    # ── 3. Policy + RG: Compliance mapped to resources ──
    {
        "name": "Policy + RG: Compliance vs inventory",
        "skill_id": "shared:architect",
        "message": (
            "Check Azure Policy compliance using az_policy_check to get non-compliant "
            "resources. Then use az_resource_graph to get details about those specific "
            "non-compliant resources (type, location, tags). Give me a compliance "
            "report with remediation priority."
        ),
        "expected_tools": ["az_policy_check", "az_resource_graph"],
        "min_tools": 2,
    },
    # ── 4. Network + RG: DNS check then resource lookup ──
    {
        "name": "Network + RG: Connectivity diagnosis",
        "skill_id": "shared:architect",
        "message": (
            "I'm having issues reaching our services. First use network_test to do "
            "a DNS lookup for 'management.azure.com' and test port 443 on it. Then "
            "use az_resource_graph to list all our NSGs. Tell me if we can reach "
            "Azure management plane and what NSGs might be interfering."
        ),
        "expected_tools": ["network_test", "az_resource_graph"],
        "min_tools": 2,
    },
    # ── 5. Diagram + KB: Architecture from docs ──
    {
        "name": "Diagram + KB: Generate arch from KB",
        "skill_id": "shared:architect",
        "message": (
            "Search our knowledge base for architecture documentation or ADRs. "
            "Then generate a Mermaid architecture diagram showing the main components "
            "and their relationships based on what you found in the KB. Use diagram_gen "
            "to produce the diagram."
        ),
        "expected_tools": ["search_kb", "diagram_gen"],
        "min_tools": 2,
    },
    # ── 6. Web fetch + Docs: External research ──
    {
        "name": "Web + Docs: Research Azure pricing",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Research Azure Kubernetes Service pricing. Use fetch_ms_docs to find "
            "the official AKS pricing documentation. Then use web_fetch to fetch "
            "the Azure pricing calculator page at https://azure.microsoft.com/en-us/pricing/details/kubernetes-service/. "
            "Summarize the key pricing tiers."
        ),
        "expected_tools": ["fetch_ms_docs", "web_fetch"],
        "min_tools": 2,
    },
    # ── 7. Cost + Policy: Spend vs compliance ──
    {
        "name": "Cost + Policy: Spend on non-compliant",
        "skill_id": "shared:architect",
        "message": (
            "Are we spending money on non-compliant resources? Use az_policy_check to "
            "find non-compliant resources, then use az_cost_query to get the current "
            "month's costs by resource group. Cross-reference to estimate how much "
            "we're spending on non-compliant resource groups."
        ),
        "expected_tools": ["az_policy_check", "az_cost_query"],
        "min_tools": 2,
    },
    # ── 8. Generate file + Diagram: Create deliverable ──
    {
        "name": "Generate + Diagram: Create doc with diagram",
        "skill_id": "shared:architect",
        "message": (
            "Create a deployment architecture document. First generate a Mermaid "
            "flowchart diagram showing: User → CDN → App Service → SQL Database → Blob Storage. "
            "Then use generate_file to save the complete architecture document "
            "(with the diagram embedded) as 'architecture.md'."
        ),
        "expected_tools": ["diagram_gen", "generate_file"],
        "min_tools": 2,
    },
    # ── 9. RG + REST API: Query then deep-dive ──
    {
        "name": "RG + REST: Find resources then REST details",
        "skill_id": "shared:architect",
        "message": (
            "Use az_resource_graph to find all App Services in the subscription. "
            "Then for the first result, use az_rest_api with a GET call to the "
            "ARM endpoint to get its full configuration including site config, "
            "app settings structure, and hosting plan details."
        ),
        "expected_tools": ["az_resource_graph", "az_rest_api"],
        "min_tools": 2,
    },
    # ── 10. Monitor + Network: Diagnose connectivity issue ──
    {
        "name": "Monitor + Network: Connectivity diagnosis",
        "skill_id": "shared:architect",
        "message": (
            "Help diagnose a connectivity issue. First use az_monitor_logs to query "
            "for any NetworkConnection or AzureFirewall events in the last hour. "
            "Then use network_test to check if we can reach login.microsoftonline.com "
            "on port 443 and do a DNS lookup for it. Correlate the findings."
        ),
        "expected_tools": ["az_monitor_logs", "network_test"],
        "min_tools": 2,
    },
    # ── 11. Advisor + Generate: Export recommendations ──
    {
        "name": "Advisor + Generate: Export report",
        "skill_id": "shared:architect",
        "message": (
            "Get all Azure Advisor recommendations using az_advisor, categorized by "
            "type (Cost, Security, Performance). Then use generate_file to save the "
            "full recommendations report as 'advisor-report.md' with a summary table "
            "at the top showing counts per category."
        ),
        "expected_tools": ["az_advisor", "generate_file"],
        "min_tools": 2,
    },
    # ── 12. DevOps + KB: Pipeline info cross-ref ──
    {
        "name": "DevOps + KB: Pipeline docs audit",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Search our knowledge base for any CI/CD or pipeline documentation. "
            "Then use az_devops to list the actual pipelines in our project. "
            "Compare: are all pipelines documented in the KB? Which ones are missing docs?"
        ),
        "expected_tools": ["search_kb", "az_devops"],
        "min_tools": 2,
    },
    # ── 13. Cost + Advisor + Generate: Full cost report ──
    {
        "name": "Cost + Advisor + Generate: Savings report",
        "skill_id": "shared:architect",
        "message": (
            "Create a complete cost optimization report. Step 1: Use az_cost_query "
            "to get this month's cost by resource type. Step 2: Use az_advisor to get "
            "Cost category recommendations. Step 3: Use generate_file to save a "
            "'cost-optimization.md' report combining current spend and advisor "
            "recommendations with estimated savings."
        ),
        "expected_tools": ["az_cost_query", "az_advisor", "generate_file"],
        "min_tools": 3,
    },
    # ── 14. Shell + Generate: System info export ──
    {
        "name": "Shell + Generate: Export system info",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Gather system information by running 'hostname' via run_shell. "
            "Then save the output to a file called 'system-info.txt' using "
            "generate_file. Include the hostname and current timestamp."
        ),
        "expected_tools": ["run_shell", "generate_file"],
        "min_tools": 2,
    },
    # ── 15. Web + Generate: Fetch and save reference ──
    {
        "name": "Web + Generate: Fetch and save docs",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Fetch the Azure status page at https://status.azure.com using web_fetch "
            "in text mode. Then save a summary of the current Azure service health "
            "to 'azure-status.md' using generate_file."
        ),
        "expected_tools": ["web_fetch", "generate_file"],
        "min_tools": 2,
    },
    # ── 16. RG + Diagram: Visualize infrastructure ──
    {
        "name": "RG + Diagram: Auto-generate infra diagram",
        "skill_id": "shared:architect",
        "message": (
            "Use az_resource_graph to discover all resource types in the subscription "
            "and count them. Then use diagram_gen to create a Mermaid pie chart or "
            "flowchart showing the distribution of resource types in our environment."
        ),
        "expected_tools": ["az_resource_graph", "diagram_gen"],
        "min_tools": 2,
    },
    # ── 17. Network + Docs: Diagnose then research ──
    {
        "name": "Network + Docs: Check then research fix",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Test connectivity to vault.azure.net on port 443 using network_test. "
            "Whether it succeeds or fails, look up Microsoft documentation for "
            "Azure Key Vault network requirements and private endpoint setup using "
            "fetch_ms_docs. Give me a connectivity assessment with fix steps."
        ),
        "expected_tools": ["network_test", "fetch_ms_docs"],
        "min_tools": 2,
    },
    # ── 18. KB + Learnings + Generate: Knowledge export ──
    {
        "name": "KB + Learnings + Generate: Export knowledge",
        "skill_id": "shared:chat-with-kb",
        "message": (
            "Search the knowledge base for all documentation topics. Then read the "
            "learnings file. Combine both into a comprehensive 'knowledge-export.md' "
            "saved via generate_file, with sections for KB topics and agent learnings."
        ),
        "expected_tools": ["search_kb", "read_learnings", "generate_file"],
        "min_tools": 3,
    },
    # ── 19. Policy + Monitor + Diagram: Security posture ──
    {
        "name": "Policy + Monitor + Diagram: Security visual",
        "skill_id": "shared:architect",
        "message": (
            "Build a security posture overview. First check az_policy_check for "
            "compliance status. Then query az_monitor_logs for any security-related "
            "events (SecurityEvent or AzureActivity with security operations). "
            "Finally use diagram_gen to create a flowchart showing the compliance "
            "state and key security findings."
        ),
        "expected_tools": ["az_policy_check", "az_monitor_logs", "diagram_gen"],
        "min_tools": 3,
    },
    # ── 20. RG + Cost + Network + Generate: Full environment audit ──
    {
        "name": "RG + Cost + Net + Gen: Full audit report",
        "skill_id": "shared:architect",
        "message": (
            "Perform a complete environment audit. Step 1: Use az_resource_graph to "
            "count all resources by type. Step 2: Use az_cost_query for this month's "
            "total cost. Step 3: Use network_test to verify Azure management plane "
            "connectivity (DNS lookup for management.azure.com). "
            "Step 4: Save the complete audit report to 'env-audit.md' using generate_file."
        ),
        "expected_tools": ["az_resource_graph", "az_cost_query", "network_test", "generate_file"],
        "min_tools": 4,
    },
]


_BAIL_PHRASES = [
    "not available", "don't have", "isn't available", "not have",
    "no tool", "no such tool", "tool is not", "tool isn't",
    "cannot find", "can't find the tool", "i don't see",
    "doesn't exist", "does not exist", "not provided",
    "not supported in this environment",
]

# Max ratio of actual calls to expected — beyond this is "wandering"
_MAX_EFFICIENCY_RATIO = 3.0


def run_test(test: dict, index: int) -> dict:
    """Run a single multi-tool test with threaded SSE + auto-approval."""
    result = {
        "name": test["name"],
        "skill_id": test["skill_id"],
        "message": test["message"][:120] + "...",
        "events": [],
        "errors": [],
        "tool_calls": [],
        "tool_sequence": [],
        "tool_errors": {},       # tool_name -> [error_msg, ...]
        "tool_successes": set(), # tool_names that returned without error
        "approvals_auto_resolved": 0,
        "assistant_text": "",
        "conversation_id": None,
        "duration_ms": 0,
        "status": "unknown",
        "grade": "F",            # strict letter grade
        "deductions": [],        # reasons for grade reduction
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
                        tool_name = data.get("name", "?")
                        is_err = content.startswith("Error")
                        if is_err:
                            with lock:
                                result["errors"].append(
                                    f"Tool {tool_name}: {content[:200]}"
                                )
                                result["tool_errors"].setdefault(tool_name, []).append(content[:200])
                        else:
                            with lock:
                                result["tool_successes"].add(tool_name)
                    elif event_type == "done":
                        with lock:
                            result["conversation_id"] = data.get("conversation_id")
                    elif event_type == "error":
                        with lock:
                            result["errors"].append(data.get("message", "unknown"))
                elif line.strip() == "":
                    event_type = ""
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

        # Poll for approvals, auto-approve all
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

    # ── Strict evaluation ────────────────────────────────────────────────
    with lock:
        unique_tools = set(result["tool_sequence"])
        expected_set = set(test["expected_tools"])
        tools_hit = expected_set & unique_tools
        tools_missed = expected_set - unique_tools
        extra_tools = unique_tools - expected_set
        total_calls = len(result["tool_calls"])
        deductions = result["deductions"]
        score = 100  # start perfect, deduct

        # 1. Did the agent call ALL expected tools?
        if tools_missed:
            score -= 30 * len(tools_missed)
            deductions.append(f"missed expected tools: {sorted(tools_missed)}")

        # 2. Did the expected tools SUCCEED (not error)?
        expected_succeeded = expected_set & result["tool_successes"]
        expected_errored = expected_set & set(result["tool_errors"].keys())
        if expected_errored:
            # Only deduct if the tool errored AND never succeeded
            only_errored = expected_errored - expected_succeeded
            if only_errored:
                score -= 15 * len(only_errored)
                deductions.append(f"expected tools errored: {sorted(only_errored)}")

        # 3. Efficiency — penalty for excessive wandering
        if total_calls > 0 and test["min_tools"] > 0:
            ratio = total_calls / test["min_tools"]
            if ratio > _MAX_EFFICIENCY_RATIO:
                penalty = min(20, int((ratio - _MAX_EFFICIENCY_RATIO) * 10))
                score -= penalty
                deductions.append(f"wandering: {total_calls} calls for {test['min_tools']} expected (ratio {ratio:.1f}x)")
            elif ratio > 2.0:
                score -= 5
                deductions.append(f"slightly verbose: {total_calls} calls for {test['min_tools']} expected")

        # 4. Bail language — agent said "tool not available" etc
        text_lower = result["assistant_text"].lower()
        bail_found = [p for p in _BAIL_PHRASES if p in text_lower]
        if bail_found:
            # Check if the bail language is about tools we actually expected
            # (vs the agent just noting some other limitation)
            for tool in expected_set:
                tool_display = tool.replace("_", " ").replace("-", " ")
                for phrase in bail_found:
                    # Check if bail phrase appears near tool name
                    idx = text_lower.find(phrase)
                    if idx >= 0:
                        context = text_lower[max(0, idx-80):idx+80]
                        if tool in context or tool_display in context:
                            score -= 25
                            deductions.append(f"bail: agent said '{phrase}' about {tool}")
                            break

        # 5. No response at all
        if not result["assistant_text"].strip():
            score -= 40
            deductions.append("empty response")

        # 6. No tools called at all
        if total_calls == 0:
            score -= 30
            deductions.append("no tools called")

        # ── Assign grade ──
        score = max(0, score)
        if score >= 90:
            result["grade"] = "A"
        elif score >= 75:
            result["grade"] = "B"
        elif score >= 60:
            result["grade"] = "C"
        elif score >= 40:
            result["grade"] = "D"
        else:
            result["grade"] = "F"

        # Legacy fields
        result["chaining_ok"] = (
            len(tools_missed) == 0
            and total_calls >= test["min_tools"]
            and len(expected_errored - expected_succeeded) == 0  # expected tools must succeed
        )

        if result["assistant_text"] and total_calls >= test["min_tools"]:
            result["status"] = "ok" if not tools_missed else "wrong_tools"
        elif result["assistant_text"] and total_calls > 0:
            result["status"] = "partial"
        elif result["assistant_text"]:
            result["status"] = "no_tools"
        else:
            result["status"] = "empty"

        result["score"] = score
        result["tools_missed"] = sorted(tools_missed)
        result["tools_extra"] = sorted(extra_tools)
        result["expected_errored"] = sorted(expected_errored - expected_succeeded)

    return result


def main():
    total = len(TESTS)
    print("=" * 78)
    print(f"NEXUS MULTI-TOOL E2E — {total} integration scenarios")
    print("=" * 78)

    # Health check
    try:
        r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
        print(f"Backend: {r.json()}\n")
    except Exception as e:
        print(f"ERROR: Backend not reachable at {BASE_URL}: {e}")
        sys.exit(1)

    # Clean output/ dir from previous runs to avoid "already exists" errors
    output_dir = Path(__file__).resolve().parent.parent / "backend" / "output"
    if output_dir.exists():
        file_count = sum(1 for _ in output_dir.rglob("*") if _.is_file())
        if file_count > 0:
            shutil.rmtree(output_dir)
            output_dir.mkdir(exist_ok=True)
            print(f"  Cleaned {file_count} files from {output_dir}\n")

    results = []

    for i, test in enumerate(TESTS):
        print(f"  [{i+1:2d}/{total}] {test['name']}")
        print(f"         expects: {' + '.join(test['expected_tools'])} (min {test['min_tools']} calls)")
        print(f"         ", end="", flush=True)

        # Health check between tests
        try:
            hc = httpx.get(f"{BASE_URL}/healthz", timeout=5)
            if hc.status_code != 200:
                print("[!] Backend unhealthy, waiting 5s...")
                time.sleep(5)
        except Exception:
            print("[!] Backend unreachable, waiting 5s...")
            time.sleep(5)

        r = run_test(test, i)
        results.append(r)

        # Report
        seq = " → ".join(r["tool_sequence"]) if r["tool_sequence"] else "(none)"
        chain_icon = "✓" if r["chaining_ok"] else "✗"
        grade = r.get("grade", "?")
        score = r.get("score", 0)
        approv = f" approved:{r['approvals_auto_resolved']}" if r["approvals_auto_resolved"] else ""

        print(
            f"[{grade}] {score:3d}/100 | chain:{chain_icon} | "
            f"{r['duration_ms']}ms | tools:{len(r['tool_calls'])}{approv}"
        )
        print(f"         sequence: {seq}")

        if r.get("tools_missed"):
            print(f"         MISSED: {r['tools_missed']}")
        if r.get("expected_errored"):
            print(f"         ERRORED: {r['expected_errored']}")
        if r.get("tools_extra"):
            print(f"         extra: {r['tools_extra']}")
        if r.get("deductions"):
            for d in r["deductions"]:
                print(f"         └─ -{d}")

        if r["errors"]:
            for err in r["errors"][:2]:
                print(f"         └─ err: {err[:150]}")

        snippet = r["assistant_text"].replace("\n", " ")[:150]
        if snippet:
            print(f"         response: {snippet}...")
        print()

    # Write full results
    out_file = "e2e_multitool_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Full results → {out_file}\n")

    # ── Summary ──────────────────────────────────────────────────────────
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)

    # Grade distribution
    from collections import Counter
    grades = Counter(r.get("grade", "?") for r in results)
    grade_str = "  ".join(f"{g}: {c}" for g, c in sorted(grades.items()))
    print(f"  Grades: {grade_str}")

    avg_score = sum(r.get("score", 0) for r in results) / len(results)
    print(f"  Average score: {avg_score:.1f}/100")

    chain_ok = sum(1 for r in results if r["chaining_ok"])
    print(f"  Strict chaining correct: {chain_ok}/{total}")

    # Status breakdown
    ok = sum(1 for r in results if r["status"] == "ok")
    wrong_tools = sum(1 for r in results if r["status"] == "wrong_tools")
    partial = sum(1 for r in results if r["status"] == "partial")
    no_tools = sum(1 for r in results if r["status"] == "no_tools")
    errs = sum(1 for r in results if r["status"] in ("empty", "exception", "http_error"))
    print(f"  Status: OK={ok}  Wrong-tools={wrong_tools}  Partial={partial}  No-tools={no_tools}  Errors={errs}")

    # Efficiency
    total_calls = sum(len(r["tool_calls"]) for r in results)
    expected_min_calls = sum(r["min_tools"] for r in results)
    extra_tool_count = sum(len(r.get("tools_extra", [])) for r in results)
    total_errors = sum(len(r["errors"]) for r in results)
    print(f"  Total tool calls: {total_calls} (expected min: {expected_min_calls}, ratio: {total_calls/expected_min_calls:.1f}x)")
    print(f"  Extra tools used: {extra_tool_count}")
    print(f"  Tool errors: {total_errors}")

    total_approved = sum(r["approvals_auto_resolved"] for r in results)
    print(f"  Approvals auto-resolved: {total_approved}")

    # Tool usage stats
    all_tools = []
    for r in results:
        all_tools.extend(r["tool_sequence"])
    if all_tools:
        usage = Counter(all_tools)
        print(f"  Tool usage: {dict(usage.most_common())}")

    # Timing
    active = [r for r in results if r["status"] not in ("http_error",)]
    if active:
        avg = sum(r["duration_ms"] for r in active) / len(active)
        fastest = min(r["duration_ms"] for r in active)
        slowest = max(r["duration_ms"] for r in active)
        print(f"  Timing — avg: {avg:.0f}ms  fastest: {fastest}ms  slowest: {slowest}ms")

    # Per-test scorecard
    print(f"\n  Scorecard:")
    for r in results:
        grade = r.get("grade", "?")
        score = r.get("score", 0)
        chain_icon = "✓" if r["chaining_ok"] else "✗"
        missed = r.get("tools_missed", [])
        errored = r.get("expected_errored", [])
        issues = []
        if missed:
            issues.append(f"missed:{missed}")
        if errored:
            issues.append(f"errored:{errored}")
        extra = r.get("tools_extra", [])
        if extra:
            issues.append(f"+{len(extra)} extra")
        issue_str = f" — {'; '.join(issues)}" if issues else ""
        print(f"    [{grade}] {score:3d}  {chain_icon} {r['name']}{issue_str}")

    # New tools coverage
    new_tools = {
        "az_cost_query", "az_monitor_logs", "az_rest_api", "generate_file",
        "az_devops", "az_policy_check", "az_advisor", "network_test",
        "diagram_gen", "web_fetch",
    }
    used_new = new_tools & set(all_tools)
    unused_new = new_tools - used_new
    print(f"\n  New tool coverage: {len(used_new)}/{len(new_tools)}")
    if unused_new:
        print(f"  Untested new tools: {', '.join(sorted(unused_new))}")
    else:
        print(f"  All new tools exercised ✓")

    # Behavioral notes
    notes = []
    for r in results:
        if r["status"] == "no_tools":
            notes.append(f"⚠ {r['name']}: Agent answered without using any tools")
        elif r["status"] == "wrong_tools":
            notes.append(f"⚠ {r['name']}: Called tools but missed expected ones")
        elif r["status"] == "partial":
            notes.append(f"⚠ {r['name']}: Incomplete chaining ({len(r['tool_calls'])} calls)")
        if r.get("expected_errored"):
            notes.append(f"✗ {r['name']}: Expected tools errored: {r['expected_errored']}")
        tc = len(r["tool_calls"])
        if tc > 0 and r["min_tools"] > 0 and tc / r["min_tools"] > _MAX_EFFICIENCY_RATIO:
            notes.append(f"⚠ {r['name']}: Wandering — {tc} calls for {r['min_tools']} expected")
    if notes:
        print(f"\n  Behavioral notes ({len(notes)}):")
        for n in notes:
            print(f"    {n}")

    print()
    a_count = grades.get("A", 0)
    b_count = grades.get("B", 0)
    pass_count = a_count + b_count
    fail_count = total - pass_count
    print(f"  Final: {pass_count}/{total} passed (A+B), {fail_count} below standard")
    print(f"  Average: {avg_score:.1f}/100 | Strict chain: {chain_ok}/{total}")
    print()


if __name__ == "__main__":
    main()
