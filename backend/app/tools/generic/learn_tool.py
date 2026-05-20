"""
Legacy learnings file helpers. The `ReadLearningsTool` and
`UpdateLearningsTool` classes that lived here have been removed as part of
the 2026-05-18 redesign — the agent no longer writes learnings via a tool
call. See `app/agent/learnings.py` for the orchestrator-owned write path,
`app/agent/learn_judge.py` for the LLM-judge filter, and DESIGN.md §5 for
the rationale.

This module is kept only for:
  - `_LEARN_FILE` path constant (used by the migration import)
  - `_split_entries` markdown parser (used by the migration import)
  - `_looks_like_override_attempt` regex guard (used by `learnings.py` as
    a cheap deterministic backstop before the LLM judge runs)
  - `_OVERRIDE_PATTERNS` / `_TOOL_NOUN` / `_DISCREDIT_ADJ` constants
    (exported for tests that exercise the regex behaviour)

Once the migration has run on every environment and the legacy `learn.md`
file is no longer referenced anywhere, this module can be deleted.
"""

import logging
import os
import re

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
    """Ensure the learn.md file and directory exist. Return absolute path.

    Kept for the one-time migration import; new writes do not go here.
    """
    path = os.path.abspath(_LEARN_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Agent Learnings (legacy)\n\n"
                    "This file is preserved as an archive of the pre-2026-05-18 "
                    "agent learnings model. New learnings live in the SQLite "
                    "`agent_learnings` table — see app/agent/learnings.py.\n\n"
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


# Tool classes removed 2026-05-18. The agent-facing path is closed; see
# app/agent/learnings.py for the orchestrator-owned write API.
