from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import tomlkit

from qmt_agent.config import PROJECT_CONFIG_PATH, AppConfig, load_config
from qmt_agent.initializer import initialize


def make_test_config(
    tmp_path: Path,
    overrides: dict[str, dict[str, Any]] | None = None,
    *,
    initialize_state: bool = True,
) -> AppConfig:
    project_dir = tmp_path / "project"
    shutil.copytree(PROJECT_CONFIG_PATH.parent, project_dir)

    project_config_path = project_dir / PROJECT_CONFIG_PATH.name
    project_document = tomlkit.parse(project_config_path.read_text(encoding="utf-8"))
    root = tmp_path / "root"
    project_document["paths"]["root"] = str(root)
    project_config_path.write_text(tomlkit.dumps(project_document), encoding="utf-8")

    if overrides:
        root.mkdir(parents=True)
        (root / "qmt.toml").write_text(tomlkit.dumps(overrides), encoding="utf-8")

    config = load_config(project_config_path)
    if initialize_state:
        initialize(config)
    return config
