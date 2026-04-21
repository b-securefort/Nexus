"""
Learnings tool — persistent memory of mistakes, known issues, and solutions.
The agent reads this before executing commands and updates it after discovering issues.
"""

import logging
import os
from datetime import datetime, timezone

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

# Path relative to backend/ working directory
_LEARN_FILE = os.path.join("kb_data", "learnings", "learn.md")


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


def get_learnings_content() -> str:
    """Read learn.md content. Used by orchestrator to inject into system prompt."""
    path = _ensure_learn_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
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
        tool_name = args.get("tool_name", "general")
        summary = args.get("summary", "")
        details = args.get("details", "")

        if not summary or not details:
            return "Error: summary and details are required"

        path = _ensure_learn_file()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        entry = (
            f"## [{category}] {summary}\n"
            f"- **Date**: {timestamp}\n"
            f"- **Tool**: {tool_name}\n"
            f"- **Details**: {details}\n\n"
        )

        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info("Learning recorded: [%s] %s", category, summary)
            return f"Learning recorded: [{category}] {summary}"
        except Exception as e:
            return f"Error writing learning: {e}"
