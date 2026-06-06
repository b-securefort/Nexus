"""
Ad-hoc tool test harness (NOT a pytest file). Imports the live TOOL_REGISTRY,
calls each tool's execute() with persona-driven inputs, prints classified results.
Run: python _tooltest.py <group>   where group in: generic, kb, diagram, script, azure, all
"""
import sys, time, traceback, json

from app.tools.base import init_tools, TOOL_REGISTRY, classify_tool_outcome, set_arm_token
from app.auth.models import User

init_tools()

USER = User(oid="dev-user", email="balaji@futurefortifiedtech.com", display_name="Balaji")

def run(tool_name, label, args, timeout_note=""):
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        print(f"\n### {tool_name} :: {label}\n  !! NOT REGISTERED")
        return
    print(f"\n### {tool_name} :: {label}")
    print(f"  args: {json.dumps(args)[:200]}")
    t0 = time.time()
    try:
        out = tool.execute(args, USER)
        dt = time.time() - t0
        outcome = classify_tool_outcome(out) if isinstance(out, str) else "non-str"
        n = len(out) if isinstance(out, str) else -1
        print(f"  [{outcome}] {dt:.2f}s  len={n}")
        body = out if isinstance(out, str) else repr(out)
        print("  " + body[:1400].replace("\n", "\n  "))
        if n > 1400:
            print(f"  ...[+{n-1400} more chars]")
    except Exception as e:
        dt = time.time() - t0
        print(f"  !! EXCEPTION after {dt:.2f}s: {type(e).__name__}: {e}")
        traceback.print_exc()

GROUPS = {}
def group(name):
    def deco(fn):
        GROUPS[name] = fn
        return fn
    return deco

@group("generic")
def g_generic():
    # web_search — persona: platform engineer comparing services
    run("web_search", "happy: reddit scope shortcut", {"query": "azure front door vs application gateway", "site": "reddit", "limit": 3})
    run("web_search", "edge: empty query", {"query": ""})
    run("web_search", "edge: double site operator", {"query": "aks autoscaling site:reddit.com", "site": "reddit", "limit": 3})
    run("web_search", "happy: plain query", {"query": "azure container apps cold start latency", "limit": 3})
    # web_fetch
    run("web_fetch", "happy: fetch MS learn page", {"url": "https://learn.microsoft.com/en-us/azure/container-apps/overview"})
    run("web_fetch", "edge: bad scheme", {"url": "file:///etc/passwd"})
    run("web_fetch", "edge: 404", {"url": "https://learn.microsoft.com/en-us/azure/this-does-not-exist-xyz"})
    # fetch_ms_docs
    run("fetch_ms_docs", "happy: search docs", {"query": "azure key vault rbac vs access policy"})
    run("fetch_ms_docs", "edge: empty", {"query": ""})
    # search_github
    run("search_github", "happy: bicep repos", {"query": "azure bicep aks module"})
    run("search_github", "edge: empty", {"query": ""})
    # search_stack_overflow
    run("search_stack_overflow", "happy: az cli error", {"query": "az cli login device code timeout"})
    run("search_stack_overflow", "edge: empty", {"query": ""})
    # search_azure_updates
    run("search_azure_updates", "happy: recent updates", {"query": "container apps"})

@group("kb")
def g_kb():
    run("search_kb", "happy: keyword", {"query": "network security group"})
    run("search_kb", "edge: empty", {"query": ""})
    run("search_kb", "edge: nonsense", {"query": "zxqwv nonexistent topic"})
    run("search_kb_hybrid", "happy: nl query", {"query": "how do we handle private endpoints"})
    run("search_kb_hybrid", "edge: empty", {"query": ""})
    run("search_kb_semantic", "happy: acronym expansion", {"query": "AKS rbac"})
    run("read_kb_file", "edge: missing path", {})
    run("read_kb_file", "edge: traversal", {"path": "../../../etc/passwd"})
    run("read_kb_file", "edge: not found", {"path": "kb/does-not-exist.md"})
    run("read_file", "edge: traversal", {"path": "../config.py"})
    run("read_file", "edge: not found", {"path": "nope.txt"})

@group("script")
def g_script():
    # execute_script — approval-gated; requires a script already under output/scripts/
    # First write one with generate_file, then execute it.
    run("generate_file", "setup: write safe ps1", {"path": "scripts/hello.ps1", "content": "Write-Output 'hello-from-nexus'\nGet-Date"})
    run("execute_script", "happy: run hello.ps1", {"path": "scripts/hello.ps1", "reason": "smoke test"})
    run("execute_script", "edge: missing args", {})
    run("execute_script", "edge: traversal", {"path": "../../config.py", "reason": "x"})
    run("execute_script", "edge: not found", {"path": "nope.ps1", "reason": "x"})
    run("execute_script", "edge: bad extension", {"path": "hello.txt", "reason": "x"})
    # ask_user — correct schema (questions array)
    run("ask_user", "happy: valid question", {"questions": [{"question": "Which subscription should I target?", "header": "Subscription", "options": [{"label": "Prod"}, {"label": "Dev"}]}]})
    run("ask_user", "edge: too few options", {"questions": [{"question": "X?", "header": "H", "options": [{"label": "only"}]}]})
    run("ask_user", "edge: not a list", {"questions": "nope"})

GROUPS_ORDER = ["generic", "kb", "script"]

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "generic"
    if which == "all":
        for g in GROUPS_ORDER:
            print(f"\n{'='*70}\n GROUP: {g}\n{'='*70}")
            GROUPS[g]()
    elif which in GROUPS:
        GROUPS[which]()
    else:
        print("unknown group", which)
