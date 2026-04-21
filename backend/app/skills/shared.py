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

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
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
