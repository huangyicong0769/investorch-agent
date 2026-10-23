from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import tomlkit

from investorch.config import REQUIRED_AGENT_ROLES, AppConfig, load_config
from investorch.initializer import initialize


def make_test_config(
    tmp_path: Path,
    overrides: dict[str, dict[str, Any]] | None = None,
    *,
    initialize_state: bool = True,
) -> AppConfig:
    root = (tmp_path / "root").resolve()
    data = load_config(root=root).public()
    local: dict[str, Any] = {
        "models": {
            "test": {
                "type": "openai_compatible",
                "base_url": "https://provider.example.test/v1",
                "api_key_secret": "TEST_API_KEY",
            },
        },
        "agents": {
            role: {
                "model": "test",
                "name": "test-model",
                "context_window_tokens": 1000000,
            }
            for role in REQUIRED_AGENT_ROLES
        },
        "secrets": {"TEST_API_KEY": "fixture-key-never-used-for-network"},
    }

    def merge(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = deepcopy(value)

    merge(local, overrides or {})
    merge(data, local)
    if overrides:
        root.mkdir(parents=True)
        (root / "investorch.toml").write_text(tomlkit.dumps(local), encoding="utf-8")
        config = load_config(root=root)
    else:
        config = AppConfig(data, local_overrides=local)
    if initialize_state:
        initialize(config)
    return config
