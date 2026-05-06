# Prompt: Create a PowerPoint Presentation for Nexus

> **Instructions for Claude Desktop**: Use this document as the source of truth to create a professional PowerPoint presentation about **Nexus** — a self-hosted AI assistant platform for Azure cloud teams. The presentation should excite Architects and Engineers about what Nexus can do *for them today* and what it can grow into. Use a dark, modern tech aesthetic (deep navy/charcoal background, accent colors in electric blue and teal). Slide count: **15–18 slides**. Tone: confident, technical, practical — no fluff.

---

## Context: What Is Nexus?

Nexus is a **self-hosted AI assistant** built for Azure cloud teams. It is not a generic chatbot. It combines:

- **Azure OpenAI (GPT)** as the reasoning engine
- A **team knowledge base** synced from Git (markdown docs, ADRs, runbooks, patterns)
- A **skills system** — switchable AI personas that change both behavior and available tools
- An **approval-gated tool execution engine** — it actually runs `az` CLI commands, PowerShell, KQL queries, and more

Instead of just suggesting commands, Nexus **executes them**. It retries failed commands using 3 different strategies, learns from mistakes via a persistent `learn.md`, and streams everything live via SSE.

**Tech Stack**: Python 3.11 · FastAPI · React 19 · TypeScript · SQLite · Azure OpenAI · Microsoft Entra ID (MSAL)

---

## Slide-by-Slide Guide

### Slide 1 — Title Slide

**Title**: Nexus — Your Team's AI Brain for Azure
**Subtitle**: A self-hosted AI assistant that executes, not just suggests
**Visual**: Split screen — left side shows a dark terminal with `az` commands running; right side shows the Nexus chat UI with streaming responses
**Speaker notes**: "This isn't a chatbot. This is an AI agent that knows your team's documentation, understands your Azure environment, and runs commands on your behalf."

---

### Slide 2 — The Problem We're Solving

**Title**: Why Your Team Needs Nexus

**Three pain point columns**:

1. **Context Switching Overload**
   - Switching between Azure Portal, docs, runbooks, Slack, and ChatGPT constantly
   - ChatGPT doesn't know your team's patterns, ADRs, or infrastructure

2. **Knowledge Silos**
   - Team knowledge lives in wikis, people's heads, or outdated Confluence pages
   - New engineers take weeks to become productive
   - Senior architects repeat the same explanations

3. **Gap Between Advice and Action**
   - AI tools suggest commands — someone still has to copy-paste and run them
   - Every failed `az` command means back to Google

**Speaker notes**: "Every architect and engineer on Azure deals with these. Nexus closes all three gaps at once."

---

### Slide 3 — What Nexus Does (The Big Picture)

**Title**: Nexus = Your Team's Collective Intelligence, Automated

**Central diagram** (describe for Claude to render as a flow):

```
[User Chat] → [Skills / Persona] → [Azure OpenAI]
                                         ↓
                                   [Tool Orchestrator]
                              ↙         ↓          ↘
                    [Knowledge Base]  [Azure APIs]  [Web Search]
                                         ↓
                              [Approval Gate] (for dangerous ops)
                                         ↓
                                    [Execute]
                                         ↓
                              [Learn from Mistakes]
```

**Callout box**: "Nexus executes. It doesn't just suggest."

**Speaker notes**: "The magic is in the loop: LLM reasons → picks tools → optionally asks for approval → executes → feeds results back → repeats up to 15 times per query."

---

### Slide 4 — The Skills System: One Platform, Many Personas

**Title**: Switch Roles. Keep Your Knowledge Base.

**Concept**: A "skill" is a YAML-defined persona that controls the AI's behavior and which tools it can access. Think of it as giving the same AI different job roles.

**Grid of current skills** (icon + name + one-line description):

| Skill | What It Does |
|-------|-------------|
| **Chat with KB** | General-purpose assistant with full tool access — the Swiss Army knife |
| **Architect** | Senior cloud architect mode: ADR-style outputs, trade-off analysis, WAF alignment |
| **KB Searcher** | Read-only knowledge retrieval — no execution, just search and cite |
| **DrawIO Diagrammer** | Generates valid Azure architecture diagrams as `.drawio` files |
| **Deploy Backend** | Guided Azure Container Apps backend deployment workflow |
| **Deploy Frontend** | Guided Azure Static Web Apps / CDN frontend deployment workflow |
| **Azure Principal Architect** | Deep architectural review with live Azure state queries |

