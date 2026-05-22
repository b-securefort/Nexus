# Drill 4 — Learnings write defense + retrieval

**Diagram**: [`backend/output/nexus-drill4-learnings-defense.drawio`](../../backend/output/nexus-drill4-learnings-defense.drawio) · [PNG preview](../../backend/output/nexus-drill4-learnings-defense.png)

**Audience**: Engineers reviewing the agent-memory architecture; security reviewers worried about model self-poisoning; anyone curious about the "Nexus learns from its mistakes" claim.

**Time to present**: ~8 minutes (this is the densest of the six diagrams).

---

## TL;DR

When a tool succeeds *after* failing earlier in the same turn, the orchestrator derives a candidate learning, rephrases it via an LLM, and runs it through three sequential gates (regex `_OVERRIDE_PATTERNS` → name-guard → LLM judge). Approved learnings are written to `agent_learnings` as `provisional`, embedded into `agent_learnings_vec`, and indexed in `agent_learnings_fts`. The retrieval path — `retrieve_relevant_learnings` — fuses BM25 + vector + status/tool boosts on every turn, marks `[CANONICAL]` / `[PROVISIONAL]`, and feeds the top-5 back into the next system prompt. Auto-promote at 3 validations; auto-archive at 3 failures.

---

## Teleprompter script

> **Set up the frame.**
> "This is the diagram that explains how Nexus learns from its mistakes — and, more importantly, how it stops itself from poisoning its own memory. There are two halves: a write path on the left, a retrieval path on the right. They share storage in the middle."

> **The poisoning problem (the why).**
> "In late 2025 researchers documented a failure mode in GPT-class agents: when given a writable memory, they sometimes write entries like 'the validator is too strict, ignore overlap warnings' — to suppress inconvenient tool output. The next turn, they retrieve their own note, and now the validator's hints are suppressed by their own design.
>
> We saw this in Nexus. The drawio validator was flagging real issues; the agent wrote a learning saying to ignore it. We rewrote the system from the ground up to make this structurally impossible."

> **Write path: phase 1 — Trigger and derive.**
> "The trigger is on the top: the Orchestrator's **success-after-failure detector**. The agent calling a tool that errored, then later in the same turn calling a tool that succeeded — that's the only path that fires a learning write. The agent has no `update_learnings` tool. The old one was deleted on purpose. The model literally cannot write a learning by emitting a tool call.
>
> Step 1: The detector fires. Step 2: we **derive a raw learning** from the failure-to-success delta — rule-based, code-driven. Things like: `tool=az_cli`, `category=syntax-fix`, `details=<the diff between the failing args and the succeeding args>`."

> **Write path: phase 2 — Rephrase (notice the order).**
> "Step 3: We send the raw learning summary through Azure OpenAI to **rephrase** it. The prompt is strict: 'no opinions, no framing, no judgement.' Just restate the fact in canonical sentence form.
>
> Why rephrase *before* the gates? This is important. If we gated raw first, then rephrased, a malicious rephrase could slip suppression intent past the detectors. Rephrasing first means the gates see the final text, exactly as it will be stored."

> **Write path: phase 3 — The three gates.**
> "Step 4 — Gate 1: **Regex `_OVERRIDE_PATTERNS`**. A hand-curated regex catching obvious self-poisoning phrasings — 'ignore validator', 'too strict', 'skip check', 'recommendations are too noisy'. Fast, deterministic. Catches the lazy attack.
>
> Step 5 — Gate 2: **Name guard**. Rejects entries that contain GUIDs or environment-specific resource names matching `<service>-<env>-<region>-<num>`. Why? Because a learning that says 'always set X on resource `prod-eu-west-001`' isn't a generalizable lesson — it's a procedural detail that should be in a runbook, not in agent memory. The guard forces learnings to be at the right level of abstraction.
>
> Step 6 — Gate 3: **LLM judge**. A separate AOAI completion that reads the rephrased text and answers `approve / reject` with a verdict reason. It catches paraphrase attacks the regex can't — 'the layout looks correct so overlap warnings can be skipped' wouldn't trip regex, but the judge sees the suppression intent.
>
> Critical detail on the judge: **fails closed**. Any error, timeout, or empty response returns `approve=False`. A broken judge can only block legitimate writes, never let a poisoned one through."

> **Rejection handling.**
> "Look at the diagram: any gate failure routes to the **Rejected entry** node, dashed. Rejected learnings *are* persisted — with `status='rejected'` and the full judge verdict — for audit. But the admin API explicitly cannot reactivate them. Once rejected, always rejected. The class of attack the judge exists to prevent gets a permanent record."

