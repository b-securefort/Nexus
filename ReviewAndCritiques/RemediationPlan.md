# Nexus Remediation Plan

Consolidated execution plan for all findings in this folder. Designed for **parallel execution across multiple Claude Code instances**.

## How to use this plan

- Items are grouped into **Phases** (ship in this order — later phases depend on earlier ones landing).
- Within a phase, items are split into **Tracks**. **Tracks within the same phase can run in parallel in separate Claude instances** — they touch disjoint files.
- Each item has a status checkbox. As work lands, update the box.
- "Serial within track" = the listed items touch the same file(s) and must be done sequentially by one instance.
- Where a finding has been superseded by `ReviewCritique_AdditionalFindings.md`, this plan follows the revised guidance. Originals are still referenced for context.

Legend: 🔴 critical · 🟡 moderate · 🟢 low

---

## Phase 1 — Security hot-fixes (ship first, blocks no other work)

Goal: close the credential-exfiltration and injection vectors that are exploitable today.

### Track 1A — `backend/app/tools/base.py` (SERIAL within this track)
One instance should own this file end-to-end for the phase to avoid conflicts.

- [ ] 🔴 **B1 — Strip env in `AzureToolBase._run_az()`** (`base.py:214`)
  Replace `env = os.environ.copy()` with the explicit allow-list (`PATH`, `HOME`, `AZURE_CONFIG_DIR`, `SYSTEMROOT`, plus `AZURE_ACCESS_TOKEN` from the ARM ContextVar). See `ReviewCritique_AdditionalFindings.md` B1.
- [ ] 🔴 **B2 — Block `%` metachar on Windows** (`base.py`)
  Add `%` to `_SHELL_METACHAR_PATTERN` so `cmd.exe` cannot expand `%AZURE_OPENAI_API_KEY%` style payloads. Defence-in-depth for B1.
- [ ] 🔴 **CodeReview #1 — Command injection in `_run_az`** (`base.py`)
  Drop `shell=(sys.platform == "win32")` and invoke `az.cmd` directly via `subprocess.run([...], shell=False)`. Update `check_shell_injection` to also reject `&` and `|` for arg values (the current allowlist is wrong). Verify on Windows that `shutil.which("az")` resolves to `az.cmd`.
- [ ] **Tests** — add regression tests in `backend/tests/test_tools.py`:
  - `az` arg containing `&whoami` is rejected
  - `az` arg containing `%PATH%` is rejected
  - `_run_az` subprocess receives the stripped env (assert via monkeypatch on `subprocess.run`)

### Track 1B — `backend/app/api/chat.py` (SERIAL within this track)

- [ ] 🟡 **CodeReview #2 — Bound answer-submission payload** (`/api/questions/{question_id}/answer`)
  Add a Pydantic model with `max_length` on `question`, `selected`, `notes` and `max_items` on the list. Reject oversized requests with `422` before DB write.
- [ ] 🟢 **CodeReview #4 — Sanitize greeting injection** (`get_greeting`)
  Either strip non-`\w \-'.` chars from `first_name` before formatting, or move it to a `user` role message instead of interpolating into the system prompt.
- [ ] 🟡 **B7 — Enforce `CHAT_RATE_LIMIT_PER_MINUTE`** (`config.py:130` is dead today)
  Add a per-user dependency in `chat.py` that tracks `(user_oid, minute_bucket)` counts in memory (or remove the config). Recommend implementing — config implies protection users don't have.

### Track 1C — `backend/app/main.py`

- [ ] 🟢 **CodeReview #3 — Authenticate `/metrics`**
  Either gate behind `Depends(get_current_user)` plus an admin allow-list, or move to a separate internal port that the ingress doesn't expose. Prefer the auth gate — simpler.

### Track 1D — `backend/app/auth/entra.py`