**Callout**: "You can create your own skills. Your team's custom persona, with your rules, in minutes."

**Speaker notes**: "Each skill is a markdown file with YAML frontmatter. Any engineer can write a new one and push it to the KB repo. It shows up in the UI immediately."

---

### Slide 5 — Skill Deep Dive: Chat with KB

**Title**: The Default Skill — A Fully-Armed Azure AI Assistant

**Left side — Skill philosophy**:
> "Execute, don't just suggest. When you have the tools to get real data or run a real command, do it. Don't ask the user to run it for you."

**Right side — Tool access list** (organized by category):

- **Knowledge**: `read_kb_file`, `search_kb`, `search_kb_semantic`
- **Web Search**: `fetch_ms_docs`, `search_stack_overflow`, `search_github`, `search_azure_updates`, `web_search`, `web_fetch`
- **Azure Data**: `az_resource_graph`, `az_cost_query`, `az_monitor_logs`, `az_rest_api`, `az_devops`, `az_policy_check`, `az_advisor`
- **Execution** (approval-gated): `az_cli`, `run_shell`
- **Diagramming**: `generate_file`, `validate_drawio`, `diagram_gen`
- **Learning**: `read_learnings`, `update_learnings`

**Total: 27 tools available in one skill**

**Speaker notes**: "The skill instructs the AI on when to use each tool and in what order. The tool selection guide inside the skill prompt says things like 'use az_resource_graph before az_cli — it's faster and read-only.'"

---

### Slide 6 — The Knowledge Base: Your Team's Memory

**Title**: One Knowledge Base. Every Answer Has a Source.

**What goes in the KB**:
- Architecture Decision Records (ADRs)
- Platform documentation
- Runbooks and incident response guides
- Design patterns (circuit breaker, retry, CQRS…)
- DrawIO diagramming references (Azure and AWS icon sets)
- Any markdown file your team writes

**How it works**:
1. KB lives in a Git repo (Azure DevOps or GitHub)
2. Nexus syncs it on startup and on schedule
3. KB is indexed into a searchable JSON index
4. Every skill can search the KB and cite exact file paths

**Callout box**:
> "When Nexus answers an architecture question, it tells you *which ADR* or *which runbook* it used."

**Visual**: Screenshot mock of a KB search result with `[Source: kb/adrs/adr-001-multi-region.md]` citation

**Speaker notes**: "The KB is what makes Nexus *yours*. ChatGPT knows Azure docs. Nexus knows your team's decisions about Azure."

---

### Slide 7 — The Tools Engine: Execution, Not Advice

**Title**: 24 Tools Implemented. 67 Planned.

**Subtitle**: Nexus runs commands. You approve the dangerous ones.

**Two-column layout**:

**Column 1 — Implemented Today (24 tools)**:

| Category | Tools |
|----------|-------|
| KB Access | read_kb_file, search_kb, search_kb_semantic |
| Web Search | fetch_ms_docs, search_github, search_stackoverflow, search_azure_updates, web_search, web_fetch |
| Azure Queries | az_resource_graph, az_cost_query, az_monitor_logs, az_rest_api, az_policy_check, az_advisor, az_devops |
| Execution | az_cli ⚠️, run_shell ⚠️ |
| Networking | network_test |
| Diagrams | generate_file, validate_drawio, diagram_gen |
| Learning | read_learnings, update_learnings |

⚠️ = Approval required before execution

**Column 2 — Planned (43 more tools)**:

- **Azure Architect**: pricing lookup, service health, quota check, WAF review, landing zone compliance, Bicep what-if & lint, TCO estimate
- **Azure Engineer**: kubectl, Key Vault ops, storage management, ACR, App Service, deployment tracking, activity log, Defender, RBAC, DNS, TLS, managed identity
- **Security Architect**: Entra privileged roles, risky users, Conditional Access audit, PIM, Defender attack paths, Sentinel incidents, Firewall/WAF/NSG audits, Key Vault secret audit

**Speaker notes**: "Every tool is a Python class that implements a standard interface. Adding a new tool is about 50 lines of code and a YAML entry in the skill definition."

