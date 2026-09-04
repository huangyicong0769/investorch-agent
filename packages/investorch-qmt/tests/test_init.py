from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from investorch_qmt.config import ConfigError, default_paths, initialize_config, load_config


def run_cli(local_app_data: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(local_app_data)
    return subprocess.run(
        ["investorch-qmt", *args],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows deployment path contract")
def test_init_creates_valid_config_and_log_directory_without_revealing_token(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"

    completed = run_cli(local_app_data, "init")

    paths = default_paths(local_app_data / "InvestOrch" / "QMT")
    config = load_config(paths.config)
    assert completed.returncode == 0
    assert "Initialized InvestOrch QMT." in completed.stdout
    assert f"Config: {paths.config}" in completed.stdout
    assert "investorch-qmt token show" in completed.stdout
    assert config.auth.token not in completed.stdout
    assert completed.stderr == ""
    assert paths.log.parent.is_dir()
    assert len(config.auth.token) >= 32


@pytest.mark.skipif(os.name != "nt", reason="Windows deployment path contract")
def test_init_accepts_existing_directory_but_refuses_existing_config(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    root = local_app_data / "InvestOrch" / "QMT"
    root.mkdir(parents=True)

    first = run_cli(local_app_data, "init")
    original = (root / "investorch-qmt.toml").read_bytes()
    second = run_cli(local_app_data, "init")

    assert first.returncode == 0
    assert second.returncode == 1
    assert second.stdout == ""
    assert "already initialized" in second.stderr
    assert (root / "investorch-qmt.toml").read_bytes() == original


def test_failed_atomic_replace_leaves_no_partial_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = default_paths(tmp_path / "QMT")

    def fail_replace(source: str | Path, target: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("investorch_qmt.config.os.replace", fail_replace)

    with pytest.raises(ConfigError, match="Cannot write configuration"):
        initialize_config(paths)

    assert not paths.config.exists()
    assert list(paths.root.glob("*.tmp")) == []
