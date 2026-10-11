import pytest

from investorch.application.skills import SkillOperations
from tests.behavior.test_skill_storage_behavior import candidate
from tests.support.config import make_test_config


def installed_skill(tmp_path, *, origin=None):
    config = make_test_config(tmp_path)
    candidate(config.workspace_dir / ".skill-staging")
    skills = SkillOperations(config=config)
    arguments = dict(run_id="setup", candidate_path=".skill-staging/example", source_type="external", origin=origin)
    skills.authorize_install(**arguments)
    skills.install(**arguments)
    return config, skills


def test_disabled_skill_can_be_previewed_without_becoming_loadable(tmp_path):
    config, skills = installed_skill(tmp_path)
    skills.set_enabled("example", False)
    path = config.workspace_dir / "skills/example"
    (path / "guide.md").write_text("Local reference")

    detail = skills.inspect_content("example")
    assert detail["status"] == "disabled"
    assert "Read the report." in detail["content"]
    assert detail["resources"] == ["guide.md"]
    assert skills.inspect_content("example", "guide.md")["content"] == "Local reference"
    with pytest.raises(ValueError):
        skills.load("example")


@pytest.mark.parametrize("state", ["missing", "invalid", "symlink"])
def test_unavailable_skill_preview_explains_status_without_reading_outside(tmp_path, state):
    config, skills = installed_skill(tmp_path)
    path = config.workspace_dir / "skills/example"
    (path / "SKILL.md").unlink()
    if state == "missing":
        path.rmdir()
    elif state == "symlink":
        private = tmp_path / "private.txt"
        private.write_text("private sentinel")
        (path / "SKILL.md").symlink_to(private)

    detail = skills.inspect_content("example")
    assert detail["status"] == ("missing" if state == "missing" else "invalid")
    assert detail["content"] is None
    assert "private sentinel" not in str(detail)


def test_skill_resource_preview_enforces_inventory_text_and_limits(tmp_path):
    config, skills = installed_skill(tmp_path)
    path = config.workspace_dir / "skills/example"
    (path / "binary.dat").write_bytes(b"\xff\x00")
    (path / "large.txt").write_text("a" * (config["explore.max_full_read_bytes"] + 1))
    for resource in ("../MEMORY.md", str(path / "SKILL.md"), "missing.md", "binary.dat", "large.txt"):
        with pytest.raises(ValueError):
            skills.inspect_content("example", resource)


def test_skill_provenance_does_not_expose_credentials_or_query_tokens(tmp_path):
    _, skills = installed_skill(
        tmp_path, origin="https://owner:password@example.org/secret-path?token=credential#private"
    )
    detail = skills.inspect_content("example")
    assert detail["source"] == {"type": "external", "origin": "https://example.org"}
    listed = next(item for item in skills.list() if item["name"] == "example")
    assert listed["source"] == detail["source"]
