# Nexus — Team Architect Assistant

## What This Is

Nexus is a self-hosted AI assistant for Azure cloud teams. It combines Azure OpenAI (GPT) with a team knowledge base (KB) synced from Git, a skills system (switchable personas), and approval-gated tool execution (az CLI, PowerShell, Resource Graph queries). It runs commands proactively instead of just suggesting them, learns from mistakes via a persistent `learn.md`, and retries failed commands using 3 different strategies before giving up.

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.11, FastAPI, SQLModel, OpenAI SDK, GitPython, httpx |
| Frontend | React 19, TypeScript 6, Vite 8, Tailwind CSS v4, zustand, @tanstack/react-query |
| Database | SQLite (via SQLModel/SQLAlchemy) |
| AI | Azure OpenAI (`gpt-5.4-mini`), streaming via SSE |
| Auth | Microsoft Entra ID (MSAL) — bypassed in dev via `DEV_AUTH_BYPASS=true` |
| Testing | Backend: pytest (115 tests) · Frontend: vitest (109 tests) |

## How To Run

```bash
# Backend (from backend/ directory)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# Frontend (from frontend/ directory)
cd frontend
npm install
npm run dev
```

- Backend `.env` is at `backend/.env` — has Azure OpenAI keys, DB path, tool toggles
- Frontend `.env` is at `frontend/.env` — has `VITE_API_BASE_URL` pointing to backend
- `DEV_AUTH_BYPASS=true` skips Entra auth in dev (uses fake "dev-user" identity)

## How To Test

```bash
# Backend
cd backend && python -m pytest tests/ -x -q

# Frontend  
cd frontend && npm test
```

## Project Structure

