from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from investorch.initializer import initialize
from investorch.skills.bundled import update_builtins
from investorch.skills.domain import SkillRegistration, SkillSource
from investorch.skills.registry import read_registry, write_registry
from tests.support.config import make_test_config


@pytest.mark.parametrize("mutation", ["content", "higher-version", "missing", "extra-file"])
def test_update_restores_package_authority_and_preserves_disabled_state(tmp_path: Path, mutation: str) -> None:
    config = make_test_config(tmp_path)
    root = config.workspace_dir / "skills" / "skill-creator"
    content = (root / "SKILL.md").read_text(encoding="utf-8")
    registry = config.state_dir / "skills.json"
    records = read_registry(registry)
    records["skill-creator"] = SkillRegistration("skill-creator", False, SkillSource("builtin"))
    write_registry(registry, records)
    if mutation == "content":
        (root / "SKILL.md").write_text(content + "\nUser modification", encoding="utf-8")
    elif mutation == "higher-version":
        (root / "SKILL.md").write_text(content.replace("1.0.0", "999.0.0"), encoding="utf-8")
    elif mutation == "extra-file":
        (root / "obsolete.txt").write_text("remove me", encoding="utf-8")
    else:
        shutil.rmtree(root)

    result = update_builtins(config)

    assert result == {"installed": int(mutation == "missing"), "updated": int(mutation != "missing"), "unchanged": 5}
    assert (root / "SKILL.md").read_text(encoding="utf-8") == content
    assert not (root / "obsolete.txt").exists()
    assert read_registry(registry)["skill-creator"].enabled is False


def test_update_installs_a_newly_distributed_builtin_and_preserves_user_skills(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    registry = config.state_dir / "skills.json"
    records = read_registry(registry)
    del records["qmt-strategy"]
    shutil.rmtree(config.workspace_dir / "skills" / "qmt-strategy")
    for source_type in ("created", "external"):
        name = f"my-{source_type}"
        root = config.workspace_dir / "skills" / name
        root.mkdir()
        (root / "SKILL.md").write_text(f"User-owned {source_type}", encoding="utf-8")
        records[name] = SkillRegistration(name, False, SkillSource(source_type))
    write_registry(registry, records)

    result = update_builtins(config)

    assert result == {"installed": 1, "updated": 0, "unchanged": 5}
    assert (config.workspace_dir / "skills" / "qmt-strategy" / "SKILL.md").is_file()
    for source_type in ("created", "external"):
        name = f"my-{source_type}"
        assert read_registry(registry)[name] == records[name]
        assert (config.workspace_dir / "skills" / name / "SKILL.md").read_text() == f"User-owned {source_type}"


def test_reinitialization_does_not_update_or_repair_existing_skills(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    skill = config.workspace_dir / "skills" / "skill-creator" / "SKILL.md"
    skill.write_text("User modification", encoding="utf-8")
    missing = config.workspace_dir / "skills" / "qmt-strategy"
    shutil.rmtree(missing)

    initialize(config)

    assert skill.read_text() == "User modification"
    assert not missing.exists()


def test_update_does_not_silently_replace_a_nonbuiltin_name_collision(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    registry = config.state_dir / "skills.json"
    records = read_registry(registry)
    records["skill-creator"] = SkillRegistration("skill-creator", True, SkillSource("created"))
    write_registry(registry, records)
    skill = config.workspace_dir / "skills" / "skill-creator" / "SKILL.md"
    skill.write_text("User-owned Skill", encoding="utf-8")

    with pytest.raises(ValueError, match="collision"):
        update_builtins(config)

    assert skill.read_text() == "User-owned Skill"
    assert read_registry(registry) == records
