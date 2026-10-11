from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from investorch.application.skill_sessions import SkillAction
from investorch.presentation import serialize_session_record
from investorch.storage import get_session

from .errors import APIError
from .routes import ConfirmRequest, Host

router = APIRouter(prefix="/api")


class EnableSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class SkillSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: SkillAction
    request_id: str = Field(min_length=1)
    name: str | None = None


@router.get("/skills")
async def list_skills(host: Host) -> dict[str, Any]:
    return {"skills": host.skills.list()}


@router.get("/skills/{name}")
async def inspect_skill(name: str, host: Host, resource: str | None = None) -> dict[str, Any]:
    try:
        return host.skills.inspect_content(name, resource)
    except ValueError as exc:
        status = 404 if str(exc).startswith("Unregistered Skill:") else 422
        raise APIError(status, "skill_unavailable", str(exc)) from None


@router.patch("/skills/{name}")
async def enable_skill(name: str, body: EnableSkillRequest, host: Host) -> dict[str, Any]:
    try:
        return host.skills.set_enabled(name, body.enabled)
    except ValueError as exc:
        raise APIError(422, "invalid_skill_change", str(exc)) from None


@router.delete("/skills/{name}")
async def remove_skill(name: str, body: ConfirmRequest, host: Host) -> dict[str, Any]:
    if not body.confirm:
        raise APIError(400, "confirmation_required", "Confirm deletion of this custom Skill.")
    try:
        return host.skills.remove(name)
    except ValueError as exc:
        raise APIError(422, "invalid_skill_change", str(exc)) from None


@router.post("/skill-sessions")
async def start_skill_session(body: SkillSessionRequest, host: Host) -> dict[str, Any]:
    if not body.request_id.strip():
        raise APIError(422, "invalid_request_id", "Request ID must not be empty.")
    try:
        result = await host.skill_sessions.start(action=body.action, request_id=body.request_id, name=body.name)
    except ValueError as exc:
        raise APIError(422, "invalid_skill_workflow", str(exc)) from None
    record = await asyncio.to_thread(get_session, host.config.sessions_db, result.session_id)
    assert record is not None
    return {"session": serialize_session_record(record), "run_id": result.run_id, "started": result.started}