---

### Slide 8 — Approval Gates: Power With Safety

**Title**: Run Commands With Confidence

**The approval flow** (visual state machine):

```
Agent decides to run az_cli / run_shell
         ↓
[approval_required event] → streamed to UI
         ↓
User sees command + reasoning in chat
         ↓
   [Approve] or [Deny]
         ↓
Agent continues or adapts
```

**What the user sees in the approval card**:
- The exact command to be run
- Why the agent chose this command
- One-click Approve / Deny

**Key point**: Read-only operations (Resource Graph KQL, cost queries, monitor logs) **never need approval** — they run instantly.

**Speaker notes**: "Nexus isn't a runaway script. It's an AI with judgment and a safety net. Architects define which tools require approval in the skill definition."

---

### Slide 9 — The Learning System: It Gets Smarter

**Title**: Nexus Remembers Its Mistakes

**How it works**:

1. A tool call fails (e.g., wrong `az` syntax for the team's subscription)
2. Nexus tries **3 retry strategies** before giving up:
   - Strategy 1: Look up Microsoft docs, fix syntax, retry
   - Strategy 2: Try a completely different command or tool
   - Strategy 3: Try the simplest possible form
3. If all 3 fail → agent calls `update_learnings` and writes the failure + fix to `learn.md`
4. `learn.md` is injected into every future system prompt — the agent already knows the issue next time

**Visual**: Show a snippet of `learn.md` format:
```markdown
## 2026-04-15 — az group list timeout

**Category**: known-issue
**Symptom**: az group list times out in large subscriptions
**Fix**: Use az group list --subscription <id> with --query to limit output
**Workaround**: Use az_resource_graph instead — it's faster
```

**Speaker notes**: "This is institutional memory that accumulates over time. Every team member's Nexus session contributes to a shared learning file. The AI gets smarter for everyone."

---

### Slide 10 — Real-World Demo: Architect Mode in Action

**Title**: Ask. Execute. Diagram. Done.

**Narrative walkthrough** (simulate a real conversation):

> **Engineer**: "Show me all VMs in our subscription that don't have tags and are running, and generate an architecture diagram of the ones in the East US region."

**What Nexus does** (step by step, with tool callouts):

1. `search_kb` → finds team tagging ADR for context
2. `az_resource_graph` → KQL query for untagged running VMs (no approval needed)
3. Filters East US results from the response
4. `diagram_gen` → generates a `.drawio` file with correct Azure VM icons, proper layout
5. `validate_drawio` → confirms the file is valid XML
6. `generate_file` → saves the file and returns the path
7. Streams the full answer with citations, table of results, and diagram file

**Total time**: ~15 seconds. Zero copy-paste.

**Speaker notes**: "This is the compound power of Nexus: it combined KB context, live Azure data, and diagram generation in a single natural language request."

---

### Slide 11 — Custom Skills: Build Your Own Persona

**Title**: Your Team's Rules. Your AI's Behavior.

**How to create a skill** (show the SKILL.md format):

```yaml
---
display_name: Security Reviewer
description: Reviews infrastructure changes for security gaps and policy violations
tools:
  - search_kb
  - az_resource_graph
  - az_policy_check
  - az_advisor
  - fetch_ms_docs
  - read_learnings
---

You are a senior cloud security architect. When reviewing infrastructure:

1. Always check the team's security ADRs in the KB first
2. Run az_policy_check before recommending any deployment
3. Use az_advisor to surface active security recommendations
4. Cite WAF Security pillar principles in every response
5. Flag any resource missing diagnostic settings as a HIGH finding
```

**Steps**:
1. Write the SKILL.md file
2. Push to the KB Git repo
3. Nexus auto-syncs — skill appears in the UI immediately
4. No code changes, no deploys, no tickets

**Callout**: "A new skill takes 15 minutes to write. It immediately becomes available to every team member."

**Speaker notes**: "Personal skills are also supported — engineers can create private skills stored in the database, visible only to them."

---

### Slide 12 — Adding New Tools: Extensibility by Design

**Title**: 50 Lines to a New Tool

**The tool interface** (show simplified Python):

```python
class MyNewTool(BaseTool):
    name = "my_new_tool"
    description = "What this tool does — the LLM reads this"
    requires_approval = False

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The query to run"}
            },
            "required": ["query"]
        }

    async def execute(self, query: str) -> str:
        result = await call_my_api(query)
        return json.dumps(result)
```

**Then**:
1. Register it in `TOOL_REGISTRY` in `base.py` — one line
2. Add `my_new_tool` to any skill's tool list in the SKILL.md
3. The LLM now knows about the tool and will use it based on your description

**Planned tool pipeline (43 tools)**:
- Azure Bicep linting and what-if
- Kubernetes `kubectl` execution
- Sentinel incident management
- Defender for Cloud assessment
- Azure Pricing API
- Service Health alerts
- Landing Zone compliance checks

**Speaker notes**: "Tools are Python classes. If you can call an API in Python, you can add a tool. The LLM learns what the tool does from the description field alone."

---

### Slide 13 — Architecture: Self-Hosted, Secure, Scalable

**Title**: Your Data. Your Infrastructure. Your Control.

**Deployment architecture** (describe the diagram):

```
[Browser] → [Azure Static Web Apps / CDN]
                      ↓
              [Azure Container Apps]
                  ↙          ↘
         [FastAPI Backend]   [SQLite on Azure Files]
                  ↓
          [Azure OpenAI GPT]
                  ↓
            [KB Git Repo]
         (Azure DevOps / GitHub)
```

**Security posture**:
- Microsoft Entra ID (MSAL) authentication — every request validated
- Approval gating for all shell/CLI execution
- Path traversal protection on all KB file reads
- Dev auth bypass only available via environment variable — disabled in production
- All data stays in your Azure subscription — no third-party SaaS

**Scale**: 
- 5 concurrent users on a single Container Apps instance
- Auto-scale 1–3 replicas
- SQLite with Azure Files mount (upgrade path to PostgreSQL for larger teams)

**Speaker notes**: "Nothing leaves your Azure tenant. OpenAI calls go to your Azure OpenAI resource — not to openai.com."

---

### Slide 14 — For Architects: Specific Use Cases

**Title**: How Architects Use Nexus Every Day

**Six use case cards**:

1. **ADR Consultation**
   "What did we decide about multi-region deployments? What are the trade-offs if we revisit?"
   → Nexus reads your ADR, summarizes the decision, and queries current Azure state to see if implementation matches the decision.

2. **Live Architecture Review**
   "Review our current production resource group against the WAF reliability pillar."
   → Runs `az_resource_graph` queries, checks against KB patterns, outputs findings with citations.

3. **Diagram Generation**
   "Generate a DrawIO architecture diagram for our App Service + SQL + Key Vault setup in East US."
   → Produces a valid `.drawio` file with correct Azure icons, labeled connectors, proper grouping.

4. **Pattern Guidance**
   "Should we use circuit breaker or retry with exponential backoff for this service?"
   → Searches KB for existing patterns, cites team ADRs, references Microsoft docs, gives a recommendation with trade-offs.

5. **Cost Analysis**
   "Which resource groups are over budget this month? What's driving the overrun?"
   → `az_cost_query` + analysis + recommendation without leaving the chat.

6. **Policy Compliance**
   "Are any of our deployments violating our internal tagging or location policies?"
   → `az_policy_check` + `az_resource_graph` + KB policy docs → compliance report.

---

### Slide 15 — For Engineers: Specific Use Cases

**Title**: How Engineers Use Nexus Every Day

**Six use case cards**:

1. **Runbook Execution**
   "Walk me through the incident response runbook for a database failover."
   → Reads the runbook from KB step by step, executes diagnostic commands along the way.

2. **Debugging Live Issues**
   "Our app service in prod is returning 502s. What's wrong?"
   → `az_monitor_logs` for recent errors + `az_resource_graph` for health status + root cause hypothesis.

3. **Stack Overflow + Docs Search**
   "Why does my ARM template deployment fail with InvalidTemplateDeployment?"
   → `search_stackoverflow` + `fetch_ms_docs` → synthesized answer with working fix.

4. **Bicep/ARM Assistance**
   "Generate a Bicep template for a Container App with managed identity and Key Vault reference."
   → Generates file, `validate_drawio` equivalent for Bicep (planned), cites docs.

5. **Resource Discovery**
   "Find all App Services in our subscription that are still on .NET 6 (EOL)."
   → KQL query via `az_resource_graph` → tabulated results → remediation steps.

6. **Learning Institutional Knowledge**
   "How do we deploy to the staging environment? What's our branching strategy?"
   → Searches KB for deploy runbooks, pipeline docs, branching conventions.

---

### Slide 16 — The Roadmap: Where Nexus Is Going

**Title**: 24 Tools Today. 67 on the Roadmap.

**Three-column roadmap**:

**Azure Architect Tools (Next)**:
- Azure Pricing API integration
- Service Health & outage detection
- Quota check across subscriptions
- WAF automated review (per pillar)
- Landing Zone compliance checker
- Bicep what-if execution
- Bicep lint & best-practice check
- Service comparison (App Service vs Container Apps vs AKS)
- TCO estimation

**Azure Engineer Tools (Soon)**:
- `kubectl` execution for AKS clusters
- Key Vault secrets management
- Storage account operations
- Container Registry management
- App Service deployment slot management
- Defender for Cloud findings
- RBAC assignment auditing
- DNS and TLS certificate checks
- Managed Identity operations

**Security Architect Tools (Future)**:
- Entra privileged role audit
- Risky user detection (via Graph API)
- Conditional Access policy gaps
- PIM activation history
- Sentinel incident management
- Defender attack path analysis
- JIT access management
- Firewall and WAF rule auditing
- Key Vault and secret expiry audit

**Footer**: "Every tool follows the same 50-line pattern. Community contributions welcome."

---

### Slide 17 — Getting Started: Up in 30 Minutes

**Title**: From Zero to Running in 30 Minutes

**Prerequisites**:
- Python 3.11+, Node.js 20+
- Azure subscription with Azure OpenAI resource (`gpt-4o-mini` or equivalent)
- Git repo for your knowledge base

**Steps**:

```bash
# 1. Clone and configure
git clone <nexus-repo>
cp backend/.env.example backend/.env
# Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, KB_REPO_URL

# 2. Start backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8002

# 3. Start frontend
cd frontend
npm install && npm run dev
# Open http://localhost:5174
```

**For development** (no Entra ID required):
- Set `DEV_AUTH_BYPASS=true` in both `.env` files
- Uses a fake "dev-user" identity
- Full functionality available immediately

**First things to do**:
1. Add your team's markdown docs to `kb_data/kb/`
2. Write your first custom skill in `kb_data/skills/shared/`
3. Ask it about your infrastructure

---

### Slide 18 — Closing: The Vision

**Title**: Nexus Is Your Team's Institutional Intelligence

**Three big ideas**:

1. **Your knowledge, amplified**
   The KB is what separates Nexus from generic AI. When you document decisions, runbooks, and patterns, Nexus becomes a force multiplier for every team member.

2. **Your rules, enforced**
   Skills encode how your team works — naming conventions, approval policies, architectural standards. New engineers inherit senior judgment automatically.

3. **Your tools, growing**
   Every new tool makes every skill smarter. The 24 tools today are the foundation. The 43 planned tools are the roadmap. Your team's custom tools are the ceiling.

**Closing statement** (large text, centered):
> "Stop copying commands from AI into your terminal.  
> Start working with an AI that runs them for you."

**Call to action**: Deploy Nexus · Write a skill · Add a tool

---

## Design Instructions for Claude Desktop

- **Color palette**: `#0a0f1e` (background), `#1a2744` (card/panel), `#00b4d8` (accent blue), `#0077b6` (secondary blue), `#48cae4` (highlight), white text
- **Font**: A clean sans-serif — Inter or Segoe UI. Code blocks in JetBrains Mono or Consolas
- **Iconography**: Use Azure-style icons where possible. For tools, use simple geometric icons
- **Slide transitions**: Subtle fade. No spinning or bouncing animations
- **Code blocks**: Dark background (`#0d1117`), syntax-highlighted, monospaced
- **Diagrams**: Clean flow diagrams with straight or right-angle connectors, no curves
- **Layout**: Prefer two-column layouts for comparison slides. Single-column for narrative slides
- **Logo**: Place "NEXUS" wordmark in the top-left corner of every slide in `#00b4d8` color

---

*Generated from Nexus codebase on 2026-05-05 for use as a Claude Desktop PowerPoint prompt.*
