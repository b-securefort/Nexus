# Team Architect Assistant — v1 Specification (Nexus)

**Version:** 1.0
**Target audience:** AI coding assistants (Claude Code, ChatGPT) and human developers implementing this system.
**Implementation mode:** Local-first for development, Azure-deployed for production.

---

## 1. Purpose

Build a self-hosted, team-oriented AI assistant that:

- Serves a small team (5 concurrent users) via a web interface.
- Uses **Azure OpenAI** as the LLM backend.
- Reads from a **shared Markdown knowledge base** (architectural decision records, patterns, runbooks, code snippets, platform context) stored in an **Azure DevOps Git repository**.
- Lets users chat with the KB through configurable **skills** (system prompt + tool allowlist), analogous to Claude Code skills.
- Supports **shared skills** (Git-backed, curated by admin) and **personal skills per user** (DB-backed, private, edited via in-app UI).
- Exposes **tools** the LLM can call, including CLI wrappers (Azure CLI, MS Docs search) and a shell runner.
- Gates any script/shell execution behind **explicit user approval** in the UI.
- Authenticates via **Microsoft Entra ID** (Azure AD).

This document is the single source of truth for v1. Implement only what is specified here. Out-of-scope items are listed explicitly in §17.

---

## 2. High-level architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite + TypeScript)                        │
│  - Chat UI                                                   │
│  - Skill picker (shared + personal)                          │
│  - Personal skill editor                                     │
│  - Approval cards for pending tool calls                     │
│  - Auth via MSAL (Entra ID)                                  │
└───────────────────────┬──────────────────────────────────────┘
                        │ REST + SSE
┌───────────────────────▼──────────────────────────────────────┐
│  Backend (FastAPI, Python 3.11+)                             │
│  - Auth middleware (validates Entra ID tokens)               │
│  - Chat orchestrator (agent loop with tool calling)          │
│  - Skill loader (shared from Git, personal from SQLite)      │
│  - KB service (Git-backed, indexed)                          │
│  - Tool registry + executor (with approval gating)           │
│  - SQLite persistence (users, conversations, messages,       │
│    personal skills, pending approvals)                       │
└──┬─────────────────┬─────────────────┬───────────────────────┘
   │                 │                 │
   ▼                 ▼                 ▼
 Azure OpenAI    KB Git repo        Tools
 (chat API)      (cloned on disk,   (az CLI, shell,
                  pulled nightly)    MS Docs fetch,
                                     KB read/search)
