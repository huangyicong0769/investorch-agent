import json
import os
import subprocess
import sys

import pytest

from investorch.config import load_config
from investorch.initializer import initialize


def run_cli(tmp_path, *arguments):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-m", "investorch", *arguments],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("command", [("doctor",), ("upgrade", "check")])
def test_management_checks_new_root_without_creating_it(tmp_path, command):
    root = tmp_path / "not-initialized"
    result = run_cli(tmp_path, *command, "--root", str(root), "--json")
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["exit_code"] == 1
    assert any(item["code"] == "config" and item["status"] == "warning" for item in report["checks"])
    assert not root.exists()


def test_doctor_invalid_configuration_reports_error_without_secret_values(tmp_path):
    root = tmp_path / "invalid"
    root.mkdir()
    config = root / "investorch.toml"
    original = b'[secrets]\nkey = "secret-sentinel"\n[permission]\nmode = "invalid"\n'
    config.write_bytes(original)
    result = run_cli(tmp_path, "doctor", "--root", str(root), "--json")
    assert result.returncode == 2
    assert json.loads(result.stdout)["exit_code"] == 2
    assert "secret-sentinel" not in result.stdout + result.stderr
    assert config.read_bytes() == original
    assert sorted(path.name for path in root.iterdir()) == ["investorch.toml"]


def test_backup_cli_creates_and_verifies_without_touching_default_home(tmp_path):
    root = tmp_path / "source"
    initialize(load_config(root))
    output = tmp_path / "backup.zip"
    created = run_cli(tmp_path, "backup", "create", "--root", str(root), "--output", str(output))
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["path"] == str(output)
    verified = run_cli(tmp_path, "backup", "verify", str(output))
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["verified"] is True
    assert not (tmp_path / "home/.investorch").exists()


def test_plain_incomplete_existing_configuration_points_to_web_setup(tmp_path):
    home = tmp_path / "home"
    root = home / ".investorch"
    initialize(load_config(root))
    result = run_cli(tmp_path, "--plain")
    assert result.returncode == 0, result.stderr
    assert "investorch web" in result.stdout
