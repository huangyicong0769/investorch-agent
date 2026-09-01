from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import investorch
from investorch.config import PROJECT_CONFIG_PATH
from investorch.web.assets import STATIC_DIR

BOOTSTRAP_FILES = {
    "MEMORY.md.template": Path("MEMORY.md"),
    "configuration.md.template": Path("memory/configuration.md"),
    "rqalpha.md.template": Path("memory/rqalpha.md"),
}


def _run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, env=env, text=True, capture_output=True, timeout=60)


def main() -> None:
    package_file = Path(investorch.__file__ or "").resolve()
    package_distribution = distribution("investorch")
    installed_package = Path(package_distribution.locate_file("investorch")).resolve()
    assert package_file.parent == installed_package
    assert package_distribution.metadata["Name"] == "investorch"
    assert package_distribution.metadata["License-Expression"] == "Apache-2.0"
    assert set(package_distribution.metadata.get_all("License-File") or []) == {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
    }
    assert {entry.name for entry in package_distribution.entry_points if entry.group == "console_scripts"} == {
        "investorch"
    }
    cnequity_requirements = [
        requirement for requirement in package_distribution.requires or [] if requirement.lower().startswith("cnequity")
    ]
    assert len(cnequity_requirements) == 1
    assert "cnequity==0.7.3" in cnequity_requirements[0]
    assert "extra ==" in cnequity_requirements[0]
    try:
        distribution("cnequity")
    except PackageNotFoundError:
        pass
    else:
        raise AssertionError("CNEquity must not be installed by default")
    assert not (Path.cwd() / "pyproject.toml").exists()
    assert not (Path.cwd() / "src").exists()

    resources = PROJECT_CONFIG_PATH.parent
    assert PROJECT_CONFIG_PATH.is_file()
    assert PROJECT_CONFIG_PATH.is_relative_to(package_file.parent)
    for template_name in BOOTSTRAP_FILES:
        assert (resources / template_name).is_file()

    assert (STATIC_DIR / "index.html").is_file()
    assert any(path.is_file() for path in (STATIC_DIR / "assets").iterdir())
    assert (STATIC_DIR / "THIRD_PARTY_LICENSES.txt").is_file()

    executable = shutil.which("investorch")
    assert executable is not None
    _run([executable, "--help"])
    _run([executable, "web", "--help"])

    with tempfile.TemporaryDirectory(prefix="investorch-package-smoke-") as temp_dir:
        temp = Path(temp_dir)
        home = temp / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)

        initialized = _run([executable, "web"], env=env)
        root = home / ".investorch"
        assert initialized.returncode == 0
        assert "InvestOrch Agent initialized" in initialized.stdout
        assert (root / "investorch.toml").is_file()
        assert (root / "mcp.toml").is_file()
        assert (root / "state").is_dir()

        workspace = root / "workspace"
        for template_name, target in BOOTSTRAP_FILES.items():
            assert (workspace / target).read_bytes() == (resources / template_name).read_bytes()


if __name__ == "__main__":
    main()
