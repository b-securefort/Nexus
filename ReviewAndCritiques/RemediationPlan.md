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

### ✅ Track 1A — `backend/app/tools/base.py` (SERIAL within this track)
One instance should own this file end-to-end for the phase to avoid conflicts.

- [x] 🔴 **B1 — Strip env in `AzureToolBase._run_az()`** (`base.py:214`)
  Replace `env = os.environ.copy()` with the explicit allow-list (`PATH`, `HOME`, `AZURE_CONFIG_DIR`, `SYSTEMROOT`, plus `AZURE_ACCESS_TOKEN` from the ARM ContextVar). See `ReviewCritique_AdditionalFindings.md` B1.
- [x] 🔴 **B2 — Block `%` metachar on Windows** (`base.py`)
  Add `%` to `_SHELL_METACHAR_PATTERN` so `cmd.exe` cannot expand `%AZURE_OPENAI_API_KEY%` style payloads. Defence-in-depth for B1.
- [x] 🔴 **CodeReview #1 — Command injection in `_run_az`** (`base.py`)
  Drop `shell=(sys.platform == "win32")` and invoke `az.cmd` directly via `subprocess.run([...], shell=False)`. Update `check_shell_injection` to also reject `&` for arg values. `|` intentionally kept unblocked (safe with shell=False; needed for KQL). Verify on Windows that `shutil.which("az")` resolves to `az.cmd`.
- [x] **Tests** — added regression tests in `backend/tests/test_tools.py`:
  - `TestRunAzEnvAllowlist` — secrets stripped from subprocess env; ARM token forwarded
  - `TestShellInjectionBlocking` — `&whoami`, `%PATH%`, backtick, NUL blocked; KQL pipe and semicolons allowed
  - `TestRunAzShellFalse` — subprocess.run called with shell=False (all 11 tests pass)

### ✅ Track 1B — `backend/app/api/chat.py` (SERIAL within this track)

- [x] 🟡 **CodeReview #2 — Bound answer-submission payload** (`/api/questions/{question_id}/answer`)
  Add a Pydantic model with `max_length` on `question`, `selected`, `notes` and `max_items` on the list. Reject oversized requests with `422` before DB write. _(New `AnswerEntry`/`AnswerSubmission` models cap at 4 answers, 4 selected/answer, 500-char question, 300-char label, 2000-char notes. Tests in `TestAnswerSubmissionBounds`.)_
- [x] 🟢 **CodeReview #4 — Sanitize greeting injection** (`get_greeting`)
  Either strip non-`\w \-'.` chars from `first_name` before formatting, or move it to a `user` role message instead of interpolating into the system prompt. _(`_sanitize_first_name` strips non-`[\w \-'.]` chars and caps at 40 chars before interpolation. Tests in `TestGreetingSanitizer`.)_
- [x] 🟡 **B7 — Enforce `CHAT_RATE_LIMIT_PER_MINUTE`** (`config.py:130` is dead today)
  Add a per-user dependency in `chat.py` that tracks `(user_oid, minute_bucket)` counts in memory (or remove the config). Recommend implementing — config implies protection users don't have. _(Already wired at `chat.py:_check_rate_limit` + invoked from `chat()` entrypoint; existing tests in `tests/test_rate_limit.py`.)_

### ✅ Track 1C — `backend/app/main.py`

- [x] 🟢 **CodeReview #3 — Authenticate `/metrics`**
  Either gate behind `Depends(get_current_user)` plus an admin allow-list, or move to a separate internal port that the ingress doesn't expose. Prefer the auth gate — simpler. _(`/metrics` now gated via `Depends(require_architect)`; dev bypass still permits scraping in local mode. Test in `TestMetrics.test_metrics_endpoint_uses_admin_gate`.)_

### ✅ Track 1D — `backend/app/auth/entra.py`

- [x] 🟢 **CodeReview #5 — ARM token unverified-claim hardening**
  Add an inline `# SECURITY:` comment at `_extract_arm_token` explicitly stating the claims are untrusted and must not be used for authorization decisions. Add a unit test that asserts the function only ever returns claims used as opaque pass-through (tenant + audience). No behavioral change — this is a guardrail for future maintainers. _(SECURITY block added; 9 guardrail tests in `TestExtractArmTokenGuardrails`, including a source-inspection test that fails if future code reads `oid`/`roles`/`groups`/`sub`/`upn` from the unverified ARM JWT.)_

### ✅ Track 1E — Frontend (`frontend/src/components/MessageBubble.tsx` + `frontend/index.html`)

