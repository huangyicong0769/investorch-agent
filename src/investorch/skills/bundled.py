from __future__ import annotations

import shutil
from pathlib import Path

from investorch.config import AppConfig

from .domain import SkillRegistration, SkillSource
from .registry import read_registry, write_registry
from .validation import validate_skill

BUNDLED_ROOT = Path(__file__).resolve().parents[1] / "resources" / "skills"


def initialize_skills(config: AppConfig) -> None:
    if not (config.state_dir / "skills.json").exists():
        update_builtins(config)


def update_builtins(config: AppConfig) -> dict[str, int]:
    registry_path = config.state_dir / "skills.json"
    records = read_registry(registry_path) if registry_path.exists() else {}
    root = config.workspace_dir / "skills"
    if root.is_symlink():
        raise ValueError("Installed Skill root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    bundles = [
        (path, validate_skill(path, source_type="builtin")) for path in sorted(BUNDLED_ROOT.iterdir()) if path.is_dir()
    ]
    if not bundles:
        raise ValueError("Package contains no built-in Skills; reinstall InvestOrch")
    for _path, skill in bundles:
        name = skill.metadata.name
        record = records.get(name)
        target = root / name
        if (record is not None and record.source.type != "builtin") or (record is None and target.exists()):
            raise ValueError(f"Built-in Skill name collision: {name}; fork or move the existing Skill explicitly")
        if target.is_symlink():
            raise ValueError(f"Built-in Skill path must not be a symlink: {name}")
    counts = dict(installed=0, updated=0, unchanged=0)
    for path, skill in bundles:
        name = skill.metadata.name
        target = root / name
        existed = target.exists()
        identical = False
        if existed:
            try:
                current = validate_skill(target, source_type="builtin")
                identical = current.files == skill.files and all(
                    (target / file).read_bytes() == (path / file).read_bytes() for file in skill.files
                )
            except (ValueError, OSError):
                pass
        if not identical:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            shutil.copytree(path, target)
        counts["unchanged" if identical else "updated" if existed else "installed"] += 1
        previous = records.get(name)
        records[name] = SkillRegistration(name, previous.enabled if previous else True, SkillSource("builtin"))
    write_registry(registry_path, records)
    return counts