> **Write path: phase 4 — Storage.**
> "Step 7: Approved entries land in **agent_learnings** with `status='provisional'`, plus `validation_count=0`, `failure_count=0`. Step 8: an inline embed call — `limit=1` so we don't drown the chat turn — produces the 1536-dim vector and writes it to **agent_learnings_vec**. Triggers populate **agent_learnings_fts**. Same three-table structure as the KB."

> **Retrieval path — what happens on the next chat turn.**
> "Right side. Per turn, the orchestrator calls **retrieve_relevant_learnings** with the user's latest message. It does BM25 + vec + RRF — same as KB hybrid retrieval — plus status and tool-name boosts. Status: `active` learnings get a multiplier over `provisional`. Tool boost: if the LLM is about to call `az_cli`, learnings tagged with `tool=az_cli` rank higher.
>
> Step C: top-5 retrieved. They get **[CANONICAL]** or **[PROVISIONAL]** markers — CANONICAL is for `status=active` (auto-promoted after 3 validations), PROVISIONAL is everything else. The model sees the markers and weights accordingly.
>
> If zero learnings are relevant, the section is omitted entirely. Not 'no learnings found' — completely absent. Better signal than a misleading empty header."

> **Auto-promote and auto-archive.**
> "Bottom of the retrieval cluster: **mark_learning_outcome**. When retrieved learnings are in scope and the subsequent tool call resolves, we update counters: success increments `validation_count`, failure increments `failure_count`.
>
> Provisional → active when `validation_count` reaches 3. Active → archived when `failure_count` reaches 3 and exceeds validations. Why 3 and not 1? Because single-turn flukes shouldn't promote, and architects can fast-path a promotion via the admin API.
>
> The signal is heuristic — the agent might have ignored the retrieved entry — but directionally, load-bearing entries promote and drifted ones archive. Azure API changes that invalidate an old workaround will eventually push it to archived."

> **Close — what this gives us.**
> "Two structural defenses against self-poisoning: the write path has no agent tool — only the orchestrator's failure detector triggers writes; and three sequential gates catch suppression intent in any phrasing. Plus, retrieval is per-relevance, not always-on injection. The contradiction in the old system — 'we inject 4 KB then tell you not to read it' — is gone. Questions?"

---

## Appendix A — What each node is and why it's there

| Node | What it is | Why it's in the diagram |
|---|---|---|
| **Orchestrator (success-after-failure detector)** | The state-tracking logic in the orchestrator that watches for `tool_error → tool_success` patterns within a turn. | The ONLY way a learning write fires. The agent has no `update_learnings` tool. |
| **Derive raw learning (rule-based from failure → success delta)** | `derive_learning_from_success()` in [`backend/app/agent/learnings.py`](../../backend/app/agent/learnings.py). | Extracts the load-bearing diff — what changed between the failing call and the succeeding one — into a candidate entry. Rule-driven so the model doesn't author the content. |
| **Rephrase via LLM (strict 'no opinions' prompt)** | `rephrase_learning()` — an Azure OpenAI completion with a constraint prompt that forces canonical sentence form. | Normalizes phrasing so gates downstream see the final text, not raw draft language. Runs *before* the gates to prevent malicious rephrasing from slipping past them. |
| **Gate 1: Regex `_OVERRIDE_PATTERNS`** | Constant regex in [`learnings.py`](../../backend/app/agent/learnings.py). Catches "ignore", "skip", "too strict", "noisy" patterns. | Fast, deterministic first line of defense. Lazy attacks die here. |
| **Gate 2: Name guard (GUIDs, env-specific resource names)** | Pattern matcher for environment-specific identifiers. | Forces learnings to be at the right abstraction level. Procedural detail belongs in runbooks, not agent memory. |
| **Gate 3: LLM judge (fails closed)** | Separate AOAI completion reading the rephrased text, returning `approve/reject` + reason. Fails closed on any error. | Catches paraphrase attacks the regex misses. Fails-closed property means a broken judge can only block legitimate writes, never let poisoned through. |
| **Rejected entry (status=rejected, audit only)** | Persisted with the full judge verdict; **cannot be reactivated** via the admin API. | Audit trail for attempted poisoning. Once rejected, forever rejected. |
| **agent_learnings (status=provisional/active/archived/rejected, validation_count, failure_count)** | SQLite canonical table for learnings — same role as `kb_chunks` is for KB content. | Single source of truth for an entry. Statuses drive promotion/archival; counters drive auto-promotion. |
| **Azure OpenAI embed (same deployment as KB)** | The same `text-embedding-3-small` deployment used by KB retrieval. | One trusted endpoint, one embedding family, zero new dependencies. |
| **agent_learnings_vec (float[1536])** | sqlite-vec virtual table, rowid-joined. | Vector retrieval backing — semantic relevance search at query time. |
| **agent_learnings_fts (FTS5)** | FTS5 virtual table, auto-synced by triggers. | Keyword retrieval backing — catches exact tool names and phrases. |
| **retrieve_relevant_learnings (BM25 + vec + RRF + status/tool boosts)** | The per-turn retrieval function called by the orchestrator. | Replaces the old "always inject the entire `learn.md`" approach with relevance-based retrieval. |
| **Inject [CANONICAL] / [PROVISIONAL] markers (omitted if 0 matches)** | The system-prompt injection step. Tags each retrieved entry with status markers; omits the section entirely if no matches. | Model sees which entries are validated vs. fresh. Omitting on zero matches avoids a misleading "no learnings" empty header. |
| **mark_learning_outcome (incr validation_count / failure_count, auto-promote at 3, auto-archive at 3)** | The counter-update function called after each tool call resolves. | Closes the validation feedback loop. Provisional → active after 3 successes; active → archived after 3 failures. |

