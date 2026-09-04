from __future__ import annotations

import subprocess
from importlib.metadata import distribution


def test_distribution_exposes_package_metadata_and_cli() -> None:
    import investorch_qmt

    package = distribution("investorch-qmt")

    assert investorch_qmt.__file__ is not None
    assert package.metadata["Name"] == "investorch-qmt"
    assert package.version == "0.1.0"
    assert package.metadata["License-Expression"] == "Apache-2.0"
    assert set(package.metadata.get_all("License-File") or []) == {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
    }
    assert {entry.name for entry in package.entry_points if entry.group == "console_scripts"} == {"investorch-qmt"}
    assert any(requirement.lower().startswith("mcp-types") for requirement in package.requires or [])


def test_version_command_uses_installed_metadata() -> None:
    completed = subprocess.run(
        ["investorch-qmt", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == "investorch-qmt 0.1.0\n"
    assert completed.stderr == ""


def test_missing_command_is_a_cli_syntax_error() -> None:
    completed = subprocess.run(
        ["investorch-qmt"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "required" in completed.stderr