- [x] 🟡 **Frontend #1 — Attachment URL allowlist** (`MessageBubble.tsx:resolveAttachmentUrl`)
  Only allow URLs whose origin matches `VITE_API_BASE_URL` or a configured allowlist. Drop arbitrary `http(s)://` pass-through. Render a placeholder with the raw URL as text for everything else. _(New `isAllowedAttachmentUrl` + `resolveAttachmentUrl` returns `null` for disallowed origins; UI renders inert "Attachment blocked" placeholder. Tests in `src/test/attachmentUrl.test.ts`.)_
- [x] 🟢 **Frontend #2 — Content Security Policy**
  Add a strict `Content-Security-Policy` `<meta>` to `index.html` (script-src self, img-src self + data: + the allowlisted attachment origin, style-src self + 'unsafe-inline' for Tailwind injected styles, connect-src self + API origin). Verify dev server still works. _(Meta tag uses Vite's `%VITE_API_BASE_URL%` substitution so the same template works in dev and prod; `frame-ancestors` isn't supported in a `<meta>` CSP — set it at the ingress.)_

**Phase 1 parallelism:** Tracks 1A–1E touch disjoint files — **5 instances can run concurrently**.

---

## Phase 2 — Orchestrator hardening

Goal: fix the in-memory state, RBAC, and concurrency hazards in `orchestrator.py`. Most of this lives in one file, so parallelism is limited.

### ✅ Track 2A — `backend/app/agent/orchestrator.py` (SERIAL, single instance)

Order matters — each step depends on the orchestrator structure that the previous step left.

- [x] 🔴 **B3 — ARM token expiry check**
  New `arm_token_status()` helper in `auth/entra.py` decodes the JWT `exp` claim. Orchestrator pre-flights every `AzureToolBase` dispatch (both prefetched + serial); `missing`/`expired` short-circuits with a structured error telling the agent to wait, `near_expiry` still executes but emits the SSE event so the frontend can refresh in flight. New `sse_token_refresh_required` event in `streaming.py`.
- [x] 🟡 **B4 — Per-user `_tool_call_history`**
  Schema now `dict[user_oid, dict[tool, list[float]]]`, guarded by `threading.Lock`. New `_check_user_rate_limit()` prunes anything older than `max(window, _HISTORY_RETENTION_SECONDS)` on each access. Tests in `test_remediation_phase2.py::TestPerUserRateLimit`.
- [x] 🟡 **A5 — Parallel tool execution**
  New `_prefetch_safe_calls()` eagerly dispatches no-approval / valid-arg / non-ask_user tool calls via `asyncio.create_task` while the serial loop iterates results in arrival order. SSE event order preserved (each call still emits `tool_executing` → chunks → `tool_result` sequentially). Leftover tasks cancelled at end-of-iteration.
- [x] 🟡 **A4 — Lease-based approval recovery**
  Added `conversations.lease_heartbeat_at` + `lease_owner` columns (with lightweight migration). Orchestrator writes a heartbeat at most every `LEASE_HEARTBEAT_INTERVAL_SECONDS` (30s) during the turn and clears it at end-of-turn. New `GET /api/conversations/{id}/lease` returns `idle | active | stale` + `last_user_message_id` for the frontend's "Restart turn" affordance. Synthetic retry/drawio state is NOT reconstructed (per revised guidance).
- [x] 🟢 **B10 — `ContextVar` propagation**
  Replaced `loop.run_in_executor(None, _consume_openai_stream)` with `asyncio.create_task(asyncio.to_thread(...))`. The dedicated tool executor introduced in A2 also uses `copy_context().run(...)` so ARM token + active skill propagate into worker threads. Verified by `TestConcurrencyPrimitives::test_run_in_tool_executor_propagates_contextvar`.

### ✅ Track 2B — `backend/app/agent/compaction.py`

- [x] 🟡 **B6 — Async compaction LLM calls** (`compaction.py:97-106`)
  In `load_compacted_history()`, if `_summarize_long_paste()` / `_describe_image()` would need to call the LLM (cache miss), enqueue the call as a `BackgroundTask` and use the raw content for this turn. Cached summaries are picked up on the next turn.

### ✅ Track 2C — Concurrency primitives (`backend/app/agent/concurrency.py` + orchestrator wiring)

⚠️ **This track touches orchestrator.py — coordinate with 2A.** Either fold into 2A as the final step, or have 2A land first and 2C rebase.

- [x] 🟡 **A2 — Targeted concurrency fix** (revised from `Resolve_ConcurrencyExhaustion.md`)
  - Created `app/agent/concurrency.py` with lazy-singleton `tool_executor()` (`ThreadPoolExecutor(max_workers=64, thread_name_prefix="tool")`) so tool work doesn't compete with KB/SQLite on Python's default executor.
  - `get_user_semaphore(user_oid, max_concurrent=4)` per-user `asyncio.Semaphore` — one chatty user cannot exhaust the pool.
  - `run_in_tool_executor()` copies the current `contextvars.Context` into the worker so ARM token + active skill survive the hop.
  - Orchestrator's `_gated_tool_execute()` is the single chokepoint for prefetch + serial tool dispatch — acquires the per-user semaphore then runs on the tool executor.
  - Executor torn down via `shutdown_tool_executor()` from the FastAPI lifespan.
  - **Skipped:** `asyncio.create_subprocess_exec` port of `_run_az` / `RunShellTool` — substantial change, deferred per the revised guidance. The bounded pool + per-user semaphore already address the exhaustion symptom this track was scoped against.

