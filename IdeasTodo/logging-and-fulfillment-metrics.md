# Logging and Fulfillment Metrics

**Status**: Plan drafted 2026-05-21. Implementation not started.
**Discussed via**: `/grill-with-docs` session 2026-05-21
**Related**:
- [DESIGN.md §6 Operations](../Documentation/DESIGN.md#6-operations) — current logging footprint
- [DESIGN.md §4 Data model](../Documentation/DESIGN.md#4-data-model) — schema additions
- [DESIGN.md §5 2026-05-17 RBAC](../Documentation/DESIGN.md#5-decision-log) — role model reused
- [DESIGN.md §5 2026-05-20 architect-gated admin](../Documentation/DESIGN.md#5-decision-log) — precedent for `/admin/*` pattern

---

## 1. Problem

Nexus today emits a JSON-structured log line per tool call and tracks six Prometheus counters in [main.py:27-32](../backend/app/main.py#L27-L32), but has **no queryable analytical store** over tool usage, command shapes, Turn-level outcomes, or user satisfaction. Architects cannot answer:

- Which tools fail most? Under which Skill?
- What are the most-run `az` subcommands? `run_shell` verbs?
- Which Conversations completed cleanly vs hit max iterations?
- Are users actually getting what they want?

The `messages` table already stores tool args (`tool_calls_json`) and outputs (tool-role `content`), so the raw forensic data exists — but it is not indexed for analytical query and casual access would be a credential-exposure risk.

---

## 2. Decisions (locked 2026-05-21)

| # | Decision | Notes |
|---|---|---|
| 1 | **Storage**: new DB tables in `app.db` | Rejected JSONL on disk (harder to join with Conversation state) and Azure Application Insights (new external dep + cost + sensitive data leaving the box) |
| 2 | **`tool_executions` is a projection, not a content store** | FK to `messages.id`; tool result body stays in `messages.content` (no duplication) |
| 3 | **Redact-at-write, strict per-tool allowlist** | Unknown args become `[REDACTED]`; no regex scrubbing |
| 4 | **Tool scope (allowlist)**: `az_*`, `run_shell`, `generate_file`, all diagram tools, `web_*`, `fetch_ms_docs`, `ask_user` | KB / read tools (`read_kb_file`, `search_kb*`) excluded by default. `LOG_TOOL_EXECUTIONS` env var allows operational override |
| 5 | **Phase 1**: proxy signals only — `tool_executions` + `turn_outcomes`. Admin API **and** admin UI both ship in Phase 1 | No LLM judge in Phase 1 |
| 6 | **Phase 2**: per-Conversation 3-state feedback (😊 😐 😢) via navigate-away modal | New `conversation_feedback` table |
| 7 | **Phase 3** (deferred): LLM judge for retrospective fulfillment scoring | Revisit only if Phase 1+2 leave clear blind spots |
| 8 | **Admin surface**: reuse `architect` role, no new `superadmin` | New `/admin/insights` page + `/api/admin/insights/*` routes |
| 9 | **Retention**: `tool_executions` 180 days; `turn_outcomes` and `conversation_feedback` forever; all survive Conversation delete | Audit trail; periodic sweeper |
| 10 | **Prometheus**: unchanged | New tables are the analytical store; existing counters remain the live-dashboard store |
| 11 | **Feedback modal triggers**: sidebar Conversation click, new Conversation button, logout | NOT browser tab close (unreliable), NOT idle timeout |
| 12 | **Feedback gating**: only prompt if Conversation has ≥ 2 user messages | Prevents annoying prompts on abandoned Conversations |
| 13 | **Feedback re-prompt**: never auto-re-prompt; show current rating in Conversation footer, click to change | |
| 14 | **Feedback dismissal**: dismissable; closing without rating defaults to `neutral` | Simpler than tracking a separate `dismissed` state |
| 15 | **Settings toggle**: `users.preferences_json` JSON column carries `feedback_prompt_enabled` flag | Settings page itself is **out of scope** — only the toggle stub lands in Phase 2. See §7 Parking |

---

## 3. Proposed GLOSSARY additions

Two new terms — to be added to [Documentation/GLOSSARY.md](../Documentation/GLOSSARY.md) "Language" section as part of Phase 1.

```
| **Turn** | One user message plus all the assistant + tool messages produced
in response to it, up to the next user message. A Conversation is a sequence
of Turns; each Turn contains 1 or more Messages. The orchestrator's main loop
services one Turn per invocation, iterating its inner loop (max 15) until the
assistant terminates without further tool calls. | "round", "exchange",
"iteration" (iteration is the inner-loop counter inside a Turn, not the Turn
itself) |

| **Conversation feedback** | A user-supplied rating (happy / neutral / sad)
attached to an entire Conversation, captured via a dismissable prompt when
the user navigates away from that Conversation. One row per
(Conversation, User) pair; re-rating updates in place. Distinct from the
per-Turn proxy signals in `turn_outcomes`, which are derived automatically. |
"thumbs", "rating", "satisfaction score" |
```

---

## 4. Schema

### 4.1 New tables

```sql
-- Tool-level analytics projection. Result body NOT duplicated; FK to messages.
CREATE TABLE tool_executions (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,                   -- FK to messages (tool-role result row)
  tool_call_id TEXT,                             -- matches messages.tool_call_id
  tool_name TEXT NOT NULL,
  outcome TEXT NOT NULL,                         -- ok | error | approval_denied | timeout
  error_class TEXT,                              -- exception class name, or null
  duration_ms INTEGER NOT NULL,
  args_summary TEXT,                             -- ≤ 200 chars, redacted per allowlist
  iteration_index INTEGER NOT NULL,              -- inner-loop counter within Turn
  retry_strategy INTEGER,                        -- 1 | 2 | 3 | null (not a retry)
  skill_name TEXT,                               -- snapshot from Conversation
  user_oid TEXT NOT NULL,                        -- for rollups; survives Conversation delete
  created_at DATETIME NOT NULL
);
CREATE INDEX idx_tool_exec_created ON tool_executions(created_at);
CREATE INDEX idx_tool_exec_tool_outcome ON tool_executions(tool_name, outcome);

-- Per-Turn fulfillment proxies. Written at `done` SSE event time.
CREATE TABLE turn_outcomes (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  user_message_id INTEGER NOT NULL,              -- user message that opened the Turn
  final_assistant_message_id INTEGER,            -- last assistant message; null if hard-error
  completed_cleanly INTEGER NOT NULL,            -- bool: reached done without error
  error_class TEXT,                              -- top-level error class, or null
  iterations_used INTEGER NOT NULL,              -- inner-loop count, ≤ 15
  tool_failures INTEGER NOT NULL,                -- count of tool_executions.outcome='error' this Turn
  user_followed_up_within_min INTEGER,           -- minutes until user's next message; null if none
  user_followup_looked_like_rephrase INTEGER,    -- bool; >50% word overlap or negative cue
  duration_sec REAL NOT NULL,
  token_prompt INTEGER,                          -- from done event usage payload (§5 2026-05-18)
  token_completion INTEGER,
  token_cached INTEGER,
  skill_name TEXT,
  user_oid TEXT NOT NULL,
  created_at DATETIME NOT NULL
);
CREATE INDEX idx_turn_created ON turn_outcomes(created_at);
CREATE INDEX idx_turn_skill ON turn_outcomes(skill_name);

-- Per-Conversation explicit feedback via navigate-away modal.
CREATE TABLE conversation_feedback (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  user_oid TEXT NOT NULL,
  rating TEXT NOT NULL,                          -- happy | neutral | sad
  comment TEXT,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE(conversation_id, user_oid)
);
```

### 4.2 Existing-table modifications

```sql
-- Phase 2: user preferences (carries feedback_prompt_enabled, future settings flags)
ALTER TABLE users ADD COLUMN preferences_json TEXT;
```

All schema changes land via `_apply_lightweight_migrations` in [main.py](../backend/app/main.py) (per §5 2026-05-15 single-schema-migration-path).

---

## 5. File changes

### 5.1 Backend

| File | Change |
|---|---|
| [app/db/models.py](../backend/app/db/models.py) | Add `ToolExecution`, `TurnOutcome`, `ConversationFeedback` SQLModel classes; add `preferences_json` to `User` |
| [app/main.py](../backend/app/main.py) `_apply_lightweight_migrations` | Add `ALTER TABLE` for `users.preferences_json`; `SQLModel.metadata.create_all` handles new tables |
| [app/main.py](../backend/app/main.py) `lifespan` | Add `_retention_sweeper` background task — `DELETE FROM tool_executions WHERE created_at < now − TOOL_EXECUTIONS_RETENTION_DAYS`. Default 180 days, runs every 6 h |
| [app/agent/orchestrator.py](../backend/app/agent/orchestrator.py) | Write `tool_executions` row near the existing TELEMETRY line at ~1202; write `turn_outcomes` row just before final `done`-event yield. Hook should already have access to iterations_used, error state, token usage |
| `app/agent/redaction.py` *(new)* | Per-tool allowlist redaction. Returns `args_summary` ≤ 200 chars. Tests: `tests/test_redaction.py` |
| `app/api/admin_insights.py` *(new)* | Read-only routes gated by `require_architect`: `GET /api/admin/insights/tool-stats`, `/turn-stats`, `/feedback-summary`, `/top-commands` with date-range + Skill + tool filters |
| `app/api/feedback.py` *(new)* | `POST /api/conversations/{id}/feedback`, `GET /api/conversations/{id}/feedback`. Upserts on `UNIQUE(conversation_id, user_oid)` |
| `app/api/preferences.py` *(new, stub)* | `GET /api/users/me/preferences`, `PATCH /api/users/me/preferences`. Minimum needed for Phase 2 to read `feedback_prompt_enabled` |
| [app/auth/rbac.py](../backend/app/auth/rbac.py) | Add `/admin/insights` to architect role's allowed paths |
| [app/config.py](../backend/app/config.py) | `LOG_TOOL_EXECUTIONS` (CSV allowlist, default = curated set in §2 row 4); `TOOL_EXECUTIONS_RETENTION_DAYS` (default 180); `FEEDBACK_PROMPT_MIN_USER_MESSAGES` (default 2) |

### 5.2 Frontend

| File | Change |
|---|---|
| `src/pages/AdminInsightsPage.tsx` *(new)* | Tabbed view: Tool usage (top tools, error rate, top commands), Turn outcomes (clean-completion rate, iterations distribution), Feedback summary (rating distribution by Skill, over time). Date-range + Skill filter |
| `src/components/FeedbackModal.tsx` *(new)* | 3-smiley picker (😊 😐 😢), optional comment, dismissable. Closing without rating writes `neutral` |
| `src/components/ConversationFooter.tsx` *(new)* | Shows current rating + click-to-change; visible only when a rating exists |
| `src/store/useAppStore.ts` | Detect Conversation switch + new-Conversation + logout intent. Trigger modal if all of: ≥ 2 user messages, no existing rating, not dismissed in session, `feedback_prompt_enabled === true` |
| `src/api/admin.ts` *(new)* | Wrappers for `/api/admin/insights/*` |
| `src/api/feedback.ts` *(new)* | `getFeedback`, `postFeedback` |
| `src/api/preferences.ts` *(new, stub)* | `getPreferences`, `patchPreferences` |
| `src/pages/SettingsPage.tsx` *(new, stub)* | Single toggle "Show feedback prompt when leaving a conversation." Full settings page = separate work item (§7) |
| `src/App.tsx` | Add routes `/admin/insights` (architect-gated) and `/settings` |

### 5.3 Tests

- Backend: `test_admin_insights.py`, `test_feedback.py`, `test_redaction.py`, `test_tool_executions.py`, `test_turn_outcomes.py`, `test_retention_sweeper.py`
- Frontend: `FeedbackModal.test.tsx`, `AdminInsightsPage.test.tsx`, `ConversationFooter.test.tsx`

---

## 6. Phasing

### Phase 1 — Logging foundation (backend + minimal admin UI)

Goal: architects can answer "what's good, what needs improvement" via the admin page within 1 week of merging.

1. Schema — all three new tables + `users.preferences_json` column land together (cheaper than two migrations)
2. Redaction module + per-tool allowlist (with tests)
3. Orchestrator writes `tool_executions` and `turn_outcomes`
4. Retention sweeper background task
5. Admin API endpoints (architect-gated)
6. Admin UI page (read-only, charts + tables)

### Phase 2 — Conversation feedback

Goal: users contribute explicit satisfaction signal.

1. Frontend `FeedbackModal` + navigation-event detection in store
2. `POST/GET /api/conversations/{id}/feedback`
3. `ConversationFooter` for click-to-change
4. Stub settings page with the single toggle
5. Admin UI feedback section becomes meaningful as data arrives

### Phase 3 — DEFERRED

LLM judge for retrospective fulfillment scoring. Revisit after 3 months of Phase 1+2 data. Same shape as [learn_judge.py](../backend/app/agent/learn_judge.py), fails-closed.

---

## 7. Parking — out of scope for this work

- **Full settings page**: user has more items planned. Only the `feedback_prompt_enabled` toggle stub lands in Phase 2. Separate brainstorm session needed before that page is built out.
- **Output truncation in `messages.content`**: independent decision, separately impacts DB size and compaction quality. Worth its own §5 entry. Park.
- **Tab-close / idle-timeout feedback capture**: explicitly skipped due to `beforeunload` unreliability.
- **Phase 3 LLM judge**: deferred until proxies + explicit feedback prove insufficient.
- **`messages.tool_calls_json` security audit**: existing table already contains raw forensic args including potential secrets (pre-existing). Worth a separate security review — not blocked by this work, not blocking this work.

---

## 8. Open follow-ups / unknowns to resolve at implementation time

- **`turn_outcomes` write site**: cleanest hook is just before the final `yield done_event(...)` in `orchestrator.handle_chat`. Confirm during implementation that all fields (token usage, final_assistant_message_id, error state) are in scope there.
- **`user_followup_looked_like_rephrase` heuristic**: agreed criteria are >50% word overlap OR negative-cue regex ("no", "that's not", "wrong", "try again", "doesn't work"). Implementation should pick one of (`difflib.SequenceMatcher` ratio | pure token overlap | both) and document the choice in code.
- **Admin UI charting library**: existing frontend = React 19 + Tailwind v4. Pick between Recharts / Chart.js / hand-rolled SVG during Phase 1.
- **Initial Skill snapshot field on `tool_executions` / `turn_outcomes`**: source is `Conversation.skill_snapshot_json['name']` (frozen at Conversation creation per the invariant). Confirm column population path.

---

## 9. Proposed §5 DESIGN.md entries (drafts for the engineer who ships each phase)

Per [DECISION-LOG-FORMAT.md](../.claude/commands/DECISION-LOG-FORMAT.md). Do NOT commit these mid-conversation; paste them in the PR that ships each phase.

### Phase 1 draft

> ### 2026-XX-XX — Analytical store for tool executions and Turn outcomes
>
> Added three DB tables (`tool_executions`, `turn_outcomes`, `conversation_feedback`) to answer architectural questions about tool usage, failure modes, and Conversation-level satisfaction. `tool_executions` is a projection — args (redacted) and outcome are stored, but the result body stays in `messages.content` reachable via FK. Rejected JSONL on disk (harder to join with Conversation state) and Azure Application Insights (new external dep + cost + sensitive data leaving the box). Redact-at-write with a strict per-tool allowlist — unknown args become `[REDACTED]` — so the new table is no more sensitive than the existing `messages.tool_calls_json`, which retains the raw forensic detail.
> **Trade-off**: schema commitment (hard to reverse — DB schema is an invariant). 180-day retention on `tool_executions` keeps storage bounded (~75 MB at projected scale). The redaction policy loses forensic fidelity on `run_shell` argument bodies — full bodies remain in `messages.content` for one-off forensics, but you can't `WHERE args LIKE '%X%'` against them analytically.

### Phase 2 draft

> ### 2026-XX-XX — Per-Conversation 3-state feedback via navigate-away modal
>
> Captured explicit user satisfaction via `conversation_feedback` (happy / neutral / sad) prompted when the user leaves a Conversation. Per-Conversation, not per-Turn, because users will not rate 12 turns individually; per-Turn explicit feedback was rejected as a friction-vs-signal tradeoff (per-Turn proxy signals in `turn_outcomes` already cover automated diagnostics). Modal triggers on sidebar nav + new-Conversation + logout — not `beforeunload` (unreliable across browsers, rage-quit UX). Dismissal without rating defaults to `neutral`. Gated to Conversations with ≥ 2 user messages.
> **Trade-off**: if a 12-Turn Conversation gets `sad`, the system cannot pinpoint *which* Turn caused it — only the overall verdict. The `turn_outcomes` proxy signals (clean-completion, max-iterations-hit, tool failures) fill that gap automatically without user input. The settings-page toggle to disable the modal lands stubbed; the full settings page is separate work.

---

## 10. Changelog of this document

| Date | Change |
|---|---|
| 2026-05-21 | Initial draft from `/grill-with-docs` session |
