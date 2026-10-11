import asyncio
from typing import Any

from fastapi import APIRouter, Request

from investorch.diagnostics import doctor_checks, doctor_exit_code

router = APIRouter(prefix="/api/system")


@router.get("")
async def system_status(request: Request) -> dict[str, Any]:
    checks = await asyncio.to_thread(doctor_checks, request.app.state.root)
    return {"checks": checks, "exit_code": doctor_exit_code(checks)}
