# Testing Guide — Nexus

Reference for the **automated** test suites (pytest + vitest). For manual/exploratory
tool QA, see [Tester.md](Tester.md).

Current totals: **~917 backend tests** across 40 files · **~146 frontend tests** across 15 files.
(Counts are from `pytest --collect-only`; they drift as tests are added — re-run the collect
command below to refresh.)

## Quick Commands

### Run all backend tests
```powershell
cd e:\Work\MyProjects\Nexus\backend
& ".\.venv\Scripts\python.exe" -m pytest tests/ -q
```

### Run all frontend tests
```powershell
cd e:\Work\MyProjects\Nexus\frontend
npm test
```

### Run everything (one-liner)
```powershell
cd e:\Work\MyProjects\Nexus\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/ -q; cd ..\frontend; npm test
```

### Recount tests (when this doc drifts)
```powershell
cd e:\Work\MyProjects\Nexus\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/ --collect-only -q
```

---

## Backend Tests (pytest)

40 files, grouped by area. "Count" = collected test cases (includes parametrized cases).

### Config, auth, access control
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_config.py` | `app.config` | Settings defaults, CORS parsing, dev-bypass validation, prod settings, tool flags | 21 |
| `test_auth.py` | `app.auth` | Dev bypass user, User model, prod bypass rejection | 12 |
| `test_rbac.py` | `app.auth.rbac` | Entra app-role → skill/tool visibility filtering | 24 |
| `test_phase_gates.py` | `app.phases` | `NEXUS_PHASE` gating of tools/skills/features | 13 |

### Database & API
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_db_models.py` | `app.db.models` | UserRecord, Conversation, Message, PersonalSkill, PendingApproval CRUD | 7 |
| `test_api.py` | `app.api.*` | Health/readyz, skills/tools list, personal-skill CRUD, conversations, approvals, chat auth, metrics | 48 |
| `test_rate_limit.py` | `app.api.chat` | Under/at limit (429), per-user isolation | 3 |
| `test_learnings_api.py` | `app.api` (learnings) | Learnings list/inspect endpoints | 20 |
| `test_skills_audit.py` | skills audit path | Skill audit logging | 5 |

### Skills
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_skills.py` | `app.skills.shared/personal` | SKILL.md parsing, personal CRUD, user isolation, soft delete | 7 |
| `test_loader.py` | `app.skills.loader` | Load shared/personal, not-found, invalid formats | 8 |

### Knowledge base, ingest & retrieval
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_kb.py` | `app.kb` | Path traversal (4 vectors), valid reads, keyword search, indexer | 11 |
| `test_kb_hybrid.py` | `app.kb` hybrid | BM25 + vector + RRF hybrid search, confidence banding | 9 |
| `test_chunker.py` | `app.kb` chunker | Markdown chunking boundaries/headings | 37 |
| `test_ingest_normalize.py` | KB ingest | Source normalization | 33 |
| `test_ingest_pdf.py` | KB ingest | PDF extraction | 12 |
| `test_reranker.py` | `app.kb.reranker` | LLM-judge rerank (uses `max_completion_tokens`) | 20 |
| `test_vector_store.py` | `app.kb.vector_store` | sqlite-vec store, hybrid_search, diversify | 21 |

### Tools (generic + Azure bundle)
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_tools.py` | `app.tools` | Registry init, schemas, approval flags, KB read/search, `execute_script`, ms_docs | 111 |
| `test_new_tools.py` | newer generic + Azure tools | web/github/SO search, az_* bundle tools, drawio family | 122 |
| `test_bundle_decoupling.py` | `app.tools.base` capability matrix | retry/learning eligibility, result limits, config flags, credentials | 14 |
| `test_ask_user.py` | `app.tools.generic.ask_user` | Question schema validation | 13 |

### Diagrams
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_drawio_emitter.py` | `_drawio_emitter` | DOT→drawio cell/edge emission, icon mapping | 49 |
| `test_validate_drawio.py` | `validate_drawio` | Overlap, containment, vendor-icon, edge-routing, literal `\n` checks | 26 |
| `test_patch_drawio.py` | `patch_drawio_cell` | Surgical geometry patch | 12 |
| `test_python_diagram.py` | `generate_python_diagram` | AST safety guard, kwarg injection, render | 22 |
| `test_python_to_drawio.py` | `generate_drawio_from_python` | AST guard, capture→emit pipeline | 24 |

### Agent / orchestrator
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_agent.py` | `app.agent` | Approval state machine, system-prompt composition | 7 |
| `test_streaming.py` | `app.agent.streaming` | SSE event formatters, special chars | 10 |
| `test_compaction.py` | history compaction | Context-window compaction | 34 |
| `test_token_usage.py` | `app.agent.token_usage` | Occupancy segments, scaling to API `prompt_tokens` | 13 |
| `test_kill_switch.py` | process kill registry | Stop/disconnect kills script subprocess tree | 7 |
| `test_denial_handling.py` | approval denial path | Denied tool → graceful continue | 12 |
| `test_orchestrator_narration_nudge.py` | orchestrator | "Narrate-then-act" nudge behavior | 15 |
| `test_orchestrator_render_review.py` | orchestrator | Rendered-PNG vision-review message injection | 7 |

### Learnings (orchestrator-owned memory)
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_learnings.py` | `app.agent.learnings` | Write gates (rephrase/regex/name-guard/judge), retrieval, lifecycle | 25 |
| `test_user_correction_learning.py` | user-correction path | Capture + contradiction-archive | 26 |

