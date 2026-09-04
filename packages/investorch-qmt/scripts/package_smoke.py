from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import investorch_qmt


def main() -> None:
    package = distribution("investorch-qmt")
    package_file = Path(investorch_qmt.__file__ or "").resolve()

    assert package_file.parent == Path(package.locate_file("investorch_qmt")).resolve()
    assert package.metadata["Name"] == "investorch-qmt"
    assert package.version == "0.1.0"
    assert package.metadata["License-Expression"] == "Apache-2.0"
    assert set(package.metadata.get_all("License-File") or []) == {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
    }
    assert {entry.name for entry in package.entry_points if entry.group == "console_scripts"} == {"investorch-qmt"}
    assert not (Path.cwd() / "pyproject.toml").exists()
    assert not (Path.cwd() / "src").exists()

    requirements = [requirement.lower() for requirement in package.requires or []]
    for prohibited in (
        "cnequity",
        "investorch",
        "openai-agents",
        "rqalpha",
        "textual",
        "xtquant",
        "xtquant-big-convert",
    ):
        assert not any(requirement.startswith(prohibited) for requirement in requirements)
        try:
            distribution(prohibited)
        except PackageNotFoundError:
            pass
        else:
            raise AssertionError(f"{prohibited} must not be installed")

    executable = shutil.which("investorch-qmt")
    assert executable is not None
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stdout == "investorch-qmt 0.1.0\n"
    assert completed.stderr == ""

    with tempfile.TemporaryDirectory(prefix="investorch-qmt-package-smoke-") as temp_dir:
        data_root = Path(temp_dir) / "data"
        env = os.environ.copy()
        env["XDG_DATA_HOME"] = str(data_root)
        env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(data_root)
        initialized = subprocess.run(
            [executable, "init"],
            check=True,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        shown = subprocess.run(
            [executable, "token", "show"],
            check=True,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        config_paths = list(data_root.rglob("investorch-qmt.toml"))
        assert len(config_paths) == 1
        token = shown.stdout.strip()
        assert len(token) >= 32
        assert token not in initialized.stdout
        assert initialized.stderr == ""
        assert shown.stderr == ""


if __name__ == "__main__":
    main()
