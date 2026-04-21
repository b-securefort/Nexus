"""Skills API endpoints — list shared + personal, CRUD personal skills."""

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.auth.models import User
from app.db.engine import get_session
from app.deps import current_user
from app.skills.personal import (
    create_personal_skill,
    delete_personal_skill,
    get_personal_skill,
    list_personal_skills,
    update_personal_skill,
)
from app.skills.shared import get_shared_skills
from app.tools.base import list_tools, TOOL_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["skills"])

_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class SkillResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: str
    tools: list[str]
    source: str


class SkillDetailResponse(SkillResponse):
    system_prompt: str


class CreateSkillRequest(BaseModel):
    name: str
    display_name: str
    description: str = ""
    system_prompt: str
    tools: list[str] = []

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _SKILL_NAME_PATTERN.match(v):
            raise ValueError("Name must be lowercase alphanumeric + hyphens, 1-64 chars, starting with alphanumeric")
        return v

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        if not v or len(v) > 100:
            raise ValueError("Display name must be 1-100 characters")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("Description must be 0-500 characters")
        return v

    @field_validator("system_prompt")
    @classmethod
    def validate_system_prompt(cls, v: str) -> str:
        if not v or len(v) > 32000:
            raise ValueError("System prompt must be 1-32,000 characters")
        return v

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, v: list[str]) -> list[str]:
        for tool_name in v:
            if tool_name not in TOOL_REGISTRY:
                raise ValueError(f"Unknown tool: {tool_name}")
        return v


class UpdateSkillRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str | None) -> str | None:
        if v is not None and (not v or len(v) > 100):
            raise ValueError("Display name must be 1-100 characters")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 500:
            raise ValueError("Description must be 0-500 characters")
        return v

    @field_validator("system_prompt")
    @classmethod
    def validate_system_prompt(cls, v: str | None) -> str | None:
        if v is not None and (not v or len(v) > 32000):
            raise ValueError("System prompt must be 1-32,000 characters")
        return v

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for tool_name in v:
                if tool_name not in TOOL_REGISTRY:
                    raise ValueError(f"Unknown tool: {tool_name}")
        return v


class ToolResponse(BaseModel):
    name: str
    description: str
    requires_approval: bool


@router.get("/skills", response_model=list[SkillResponse])
async def list_skills(user: User = Depends(current_user)):
    """List shared + current user's personal skills."""
    results = []

    # Shared skills
    for skill in get_shared_skills().values():
        results.append(
            SkillResponse(
                id=skill.id,
                name=skill.name,
                display_name=skill.display_name,
                description=skill.description,
                tools=skill.tools,
                source=skill.source,
            )
        )

    # Personal skills
    with get_session() as session:
        for skill in list_personal_skills(session, user.oid):
            results.append(
                SkillResponse(
                    id=skill.id,
                    name=skill.name,
                    display_name=skill.display_name,
                    description=skill.description,
                    tools=skill.tools,
                    source=skill.source,
                )
            )

    return results


@router.get("/tools", response_model=list[ToolResponse])
async def list_available_tools(user: User = Depends(current_user)):
    """List all available tool names and descriptions."""
    return [
        ToolResponse(name=t.name, description=t.description, requires_approval=t.requires_approval)
        for t in list_tools()
    ]


@router.get("/skills/personal/{name}", response_model=SkillDetailResponse)
async def get_personal_skill_detail(name: str, user: User = Depends(current_user)):
    """Fetch one personal skill for editing."""
    with get_session() as session:
        skill = get_personal_skill(session, user.oid, name)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillDetailResponse(
            id=skill.id,
            name=skill.name,
            display_name=skill.display_name,
            description=skill.description,
            system_prompt=skill.system_prompt,
            tools=skill.tools,
            source=skill.source,
        )


@router.post("/skills/personal", response_model=SkillResponse, status_code=201)
async def create_skill(body: CreateSkillRequest, user: User = Depends(current_user)):
    """Create a new personal skill."""
    with get_session() as session:
        # Check for duplicate
        existing = get_personal_skill(session, user.oid, body.name)
        if existing:
            raise HTTPException(status_code=409, detail="Skill with this name already exists")

        skill = create_personal_skill(
            session=session,
            user_oid=user.oid,
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            system_prompt=body.system_prompt,
            tools=body.tools,
        )
        return SkillResponse(
            id=skill.id,
            name=skill.name,
            display_name=skill.display_name,
            description=skill.description,
            tools=skill.tools,
            source=skill.source,
        )


@router.put("/skills/personal/{name}", response_model=SkillResponse)
async def update_skill(name: str, body: UpdateSkillRequest, user: User = Depends(current_user)):
    """Update an existing personal skill."""
    with get_session() as session:
        skill = update_personal_skill(
            session=session,
            user_oid=user.oid,
            name=name,
            display_name=body.display_name,
            description=body.description,
            system_prompt=body.system_prompt,
            tools=body.tools,
        )
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillResponse(
            id=skill.id,
            name=skill.name,
            display_name=skill.display_name,
            description=skill.description,
            tools=skill.tools,
            source=skill.source,
        )


@router.delete("/skills/personal/{name}")
async def delete_skill(name: str, user: User = Depends(current_user)):
    """Soft delete a personal skill."""
    with get_session() as session:
        deleted = delete_personal_skill(session, user.oid, name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Skill not found")
        return {"status": "ok"}
