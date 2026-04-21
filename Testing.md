# Testing Guide — Nexus

## Quick Commands

### Run all backend tests
```powershell
cd e:\Work\MyProjects\Nexus\backend
& ".\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short
```

### Run all frontend tests
```powershell
cd e:\Work\MyProjects\Nexus\frontend
npm test
```

### Run everything (one-liner)
```powershell
cd e:\Work\MyProjects\Nexus\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/ -v --tb=short; cd ..\frontend; npm test
```

---

## Backend Tests (pytest)

| File | Module Under Test | What It Covers | Test Count |
|---|---|---|---|
| `test_config.py` | `app.config` | Settings defaults, CORS parsing, dev bypass validation, prod settings | 6 |
| `test_auth.py` | `app.auth` | Dev bypass user, User model, prod bypass rejection | 3 |
| `test_db_models.py` | `app.db.models` | UserRecord, Conversation, Message, PersonalSkill, PendingApproval CRUD | 6 |
| `test_kb.py` | `app.kb` | Path traversal (4 vectors), valid reads, search (case, limit), indexer | 9 |
| `test_skills.py` | `app.skills.shared/personal` | SKILL.md parsing, personal CRUD, user isolation, soft delete | 7 |
| `test_loader.py` | `app.skills.loader` | Load shared/personal, not-found, invalid formats, user isolation | 8 |
| `test_tools.py` | `app.tools` | Registry init, schemas, approval flags, KB read/search, ms_docs mock, shell exec | 16 |
| `test_streaming.py` | `app.agent.streaming` | SSE format for all 8 event types, special chars | 10 |
| `test_agent.py` | `app.agent` | Approval state machine (6 transitions), system prompt composition | 7 |
| `test_rate_limit.py` | `app.api.chat` | Under limit, at limit (429), per-user isolation | 3 |
| `test_api.py` | `app.api.*` | Health, readyz, skills list, tools list, personal skill CRUD (10 validations), conversations, approvals, chat auth, metrics | 25 |

### Backend test categories

**Security tests:**
- Path traversal: `../`, `../../`, absolute `/`, backslash `\`
- Auth: missing header → 401, dev bypass in prod → rejected
- Rate limiting: 429 after 30 req/min, per-user isolation
- Tool approval flags: `run_shell` and `az_cli` require approval

**Data integrity tests:**
- Soft delete (conversations, personal skills)
- User isolation (personal skills, conversations)
- State machine transitions (pending → approved/denied, no double-resolve)

**Validation tests:**
- Skill name regex: uppercase, spaces, too long, starts with hyphen
- Display name length, description length, system prompt empty/too long
- Unknown tools rejected, invalid approval actions

**Integration tests:**
- Full API endpoint testing with app lifespan (DB, tools, KB initialized)
- Health and readiness probes
- Prometheus metrics endpoint

---

## Frontend Tests (Vitest + React Testing Library)

| File | Module Under Test | What It Covers | Test Count |
|---|---|---|---|
| `types.test.ts` | `src/types.ts` | All TypeScript interfaces compile & type-check | 10 |
| `useAppStore.test.ts` | `src/store/useAppStore` | All store actions: messages, streaming, skills, approvals, tool calls, reset | 17 |
| `ApprovalCard.test.tsx` | `src/components/ApprovalCard` | Renders tool name/reason/args, approve/deny button clicks | 6 |
| `MessageBubble.test.tsx` | `src/components/MessageBubble` | User/assistant rendering, tool messages hidden, alignment | 5 |
| `App.test.tsx` | `src/App` | App renders without crashing | 1 |

### Frontend test categories

**State management:**
- Zustand store: every action tested independently
- `resetChat` preserves `selectedSkillId` but clears everything else
- Tool call result mapping by `call_id`

**Component rendering:**
- ApprovalCard: renders all fields, fires correct callbacks
- MessageBubble: user vs assistant styling, tool messages return null

**Type safety:**
- All interface shapes validated at test time
- Optional fields (`system_prompt`, `conversation_id`) tested

---

## Running Individual Test Files

### Backend
```powershell
# Single file
& ".\.venv\Scripts\python.exe" -m pytest tests/test_kb.py -v

# Single test class
& ".\.venv\Scripts\python.exe" -m pytest tests/test_kb.py::TestPathTraversal -v

# Single test
& ".\.venv\Scripts\python.exe" -m pytest tests/test_tools.py::TestRunShellTool::test_shell_executes_command -v

# With coverage
& ".\.venv\Scripts\python.exe" -m pytest tests/ -v --cov=app --cov-report=term-missing
```

### Frontend
```powershell
# All tests
npm test

# Watch mode
npm run test:watch

# Single file
npx vitest run src/test/useAppStore.test.ts
```

---

## Test Architecture

### Backend
- **Framework:** pytest + pytest-asyncio
- **HTTP client:** httpx `AsyncClient` with `ASGITransport` + `asgi-lifespan` for full app lifecycle
- **DB fixture:** In-memory SQLite per test via `conftest.py::db_session`
- **Mocking:** `unittest.mock` for external HTTP calls (MS Docs API)
- **Config:** Test env vars set in `conftest.py` before any app imports

### Frontend
- **Framework:** Vitest (integrated with Vite)
- **DOM:** jsdom
- **Components:** `@testing-library/react` + `@testing-library/user-event`
- **Assertions:** `@testing-library/jest-dom` matchers
- **Setup:** `src/test/setup.ts` loads jest-dom matchers