- [ ] 🟢 **CodeReview #5 — ARM token unverified-claim hardening**
  Add an inline `# SECURITY:` comment at `_extract_arm_token` explicitly stating the claims are untrusted and must not be used for authorization decisions. Add a unit test that asserts the function only ever returns claims used as opaque pass-through (tenant + audience). No behavioral change — this is a guardrail for future maintainers.

### Track 1E — Frontend (`frontend/src/components/MessageBubble.tsx` + `frontend/index.html`)

- [ ] 🟡 **Frontend #1 — Attachment URL allowlist** (`MessageBubble.tsx:resolveAttachmentUrl`)
  Only allow URLs whose origin matches `VITE_API_BASE_URL` or a configured allowlist. Drop arbitrary `http(s)://` pass-through. Render a placeholder with the raw URL as text for everything else.
- [ ] 🟢 **Frontend #2 — Content Security Policy**
  Add a strict `Content-Security-Policy` `<meta>` to `index.html` (script-src self, img-src self + data: + the allowlisted attachment origin, style-src self + 'unsafe-inline' for Tailwind injected styles, connect-src self + API origin). Verify dev server still works.

**Phase 1 parallelism:** Tracks 1A–1E touch disjoint files — **5 instances can run concurrently**.

---

## Phase 2 — Orchestrator hardening

Goal: fix the in-memory state, RBAC, and concurrency hazards in `orchestrator.py`. Most of this lives in one file, so parallelism is limited.

### Track 2A — `backend/app/agent/orchestrator.py` (SERIAL, single instance)

Order matters — each step depends on the orchestrator structure that the previous step left.

- [ ] 🔴 **B3 — ARM token expiry check** (`orchestrator.py:708`)
  Decode the JWT `exp` claim before each Azure tool call. If expired or within 60s of expiry, return a structured error to the agent so it can ask the user to refresh, and emit an SSE `token_refresh_required` event. Option A from the finding; Option B (frontend refresh endpoint) is a follow-up below.
- [ ] 🟡 **B4 — Per-user `_tool_call_history`** (`orchestrator.py:637`)
  Change schema to `dict[user_oid, dict[tool, list[float]]]`, guard with `threading.Lock`, and prune stale entries (older than the rate window) on each access.
- [ ] 🟡 **A5 — Parallel tool execution** (`orchestrator.py:911`)
  Split the per-turn `tool_calls` into approval-required and safe groups. Dispatch safe calls with `asyncio.gather()`. Preserve ordering when appending results back to the message list (use indices, not iteration order).
- [ ] 🟡 **A4 — Lease-based approval recovery**
  Add a `conversation_leases` table (or column on `conversations`) with `(conversation_id, last_heartbeat, owning_instance)`. Orchestrator writes a heartbeat every 30s. Frontend polls; if `>60s` stale, surface a "Restart turn" button that creates a fresh user turn from the last user message. **Do not** attempt to reconstruct synthetic retry/drawio state — explicitly out of scope per the revised guidance.
- [ ] 🟢 **B10 — `ContextVar` propagation**
  Replace the `run_in_executor` call at `orchestrator.py:787` with `asyncio.to_thread()` (which uses `copy_context()`), or wrap the executor callable in `copy_context().run(...)`.

### Track 2B — `backend/app/agent/compaction.py`

- [ ] 🟡 **B6 — Async compaction LLM calls** (`compaction.py:97-106`)
  In `load_compacted_history()`, if `_summarize_long_paste()` / `_describe_image()` would need to call the LLM (cache miss), enqueue the call as a `BackgroundTask` and use the raw content for this turn. Cached summaries are picked up on the next turn.

### Track 2C — Concurrency primitives (`backend/app/agent/orchestrator.py` shared module + new `backend/app/agent/concurrency.py`)

⚠️ **This track touches orchestrator.py — coordinate with 2A.** Either fold into 2A as the final step, or have 2A land first and 2C rebase.

