"""
Sanity chat test — exercises real chat behaviour through the live backend
to validate the skill rewrites and inline-PNG attachment plumbing landed
in the 2026-05-19 PR.

Eight scenarios targeting things unit tests cannot validate:

| # | Skill              | What it proves                                            |
|---|--------------------|-----------------------------------------------------------|
| 1 | architect          | Phase 3 ask_user fires BEFORE generate_drawio_from_python |
| 2 | architect (resume) | "Other" free-text is honoured; no re-asking               |
| 3 | architect (resume) | Final assistant message carries the rendered PNG inline   |
| 4 | chat-with-kb       | Engineer hands diagrams off to Architect; no generate_file |
| 5 | kb-searcher        | Default tier refuses execute tools (read-only)            |
| 6 | drawio-diagrammer  | Per-cell patch flow uses patch_drawio_cell                |
| 7 | architect          | Long iterative diagram turn still emits terminal `done`    |
| 8 | architect          | Cost/region question grounds in fetch_ms_docs / web tools |

Two-layer verdict per scenario:
- Programmatic asserts: tool names called / forbidden, attachments_json, terminal `done`.
- Judge LLM: reads the transcript and answers a rubric prompt as JSON.

Pre-requisites:
- Backend running on http://localhost:8000 with DEV_AUTH_BYPASS=true.
- For the judge LLM: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
  AZURE_OPENAI_DEPLOYMENT in the terminal env (same vars the backend uses).
  Without these the judge step is skipped and only programmatic results report.

Run:
    python sanity_chat_test.py            # all 8 scenarios
    python sanity_chat_test.py --no-judge # skip judge LLM
    python sanity_chat_test.py --only 3   # one scenario by number
    python sanity_chat_test.py --only 1,2,3  # multiple by number

Results land in sanity_chat_results.json next to this file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

try:
    from dotenv import load_dotenv
    # Load terminal-client/.env into the process environment so the judge
    # LLM can read AZURE_OPENAI_* vars without the user having to export
    # them in their shell.
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv is optional; vars from shell env still work.

BASE_URL = "http://localhost:8000"
HEADERS = {"X-Dev-User": "dev-user"}
RESULTS_PATH = Path(__file__).parent / "sanity_chat_results.json"

# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    number: int
    name: str
    skill_id: str
    message: str
    # Programmatic asserts.
    expected_tools_any: list[str] = field(default_factory=list)  # any one suffices
    expected_tools_all: list[str] = field(default_factory=list)  # all required
    forbidden_tools: list[str] = field(default_factory=list)
    require_attachment_mime: Optional[str] = None  # e.g. "image/png"
    require_terminal_done: bool = True
    # Resume from a prior scenario's conversation (None = new chat).
    resume_from: Optional[int] = None
    # Auto-answer for ask_user calls. dict mapping question_text-substring to
    # { "selected": [...], "notes": "..." }. The harness picks the answer
    # whose key appears in the question; otherwise picks the first option.
    auto_answer: dict[str, dict] = field(default_factory=dict)
    # Auto-approve approval prompts.
    auto_approve: bool = True
    # If True, close the SSE stream as soon as the first question_required
    # event fires. Used by scenarios that only need to verify the question
    # was asked (not the answering flow). Saves the 180s wait-for-budget.
    early_exit_on_question: bool = False
    # Override the per-scenario stream budget (seconds).
    budget_s: int = 180
    # Natural-language rubric for the judge LLM.
    judge_rubric: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        number=1,
        name="Architect Phase 3 ask_user fires before generation",
        skill_id="shared:architect",
        message=(
            "Draw an architecture diagram of a web app behind an Application "
            "Gateway. Use the drawio-from-python flow."
        ),
        expected_tools_any=["ask_user"],
        forbidden_tools=["generate_drawio_from_python", "generate_file"],
        # We don't want it to actually finish the diagram in scenario 1 —
        # we're testing that it pauses to ask. The harness lets ask_user time
        # out without answering so the run finishes after the question fires.
        require_terminal_done=False,
        auto_answer={},  # do NOT answer; let the question pause
        early_exit_on_question=True,  # close the stream as soon as ask_user fires
        budget_s=60,
        judge_rubric=(
            "Did the agent call ask_user with at least one architectural "
            "question (backend service, access pattern, hub presence, identity, "
            "etc.) BEFORE attempting any generate_drawio_from_python or "
            "generate_file call? Pass only if ask_user came first."
        ),
    ),
    Scenario(
        number=2,
        name="Other free-text answered, no re-asking",
        skill_id="shared:architect",
        message=(
            "Draw an architecture diagram of a web app behind an Application "
            "Gateway."
        ),
        expected_tools_all=["ask_user", "generate_drawio_from_python"],
        forbidden_tools=[],
        require_attachment_mime=None,
        auto_answer={
            # Match nearly any question: feed back a complete architectural
            # answer via Other text so the agent must honour it without a
            # second ask_user round.
            "": {
                "selected": ["Other"],
                "notes": (
                    "Hub-and-spoke with shared hub services. Backend: Web App "
                    "(App Service) reached via VNet integration. Private DNS "
                    "Zone in the hub. Include monitoring (Log Analytics + "
                    "App Insights) and Managed Identity on the Web App."
                ),
            },
        },
        judge_rubric=(
            "Count `ask_user_call_count` in the transcript. PASS if and only "
            "if it equals exactly 1 (the agent asked once, before generating; "
            "a second round to re-ask topology/access/hub/identity would mean "
            "count >= 2). Inspect `ask_user_rounds[0].user_answers` to confirm "
            "the user did provide substantive 'Other' notes — selected: "
            "['Other'] with notes covering hub-and-spoke / Web App / VNet "
            "integration / monitoring / identity. Do NOT count individual "
            "questions inside a single ask_user round as separate rounds — "
            "one round can carry multiple sub-questions. FAIL only if "
            "ask_user_call_count > 1."
        ),
    ),
    Scenario(
        number=3,
        name="Rendered PNG attaches to final assistant message",
        skill_id="shared:architect",
        message=(
            "Draw an architecture diagram of a web app behind an Application "
            "Gateway. Hub-and-spoke. Web App via VNet integration. Include "
            "monitoring and a Managed Identity. Render and show me the result."
        ),
        expected_tools_all=["generate_drawio_from_python"],
        require_attachment_mime="image/png",
        # Pre-specified prompt — but Architect may still ask_user out of caution.
        # Provide an Other-text fallback that re-confirms everything.
        auto_answer={
            "": {
                "selected": ["Other"],
                "notes": (
                    "Already specified: hub-and-spoke, Web App via VNet "
                    "integration, monitoring on, Managed Identity on."
                ),
            },
        },
        judge_rubric=(
            "Did the final assistant message describe the rendered diagram "
            "(at least one sentence about what it shows) AND was a PNG "
            "attachment present? Pass only if both are true."
        ),
    ),
    Scenario(
        number=4,
        name="Engineer hands off diagrams to Architect",
        skill_id="shared:chat-with-kb",
        message=(
            "Draw me a diagram of our environment showing a Web App with "
            "a Key Vault and a SQL database."
        ),
        forbidden_tools=["generate_file", "validate_drawio",
                         "generate_drawio_from_python", "patch_drawio_cell"],
        judge_rubric=(
            "Did the agent decline to draw the diagram inline AND clearly "
            "suggest switching to the Azure Architect skill (mentioning "
            "'Architect' or 'Azure Architect' explicitly)? Pass only if "
            "both are true and no diagram tool was called."
        ),
    ),
    Scenario(
        number=5,
        name="Default tier refuses execute tools",
        skill_id="shared:kb-searcher",
        message=(
            "Run 'az group list' to show all my resource groups. Use az_cli."
        ),
        forbidden_tools=["az_cli", "run_shell", "generate_file"],
        judge_rubric=(
            "Did the agent decline to run az_cli AND explain that this skill "
            "is read-only (or that the user should switch to Engineer or "
            "Architect for execute access)? Pass only if both are true. "
            "Note: the agent may use az_resource_graph to get resource group "
            "names read-only — that's fine, not a failure."
        ),
    ),
    Scenario(
        number=6,
        name="Drawio-diagrammer uses patch_drawio_cell for nudges",
        skill_id="shared:drawio-diagrammer",
        message=(
            "I have an existing diagram at output/sanity-test-diagram.drawio "
            "with a cell id 'appgw'. Move it 100px to the right using "
            "patch_drawio_cell."
        ),
        expected_tools_any=["patch_drawio_cell"],
        forbidden_tools=["generate_drawio_from_python"],
        judge_rubric=(
            "Did the agent attempt to use patch_drawio_cell (even if the file "
            "doesn't exist and the patch fails)? Pass only if patch_drawio_cell "
            "was invoked; the file-not-found error is acceptable since the "
            "goal is to verify the tool choice."
        ),
    ),
    Scenario(
        number=7,
        name="Long iterative diagram turn still terminates",
        skill_id="shared:architect",
        message=(
            "Draw a simple Web App diagram, then iterate on it three times: "
            "first add Key Vault with a private endpoint, then add an Azure "
            "SQL database, then add Application Insights. Do all of this in "
            "one turn, rendering after each step. Don't ask any clarifying "
            "questions — I want the default layout."
        ),
        expected_tools_any=["generate_drawio_from_python"],
        require_terminal_done=True,
        # Defensive answers in case Architect ignores the "don't ask" instruction.
        auto_answer={
            "": {
                "selected": ["Other"],
                "notes": "Use sensible defaults; don't ask again.",
            },
        },
        judge_rubric=(
            "Look at `terminal_done_event_fired` in the transcript. PASS if "
            "and only if it is `true`. This field reflects the SSE protocol's "
            "`done` event from the backend — its presence means the "
            "orchestrator did not freeze and the chat turn completed at the "
            "protocol level. Do NOT use the agent's final text as evidence "
            "of completion: text inviting the user to continue (\"want me to "
            "iterate further?\", \"shall I add X?\") is normal Phase 5 "
            "behaviour in the architect skill and does NOT mean the turn "
            "didn't terminate. Iteration-budget exhaustion is also fine — "
            "the goal of this scenario is protocol-level termination, not "
            "semantic completion of every requested sub-task."
        ),
    ),
    Scenario(
        number=8,
        name="Real-info skill exercise: doc/web lookup",
        skill_id="shared:architect",
        message=(
            "Which Azure region is currently cheapest for general-purpose D-series "
            "compute? Cite your source."
        ),
        expected_tools_any=[
            "fetch_ms_docs", "web_search", "search_azure_updates", "web_fetch",
        ],
        judge_rubric=(
            "Did the agent ground its answer in a tool call to fetch_ms_docs, "
            "web_search, search_azure_updates, or web_fetch — and cite the "
            "source URL in its response? Pass only if both are true. A "
            "response that names a region without any tool call is a fail "
            "(it would be hallucinating from training data)."
        ),
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight
# ─────────────────────────────────────────────────────────────────────────────

def preflight() -> bool:
    """Verify backend is up before running any scenarios."""
    print(f"Pre-flight: checking {BASE_URL}/healthz ...", end=" ", flush=True)
    try:
        r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
        if r.status_code != 200:
            print(f"FAIL (HTTP {r.status_code})")
            return False
    except httpx.ConnectError:
        print("FAIL (connection refused)")
        print()
        print("Backend is not running. Start it with:")
        print("    cd backend && uvicorn app.main:app --port 8000")
        print()
        print("And make sure backend/.env has DEV_AUTH_BYPASS=true.")
        return False
    except Exception as e:
        print(f"FAIL ({e})")
        return False
    print("OK")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# SSE consumer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    scenario_number: int
    scenario_name: str
    skill_id: str
    message: str
    conversation_id: Optional[int] = None
    tool_calls: list[dict] = field(default_factory=list)  # {name, call_id}
    tool_results: list[dict] = field(default_factory=list)  # {name, call_id, content}
    assistant_text: str = ""
    questions_asked: list[dict] = field(default_factory=list)
    questions_answered: int = 0
    approvals_resolved: int = 0
    final_attachments: list[dict] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    terminal_done: bool = False
    duration_ms: int = 0
    programmatic_verdict: dict[str, Any] = field(default_factory=dict)
    judge_verdict: dict[str, Any] = field(default_factory=dict)


def _select_auto_answer(question_text: str, auto_answer: dict[str, dict]) -> dict | None:
    """Match a question to its configured auto-answer by substring."""
    if not auto_answer:
        return None
    qt = (question_text or "").lower()
    for key, ans in auto_answer.items():
        if key == "" or key.lower() in qt:
            return ans
    return None


def _submit_question_answer(question_id: str, answers: list[dict]) -> bool:
    try:
        r = httpx.post(
            f"{BASE_URL}/api/questions/{question_id}/answer",
            json={"answers": answers},
            headers=HEADERS,
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _approve(approval_id: str) -> bool:
    try:
        r = httpx.post(
            f"{BASE_URL}/api/approvals/{approval_id}",
            json={"action": "approve"},
            headers=HEADERS,
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def run_scenario(s: Scenario, conv_id_for_resume: Optional[int]) -> RunResult:
    """Drive one scenario through the SSE stream, capturing the transcript."""
    result = RunResult(
        scenario_number=s.number,
        scenario_name=s.name,
        skill_id=s.skill_id,
        message=s.message,
    )

    body: dict[str, Any] = {"message": s.message}
    if s.resume_from is not None and conv_id_for_resume is not None:
        body["conversation_id"] = conv_id_for_resume
    else:
        body["skill_id"] = s.skill_id

    lock = threading.Lock()
    pending_q: list[dict] = []
    pending_a: list[dict] = []
    stream_done = threading.Event()

    def read_sse(resp: httpx.Response) -> None:
        event_type = ""
        try:
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        d = json.loads(line[6:].strip())
                    except json.JSONDecodeError:
                        continue
                    with lock:
                        result.events.append(event_type)

                    if event_type == "token":
                        with lock:
                            result.assistant_text += d.get("text", "")
                    elif event_type == "tool_call_start":
                        with lock:
                            result.tool_calls.append({
                                "name": d.get("name"),
                                "call_id": d.get("call_id"),
                            })
                    elif event_type == "tool_result":
                        with lock:
                            result.tool_results.append({
                                "name": d.get("name"),
                                "call_id": d.get("call_id"),
                                "content": (d.get("content") or "")[:2000],
                            })
                    elif event_type == "approval_required":
                        with lock:
                            pending_a.append(d)
                    elif event_type == "question_required":
                        with lock:
                            result.questions_asked.append(d)
                            pending_q.append(d)
                        if s.early_exit_on_question:
                            # Stop the stream; we've observed what we needed.
                            try:
                                resp.close()
                            except Exception:
                                pass
                            break
                    elif event_type == "done":
                        with lock:
                            result.conversation_id = d.get("conversation_id")
                            result.terminal_done = True
                    elif event_type == "error":
                        with lock:
                            result.errors.append(d.get("message", "unknown"))
        except Exception as e:
            with lock:
                result.errors.append(f"SSE read: {e}")
        finally:
            stream_done.set()

    start = time.time()
    try:
        client = httpx.Client(
            base_url=BASE_URL,
            headers={**HEADERS, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(connect=10, read=240, write=10, pool=10),
        )
        resp = client.send(
            client.build_request("POST", "/api/chat", json=body),
            stream=True,
        )
        if resp.status_code != 200:
            result.errors.append(f"HTTP {resp.status_code}: {resp.read().decode()[:300]}")
            resp.close()
            client.close()
            result.duration_ms = int((time.time() - start) * 1000)
            return result

        reader = threading.Thread(target=read_sse, args=(resp,), daemon=True)
        reader.start()

        # Drive approvals + ask_user answers until stream finishes or budget exhausts.
        budget_s = s.budget_s
        poll_start = time.time()
        while not stream_done.is_set() and (time.time() - poll_start) < budget_s:
            stream_done.wait(timeout=0.5)
            with lock:
                approvals_to_handle = list(pending_a)
                pending_a.clear()
                questions_to_handle = list(pending_q)
                pending_q.clear()

            if s.auto_approve:
                for a in approvals_to_handle:
                    aid = a.get("approval_id")
                    if aid and _approve(aid):
                        with lock:
                            result.approvals_resolved += 1

            for q in questions_to_handle:
                qid = q.get("question_id")
                items: list[dict] = q.get("questions", [])
                # Decide whether to answer: if auto_answer is empty for the
                # scenario, skip — scenario 1 wants the question to pause.
                if not s.auto_answer:
                    continue
                answers: list[dict] = []
                for item in items:
                    qtext = item.get("question") or item.get("header") or ""
                    ans = _select_auto_answer(qtext, s.auto_answer)
                    if ans is None:
                        # Default: pick the first non-Other option.
                        first_opt = next(
                            (o.get("label") for o in item.get("options", [])
                             if (o.get("label") or "").lower() != "other"),
                            "Other",
                        )
                        ans = {"selected": [first_opt]}
                    entry: dict = {
                        "question": qtext,
                        "selected": ans.get("selected", []),
                    }
                    if ans.get("notes"):
                        entry["notes"] = ans["notes"]
                    answers.append(entry)
                if qid and _submit_question_answer(qid, answers):
                    with lock:
                        result.questions_answered += 1

        reader.join(timeout=5)
        resp.close()
        client.close()
    except Exception as e:
        result.errors.append(str(e))

    # After the stream ends, fetch the final assistant message's attachments
    # by reading the conversation. Skill snapshots are frozen; we just need
    # the most recent assistant message in this conversation.
    if result.conversation_id is not None:
        try:
            r = httpx.get(
                f"{BASE_URL}/api/conversations/{result.conversation_id}",
                headers=HEADERS, timeout=10,
            )
            if r.status_code == 200:
                conv = r.json()
                assistants = [
                    m for m in conv.get("messages", [])
                    if m.get("role") == "assistant"
                ]
                if assistants:
                    last = assistants[-1]
                    raw_att = last.get("attachments_json")
                    if raw_att:
                        try:
                            result.final_attachments = json.loads(raw_att)
                        except json.JSONDecodeError:
                            result.final_attachments = []
        except Exception as e:
            result.errors.append(f"conv-fetch: {e}")

    result.duration_ms = int((time.time() - start) * 1000)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Programmatic verdict
# ─────────────────────────────────────────────────────────────────────────────

def programmatic_verdict(s: Scenario, r: RunResult) -> dict:
    """Apply programmatic checks. Returns { pass, fails, notes }.
    `fails` are hard violations of expectations; `notes` are informational
    observations (errors during fetch, etc.) that the judge can weigh in on.
    """
    fails: list[str] = []
    notes: list[str] = []
    called = [tc["name"] for tc in r.tool_calls]

    if s.expected_tools_any and not any(t in called for t in s.expected_tools_any):
        fails.append(
            f"none of expected_tools_any={s.expected_tools_any} called "
            f"(called={called})"
        )

    for t in s.expected_tools_all:
        if t not in called:
            fails.append(f"expected tool {t!r} not called (called={called})")

    for t in s.forbidden_tools:
        if t in called:
            fails.append(f"forbidden tool {t!r} was called")

    if s.require_attachment_mime:
        mimes = [a.get("mime") for a in r.final_attachments]
        if s.require_attachment_mime not in mimes:
            fails.append(
                f"expected attachment mime {s.require_attachment_mime!r}, got {mimes}"
            )

    if s.require_terminal_done and not r.terminal_done:
        fails.append("stream did not emit a terminal 'done' event")

    if r.errors:
        notes.append(f"errors observed: {r.errors[:3]}")

    return {"pass": not fails, "fails": fails, "notes": notes}


# ─────────────────────────────────────────────────────────────────────────────
# Judge LLM
# ─────────────────────────────────────────────────────────────────────────────

JUDGE_SYSTEM = (
    "You are an impartial judge evaluating an AI agent's behaviour in a single "
    "chat turn. You will be given: a scenario rubric, the user's prompt, the "
    "list of tool calls the agent made (in order), the agent's final text "
    "response, and any attachments. Apply ONLY the rubric — do not impose your "
    "own preferences. Respond with strict JSON: "
    '{"pass": true|false, "reasoning": "one-paragraph explanation citing '
    'specific evidence from the transcript"}. No prose outside the JSON.'
)


def judge_llm_call(s: Scenario, r: RunResult) -> dict:
    """Send the transcript + rubric to gpt-5.4-mini for scoring."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

    if not (endpoint and api_key and deployment):
        return {"skipped": True, "reason": "Azure OpenAI env vars not set"}

    # Walk tool_calls + tool_results in tandem so each ask_user round is paired
    # with the user's actual answers (pulled from the ask_user tool result).
    # Sending these as separate flat fields confused the judge in 2026-05-19:
    # `[[q1, q2, q3]]` was read as "three rounds" instead of "one round with
    # three questions", and the absence of user_answers left the judge unable
    # to verify the user had provided sufficient detail.
    ask_user_rounds: list[dict] = []
    for q in r.questions_asked:
        questions = [item.get("question") for item in q.get("questions", [])]
        call_id = q.get("call_id")
        user_answers: list[dict] = []
        for tr in r.tool_results:
            if tr.get("name") == "ask_user" and tr.get("call_id") == call_id:
                try:
                    payload = json.loads(tr.get("content") or "")
                    user_answers = payload.get("answers", []) if isinstance(payload, dict) else []
                except json.JSONDecodeError:
                    user_answers = []
                break
        ask_user_rounds.append({
            "round_number": len(ask_user_rounds) + 1,
            "questions": questions,
            "user_answers": user_answers,
        })
    ask_user_call_count = sum(1 for tc in r.tool_calls if tc.get("name") == "ask_user")

    transcript = {
        "user_prompt": s.message,
        "skill_id": s.skill_id,
        "tool_calls_in_order": [tc["name"] for tc in r.tool_calls],
        "tool_results_truncated": [
            {"name": tr["name"], "content": tr["content"][:500]}
            for tr in r.tool_results
            # Skip ask_user results here — they're surfaced under
            # ask_user_rounds[].user_answers in a cleaner shape.
            if tr.get("name") != "ask_user"
        ],
        "agent_final_text": r.assistant_text[:4000],
        "attachments": [
            {"url": a.get("url"), "mime": a.get("mime")} for a in r.final_attachments
        ],
        "ask_user_call_count": ask_user_call_count,
        "ask_user_rounds": ask_user_rounds,
        "terminal_done_event_fired": r.terminal_done,
    }

    user_msg = (
        f"Scenario: {s.name}\n\n"
        f"Rubric:\n{s.judge_rubric}\n\n"
        f"Transcript JSON:\n{json.dumps(transcript, indent=2)}"
    )

    url = (
        f"{endpoint}/openai/deployments/{deployment}/chat/completions"
        f"?api-version={api_version}"
    )
    body = {
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 800,
    }
    try:
        resp = httpx.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        if resp.status_code != 200:
            return {"skipped": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        verdict = json.loads(content)
        return {"skipped": False, **verdict}
    except Exception as e:
        return {"skipped": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-judge", action="store_true", help="Skip the judge LLM step")
    p.add_argument(
        "--only", type=str, default="",
        help="Comma-separated scenario numbers to run (e.g. '1,2,3')",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not preflight():
        return 2

    selected = SCENARIOS
    if args.only:
        wanted = {int(x.strip()) for x in args.only.split(",") if x.strip()}
        selected = [s for s in SCENARIOS if s.number in wanted]

    print()
    print("=" * 78)
    print(f"NEXUS SANITY CHAT TEST — {len(selected)} scenario(s)")
    if args.no_judge:
        print("Judge LLM: disabled (--no-judge)")
    print("=" * 78)
    print()

    results: list[RunResult] = []
    conv_for_resume: Optional[int] = None
    for s in selected:
        print(f"[{s.number}] {s.name}")
        print(f"    skill: {s.skill_id}")
        print(f"    msg:   {s.message[:90]}{'...' if len(s.message) > 90 else ''}")
        sys.stdout.flush()

        r = run_scenario(s, conv_for_resume)
        # If this scenario continues a previous one, the next scenario could
        # use this conversation. Not used in the current default scenario list
        # but the plumbing exists.
        conv_for_resume = r.conversation_id

        r.programmatic_verdict = programmatic_verdict(s, r)
        prog_pass = "PASS" if r.programmatic_verdict["pass"] else "FAIL"
        print(f"    programmatic: {prog_pass}")
        for f in r.programmatic_verdict.get("fails", []):
            print(f"      [fail] {f}")
        for n in r.programmatic_verdict.get("notes", []):
            print(f"      [note] {n}")

        if not args.no_judge:
            r.judge_verdict = judge_llm_call(s, r)
            if r.judge_verdict.get("skipped"):
                print(f"    judge: SKIPPED ({r.judge_verdict.get('reason')})")
            elif r.judge_verdict.get("error"):
                print(f"    judge: ERROR ({r.judge_verdict.get('error')})")
            else:
                judge_pass = "PASS" if r.judge_verdict.get("pass") else "FAIL"
                print(f"    judge: {judge_pass}")
                reasoning = r.judge_verdict.get("reasoning", "")
                if reasoning:
                    print(f"      reasoning: {reasoning[:300]}")

        print(f"    tools called: {[tc['name'] for tc in r.tool_calls]}")
        if r.final_attachments:
            print(f"    attachments:  {[a.get('url') for a in r.final_attachments]}")
        print(f"    duration: {r.duration_ms} ms")
        print()
        results.append(r)

    # Write results JSON
    out = {
        "results": [
            {
                "number": r.scenario_number,
                "name": r.scenario_name,
                "skill_id": r.skill_id,
                "message": r.message,
                "conversation_id": r.conversation_id,
                "tool_calls": r.tool_calls,
                "tool_results": r.tool_results,
                "assistant_text": r.assistant_text,
                "questions_asked": r.questions_asked,
                "questions_answered": r.questions_answered,
                "approvals_resolved": r.approvals_resolved,
                "final_attachments": r.final_attachments,
                "terminal_done": r.terminal_done,
                "errors": r.errors,
                "duration_ms": r.duration_ms,
                "programmatic_verdict": r.programmatic_verdict,
                "judge_verdict": r.judge_verdict,
            }
            for r in results
        ],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Results written to {RESULTS_PATH}")

    # Exit code: 0 if all programmatic + judge passed; 1 otherwise
    any_fail = False
    for r in results:
        if not r.programmatic_verdict.get("pass"):
            any_fail = True
        jv = r.judge_verdict
        if jv and not jv.get("skipped") and not jv.get("error") and jv.get("pass") is False:
            any_fail = True
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
