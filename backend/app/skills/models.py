"""Skill data model."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Skill:
    """Represents a skill (system prompt + tool allowlist)."""

    id: str  # "shared:architect" or "personal:my-architect"
    name: str  # slug
    display_name: str
    description: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    source: Literal["shared", "personal"] = "shared"
    # Per-skill decoder tuning for the main agent loop. None = inherit the
    # CHAT_REASONING_EFFORT / CHAT_VERBOSITY config defaults (see config.py).
    reasoning_effort: str | None = None  # minimal | low | medium | high
    verbosity: str | None = None  # low | medium | high
