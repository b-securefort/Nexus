# Nexus Agent — Additional Findings & Corrections

**Source**: Second-opinion review of [ArchitecturalReview.md](./ArchitecturalReview.md) and all resolution documents, validated against actual codebase.  
**Date**: May 2026

---

## Part A: Corrections to Existing Resolution Documents

### A1. Database Persistence — Azure Managed Disk on Container Apps (Chosen)

**File**: [Resolve_DatabasePersistence.md](./Resolve_DatabasePersistence.md)  
**Original recommendation**: Migrate to Azure SQL  
**Problem with recommendation**: `sqlite-vec` (`kb_chunks_vec`, `agent_learnings_vec`) and `FTS5` (`kb_chunks_fts`) have no clean equivalent in Azure SQL. The review's fallback of "store vectors as `varbinary` and calculate cosine similarity in Python" would be ~100x slower than sqlite-vec's native ANN index at 1000+ chunks × 1536 dims.

**Decision: Use Azure Managed Disk on Azure Container Apps.**

#### Why this works

Azure Container Apps supports three storage types:

| Storage Type | SQLite WAL Safe? | Persistent? | Constraint |
|---|---|---|---|
| Ephemeral (container filesystem) | ✅ Yes (local disk) | ❌ Lost on restart | Useless for a database |
| Azure Files (SMB mount) | ❌ No — WAL corruption | ✅ Yes | **This is the current problem** |
| **Azure Managed Disk** | ✅ Yes (block storage) | ✅ Yes | **Single replica only** |

The single-replica constraint is acceptable because the Nexus architecture already requires single-instance:
- In-memory `_approval_events` / `_approval_results` dicts (approvals.py)
- `threading.Lock` on the KB reindexer (DESIGN.md §6)
- Global `_tool_call_history` for rate limiting (orchestrator.py)
- `_az_circuit_breaker_tripped` global flag (base.py)

Multi-instance would break all of these regardless. The managed disk constraint costs nothing we don't already pay.

#### Setup (Bicep)

```bicep
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  properties: {
    template: {
      volumes: [
        {
          name: 'sqlitedata'
          storageType: 'AzureDisk'
          mountOptions: 'uid=1000,gid=1000'  // match container user
        }
      ]
      containers: [
        {
          name: 'nexus-backend'
          volumeMounts: [
            {
              volumeName: 'sqlitedata'
              mountPath: '/data'
            }
          ]
        }
      ]
    }
  }
}
```

#### Config change

```env
DATABASE_URL=sqlite:////data/app.db
```

#### Backup strategy

The existing `_backup_loop()` in `main.py` uses `sqlite3.backup()` to snapshot `app.db`. Update the backup path to write to Azure Blob Storage (the `BACKUP_AZURE_STORAGE_CONNECTION_STRING` config already exists but the upload logic is a TODO at `main.py:346`).

#### Future path

| Approach | Effort | SQLite-safe | Multi-replica | Backup story |
|---|---|---|---|---|
| **Azure Managed Disk** ✅ chosen | Low (infra only) | ✅ | ❌ Single replica | `sqlite3 .backup` → Azure Blob |
| Litestream on ephemeral | Medium | ✅ | ❌ Single writer | Continuous replication to Blob |
| PostgreSQL + pgvector | High (code rewrite) | N/A | ✅ | Azure-managed PITR |

When multi-instance becomes a real requirement (not before), migrate to **PostgreSQL Flexible Server + pgvector** — not Azure SQL. pgvector has native IVFFlat/HNSW ANN indexes and PostgreSQL's `tsvector`/`tsquery` maps more cleanly to the existing FTS5 patterns.

---

### A2. Concurrency Exhaustion — Targeted Fix, Not Full Rewrite

**File**: [Resolve_ConcurrencyExhaustion.md](./Resolve_ConcurrencyExhaustion.md)  
**Original recommendation**: Full async rewrite of orchestrator, streaming, and tool interfaces  
**Problem with recommendation**: `GitPython`, `msal`, `subprocess.run()`, and SQLite are all synchronous — they'd still need `to_thread()` wrapping even after a full async migration. The effort/reward ratio is poor.

**Revised recommendation:**