**Phase 2 parallelism:** 2A and 2B run in parallel (disjoint files). 2C must merge with 2A — either run as the same instance or sequentially.

---

## Phase 3 — Learnings module overhaul

Depends on Phase 2's orchestrator changes landing so the learning-derivation flow is stable.

### ✅ Track 3A — `backend/app/agent/learnings.py` + `backend/app/agent/learn_judge.py`

- [x] 🟡 **A6 — Derive + rephrase + dual storage** (revised from `LearningModuleImprovements.md` #2)
  - `derive_learning_from_success()` unchanged — still produces `details` (raw facts) + rule-derived rough `summary`.
  - New `rephrase_learning()` in `learn_judge.py` calls the chat deployment with a strict "no opinions, no framing" system prompt to produce a single-sentence canonical summary.
  - The 3-gate defence (regex / name guard / LLM judge) now runs on the **rephrased** text, so a malicious rephrase can't slip suppression intent past the detectors.
  - On rephrase failure / empty output / 3× length blowup, falls back to the rule-derived summary (no degradation).
  - Schema reuse confirmed: `agent_learnings.summary` = rephrased, `.details` = raw derived. No migration.
- [x] 🟡 **LMI #1 — Async LLM judge**
  Orchestrator now calls `_schedule_learning_write()` which spawns an `asyncio` task that opens its own DB session and runs derive + rephrase + judge + persist out-of-band. The SSE stream returns immediately. Sync fallback when no event loop is running (test contexts).
- [x] 🟡 **LMI #3 — Hybrid retrieval (RRF + FTS5)**
  Added `agent_learnings_fts` (FTS5, external-content over `agent_learnings`) with INSERT/UPDATE/DELETE triggers + a `rebuild` backfill — all set up in `_ensure_agent_learnings_vec()`. `retrieve_relevant_learnings()` now runs BM25 + sqlite-vec in parallel and fuses via Reciprocal Rank Fusion (`_rrf_fuse`). Either side may be absent (vec0 or FTS5 missing) and the other carries the result. Status / tool-name / validation boosts preserved on top of the RRF score.
- [x] 🟢 **B8 — Parameterize SQL**
  All three `WHERE id IN ({placeholders})` sites in `learnings.py` now use bound `:id0,:id1,...` parameters with explicit `int()` coercion of ids upstream.

### ✅ Track 3B — Learnings admin UI (`frontend/src/pages/LearningsAdminPage.tsx` + `backend/app/api/learnings.py`)

Independent of 3A — can run in parallel.

- [x] 🟡 **LMI #4 — Human-in-the-loop curation UI**
  - `app/api/learnings.py` exposes `GET /api/learnings`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}` — all admin-gated by `require_architect` (DEV_AUTH_BYPASS passes through). `rejected` status is read-only to preserve the LLM-judge audit trail.
  - `frontend/src/pages/LearningsAdminPage.tsx` renders the list with status/type/category/tool filters, paginates, and shows a side-drawer detail view with `summary` + `details` side-by-side so architects can spot bad rephrases. Promote / Demote / Archive / Delete actions wired up.
  - Route `/admin/learnings` registered in `App.tsx`.

**Phase 3 parallelism:** 3A and 3B touch disjoint files — **2 instances in parallel**.

---

## Phase 4 — Reliability and cleanup

Independent low-risk improvements. All four tracks can run in parallel.

### ✅ Track 4A — `backend/app/agent/openai_client.py` (or wherever the AOAI client is constructed)

- [x] 🟡 **B5 — Azure OpenAI circuit breaker**
  - Add explicit timeouts to every completions call (main, compaction, judge, rephrase).
  - Module-level failure counter; after N consecutive failures within window, short-circuit chat requests with a clear error message.
  - Include OpenAI reachability in `/healthz` response.

### ✅ Track 4B — `backend/app/api/conversations.py`

- [x] 🟢 **B9 — Clean orphaned files on conversation delete**
  In the delete endpoint, also `unlink` associated files in `UPLOAD_DIR` and `output/`. Wrap in try/except so missing files don't fail the delete.

### ✅ Track 4C — ARM token refresh UX (frontend + `backend/app/api/chat.py`)

- [x] 🟡 **B3 Option B — Frontend refresh of ARM token**
  - Backend: `POST /api/chat/refresh-token` endpoint with Pydantic validation (`ArmTokenRefreshRequest`), conversation ownership check, JWT audience/tenant/expiry validation. Stores override via `set_arm_token_override()` for in-flight orchestrator turns.
  - Frontend: `ChatWindow.tsx` handles `token_refresh_required` SSE event, calls `msalInstance.acquireTokenSilent()` with ARM scope, POSTs refreshed token via new `refreshArmToken()` API function.
  - `TokenRefreshRequired` type added to SSE event union in `types.ts`.
  - Tests: 6 backend tests in `TestRefreshArmToken` (success, bad audience, expired, not JWT, nonexistent conv, tenant mismatch); 3 frontend tests (POST shape, error handling, SSE parsing).

### ✅ Track 4D — Truncation correctness (`backend/app/agent/orchestrator.py`)

⚠️ Touches orchestrator.py — coordinate with Phase 2.

- [x] 🟡 **A5 follow-up — replace head+tail truncation with LLM summarization** (revised from `Resolve_ContextOptimization.md`)
  The current `head + tail` split at `orchestrator.py:88-93` can produce invalid JSON. For tool outputs > 2KB, route through LLM summarisation via `_summarize_tool_result_with_llm()` **instead of** truncating. Falls back to current truncation on summariser failure. Error envelopes (`status: "error"`) skip the LLM path so the model gets exact error text for retry decisions. Tests in `test_remediation_phase2.py::TestLlmTruncate` (4 tests) + `test_compaction.py` (4 tests).

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

### ✅ Track 5B — Retire `run_shell`; close the typed-tool gaps it papered over

**Revised scope** (replaces the original "sandbox `run_shell` in ACI" plan after the 14-conv DB audit; rationale captured in §5 2026-05-22 of DESIGN.md). The data showed `run_shell` was either being misused (Azure tool bypass, self-startup, test traffic) or hitting two specific typed-tool gaps. Closing those gaps + replacing the inline-command surface with a path-only `execute_script` covers every legitimate use without the ACI image/network/cold-start surface.

- [x] 🟡 **Add `read_file` tool** scoped to `output/` — symmetric with `generate_file`. Read-only inside the sandbox; no approval needed. Uses the same `_DANGEROUS_PATTERNS` + `Path.resolve().relative_to(sandbox)` defence-in-depth as §5 2026-05-15 "Output sandbox defense-in-depth." Closes the "model wrote a file, now needs to read it back" gap that conv 257 hit.
- [x] 🟡 **Add `body_file` parameter to `az_rest_api`** — accepts a path under `output/`, resolves it server-side, forwards as `az rest --body @<abs_path>`. Mutually exclusive with `body`. Pre-validates JSON before the az call so the model gets a clear error instead of az's generic "Invalid JSON body." Closes the conv 257 Logic App PATCH cascade.
- [x] 🟡 **Replace `RunShellTool` with `ExecuteScriptTool`** (`app/tools/generic/execute_script.py`). Path-only, must resolve under `output/scripts/`. Shell inferred from extension (`.ps1` → PowerShell, `.sh` → bash). No `command:` parameter, no `args:` parameter (deferred — observed scripts were self-contained). Still `requires_approval=True`. Streaming + non-streaming variants. Env-allowlist + `shell=False` like the §5 2026-05-21 `_run_az` hardening.
- [x] 🟢 **Delete `RunShellTool`** source (`app/tools/generic/shell.py`), registry entry, and its tests. Replace test fixtures using `"run_shell"` with `"execute_script"` across `test_agent.py`, `test_streaming.py`, `test_compaction.py`, `test_db_models.py`, `test_learnings.py`, `test_learnings_api.py`, `test_rbac.py`, `test_new_tools.py`, `test_tools.py`, plus the frontend `chat.test.ts`, `types.test.ts`, `useAppStore.test.ts`, `ApprovalCard.test.tsx`, `ChatWindow.test.tsx`. Update `ApprovalCard.tsx` + `ToolCallCard.tsx` `formatCommand` to render the script path instead of an inline command.
- [x] 🟢 **Update skill allowlists** — `chat-with-kb` (Engineer) and `architect` SKILL.md frontmatter swap `run_shell` for `execute_script` + `read_file`; in-prose tool guides updated. `app/auth/rbac.py` engineer + architect tool lists updated.
- [x] 🟢 **Update DESIGN.md** §2 Tools table and add §5 2026-05-22 entry recording the decision and the audit that drove it.

**ACI sandbox dropped** — the perimeter-style protection it offered is replaced by structural impossibility: the model cannot pass an inline command, cannot bypass `az_cli` by running `az` directly from a string, and cannot self-deploy Nexus. If a future use case genuinely needs arbitrary shell, it surfaces as a typed-tool gap and gets closed deliberately rather than smuggled in as "ACI sandbox is safer."

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
