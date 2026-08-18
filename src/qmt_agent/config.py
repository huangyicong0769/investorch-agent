from __future__ import annotations

import math
import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tomlkit

REDACTED = "<redacted>"

DEFAULT_CONFIG = {
    "paths": {
        "workspace": "workspace",
        "state": ".qmt",
    },
    "model": {
        "name": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
    },
    "observability": {
        "summary_enabled": True,
    },
    "secrets": {},
}

RESTART_REQUIRED_KEYS = {
    "paths.workspace",
    "paths.state",
    "model.name",
    "model.base_url",
    "mcp.default_timeout_seconds",
}

class ConfigError(ValueError):
    pass


class AppConfig:
    def __init__(self, data: dict[str, dict[str, Any]], project_config_path: Path) -> None:
        self._data = data
        self.project_config_path = project_config_path

        self.root = Path(self._data["paths"]["root"]).expanduser().resolve()

        self._validate_data(self._data)

        _ = self.bootstrap_files

    @property
    def root_config_path(self) -> Path:
        return self.root / "qmt.toml"

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
        return _resolve_under_root(self.workspace_dir, self["execution.background_job_dir"], "execution.background_job_dir")

    @property
    def sessions_db(self) -> Path:
        return self.state_dir / "sessions.db"

    @property
    def bootstrap_files(self,) -> list[tuple[Path, Path]]:
        """
        Return configured bootstrap files as:

            (source_template, workspace_target)

        Source paths are relative to config/qmt.toml.
        Target paths are relative to the user workspace.
        """
        raw_files = self._data.get("bootstrap", {}).get("files", [])

        if not isinstance(raw_files, list):
            raise ConfigError("bootstrap.files must be an array")

        config_dir = self.project_config_path.parent.resolve()

        files: list[tuple[Path, Path]] = []
        seen_targets: set[Path] = set()

        for index, raw_file in enumerate(raw_files):
            if not isinstance(raw_file, dict):
                raise ConfigError("bootstrap.files entries must be tables")

            source = raw_file.get("source")
            target = raw_file.get("target")

            if not isinstance(source, str) or not source:
                raise ConfigError(f"bootstrap.files[{index}].source must be a non-empty string")

            if not isinstance(target, str) or not target:
                raise ConfigError(f"bootstrap.files[{index}].target must be a non-empty string")

            source_relative = Path(source)

            if source_relative.is_absolute():
                raise ConfigError("Bootstrap source paths must be relative to config/qmt.toml")

            source_path = (config_dir / source_relative).resolve()

            if not source_path.is_relative_to(config_dir):
                raise ConfigError(f"Bootstrap source escapes config directory: {source}")

            if not source_path.is_file():
                raise ConfigError(f"Bootstrap template not found: {source_path}")

            target_relative = Path(target).expanduser()

            if target_relative.is_absolute():
                raise ConfigError(f"bootstrap.files[{index}].target must be relative to workspace")

            lexical_target = Path(os.path.normpath(str(self.workspace_dir / target_relative)))
            if lexical_target.is_symlink():
                raise ConfigError(f"Bootstrap target is not a regular file: {lexical_target}")

            target_path = _resolve_under_root(self.workspace_dir, target, f"bootstrap.files[{index}].target")

            if target_path in seen_targets:
                raise ConfigError(f"Duplicate bootstrap target: {target}")

            seen_targets.add(target_path)

            files.append((source_path, target_path))

        return files

    @property
    def secrets(self) -> dict[str, str]:
        return dict(self._data.get("secrets", {}))

    def __getitem__(self, key: str) -> Any:
        """
        Internal access.

        Example:
            config["model.name"]
        """
        return self.get(key, redact=False)

    def get(self, key: str, *, redact: bool = True) -> Any:
        section, name = _split_key(key)

        try:
            value = self._data[section][name]
        except KeyError as exc:
            raise ConfigError(f"Unknown config key: {key}") from exc

        if redact and section == "secrets":
            return REDACTED

        return value

    def secret(self, name: str) -> str:
        """
        Internal access to a secret.

        Secrets should never be returned directly
        by Agent-facing tools.
        """
        value = self[f"secrets.{name}"]

        if not isinstance(value, str):
            raise ConfigError(f"Secret must be a string: {name}")

        return value

    def public(self) -> dict[str, Any]:
        """
        Return the effective configuration with
        secrets redacted.
        """
        data = deepcopy(self._data)

        if "secrets" in data:
            data["secrets"] = { key: REDACTED for key in data["secrets"] }

        return data

    def update(self, key: str, value: Any, *, persist: bool = True) -> dict[str, Any]:
        """
        Update one config value.

        persist=False:
            only update the current AppConfig.

        persist=True:
            also write the override to <root>/qmt.toml.
        """
        if key == "paths.root":
            raise ConfigError("paths.root cannot be changed at runtime")

        if key.startswith("bootstrap."):
            raise ConfigError("bootstrap cannot be changed at runtime")

        if key in RESTART_REQUIRED_KEYS and not persist:
            raise ConfigError(f"{key} requires persist=true and an application restart")

        section, name = _split_key(key)

        # New arbitrary config fields are not allowed.
        # New secrets are allowed.
        if section != "secrets":
            if section not in self._data or name not in self._data[section]:
                raise ConfigError(f"Unknown config key: {key}")

            current = self._data[section][name]

            if key == "mcp.default_timeout_seconds":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConfigError(f"{key} must be int or float")
            elif type(value) is not type(current):
                raise ConfigError(f"{key} must be {type(current).__name__}")

        else:
            if not isinstance(value, str):
                raise ConfigError("Secrets must be strings")

        candidate = deepcopy(self._data)
        candidate.setdefault(section, {})[name] = value

        # Validate the complete candidate before touching memory or disk.
        self._validate_data(candidate)

        # Write disk first. If writing fails,
        # runtime config remains unchanged.
        if persist:
            self._persist(key, value)

        requires_restart = key in RESTART_REQUIRED_KEYS

        if not requires_restart:
            self._data = candidate

        return {
            "key": key,
            "value": REDACTED if section == "secrets" else value,
            "persisted": persist,
            "applied": not requires_restart,
            "requires_restart": requires_restart,
        }

    def _validate_data(self, data: dict[str, dict[str, Any]]) -> None:
        _validate_config_data(data, self.root)

    def _resolve_root_path(self, key: str) -> Path:
        return _resolve_under_root(self.root, self[key], key)

    def _persist(self, key: str, value: Any) -> None:
        path = self.root_config_path

        if path.exists():
            document = tomlkit.parse(path.read_text(encoding="utf-8"))
        else:
            document = tomlkit.document()

        section, name = _split_key(key)

        if section not in document:
            document[section] = tomlkit.table()

        document[section][name] = value

        path.write_text(
            tomlkit.dumps(document),
            encoding="utf-8",
        )

        # root/qmt.toml contains secrets.
        if os.name == "posix":
            path.chmod(0o600)