1. **Immediate (1 hour):** Set a dedicated executor with explicit `max_workers`:
   ```python
   from concurrent.futures import ThreadPoolExecutor
   _tool_executor = ThreadPoolExecutor(max_workers=64, thread_name_prefix="tool")
   # Use: loop.run_in_executor(_tool_executor, ...)
   ```

2. **Immediate (2 hours):** Add per-user concurrency semaphore to prevent one power user from consuming all threads:
   ```python
   _user_semaphores: dict[str, asyncio.Semaphore] = {}
   def _get_user_semaphore(user_oid: str, max_concurrent: int = 4) -> asyncio.Semaphore:
       if user_oid not in _user_semaphores:
           _user_semaphores[user_oid] = asyncio.Semaphore(max_concurrent)
       return _user_semaphores[user_oid]
   ```

3. **Short-term:** Port only `subprocess.run()` calls in `AzureToolBase._run_az()` and `RunShellTool` to `asyncio.create_subprocess_exec` — these are the actual long-running blocking calls.

4. **Leave as-is:** The OpenAI streaming path (already uses queue pattern correctly), SQLite operations, GitPython, MSAL.

---

### A3. Execution Sandbox — Strip Environment Before Container Isolation

**File**: [Resolve_ExecutionSandbox.md](./Resolve_ExecutionSandbox.md)  
**Original recommendation**: Ephemeral sandbox container  
**What's missing**: The most immediate vulnerability isn't `run_shell` (which already strips the env) — it's `_run_az()` which leaks the full server environment. See **B1** below for the fix that should ship before any container work.

---

### A4. Stateful Approvals — Lease-Based Recovery, Not Full Stateless

**File**: [Resolve_StatefulApprovals.md](./Resolve_StatefulApprovals.md)  
**Original recommendation**: Fully stateless orchestrator with DB-persisted context  
**Problem with recommendation**: The orchestrator maintains five pieces of mutable in-memory state (`messages` list, `failure_tracker`, `failure_history`, `drawio_attempt_count`, `pending_render_attachments`) that are expensive to serialize/deserialize correctly — especially the synthetic retry strategy messages.

**Revised recommendation — lease-based heartbeat:**
1. The orchestrator sends heartbeats every 30s to a `conversation_leases` row in SQLite.
2. Frontend polls and detects stale leases (>60s). Shows: *"The agent may have crashed. Click to restart this turn."*
3. Clicking creates a **new** chat turn from the user's last message — the agent replays from clean state.
4. No need to reconstruct synthetic retry/drawio state. The worst case is losing one partial turn — the user sees the last completed response and can retry.

---

### A5. Context Optimization — Parallel Execution Is the Real Gap

**File**: [Resolve_ContextOptimization.md](./Resolve_ContextOptimization.md)  
**What's already solved**: `_TOOL_RESULT_LIMITS` truncation (4-6KB per tool), drawio XML stripping, compaction module.  
**What the review missed**:

1. **Truncation splits mid-JSON.** The `head + tail` split at `orchestrator.py:88-93` can produce invalid JSON fragments. The `gpt-4o-mini` summarization idea is correct but should be framed as **replacing** truncation, not adding on top.

2. **Parallel tool execution** is the higher-value item. The orchestrator at `orchestrator.py:911` iterates `for call in tool_calls:` **sequentially**. When the model emits 3+ read-only tool calls, they run one after another. Fix:
   ```python
   # Group calls: approval-required vs safe
   safe_calls = [c for c in tool_calls if not _tool_needs_approval(tool, args)]
   # Execute safe calls with asyncio.gather()
   results = await asyncio.gather(*[execute(c) for c in safe_calls])
   ```

---

### A6. Learning Module — Derive + Rephrase + Keep Both

**File**: [LearningModuleImprovements.md](./LearningModuleImprovements.md)  
**Original recommendation (Item 2)**: "Agent Proposes, Judge Disposes" — allow agent to write learning text  
**Problem**: Re-opens the memory poisoning vector that was structurally closed by removing `update_learnings` tool. The LLM judge is not infallible.

**Revised recommendation — Derive + Rephrase + Dual Storage:**

