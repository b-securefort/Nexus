"""Driver to invoke generate_drawio_from_python for the L1 + 5 drill diagrams.

Not committed; deleted after diagram generation completes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth.models import User
from app.tools.generic.python_to_drawio import GenerateDrawioFromPythonTool


DRILL1_CODE = r'''
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.client import Users

with Diagram("Nexus Drill 1 - Chat turn lifecycle", direction="TB", show=False, graph_attr={"nodesep": "1.5", "ranksep": "1.2"}):
    user = Users("User")
    frontend = AzureGeneric("Frontend\n(MSAL + SSE consumer)", azure_icon="globe")

    with Cluster("Per chat turn (<= 15 LLM iters)"):
        api = AzureGeneric("api/chat (SSE)", azure_icon="apim")
        save = AzureGeneric("Save user msg\n+ attachments", azure_icon="resource_group")
        compact = AzureGeneric("Compaction\n(history -> bullets)", azure_icon="automation")
        learn = AzureGeneric("Learnings retrieval\n(BM25 + vec + RRF)", azure_icon="ai_search")
        sysprompt = AzureGeneric("System prompt build\n(KB summary + learnings\n+ ARM ctx + pinned task)", azure_icon="resource_group")
        cb = AzureGeneric("Circuit breaker", azure_icon="policy")
        dispatch = AzureGeneric("Tool dispatch\n+ approval/ask_user", azure_icon="conditional_access")
        tools = AzureGeneric("Tool execute\n(subprocess for az tools)", azure_icon="subscription")
        done = AzureGeneric("SSE done event\n+ usage payload", azure_icon="apim")

    aoai = AzureGeneric("Azure OpenAI\n(chat completions)", azure_icon="openai")

    # Linear flow with numbered edges
    user >> Edge(label="1 user msg") >> frontend >> Edge(label="2 SSE POST") >> api >> Edge(label="3") >> save
    save >> Edge(label="4") >> compact >> Edge(label="5") >> learn >> Edge(label="6") >> sysprompt
    sysprompt >> Edge(label="7 chat") >> cb >> Edge(label="8") >> aoai
    aoai >> Edge(label="9 tokens + tool_calls") >> dispatch
    dispatch >> Edge(label="10 execute") >> tools
    # Loop back to next iter via system prompt (dashed = loop edge)
    tools >> Edge(style="dashed", label="11 tool result\n(loop <=15)") >> sysprompt
    # Terminal: when LLM returns no tool_calls, emit done
    aoai >> Edge(style="dashed", label="12 final assistant msg") >> done
    done >> Edge(label="13 SSE done") >> frontend
'''


DRILL2_CODE = r'''
from diagrams import Diagram, Cluster, Edge

with Diagram("Nexus Drill 2 - Tool execution & approval pipeline", direction="LR", show=False, graph_attr={"nodesep": "1.2", "ranksep": "1.0"}):
    orch = AzureGeneric("Orchestrator\n(LLM returned tool_call)", azure_icon="policy")

    with Cluster("Tool dispatch pipeline (app/agent/concurrency.py)"):
        skill_check = AzureGeneric("Skill allowlist\n+ skill_name CV", azure_icon="conditional_access")
        sema = AzureGeneric("Per-user Semaphore(4)\n+ ThreadPool(64)", azure_icon="resource_group")
        arm_pre = AzureGeneric("ARM token preflight\n(JWT exp claim)", azure_icon="conditional_access")
        blocked = AzureGeneric("Blocked-prefix check\n(az account clear,\nad app/sp create/delete,\nrole assignment delete)", azure_icon="policy")
        approval = AzureGeneric("Approval gate /\nask_user pause\n(pending_approvals,\npending_questions)", azure_icon="conditional_access")
        subproc = AzureGeneric("subprocess.run\nenv allowlist (~14 keys)\nshell=False\nAZURE_ACCESS_TOKEN injected", azure_icon="automation")

    with Cluster("Result handling"):
        result = AzureGeneric("Tool result\n(status/output)", azure_icon="resource_group")
        summarise = AzureGeneric("LLM summariser\n(if >2 KB, non-error)", azure_icon="openai")
        learn_trigger = AzureGeneric("Success-after-failure\ndetector\n(triggers learning write)", azure_icon="ai_search")

    arm = AzureGeneric("Azure ARM / CLI", azure_icon="subscription")
    aoai = AzureGeneric("Azure OpenAI\n(summariser deployment)", azure_icon="openai")

    orch >> Edge(label="1 tool_call") >> skill_check
    skill_check >> Edge(label="2 allowed") >> sema
    sema >> Edge(label="3") >> arm_pre
    arm_pre >> Edge(label="4 ok / refresh") >> blocked
    blocked >> Edge(label="5 not blocked") >> approval
    approval >> Edge(label="6 approved") >> subproc
    subproc >> Edge(label="7 az + token CV") >> arm
    subproc >> Edge(label="8 stdout/stderr") >> result
    result >> Edge(label="9 if >2 KB") >> summarise >> Edge(label="10") >> aoai
    result >> Edge(style="dashed", label="11 back to orchestrator") >> orch
    result >> Edge(style="dashed", label="12 after retry success") >> learn_trigger
'''


DRILL3_CODE = r'''
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.database import SQLDatabases

with Diagram("Nexus Drill 3 - KB hybrid retrieval", direction="TB", show=False, graph_attr={"nodesep": "1.5", "ranksep": "1.2"}):
    # Ingest path (top)
    kb_git = AzureGeneric("KB Git repo\n(ADO / GitHub)", azure_icon="devops")
    sync = AzureGeneric("git sync (15m)\n+ normalize markdown", azure_icon="automation")
    chunker = AzureGeneric("Chunker\n(split at H2/H3,\nKB_CHUNK_MAX_CHARS)", azure_icon="resource_group")
    embed_ingest = AzureGeneric("Azure OpenAI embed\ntext-embedding-3-small\n(1536 dims, document side)", azure_icon="openai")

    # Storage (middle) - 3 virtual tables on app.db
    with Cluster("app.db (SQLite + sqlite-vec, WAL mode)"):
        chunks = SQLDatabases("kb_chunks\n(canonical: path,\nchunk_idx, heading, text,\ncontent_hash, source_url,\nembed_model)")
        fts = AzureGeneric("kb_chunks_fts\n(FTS5 virtual,\nunicode61 no porter)", azure_icon="ai_search")
        vec = AzureGeneric("kb_chunks_vec\n(vec0 virtual,\nfloat[1536])", azure_icon="ai_search")

    # Query path (bottom)
    query_in = AzureGeneric("search_kb_hybrid\n(tool call from agent)", azure_icon="apim")
    embed_query = AzureGeneric("Azure OpenAI embed\n(query side, ~50 ms)", azure_icon="openai")
    bm25 = AzureGeneric("BM25 stage\n(FTS5 MATCH)", azure_icon="ai_search")
    vec_stage = AzureGeneric("Vector stage\n(vec0 MATCH ORDER BY distance)", azure_icon="ai_search")
    rrf = AzureGeneric("Reciprocal Rank Fusion\n(_rrf_fuse)", azure_icon="resource_group")
    results = AzureGeneric("Top-K chunks\n+ source_url cite", azure_icon="apim")

    # Ingest chain
    kb_git >> Edge(label="1 pull") >> sync >> Edge(label="2 changed *.md") >> chunker
    chunker >> Edge(label="3 chunk text") >> embed_ingest >> Edge(label="4 vector") >> chunks
    chunks >> Edge(style="dashed", label="5 trigger") >> fts
    chunks >> Edge(style="dashed", label="6 vec0 insert") >> vec

    # Query chain
    query_in >> Edge(label="A query text") >> embed_query
    embed_query >> Edge(label="B vec") >> vec_stage
    query_in >> Edge(label="C tokens") >> bm25
    bm25 >> Edge(label="D rank list") >> rrf
    vec_stage >> Edge(label="E rank list") >> rrf
    rrf >> Edge(label="F fused") >> results

    # Query stages read from their FTS / vec tables — relationship is conveyed
    # by the stage labels (BM25 stage / Vector stage) and the parent kb_chunks
    # row. Explicit cross-cluster edges would cross unrelated icons.
'''


DRILL4_CODE = r'''
from diagrams import Diagram, Cluster, Edge

with Diagram("Nexus Drill 4 - Learnings write defense + retrieval", direction="TB", show=False, graph_attr={"nodesep": "1.5", "ranksep": "1.2"}):
    orch = AzureGeneric("Orchestrator\n(success-after-failure\ndetector)", azure_icon="policy")

    with Cluster("Write path (orchestrator-owned, no agent tool)"):
        derive = AzureGeneric("Derive raw learning\n(rule-based from\nfailure -> success delta)", azure_icon="resource_group")
        rephrase = AzureGeneric("Rephrase via LLM\nstrict 'no opinions' prompt", azure_icon="openai")
        gate1 = AzureGeneric("Gate 1: Regex\n_OVERRIDE_PATTERNS\n(ignore validator,\ntoo strict, skip check)", azure_icon="policy")
        gate2 = AzureGeneric("Gate 2: Name guard\n(GUIDs, env-specific\nresource names)", azure_icon="policy")
        gate3 = AzureGeneric("Gate 3: LLM judge\nfails closed\n(approve=False on error)", azure_icon="openai")

    rejected = AzureGeneric("Rejected entry\nstatus=rejected\n(audit only,\ncannot reactivate)", azure_icon="policy")

    with Cluster("Storage (app.db, sqlite-vec)"):
        learnings_tbl = AzureGeneric("agent_learnings\nstatus=provisional/active/\narchived/rejected\nvalidation_count, failure_count", azure_icon="ai_search")
        embed = AzureGeneric("Azure OpenAI embed\n(same deployment as KB)", azure_icon="openai")
        lvec = AzureGeneric("agent_learnings_vec\nfloat[1536]", azure_icon="ai_search")
        lfts = AzureGeneric("agent_learnings_fts\n(FTS5)", azure_icon="ai_search")

    with Cluster("Retrieval (per turn, into next system prompt)"):
        retrieve = AzureGeneric("retrieve_relevant_learnings\nBM25 + vec + RRF\n+ status/tool boosts", azure_icon="ai_search")
        prompt_inject = AzureGeneric("Inject [CANONICAL] / [PROVISIONAL]\nmarkers into system prompt\n(omitted if 0 matches)", azure_icon="resource_group")
        mark = AzureGeneric("mark_learning_outcome\nincr validation_count / failure_count\nauto-promote at 3, auto-archive at 3", azure_icon="automation")

    # Write pipeline
    orch >> Edge(label="1 success after failure") >> derive
    derive >> Edge(label="2") >> rephrase
    rephrase >> Edge(label="3") >> gate1
    gate1 >> Edge(label="4 pass") >> gate2
    gate2 >> Edge(label="5 pass") >> gate3
    gate3 >> Edge(label="6 approve") >> learnings_tbl
    gate1 >> Edge(style="dashed", label="reject") >> rejected
    gate2 >> Edge(style="dashed", label="reject") >> rejected
    gate3 >> Edge(style="dashed", label="reject") >> rejected

    # Embed and index
    learnings_tbl >> Edge(label="7 inline embed (limit=1)") >> embed
    embed >> Edge(label="8") >> lvec
    learnings_tbl >> Edge(style="dashed", label="trigger") >> lfts

    # Retrieval
    lvec >> Edge(label="A") >> retrieve
    lfts >> Edge(label="B") >> retrieve
    retrieve >> Edge(label="C top-5") >> prompt_inject
    prompt_inject >> Edge(style="dashed", label="next turn") >> orch
    prompt_inject >> Edge(style="dashed", label="track usage") >> mark
    mark >> Edge(style="dashed", label="counter update") >> learnings_tbl
'''


DRILL5_CODE = r'''
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.database import SQLDatabases
from diagrams.onprem.client import Users

with Diagram("Nexus Drill 5 - Skills & RBAC", direction="TB", show=False, graph_attr={"nodesep": "1.5", "ranksep": "1.2"}):
    user = Users("User")
    frontend = AzureGeneric("Frontend\n(MSAL acquireToken)", azure_icon="globe")
    entra = AzureGeneric("Entra ID\n(JWT + roles claim)", azure_icon="entra_id")

    with Cluster("Backend startup"):
        appconfig = AzureGeneric("App Configuration\nNexus:RoleAccessMap\n(JSON, read once)", azure_icon="app_config")
        defaults = AzureGeneric("Hardcoded defaults\napp/auth/rbac.py\n(fallback)", azure_icon="policy")
        access_map = AzureGeneric("_ACCESS_MAP\n(in-process,\nrole -> skills+tools)", azure_icon="resource_group")

    with Cluster("Per-request auth"):
        auth_mw = AzureGeneric("Auth middleware\nvalidate JWT\nextract User.roles", azure_icon="conditional_access")

    with Cluster("Filter points (3 enforcement boundaries)"):
        skills_api = AzureGeneric("GET /api/skills\n(visibility filter)", azure_icon="apim")
        tools_api = AzureGeneric("GET /api/tools\n(allow-list filter)", azure_icon="apim")
        personal_api = AzureGeneric("POST /api/skills/personal\n403 gate on tool save\n(non-negotiable)", azure_icon="apim")

    convo_create = AzureGeneric("POST /api/conversations\nfreeze skill_snapshot_json\n(invariant: snapshot wins)", azure_icon="apim")
    db = SQLDatabases("conversations.skill_snapshot_json\n+ personal_skills.tools_json")
    orch = AzureGeneric("Orchestrator\nresolves tools from snapshot,\nnot live skill", azure_icon="policy")

    # Identity flow
    user >> Edge(label="1 sign in") >> frontend >> Edge(label="2 token req") >> entra
    entra >> Edge(label="3 JWT (roles)") >> auth_mw

    # Startup config load
    appconfig >> Edge(label="0 @ startup") >> access_map
    defaults >> Edge(style="dashed", label="fallback") >> access_map

    # Per-request: roles drive the three filter points. _ACCESS_MAP is the
    # in-process lookup they consult; the cluster grouping conveys that, so
    # explicit access_map -> filter edges would just cross unrelated icons.
    auth_mw >> Edge(label="4 visibility") >> skills_api
    auth_mw >> Edge(label="5 allow-list") >> tools_api
    auth_mw >> Edge(label="6 save gate") >> personal_api

    # Skill snapshot freeze
    skills_api >> Edge(label="7 user picks") >> convo_create
    convo_create >> Edge(label="8 freeze") >> db
    db >> Edge(label="9 resolve") >> orch
'''


DIAGRAMS = [
    ("nexus-drill1-chat-turn-lifecycle", DRILL1_CODE),
    ("nexus-drill2-tool-approval", DRILL2_CODE),
    ("nexus-drill3-kb-hybrid-retrieval", DRILL3_CODE),
    ("nexus-drill4-learnings-defense", DRILL4_CODE),
    ("nexus-drill5-skills-rbac", DRILL5_CODE),
]


def main() -> None:
    user = User(oid="dev-diagram", email="dev@local", display_name="dev")
    tool = GenerateDrawioFromPythonTool()

    for filename, code in DIAGRAMS:
        print(f"\n{'='*60}\nGenerating: {filename}\n{'='*60}")
        result = tool.execute({"filename": filename, "code": code}, user)
        print(result)


if __name__ == "__main__":
    main()
