"""
Advisory risk assessment for approval-gated tool calls (DESIGN.md §5 2026-06-04).

Every tool call that requires user approval is passed through an *independent*
review LLM that returns a risk tier (safe / caution / destructive) plus a
neutral one-line "what this command does" description, shown on the approval
card. It is advisory only — it never approves or denies; the human stays the
sole gate.

Two independent signals are combined:

  1. A deterministic floor — pattern matching over the resolved command. This is
     the load-bearing safety signal: a regex can't be talked out of flagging
     `rm -rf`. The floor sets the *minimum* tier.
  2. The review LLM — produces the description and may *raise* the tier when it
     spots a risk the rules missed, but can never lower it below the floor.

So a false "✓ safe" from the model can never downgrade a destructive command.
The review is run separately from the generation that proposed the command (the
generator is biased toward its own output) and is fed only the raw command —
never the generator's `reason`.

Fail-closed: on any review error, timeout, circuit-open, or disabled config, the
verdict is the floor raised to at least `caution` ("assessment unavailable") —
never `safe`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import AzureOpenAI

from app.agent import circuit_breaker
from app.config import get_settings

logger = logging.getLogger(__name__)

# Tiers, ordered low → high. The card maps these to ✓ / ⚠ / ⛔.
SAFE = "safe"
CAUTION = "caution"
DESTRUCTIVE = "destructive"
PENDING = "pending"  # transient state while the review is in flight

_TIER_ORDER = {SAFE: 0, CAUTION: 1, DESTRUCTIVE: 2}


@dataclass
class RiskVerdict:
    risk_level: str  # safe | caution | destructive
    description: str | None  # neutral "what this command does"; None if unavailable
    source: str  # "llm" | "floor" | "fallback" — for logging/audit


def _tier_max(a: str, b: str) -> str:
    return a if _TIER_ORDER.get(a, 1) >= _TIER_ORDER.get(b, 1) else b


# ── Deterministic floor ───────────────────────────────────────────────────────

# az subcommand tokens that imply irreversible data/identity loss. Matched as
# standalone tokens anywhere in the args list (global flags can't shield them).
_AZ_DESTRUCTIVE_TOKENS = {
    "delete", "purge", "remove", "destroy", "revoke",
}
# az read-only leaf verbs — these are genuinely safe even though az_cli is
# always approval-gated.
_AZ_READ_VERBS = {
    "list", "show", "get", "check", "exists", "wait", "version", "list-keys",
}

# Destructive shell patterns scanned in an execute_script body (lower-cased).
_SHELL_DESTRUCTIVE_SUBSTRINGS = (
    "rm -rf", "rm -fr", "rm -r ", "rm -f ",
    "remove-item",  # PowerShell — pair with -recurse/-force below for the worst case
    "rmdir /s", "rmdir /q", "del /s", "del /q", "del /f",
    "format-volume", "format ", "mkfs", "dd if=",
    "drop table", "drop database", "truncate table",
    "> /dev/sd", "clear-disk", "reset-",
)
_SHELL_DESTRUCTIVE_FORCE = ("remove-item",)  # destructive when combined with -recurse/-force


def _script_body(func_args: dict) -> str | None:
    """Read the resolved execute_script body, or None if it can't be resolved."""
    # Imported lazily to avoid a tools→agent import at module load.
    from app.tools.generic.execute_script import _resolve_script

    raw_path = func_args.get("path") or func_args.get("script") or func_args.get("file") or ""
    if not isinstance(raw_path, str) or not raw_path:
        return None
    script_path, err = _resolve_script(raw_path)
    if err or script_path is None:
        return None
    try:
        return script_path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def _shell_floor(body: str | None) -> str:
    """Floor for a shell-script body. Unreadable body → caution (can't verify)."""
    if body is None:
        return CAUTION
    low = body.lower()
    for sub in _SHELL_DESTRUCTIVE_SUBSTRINGS:
        if sub in low:
            return DESTRUCTIVE
    for cmd in _SHELL_DESTRUCTIVE_FORCE:
        if cmd in low and ("-recurse" in low or "-force" in low):
            return DESTRUCTIVE
    # Running an arbitrary host script is never "safe".
    return CAUTION


def deterministic_floor(tool_name: str, func_args: dict) -> str:
    """The minimum risk tier from pattern rules alone. Never raises."""
    try:
        if tool_name == "az_cli":
            tokens = [str(t).lower() for t in (func_args.get("args") or [])]
            if any(t in _AZ_DESTRUCTIVE_TOKENS for t in tokens):
                return DESTRUCTIVE
            non_flag = [t for t in tokens if not t.startswith("-")]
            if non_flag and any(v in _AZ_READ_VERBS for v in non_flag):
                return SAFE
            return CAUTION

        if tool_name == "execute_script":
            return _shell_floor(_script_body(func_args))

        if tool_name == "az_rest_api":
            method = str(func_args.get("method", "")).upper()
            if method == "DELETE":
                return DESTRUCTIVE
            if method in {"PUT", "PATCH", "POST"}:
                return CAUTION
            return SAFE

        if tool_name == "az_devops":
            # Only mutating az_devops calls reach approval; treat as caution.
            return CAUTION
    except Exception as e:  # noqa: BLE001 — floor must never break the approval path
        logger.warning("deterministic_floor failed for %s: %s", tool_name, str(e)[:120])
        return CAUTION

    # Unknown approval-gated tool — be conservative.
    return CAUTION