def load_config(project_config_path: str | Path = "config/qmt.toml") -> AppConfig:
    project_path = Path(project_config_path).expanduser().resolve()

    if not project_path.is_file():
        raise ConfigError(f"Project config not found: {project_path}")

    project_data = _read_toml(project_path)

    # Secrets must live outside the repository.
    if project_data.get("secrets"):
        raise ConfigError("[secrets] must not appear in config/qmt.toml")

    root_value = project_data.get("paths", {}).get("root")

    if not isinstance(root_value, str):
        raise ConfigError("config/qmt.toml must define paths.root")

    root = Path(root_value).expanduser()

    if not root.is_absolute():
        raise ConfigError("paths.root must be absolute")

    root = root.resolve()

    if root.exists() and not root.is_dir():
        raise ConfigError(
            f"paths.root is not a directory: {root}"
        )

    root_config_path = root / "qmt.toml"

    if root_config_path.exists():
        if not root_config_path.is_file():
            raise ConfigError(f"Local config path is not a file: {root_config_path}")

        root_data = _read_toml(root_config_path)
    else:
        root_data = {}

    # root/qmt.toml cannot redirect itself.
    if "root" in root_data.get("paths", {}):
        raise ConfigError("paths.root must only be defined in config/qmt.toml")

    if "bootstrap" in root_data:
        raise ConfigError("[bootstrap] must only be defined in config/qmt.toml")

    data = _merge(DEFAULT_CONFIG, project_data, root_data)

    # Bootstrap root always wins.
    data.setdefault("paths", {})["root"] = str(root)

    return AppConfig(
        data=data,
        project_config_path=project_path,
    )


def _read_toml(path: Path,) -> dict[str, Any]:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)

    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc


