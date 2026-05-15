"""
Learnings tool — persistent memory of mistakes, known issues, and solutions.
The agent reads this before executing commands and updates it after discovering issues.
"""

import logging
import os
import re
from datetime import datetime, timezone

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

# Path relative to backend/ working directory
_LEARN_FILE = os.path.join("kb_data", "learnings", "learn.md")


# Tool-output nouns the agent might tell its future self to ignore.
# Tool-agnostic — these terms apply equally to drawio validator hints,
# az_advisor recommendations, az_policy violations, az_cli warnings,
# orchestrator retry hints, etc. Plural is matched explicitly below.
_TOOL_NOUN = (
    r"validator|validation|check|tool|recommendation|hint|warning|"
    r"violation|suggestion|advisor"
)
# Adjectives the agent uses to discredit tool output.
_DISCREDIT_ADJ = (
    r"aggressive|strict|noisy|broken|wrong|incorrect|verbose|unreliable|"
    r"buggy|useless|bogus|spammy"
)

# Patterns that indicate the agent is trying to use the learning system to
# suppress tool guidance — telling its future self to ignore a validator,
# skip a check, or treat a tool's output as wrong/too-strict. This is a
# self-poisoning loop: the agent observes a hint it doesn't want to act on,
# records "ignore that hint" as a learning, then on the next run the system
# prompt contains the override and the tool's guidance is silently dropped.
# Patterns are intentionally generic (the noun list covers any tool's
# advisory output) and accept singular/plural plus is/are forms. Block at
# write time AND filter at read time.
_OVERRIDE_PATTERNS = re.compile(
    r"("
    # "ignore the recommendation(s)" / "ignore X warnings"
    rf"ignore (the |its |these |those |all )?\w*\s*({_TOOL_NOUN})s?\b|"
    # "skip / disable / bypass / silence / suppress the check(s)"
    rf"(skip|disable|bypass|silence|suppress) (the |its |these |those |all )?\w*\s*({_TOOL_NOUN})s?\b|"
    # "validator is too strict" / "recommendations are too noisy"
    rf"({_TOOL_NOUN})s? (is|are) (too )?({_DISCREDIT_ADJ})\b|"
    # "too aggressive validator" / "too noisy hints"
    rf"(too )?({_DISCREDIT_ADJ}) ({_TOOL_NOUN})s?\b|"
    # "override the recommendation(s)"
    rf"override (the |its |these |those )?\w*\s*({_TOOL_NOUN})s?\b|"
    # "don't trust / follow / run / use the validator"
    rf"don'?t (trust|follow|run|use) (the )?({_TOOL_NOUN})s?\b"
    r")",
    re.IGNORECASE,
)


def _looks_like_override_attempt(*texts: str) -> bool:
    """Return True if any of the given strings reads like an attempt to
    use the learning system to suppress a tool's guidance (validator hints,
    warnings, recommendations) rather than record a real-world fact.
    """
    for t in texts:
        if t and _OVERRIDE_PATTERNS.search(t):
            return True
    return False


_OVERRIDE_REJECTION = (
    "Error: This entry looks like an attempt to suppress tool guidance "
    "rather than record a real-world fact. Learnings must NOT be used to "
    "tell future runs to ignore validator output, skip checks, or treat "
    "warnings as too strict. If a recommendation is genuinely wrong for "
    "the current task, surface that to the user in your reply — don't "
    "bake it into persistent memory.\n\n"
    "If you intended to record a known limitation as informational context "
    "(e.g. \"validate_drawio flags large vertices as containers — width >= "
    "300px is the threshold\"), rephrase factually: state what is, not "
    "what to ignore."
)


def _ensure_learn_file() -> str:
    """Ensure the learn.md file and directory exist. Return absolute path."""
    path = os.path.abspath(_LEARN_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Agent Learnings\n\n"
                    "This file records known issues, mistakes, and solutions "
                    "discovered during tool execution.\n"
                    "The agent consults this before running commands to avoid repeating errors.\n\n"
                    "---\n\n")
    return path


def _split_entries(content: str) -> tuple[str, list[str]]:
    """Split learn.md content into (header, entries).

    Robust to A1/A6: handles entries that lack a preceding newline by
    normalising all `## [` headers to start on a new line before splitting.
    Returns (header_text, [entry_strings]) where each entry starts with `## [`
    and is guaranteed to end with exactly `\n\n`.
    """
    # Normalise: collapse any whitespace run before ## [ into a single \n (A6)
    normalised = re.sub(r'\n*## \[', '\n## [', content)
    parts = normalised.split('\n## [')
    head = parts[0]
    # Normalise each entry to end with exactly \n\n (A1)
    entries = [("## [" + p).rstrip("\n") + "\n\n" for p in parts[1:]]
    return head, entries


