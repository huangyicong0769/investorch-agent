from __future__ import annotations

import os
import tempfile
import threading
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    REQUIRED_AGENT_ROLES as REQUIRED_AGENT_ROLES,
)
from investorch.config_schema import (
    AppConfigSchema,
    OAuthCredentialSchema,
    config_field_metadata,
    resolve_under_root,
)

REDACTED = "<redacted>"
LOCAL_CONFIG_TEMPLATE = "# Local InvestOrch configuration: overrides Python Schema defaults.\n\n[secrets]\n"


@dataclass(frozen=True, slots=True)
class ModelConnectionConfig:
    type: str
    base_url: str | None = None
    api_key_secret: str | None = None
    credentials: str | None = None


@dataclass(frozen=True, slots=True)
class AgentModelConfig:
    model: str
    name: str
    reasoning_effort: str
    context_window_tokens: int

    def __post_init__(self) -> None:
        if not self.model or not self.name or self.reasoning_effort not in REASONING_EFFORTS:
            raise ConfigError("Execution requires a connection, model name and supported reasoning effort")
        if type(self.context_window_tokens) is not int or self.context_window_tokens <= 0:
            raise ConfigError("context_window_tokens must be a positive integer")


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
        """String secrets available for API keys and MCP environment expansion."""
        return {key: value for key, value in self._data.get("secrets", {}).items() if isinstance(value, str)}

    def secret_values(self) -> list[str]:
        """Credential values that must be removed from diagnostics."""
        values = list(self.secrets.values())
        for secret in self._data["secrets"].values():
            if isinstance(secret, dict):
                for credential in (secret, secret.get("pending_rotation") or {}):
                    values.extend(
                        credential[key] for key in ("access_token", "refresh_token", "id_token") if credential.get(key)
                    )
        return values

    def __getitem__(self, key: str) -> Any:
        return self.get(key, redact=False)

    def get(self, key: str, *, redact: bool = True) -> Any:
        parts = _split_key(key)
        value = _config_value(self._data, parts, key)
        return REDACTED if redact and parts[0] == "secrets" else deepcopy(value)

    def model_connection(self, connection_id: str) -> ModelConnectionConfig:
        raw = self[f"models.{connection_id}"]
        required = ("base_url", "api_key_secret") if raw["type"] == "openai_compatible" else ("credentials",)
        if any(not raw[field] for field in required):
            raise ConfigError(f"models.{connection_id} is incomplete; run investorch web to complete Setup")
        return ModelConnectionConfig(**raw)

    def agent_model(self, role: str) -> AgentModelConfig:
        return self._agent_model(self._data, role)

    @staticmethod
    def _agent_model(data: dict[str, Any], role: str) -> AgentModelConfig:
        if role not in REQUIRED_AGENT_ROLES:
            raise ConfigError(f"Unknown Agent role: {role}")
        raw = dict(data["agents"][role])
        if not raw["model"] or not raw["name"]:
            raise ConfigError(f"agents.{role} requires model and name; complete Setup")
        if raw["context_window_tokens"] is None and role != "main":
            main = data["agents"]["main"]
            if (raw["model"], raw["name"]) == (main["model"], main["name"]):
                raw["context_window_tokens"] = main["context_window_tokens"]
        if raw["context_window_tokens"] is None:
            raise ConfigError(f"agents.{role}.context_window_tokens requires a positive integer")
        return AgentModelConfig(**raw)

    def resolve_model(
        self,
        base: AgentModelConfig,
        *,
        model: str | None = None,
        name: str | None = None,
        reasoning_effort: str | None = None,
        context_window_tokens: int | None = None,
    ) -> AgentModelConfig:
        connection_id = base.model if model is None else model
        self.model_connection(connection_id)
        if connection_id != base.model and any(
            value is None for value in (name, reasoning_effort, context_window_tokens)
        ):
            raise ConfigError("Changing model connection requires name, reasoning_effort and context_window_tokens")
        if name is not None and name != base.name and context_window_tokens is None:
            raise ConfigError("Changing model name requires context_window_tokens")
        return AgentModelConfig(
            model=connection_id,
            name=base.name if name is None else name,
            reasoning_effort=base.reasoning_effort if reasoning_effort is None else reasoning_effort,
            context_window_tokens=base.context_window_tokens
            if context_window_tokens is None
            else context_window_tokens,
        )

    def secret(self, name: str) -> str:
        value = self[f"secrets.{name}"]
        if not isinstance(value, str):
            raise ConfigError("OAuth credentials cannot be used as an API key or MCP secret")
        return value

    def readiness(self, *, saved: bool = False) -> list[str]:
        data = self._saved_data if saved else self._data
        missing = []
        for role in REQUIRED_AGENT_ROLES:
            agent = data["agents"][role]
            for field in ("model", "name"):
                if not agent[field]:
                    missing.append(f"agents.{role}.{field}")
            try:
                self._agent_model(data, role)
            except ConfigError:
                if agent["model"] and agent["name"]:
                    missing.append(f"agents.{role}.context_window_tokens")
            connection = data["models"].get(agent["model"])
            if connection is None:
                continue
            fields = ("base_url", "api_key_secret") if connection["type"] == "openai_compatible" else ("credentials",)
            missing.extend(f"models.{agent['model']}.{field}" for field in fields if not connection[field])
            if connection["type"] == "openai_compatible":
                name = connection["api_key_secret"]
                if name and not data["secrets"].get(name, "").strip():
                    missing.append(f"secrets.{name}")
            else:
                name = connection["credentials"]
                credential = data["secrets"].get(name)
                if name and (
                    not isinstance(credential, dict)
                    or not credential.get("access_token")
                    or "chatgpt.tokens.use.direct" not in credential.get("scopes", [])
                ):
                    missing.append(f"secrets.{name}.chatgpt_plan_use")
        return list(dict.fromkeys(missing))

    def public(self, *, saved: bool = False) -> dict[str, Any]:
        data = deepcopy(self._saved_data if saved else self._data)
        data["secrets"] = {key: REDACTED for key in data["secrets"]}
        return data

    def secret_status(self) -> dict[str, bool]:
        return {
            key: bool(value.strip()) if isinstance(value, str) else bool(value.get("access_token"))
            for key, value in self._saved_data["secrets"].items()
        }

    def oauth_credential(self, name: str, *, saved: bool = False) -> dict[str, Any] | None:
        with self._lock:
            data = self._saved_data if saved else self._data
            value = data["secrets"].get(name)
            if value is not None and not isinstance(value, dict):
                raise ConfigError("Credential name is already used by an API key")
            return deepcopy(value)

    def save_oauth_credential(self, name: str, value: dict[str, Any]) -> None:
        with self._lock:
            if not name or "." in name:
                raise ConfigError("Invalid credential name")
            self.oauth_credential(name)
            try:
                credential = OAuthCredentialSchema.model_validate(value).model_dump(exclude_none=True)
            except ValidationError:
                raise ConfigError("Invalid OAuth credential fields") from None
            self._save_auth_value(f"secrets.{name}", credential)

    def ensure_chatgpt_host_id(self) -> str:
        with self._lock:
            host_id = self._data["auth"]["chatgpt_host_id"]
            if host_id is None:
                host_id = f"urn:uuid:{uuid4()}"
                self._save_auth_value("auth.chatgpt_host_id", host_id)
            return host_id

    def _save_auth_value(self, key: str, value: Any) -> None:
        """Share Settings' lock and disk compare-and-swap, but apply authorization live."""
        current = self.root_config_path.read_bytes() if self.root_config_path.exists() else None
        if current != self._source_bytes:
            raise ConfigError("Configuration changed outside this instance; restart before saving authorization")
        document = tomlkit.parse(current.decode()) if current is not None else tomlkit.document()
        _set_config_value(document, _split_key(key), value)
        encoded = tomlkit.dumps(document).encode()
        overrides = _local_data(encoded, self.root_config_path)
        saved = _effective(self.root, overrides)
        active = deepcopy(self._data)
        _set_config_value(active, _split_key(key), value)
        active = _validated(active)
        _atomic_write(self.root_config_path, encoded)
        self._source_bytes, self._overrides, self._saved_data, self._data = encoded, overrides, saved, active

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
    if isinstance(data.get("models"), dict) and any(
        isinstance(value, dict) and set(value) & {"name", "reasoning_effort", "context_window_tokens"}
        for value in data["models"].values()
    ):
        raise ConfigError(
            "Breaking change: models now define connections; move role model names and parameters to agents. "
            "Back up the instance and follow the configuration upgrade guide. TOML is not migrated automatically."
        )
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
