"""
Agent orchestrator — main agent loop with tool calling and approval gating.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from openai import AzureOpenAI
from sqlmodel import Session, select

from app.agent.approvals import create_pending_approval, wait_for_approval
from app.agent.streaming import (
    sse_approval_required,
    sse_done,
    sse_error,
    sse_message_saved,
    sse_token,
    sse_tool_call_start,
    sse_tool_executing,
    sse_tool_output_chunk,
    sse_tool_result,
)
from app.auth.models import User
from app.config import get_settings
from app.db.models import Conversation, Message
from app.kb.indexer import get_index_summary
from app.skills.models import Skill
from app.tools.base import Tool, resolve_tools

logger = logging.getLogger(__name__)

# Safety caps
MAX_TOOL_ITERATIONS = 15  # Increased to allow room for retry strategies

# Tools whose errors should trigger automatic multi-strategy retry
_COMMAND_TOOLS = {"az_cli", "run_shell", "az_resource_graph"}

# Max consecutive failures on the same type of tool before giving up
_MAX_RETRIES_PER_TOOL = 3
MAX_HISTORY_MESSAGES = 50


def _get_openai_client() -> AzureOpenAI:
    settings = get_settings()
    return AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )


def _tool_needs_approval(tool: Tool, args: dict) -> bool:
    """Check if a tool invocation requires user approval.
    
    Supports both static (requires_approval attribute) and dynamic
    approval (e.g., az_rest_api where GET is safe but mutations need approval).
    """
    if hasattr(tool, '_needs_approval'):
        # Pass the discriminator field (method for REST, action for DevOps)
        key = args.get("method") or args.get("action") or "GET"
        return tool._needs_approval(key)
    return tool.requires_approval


def _compose_system_prompt(skill: Skill, user: User) -> str:
    """Compose the final system prompt per §11.6."""
    from app.tools.learn_tool import get_learnings_content
    from app.tools.az_login_check import get_az_context_prompt

    kb_summary = get_index_summary()
    learnings = get_learnings_content()
    az_context = get_az_context_prompt()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Static content FIRST (maximizes Azure OpenAI prompt cache prefix) ---
    parts = [
        skill.system_prompt,
        "\n---\n"
        "## Tool hierarchy\n"
        "Always prefer tools in this order when querying Azure resources:\n"
        "1. **`az_resource_graph`** (KQL) — fastest, read-only, no approval. Use first for resource queries.\n"
        "2. **`az_cost_query`** — cost/usage data. No approval.\n"
        "3. **`az_monitor_logs`** — Log Analytics KQL queries. No approval.\n"
        "4. **`az_advisor`** / **`az_policy_check`** — recommendations and compliance. No approval.\n"
        "5. **`az_cli`** — general Azure operations. Requires approval for mutations.\n"
        "6. **`az_rest_api`** — direct ARM REST calls. GET=no approval, mutations=approval.\n"
        "7. **`az_devops`** — Azure DevOps pipelines/PRs/builds. Read=no approval, mutations=approval.\n"
        "8. **`run_shell`** — PowerShell/shell commands. Always requires approval.\n\n"
        "Other tools:\n"
        "- **`network_test`** — DNS/port checks, NSG rules. No approval.\n"
        "- **`generate_file`** — Write files to output/ sandbox. No approval.\n"
        "- **`diagram_gen`** — Generate Mermaid diagrams. No approval.\n"
        "- **`web_fetch`** — Fetch web page content. No approval.\n"
        "Before running any command, call `fetch_ms_docs` to verify the correct syntax.\n\n"
        "## Thinking before acting\n"
        "ALWAYS include a brief text explanation BEFORE making any tool call(s). "
        "The user must see your reasoning. Specifically:\n"
        "- **Before a tool call**: Explain what you're about to do and why (1-2 sentences).\n"
        "- **Before a retry**: Explain what went wrong with the previous attempt and what you'll try differently.\n"
        "- **Before multiple tool calls**: Explain why you need each one.\n"
        "NEVER emit tool calls without accompanying text content in the same response.\n\n"
        "## Retry policy\n"
        "When a tool call fails, you MUST try at least 3 different approaches before giving up:\n"
        "1. **Fix the syntax** — Read the error carefully, check docs with `fetch_ms_docs`, and retry.\n"
        "2. **Try a different approach** — Move down the tool hierarchy (Resource Graph → Az CLI/PowerShell → REST API).\n"
        "3. **Try the simplest form** — Strip to minimal parameters, or use a completely different tool.\n\n"
        "## Learning policy\n"
        "You MUST call `update_learnings` in these situations:\n"
        "- When you **succeed after a failure** — record what failed, why, what worked instead, and which approach was faster/simpler.\n"
        "- When you **exhaust all retries** — record the error so you don't repeat it.\n"
        "- When you **discover a workaround** — record it as a best-practice, noting which tool in the hierarchy worked.\n"
        "- When a command has **unexpected behavior** — record it as a gotcha.\n"
        "- When you find one approach is **significantly faster** than another — record the timing comparison.\n"
        "Learnings are already loaded above in the system prompt — do NOT call `read_learnings` "
        "unless you have just called `update_learnings` and need to verify the update.\n"
        "---",
    ]

    # --- Dynamic content AFTER static (changes per conversation/turn) ---
    parts.append(
        "\n---\n"
        "Knowledge base index (use read_kb_file or search_kb to retrieve full content):\n"
        f"{kb_summary}\n"
        "---"
    )

    if learnings.strip():
        parts.append(
            "\n---\n"
            "**Agent Learnings** (known issues & past mistakes — DO NOT repeat these):\n"
            f"{learnings}\n"
            "---\n"
            "IMPORTANT: Review the learnings above before executing any commands. "
            "If a command matches a known issue, use the documented fix or workaround instead."
        )

    parts.append(
        f"\nCurrent user: {user.display_name} ({user.email})\n"
        f"Current date: {now}\n\n"
        f"{az_context}"
    )

    return "\n".join(parts)


def _load_message_history(session: Session, conversation_id: int) -> list[dict]:
    """Load message history for the conversation, limited to MAX_HISTORY_MESSAGES.

    Ensures tool-role messages are always preceded by an assistant message
    with matching tool_calls — the OpenAI API rejects orphaned tool messages.
    This can happen when the LIMIT window cuts off mid-tool-call sequence.
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())  # type: ignore
        .limit(MAX_HISTORY_MESSAGES)
    )
    rows = list(session.exec(stmt).all())
    rows.reverse()  # Chronological order

    messages = []
    for row in rows:
        msg: dict = {"role": row.role, "content": row.content}
        if row.role == "assistant" and row.tool_calls_json:
            tool_calls = json.loads(row.tool_calls_json)
            if tool_calls:
                msg["tool_calls"] = tool_calls
        if row.role == "tool":
            msg["tool_call_id"] = row.tool_call_id or ""
        messages.append(msg)

    # Collect tool_call_ids provided by assistant messages in the history
    valid_tool_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id") or ""
                if tc_id:
                    valid_tool_call_ids.add(tc_id)

    # Collect tool_call_ids that have a corresponding tool-role response
    answered_tool_call_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id:
                answered_tool_call_ids.add(tc_id)

    # Drop tool-role messages whose tool_call_id has no matching assistant message
    # AND strip tool_calls from assistant messages whose responses are missing
    cleaned: list[dict] = []
    for msg in messages:
        if msg.get("role") == "tool":
            if msg.get("tool_call_id", "") not in valid_tool_call_ids:
                continue  # orphaned tool response
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            kept = [
                tc for tc in msg["tool_calls"]
                if (tc.get("id") or "") in answered_tool_call_ids
            ]
            if not kept:
                # All tool_calls are unanswered — drop the key entirely
                msg = {k: v for k, v in msg.items() if k != "tool_calls"}
            else:
                msg = {**msg, "tool_calls": kept}
        cleaned.append(msg)

    return cleaned