```
Orchestrator detects success-after-failure
        │
        ▼
derive_learning_from_success()    ← Current code: extracts raw factual skeleton
        │                            (failing args, error message, working args)
        ▼
NEW: rephrase_learning()          ← gpt-4o-mini call with constrained prompt:
        │                            "Rewrite as a concise tip. Only describe
        │                             what happened and what fixed it.
        │                             No opinions, no framing."
        ▼
3-gate defense pipeline           ← regex → name guard → LLM judge (unchanged)
        │
        ▼
Store in agent_learnings:
  summary = rephrased version     ← What the agent sees in system prompt
  details = raw derived facts     ← Ground truth for audit + admin review
```

**Why this is safe:**
- Content source is still the orchestrator's tracked state (bounded by observable args/errors)
- The rephrasing LLM is a **separate** call (not the agent) with a system prompt that prohibits opinions
- 3-gate defense still runs on the rephrased output as a final check
- If the rephrase call fails/times out, fall back to raw derived output (current behavior, no degradation)
- Raw facts are **always preserved** in `details` regardless of what happens to `summary`
- Architects can compare `summary` vs `details` in the admin UI and manually correct bad rephrases

**Zero schema changes** — just changing what goes into each existing column.

---

## Part B: Newly Discovered Critical Issues

### B1. 🔴 `_run_az()` Leaks Full Server Environment to Subprocesses

**File**: `backend/app/tools/base.py:214`  
**Severity**: Critical — credential exfiltration  

**Problem:**
```python
# base.py L214 — AzureToolBase._run_az()
env = os.environ.copy()  # ← FULL environment: AZURE_OPENAI_API_KEY, KB_REPO_PAT, etc.
```

Every Azure tool call (`az_cli`, `az_resource_graph`, `az_cost_query`, `az_monitor_logs`, `az_rest_api`, `az_advisor`, `az_policy_check`, `az_devops`) inherits the complete server environment including all secrets. Meanwhile, `run_shell` at `shell.py:120-124` correctly strips to a minimal set.

An LLM-crafted `az` argument could exfiltrate secrets through error messages, `--query` output, or `--debug` tracing.

**Fix (5 lines):**
```python
env = {
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", ""),
    "AZURE_CONFIG_DIR": os.environ.get("AZURE_CONFIG_DIR", ""),
    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # Windows requires this
}
arm_token = _current_arm_token.get()
if arm_token:
    env["AZURE_ACCESS_TOKEN"] = arm_token
```

---

### B2. 🔴 `shell=True` on Windows Enables `%VAR%` Expansion

**File**: `backend/app/tools/base.py:226`  
**Severity**: Critical (compounded by B1)  

**Problem:**
```python
shell=(sys.platform == "win32"),  # cmd.exe interprets %VARIABLE% in arguments
```

On Windows, `_run_az()` uses `shell=True`, which routes through `cmd.exe`. The injection guard (`check_shell_injection`) only blocks backtick and NUL — `%` is not blocked. So an LLM-crafted argument like `%AZURE_OPENAI_API_KEY%` would be expanded by cmd.exe before `az` sees it.

