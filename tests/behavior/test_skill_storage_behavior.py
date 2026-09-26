from pathlib import Path

import pytest

from investorch.skills.domain import SkillRegistration, SkillSource
from investorch.skills.registry import read_registry, write_registry
from investorch.skills.validation import validate_skill


def candidate(root: Path, name: str = "example", version: str = "1.0.0") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Validate local reports when requested.\nversion: {version}\n---\nRead the report.\n"
    )
    return path


def test_skill_metadata_and_resource_inventory(tmp_path):
    path = candidate(tmp_path)
    (path / "references").mkdir()
    (path / "references" / "guide.md").write_text("Guide")
    skill = validate_skill(path, source_type="created")
    assert skill.metadata.name == "example"
    assert skill.metadata.version == "1.0.0"
    assert skill.resources == ("references/guide.md",)
    assert "Read the report." in skill.content


def test_registry_roundtrip_keeps_only_registration(tmp_path):
    path = tmp_path / "skills.json"
    records = {"example": SkillRegistration("example", False, SkillSource("external", "https://example.com"))}
    write_registry(path, records)
    assert read_registry(path) == records
    assert "version" not in path.read_text().replace("schema_version", "")


@pytest.mark.parametrize(
    "content", ["{", '{"schema_version":2,"skills":{}}', '{"schema_version":1,"skills":{"x":{"enabled":"yes"}}}']
)
def test_registry_rejects_invalid_data(tmp_path, content):
    path = tmp_path / "skills.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        read_registry(path)


@pytest.mark.parametrize(
    "replacement",
    [
        "name: ../example",
        "name: different",
        "name: ",
        "description: ",
        "version: 01.0.0",
        "version: 1.0.0-01",
        "version: null",
    ],
)
def test_invalid_skill_metadata_is_rejected(tmp_path, replacement):
    path = candidate(tmp_path)
    skill_file = path / "SKILL.md"
    lines = skill_file.read_text().splitlines()
    key = replacement.split(":")[0] + ":"
    skill_file.write_text("\n".join(replacement if line.startswith(key) else line for line in lines))
    with pytest.raises(ValueError):
        validate_skill(path)


@pytest.mark.parametrize(
    "content", [b"\xff", b"---\nname: [\n---\nbody", b"no frontmatter", b"---\nname: example\ndescription: Test\n---\n"]
)
def test_invalid_skill_document_is_rejected(tmp_path, content):
    path = candidate(tmp_path)
    (path / "SKILL.md").write_bytes(content)
    with pytest.raises(ValueError):
        validate_skill(path)


def test_external_version_optional_but_created_requires_version(tmp_path):
    path = candidate(tmp_path)
    file = path / "SKILL.md"
    file.write_text(file.read_text().replace("version: 1.0.0\n", ""))
    assert validate_skill(path).metadata.version is None
    with pytest.raises(ValueError, match="require a version"):
        validate_skill(path, source_type="created")


def test_symlink_escape_is_rejected(tmp_path):
    path = candidate(tmp_path)
    (path / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlinks"):
        validate_skill(path)


def test_size_and_inventory_limits(tmp_path):
    from investorch.skills.validation import MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES

    path = candidate(tmp_path)
    resource = path / "large.bin"
    with resource.open("wb") as file:
        file.truncate(MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError, match="size limit"):
        validate_skill(path)
    resource.unlink()
    for index in range(MAX_TOTAL_BYTES // MAX_FILE_BYTES + 1):
        with (path / f"{index}.bin").open("wb") as file:
            file.truncate(MAX_FILE_BYTES)
    with pytest.raises(ValueError, match="size limit"):
        validate_skill(path)
    for file in path.glob("*.bin"):
        file.unlink()
    for index in range(MAX_FILES):
        (path / f"{index}.txt").touch()
    with pytest.raises(ValueError, match="too many"):
        validate_skill(path)


def test_registry_failed_replace_preserves_previous_document(tmp_path, monkeypatch):
    import os

    path = tmp_path / "skills.json"
    write_registry(path, {})

    def unavailable(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "replace", unavailable)
    with pytest.raises(OSError):
        write_registry(path, {"example": SkillRegistration("example", True, SkillSource("created"))})
    assert read_registry(path) == {}
    assert list(tmp_path.iterdir()) == [path]