def _merge(*configs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Merge shallow TOML sections.

    Later configs override earlier configs.
    """
    result: dict[str, dict[str, Any]] = {}

    for config in configs:
        for section, values in config.items():
            if not isinstance(values, dict):
                raise ConfigError(f"Config section [{section}] must be a table")

            result.setdefault(section, {}).update(values)

    return result


def _split_key(key: str) -> tuple[str, str]:
    try:
        section, name = key.split(".", 1)
    except ValueError as exc:
        raise ConfigError(f"Invalid config key: {key}") from exc

    if not section or not name:
        raise ConfigError(f"Invalid config key: {key}")

    return section, name


def _required_config_value(data: dict[str, Any], key: str) -> Any:
    section, name = _split_key(key)

    try:
        values = data[section]
        value = values[name]
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"Missing required config key: {key}") from exc

    return value


def _require_string(data: dict[str, Any], key: str) -> str:
    value = _required_config_value(data, key)

    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")

    return value


def _require_int(data: dict[str, Any], key: str, *, minimum: int = 0) -> int:
    value = _required_config_value(data, key)

    if type(value) is not int or value < minimum:
        raise ConfigError(f"{key} must be an integer >= {minimum}")

    return value


def _require_bool(data: dict[str, Any], key: str) -> bool:
    value = _required_config_value(data, key)

    if type(value) is not bool:
        raise ConfigError(f"{key} must be a boolean")

    return value


def _require_number(data: dict[str, Any], key: str, *, minimum: float = 0) -> int | float:
    value = _required_config_value(data, key)

    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)) or value < minimum:
        raise ConfigError(f"{key} must be a finite number >= {minimum}")

    return value


def _validate_config_data(data: dict[str, Any], root: Path) -> None:
    _require_string(data, "paths.root")
    _require_string(data, "model.name")
    _require_string(data, "model.base_url")

    _require_bool(data, "observability.summary_enabled")

    secrets = data.get("secrets", {})
    if not isinstance(secrets, dict) or any(not isinstance(value, str) for value in secrets.values()):
        raise ConfigError("secrets values must be strings")

    workspace = _resolve_under_root(root, _require_string(data, "paths.workspace"), "paths.workspace")
    state = _resolve_under_root(root, _require_string(data, "paths.state"), "paths.state")

    if workspace == root:
        raise ConfigError("paths.workspace must be a strict subdirectory of paths.root")

    if state == root:
        raise ConfigError("paths.state must be a strict subdirectory of paths.root")

    if workspace == state or workspace.is_relative_to(state) or state.is_relative_to(workspace):
        raise ConfigError("paths.workspace and paths.state must not overlap")

    _resolve_under_root(workspace, _require_string(data, "execution.background_job_dir"), "execution.background_job_dir")

    _require_int(data, "observability.summary_threshold", minimum=1)
    timezone = _require_string(data, "runtime.default_timezone")

    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ConfigError(f"runtime.default_timezone is not a valid IANA timezone: {timezone}") from exc

    default_timeout = _require_int(data, "execution.default_timeout_seconds", minimum=1)
    max_timeout = _require_int(data, "execution.max_timeout_seconds", minimum=1)

    if max_timeout < default_timeout:
        raise ConfigError("execution.max_timeout_seconds must be >= execution.default_timeout_seconds")

    _require_int(data, "explore.max_full_read_bytes", minimum=1)
    _require_int(data, "explore.max_read_chars", minimum=1)
    _require_int(data, "explore.max_search_results", minimum=1)
    _require_int(data, "explore.max_search_snippet_chars", minimum=1)

    _require_int(data, "calculate.max_expression_chars", minimum=1)
    _require_int(data, "calculate.max_nodes", minimum=1)
    _require_int(data, "calculate.max_integer_bits", minimum=1)
    _require_int(data, "calculate.max_abs_exponent", minimum=1)
    mcp_timeout = _require_number(data, "mcp.default_timeout_seconds", minimum=0.0)
    if mcp_timeout <= 0:
        raise ConfigError("mcp.default_timeout_seconds must be greater than 0")


def _resolve_under_root(root: Path, value: str, key: str) -> Path:
    relative = Path(value).expanduser()

    if relative.is_absolute():
        raise ConfigError(f"{key} must be relative to paths.root")

    resolved = (root / relative).resolve()

    if not resolved.is_relative_to(root):
        raise ConfigError(f"{key} escapes paths.root: {value}")

    return resolved