```

**Storage:**
- **SQLite** — users, conversations, messages, personal skills, pending approvals. Single file on disk; in production, mounted on Azure Files for persistence.
- **Git working copy** — KB and shared skills cloned locally on backend startup, refreshed periodically.

**No MongoDB, no Redis, no vector DB, no Meilisearch.**

---

## 3. Technology stack (authoritative)

Implementers MUST use these choices unless a change is explicitly approved.

| Layer | Choice | Notes |
|---|---|---|
| Backend language | Python 3.11+ | |
| Backend framework | FastAPI | Async, good OpenAI SDK support, simple tooling |
| ASGI server | Uvicorn (dev), Gunicorn+Uvicorn workers (prod) | |
| LLM client | `openai` Python SDK, configured for Azure | Use `AzureOpenAI` client |
| DB | SQLite via `sqlite3` stdlib + `sqlmodel` for ORM | `sqlmodel` wraps SQLAlchemy + Pydantic |
| DB migrations | `alembic` | Required even for SQLite to make prod-safe schema changes |
| Git ops | `GitPython` | For cloning/pulling the KB repo |
| Auth (backend) | `msal` + custom JWT validation middleware | Validate Entra ID access tokens |
| Frontend language | TypeScript | |
| Frontend framework | React 18 + Vite | |
| Frontend UI lib | `shadcn/ui` (Radix + Tailwind) | |
| Frontend state | `zustand` for app state, `@tanstack/react-query` for server state | |
| Frontend auth | `@azure/msal-browser` + `@azure/msal-react` | |
| Streaming | Server-Sent Events (SSE) for chat responses | Simpler than WebSocket for unidirectional streaming |
| Dev runtime | Docker Compose (optional) or direct local run | |
| Prod runtime | Azure Container Apps | Single container, scale 1–3 |
| Prod persistence | Azure Files volume mounted into container | For SQLite DB file + Git working copy |
| CI/CD | Azure Pipelines | |

---

## 4. Repository layout

Two repositories are involved:

### 4.1 Application repository (`team-architect-app`)

```
team-architect-app/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app factory, router registration
│   │   ├── config.py                # Settings via pydantic-settings, env-driven
│   │   ├── deps.py                  # FastAPI dependencies (current_user, db session)
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── entra.py             # Entra ID token validation
│   │   │   └── models.py            # User dataclass (oid, email, name)
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py            # SQLModel engine, session factory
│   │   │   ├── models.py            # SQLModel table definitions
│   │   │   └── migrations/          # alembic
│   │   ├── kb/
│   │   │   ├── __init__.py
│   │   │   ├── git_sync.py          # Clone/pull repo
│   │   │   ├── indexer.py           # Build kb_index.json
│   │   │   └── service.py           # read_kb_file, search_kb
│   │   ├── skills/
│   │   │   ├── __init__.py
│   │   │   ├── models.py            # Skill dataclass
│   │   │   ├── loader.py            # load_skill, list_skills
│   │   │   ├── shared.py            # Read shared skills from Git working copy
│   │   │   └── personal.py          # CRUD on personal_skills table
│   │   ├── tools/
│   │   │   ├── __init__.py
│   │   │   ├── base.py              # Tool ABC, registry
│   │   │   ├── kb_tools.py          # read_kb_file, search_kb
│   │   │   ├── shell.py             # run_shell (approval-gated)
│   │   │   ├── az_cli.py            # az_cli (approval-gated)
│   │   │   └── ms_docs.py           # fetch_ms_docs
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py      # Main agent loop
│   │   │   ├── approvals.py         # Pending-approval state machine
│   │   │   └── streaming.py         # SSE emission helpers
│   │   └── api/
│   │       ├── __init__.py
│   │       ├── chat.py              # POST /chat, approvals endpoints
│   │       ├── skills.py            # Skill list + personal skill CRUD
│   │       ├── conversations.py     # List, fetch, delete conversations
│   │       └── health.py            # /healthz
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_auth.py
│   │   ├── test_kb.py
│   │   ├── test_skills.py
│   │   ├── test_agent.py
│   │   └── test_api.py
│   ├── alembic.ini
│   ├── pyproject.toml
│   ├── requirements.txt             # Generated from pyproject
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── auth/
│   │   │   ├── msalConfig.ts
│   │   │   └── AuthProvider.tsx
│   │   ├── api/
│   │   │   ├── client.ts            # Fetch wrapper with token attachment
│   │   │   ├── chat.ts
│   │   │   ├── skills.ts
│   │   │   └── conversations.ts
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── ApprovalCard.tsx
│   │   │   ├── SkillPicker.tsx
│   │   │   ├── SkillEditor.tsx
│   │   │   ├── ConversationList.tsx
│   │   │   └── ui/                  # shadcn components
│   │   ├── pages/
│   │   │   ├── ChatPage.tsx
│   │   │   └── SkillsPage.tsx
│   │   ├── store/
│   │   │   └── useAppStore.ts       # zustand
│   │   └── types.ts
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml               # Local dev convenience
├── .env.example
├── .gitignore
├── azure-pipelines.yml
└── README.md
```

### 4.2 Knowledge base repository (`team-kb`, separate Azure DevOps repo)

```
team-kb/
├── kb/
│   ├── adrs/
│   │   └── *.md
│   ├── patterns/
│   │   └── *.md
│   ├── runbooks/
│   │   └── *.md
│   ├── snippets/
│   │   └── *.md
│   └── platform/
│       └── *.md
├── skills/
│   └── shared/
│       ├── architect/
│       │   └── SKILL.md
│       ├── architect-mentor/
│       │   └── SKILL.md
│       └── kb-searcher/
│           └── SKILL.md
├── kb_index.json                    # Auto-generated, committed by CI or tooling
└── README.md
```

---

## 5. Configuration

All backend config via environment variables, loaded by `pydantic-settings`. No hardcoded secrets.

### 5.1 Required env vars

```
# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key>                    # or use managed identity
AZURE_OPENAI_DEPLOYMENT=gpt-4o                # deployment name, not model name
AZURE_OPENAI_API_VERSION=2024-10-21

# Entra ID (backend token validation)
ENTRA_TENANT_ID=<tenant-guid>
ENTRA_API_CLIENT_ID=<backend-app-registration-client-id>
ENTRA_API_AUDIENCE=api://<backend-app-registration-client-id>

# KB Git repo
KB_REPO_URL=https://dev.azure.com/<org>/<project>/_git/team-kb
KB_REPO_BRANCH=main
KB_REPO_LOCAL_PATH=/var/app/kb               # local mount, persistent
KB_REPO_AUTH_METHOD=pat                      # pat | managed_identity
KB_REPO_PAT=<personal-access-token>          # if method=pat
KB_SYNC_INTERVAL_SECONDS=900                 # pull every 15 min

# DB
DATABASE_URL=sqlite:////var/app/data/app.db  # 4 slashes for absolute path

# App
APP_ENV=dev                                  # dev | prod
APP_LOG_LEVEL=INFO
APP_CORS_ORIGINS=http://localhost:5173       # comma-separated list

# Tool config
TOOL_SHELL_ENABLED=true
TOOL_AZ_CLI_ENABLED=true
TOOL_MS_DOCS_ENABLED=true
TOOL_APPROVAL_TIMEOUT_SECONDS=600            # approvals expire after 10 min