def _save_message(
    session: Session,
    conversation_id: int,
    role: str,
    content: str,
    tool_calls_json: str | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> Message:
    """Save a message to the database."""
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls_json=tool_calls_json,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)
    return msg


def _skill_from_snapshot(snapshot_json: str) -> Skill:
    """Reconstruct a Skill from a conversation's skill snapshot."""
    data = json.loads(snapshot_json)
    return Skill(
        id=data.get("id", ""),
        name=data.get("name", ""),
        display_name=data.get("display_name", ""),
        description=data.get("description", ""),
        system_prompt=data.get("system_prompt", ""),
        tools=data.get("tools", []),
        source=data.get("source", "shared"),
    )


# ── Multi-strategy retry system ──────────────────────────────────────────────

def _build_docs_query(tool_name: str, func_args: dict, error_text: str) -> str:
    """Build a search query from a failed tool call to look up docs."""
    if tool_name == "az_cli":
        args = func_args.get("args", [])
        if args:
            return f"az {' '.join(args[:3])} syntax parameters"
    elif tool_name == "az_resource_graph":
        return f"Azure Resource Graph KQL query syntax {func_args.get('query', '')[:80]}"
    elif tool_name == "run_shell":
        cmd = func_args.get("command", "")
        return f"{cmd[:60]} syntax"
    return f"Azure CLI {error_text[:60]}"


