from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from investorch_qmt.config import ConfigError, default_paths, initialize_config, load_config, rotate_token


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
def test_token_show_outputs_only_the_exact_configured_token(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    init = run_cli(local_app_data, "init")
    paths = default_paths(local_app_data / "InvestOrch" / "QMT")
    token = load_config(paths.config).auth.token

    shown = run_cli(local_app_data, "token", "show")

    assert init.returncode == 0
    assert shown.returncode == 0
    assert shown.stdout == f"{token}\n"
    assert shown.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows deployment path contract")
def test_token_show_before_init_is_actionable(tmp_path: Path) -> None:
    shown = run_cli(tmp_path / "LocalAppData", "token", "show")

    assert shown.returncode == 1
    assert shown.stdout == ""
    assert "not initialized" in shown.stderr
    assert "investorch-qmt init" in shown.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows deployment path contract")
def test_token_rotate_updates_disk_but_not_loaded_snapshot_and_preserves_document(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    paths = default_paths(local_app_data / "InvestOrch" / "QMT")
    paths.root.mkdir(parents=True)
    old_token = "o" * 32
    paths.config.write_text(
        f"""# operator note
[server]
host = "127.0.0.1"
port = 9988 # custom port
allowed_hosts = []

[auth]
token = "{old_token}"

[logging]
level = "DEBUG"
max_bytes = 12345
backup_count = 0
""",
        encoding="utf-8",
    )
    running_snapshot = load_config(paths.config)

    rotated = run_cli(local_app_data, "token", "rotate")

    persisted = load_config(paths.config)
    new_token = rotated.stdout.splitlines()[0]
    document = paths.config.read_text(encoding="utf-8")
    assert rotated.returncode == 0
    assert new_token == persisted.auth.token
    assert new_token != old_token
    assert running_snapshot.auth.token == old_token
    assert persisted.server.port == 9988
    assert persisted.logging.backup_count == 0
    assert "# operator note" in document
    assert "# custom port" in document
    assert "Token rotated." in rotated.stdout
    assert "Restart investorch-qmt for the new token to take effect." in rotated.stdout
    assert "Update the corresponding InvestOrch MCP secret" in rotated.stdout
    assert rotated.stderr == ""


def test_failed_token_replace_preserves_original_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = default_paths(tmp_path / "QMT")
    initialize_config(paths)
    original = paths.config.read_bytes()

    def fail_replace(source: str | Path, target: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("investorch_qmt.config.os.replace", fail_replace)

    with pytest.raises(ConfigError, match="Cannot write configuration"):
        rotate_token(paths.config)

    assert paths.config.read_bytes() == original
    assert list(paths.root.glob("*.tmp")) == []
