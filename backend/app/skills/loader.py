"""
Unified skill loader — loads shared or personal skills by skill_id.
"""

import logging

from sqlmodel import Session

from app.skills.models import Skill
from app.skills.shared import get_shared_skill
from app.skills.personal import get_personal_skill

logger = logging.getLogger(__name__)


def load_skill(skill_id: str, user_oid: str, session: Session) -> Skill:
    """
    Load a skill by its ID.
    skill_id format: "shared:<name>" or "personal:<name>"
    """
    if ":" not in skill_id:
        raise ValueError(f"Invalid skill id format: {skill_id}")

    kind, name = skill_id.split(":", 1)

    if kind == "shared":
        skill = get_shared_skill(name)
        if not skill:
            raise ValueError(f"Shared skill not found: {name}")
        return skill

    elif kind == "personal":
        skill = get_personal_skill(session, user_oid, name)
        if not skill:
            raise ValueError(f"Personal skill not found: {name}")
        return skill

    else:
        raise ValueError(f"Invalid skill kind: {kind}")