# ── Resolved-command rendering (what the reviewer sees) ────────────────────────


def render_command(tool_name: str, func_args: dict) -> str:
    """Human-readable command string, deliberately excluding the generator's
    `reason` so the reviewer judges the command cold."""
    if tool_name == "az_cli" and isinstance(func_args.get("args"), list):
        return "az " + " ".join(str(a) for a in func_args["args"])
    if tool_name == "execute_script":
        body = _script_body(func_args)
        name = func_args.get("path") or func_args.get("script") or func_args.get("file") or "?"
        if body is not None:
            return f"execute script {name}:\n{body[:4000]}"
        return f"execute script {name} (body could not be read)"
    if tool_name == "az_rest_api":
        method = func_args.get("method", "")
        url = func_args.get("url", "")
        body = func_args.get("body") or func_args.get("body_file") or ""
        return f"{method} {url}\n{body}".strip()
    # Fallback: dump args minus the generator's reason.
    return json.dumps({k: v for k, v in func_args.items() if k != "reason"}, default=str)


# ── Review LLM ─────────────────────────────────────────────────────────────────

_REVIEW_SYSTEM_PROMPT = """You are a security reviewer for an Azure platform-team assistant.

You are shown a command that the assistant wants to run on the user's behalf. The
user must approve it. Your job is to help them decide quickly by classifying the
command's risk and describing — in plain language — what it does.

You did NOT write this command. Judge it cold. Do not assume good intent.

Classify risk as exactly one of:
  - "safe"        : read-only or inspection only; no state change (list, show, get, query).
  - "caution"     : changes state but is recoverable or routine (create, update, start, deploy).
  - "destructive" : deletes data/resources, removes access, wipes credentials, is
                    hard or impossible to undo, or affects many resources at once.

When unsure between two tiers, pick the HIGHER one. A wrong "safe" on a destructive
command is the worst outcome — it trains the user to stop reading.

Write the description as ONE short sentence (max ~120 chars), neutral and factual:
state what the command does, not whether it is safe. No preamble, no "this command".

Respond ONLY as JSON:
{"risk": "safe" | "caution" | "destructive", "description": "<one sentence>"}
"""


def _get_client() -> AzureOpenAI:
    settings = get_settings()
    return AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        timeout=settings.RISK_REVIEW_TIMEOUT_SECONDS,
        max_retries=0,
    )


def assess_risk(tool_name: str, func_args: dict) -> RiskVerdict:
    """Return an advisory RiskVerdict for an approval-gated tool call.

    Synchronous (makes a blocking OpenAI call) — call via asyncio.to_thread from
    the orchestrator. Always returns; never raises.
    """
    settings = get_settings()
    floor = deterministic_floor(tool_name, func_args)

    # Review disabled, or breaker open → floor verdict, no LLM description.
    if not settings.RISK_REVIEW_ENABLED:
        return RiskVerdict(risk_level=floor, description=None, source="floor")
    try:
        circuit_breaker.check()
    except circuit_breaker.CircuitOpenError:
        return RiskVerdict(
            risk_level=_tier_max(floor, CAUTION),
            description="Risk assessment unavailable (service busy).",
            source="fallback",
        )

    command = render_command(tool_name, func_args)
    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": f"Tool: {tool_name}\nCommand:\n{command}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_completion_tokens=200,
            timeout=settings.RISK_REVIEW_TIMEOUT_SECONDS,
        )
        circuit_breaker.record_success()
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        llm_tier = str(parsed.get("risk", CAUTION)).lower()
        if llm_tier not in _TIER_ORDER:
            llm_tier = CAUTION
        description = str(parsed.get("description", "")).strip()[:200] or None
        # LLM can escalate above the floor but never downgrade below it.
        final = _tier_max(floor, llm_tier)
        logger.info(
            "Risk review for %s: floor=%s llm=%s final=%s", tool_name, floor, llm_tier, final
        )
        return RiskVerdict(risk_level=final, description=description, source="llm")
    except Exception as e:  # noqa: BLE001
        circuit_breaker.record_failure()
        logger.warning(
            "Risk review failed for %s (failing closed to >=caution): %s",
            tool_name, str(e)[:200],
        )
        return RiskVerdict(
            risk_level=_tier_max(floor, CAUTION),
            description="Risk assessment unavailable — review the command carefully.",
            source="fallback",
        )
