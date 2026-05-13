# Plan: Reduce system-prompt context so gpt-5.4-mini stops hallucinating tool calls

## Context

gpt-5.4-mini (behind the `drawio-diagrammer` skill) recently started narrating file edits without calling the write tool ("I added the hub abstraction" with no `generate_file`/`patch_drawio_cell` call). The user observed this only after we added the `ask_user` flow and grew the SKILL.md. Hypothesis: the model's attention budget is being eaten by accumulated context, not by any one bad instruction.

Measured the actual bloat: the biggest contributor by an order of magnitude is the **rendered-PNG vision attachments**. Every diagram iteration calls `_build_render_review_message` which inlines a 50–200 KB base64 PNG at `detail: "high"`. By iteration 5, the in-loop `messages` list contains 5 of these — ~500 KB–1 MB of raw bytes, ~150–300 K tokens. Each subsequent API call re-sends them all.

Secondary contributors: `learn.md` (already 4 KB-capped at read time but with ~75 duplicate/placeholder lines polluting that cap) and learnings entries that aren't relevant to the active skill (drawio session getting az_cli / az_resource_graph entries).

This plan implements four fixes covering the dynamic context plus one orchestrator-side guardrail for the specific failure mode (narration without a tool call).

## Changes

### 1. Manual prune of [`backend/kb_data/learnings/learn.md`](backend/kb_data/learnings/learn.md)

Delete the following lines (numbers as they stand today):
- **142–146**: three `## [gotcha] real entry` empty placeholders
- **158**: fourth empty `## [gotcha] real entry`
- **160–168**: duplicate copies of `[syntax-fix] az login` and `[gotcha] validator vertex-size threshold` (canonical copies remain at 148–156)
- **185–193**: third copy of those same two entries
- **195**: fifth empty `## [gotcha] real entry`
- **197–220**: second copy of `[syntax-fix] let-bindings`, `[known-issue] azure-devops extension`, `[best-practice] Cost API daily granularity`, `[syntax-fix] az login`, `[gotcha] validator vertex-size threshold` (canonical copies remain at 170–183)

Net: ~75 lines removed, ~9 KB saved on disk. Non-drawio entries (cost API rate limits, RBAC, Resource Graph syntax, etc.) stay — they're useful to other skills.

### 2. Skill-aware learnings filter in [`backend/app/tools/learn_tool.py`](backend/app/tools/learn_tool.py)

- Add helper `_entries_by_tool(content: str) -> list[tuple[str, str | None, str]]` that splits learn.md using the existing `_split_entries()` (line 101) and pulls each entry's `**Tool**:` field via a `re.search(r"\*\*Tool\*\*:\s*(\S+)", body)`.
- Change `get_learnings_content()` (line 136) signature to `get_learnings_content(allowed_tools: set[str] | None = None) -> str`. When `allowed_tools` is provided, keep an entry iff its tool is in `allowed_tools` OR is `None` / `"general"` / `""`. Default behaviour (None) is unchanged for `ReadLearningsTool`.
- In [`backend/app/agent/orchestrator.py`](backend/app/agent/orchestrator.py) `_compose_system_prompt` (line 78), pass `allowed_tools=set(skill.tools) | {"general"}`.

Drawio skill (10 tools) sees ~40% of the post-prune entries; broad skills like `chat-with-kb` (26 tools) see almost everything. Estimated drawio prompt savings: ~2 KB inside the 4 KB cap, but the **quality** of what fits goes up substantially because no slot is wasted on az_cli rules during a diagramming session.

### 3. Tiered image-attachment retention in [`backend/app/agent/orchestrator.py`](backend/app/agent/orchestrator.py)

The big one. Strategy: most-recent render at full quality; older renders within a rolling window degrade to `auto`; renders beyond 5 iterations old drop their bytes entirely and become a one-line text marker.

- In `_build_render_review_message` (line 149), tag the synthetic user message with a sentinel — easiest is to set the message's first text part to begin with a stable token, e.g. `[render-review iter=<N>]` where `N` increments per call within `handle_chat`. Pass an `iteration` arg to the helper.
- New helper `_age_out_render_reviews(messages: list[dict], keep_high: int = 1, keep_auto: int = 4) -> None` invoked just before each new render-review is appended:
  - Find all render-review messages in `messages` by sentinel-text scan.
  - For the newest `keep_high` (default 1): leave untouched (`detail: "high"`).
  - For the next `keep_auto` (default 4): rewrite the image_url part to `detail: "auto"`.
  - For everything older: replace the entire content list with `[{"type": "text", "text": "[Rendered iteration <N> reviewed]"}]`. The base64 bytes are dropped.
- Wire the helper into the orchestrator at the existing append site (search for where `post_iteration_messages.append(review_msg)` is currently called near line 825).

Estimated savings on a 6+ iteration diagram session: 200–600 KB of base64 dropped, ~50–150 K tokens, roughly **50–70% of the iteration-3+ context bloat**.