# Backup (prod only)
BACKUP_ENABLED=false                         # true in prod
BACKUP_AZURE_STORAGE_CONNECTION_STRING=
BACKUP_CONTAINER_NAME=sqlite-backups
BACKUP_INTERVAL_SECONDS=86400                # daily
```

### 5.2 Frontend env vars (Vite, prefixed `VITE_`)

```
VITE_API_BASE_URL=http://localhost:8000
VITE_ENTRA_TENANT_ID=<tenant-guid>
VITE_ENTRA_CLIENT_ID=<frontend-app-registration-client-id>
VITE_ENTRA_API_SCOPE=api://<backend-app-registration-client-id>/user_impersonation
```

### 5.3 `.env.example`

Include a complete `.env.example` in the repo with placeholder values and comments.

---

## 6. Authentication

### 6.1 App registrations

Two Entra ID app registrations are required. **These are created manually in Azure, not by the app.** Document the setup in README.

1. **Backend API** (`team-architect-api`):
   - Expose an API with scope `user_impersonation`.
   - Application ID URI: `api://<client-id>`.
   - No platform (it's an API).

2. **Frontend SPA** (`team-architect-web`):
   - Platform: Single-page application.
   - Redirect URI: `http://localhost:5173` (dev) and `https://<prod-host>` (prod).
   - API permissions: delegated permission to backend API's `user_impersonation`.
   - Grant admin consent.

### 6.2 Backend token validation

- On every authenticated request, extract `Authorization: Bearer <token>` header.
- Fetch Entra ID OIDC metadata from `https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration` and cache JWKS.
- Validate the access token:
  - Signature via JWKS.
  - `iss` matches `https://login.microsoftonline.com/{tenant_id}/v2.0` or `https://sts.windows.net/{tenant_id}/`.
  - `aud` matches `ENTRA_API_AUDIENCE`.
  - `exp` is in the future.
  - `tid` matches `ENTRA_TENANT_ID`.
- Extract `oid` (user Object ID), `preferred_username` or `upn` (email), `name` (display name).
- Construct a `User` object and attach to the request via FastAPI dependency `current_user`.

### 6.3 Frontend auth flow

- Use `@azure/msal-react` with `MsalProvider` at the app root.
- On load, if unauthenticated, redirect to `loginRedirect` with scope `api://<backend>/user_impersonation`.
- On every API call, acquire token via `acquireTokenSilent` (fall back to `acquireTokenRedirect` on failure) and attach as `Authorization: Bearer`.
- Store nothing sensitive in localStorage beyond what MSAL itself stores.

### 6.4 User records in DB

On first successful auth, upsert a row in `users` table keyed by `oid`. Store `oid`, `email`, `display_name`, `created_at`, `last_seen_at`. Update `last_seen_at` on every request (throttle to once per minute to avoid write amplification).

---

## 7. Database schema

Use `sqlmodel` to define tables. Alembic migrations required.

### 7.1 Tables

```python
# users
id: int PK
oid: str UNIQUE NOT NULL        # Entra Object ID
email: str NOT NULL
display_name: str NOT NULL
created_at: datetime NOT NULL DEFAULT now
last_seen_at: datetime NOT NULL DEFAULT now

# conversations
id: int PK
user_oid: str NOT NULL INDEX    # FK-ish; not enforced since users keyed by oid
title: str NOT NULL             # auto-generated from first message
skill_id: str NOT NULL          # "shared:architect" or "personal:my-architect"
skill_snapshot_json: str NOT NULL  # JSON of skill at conversation start (prompt, tools, display_name)
                                   # ^ so conversation survives skill deletion/edits
created_at: datetime NOT NULL
updated_at: datetime NOT NULL
deleted_at: datetime NULL       # soft delete

# messages
id: int PK
conversation_id: int NOT NULL INDEX
role: str NOT NULL              # "user" | "assistant" | "tool"
content: str NOT NULL           # text content
tool_calls_json: str NULL       # JSON of tool calls made by assistant, if any
tool_call_id: str NULL          # for role="tool", links back to the call
tool_name: str NULL             # for role="tool"
created_at: datetime NOT NULL

# personal_skills
id: int PK
user_oid: str NOT NULL INDEX
name: str NOT NULL              # slug, e.g., "my-architect"
display_name: str NOT NULL
description: str NOT NULL DEFAULT ""
system_prompt: str NOT NULL
tools_json: str NOT NULL DEFAULT "[]"
created_at: datetime NOT NULL
updated_at: datetime NOT NULL
deleted_at: datetime NULL       # soft delete
UNIQUE(user_oid, name) WHERE deleted_at IS NULL

# pending_approvals
id: str PK                      # UUID
conversation_id: int NOT NULL INDEX
user_oid: str NOT NULL
tool_name: str NOT NULL
tool_args_json: str NOT NULL
reason: str NOT NULL            # LLM-provided reason
status: str NOT NULL            # "pending" | "approved" | "denied" | "expired"
created_at: datetime NOT NULL
resolved_at: datetime NULL
result_json: str NULL           # tool execution result after approval
```

### 7.2 Migration policy

- Initial schema = first Alembic migration.
- All subsequent changes via new migrations.
- Never edit an existing migration once merged.
- Run `alembic upgrade head` on every container start (safe because SQLite migrations are idempotent when written correctly).

---

## 8. Knowledge base service

### 8.1 Git sync

- On backend startup:
  - If `KB_REPO_LOCAL_PATH` does not exist or is not a git repo, `git clone` the `KB_REPO_URL` at branch `KB_REPO_BRANCH` using credentials from `KB_REPO_PAT`.
  - Otherwise, `git fetch` + `git reset --hard origin/<branch>` (we never write to the KB from the app, so discarding local changes is safe and prevents divergence).
- Background task runs every `KB_SYNC_INTERVAL_SECONDS` and does the same `fetch + reset`.
- Sync errors must be logged but must not crash the app. Serve stale content gracefully.

### 8.2 KB index

A file `kb_index.json` at the root of the KB repo, containing an array:

```json
[
  {
    "path": "kb/adrs/adr-001-multi-region.md",
    "title": "ADR 001: Multi-region active-active",
    "summary": "Decision to run active-active across two Azure regions...",
    "tags": ["azure", "multi-region", "adr"]
  },
  ...
]
```

**Generation:** A helper script `scripts/build_kb_index.py` in the KB repo (not the app repo) generates this by:
- Walking `kb/**/*.md`.
- For each file, reading frontmatter (YAML) if present for `title`, `summary`, `tags`. Falling back to first H1 for title and first paragraph for summary.
- Writing `kb_index.json` at root.

This script is run by the KB maintainer (i.e., the user) manually or via an Azure DevOps pipeline on push. **The app does not generate the index itself** — it only reads it. This keeps the app stateless with respect to the KB and avoids write permissions.

If `kb_index.json` is missing at load time, log a warning and build a minimal in-memory index from file paths + first H1 only. Do not fail.

### 8.3 KB service API

```python
class KBService:
    def list_index(self) -> list[KBEntry]: ...
    def read_file(self, path: str) -> str:
        """Read a file under kb/. Path must be relative and must not escape kb/."""
    def search(self, query: str, limit: int = 10) -> list[KBEntry]:
        """Substring search over index titles, summaries, tags. Case-insensitive."""
```

### 8.4 Security: path traversal

`read_file(path)` MUST:
- Reject any path containing `..` or starting with `/`.
- Resolve the absolute path and verify it is within `{KB_REPO_LOCAL_PATH}/kb/`.
- Raise `PermissionError` on violation.

---

## 9. Skills

### 9.1 Skill model

```python
@dataclass
class Skill:
    id: str                    # "shared:architect" or "personal:my-architect"
    name: str                  # slug
    display_name: str
    description: str
    system_prompt: str
    tools: list[str]           # tool names, must exist in registry
    source: Literal["shared", "personal"]
```

### 9.2 Shared skills (Git-backed)

Each shared skill is a folder `skills/shared/<name>/` containing `SKILL.md`:

```markdown
---
display_name: Architect
description: 10x cloud architect mode for design decisions
tools:
  - read_kb_file
  - search_kb
  - fetch_ms_docs
---

You are a senior cloud architect specializing in Azure...
[full system prompt body]
```

Loader:
- On backend startup and every KB sync, scan `{KB_REPO_LOCAL_PATH}/skills/shared/*/SKILL.md`.
- Parse YAML frontmatter. Validate `display_name`, `description`, `tools` (list of strings). Missing fields → log warning and skip.
- Body after frontmatter = `system_prompt`.
- Cache in memory. Invalidate on sync.

### 9.3 Personal skills (DB-backed)

Stored in `personal_skills` table per §7.1. Accessed only via backend API (§10.3).

### 9.4 Skill loader (unified)

```python
def load_skill(skill_id: str, user_oid: str) -> Skill:
    kind, name = skill_id.split(":", 1)
    if kind == "shared":
        return _load_shared(name)   # from in-memory cache
    elif kind == "personal":
        return _load_personal(user_oid, name)  # from DB, scoped to user_oid
    raise ValueError("Invalid skill id")
```

The `_load_personal` query MUST include `WHERE user_oid = ? AND deleted_at IS NULL`. This is the privacy boundary. Never accept a `user_oid` from client input — always take it from the authenticated `current_user`.

### 9.5 Skill snapshot on conversation start

When a conversation is created, snapshot the skill's state (prompt, tools, display name) into `conversations.skill_snapshot_json`. Use this snapshot for the entire conversation's duration, not the live skill. This ensures:
- Editing a skill mid-conversation doesn't change in-flight behavior.
- Deleting a skill doesn't break past conversations.

---

## 10. API surface

All endpoints require auth unless noted.

### 10.1 Health

```
GET /healthz            # public, returns {status: "ok", git_last_sync: "..."}
```

### 10.2 Conversations

```
GET    /api/conversations                     # list current user's conversations, newest first, excluding soft-deleted
GET    /api/conversations/{id}                # fetch with messages
DELETE /api/conversations/{id}                # soft delete
PATCH  /api/conversations/{id}  { title }     # rename
```

Authorization: every handler must verify `conversation.user_oid == current_user.oid`.

### 10.3 Skills

```
GET    /api/skills                             # list shared + current user's personal
GET    /api/tools                              # list available tool names and descriptions
GET    /api/skills/personal/{name}             # fetch one personal skill (for editing)
POST   /api/skills/personal                    # create
       body: { name, display_name, description, system_prompt, tools: [] }
PUT    /api/skills/personal/{name}             # update
DELETE /api/skills/personal/{name}             # soft delete
```

Validation:
- `name`: lowercase, alphanumeric + hyphens, 1–64 chars, matches `^[a-z0-9][a-z0-9-]{0,63}$`.
- `display_name`: 1–100 chars.
- `description`: 0–500 chars.
- `system_prompt`: 1–32,000 chars.
- `tools`: each must exist in the tool registry; reject unknown tools with 400.

### 10.4 Chat

```
POST /api/chat
  body: {
    conversation_id: int | null,   # null = new conversation
    skill_id: str,                 # only required when conversation_id is null
    message: str
  }
  response: SSE stream (see §11.3)
```

```
POST /api/approvals/{approval_id}
  body: { action: "approve" | "deny" }
  response: { status: "ok" }
  # Resumes the paused agent loop; the chat SSE stream must still be connected,
  # or the client can reconnect via GET /api/chat/resume (see §11.5).
```

```
GET /api/chat/resume?conversation_id=<id>
  response: SSE stream
  # Reconnects a paused stream, e.g., after page reload while approval was pending.
```

---

## 11. Agent orchestrator

### 11.1 Core loop

Pseudocode:

```
function handle_chat(conversation, user_message, user):
    # 1. Persist user message
    save_message(conversation, role="user", content=user_message)

    # 2. Build request
    skill = skill_from_snapshot(conversation)
    messages = load_message_history(conversation)     # all prior non-deleted messages
    system_prompt = compose_system_prompt(skill, kb_index_summary)
    tools = resolve_tools(skill.tools)

    while True:
        # 3. Call Azure OpenAI, streaming
        stream = azure_openai.chat.completions.create(
            model=config.AZURE_OPENAI_DEPLOYMENT,
            messages=[system_prompt] + messages,
            tools=[t.to_openai_schema() for t in tools],
            stream=True,
        )

        assistant_content, tool_calls = consume_stream(stream, emit_sse=True)

        save_message(conversation, role="assistant",
                     content=assistant_content, tool_calls=tool_calls)
        messages.append(assistant_message)

        if not tool_calls:
            emit_sse({type: "done"})
            return

        # 4. Execute tool calls in order
        for call in tool_calls:
            tool = tools[call.name]

            if tool.requires_approval:
                approval = create_pending_approval(conversation, call)
                emit_sse({type: "approval_required", approval})
                result = await_approval_resolution(approval.id)  # blocks this request
                if result.status == "denied":
                    tool_result = "User denied the tool call."
                elif result.status == "expired":
                    tool_result = "Approval timed out."
                else:
                    tool_result = execute_tool(tool, call.args)
            else:
                tool_result = execute_tool(tool, call.args)

            save_message(conversation, role="tool",
                         tool_call_id=call.id, tool_name=call.name,
                         content=tool_result)
            messages.append(tool_message)
            emit_sse({type: "tool_result", name: call.name, content: tool_result})

        # 5. Loop back: feed tool results to model
```

### 11.2 Safety caps

- **Max tool call iterations per turn:** 10. If exceeded, emit error and stop.
- **Max tokens in a single response:** set via API, e.g., 4096.
- **Max message history sent to LLM:** 50 most recent messages. Older messages dropped silently (not deleted from DB). Record this as a known limitation — if v2 needs summarization, we'll add it.

### 11.3 SSE event protocol

Events emitted to the client over SSE during a chat turn:

```
event: token
data: {"text": "chunk of assistant text"}

event: tool_call_start
data: {"call_id": "...", "name": "read_kb_file", "args": {...}}

event: approval_required
data: {"approval_id": "uuid", "tool_name": "...", "args": {...}, "reason": "..."}

event: tool_result
data: {"call_id": "...", "name": "...", "content": "..."}

event: message_saved
data: {"message_id": 123, "role": "assistant"}

event: done
data: {"conversation_id": 42}

event: error
data: {"message": "..."}
```

Use `text/event-stream` content type. Flush after each event.

### 11.4 Approval state machine

- On tool call needing approval, insert row in `pending_approvals` with status `pending`.
- Emit `approval_required` SSE event.
- Agent loop blocks awaiting resolution. Implementation: an `asyncio.Event` keyed by approval_id, set by the `POST /api/approvals/{id}` handler.
- On timeout (`TOOL_APPROVAL_TIMEOUT_SECONDS`), the row is marked `expired`, the event is set, and the agent resumes with a "timed out" tool result.
- A background sweeper task updates stale `pending` rows to `expired` every 60 seconds.

### 11.5 Resuming after disconnect

If the SSE client disconnects while an approval is pending, the approval row persists. On reconnect via `GET /api/chat/resume?conversation_id=X`:
- If there's a `pending` approval for this conversation, emit `approval_required` again.
- Otherwise, resume/finish as normal.

### 11.6 System prompt composition

The final system prompt is:

```
<skill.system_prompt>

---
Knowledge base index (use read_kb_file or search_kb to retrieve full content):
<kb_index_summary>
---

Current user: <display_name> (<email>)
Current date: <ISO date>
```

`kb_index_summary` is a compact listing: `- path — title: summary (tags)`, one per line. For 100 files this is a few KB, well within limits. If the index ever exceeds 20 KB, truncate and log a warning; v2 can add retrieval.

---

## 12. Tools

### 12.1 Tool base interface

```python
class Tool(ABC):
    name: str
    description: str
    parameters_schema: dict            # JSON Schema
    requires_approval: bool = False
    enabled_by_config: bool = True     # gated on env flag

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    @abstractmethod
    def execute(self, args: dict, user: User) -> str: ...
```

### 12.2 Registry

A module-level dict `TOOL_REGISTRY: dict[str, Tool]`. Populated at import time. Filtered by `enabled_by_config` based on env flags.

### 12.3 Tools to implement in v1

#### `read_kb_file`
- `requires_approval`: False
- Args: `{ "path": string }`
- Behavior: `kb_service.read_file(path)`; returns file contents.
- Errors: path traversal → "Invalid path"; not found → "File not found".

#### `search_kb`
- `requires_approval`: False
- Args: `{ "query": string, "limit": int (default 10, max 50) }`
- Behavior: `kb_service.search(query, limit)`; returns JSON list of `{path, title, summary}`.

#### `fetch_ms_docs`
- `requires_approval`: False
- Args: `{ "query": string }`
- Behavior: call Microsoft Learn search API (`https://learn.microsoft.com/api/search?...`) with the query, return top 5 results as JSON (`title`, `url`, `description`).
- Rate limit: max 20 calls per conversation; after that return error "rate limit exceeded".

#### `run_shell`
- `requires_approval`: **True**
- `enabled_by_config`: `TOOL_SHELL_ENABLED`
- Args: `{ "command": string, "reason": string, "timeout_seconds": int (default 30, max 120) }`
- Behavior: execute `command` via `subprocess.run` with `shell=True`, capture stdout/stderr/returncode, enforce timeout. Return combined output truncated to 8 KB.
- Working directory: a per-conversation temp directory under `/tmp/team-architect/<conversation_id>/` created on first use.
- Environment: inherit nothing sensitive. Start from an empty env + `PATH`, `HOME=/tmp/team-architect/<conversation_id>/`, and a whitelist.
- **This is the most dangerous tool.** Document in README that enabling it means the LLM can run arbitrary commands inside the container after user approval. The container itself is the security boundary — run with a non-root user, read-only root filesystem where possible.

#### `az_cli`
- `requires_approval`: **True**
- `enabled_by_config`: `TOOL_AZ_CLI_ENABLED`
- Args: `{ "args": string[], "reason": string }`
- Behavior: execute `az` with the given args (never shell-interpolated — pass as list). Timeout 60 s. Return stdout+stderr truncated to 8 KB.
- Auth: the container's managed identity or a pre-configured service principal. **The LLM never sees credentials.** Document required RBAC.

### 12.4 Disabled-tool handling

If a skill declares a tool that is disabled by config (e.g., `run_shell` with `TOOL_SHELL_ENABLED=false`), filter it out at skill-load time and include a note in the skill's resolved tools. Log a warning once per skill load.

---

## 13. Frontend requirements

### 13.1 Pages

- **Chat page** (`/`): left sidebar = conversation list + "New chat" button; main area = messages + input box; header = skill picker.
- **Skills page** (`/skills`): list of shared + personal skills; "New skill" button; click a personal skill to edit.

### 13.2 Chat UX

- User types message, clicks send (or Enter; Shift+Enter = newline).
- Streaming response renders tokens as they arrive.
- Tool calls render as collapsed cards inline: "Used `read_kb_file`: kb/adrs/adr-001.md" — expandable to show args and result.
- Approval required → render an **Approval Card** with:
  - Tool name (prominent)
  - Args (formatted code block)
  - Reason (LLM-provided text)
  - Approve / Deny buttons
  - A countdown showing time remaining before expiry
- User cannot send new messages until pending approval is resolved or errors/done received.
- Display error events in red; allow retry.

### 13.3 Skill editor UX

- Form fields:
  - `name` — slug, disabled on edit (only editable on create)
  - `display_name` — text input
  - `description` — short text input
  - `tools` — list of checkboxes pulled from `GET /api/tools`
  - `system_prompt` — `<textarea>`, monospace, min-height 400px, no fancy editor in v1
- Save / Cancel / Delete buttons.
- Show validation errors inline from backend 400 responses.
- **No markdown preview in v1.** Note this explicitly in the UI ("tip: draft in your editor of choice, paste here").

### 13.4 Skill picker UX

- Dropdown grouped:
  - **Shared** (team skills)
  - **My skills** (personal)
- Show icon/badge distinguishing the two.
- Selecting a skill for a new chat is required; for an existing chat, skill is locked.

### 13.5 Error handling

- 401 responses → trigger MSAL re-auth.
- 403 responses → show "You don't have access" toast.
- 5xx → toast "Something went wrong" + log to console.
- SSE `error` events → toast + mark last assistant message as errored.

---

## 14. Local development

### 14.1 Prerequisites

- Python 3.11+
- Node.js 20+
- Git
- Azure CLI (only if using `az_cli` tool locally)
- A local clone of the KB repo (can be a local-only folder with some test `.md` files for dev)

### 14.2 Quick start

```bash
# 1. Clone and enter
git clone <this-repo>
cd team-architect-app
cp .env.example .env   # fill in values

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 3. Frontend (new shell)
cd frontend
npm install
npm run dev   # Vite on port 5173
```

### 14.3 Local KB setup

For dev, `KB_REPO_URL` can be a local path (use `file://`). Or set `KB_REPO_LOCAL_PATH` to a pre-cloned directory and have the sync task no-op when it detects a local-only setup (config flag `KB_REPO_LOCAL_ONLY=true`).

### 14.4 Local auth

Dev mode can be simplified by setting `APP_ENV=dev` and `DEV_AUTH_BYPASS=true`, which injects a fake user `{oid: "dev-user", email: "dev@local", name: "Dev User"}`. **This flag MUST be rejected if `APP_ENV != dev`.** The backend must fail to start if `DEV_AUTH_BYPASS=true` and `APP_ENV=prod`.

### 14.5 Docker Compose (optional)

Provide a `docker-compose.yml` that runs backend + frontend + a bind-mounted KB folder. Useful for team members who don't want to install Python locally.

---

## 15. Deployment (Azure)

### 15.1 Target

Single Azure Container App in a Container Apps environment. Autoscale 1–3 replicas based on HTTP concurrency (target 5 concurrent requests per replica).

### 15.2 Resources to provision

Document in `infra/README.md` (bicep/terraform optional for v1; manual provisioning acceptable):

- Azure Container Apps environment
- Container App (backend + frontend as separate containers, or one container serving both — recommend **one container** for v1: backend serves the built frontend from `/static`)
- Azure Files share for persistent storage; mount to `/var/app/data` (SQLite) and `/var/app/kb` (Git working copy)
- Azure Container Registry
- Azure OpenAI resource with GPT-4o deployment
- App registrations (backend API + frontend SPA)
- Storage account for backups
- Log Analytics workspace linked to Container App

### 15.3 Secrets

All secrets via Container App secrets + env var refs:
- `AZURE_OPENAI_API_KEY` (or switch to managed identity)
- `KB_REPO_PAT`
- `BACKUP_AZURE_STORAGE_CONNECTION_STRING`

### 15.4 Identity

Use a user-assigned managed identity for the Container App. Grant it:
- `Cognitive Services OpenAI User` role on the Azure OpenAI resource (if using MI for LLM).
- Appropriate RBAC on any Azure resources the `az_cli` tool should manage.

### 15.5 CI/CD

Azure Pipelines YAML (`azure-pipelines.yml`):
- Trigger on main branch.
- Build backend image, build frontend assets (bundled into backend image).
- Push image to ACR.
- Deploy via `az containerapp update --image`.
- Run `alembic upgrade head` as part of container startup (not a separate step).

### 15.6 Backup job

If `BACKUP_ENABLED=true`:
- Background task in backend runs every `BACKUP_INTERVAL_SECONDS`.
- Uses SQLite online backup API (`sqlite3.Connection.backup()`) to snapshot `app.db` to a temp file without locking.
- Uploads to Azure Blob Storage container `BACKUP_CONTAINER_NAME` with key `app-db-<utc-iso-timestamp>.db`.
- Retention: keep last 30 snapshots; delete older.

---

## 16. Observability

### 16.1 Logging

- Structured JSON logs via stdlib `logging` + a JSON formatter (`python-json-logger`).
- Every log line: `timestamp`, `level`, `logger`, `message`, and correlation fields: `user_oid`, `conversation_id`, `request_id` when applicable.
- Request IDs: generated in a middleware, returned as `X-Request-ID` header.
- **Never log:** access tokens, API keys, full message bodies of user/assistant messages, PAT values. OK to log: metadata (lengths, counts), tool names, approval decisions, errors.

### 16.2 Metrics

Minimum for v1: expose `/metrics` (Prometheus format via `prometheus-client`) with:
- `chat_requests_total{status}`
- `chat_request_duration_seconds`
- `tool_calls_total{tool, result}`
- `approvals_total{result}`
- `azure_openai_tokens_total{direction}`
- `kb_sync_total{result}`

### 16.3 Health checks

- `GET /healthz` returns `{status, kb_last_sync, db_ok}`. Used by Container Apps liveness probe.
- `GET /readyz` stricter: fails if KB never synced or DB migration not at head.

---

## 17. Explicitly out of scope for v1

Do not implement. Log as known limitations / future work.

- Vector search / embeddings-based retrieval over KB.
- Conversation summarization or context compression.
- Markdown preview / rich editor in skill editor.
- Sharing personal skills between users.
- Admin UI for managing users or tool allowlists.
- Conversation export.
- Multiple KB repos.
- Model selection per skill (always uses `AZURE_OPENAI_DEPLOYMENT`).
- Function-calling tools that mutate the KB (no `write_kb_file`).
- Streaming partial tool results.
- Mobile-optimized UI (desktop-first).
- i18n.
- Rate limiting per user (relying on Azure OpenAI's own limits for v1).

---

## 18. Testing requirements

### 18.1 Backend

- **Unit tests** for: path traversal guard, skill parsing (frontmatter + body), tool registry, approval state machine transitions, system prompt composition.
- **Integration tests** for: all API endpoints with a mocked Azure OpenAI client; auth boundary (user A cannot access user B's skills or conversations).
- **Auth tests:** valid token, expired token, wrong audience, wrong tenant — all must be covered.

Use `pytest` + `pytest-asyncio` + `httpx` for API testing. Target coverage 70%+ on `app/` modules.

### 18.2 Frontend

- Component tests for `ApprovalCard`, `SkillEditor` (validation), `SkillPicker` (grouping).
- E2E tests are **out of scope** for v1 (nice-to-have via Playwright later).

### 18.3 Manual test checklist (pre-release)

Document in `docs/release-checklist.md`:

- [ ] Sign in as two different users; each sees only own conversations and personal skills.
- [ ] Create a personal skill, use it in chat, edit it, delete it. Past conversation still renders correctly.
- [ ] Trigger `read_kb_file` — no approval, returns content.
- [ ] Trigger `run_shell` — approval card appears, approve → executes, deny → agent receives "denied", timeout → agent receives "timed out".
- [ ] Kill backend mid-chat, restart, resume via `/api/chat/resume` — pending approval still honored.
- [ ] Push a new file to KB repo; wait for sync interval; `search_kb` finds it.
- [ ] Push a new shared skill; wait for sync; appears in skill picker.
- [ ] 401 → frontend re-auths.
- [ ] SQLite file survives container restart when mounted on Azure Files.

---

## 19. Security checklist

- [ ] All DB queries touching per-user data filter by `user_oid` from the authenticated token.
- [ ] `read_kb_file` rejects paths outside `kb/`.
- [ ] `run_shell` and `az_cli` only run after explicit approval tied to the exact args shown to the user. If args change between approval request and execution, reject.
- [ ] Secrets never logged.
- [ ] `DEV_AUTH_BYPASS` rejected in prod.
- [ ] CORS limited to configured origins.
- [ ] Rate limit `POST /api/chat` to 30/min/user (FastAPI middleware with in-memory counter; Redis not required for this scale).
- [ ] MSAL redirect URIs limited to known hosts.
- [ ] Container runs as non-root user.
- [ ] Dependency scanning in CI (e.g., `pip-audit`, `npm audit`).

---

## 20. Implementation order

Recommended build order. Each step should be a PR, and the system should be runnable (even if partial) after every step.

**Week 1 — Foundations**
1. Repo scaffold: backend + frontend skeletons, `.env.example`, docker-compose.
2. Backend config + logging + `GET /healthz`.
3. Entra ID auth: token validation, `current_user` dependency, `GET /api/me`.
4. Frontend MSAL integration, protected shell, login redirect working end-to-end.
5. SQLite + SQLModel + first Alembic migration (users, conversations, messages tables).

**Week 2 — KB and skills**
6. Git sync service (clone + pull) and KB index loader.
7. `read_kb_file`, `search_kb` tools (no LLM yet — test via a debug endpoint).
8. Shared skills loader.
9. Personal skills: DB table, CRUD endpoints, validation.
10. Frontend skills page: list, create, edit, delete.

**Week 3 — The agent**
11. Azure OpenAI client wired up, basic non-streaming chat (no tools).
12. Tool registry integration; chat with `read_kb_file`/`search_kb`/`fetch_ms_docs`.
13. SSE streaming to frontend.
14. Conversations persisted; history rendered on reload.
15. Skill snapshot on conversation start.

**Week 4 — Approvals, polish, deploy**
16. Approval state machine + `run_shell` + `az_cli` tools.
17. Approval UX in frontend (approval card, pending state, timeout countdown).
18. Resume endpoint and reconnect flow.
19. Backup job.
20. Dockerfile, Azure Pipelines, deploy to Container Apps with Azure Files mount.
21. Manual test checklist pass.

---

## 21. Definition of done for v1

- [ ] All items in §20 implemented and merged to main.
- [ ] Backend coverage ≥ 70%.
- [ ] Manual test checklist (§18.3) passes in prod environment.
- [ ] README documents: setup, env vars, app registration steps, KB repo conventions, skill authoring guide.
- [ ] 5 team members can each sign in, chat, and manage personal skills without support.
- [ ] One dry-run disaster recovery: restore SQLite from backup, verify data intact.

---

## 22. Notes for the implementing AI / developer

- Follow the file/folder layout in §4.1 exactly. Do not invent alternative structures.
- Ask for clarification only on genuinely ambiguous points. Most design decisions are fixed in this document; do not re-open them.
- Prefer boring, well-supported library choices over novel ones.
- Write tests alongside implementation, not after.
- When in doubt on security, choose the more restrictive option and leave a `# TODO: revisit` comment.
- The §19 security checklist is binding. Do not mark v1 done without every item verified.
- If you discover the spec is wrong or infeasible at some point, stop and flag it. Do not silently deviate.

---

**End of specification.**