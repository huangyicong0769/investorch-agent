from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from investorch.config_schema import ModelConnectionSchema, ReasoningEffort

from .config_routes import ConfigChanges, apply_changes, available_config
from .errors import APIError

router = APIRouter(prefix="/api/setup")


def setup_state(request: Request) -> dict[str, Any]:
    state = request.app.state
    if state.recovery_error or state.startup_error:
        return {"status": "recovery_required", "missing": [], "error": state.recovery_error or state.startup_error}
    if state.host is not None:
        return {"status": "ready", "missing": []}
    return {
        "status": "setup_required",
        "missing": state.config.readiness(saved=True) if state.config else [],
    }


def require_setup(request: Request) -> None:
    if setup_state(request)["status"] != "setup_required":
        raise APIError(409, "setup_unavailable", "Setup is available only before the application host starts.")


@router.get("/state")
async def read_setup_state(request: Request) -> dict[str, Any]:
    return setup_state(request)


@router.patch("/config")
async def save_setup_config(body: ConfigChanges, request: Request) -> dict[str, Any]:
    require_setup(request)
    if not body.persist or any(not key.startswith(("models.", "agents.", "secrets.")) for key in body.changes):
        raise APIError(
            422, "invalid_setup_fields", "Setup only saves model connections, Agent defaults and credentials."
        )
    return apply_changes(available_config(request), body)


class TestModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    base_url: str
    api_key: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort = "high"


@router.post("/test-model")
async def test_model(body: TestModelRequest, request: Request) -> dict[str, Any]:
    require_setup(request)
    # Reuse the domain's model validator, including reasoning-effort constraints.
    try:
        connection = ModelConnectionSchema.model_validate(
            {
                "base_url": body.base_url,
                "api_key_secret": "SETUP_TEST_KEY",
            }
        )
    except ValueError:
        raise APIError(422, "invalid_model", "The model fields do not match the configuration schema.") from None
    try:
        async with (
            asyncio.timeout(20),
            AsyncOpenAI(
                api_key=body.api_key,
                base_url=connection.base_url,
                timeout=15,
                max_retries=0,
            ) as client,
        ):
            await client.responses.create(
                model=body.name,
                input="Reply with OK.",
                max_output_tokens=32,
                reasoning={"effort": body.reasoning_effort},
            )
    except Exception:
        # Provider error bodies and URLs can contain submitted credentials.
        return {"ok": False, "message": "Connection test failed or timed out. Check the endpoint, model and API key."}
    return {"ok": True, "message": "The model responded successfully."}


@router.post("/complete")
async def complete_setup(request: Request) -> dict[str, Any]:
    if request.app.state.host is not None:
        return setup_state(request)
    require_setup(request)
    await request.app.state.start_host()
    return setup_state(request)


class TestConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    connection: str
    name: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort = "high"


@router.post("/test-connection")
async def test_saved_connection(body: TestConnectionRequest, request: Request) -> dict[str, Any]:
    """Probe saved Settings or Setup without starting another Agent Host."""
    from investorch.config import ConfigError
    from investorch.llm.oauth import ChatGPTAuthError

    config = available_config(request).snapshot()
    try:
        config.activate_saved_for_startup()
        connection = config.model_connection(body.connection)
        if connection.type == "chatgpt":
            token = await request.app.state.chatgpt_auth.access_token(connection.credentials)
            endpoint = "https://api.openai.com/v1"
        else:
            token = config.secret(connection.api_key_secret)
            endpoint = connection.base_url
    except ChatGPTAuthError as exc:
        raise APIError(exc.status, exc.code, str(exc)) from None
    except ConfigError:
        raise APIError(
            422, "invalid_connection", "Save a complete connection and its credentials before testing."
        ) from None
    try:
        async with (
            asyncio.timeout(20),
            AsyncOpenAI(api_key=token, base_url=endpoint, timeout=15, max_retries=0) as client,
        ):
            stream = await client.responses.create(
                model=body.name,
                input=[{"role": "user", "content": "Reply with OK."}],
                reasoning={"effort": body.reasoning_effort},
                store=False,
                stream=True,
            )
            async with stream:
                async for event in stream:
                    if event.type == "response.completed":
                        return {"ok": True, "message": "The model responded successfully."}
        return {"ok": False, "message": "The model response ended without completing."}
    except Exception:
        return {
            "ok": False,
            "message": "Connection test failed or timed out. Check model access, account usage and connection settings.",
        }
