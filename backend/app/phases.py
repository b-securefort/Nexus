"""
Phase gating for the Nexus team rollout.

This module is the SINGLE source of truth for which features are unlocked at
the current NEXUS_PHASE. It exists to support a phased introduction of Nexus
to a team — see gatesreadme.md (gitignored, at repo root) for the full
removal playbook and operator notes.

Three rules — break them and the cleanup story breaks with you:

1. NEVER read `settings.NEXUS_PHASE` directly outside this module. Always go
   through `is_enabled(...)`, `is_tool_enabled(...)`, `is_skill_enabled(...)`.
   The CI test `tests/test_phase_gates.py::test_no_raw_phase_checks` enforces
   this — call sites that bypass the registry can't be cleaned up
   mechanically when the day comes to delete this module.

2. Phase gates are TEMPORARY. Every entry has a `review_by` date. At full
   rollout the entire module + every call-site wrapper goes away. Permanent
   feature toggles (things that stay configurable after rollout) belong in
   `app.config.Settings`, NOT in PHASE_GATES.

3. Adding a new gate requires registering it in `PHASE_GATES` AND mapping the
   tool/skill name in `_TOOL_GATES` / `_SKILL_GATES` when applicable.
   `is_enabled("typo")` raises KeyError — no silent "gate off forever".
"""

from dataclasses import dataclass
from datetime import date

from app.config import get_settings


@dataclass(frozen=True)
class PhaseGate:
    """One phase gate.

    min_phase        — minimum NEXUS_PHASE that unlocks the gated feature.
    description      — human-readable explanation of what this gate controls.
    review_by        — date this gate should be removed or its review extended.
                       Past this date + currently active = CI fails.
    removal_criteria — what condition the operator should observe before
                       removing the gate (e.g. "P2 stable for 4 weeks").
    removal_action   — plain English instructions for the cleanup PR so
                       future-you (or a future Claude) knows exactly what to
                       inline / delete.
    """

    min_phase: int
    description: str
    review_by: date
    removal_criteria: str
    removal_action: str


# ── Gate registry ────────────────────────────────────────────────────────────
# Add a new gate here; reference it via is_enabled("name") at the call site.
#
# Naming convention:
#   tool_<tool_name>     — gates an entry in TOOL_REGISTRY
#   skill_<skill_slug>   — gates a SKILL.md folder under kb_data/skills/shared/
#   feature_<thing>      — anything else (an API endpoint, a code path)
PHASE_GATES: dict[str, PhaseGate] = {
    # ── Phase 1 — read-only Azure observer ──────────────────────────────────
    "tool_az_resource_graph": PhaseGate(
        min_phase=1,
        description="Azure Resource Graph KQL queries (read-only, no approval)",
        review_by=date(2026, 12, 1),
        removal_criteria=(
            "When the team has been on P1+ for several weeks and "
            "az_resource_graph is permanently enabled."
        ),
        removal_action=(
            "Remove this entry and the _TOOL_GATES mapping for "
            "'az_resource_graph'. resolve_tools() in app/tools/base.py will "
            "then let it through unconditionally."
        ),
    ),
    # ── Phase 2 — approval-gated executor + Engineer skill ──────────────────
    "tool_az_cli": PhaseGate(
        min_phase=2,
        description="General az CLI commands (approval-gated for mutations)",
        review_by=date(2027, 3, 1),
        removal_criteria=(
            "P2 has been live for the whole team for 4+ weeks with no "
            "incidents; team is comfortable with approval workflow."
        ),
        removal_action=(
            "Remove this entry and the _TOOL_GATES mapping for 'az_cli'."
        ),
    ),
    "tool_execute_script": PhaseGate(
        min_phase=2,
        description="PowerShell / shell script execution (always approval-gated)",
        review_by=date(2027, 3, 1),
        removal_criteria=(
            "Same trigger as tool_az_cli — both ship together in P2."
        ),
        removal_action=(
            "Remove this entry and the _TOOL_GATES mapping for "
            "'execute_script'."
        ),
    ),
    "skill_chat_with_kb": PhaseGate(
        min_phase=2,
        description="Azure Engineer skill (chat-with-kb tier)",
        review_by=date(2027, 3, 1),
        removal_criteria=(
            "P2 stable; the chat-with-kb skill is permanently visible to "
            "the team."
        ),
        removal_action=(
            "Remove this entry and the _SKILL_GATES mapping for "
            "'chat-with-kb'. load_shared_skills() in app/skills/shared.py "
            "will load it unconditionally."
        ),
    ),
    # ── Phase 3 — advanced surfaces ─────────────────────────────────────────
    "skill_architect": PhaseGate(
        min_phase=3,
        description="Azure Architect skill — ADR + WAF framing, inline diagrams",
        review_by=date(2027, 6, 1),
        removal_criteria="P3 stable; advanced skills permanently visible.",
        removal_action=(
            "Remove this entry and the _SKILL_GATES mapping for 'architect'."
        ),
    ),
    "skill_drawio_diagrammer": PhaseGate(
        min_phase=3,
        description="Drawio diagrammer specialist skill",
        review_by=date(2027, 6, 1),
        removal_criteria="Same trigger as skill_architect — both gate to P3.",
        removal_action=(
            "Remove this entry and the _SKILL_GATES mapping for "
            "'drawio-diagrammer'."
        ),
    ),
    "feature_personal_skills": PhaseGate(
        min_phase=3,
        description="User-created personal skills (CRUD endpoints + listing)",
        review_by=date(2027, 6, 1),
        removal_criteria=(
            "P3 stable; team has expressed need for personal-skill "
            "authoring."
        ),
        removal_action=(
            "Remove this entry, the is_enabled('feature_personal_skills') "
            "guard in app/api/skills.py::list_skills, and the "
            "require_personal_skills_phase dependency on the four "
            "/api/skills/personal endpoints."
        ),
    ),
}


