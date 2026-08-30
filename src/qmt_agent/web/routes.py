from importlib.metadata import version

from fastapi import APIRouter

APPLICATION_VERSION = version("qmt-agent-trader")
router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APPLICATION_VERSION}
