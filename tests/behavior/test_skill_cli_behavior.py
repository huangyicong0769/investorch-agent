from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from investorch.cli import parse_startup_args


@pytest.mark.parametrize("option", ["--sync", "--sync-force"])
def test_retired_synchronization_options_are_rejected(option: str) -> None:
    with pytest.raises(SystemExit) as error:
        parse_startup_args([option])

    assert error.value.code == 2


def test_update_installs_and_repairs_only_builtins_without_model_credentials(tmp_path: Path) -> None:
    environment = {**os.environ, "HOME": str(tmp_path)}
    first = subprocess.run(
        [sys.executable, "-m", "investorch", "--update"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert first.returncode == 0, first.stderr
    assert "Restart InvestOrch" in first.stdout
    root = tmp_path / ".investorch"
    assert not (root / "investorch.toml").exists()
    assert not (root / "state/portfolio.db").exists()
    assert not (root / "state/sessions.db").exists()
    skill = tmp_path / ".investorch/workspace/skills/skill-creator/SKILL.md"
    original = skill.read_text(encoding="utf-8")
    skill.write_text("modified built-in", encoding="utf-8")

    second = subprocess.run(
        [sys.executable, "-m", "investorch", "--update"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert second.returncode == 0, second.stderr
    assert "updated=1" in second.stdout
    assert skill.read_text(encoding="utf-8") == original


def test_update_does_not_migrate_existing_configuration_or_database(tmp_path: Path) -> None:
    root = tmp_path / ".investorch"
    (root / "state").mkdir(parents=True)
    config = root / "investorch.toml"
    config.write_text('[secrets]\nDEEPSEEK_API_KEY = "unused-secret"\n')
    database = root / "state/portfolio.db"
    database.write_bytes(b"preserve incompatible database")
    result = subprocess.run(
        [sys.executable, "-m", "investorch", "--update"],
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert config.read_text() == '[secrets]\nDEEPSEEK_API_KEY = "unused-secret"\n'
    assert database.read_bytes() == b"preserve incompatible database"
    assert not (root / "investorch.toml.pre-schema.bak").exists()
