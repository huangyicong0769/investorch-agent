from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from importlib.metadata import distribution
from pathlib import Path

import qmt_agent
from qmt_agent.config import PROJECT_CONFIG_PATH
from qmt_agent.web.assets import STATIC_DIR

BOOTSTRAP_FILES = {
    "MEMORY.md.template": Path("MEMORY.md"),
    "configuration.md.template": Path("memory/configuration.md"),
    "rqalpha.md.template": Path("memory/rqalpha.md"),
}


def _run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, env=env, text=True, capture_output=True, timeout=60)


def main() -> None:
    package_file = Path(qmt_agent.__file__ or "").resolve()
    installed_package = Path(distribution("qmt-agent-trader").locate_file("qmt_agent")).resolve()
    assert package_file.parent == installed_package
    assert not (Path.cwd() / "pyproject.toml").exists()
    assert not (Path.cwd() / "src").exists()

    resources = PROJECT_CONFIG_PATH.parent
    assert PROJECT_CONFIG_PATH.is_file()
    assert PROJECT_CONFIG_PATH.is_relative_to(package_file.parent)
    for template_name in BOOTSTRAP_FILES:
        assert (resources / template_name).is_file()

    assert (STATIC_DIR / "index.html").is_file()
    assert any(path.is_file() for path in (STATIC_DIR / "assets").iterdir())

    executable = shutil.which("qmt-agent")
    assert executable is not None
    _run([executable, "--help"])
    _run([executable, "web", "--help"])

    with tempfile.TemporaryDirectory(prefix="qmt-agent-package-smoke-") as temp_dir:
        temp = Path(temp_dir)
        home = temp / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)

        initialized = _run([executable, "web"], env=env)
        root = home / ".qmt-agent-trader"
        assert initialized.returncode == 0
        assert "QMT Agent initialized" in initialized.stdout
        assert (root / "qmt.toml").is_file()
        assert (root / "mcp.toml").is_file()

        workspace = root / "workspace"
        for template_name, target in BOOTSTRAP_FILES.items():
            assert (workspace / target).read_bytes() == (resources / template_name).read_bytes()


if __name__ == "__main__":
    main()