### Remediation / risk (Phase 2)
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_remediation_phase2.py` | remediation flow | Phase-2 remediation behavior | 31 |
| `test_risk_review.py` | risk review | Risk classification/review | 21 |

### External connectivity (may need live keys)
| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `test_openai.py` | Azure OpenAI client | Real connectivity; asserts `max_completion_tokens` (not `max_tokens`); skips without keys | 5 |

### Backend test categories
- **Security:** path traversal (`../`, absolute, backslash) across every file tool; shell-injection blocking (`` ` ``, `&`, `%`, NUL); AST sandbox for `diagrams` code; auth (missing header → 401, dev-bypass-in-prod rejected); rate limiting (429); approval flags (`az_cli`/`execute_script` require approval; `az_rest_api`/`az_devops` mutations gated dynamically).
- **Data integrity:** soft delete, user isolation, approval state-machine transitions (no double-resolve).
- **Validation:** skill name/display/description/prompt rules; unknown-tool rejection; `ask_user` question schema; tool capability-matrix invariants.
- **Integration:** full API with app lifespan (DB + tools + KB initialized), health/readiness, Prometheus metrics.

---

## Frontend Tests (Vitest + React Testing Library)

15 files. "Count" = approximate (`it(`/`test(` blocks).

| File | Module Under Test | What It Covers | Count |
|---|---|---|---|
| `useAppStore.test.ts` | `src/store/useAppStore` | Messages, streaming, skills, approvals, tool calls, reset | 24 |
| `api.test.ts` | `src/api` | API client behaviors | 16 |
| `chat.test.ts` | `src/api/chat` | SSE stream parsing | 12 |
| `client.test.ts` | `src/api/client` | `apiFetch` base wrapper, auth headers | 3 |
| `learnings.test.ts` | `src/api` learnings | Learnings fetch | 8 |
| `attachmentUrl.test.ts` | attachment URL helpers | `isAllowedAttachmentUrl` / `resolveAttachmentUrl` origin gating | 11 |
| `types.test.ts` | `src/types.ts` | Interface shapes type-check | 12 |
| `App.test.tsx` | `src/App` | Renders without crashing | 1 |
| `ApprovalCard.test.tsx` | `ApprovalCard` | Tool name/reason/args render, approve/deny | 10 |
| `ChatWindow.test.tsx` | `ChatWindow` | Send, streaming, attachments, paste-image | 14 |
| `ContextUsageIndicator.test.tsx` | `ContextUsageIndicator` | Occupancy gauge + segment breakdown render | 7 |
| `ConversationList.test.tsx` | `ConversationList` | Sidebar list render/select | 7 |
| `MessageBubble.test.tsx` | `MessageBubble` | User/assistant render, tool messages hidden, image attachments | 5 |
| `SkillPicker.test.tsx` | `SkillPicker` | Skill selector dropdown | 9 |
| `SkillsPage.test.tsx` | `SkillsPage` | Skills management page | 7 |

### Frontend test categories
- **State management:** Zustand store actions tested independently; `resetChat` preserves `selectedSkillId`; tool-call result mapping by `call_id`.
- **API/SSE:** chat SSE parsing; attachment-URL origin allowlist (defends against rendering attacker-controlled image URLs).
- **Component rendering:** approval card, message bubble (incl. image attachments), context-usage gauge, skill picker, conversation list.
- **Type safety:** all interface shapes validated at test time.

---

## Running Individual Test Files

### Backend
```powershell
# Single file
& ".\.venv\Scripts\python.exe" -m pytest tests/test_kb.py -v

# Single test class / test
& ".\.venv\Scripts\python.exe" -m pytest tests/test_kb.py::TestPathTraversal -v
& ".\.venv\Scripts\python.exe" -m pytest tests/test_tools.py -k execute_script -v

# With coverage
& ".\.venv\Scripts\python.exe" -m pytest tests/ -q --cov=app --cov-report=term-missing
```

### Frontend
```powershell
npm test                                   # all
npm run test:watch                         # watch mode
npx vitest run src/test/useAppStore.test.ts  # single file
```

---

## Test Architecture

### Backend
- **Framework:** pytest + pytest-asyncio.
- **HTTP client:** httpx `AsyncClient` with `ASGITransport` + `asgi-lifespan` for full app lifecycle.
- **DB fixture:** in-memory SQLite per test via `conftest.py` (with sqlite-vec/FTS for KB + learnings tests).
- **Mocking:** `unittest.mock` for external HTTP (MS Docs, GitHub, Stack Overflow); `test_openai.py` makes real Azure OpenAI calls and skips without keys.
- **Bundle gating:** Azure-bundle tools register only when `TOOL_BUNDLE_AZURE_ENABLED=true`; `init_tools()` is called in the relevant fixtures/tests.
- **Config:** test env vars set in `conftest.py` before any app import.

### Frontend
- **Framework:** Vitest (integrated with Vite).
- **DOM:** jsdom.
- **Components:** `@testing-library/react` + `@testing-library/user-event`.
- **Assertions:** `@testing-library/jest-dom`.
- **Setup:** `src/test/setup.ts` loads jest-dom matchers and global mocks.
