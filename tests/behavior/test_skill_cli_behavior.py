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


def test_update_initializes_and_repairs_builtins_without_model_credentials(tmp_path: Path) -> None:
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
