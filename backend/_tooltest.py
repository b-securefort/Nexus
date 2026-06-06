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

_GOOD_DRAWIO = '''<mxGraphModel dx="800" dy="600" grid="1" gridSize="10">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="vm1" value="Web VM" style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg" vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="vm2" value="DB VM" style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg" vertex="1" parent="1">
      <mxGeometry x="240" y="40" width="48" height="48" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>'''

# Bad: generic-styled resource (no vendor icon), two overlapping icons, literal \\n in label.
_BAD_DRAWIO = '''<mxGraphModel dx="800" dy="600">
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="g1" value="Mystery Service" style="rounded=1;fillColor=#ffffff" vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="60" height="60" as="geometry"/>
    </mxCell>
    <mxCell id="o1" value="App One" style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg" vertex="1" parent="1">
      <mxGeometry x="300" y="40" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="o2" value="App Two&#10;Line" style="shape=image;image=img/lib/azure2/compute/Virtual_Machine.svg" vertex="1" parent="1">
      <mxGeometry x="320" y="50" width="48" height="48" as="geometry"/>
    </mxCell>
    <mxCell id="lit" value="Has literal \\n break" style="shape=image;image=img/lib/azure2/web/App_Services.svg" vertex="1" parent="1">
      <mxGeometry x="600" y="40" width="48" height="48" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>'''

@group("diagram")
def g_diagram():
    # generate_file: good drawio (triggers auto-validate + auto-render)
    run("generate_file", "good drawio (auto validate+render)", {"filename": "tt_good.drawio", "content": _GOOD_DRAWIO, "overwrite": True})
    run("generate_file", "bad drawio (should flag violations)", {"filename": "tt_bad.drawio", "content": _BAD_DRAWIO, "overwrite": True})
    run("generate_file", "edge: bad extension", {"filename": "evil.exe", "content": "x"})
    run("generate_file", "edge: traversal", {"filename": "../escape.txt", "content": "x"})
    run("generate_file", "edge: exists no overwrite", {"filename": "tt_good.drawio", "content": _GOOD_DRAWIO})
    # validate_drawio standalone
    run("validate_drawio", "good -> PASS", {"filename": "tt_good.drawio"})
    run("validate_drawio", "bad -> FAIL", {"filename": "tt_bad.drawio"})
    run("validate_drawio", "edge: malformed xml", {"filename": "tt_malformed.drawio"})  # created below
    run("generate_file", "setup malformed", {"filename": "tt_malformed.drawio", "content": "<mxGraphModel><root><mxCell id=", "overwrite": True})
    run("validate_drawio", "malformed -> parse error", {"filename": "tt_malformed.drawio"})
    run("validate_drawio", "edge: missing file", {"filename": "tt_nope.drawio"})
    run("validate_drawio", "edge: non-drawio ext", {"filename": "foo.txt"})
    # patch_drawio_cell
    run("patch_drawio_cell", "move vm2 x->420", {"filename": "tt_good.drawio", "cell_id": "vm2", "x": 420})
    run("patch_drawio_cell", "edge: missing cell", {"filename": "tt_good.drawio", "cell_id": "nonexistent"})
    # render_drawio (drawio desktop installed locally)
    run("render_drawio", "render good png (local CLI)", {"filename": "tt_good.drawio", "format": "png"})
    run("render_drawio", "edge: bad format", {"filename": "tt_good.drawio", "format": "gif"})
    run("render_drawio", "edge: missing file", {"filename": "tt_nope.drawio"})
    # generate_python_diagram (needs graphviz dot)
    py_ok = ("from diagrams import Diagram, Cluster\n"
             "from diagrams.azure.compute import VM\n"
             "from diagrams.azure.network import LoadBalancers\n"
             "with Diagram('Web Tier'):\n"
             "    lb = LoadBalancers('lb')\n"
             "    lb >> VM('web1')\n"
             "    lb >> VM('web2')\n")
    run("generate_python_diagram", "valid azure diagram (needs dot)", {"filename": "tt_pydiag", "code": py_ok})
    run("generate_python_diagram", "edge: forbidden import", {"filename": "tt_evil", "code": "import os\nfrom diagrams import Diagram\nwith Diagram('x'):\n    pass\n"})
    run("generate_python_diagram", "edge: no Diagram block", {"filename": "tt_nodiag", "code": "from diagrams.azure.compute import VM\nx = VM('a')\n"})
    run("generate_python_diagram", "edge: syntax error", {"filename": "tt_syn", "code": "with Diagram('x'\n   pass"})
    # generate_drawio_from_python (needs dot -Tjson)
    run("generate_drawio_from_python", "valid (needs dot)", {"filename": "tt_p2d", "code": py_ok, "title": "Web Tier"})
    run("generate_drawio_from_python", "edge: forbidden import", {"filename": "tt_p2d_evil", "code": "import socket\nfrom diagrams import Diagram\nwith Diagram('x'):\n    pass\n"})

@group("azure")
def g_azure():
    # Persona: Azure platform engineer doing inventory & posture review. READ-ONLY only.
    SUB = "3e40a1d8-c14c-434b-946a-dd0d1775e92f"
    # az_resource_graph — KQL inventory
    run("az_resource_graph", "inventory by type", {"query": "Resources | summarize count() by type | order by count_ desc | take 10"})
    run("az_resource_graph", "project sample", {"query": "Resources | project name, type, location | take 5"})
    run("az_resource_graph", "edge: empty query", {"query": ""})
    run("az_resource_graph", "edge: bad KQL", {"query": "Resoures | tke 5"})
    run("az_resource_graph", "security: ampersand injection", {"query": "Resources | take 5 & whoami"})
    # az_cli (read-only; approval is orchestrator-level, bypassed here)
    run("az_cli", "read: account show", {"args": ["account", "show", "-o", "json"], "reason": "smoke"})
    run("az_cli", "read: group list table", {"args": ["group", "list", "-o", "table"], "reason": "inventory"})
    run("az_cli", "security: backtick injection", {"args": ["account", "show", "`whoami`"], "reason": "x"})
    run("az_cli", "security: ampersand injection", {"args": ["account", "show", "&", "calc"], "reason": "x"})
    # az_rest_api — read-only GET
    run("az_rest_api", "GET resource groups", {"method": "GET", "url": f"https://management.azure.com/subscriptions/{SUB}/resourcegroups?api-version=2021-04-01"})
    run("az_rest_api", "edge: non-azure url", {"method": "GET", "url": "https://evil.example.com/x"})
    run("az_rest_api", "edge: missing url", {"method": "GET", "url": ""})
    # az_cost_query — FinOps persona (Bugs.md #6 scope area)
    run("az_cost_query", "usage this_month by RG", {"query_type": "usage", "time_period": "this_month", "group_by": "ResourceGroup"})
    run("az_cost_query", "budget_status", {"query_type": "budget_status"})
    # az_advisor — posture
    run("az_advisor", "cost recommendations", {"category": "Cost"})
    # az_policy_check
    run("az_policy_check", "compliance summary", {"action": "compliance_summary"})
    # az_monitor_logs — likely no workspace configured
    run("az_monitor_logs", "no workspace", {"query": "AzureActivity | take 5"})
    # az_devops — likely no org configured
    run("az_devops", "list_projects (no org)", {"action": "list_projects"})
    # network_test
    run("network_test", "dns_lookup", {"action": "dns_lookup", "hostname": "learn.microsoft.com"})
    run("network_test", "port_check 443", {"action": "port_check", "hostname": "learn.microsoft.com", "port": 443})

GROUPS_ORDER = ["generic", "kb", "diagram", "script", "azure"]

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
