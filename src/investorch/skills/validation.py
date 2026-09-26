from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .domain import SkillMetadata, SkillSourceType, ValidatedSkill

MAX_FILES = 256
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_SKILL_CHARS = 100_000
MAX_DESCRIPTION_CHARS = 2_000
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


def validate_name(name: str) -> None:
    if not isinstance(name, str) or len(name) > 64 or NAME.fullmatch(name) is None:
        raise ValueError("Skill name must be at most 64 lowercase letters/digits separated by hyphens")


def validate_skill(path: Path, *, source_type: SkillSourceType = "external") -> ValidatedSkill:
    if source_type not in ("builtin", "created", "external"):
        raise ValueError("Invalid Skill source")
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Skill root must be a directory, not a symlink")
    root = path.resolve()
    files: list[str] = []
    entries = total = 0
    # Reject symlinks rather than follow directory cycles or aliases during inventory/copy.
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            item = Path(directory) / name
            entries += 1
            if entries > MAX_FILES:
                raise ValueError("Skill contains too many filesystem entries")
            if item.is_symlink() or not item.resolve().is_relative_to(root):
                raise ValueError("Skill resources must not be symlinks or escape the Skill root")
            if item.is_dir():
                continue
            if not item.is_file():
                raise ValueError("Skill resources must be regular files")
            size = item.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("Skill exceeds file or bundle size limit")
            files.append(item.relative_to(root).as_posix())
    if "SKILL.md" not in files:
        raise ValueError("Skill requires a regular SKILL.md file")
    try:
        content = (root / "SKILL.md").read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValueError("SKILL.md must be UTF-8") from exc
    if len(content) > MAX_SKILL_CHARS:
        raise ValueError("SKILL.md exceeds character limit")
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md requires YAML frontmatter")
    try:
        end = lines.index("---", 1)
        metadata = yaml.safe_load("\n".join(lines[1:end]))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError("Invalid Skill YAML frontmatter") from exc
    if not isinstance(metadata, dict) or not "\n".join(lines[end + 1 :]).strip():
        raise ValueError("Skill requires metadata and a nonempty body")
    name = metadata.get("name")
    validate_name(name)
    if name != path.name:
        raise ValueError("Skill directory/name mismatch")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError("Skill requires a nonempty bounded description")
    version = metadata.get("version")
    if "version" in metadata and (not isinstance(version, str) or SEMVER.fullmatch(version) is None):
        raise ValueError("Skill version must be SemVer")
    if source_type in ("builtin", "created") and version is None:
        raise ValueError("Built-in and created Skills require a version")
    ordered = tuple(sorted(files))
    return ValidatedSkill(
        SkillMetadata(name, description.strip(), version),
        content,
        tuple(file for file in ordered if file != "SKILL.md"),
        ordered,
    )