**Fix:** Fixing B1 (stripping the env) neutralizes this — expanded vars resolve to empty strings. Belt and suspenders: also add `%` to `_SHELL_METACHAR_PATTERN`:
```python
_SHELL_METACHAR_PATTERN = r'[`%\x00]'
```

---

### B3. 🔴 ARM Token Expires Mid-Turn (Silent RBAC Downgrade)

**File**: `backend/app/agent/orchestrator.py:708`  
**Severity**: Critical — security/compliance  

**Problem:** The frontend sends the ARM token once at the start of a chat request. Azure AD tokens last ~60-75 minutes. A tool-heavy turn with approval waits (up to 10 min each), retries, and LLM judge calls can exceed the token lifetime. After expiry, every Azure tool call **silently falls back** to server-side credentials — the user's RBAC context disappears with no warning.

**Fix options:**

| Option | Effort | Description |
|---|---|---|
| **A. Expiry check before each az call** | Low | Decode the JWT `exp` claim in `_run_az()`. If expired, return an error telling the agent the token needs refresh. |
| **B. SSE refresh event** | Medium | Emit a `token_refresh_required` SSE event when the token is within 5 min of expiry. Frontend acquires a fresh token and sends via `POST /api/chat/refresh-token`. Backend updates the `ContextVar`. |

**Recommended**: Start with Option A (immediate safety), then add Option B for seamless UX.

---

### B4. 🟡 Global Mutable `_tool_call_history` — Thread-Unsafe, Cross-User

**File**: `backend/app/agent/orchestrator.py:637`  
**Severity**: Moderate  

**Problem:**
```python
_tool_call_history: dict[str, list[float]] = {}
```
Module-level mutable dict for rate limiting. Issues:
- Not thread-safe (concurrent mutations from `to_thread()` workers, no lock)
- Rate limits are shared across ALL users — one power user can rate-limit everyone else
- Dict keys (tool names) are never removed — unbounded growth

**Fix:** Replace with a per-user, thread-safe structure:
```python
import threading
_rate_lock = threading.Lock()
_tool_call_history: dict[str, dict[str, list[float]]] = {}  # user_oid -> tool -> timestamps
```

---

### B5. 🟡 No Circuit Breaker for Azure OpenAI

**File**: `backend/app/agent/orchestrator.py`  
**Severity**: Moderate  

**Problem:** The `_find_az()` circuit breaker at `base.py:54-78` is well-designed. But there's no equivalent for the Azure OpenAI API. If the endpoint goes down:
- Every chat request opens a connection and waits for httpx timeout (~30-120s)
- The learn judge has a 10s timeout but the main completion call has none set
- Compaction's synchronous LLM calls have no timeout
- `/healthz` still returns 200 — system appears healthy while functionally dead

**Fix:** Add a lightweight health probe and circuit breaker:
- Periodic heartbeat call (e.g., `completions.create` with `max_tokens=1` every 60s)
- Track consecutive failures; after N failures, short-circuit chat requests with a clear error
- Include OpenAI reachability in `/healthz` response

---

### B6. 🟡 Compaction LLM Calls Block First Response

**File**: `backend/app/agent/compaction.py:97-106`  
**Severity**: Moderate  

**Problem:** `_summarize_long_paste()` and `_describe_image()` are synchronous LLM calls during `load_compacted_history()`, which runs on every chat turn. If a conversation has uncached long pastes or images, the user sees dead time (3-5s per call) with no feedback before the first response token streams.

**Fix:** Run uncached compression calls in a background task and use the raw content for the current turn. The cached summaries will be ready for the next turn.

---

### B7. 🟡 `CHAT_RATE_LIMIT_PER_MINUTE` Is Not Enforced

**File**: `backend/app/config.py:130`  
**Severity**: Moderate  

**Problem:** `CHAT_RATE_LIMIT_PER_MINUTE: int = 30` exists in config but no middleware or dependency enforces it anywhere in the codebase. The setting implies protection that doesn't exist.

**Fix:** Either implement rate limiting middleware (e.g., `slowapi` or a custom FastAPI dependency) or remove the config to avoid false confidence.

---

### B8. 🟢 SQL Injection Pattern in Learnings Retrieval

**File**: `backend/app/agent/learnings.py:357, 398, 432`  
**Severity**: Low (safe today, maintenance hazard)  

**Problem:** String-interpolated SQL placeholders: `f"WHERE id IN ({placeholders})"`. Currently safe because values are always integers from sqlite-vec rowids. But the pattern is a maintenance trap for future developers.

**Fix:** Use parameterized queries or SQLAlchemy's `in_()` operator.

---

### B9. 🟢 Conversation Deletion Doesn't Clean Orphaned Files

**File**: `backend/app/api/conversations.py`  
**Severity**: Low  

**Problem:** When a conversation is deleted, uploaded images in `UPLOAD_DIR` and generated files in `output/` are not cleaned up. Slow disk space leak over time.

**Fix:** Add cleanup logic to the conversation delete endpoint that removes associated upload files.

---

### B10. 🟢 `ContextVar` + `run_in_executor` Propagation Gap

**File**: `backend/app/tools/base.py:22`, `backend/app/agent/orchestrator.py:787`  
**Severity**: Low (latent, no impact today)  

**Problem:** `asyncio.to_thread()` copies `ContextVar` values (uses `copy_context()`), but `loop.run_in_executor()` does not in all Python versions. The OpenAI streaming at L787 uses `run_in_executor` — if any future code inside that callback accesses `get_arm_token()`, it would get `None`.

**Fix:** Use `asyncio.to_thread()` consistently, or explicitly wrap in `copy_context().run()`.