def _auto_lookup_docs(tool_name: str, func_args: dict, error_text: str) -> str | None:
    """Look up Microsoft Learn docs for a failed command."""
    from app.tools.base import TOOL_REGISTRY

    ms_docs_tool = TOOL_REGISTRY.get("fetch_ms_docs")
    if not ms_docs_tool or not ms_docs_tool.enabled_by_config:
        return None

    query = _build_docs_query(tool_name, func_args, error_text)
    try:
        from app.auth.models import User as UserModel
        dummy_user = UserModel(oid="system", email="system", display_name="system")
        docs_result = ms_docs_tool.execute({"query": query}, dummy_user)
        if docs_result and not docs_result.startswith("Error"):
            return docs_result
    except Exception as e:
        logger.debug("Auto docs lookup failed: %s", e)
    return None


def _get_retry_strategy(failure_count: int, tool_name: str, func_args: dict, error_text: str) -> str | None:
    """
    Return a strategy hint message based on how many times the same tool type
    has failed consecutively. Returns None if max retries exhausted.

    Strategy 1 (1st failure): Look up docs, fix syntax, retry
    Strategy 2 (2nd failure): Try a completely different command/approach
    Strategy 3 (3rd failure): Try a different tool entirely, or record learning and report
    """
    if failure_count >= _MAX_RETRIES_PER_TOOL:
        return None  # Give up

    docs_hint = _auto_lookup_docs(tool_name, func_args, error_text) or ""

    if failure_count == 1:
        # Strategy 1: Fix syntax using docs
        return (
            f"[RETRY STRATEGY 1/3 — Fix syntax] The `{tool_name}` call failed with:\n"
            f"```\n{error_text[:500]}\n```\n\n"
            f"Here are relevant Microsoft Learn docs:\n{docs_hint}\n\n"
            "**Action**: Carefully read the error message and docs above. "
            "Fix the command syntax, parameters, or flags and retry with the corrected command. "
            "Common issues: wrong parameter names, missing required args, incorrect flag format."
        )

    elif failure_count == 2:
        # Strategy 2: Try a different command/approach entirely
        alt_tools = {
            "az_cli": "For read queries, try `az_resource_graph` (KQL) — it's faster and needs no approval. For other operations, try `run_shell` with PowerShell Az modules (e.g. Get-AzResource, Get-AzVM). As last resort, use `az rest` for direct REST API calls.",
            "az_resource_graph": "Try using `az_cli` with `az resource list` or similar commands. If that also fails, use `az rest` to call the Azure REST API directly.",
            "run_shell": "Try using `az_cli` directly. For read queries, prefer `az_resource_graph` (KQL). As last resort, use `az rest` for direct REST API calls.",
        }
        alt_hint = alt_tools.get(tool_name, "Try a different tool.")
        return (
            f"[RETRY STRATEGY 2/3 — Different approach] `{tool_name}` has now failed twice.\n"
            f"Error: {error_text[:300]}\n\n"
            f"**Action**: Do NOT retry the same command again. Instead:\n"
            f"1. {alt_hint}\n"
            "2. Break the problem into smaller steps — first verify prerequisites, then attempt the operation.\n"
            "3. Check `read_learnings` for any known issues with this type of operation.\n"
            "4. If using az_cli, try `az <command> --help` first to see the correct syntax."
        )

    else:
        # failure_count == 3 → Strategy 3: Last resort
        return (
            f"[RETRY STRATEGY 3/3 — Final attempt] `{tool_name}` has failed {failure_count} times.\n"
            f"Latest error: {error_text[:300]}\n\n"
            "**Action**: This is your LAST attempt. Choose ONE:\n"
            "1. Use a completely different tool to achieve the same goal.\n"
            "2. Try the simplest possible version of the command (fewer parameters, basic form).\n"
            "3. If nothing works, use `update_learnings` to record what went wrong and the error details, "
            "then explain to the user what you tried and suggest they run the command manually.\n\n"
            "Do NOT repeat the same command that already failed."
        )