```
Nexus/
├── claude.md                      # This file
├── Nexus_PRD.md                   # Product requirements document
├── README.md                      # User-facing readme
├── Testing.md                     # Test documentation
├── ManualFindings.txt             # Manual testing findings & feature requests
│
├── backend/
│   ├── .env                       # Backend config (Azure OpenAI keys, DB, tool flags)
│   ├── requirements.txt           # Python dependencies
│   ├── Dockerfile                 # Container build
│   ├── app.db                     # SQLite database (auto-created)
│   │
│   ├── app/
│   │   ├── main.py                # FastAPI app, lifespan, startup/shutdown
│   │   ├── config.py              # Pydantic Settings (all env vars)
│   │   ├── deps.py                # Dependency injection (get_session, get_current_user)
│   │   │
│   │   ├── api/                   # FastAPI route handlers
│   │   │   ├── health.py          # GET /healthz, GET /metrics
│   │   │   ├── chat.py            # POST /api/chat (SSE streaming)
│   │   │   ├── conversations.py   # CRUD for conversations + messages
│   │   │   └── skills.py          # GET /api/skills, GET /api/tools, personal skills CRUD
│   │   │
│   │   ├── agent/                 # AI agent core
│   │   │   ├── orchestrator.py    # Main agent loop: LLM → tool calls → retry → stream
│   │   │   ├── approvals.py       # Approval gating for dangerous tools
│   │   │   └── streaming.py       # SSE event formatters
│   │   │
│   │   ├── auth/                  # Authentication
│   │   │   ├── entra.py           # Entra ID JWT validation
│   │   │   └── models.py          # User dataclass
│   │   │
│   │   ├── db/                    # Database
│   │   │   ├── engine.py          # SQLModel engine + session factory
│   │   │   ├── models.py          # Tables: users, conversations, messages, pending_approvals, personal_skills
│   │   │   └── migrations/        # Alembic migrations
│   │   │
│   │   ├── kb/                    # Knowledge Base
│   │   │   ├── git_sync.py        # Git clone/pull from Azure DevOps/GitHub
│   │   │   ├── indexer.py         # Builds searchable index from KB markdown files
│   │   │   └── service.py         # Read/search KB content
│   │   │
│   │   ├── skills/                # Skills system (switchable AI personas)
│   │   │   ├── models.py          # Skill dataclass
│   │   │   ├── shared.py          # Loads shared skills from kb_data/skills/shared/
│   │   │   ├── personal.py        # User-created personal skills (DB-backed)
│   │   │   └── loader.py          # Parses SKILL.md frontmatter + body
│   │   │
│   │   └── tools/                 # Tool implementations (called by the LLM)
│   │       ├── base.py            # Tool ABC, TOOL_REGISTRY, init_tools(), resolve_tools()
│   │       ├── az_cli.py          # az_cli — runs Azure CLI commands (requires approval)
│   │       ├── az_resource_graph.py # az_resource_graph — KQL queries (read-only, no approval)
│   │       ├── shell.py           # run_shell — runs shell/PowerShell commands (requires approval)
│   │       ├── ms_docs.py         # fetch_ms_docs — searches Microsoft Learn docs
│   │       ├── kb_tools.py        # read_kb_file, search_kb — KB access tools
│   │       └── learn_tool.py      # read_learnings, update_learnings — persistent mistake memory
│   │
│   └── tests/                     # 115 pytest tests
│       ├── conftest.py            # Fixtures (test DB, async client, auth bypass)
│       ├── test_api.py            # API endpoint tests
│       ├── test_agent.py          # Orchestrator unit tests
│       ├── test_tools.py          # Tool registry + execution tests
│       ├── test_auth.py           # Auth middleware tests
│       ├── test_openai.py         # Real Azure OpenAI connectivity tests
│       └── ...                    # test_config, test_db_models, test_kb, test_skills, etc.
│
├── frontend/
│   ├── .env                       # VITE_API_BASE_URL, VITE_DEV_AUTH_BYPASS
│   ├── package.json               # React 19, Vite 8, Tailwind v4, zustand, react-query
│   ├── vite.config.ts
│   │
│   └── src/
│       ├── main.tsx               # App entry point
│       ├── App.tsx                 # Router: ChatPage, SkillsPage
│       ├── types.ts               # Shared TypeScript interfaces
│       │
│       ├── api/                   # Backend API clients
│       │   ├── client.ts          # apiFetch — base fetch wrapper with auth headers
│       │   ├── chat.ts            # SSE stream parser for POST /api/chat
│       │   ├── conversations.ts   # Conversation CRUD
│       │   └── skills.ts          # Skills + tools fetch
│       │
│       ├── auth/                  # MSAL auth
│       │   ├── AuthProvider.tsx   # Entra login provider (bypassed in dev)
│       │   └── msalConfig.ts     # MSAL configuration
│       │
│       ├── components/            # React components
│       │   ├── ChatWindow.tsx     # Main chat UI with SSE streaming
│       │   ├── MessageBubble.tsx  # User/assistant/tool message rendering
│       │   ├── ApprovalCard.tsx   # Approve/deny tool execution prompts
│       │   ├── ConversationList.tsx # Sidebar conversation list
│       │   ├── SkillPicker.tsx    # Skill selector dropdown
│       │   └── SkillEditor.tsx    # Create/edit personal skills
│       │
│       ├── pages/
│       │   ├── ChatPage.tsx       # Main chat page layout
│       │   └── SkillsPage.tsx     # Skills management page
│       │
│       ├── store/
│       │   └── useAppStore.ts     # Zustand global state
│       │
│       └── test/                  # 109 vitest tests
│           ├── setup.ts           # Test setup (jsdom, mocks)
│           └── *.test.ts(x)       # Component + API tests
│
└── kb_data/                       # Knowledge base content (inside backend/)
    ├── kb_index.json              # Auto-generated searchable index
    ├── kb/                        # Team documentation (markdown files)
    ├── learnings/
    │   └── learn.md               # Agent's persistent mistake memory (auto-updated)
    └── skills/
        └── shared/                # Shared skill definitions
            ├── kb-searcher/SKILL.md       # "Default" tier — read-only KB + Azure read queries
            ├── chat-with-kb/SKILL.md      # "Azure Engineer" tier — full execute, no inline diagrams
            ├── architect/SKILL.md         # "Azure Architect" tier — ADR + WAF framing, inline drawio-from-python flow
            └── drawio-diagrammer/SKILL.md # Hand-written .drawio XML + per-cell patch specialist
```

