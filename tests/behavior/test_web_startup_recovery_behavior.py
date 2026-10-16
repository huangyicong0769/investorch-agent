from pathlib import Path

import httpx
import pytest

from investorch.web.server import create_web_app
from tests.support.config import make_test_config


@pytest.mark.parametrize("damage", ["invalid_builtin", "missing_builtin", "invalid_registry"])
@pytest.mark.parametrize("models_configured", [True, False])
async def test_skill_startup_failure_enters_recovery_without_reopening_model_setup(
    tmp_path: Path, damage: str, models_configured: bool
) -> None:
    config = make_test_config(tmp_path)
    if not models_configured:
        config.root_config_path.write_text("# Model configuration is incomplete\n")
    skill = config.workspace_dir / "skills/skill-creator/SKILL.md"
    if damage == "invalid_registry":
        damaged = config.state_dir / "skills.json"
        damaged.write_text("not valid JSON")
    elif damage == "missing_builtin":
        damaged = skill
        damaged.unlink()
    else:
        damaged = skill
        damaged.write_text("not valid Skill frontmatter")
    before = {path.relative_to(config.root): path.read_bytes() for path in config.root.rglob("*") if path.is_file()}
    app = create_web_app(root=config.root)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        state = (await client.get("/api/setup/state")).json()
        assert state["status"] == "recovery_required"
        assert (await client.get("/api/bootstrap")).status_code == 503
        checks = (await client.get("/api/system")).json()["checks"]
        assert next(check for check in checks if check["code"] == "skills")["status"] in ("warning", "error")
        assert (await client.post("/api/setup/complete", json={})).status_code == 409
        assert (
            await client.patch("/api/setup/config", json={"changes": {"agents.main.name": "unrelated-change"}})
        ).status_code == 409
    after = {path.relative_to(config.root): path.read_bytes() for path in config.root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("warning", ["builtin_drift", "invalid_custom"])
async def test_nonblocking_skill_warnings_do_not_prevent_ready(tmp_path: Path, warning: str) -> None:
    from investorch.skills.domain import SkillRegistration, SkillSource
    from investorch.skills.registry import read_registry, write_registry

    config = make_test_config(tmp_path)
    if warning == "builtin_drift":
        skill = config.workspace_dir / "skills/skill-creator/SKILL.md"
        skill.write_text(skill.read_text() + "\nLocal documentation note.\n")
    else:
        registry = config.state_dir / "skills.json"
        records = read_registry(registry)
        records["custom-skill"] = SkillRegistration("custom-skill", True, SkillSource("created"))
        write_registry(registry, records)
        skill = config.workspace_dir / "skills/custom-skill/SKILL.md"
        skill.parent.mkdir()
        skill.write_text("not valid Skill frontmatter")
    original = skill.read_bytes()
    app = create_web_app(root=config.root)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        assert (await client.get("/api/setup/state")).json()["status"] == "ready"
        checks = (await client.get("/api/system")).json()["checks"]
        assert next(check for check in checks if check["code"] == "skills")["status"] == "warning"
    assert skill.read_bytes() == original


async def test_failed_setup_completion_transitions_to_recovery(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    config.root_config_path.write_text("# Model configuration is incomplete\n")
    app = create_web_app(root=config.root)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        assert (await client.get("/api/setup/state")).json()["status"] == "setup_required"
        changes = {f"secrets.{name}": value for name, value in config.secrets.items()}
        for section in ("models", "agents"):
            for name, value in config.public()[section].items():
                changes[f"{section}.{name}"] = {key: item for key, item in value.items() if item is not None}
        assert (await client.patch("/api/setup/config", json={"changes": changes})).status_code == 200
        saved = config.root_config_path.read_bytes()
        skill = config.workspace_dir / "skills/skill-creator/SKILL.md"
        skill.write_text("not valid Skill frontmatter")
        assert (await client.post("/api/setup/complete", json={})).status_code == 503
        assert (await client.get("/api/setup/state")).json()["status"] == "recovery_required"
        assert (await client.get("/api/system")).status_code == 200
        assert (await client.post("/api/setup/complete", json={})).status_code == 409
        assert config.root_config_path.read_bytes() == saved
        assert skill.read_text() == "not valid Skill frontmatter"