def _build_failure_summary_for_learning(
    tool_name: str, attempts: list[tuple[dict, str]]
) -> str:
    """Build a summary of all failed attempts for recording in learn.md."""
    lines = [f"Failed {len(attempts)} attempts with `{tool_name}`:"]
    for i, (args, error) in enumerate(attempts, 1):
        args_str = json.dumps(args)[:200]
        lines.append(f"  Attempt {i}: args={args_str}, error={error[:150]}")
    return "\n".join(lines)


def _execute_tool_streaming(
    tool: Tool, func_args: dict, user, call_id: str, chunk_sink: list[str]
) -> str:
    """Execute a tool using its streaming method, collecting output chunks.

    Chunks are appended to chunk_sink so the caller (async generator) can
    yield them as SSE events after this synchronous call returns.
    Returns the full combined tool output.
    """
    gen = tool.execute_streaming(func_args, user)
    full_result = ""
    try:
        while True:
            chunk = next(gen)
            chunk_sink.append(chunk)
    except StopIteration as e:
        full_result = e.value if e.value else ""
    # If the generator didn't return a value, build from chunks
    if not full_result and chunk_sink:
        full_result = "".join(chunk_sink)
    return full_result


async def handle_chat(
    session: Session,
    conversation: Conversation,
    user_message: str,
    user: User,
) -> AsyncGenerator[str, None]:
    """
    Main agent loop. Yields SSE events as strings.
    """
    settings = get_settings()

    # 1. Persist user message
    user_msg = _save_message(session, conversation.id, role="user", content=user_message)
    yield sse_message_saved(user_msg.id, "user")

    # 2. Build request
    skill = _skill_from_snapshot(conversation.skill_snapshot_json)
    messages = _load_message_history(session, conversation.id)
    system_prompt = _compose_system_prompt(skill, user)
    tools = resolve_tools(skill.tools)
    tool_schemas = [t.to_openai_schema() for t in tools] if tools else None

    client = _get_openai_client()
    iteration = 0

    # Track consecutive failures per tool type for multi-strategy retry
    failure_tracker: dict[str, int] = {}  # tool_name -> consecutive failure count
    failure_history: dict[str, list[tuple[dict, str]]] = {}  # tool_name -> [(args, error), ...]

    while iteration < MAX_TOOL_ITERATIONS:
        iteration += 1

        try:
            # 3. Call Azure OpenAI
            api_messages = [{"role": "system", "content": system_prompt}] + messages

            create_kwargs = {
                "model": settings.AZURE_OPENAI_DEPLOYMENT,
                "messages": api_messages,
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_completion_tokens": 4096,
            }
            if tool_schemas:
                create_kwargs["tools"] = tool_schemas

            # Run the synchronous OpenAI streaming call in a thread so we
            # don't block the event loop (which would stall all other HTTP
            # requests, including health-checks and approval POSTs).
            chunk_queue: asyncio.Queue = asyncio.Queue()
            _SENTINEL = object()

            def _consume_openai_stream():
                try:
                    s = client.chat.completions.create(**create_kwargs)
                    for c in s:
                        chunk_queue.put_nowait(c)
                except Exception as exc:
                    chunk_queue.put_nowait(exc)
                finally:
                    chunk_queue.put_nowait(_SENTINEL)

            stream_thread = asyncio.get_event_loop().run_in_executor(
                None, _consume_openai_stream
            )

            # Consume stream
            assistant_content = ""
            tool_calls_accumulator: dict[int, dict] = {}
            stream_usage = None

            try:
                while True:
                    item = await chunk_queue.get()
                    if item is _SENTINEL:
                        break
                    if isinstance(item, Exception):
                        raise item
                    chunk = item

                    # Capture usage from the final chunk
                    if hasattr(chunk, 'usage') and chunk.usage is not None:
                        stream_usage = chunk.usage

                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta

                    # Text content
                    if delta.content:
                        assistant_content += delta.content
                        yield sse_token(delta.content)

                    # Tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tc.id or "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc.id:
                                tool_calls_accumulator[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_accumulator[idx]["function"]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_accumulator[idx]["function"]["arguments"] += tc.function.arguments
            except Exception as stream_err:
                err_str = str(stream_err)
                if "content_filter" in err_str or "content management policy" in err_str:
                    yield sse_error(
                        "Your message was flagged by the content filter. "
                        "Please rephrase and try again."
                    )
                    return
                raise
            finally:
                await stream_thread

            # Log token usage and cache stats
            if stream_usage:
                prompt_tokens = stream_usage.prompt_tokens or 0
                completion_tokens = stream_usage.completion_tokens or 0
                cached = 0
                if hasattr(stream_usage, 'prompt_tokens_details') and stream_usage.prompt_tokens_details:
                    cached = getattr(stream_usage.prompt_tokens_details, 'cached_tokens', 0) or 0
                cache_pct = (cached / prompt_tokens * 100) if prompt_tokens > 0 else 0
                logger.info(
                    "Token usage — prompt: %d (cached: %d, %.1f%%), completion: %d, total: %d",
                    prompt_tokens, cached, cache_pct, completion_tokens,
                    prompt_tokens + completion_tokens,
                )

            # Build tool_calls list
            tool_calls = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]

            # Save assistant message
            tc_json = json.dumps(tool_calls) if tool_calls else None
            assistant_msg = _save_message(
                session,
                conversation.id,
                role="assistant",
                content=assistant_content,
                tool_calls_json=tc_json,
            )
            yield sse_message_saved(assistant_msg.id, "assistant")

            # Append to history
            assistant_hist: dict = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                assistant_hist["tool_calls"] = tool_calls
            messages.append(assistant_hist)

            if not tool_calls:
                yield sse_done(conversation.id)
                return

            # 4. Execute tool calls
            for call in tool_calls:
                call_id = call["id"]
                func_name = call["function"]["name"]
                try:
                    func_args = json.loads(call["function"]["arguments"])
                except json.JSONDecodeError:
                    func_args = {}

                yield sse_tool_call_start(call_id, func_name, func_args)

                # Chunk sink for streaming tool output
                _stream_chunks: list[str] = []

                # Find tool
                tool = next((t for t in tools if t.name == func_name), None)
                if not tool:
                    tool_result = f"Error: Unknown tool '{func_name}'"
                elif _tool_needs_approval(tool, func_args):
                    # Create approval
                    approval = create_pending_approval(
                        session=session,
                        conversation_id=conversation.id,
                        user_oid=user.oid,
                        tool_name=func_name,
                        tool_args_json=json.dumps(func_args),
                        reason=func_args.get("reason", "No reason provided"),
                    )
                    yield sse_approval_required(
                        approval.id, func_name, func_args, func_args.get("reason", "")
                    )

                    # Wait for approval
                    status = await wait_for_approval(approval.id)

                    if status == "approved":
                        yield sse_tool_executing(call_id, func_name)
                        tool_result = await asyncio.to_thread(
                            _execute_tool_streaming,
                            tool, func_args, user, call_id, _stream_chunks,
                        )
                    elif status == "denied":
                        tool_result = "User denied the tool call."
                    else:
                        tool_result = "Approval timed out."
                else:
                    yield sse_tool_executing(call_id, func_name)
                    tool_result = await asyncio.to_thread(
                        _execute_tool_streaming,
                        tool, func_args, user, call_id, _stream_chunks,
                    )

                # Yield any streaming chunks as SSE events
                for chunk in _stream_chunks:
                    yield sse_tool_output_chunk(call_id, chunk)

                # Save tool result
                tool_msg = _save_message(
                    session,
                    conversation.id,
                    role="tool",
                    content=tool_result,
                    tool_call_id=call_id,
                    tool_name=func_name,
                )
                messages.append({"role": "tool", "content": tool_result, "tool_call_id": call_id})
                yield sse_tool_result(call_id, func_name, tool_result)

                # Multi-strategy retry: track failures and escalate
                if func_name in _COMMAND_TOOLS and tool_result.startswith("Error"):
                    # Auth errors — clear cache so next attempt re-checks
                    if "az login" in tool_result or "not logged in" in tool_result.lower():
                        from app.tools.az_login_check import clear_login_cache
                        clear_login_cache()

                    # Track this failure
                    failure_tracker[func_name] = failure_tracker.get(func_name, 0) + 1
                    if func_name not in failure_history:
                        failure_history[func_name] = []
                    failure_history[func_name].append((func_args, tool_result))
                    count = failure_tracker[func_name]

                    strategy = _get_retry_strategy(count, func_name, func_args, tool_result)
                    if strategy:
                        messages.append({"role": "system", "content": strategy})
                        logger.info(
                            "Retry strategy %d/%d triggered for %s (failure #%d)",
                            min(count, _MAX_RETRIES_PER_TOOL),
                            _MAX_RETRIES_PER_TOOL,
                            func_name,
                            count,
                        )
                    else:
                        # All retries exhausted — instruct agent to record and report
                        summary = _build_failure_summary_for_learning(
                            func_name, failure_history[func_name]
                        )
                        give_up_msg = (
                            f"[ALL RETRIES EXHAUSTED] `{func_name}` failed {count} times.\n"
                            f"{summary}\n\n"
                            "**You MUST now**:\n"
                            "1. Call `update_learnings` to record this failure so it won't be repeated.\n"
                            "2. Tell the user what you tried, what went wrong, and suggest "
                            "they run the command manually or check their environment/permissions."
                        )
                        messages.append({"role": "system", "content": give_up_msg})
                        logger.warning(
                            "All %d retries exhausted for %s", _MAX_RETRIES_PER_TOOL, func_name
                        )
                elif func_name in _COMMAND_TOOLS:
                    # Tool succeeded — check if there were prior failures to learn from
                    prior_failures = failure_history.get(func_name)
                    if prior_failures:
                        summary = _build_failure_summary_for_learning(func_name, prior_failures)
                        learn_msg = (
                            f"[SUCCESS AFTER FAILURES] `{func_name}` succeeded after "
                            f"{len(prior_failures)} failed attempt(s).\n"
                            f"{summary}\n\n"
                            f"The working approach was: {json.dumps(func_args)[:300]}\n\n"
                            "**You MUST call `update_learnings`** to record:\n"
                            "- What failed and why\n"
                            "- What worked (the successful command/approach)\n"
                            "- Which approach was faster/simpler (note the tool hierarchy: Resource Graph > Az CLI > REST API)\n"
                            "- So this mistake is never repeated\n\n"
                            "Do this NOW before responding to the user."
                        )
                        messages.append({"role": "system", "content": learn_msg})
                        logger.info(
                            "Success after %d failures for %s — prompting learning record",
                            len(prior_failures),
                            func_name,
                        )
                    # Reset tracker
                    failure_tracker.pop(func_name, None)
                    failure_history.pop(func_name, None)

            # 5. Loop back

        except Exception as e:
            logger.error("Agent loop error: %s", str(e), exc_info=True)
            yield sse_error(str(e))
            return

    # Exceeded max iterations
    yield sse_error("Maximum tool call iterations exceeded")
