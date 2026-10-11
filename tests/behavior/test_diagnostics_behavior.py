from __future__ import annotations

import json
from pathlib import Path

from investorch.diagnostics import doctor_checks, doctor_exit_code


def test_doctor_reports_missing_installation_without_creating_it(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    checks = {item["code"]: item for item in doctor_checks(root)}
    assert not root.exists()
    assert checks["config"]["status"] == "warning"
    assert checks["credentials"]["status"] == "warning"
    assert checks["sessions"]["status"] == "warning"
    assert checks["portfolio_schema"]["status"] == "warning"
    assert checks["cnequity"]["status"] == "disabled"
    assert doctor_exit_code(list(checks.values())) == 1
    assert all(item["checked_at"] for item in checks.values())


def test_doctor_reports_bad_config_without_echoing_or_changing_it(tmp_path: Path) -> None:
    path = tmp_path / "investorch.toml"
    original = b'[secrets]\nTOP_SECRET_NAME = "secret-value"\ninvalid = [\n'
    path.write_bytes(original)
    before = set(tmp_path.rglob("*"))
    checks = doctor_checks(tmp_path)
    assert next(item for item in checks if item["code"] == "config")["status"] == "error"
    assert doctor_exit_code(checks) == 2
    assert "secret-value" not in json.dumps(checks)
    assert "TOP_SECRET_NAME" not in json.dumps(checks)
    assert path.read_bytes() == original
    assert set(tmp_path.rglob("*")) == before


def test_missing_legacy_credentials_are_setup_warning_not_config_failure(tmp_path: Path) -> None:
    path = tmp_path / "investorch.toml"
    path.write_text('[secrets]\nUNUSED_NAME = "secret-value"\n')
    original = path.read_bytes()
    checks = {item["code"]: item for item in doctor_checks(tmp_path, upgrade=True)}
    assert checks["config"]["status"] == "warning"
    assert checks["credentials"]["configured"]["main"] is False
    assert checks["config_upgrade"]["status"] == "warning"
    assert "UNUSED_NAME" not in json.dumps(checks)
    assert "secret-value" not in json.dumps(checks)
    assert path.read_bytes() == original