- [ ] 🟡 **A2 — Targeted concurrency fix** (revised from `Resolve_ConcurrencyExhaustion.md`)
  - Create `_tool_executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="tool")` in a new module.
  - Add `_get_user_semaphore(user_oid, max_concurrent=4)` and gate tool dispatch behind it.
  - Port `AzureToolBase._run_az()` and `RunShellTool` to `asyncio.create_subprocess_exec`. Leave OpenAI/SQLite/GitPython/MSAL on threads.
  - Skip the full async rewrite — not worth the cost per the revised guidance.

**Phase 2 parallelism:** 2A and 2B run in parallel (disjoint files). 2C must merge with 2A — either run as the same instance or sequentially.

---

## Phase 3 — Learnings module overhaul

Depends on Phase 2's orchestrator changes landing so the learning-derivation flow is stable.

### Track 3A — `backend/app/agent/learnings.py` + `backend/app/agent/learn_judge.py`

- [ ] 🟡 **A6 — Derive + rephrase + dual storage** (revised from `LearningModuleImprovements.md` #2)
  - Keep `derive_learning_from_success()` as the source of `details` (raw facts).
  - Add `rephrase_learning()` calling `gpt-4o-mini` with a constrained "no opinions, no framing" prompt; output → `summary`.
  - Run the existing 3-gate defense on the rephrased text.
  - On rephrase failure/timeout, fall back to raw derived text (no degradation).
  - Schema reuse: `summary` column = rephrased; `details` column = raw derived. **No migration needed.**
- [ ] 🟡 **LMI #1 — Async LLM judge**
  Move the judge call out of the critical write path into a `BackgroundTask`. The orchestrator returns the chat response immediately; the judge grades and (if approved) inserts into `agent_learnings` out-of-band.
- [ ] 🟡 **LMI #3 — Hybrid retrieval (RRF + FTS5)**
  Mirror the KB hybrid-retrieval pattern: add an FTS5 virtual table for `agent_learnings`, run BM25 + sqlite-vec in parallel, fuse with RRF. Fixes the dense-embedding gap on error codes / flag names.
- [ ] 🟢 **B8 — Parameterize SQL** (`learnings.py:357, 398, 432`)
  Replace `f"WHERE id IN ({placeholders})"` with bound parameters via SQLAlchemy `in_()` or explicit parameter binding. Safe today, but remove the maintenance trap.

### Track 3B — Learnings admin UI (`frontend/src/pages/`, new `backend/app/api/learnings.py`)

Independent of 3A — can run in parallel.

- [ ] 🟡 **LMI #4 — Human-in-the-loop curation UI**
  - New `GET/PATCH/DELETE /api/learnings` endpoints (admin-only — reuse Phase 1 admin gate).
  - New `LearningsPage.tsx` with list, search, edit, delete. Show `summary` and `details` side-by-side so architects can spot bad rephrases.
  - Link from main nav alongside the Skills page.

**Phase 3 parallelism:** 3A and 3B touch disjoint files — **2 instances in parallel**.

---

## Phase 4 — Reliability and cleanup

Independent low-risk improvements. All four tracks can run in parallel.

### Track 4A — `backend/app/agent/openai_client.py` (or wherever the AOAI client is constructed)

- [ ] 🟡 **B5 — Azure OpenAI circuit breaker**
  - Add explicit timeouts to every completions call (main, compaction, judge, rephrase).
  - Module-level failure counter; after N consecutive failures within window, short-circuit chat requests with a clear error message.
  - Include OpenAI reachability in `/healthz` response.

### Track 4B — `backend/app/api/conversations.py`

- [ ] 🟢 **B9 — Clean orphaned files on conversation delete**
  In the delete endpoint, also `unlink` associated files in `UPLOAD_DIR` and `output/`. Wrap in try/except so missing files don't fail the delete.

### Track 4C — ARM token refresh UX (frontend + `backend/app/api/chat.py`)

- [ ] 🟡 **B3 Option B — Frontend refresh of ARM token**
  - Backend: `POST /api/chat/refresh-token` that updates the ContextVar / pending-conversation state.
  - Frontend: listen for `token_refresh_required` SSE (introduced in Phase 2 / B3 Option A), call MSAL silently, POST the new token, then resume.
  - Goal: turn the explicit "ask user to retry" path from Phase 2 into a seamless refresh.

### Track 4D — Truncation correctness (`backend/app/agent/orchestrator.py`)

⚠️ Touches orchestrator.py — coordinate with Phase 2.

- [ ] 🟡 **A5 follow-up — replace head+tail truncation with LLM summarization** (revised from `Resolve_ContextOptimization.md`)
  The current `head + tail` split at `orchestrator.py:88-93` can produce invalid JSON. For tool outputs > 2KB, route through `gpt-4o-mini` summarization **instead of** truncating. Falls back to current truncation on summarizer failure.

**Phase 4 parallelism:** 4A, 4B, 4C, 4D touch disjoint files (4D coordinates with Phase 2 if not already merged) — **up to 4 instances in parallel**.

---

## Phase 5 — Infrastructure

Higher effort, requires Bicep / deployment changes. Sequence with care.

### Track 5A — Database persistence migration

- [ ] 🟡 **A1 — Azure Managed Disk on Container Apps** (revised from `Resolve_DatabasePersistence.md`)
  - Update Bicep: add `volumes[].storageType: AzureDisk` + `volumeMounts` mounting at `/data`.
  - Update `DATABASE_URL` to `sqlite:////data/app.db`.
  - Document the single-replica constraint in `CLAUDE.md` (already implicit, make it explicit).
- [ ] 🟡 **A1 — Implement Blob backup**
  Wire the existing `BACKUP_AZURE_STORAGE_CONNECTION_STRING` config into the `_backup_loop()` TODO at `main.py:346`. Use `sqlite3.backup()` to a local temp file, upload to Blob with timestamped key, retain last N.
- [ ] **Future-path docs**
  Add a section to `CLAUDE.md` noting the migration trigger ("when multi-instance becomes a real requirement") and target (PostgreSQL + pgvector, not Azure SQL — sqlite-vec/FTS5 have no clean Azure SQL equivalent).

### Track 5B — `run_shell` sandbox container

- [ ] 🟡 **Sandbox `run_shell` in ACI** (revised from `Resolve_ExecutionSandbox.md`)
  - Build a minimal sandbox image: Python + curl + jq, no Azure SDKs, no env secrets.
  - Refactor `RunShellTool` to submit scripts to an ACI instance via the management API, poll for completion, stream output back.
  - Network policy: no egress to internal vnet, only public internet.
  - **Prerequisite:** Phase 1 Track 1A (B1) must ship first — that closes the most immediate vector and de-risks delaying this work.

**Phase 5 parallelism:** 5A and 5B touch disjoint surfaces — **2 instances in parallel**, but each is multi-day work.

---

## Suggested dispatch sequence

| Round | Instances | Tracks | Why |
|---|---|---|---|
| 1 | 5 | 1A, 1B, 1C, 1D, 1E | All security hot-fixes ship in parallel. |
| 2 | 2 | 2A (→ 2C folded in), 2B | Orchestrator + compaction independently. |
| 3 | 2 | 3A, 3B | Learnings backend + admin UI. |
| 4 | 4 | 4A, 4B, 4C, 4D | Reliability cleanup. |
| 5 | 2 | 5A, 5B | Infra changes. |

If you have more bandwidth than rounds, run 4A–4D speculatively in parallel with Phase 3 — they don't touch the learnings module.

---

## Tracking

Update the checkboxes inline as each item lands. When a whole track is done, prepend ✅ to the track header. When a whole phase is done, move it to a "Completed" section at the bottom of this file so the active plan stays short.
