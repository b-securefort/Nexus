# DSPy coverage tracker

Living checklist of the distinct problems a DSPy integration in Nexus could
address. Maintained so a partial DSPy PR can be **deliberately scoped** rather
than accidentally leaving gaps.

When you implement a row, follow the **retire-criteria** column to remove the
interim fix safely. When you skip a row, leave its interim fix in place and
note "deferred" in the row's notes.

DESIGN.md §7 points here.

---

## Use cases

| # | Capability | Problem it solves | Interim fix in code today | Files / call sites | Retire criteria when DSPy lands | Notes |
|---|---|---|---|---|---|---|
| 1 | `dspy.Signature` + `Predict` for the compaction summarizer | Replace ad-hoc `chat.completions.create` in the compaction module with a typed input/output signature so the summary format is enforced by structure, not by prose. | None — still ad-hoc | [backend/app/agent/compaction.py](../backend/app/agent/compaction.py) | Remove the raw `client.chat.completions.create(...)` call once DSPy module produces the same `Conversation.summary_text` shape on a 10-conversation golden set. | This was the original Phase 3 scope in DESIGN.md §7. Code-quality only — no behaviour change expected. Lowest risk to land first. |
| 2 | `dspy.Signature` + `Predict` for query expansion | When the orchestrator expands a user's "what's the cheapest region" into a structured search query for `search_kb_hybrid` / `web_search`, a typed signature beats free-text prompting. | None — query-expansion path is not built yet | n/a (greenfield) | Use DSPy from day one when building this path. No interim to retire. | Pre-DSPy work would just be wasted effort. Build the path with DSPy from the start. |
| 3 | `dspy.Signature` + `BootstrapFewShot` for `generate_drawio_from_python` codegen | The architect's Python codegen keeps violating the `from diagrams import AzureGeneric` rule despite the constraint being in the prompt, the learnings store, and the tool description. Few-shot examples of correct code beat negative instructions. | **Parked AST guard** — would be 4 lines in `_validate_ast` that reject the import explicitly. Not shipped. | [backend/app/tools/generic/python_to_drawio.py](../backend/app/tools/generic/python_to_drawio.py) — function `_validate_ast` at line ~60 | If post-DSPy sanity runs show 0 `AzureGeneric` import failures across 5+ scenarios, declare retired (no interim to remove since the guard was parked). If failures still happen at >5% rate, ship the AST guard as defence-in-depth. | The bootstrap pool is the `agent_learnings` table — entries id=46 and id=47 already document the correct pattern. |
| 4 | `dspy.Signature` for any "tool-emitting" agent step | Models sometimes emit narration ("I'll generate the diagram now") **instead of** the actual tool call, leaving the chat to terminate without action. DSPy's structured outputs that require a tool-call field would close this gap by construction. | **Narration nudge** — `_DEFERRED_ACTION_PATTERN` regex in orchestrator + 1 synthetic system message + `narration_nudges_used` cap. Behind feature flag `NARRATION_NUDGE_ENABLED`. | [backend/app/agent/orchestrator.py](../backend/app/agent/orchestrator.py) — `_DEFERRED_ACTION_PATTERN`, `_looks_like_deferred_action`, `_NARRATION_NUDGE_MESSAGE`, the loop branch at `if not tool_calls:` | When DSPy-wrapped agent steps eliminate the narration pattern in a 10-scenario sanity run: remove `_DEFERRED_ACTION_PATTERN` and related helpers; remove the `narration_nudges_used` accounting; remove `NARRATION_NUDGE_ENABLED` from config. Keep the loop's bare `if not tool_calls: done` as the termination path. | The nudge is **load-bearing in production today** — removing it before DSPy proves out the same coverage will regress the chat-stops-mid-thought UX the user reported on 2026-05-20. |

---

## How to scope a partial DSPy PR

A reasonable first PR covers **row 1 + row 2**: pure code-quality refactor with
no agent-behaviour change. No interim fix exists for either, so nothing to
remove. Low risk to land. Validates the DSPy plumbing (azure-openai backend
binding, optimizer config, golden-set evaluation) on the safest problems.

A reasonable second PR covers **row 3**: introduces DSPy into a tool's codegen.
Now there's a behaviour comparison to do (vs the prompt-only baseline). If the
AzureGeneric rate drops to ~0, that's the signal to leave the AST guard
unshipped. If not, the guard ships as well.

A reasonable third PR covers **row 4**: DSPy-wraps the orchestrator's agent-step
boundary. This is the trickiest one — the orchestrator's tool-call loop is the
load-bearing hot path. Removing the narration nudge depends on proving DSPy's
output schema makes the narration-without-call state structurally
unrepresentable. Until proven, keep the nudge enabled.

---

## Quick-fix paths if DSPy slips past 2026-08-19

Each row's interim fix (or lack thereof) is the fallback. For rows with no
interim:

- **Row 1**: leave as ad-hoc; the compaction module works.
- **Row 2**: build the path with the same `chat.completions.create` shape as
  the rest of the codebase if needed before DSPy lands; treat as deferred
  cleanup.
- **Row 3**: ship the AST guard standalone (4 lines + 1 test in
  `tests/test_python_to_drawio.py`).
- **Row 4**: the narration nudge already covers ~60-80% of cases and is in
  production. Widen the regex if a recurring miss pattern surfaces.

---

## Signals to watch when scoping the next PR

- Sanity test runs ([terminal-client/sanity_chat_test.py](../terminal-client/sanity_chat_test.py)):
  do scenarios 3 and 7 still hit AzureGeneric? Does any scenario hit a
  narration nudge (visible in backend logs as `"Narration nudge fired (iter=N, ...)"`)?
- Token cost per architect turn — the prompt currently includes the compaction
  summarizer's output, KB summary, retrieved learnings, Azure context, and the
  pinned original task. A DSPy compiled prompt is typically more terse than
  hand-written guidance.
- Agent learnings store: rows for the AzureGeneric pattern (id=46, id=47) are
  the bootstrap pool for row 3. Their `validation_count` rising means the agent
  is encountering the pattern repeatedly — useful when deciding row priority.