def _filter_override_entries(content: str) -> str:
    """Strip entries that look like attempts to suppress tool guidance.

    Backstop for entries that pre-date the write-time guard or that slipped
    past it. Uses the robust `_split_entries` helper (handles missing-newline
    boundary — A6). The header is preserved verbatim.
    """
    if not content or "## [" not in content:
        return content
    head, entries = _split_entries(content)
    kept = [e for e in entries if not _OVERRIDE_PATTERNS.search(e)]
    if len(kept) == len(entries):
        return content
    dropped = len(entries) - len(kept)
    logger.info("Filtered %d override-attempt entries from learn.md", dropped)
    return head + ("\n" + "".join(kept) if kept else "")


def get_learnings_content() -> str:
    """Read learn.md content. Used by orchestrator to inject into system prompt."""
    path = _ensure_learn_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        content = _filter_override_entries(content)
        # Cap at 4KB to avoid bloating the system prompt
        if len(content) > 4096:
            content = content[:4096] + "\n... (truncated, use read_learnings for full content)"
        return content
    except Exception:
        return ""


class ReadLearningsTool(Tool):
    name = "read_learnings"
    description = (
        "Read the agent's learnings file (learn.md) which contains known issues, "
        "past mistakes, and verified solutions. Always check this before executing "
        "commands to avoid repeating known errors."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        path = _ensure_learn_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"Error reading learnings: {e}"


class UpdateLearningsTool(Tool):
    name = "update_learnings"
    description = (
        "Append a new learning entry to learn.md. Use this when you discover a mistake, "
        "a known issue, a corrected command syntax, or a solution that should be remembered. "
        "Each entry should include: what went wrong, why, and the correct approach."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Category: 'known-issue', 'syntax-fix', 'workaround', 'best-practice', or 'gotcha'",
                "enum": ["known-issue", "syntax-fix", "workaround", "best-practice", "gotcha"],
            },
            "tool_name": {
                "type": "string",
                "description": "Which tool this relates to (e.g., 'az_cli', 'run_shell', 'az_resource_graph')",
            },
            "summary": {
                "type": "string",
                "description": "One-line summary of the learning",
            },
            "details": {
                "type": "string",
                "description": "What went wrong, why, and the correct approach or command",
            },
        },
        "required": ["category", "summary", "details"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        category = args.get("category", "known-issue")
        tool_name = args.get("tool_name", "general") or "general"
        summary = args.get("summary", "")
        details = args.get("details", "")

        if not summary or not details:
            return "Error: summary and details are required"

        # A2: cap details at 4KB to prevent bloated entries
        _DETAILS_MAX = 4096
        if len(details) > _DETAILS_MAX:
            details = details[:_DETAILS_MAX] + " ... (truncated to 4 KB)"

        # A5: validate tool_name against the registry (lazy import avoids circular)
        from app.tools.base import TOOL_REGISTRY  # noqa: PLC0415
        _valid_tool_names = set(TOOL_REGISTRY.keys()) | {"general"}
        if tool_name not in _valid_tool_names:
            logger.warning(
                "update_learnings: unknown tool_name '%s', falling back to 'general'",
                tool_name,
            )
            tool_name = "general"

        # Block self-poisoning attempts: don't let the agent write a learning
        # whose effect is to tell future runs to ignore tool guidance.
        if _looks_like_override_attempt(summary, details):
            logger.warning(
                "Blocked override-attempt learning by %s: [%s] %s",
                user.email, category, summary[:120],
            )
            return _OVERRIDE_REJECTION

        path = _ensure_learn_file()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        new_entry = (
            f"## [{category}] {summary}\n"
            f"- **Date**: {timestamp}\n"
            f"- **Tool**: {tool_name}\n"
            f"- **Details**: {details}\n\n"
        )

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # Robust rotation using shared helper (A1: normalises separators;
            # A6: handles entries that lack a preceding newline).
            header, entries = _split_entries(content)

            # A4: Deduplication — replace an existing entry if it has the
            # same tool_name and a near-identical summary (first 60 chars,
            # case-insensitive) rather than appending a duplicate.
            summary_key = summary.lower()[:60]
            replaced = False
            for idx, existing in enumerate(entries):
                # Extract tool_name from existing entry body
                tool_match = re.search(r'\*\*Tool\*\*:\s*(.+?)(?:\n|$)', existing)
                ex_tool = tool_match.group(1).strip() if tool_match else ""
                # Extract summary from ## [category] summary header
                hdr_match = re.match(r'^## \[[^\]]+\]\s+(.+?)(?:\n|$)', existing)
                ex_summary = hdr_match.group(1).strip() if hdr_match else ""
                if ex_tool == tool_name and ex_summary.lower()[:60] == summary_key:
                    entries[idx] = new_entry.strip() + "\n\n"
                    replaced = True
                    break

            if not replaced:
                entries.append(new_entry.strip() + "\n\n")

            MAX_ENTRIES = 50
            if len(entries) > MAX_ENTRIES:
                entries = entries[-MAX_ENTRIES:]

            with open(path, "w", encoding="utf-8") as f:
                f.write(header + "\n" + "".join(entries))

            action = "updated" if replaced else "recorded"
            logger.info("Learning %s: [%s] %s", action, category, summary)
            return f"Learning {action}: [{category}] {summary} (Total entries: {len(entries)})"
        except Exception as e:
            return f"Error writing learning: {e}"