# ── Tool / skill mappings ────────────────────────────────────────────────────
# Tools and skills whose names appear here are gated; everything else passes
# through unchanged. The values must reference a key in PHASE_GATES.
_TOOL_GATES: dict[str, str] = {
    "az_resource_graph": "tool_az_resource_graph",
    "az_cli": "tool_az_cli",
    "execute_script": "tool_execute_script",
}

_SKILL_GATES: dict[str, str] = {
    "chat-with-kb": "skill_chat_with_kb",
    "architect": "skill_architect",
    "drawio-diagrammer": "skill_drawio_diagrammer",
}


# ── Lookups ──────────────────────────────────────────────────────────────────
def is_enabled(gate_name: str) -> bool:
    """True when the gate is unlocked at the current NEXUS_PHASE.

    Raises KeyError on unknown gate names — typos surface at call time
    instead of silently leaving the gate "off forever".
    """
    gate = PHASE_GATES[gate_name]
    return get_settings().NEXUS_PHASE >= gate.min_phase


def is_tool_enabled(tool_name: str) -> bool:
    """True when the tool's gate is unlocked, OR the tool isn't gated."""
    gate_name = _TOOL_GATES.get(tool_name)
    if gate_name is None:
        return True
    return is_enabled(gate_name)


def is_skill_enabled(skill_name: str) -> bool:
    """True when the skill's gate is unlocked, OR the skill isn't gated."""
    gate_name = _SKILL_GATES.get(skill_name)
    if gate_name is None:
        return True
    return is_enabled(gate_name)


def phase_status() -> dict:
    """Structured snapshot for /healthz."""
    settings = get_settings()
    return {
        "phase": settings.NEXUS_PHASE,
        "gates": {
            name: {
                "enabled": is_enabled(name),
                "min_phase": gate.min_phase,
                "review_by": gate.review_by.isoformat(),
            }
            for name, gate in PHASE_GATES.items()
        },
    }


def overdue_gates(today: date | None = None) -> list[str]:
    """Gates past their review_by date AND currently unlocked at the running
    phase — i.e. the cleanup PR should have happened. Used by both the
    startup-time WARN log and the CI tripwire test.
    """
    today = today or date.today()
    settings = get_settings()
    return [
        name
        for name, gate in PHASE_GATES.items()
        if gate.review_by < today and settings.NEXUS_PHASE >= gate.min_phase
    ]


def format_startup_banner() -> str:
    """One-line summary for the startup log."""
    settings = get_settings()
    gate_summary = ", ".join(
        f"{name}={'on' if is_enabled(name) else 'off'}"
        for name in sorted(PHASE_GATES.keys())
    )
    return (
        f"Nexus phase={settings.NEXUS_PHASE} | gates: {{{gate_summary}}}"
    )