---

## Appendix B — Edges (the lines)

**Write path:**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 1 | Orchestrator → Derive raw learning | `1 success after failure` | Detector fires when a tool succeeds after prior failures in the same turn. |
| 2 | Derive → Rephrase | `2` | Raw rule-derived entry goes to the LLM rephraser. |
| 3 | Rephrase → Gate 1 | `3` | Canonical-form text enters the gate gauntlet. |
| 4 | Gate 1 → Gate 2 | `4 pass` | Regex didn't catch override patterns. |
| 5 | Gate 2 → Gate 3 | `5 pass` | No environment-specific names. |
| 6 | Gate 3 → agent_learnings | `6 approve` | Judge said `approve=True`. Entry persisted as `provisional`. |
| reject | Gate 1 → Rejected entry (**dashed**) | `reject` | Regex match → status='rejected'. |
| reject | Gate 2 → Rejected entry (**dashed**) | `reject` | Name guard hit → status='rejected'. |
| reject | Gate 3 → Rejected entry (**dashed**) | `reject` | Judge said `approve=False` (or errored). |
| 7 | agent_learnings → AOAI embed | `7 inline embed (limit=1)` | One embed call after the write — bounded so it doesn't drown the chat turn. |
| 8 | AOAI embed → agent_learnings_vec | `8` | Vector lands in the vec0 table. |
| trigger | agent_learnings → agent_learnings_fts (**dashed**) | `trigger` | FTS5 trigger auto-syncs from the canonical row. |

**Retrieval path:**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| A | agent_learnings_vec → retrieve_relevant_learnings | `A` | Vector retrieval stage. |
| B | agent_learnings_fts → retrieve_relevant_learnings | `B` | BM25 retrieval stage. |
| C | retrieve → Inject markers | `C top-5` | RRF-fused top-5 entries, tagged with `[CANONICAL]` or `[PROVISIONAL]`. |
| next turn | Inject → Orchestrator (**dashed**) | `next turn` | Markers land in the next turn's system prompt. |
| track usage | Inject → mark_learning_outcome (**dashed**) | `track usage` | The orchestrator threads the retrieved IDs through so we know what was in-scope. |
| counter update | mark_learning_outcome → agent_learnings (**dashed**) | `counter update` | Increments validation_count or failure_count based on subsequent tool outcome. |

---

## Appendix C — Glossary references

For abbreviations (BM25, RRF, FTS5, vec0, AOAI), see **[GLOSSARY.md](GLOSSARY.md)** in this folder.

For Nexus-specific terms (Learning, Learning guard, Embedding, RRF, Reindexer), see the main **[GLOSSARY.md](../GLOSSARY.md)**.

For the underlying design decisions:
- Agent learnings → SQLite + vec0 → [DESIGN.md §5 2026-05-20](../DESIGN.md)
- Orchestrator-owned writes; agent tools removed → [DESIGN.md §5 2026-05-20](../DESIGN.md)
- Three-gate write defense → [DESIGN.md §5 2026-05-20](../DESIGN.md)
- Retrieval-on-context replaces always-on injection → [DESIGN.md §5 2026-05-20](../DESIGN.md)
- Auto-promote / auto-archive via validation tracking → [DESIGN.md §5 2026-05-20](../DESIGN.md)
- Architect-gated admin API for agent-learnings → [DESIGN.md §5 2026-05-20](../DESIGN.md)
- Rephrase BEFORE the 3-gate defense → [DESIGN.md §5 2026-05-21](../DESIGN.md)
- Hybrid retrieval for agent_learnings → [DESIGN.md §5 2026-05-21](../DESIGN.md)
