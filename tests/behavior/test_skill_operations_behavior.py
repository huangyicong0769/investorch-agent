import pytest

from investorch.application.skills import SkillOperations
from tests.behavior.test_skill_storage_behavior import candidate
from tests.support.config import make_test_config


def test_catalog_is_metadata_only_and_frozen_load_names(tmp_path):
    config = make_test_config(tmp_path)
    operations = SkillOperations(config=config)
    catalog = operations.enabled_catalog()
    assert len(catalog) == 6
    assert {key for entry in catalog for key in entry} == {"name", "description", "version"}
    loaded = operations.load("skill-creator")
    assert loaded["content"].startswith("---")
    operations.set_enabled("skill-creator", False)
    with pytest.raises(ValueError):
        operations.load("skill-creator")


def test_unregistered_candidate_cannot_be_loaded_or_installed_without_approval(tmp_path):
    config = make_test_config(tmp_path)
    operations = SkillOperations(config=config)
    candidate(config.workspace_dir / ".skill-staging")
    with pytest.raises(ValueError):
        operations.load("example")
    with pytest.raises(ValueError, match="approval"):
        operations.install(run_id="run", candidate_path=".skill-staging/example", source_type="created")


def test_missing_builtin_fails_catalog_but_missing_external_is_listed(tmp_path):
    from investorch.skills.domain import SkillRegistration, SkillSource
    from investorch.skills.registry import read_registry, write_registry

    config = make_test_config(tmp_path)
    path = config.state_dir / "skills.json"
    records = read_registry(path)
    records["missing"] = SkillRegistration("missing", True, SkillSource("external"))
    write_registry(path, records)
    operations = SkillOperations(config=config)
    assert operations.inspect("missing")["status"] == "missing"
    assert len(operations.enabled_catalog()) == 6
    (config.workspace_dir / "skills/skill-creator/SKILL.md").unlink()
    with pytest.raises(ValueError, match="--update"):
        SkillOperations(config=config).enabled_catalog()


def test_install_replace_remove_and_restart_catalog(tmp_path):
    config = make_test_config(tmp_path)
    operations = SkillOperations(config=config)
    operations.enabled_catalog()
    path = candidate(config.workspace_dir / ".skill-staging")
    args = dict(run_id="run", candidate_path=".skill-staging/example", source_type="created")
    operations.authorize_install(**args)
    assert operations.install(**args)["restart_required"]
    with pytest.raises(ValueError, match="startup catalog"):
        operations.load("example")
    restarted = SkillOperations(config=config)
    assert restarted.load("example")["version"] == "1.0.0"
    with pytest.raises(ValueError, match="collision"):
        operations.validate_install(".skill-staging/example", "created")
    (path / "SKILL.md").write_text((path / "SKILL.md").read_text().replace("1.0.0", "1.1.0"))
    args["replace"] = True
    operations.authorize_install(**args)
    operations.install(**args)
    assert restarted.load("example")["version"] == "1.1.0"
    operations.set_enabled("example", False)
    assert operations.inspect("example")["status"] == "disabled"
    assert operations.remove("example")["restart_required"]
    assert path.exists()
    with pytest.raises(ValueError, match="Unregistered"):
        operations.inspect("example")


def test_newly_enabled_skill_needs_restart_and_builtin_mutations_rejected(tmp_path):
    config = make_test_config(tmp_path)
    setup = SkillOperations(config=config)
    setup.set_enabled("skill-creator", False)
    operations = SkillOperations(config=config)
    operations.enabled_catalog()
    operations.set_enabled("skill-creator", True)
    with pytest.raises(ValueError, match="startup catalog"):
        operations.load("skill-creator")
    assert SkillOperations(config=config).load("skill-creator")
    with pytest.raises(ValueError, match="cannot be removed"):
        operations.remove("skill-creator")
    candidate(config.workspace_dir / ".skill-staging", "skill-creator")
    with pytest.raises(ValueError, match="fork"):
        operations.validate_install(".skill-staging/skill-creator", "created", replace=True)


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", "skills/skill-creator"])
def test_candidate_must_be_workspace_staging_not_installed(tmp_path, path):
    operations = SkillOperations(config=make_test_config(tmp_path))
    with pytest.raises(ValueError, match="Candidate must"):
        operations.validate_install(path, "external")


@pytest.mark.parametrize("operation", ["remove", "replace"])
def test_invalid_custom_symlink_can_be_recovered_without_following_it(tmp_path, operation):
    from investorch.skills.domain import SkillRegistration, SkillSource
    from investorch.skills.registry import read_registry, write_registry

    config = make_test_config(tmp_path)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "untouched.txt").write_text("preserve")
    installed = config.workspace_dir / "skills/example"
    installed.symlink_to(outside, target_is_directory=True)
    registry = config.state_dir / "skills.json"
    records = read_registry(registry)
    records["example"] = SkillRegistration("example", True, SkillSource("external"))
    write_registry(registry, records)
    skills = SkillOperations(config=config)
    assert skills.inspect("example")["status"] == "invalid"
    if operation == "remove":
        skills.remove("example")
        assert not installed.exists()
    else:
        candidate(config.workspace_dir / ".skill-staging")
        args = dict(run_id="r", candidate_path=".skill-staging/example", source_type="created", replace=True)
        skills.authorize_install(**args)
        skills.install(**args)
        assert not installed.is_symlink()
        assert skills.inspect("example")["status"] == "available"
    assert (outside / "untouched.txt").read_text() == "preserve"