### 4. Image detail for the newest render kept at "high"

No change to line 202 in `_build_render_review_message` — keep `detail: "high"`. The tiering in change 3 handles older images. (Different from the earlier proposal to flatten to `auto` everywhere.)

### 5. Orchestrator-side anti-hallucination guardrail

When the assistant produces a turn whose text content matches narration-of-an-edit (e.g. regex `r"\b(i (just )?(added|patched|updated|moved|removed|changed|rewrote|inserted)|i('ve| have) (added|patched|updated|moved|removed|changed|rewrote|inserted)|the (file|diagram) (now |has been )?(updated|patched|modified|changed))\b"`, case-insensitive) AND the same turn has zero `generate_file` / `patch_drawio_cell` / `overwrite=true` tool calls AND a `.drawio` file is in scope (latest `generate_file` call exists in history), inject a system reminder before the next iteration:

> *"Your previous reply claimed a file change but called no write tool. Either call generate_file/patch_drawio_cell now to actually make the change, or correct your reply to clarify nothing was changed. Do not narrate edits you haven't made."*

This goes after tool execution but before the next API call. Implementation site: inside the main `while iteration < MAX_TOOL_ITERATIONS` loop, after the for-loop over `tool_calls` ends, when the assistant produced no write-tool calls.

Acts as a backstop: even if context compression still leaves mini confused, it catches the specific failure mode and self-corrects within the same turn.

## Critical files to modify

- [`backend/kb_data/learnings/learn.md`](backend/kb_data/learnings/learn.md) — manual prune (change 1)
- [`backend/app/tools/learn_tool.py`](backend/app/tools/learn_tool.py) — `_entries_by_tool`, `get_learnings_content(allowed_tools=…)` (change 2)
- [`backend/app/agent/orchestrator.py`](backend/app/agent/orchestrator.py) — `_compose_system_prompt` pass-through, `_build_render_review_message` sentinel, new `_age_out_render_reviews`, guardrail (changes 2, 3, 5)
- [`backend/tests/test_tools.py`](backend/tests/test_tools.py) — new test for skill-filtered learnings
- [`backend/tests/test_agent.py`](backend/tests/test_agent.py) — new tests for image age-out and guardrail
- (No frontend changes needed.)

## Tests to add

1. `test_get_learnings_content_filters_by_tool` — feed a fake learn.md with mixed Tool fields, call with `allowed_tools={"generate_file"}`, assert az_cli entries are absent and generate_file entries are present.
2. `test_get_learnings_content_keeps_generic_entries` — entries with no Tool: field or Tool: general are returned even when filtered.
3. `test_age_out_render_reviews_keeps_latest_high` — build a list of 7 simulated render-review user messages, call the helper, assert: newest 1 keeps full base64 + `detail:"high"`, next 4 keep base64 but `detail:"auto"`, oldest 2 are reduced to a single text part containing `"reviewed"`.
4. `test_anti_hallucination_guardrail_fires_on_narration` — synthesize an assistant message whose `content` says `"I added the hub"` with empty `tool_calls`, run through the orchestrator's check function, assert the system-reminder string is appended to messages.
5. `test_anti_hallucination_guardrail_silent_on_real_edit` — same narration but with a `generate_file` tool call in the same turn → no reminder injected.

## Verification path

1. Run backend test suite: `cd backend && python -m pytest tests/ -q --deselect tests/test_new_tools.py::TestGenerateFileTool` — expect 350+ passing.
2. Start a fresh drawio session and issue: *"Draw an AFD + private spoke Web App."*
3. Watch the token-usage log line at [`orchestrator.py:655`](backend/app/agent/orchestrator.py) — `Token usage — prompt: %d (cached: %d, %.1f%%), completion: %d, total: %d`.
   - Pre-change baseline (already observed): `prompt_tokens` climbs linearly across iterations, +50–150 K each render.
   - Post-change expectation: iteration 1 down ~10–15 K tokens (filter + prune). Iterations 2–5 add ~5 K per render instead of ~50 K. After iteration 5, prompt token count plateaus.
4. After validation passes, issue follow-up: *"add a Key Vault here"*. Expected: model calls `patch_drawio_cell` or `generate_file overwrite=true` immediately, no narration-without-tool-call.
5. If mini still narrates without acting, watch for the guardrail's injected system reminder in logs (`logger.info` line in the new check function), and confirm the next iteration responds with a real tool call.

## Out of scope (intentionally deferred)

- Trimming SKILL.md's icon-paths table (64 lines): would help but high risk of breaking icon coverage; leave for a separate pass.
- Compressing tool descriptions (`az_resource_graph` ~500 bytes, etc.): modest savings, scattered changes.
- Surfacing prompt-token counts to the frontend: visibility nice-to-have, not a fix.
- Replacing the manual learn.md prune with a write-time dedupe in `update_learnings`: address if duplicates reappear after a few weeks.
