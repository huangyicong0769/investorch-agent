from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

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

ROOT_RELATIVE_PATHS = {
    "paths.workspace",
    "paths.state",
}


class ConfigError(ValueError):
    pass


class AppConfig:
    def __init__(self, data: dict[str, dict[str, Any]], project_config_path: Path) -> None:
        self._data = data
        self.project_config_path = project_config_path

        self.root = Path(self._data["paths"]["root"]).expanduser().resolve()

        # Validate root-relative paths once at startup.
        for key in ROOT_RELATIVE_PATHS:
            self._resolve_root_path(key)

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
    def sessions_db(self) -> Path:
        return self.state_dir / "sessions.db"

    @property
    def memory_db(self) -> Path:
        return self.state_dir / "memory.db"

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

        section, name = _split_key(key)

        # New arbitrary config fields are not allowed.
        # New secrets are allowed.
        if section != "secrets":
            if section not in self._data or name not in self._data[section]:
                raise ConfigError(f"Unknown config key: {key}")

            current = self._data[section][name]

            if type(value) is not type(current):
                raise ConfigError(f"{key} must be {type(current).__name__}")

        else:
            if not isinstance(value, str):
                raise ConfigError("Secrets must be strings")

        # Validate before touching memory or disk.
        if key in ROOT_RELATIVE_PATHS:
            _resolve_under_root(self.root, value, key)

        # Write disk first. If writing fails,
        # runtime config remains unchanged.
        if persist:
            self._persist(key, value)

        self._data.setdefault(section, {})[name] = value

        return {
            "key": key,
            "value": REDACTED if section == "secrets" else value,
            "persisted": persist,
        }

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

    root_config_path = _ensure_root(root)

    root_data = _read_toml(root_config_path)

    # root/qmt.toml cannot redirect itself.
    if "root" in root_data.get("paths", {}):
        raise ConfigError("paths.root must only be defined in config/qmt.toml")

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


def _resolve_under_root(root: Path, value: str, key: str) -> Path:
    relative = Path(value).expanduser()

    if relative.is_absolute():
        raise ConfigError(f"{key} must be relative to paths.root")

    resolved = (root / relative).resolve()

    if not resolved.is_relative_to(root):
        raise ConfigError(f"{key} escapes paths.root: {value}")

    return resolved


def _ensure_root(root: Path) -> Path:
    if root.exists() and not root.is_dir():
        raise ConfigError(f"paths.root is not a directory: {root}")

    root.mkdir(parents=True, exist_ok=True)

    config_path = root / "qmt.toml"
    mcp_config_path = root / "mcp.toml"

    if not mcp_config_path.exists():
        mcp_config_path.write_text(
            """# Local MCP server configuration.\n""",
            encoding="utf-8",
        )

    if not config_path.exists():
        config_path.write_text(
            """# Local QMT Agent configuration.
# Overrides config/qmt.toml and stores local secrets.

[secrets]
""",
            encoding="utf-8",
        )

        if os.name == "posix":
            config_path.chmod(0o600)
            mcp_config_path.chmod(0o600)

        raise ConfigError(f"Local config created at {config_path}. Please edit it to add your secrets and overrides.")

    return config_path