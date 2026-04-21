"""
Personal skills CRUD — stored in the personal_skills SQLite table.
"""

import json
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.db.models import PersonalSkill
from app.skills.models import Skill

logger = logging.getLogger(__name__)


def list_personal_skills(session: Session, user_oid: str) -> list[Skill]:
    """List all personal skills for a user."""
    stmt = (
        select(PersonalSkill)
        .where(PersonalSkill.user_oid == user_oid)
        .where(PersonalSkill.deleted_at.is_(None))  # type: ignore
    )
    rows = session.exec(stmt).all()
    return [_row_to_skill(row) for row in rows]


def get_personal_skill(session: Session, user_oid: str, name: str) -> Skill | None:
    """Get a personal skill by name for a user."""
    stmt = (
        select(PersonalSkill)
        .where(PersonalSkill.user_oid == user_oid)
        .where(PersonalSkill.name == name)
        .where(PersonalSkill.deleted_at.is_(None))  # type: ignore
    )
    row = session.exec(stmt).first()
    return _row_to_skill(row) if row else None


def create_personal_skill(
    session: Session,
    user_oid: str,
    name: str,
    display_name: str,
    description: str,
    system_prompt: str,
    tools: list[str],
) -> Skill:
    """Create a new personal skill."""
    row = PersonalSkill(
        user_oid=user_oid,
        name=name,
        display_name=display_name,
        description=description,
        system_prompt=system_prompt,
        tools_json=json.dumps(tools),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _row_to_skill(row)


def update_personal_skill(
    session: Session,
    user_oid: str,
    name: str,
    display_name: str | None = None,
    description: str | None = None,
    system_prompt: str | None = None,
    tools: list[str] | None = None,
) -> Skill | None:
    """Update an existing personal skill."""
    stmt = (
        select(PersonalSkill)
        .where(PersonalSkill.user_oid == user_oid)
        .where(PersonalSkill.name == name)
        .where(PersonalSkill.deleted_at.is_(None))  # type: ignore
    )
    row = session.exec(stmt).first()
    if not row:
        return None

    if display_name is not None:
        row.display_name = display_name
    if description is not None:
        row.description = description
    if system_prompt is not None:
        row.system_prompt = system_prompt
    if tools is not None:
        row.tools_json = json.dumps(tools)

    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _row_to_skill(row)


def delete_personal_skill(session: Session, user_oid: str, name: str) -> bool:
    """Soft-delete a personal skill. Returns True if found and deleted."""
    stmt = (
        select(PersonalSkill)
        .where(PersonalSkill.user_oid == user_oid)
        .where(PersonalSkill.name == name)
        .where(PersonalSkill.deleted_at.is_(None))  # type: ignore
    )
    row = session.exec(stmt).first()
    if not row:
        return False

    row.deleted_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    return True


def _row_to_skill(row: PersonalSkill) -> Skill:
    """Convert a DB row to a Skill model."""
    return Skill(
        id=f"personal:{row.name}",
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        system_prompt=row.system_prompt,
        tools=json.loads(row.tools_json),
        source="personal",
    )
