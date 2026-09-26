from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import investorch
from investorch.config import PROJECT_CONFIG_PATH
from investorch.skills.validation import validate_skill
from investorch.web.assets import STATIC_DIR

BUILTIN_SKILLS = (
    "investorch-configuration",
    "investorch-portfolio",
    "qmt-strategy",
    "rqalpha-strategy",
    "skill-creator",
    "skill-installer",
)


def _run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, env=env, text=True, capture_output=True, timeout=60)


def main() -> None:
    package_file = Path(investorch.__file__ or "").resolve()
    package_distribution = distribution("investorch")
    installed_package = Path(package_distribution.locate_file("investorch")).resolve()
    assert package_file.parent == installed_package
    assert package_distribution.metadata["Name"] == "investorch"
    assert package_distribution.metadata["Author"] == "InvestOrch contributors"
    assert package_distribution.metadata["License-Expression"] == "Apache-2.0"
    assert set(package_distribution.metadata.get_all("License-File") or []) == {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
    }
    project_urls = dict(value.split(", ", 1) for value in package_distribution.metadata.get_all("Project-URL") or [])
    assert project_urls == {
        "Documentation": "https://github.com/huangyicong0769/investorch-agent#readme",
        "Homepage": "https://github.com/huangyicong0769/investorch-agent",
        "Issues": "https://github.com/huangyicong0769/investorch-agent/issues",
        "Repository": "https://github.com/huangyicong0769/investorch-agent.git",
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
    assert (resources / "MEMORY.md.template").is_file()
    bundled_skills = resources / "skills"
    assert sorted(path.name for path in bundled_skills.iterdir() if path.is_dir()) == list(BUILTIN_SKILLS)
    for name in BUILTIN_SKILLS:
        skill = validate_skill(bundled_skills / name, source_type="builtin")
        assert skill.metadata.name == name

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
        assert (workspace / "MEMORY.md").read_bytes() == (resources / "MEMORY.md.template").read_bytes()
        assert not (workspace / "memory" / "configuration.md").exists()
        assert not (workspace / "memory" / "rqalpha.md").exists()
        registry_path = root / "state" / "skills.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry == {
            "schema_version": 1,
            "skills": {name: {"enabled": True, "source": {"type": "builtin"}} for name in BUILTIN_SKILLS},
        }
        for name in BUILTIN_SKILLS:
            installed = validate_skill(workspace / "skills" / name, source_type="builtin")
            bundled = validate_skill(bundled_skills / name, source_type="builtin")
            assert installed.metadata == bundled.metadata
            assert installed.files == bundled.files
            for relative_path in bundled.files:
                assert (workspace / "skills" / name / relative_path).read_bytes() == (
                    bundled_skills / name / relative_path
                ).read_bytes()


if __name__ == "__main__":
    main()