## Architecture & Key Concepts

### Agent Loop (`orchestrator.py`)
1. User sends message → saved to DB
2. System prompt = skill prompt + KB index + learnings + retry policy
3. Calls Azure OpenAI with streaming + tool definitions
4. If model returns tool calls → execute them (with approval if needed)
5. If tool fails → **multi-strategy retry** (3 attempts):
   - Strategy 1: Look up Microsoft docs, fix syntax, retry
   - Strategy 2: Try a completely different command/tool/approach  
   - Strategy 3: Simplest possible form, or record learning and give up
6. Tool results fed back to model → loop continues (max 15 iterations)
7. If all retries fail → agent calls `update_learnings` to record the mistake

### Skills
Each skill is a `SKILL.md` file with YAML frontmatter:
```yaml
---
display_name: Chat with KB
description: General-purpose assistant
tools:
  - read_kb_file
  - search_kb
  - az_cli
  - run_shell
  - az_resource_graph
  - fetch_ms_docs
  - read_learnings
  - update_learnings
---
System prompt content goes here...
```
Skills control which tools are available and how the AI behaves.

### Tools (8 registered)
| Tool | Approval | Purpose |
|------|----------|---------|
| `read_kb_file` | No | Read a KB file by path |
| `search_kb` | No | Keyword search the KB index |
| `fetch_ms_docs` | No | Search Microsoft Learn docs |
| `az_resource_graph` | No | Read-only KQL queries against Azure Resource Graph |
| `read_learnings` | No | Read the agent's learn.md (known issues/fixes) |
| `update_learnings` | No | Append a new learning entry to learn.md |
| `az_cli` | **Yes** | Run Azure CLI commands |
| `run_shell` | **Yes** | Run shell/PowerShell commands |

### Learnings System (`learn.md`)
The agent maintains `kb_data/learnings/learn.md` — a persistent file of categorized mistakes and fixes. This is:
- Auto-injected into the system prompt so the agent sees known issues before executing
- Updated by the agent when it discovers new issues after failed retries
- Categories: `known-issue`, `syntax-fix`, `workaround`, `best-practice`, `gotcha`

### SSE Streaming Events
The `POST /api/chat` endpoint streams these events:
- `message_saved` — message persisted
- `token` — streaming text chunk
- `tool_call_start` — tool invocation starting
- `tool_result` — tool execution result
- `approval_required` — waiting for user to approve/deny
- `error` — error occurred
- `done` — conversation turn complete

### Database Tables
- `users` — Entra-authenticated users (oid, email, display_name)
- `conversations` — Chat sessions with skill snapshot
- `messages` — All messages (user, assistant, tool) with tool_calls_json
- `pending_approvals` — Tool approval queue (pending/approved/denied)
- `personal_skills` — User-created custom skills

## Important Patterns

- **Windows az CLI fix**: `az` is `az.CMD` on Windows. Tools use `shutil.which("az")` + `shell=True` on win32
- **Port management**: Backend runs on **8000**, frontend on **5174**. If a port is busy: `netstat -ano | findstr :8000`
- **Auth bypass**: Set `DEV_AUTH_BYPASS=true` in both `.env` files for local dev
- **KB path**: Backend must run from `backend/` directory so `KB_REPO_LOCAL_PATH=./kb_data` resolves correctly
- **Conversation skill snapshot**: When a conversation starts, the skill's full config is snapshot into the conversation record so changing the skill later doesn't affect existing conversations
- **Tool schemas**: Tools expose `to_openai_schema()` which converts to OpenAI function-calling format
- **Frontend envelope**: The skills API returns an array directly, but `fetchSkills()` in the frontend correctly handles both array and `{value: [...]}` envelope formats
