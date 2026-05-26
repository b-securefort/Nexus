"""
Shared skills loader — reads SKILL.md files from the KB repo's skills/shared/ directory.
"""

import logging
from pathlib import Path

import yaml

from app.config import get_settings
from app.skills.models import Skill

logger = logging.getLogger(__name__)

_shared_skills_cache: dict[str, Skill] = {}


def load_shared_skills() -> dict[str, Skill]:
    """Load all shared skills from the KB repo's skills/shared/ directory."""
    global _shared_skills_cache
    settings = get_settings()
    skills_dir = Path(settings.KB_REPO_LOCAL_PATH) / "skills" / "shared"

    if not skills_dir.exists():
        logger.warning("Shared skills directory not found: %s", skills_dir)
        _shared_skills_cache = {}
        return _shared_skills_cache

    new_cache: dict[str, Skill] = {}

    from app.phases import is_skill_enabled

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        if not is_skill_enabled(skill_dir.name):
            # Phase-gated off at the current NEXUS_PHASE — see app/phases.py.
            logger.info(
                "Shared skill '%s' gated off at current NEXUS_PHASE, skipping",
                skill_dir.name,
            )
            continue

        try:
            skill = _parse_skill_file(skill_dir.name, skill_file)
            if skill:
                new_cache[skill.name] = skill
                logger.debug("Loaded shared skill: %s", skill.name)
        except Exception as e:
            logger.warning("Failed to load shared skill %s: %s", skill_dir.name, str(e))

    _shared_skills_cache = new_cache
    logger.info("Loaded %d shared skills", len(_shared_skills_cache))
    return _shared_skills_cache


def get_shared_skills() -> dict[str, Skill]:
    """Get cached shared skills."""
    return _shared_skills_cache


def get_shared_skill(name: str) -> Skill | None:
    """Get a specific shared skill by name."""
    return _shared_skills_cache.get(name)


def audit_shared_skill_tool_allowlists() -> dict[str, list[str]]:
    """Warn about tool names listed in shared skill allowlists that aren't
    registered. Catches typos and stale references (e.g. the `diagram_gen`
    phantom found in 2026-05-19 sanity testing) at startup, before a user
    picks the skill and `resolve_tools` silently drops the missing name.

    Distinction this audit DOES make:
      - Known + enabled (no warning)
      - Known + disabled by config (no warning — operational, not drift)
      - Not in TOOL_REGISTRY at all → emit WARNING

    Known limitation: tools from a **disabled bundle** don't register at
    all and look "unknown" to this audit. In multi-bundle deployments
    (TOOL_BUNDLE_*_ENABLED=false for some teams) the warning may be a
    false positive. The log line explicitly names this case so an
    operator reading the warning has the context to decide.

    Must be called AFTER `init_tools()` and `load_shared_skills()`. Idempotent
    and safe to call multiple times. Returns the drift dict so tests and
    callers that want a structured handle (not just logs) have one;
    operational use is the WARNING log line.
    """
    from app.tools.base import TOOL_REGISTRY

    drift: dict[str, list[str]] = {}
    skills = get_shared_skills()
    if not skills:
        # Loader hasn't run, or no skills in this deploy. Don't pretend
        # to audit an empty set as "clean" — silent return.
        logger.debug("audit_shared_skill_tool_allowlists: no skills loaded, skipping")
        return drift

    for skill_name, skill in skills.items():
        unknown = [t for t in skill.tools if t not in TOOL_REGISTRY]
        if unknown:
            drift[skill_name] = unknown
            logger.warning(
                "Shared skill '%s' references %d tool name(s) not in TOOL_REGISTRY: %s. "
                "Either remove from the skill's tools allowlist, fix the typo, "
                "or confirm they live in a bundle this deploy has disabled "
                "(TOOL_BUNDLE_*_ENABLED=false). Unknown names are silently "
                "dropped by resolve_tools at chat time.",
                skill_name, len(unknown), unknown,
            )
    if not drift:
        logger.info(
            "Shared skill tool allowlists: all %d skills' tool references resolve to known tools",
            len(skills),
        )
    return drift


def _parse_skill_file(name: str, filepath: Path) -> Skill | None:
    """Parse a SKILL.md file with YAML frontmatter."""
    content = filepath.read_text(encoding="utf-8")

    # Parse YAML frontmatter
    if not content.startswith("---"):
        logger.warning("Skill %s: SKILL.md missing frontmatter", name)
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        logger.warning("Skill %s: SKILL.md has incomplete frontmatter", name)
        return None

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        logger.warning("Skill %s: Invalid YAML frontmatter: %s", name, str(e))
        return None

    if not isinstance(frontmatter, dict):
        logger.warning("Skill %s: Frontmatter is not a dict", name)
        return None

    display_name = frontmatter.get("display_name")
    if not display_name:
        logger.warning("Skill %s: Missing display_name, skipping", name)
        return None

    description = frontmatter.get("description", "")
    tools = frontmatter.get("tools", [])
    if not isinstance(tools, list):
        tools = []

    system_prompt = parts[2].strip()

    return Skill(
        id=f"shared:{name}",
        name=name,
        display_name=display_name,
        description=description,
        system_prompt=system_prompt,
        tools=[str(t) for t in tools],
        source="shared",
    )
