from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from investorch.config import AppConfig, ConfigError

from .errors import APIError
from .routes import Host

router = APIRouter(prefix="/api/config")


class ConfigChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any]
    persist: StrictBool = True


class ResetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[str]


def available_config(request: Request) -> AppConfig:
    config = request.app.state.config
    if config is None or request.app.state.recovery_error:
        raise APIError(409, "recovery_required", "Repair the instance before editing configuration.")
    return config


def apply_changes(config: AppConfig, changes: ConfigChanges) -> dict[str, Any]:
    require_offline_path_changes(list(changes.changes))
    try:
        return config.update_many(changes.changes, persist=changes.persist)
    except ConfigError as exc:
        raise APIError(422, "invalid_configuration", str(exc), details={"errors": exc.errors}) from None
    except OSError:
        raise APIError(
            503, "configuration_write_failed", "Configuration could not be saved; previous values were retained."
        ) from None


def require_offline_path_changes(keys: list[str]) -> None:
    if any(key == "paths" or key.startswith("paths.") for key in keys):
        raise APIError(
            422,
            "offline_migration_required",
            "Data directories are read-only in Settings. Stop all clients, back up and copy the data, "
            "then edit the directory configuration offline and run investorch doctor before restarting.",
        )


@router.get("")
async def read_config(request: Request) -> dict[str, Any]:
    config = available_config(request)
    values = config.public(saved=True)
    values.pop("secrets", None)
    overrides = config.override_keys()
    if request.app.state.host is None:
        values = {"models": values["models"]}
        overrides = [key for key in overrides if key.startswith(("models.", "secrets."))]
    return {
        "values": values,
        "secrets": {name: {"configured": present} for name, present in config.secret_status().items()},
        "overrides": overrides,
        "missing": config.readiness(saved=True),
    }


@router.get("/schema")
async def read_config_schema(request: Request) -> dict[str, Any]:
    config = available_config(request)
    schema = config.describe()
    if request.app.state.host is None:
        schema["properties"] = {
            key: value for key, value in schema["properties"].items() if key in ("models", "secrets")
        }
    return schema


@router.patch("")
async def update_config(body: ConfigChanges, host: Host) -> dict[str, Any]:
    return apply_changes(host.config, body)


@router.post("/reset")
async def reset_config(body: ResetConfig, host: Host) -> dict[str, Any]:
    require_offline_path_changes(body.keys)
    try:
        return host.config.reset(body.keys)
    except ConfigError as exc:
        raise APIError(422, "invalid_configuration", str(exc), details={"errors": exc.errors}) from None
    except OSError:
        raise APIError(
            503, "configuration_write_failed", "Configuration could not be saved; previous values were retained."
        ) from None
