from __future__ import annotations

import os
import tempfile
import threading
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import ValidationError

from investorch.config_schema import (
    FOLLOW_UP_BEHAVIORS as FOLLOW_UP_BEHAVIORS,
)
from investorch.config_schema import (
    PERMISSION_MODES as PERMISSION_MODES,
)
from investorch.config_schema import (
    REASONING_EFFORTS as REASONING_EFFORTS,
)
from investorch.config_schema import (
    REQUIRED_MODEL_AGENTS as REQUIRED_MODEL_AGENTS,
)
from investorch.config_schema import (
    AppConfigSchema,
    config_field_metadata,
    resolve_under_root,
)

REDACTED = "<redacted>"
LOCAL_CONFIG_TEMPLATE = "# Local InvestOrch configuration: overrides Python Schema defaults.\n\n[secrets]\n"


@dataclass(frozen=True, slots=True)
class ModelConfig:
    name: str
    base_url: str
    api_key_secret: str
    reasoning_effort: str
    context_window_tokens: int | None = None


class ConfigError(ValueError):
    def __init__(self, message: str, *, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def _validated(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return AppConfigSchema.model_validate(data).model_dump()
    except ValidationError as exc:
        # Pydantic's default exception includes input values, including secrets.
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        fields = [{"path": ".".join(map(str, error["loc"])), "message": error["msg"]} for error in errors]
        raise ConfigError("; ".join(f"{item['path']}: {item['message']}" for item in fields), errors=fields) from None


class AppConfig:
    def __init__(
        self, data: dict[str, Any], *, local_overrides: dict[str, Any] | None = None, source_bytes: bytes | None = None
    ) -> None:
        self._data = _validated(data)
        self.root = Path(self._data["paths"]["root"])
        self._saved_data = deepcopy(self._data)
        self._overrides = deepcopy(local_overrides or {})
        self._source_bytes = source_bytes
        self._lock = threading.RLock()

    @property
    def root_config_path(self) -> Path:
        return self.root / "investorch.toml"

    @property
    def mcp_config_path(self) -> Path:
        return self.root / "mcp.toml"

    @property
    def workspace_dir(self) -> Path:
        return self._resolve_root_path("paths.workspace")

    @property
    def state_dir(self) -> Path:
        return self._resolve_root_path("paths.state")

    @property
    def background_job_dir(self) -> Path:
        return resolve_under_root(
            self.workspace_dir, self["execution.background_job_dir"], "execution.background_job_dir"
        )

    @property
    def cnequity_config_path(self) -> Path:
        return self._resolve_root_path("cnequity.config_path")

    @property
    def rqalpha_bundle_dir(self) -> Path:
        return self._resolve_root_path("backtest.rqalpha_bundle_dir")

    @property
    def backtest_artifact_dir(self) -> Path:
        return resolve_under_root(self.workspace_dir, self["backtest.artifact_dir"], "backtest.artifact_dir")

    @property
    def sessions_db(self) -> Path:
        return self.state_dir / "sessions.db"

    @property
    def portfolio_db(self) -> Path:
        return self.state_dir / "portfolio.db"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def log_path(self) -> Path:
        return self.log_dir / "investorch.log"

    @property
    def session_journal_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def secrets(self) -> dict[str, str]:
        return dict(self._data.get("secrets", {}))

    def __getitem__(self, key: str) -> Any:
        return self.get(key, redact=False)

    def get(self, key: str, *, redact: bool = True) -> Any:
        parts = _split_key(key)
        value = _config_value(self._data, parts, key)
        return REDACTED if redact and parts[0] == "secrets" else deepcopy(value)

    def model(self, agent: str) -> ModelConfig:
        raw = self[f"models.{agent}"]
        if any(raw[field] is None for field in ("name", "base_url", "api_key_secret")):
            raise ConfigError(f"models.{agent} is not configured; run investorch web to complete Setup")
        return ModelConfig(**raw)

    def secret(self, name: str) -> str:
        return self[f"secrets.{name}"]

    def readiness(self, *, saved: bool = False) -> list[str]:
        data = self._saved_data if saved else self._data
        missing = []
        for role in REQUIRED_MODEL_AGENTS:
            model = data["models"][role]
            for field in ("name", "base_url", "api_key_secret"):
                if not model[field]:
                    missing.append(f"models.{role}.{field}")
            name = model["api_key_secret"]
            if name and not data["secrets"].get(name, "").strip():
                missing.append(f"secrets.{name}")
        return list(dict.fromkeys(missing))

    def public(self, *, saved: bool = False) -> dict[str, Any]:
        data = deepcopy(self._saved_data if saved else self._data)
        data["secrets"] = {key: REDACTED for key in data["secrets"]}
        return data

    def secret_status(self) -> dict[str, bool]:
        return {key: bool(value.strip()) for key, value in self._saved_data["secrets"].items()}

    def describe(self) -> dict[str, Any]:
        return AppConfigSchema.model_json_schema()

    def override_keys(self) -> list[str]:
        def leaves(table: dict, prefix: str = "") -> list[str]:
            keys = []
            for name, value in table.items():
                key = f"{prefix}.{name}" if prefix else name
                keys.extend(leaves(value, key) if isinstance(value, dict) else [key])
            return keys

        return leaves(self._overrides)

    def snapshot(self) -> AppConfig:
        with self._lock:
            result = AppConfig(self._data, local_overrides=self._overrides, source_bytes=self._source_bytes)
            result._saved_data = deepcopy(self._saved_data)
            return result

    def ensure_local_file(self) -> bool:
        with self._lock:
            if self.root_config_path.exists():
                return False
            document = tomlkit.parse(LOCAL_CONFIG_TEMPLATE)
            for key, value in self._overrides.items():
                document[key] = value
            _atomic_write(self.root_config_path, tomlkit.dumps(document).encode())
            self._source_bytes = self.root_config_path.read_bytes()
            return True

    def update(self, key: str, value: Any, *, persist: bool = True) -> dict[str, Any]:
        result = self.update_many({key: value}, persist=persist)
        field = result["fields"][key]
        return {"key": key, "value": REDACTED if key.startswith("secrets.") else value, "persisted": persist, **field}

    def update_many(self, changes: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        with self._lock:
            return self._update(changes, persist=persist)

    def reset(self, keys: list[str]) -> dict[str, Any]:
        with self._lock:
            return self._update({}, persist=True, reset_keys=keys)

    def _update(self, changes: dict[str, Any], *, persist: bool, reset_keys: list[str] | None = None) -> dict[str, Any]:
        keys = list(changes) + list(reset_keys or [])
        if not keys:
            raise ConfigError("Provide at least one configuration field")
        modes = {}
        for key in keys:
            _split_key(key)
            try:
                modes[key] = config_field_metadata(key).get("apply_mode", "hot")
            except (KeyError, ValueError) as exc:
                raise ConfigError(f"Unknown config key: {key}") from exc
            if modes[key] == "immutable":
                raise ConfigError(f"{key} cannot be changed at runtime")
            if modes[key] == "restart_required" and not persist:
                raise ConfigError(f"{key} requires persist=true and an application restart")
        if any(a != b and b.startswith(a + ".") for a in keys for b in keys):
            raise ConfigError("Configuration changes must not contain overlapping keys")

        overrides = deepcopy(self._overrides)
        for key, value in changes.items():
            _set_config_value(overrides, _split_key(key), value)
        for key in reset_keys or []:
            _delete_config_value(overrides, _split_key(key))
        saved = _effective(self.root, overrides) if persist else deepcopy(self._saved_data)
        active = deepcopy(self._data)
        for key in keys:
            if modes[key] == "hot":
                if key in changes:
                    value = changes[key]
                else:
                    try:
                        value = _config_value(saved, _split_key(key), key)
                    except ConfigError:
                        _delete_config_value(active, _split_key(key))
                        continue
                _set_config_value(active, _split_key(key), value)
        active = _validated(active)
        if persist:
            current_bytes = self.root_config_path.read_bytes() if self.root_config_path.exists() else None
            if current_bytes != self._source_bytes:
                raise ConfigError(
                    f"{self.root_config_path} changed outside this configuration instance; restart before saving"
                )
            document = tomlkit.parse(current_bytes.decode()) if current_bytes is not None else tomlkit.document()
            for key, value in changes.items():
                if value is None:
                    _delete_config_value(document, _split_key(key))
                else:
                    _set_config_value(document, _split_key(key), value)
            for key in reset_keys or []:
                _delete_config_value(document, _split_key(key))
            # TOML has no null: null clears a nullable override to its Schema default.
            encoded = tomlkit.dumps(document).encode()
            roundtrip = _effective(self.root, _local_data(encoded, self.root_config_path))
            if roundtrip != saved:
                raise ConfigError("Use reset to remove an override; null must resolve to the Schema default")
            _atomic_write(self.root_config_path, encoded)
            self._source_bytes = encoded
            self._saved_data = saved
            self._overrides = _local_data(encoded, self.root_config_path)
        self._data = active
        fields = {
            key: {"applied": mode == "hot", "requires_restart": mode == "restart_required"}
            for key, mode in modes.items()
        }
        return {
            "updated": keys,
            "persisted": persist,
            "applied": all(v["applied"] for v in fields.values()),
            "requires_restart": any(v["requires_restart"] for v in fields.values()),
            "fields": fields,
        }

    def _resolve_root_path(self, key: str) -> Path:
        return resolve_under_root(self.root, self[key], key)


def load_config(root: Path | None = None) -> AppConfig:
    root = Path(root).expanduser() if root is not None else Path.home() / ".investorch"
    if not root.is_absolute():
        raise ConfigError("paths.root must be absolute")
    root = root.resolve()
    if root.exists() and not root.is_dir():
        raise ConfigError(f"paths.root is not a directory: {root}")
    path = root / "investorch.toml"
    source = path.read_bytes() if path.exists() else None
    overrides = _local_data(source, path) if source is not None else {}
    data = _effective(root, overrides)
    return AppConfig(data, local_overrides=overrides, source_bytes=source)


def _read_toml_bytes(source: bytes, path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(source.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise ConfigError(f"Invalid TOML in {path}; correct the file before starting InvestOrch") from None


def _local_data(source: bytes, path: Path) -> dict[str, Any]:
    data = _read_toml_bytes(source, path)
    if isinstance(data.get("paths"), dict) and "root" in data["paths"]:
        raise ConfigError("paths.root cannot be redirected by Local TOML")
    return data


def _effective(root: Path, overrides: dict[str, Any]) -> dict[str, Any]:
    defaults = AppConfigSchema(paths={"root": str(root)}).model_dump()
    return _validated(_merge(defaults, overrides))


def _merge(*configs: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for config in configs:
        for key, value in config.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = _merge(result[key], value)
            else:
                result[key] = deepcopy(value)
    return result


def _split_key(key: str) -> tuple[str, ...]:
    parts = tuple(key.split("."))
    if len(parts) < 2 or any(not part for part in parts):
        raise ConfigError(f"Invalid config key: {key}")
    return parts


def _config_value(data: dict[str, Any], parts: tuple[str, ...], key: str) -> Any:
    value: Any = data
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise ConfigError(f"Unknown config key: {key}")
        value = value[part]
    return value


def _set_config_value(data: dict[str, Any], parts: tuple[str, ...], value: Any) -> None:
    table = data
    for part in parts[:-1]:
        if part not in table:
            table[part] = {}
        child = table[part]
        if not isinstance(child, dict):
            raise ConfigError(f"Config key is not a table: {part}")
        table = child
    table[parts[-1]] = deepcopy(value)


def _delete_config_value(data: dict[str, Any], parts: tuple[str, ...]) -> None:
    table = data
    for part in parts[:-1]:
        if part not in table:
            return
        table = table[part]
        if not isinstance(table, dict):
            return
    table.pop(parts[-1], None)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
