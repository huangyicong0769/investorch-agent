from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Literal

from agents import Agent

from investorch.agents.skill_review import SkillReviewResult, review_skill
from investorch.config import AppConfig
from investorch.skills.domain import SkillRegistration, SkillSource, ValidatedSkill
from investorch.skills.registry import read_registry, write_registry
from investorch.skills.validation import validate_name, validate_skill

logger = logging.getLogger(__name__)
CustomSource = Literal["created", "external"]


class SkillOperations:
    def __init__(self, *, config: AppConfig, review_agent: Agent | None = None) -> None:
        self.config = config
        self.review_agent = review_agent
        self.root = config.workspace_dir / "skills"
        self.registry_path = config.state_dir / "skills.json"
        self._catalog: tuple[dict, ...] | None = None
        self._approved_installs: set[tuple] = set()

    def _installed_path(self, name: str) -> Path:
        validate_name(name)
        if self.root.is_symlink() or not self.root.resolve().is_relative_to(self.config.workspace_dir.resolve()):
            raise ValueError("Installed Skill root escapes workspace")
        path = self.root / name
        if path.is_symlink():
            raise ValueError("Installed Skill must not be a symlink")
        return path

    def inspect(self, name: str) -> dict:
        records = read_registry(self.registry_path)
        if name not in records:
            raise ValueError(f"Unregistered Skill: {name}")
        record = records[name]
        result = dict(
            name=name,
            version=None,
            description=None,
            source=asdict(record.source),
            enabled=record.enabled,
            resources=[],
            status="missing",
        )
        try:
            path = self._installed_path(name)
            if not path.exists():
                return result
            skill = validate_skill(path, source_type=record.source.type)
        except (ValueError, OSError) as exc:
            result.update(status="invalid", validation_error=str(exc))
            return result
        result.update(
            asdict(skill.metadata),
            resources=list(skill.resources),
            status="available" if record.enabled else "disabled",
        )
        return result

    def list(self) -> list[dict]:
        return [self.inspect(name) for name in sorted(read_registry(self.registry_path))]

    def enabled_catalog(self) -> tuple[dict, ...]:
        if self._catalog is None:
            entries = []
            for record in self.list():
                if record["status"] in ("invalid", "missing"):
                    if record["source"]["type"] == "builtin":
                        raise ValueError(
                            f"Built-in Skill '{record['name']}' is missing or invalid. "
                            "Run `investorch --update` or reinstall InvestOrch."
                        )
                    logger.warning("Skill %s is %s", record["name"], record["status"])
                elif record["status"] == "available":
                    entries.append({key: record[key] for key in ("name", "version", "description")})
            self._catalog = tuple(entries)
        return tuple(dict(entry) for entry in self._catalog)

    def load(self, name: str) -> dict:
        if name not in {entry["name"] for entry in self.enabled_catalog()}:
            raise ValueError("Skill is not in the startup catalog; restart InvestOrch after management changes")
        record = read_registry(self.registry_path).get(name)
        if record is None or not record.enabled:
            raise ValueError("Skill is unregistered or disabled")
        skill = validate_skill(self._installed_path(name), source_type=record.source.type)
        return dict(**asdict(skill.metadata), content=skill.content, resources=list(skill.resources))

    def _candidate(self, candidate_path: str, source_type: CustomSource) -> tuple[Path, ValidatedSkill]:
        if source_type not in ("created", "external"):
            raise ValueError("Agent install source must be created or external")
        path = self.config.workspace_dir / candidate_path
        workspace = self.config.workspace_dir.resolve()
        if not path.resolve().is_relative_to(workspace) or path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Candidate must be inside Workspace and outside installed skills")
        return path, validate_skill(path, source_type=source_type)

    async def review_candidate(self, candidate_path: str, source_type: CustomSource = "external") -> SkillReviewResult:
        path, skill = self._candidate(candidate_path, source_type)
        if self.review_agent is None:
            raise ValueError("Skill Review Agent is unavailable")
        files = []
        for name in skill.files:
            raw = (path / name).read_bytes()
            try:
                content = raw.decode("utf-8")
            except UnicodeError:
                content = None
            files.append(dict(path=name, bytes=len(raw), text=content))
        # Bounded by deterministic bundle limits; binary assets contribute metadata only.
        return await review_skill(self.review_agent, json.dumps({"untrusted_candidate": files}, ensure_ascii=False))

    def validate_install(
        self, candidate_path: str, source_type: CustomSource, origin: str | None = None, replace: bool = False
    ) -> tuple[Path, ValidatedSkill]:
        path, skill = self._candidate(candidate_path, source_type)
        if origin is not None and (source_type != "external" or not origin.strip()):
            raise ValueError("Only external Skills may have a nonempty origin")
        records = read_registry(self.registry_path)
        old = records.get(skill.metadata.name)
        target = self._installed_path(skill.metadata.name)
        if old and old.source.type == "builtin":
            raise ValueError("Built-in customization requires a fork with a new name")
        if (old is not None or target.exists()) and not replace:
            raise ValueError("Skill name collision; explicit replace is required")
        return path, skill

    def authorize_install(
        self,
        *,
        run_id: str,
        candidate_path: str,
        source_type: CustomSource,
        origin: str | None = None,
        replace: bool = False,
    ) -> None:
        """Record one approved install from the application approval boundary."""
        self._approved_installs.add((run_id, candidate_path, source_type, origin, replace))

    def install(
        self,
        *,
        run_id: str,
        candidate_path: str,
        source_type: CustomSource,
        origin: str | None = None,
        replace: bool = False,
    ) -> dict:
        key = (run_id, candidate_path, source_type, origin, replace)
        if key not in self._approved_installs:
            raise ValueError("Installation requires fresh safety review and approval")
        self._approved_installs.remove(key)
        path, skill = self.validate_install(candidate_path, source_type, origin, replace)
        records = read_registry(self.registry_path)
        name = skill.metadata.name
        target = self._installed_path(name)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        shutil.copytree(path, target)
        old = records.get(name)
        records[name] = SkillRegistration(name, old.enabled if old else True, SkillSource(source_type, origin))
        write_registry(self.registry_path, records)
        return self._changed(name)

    def remove(self, name: str) -> dict:
        records = read_registry(self.registry_path)
        record = records.get(name)
        if record is None:
            raise ValueError(f"Unregistered Skill: {name}")
        if record.source.type == "builtin":
            raise ValueError("Built-in Skills cannot be removed")
        path = self._installed_path(name)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        del records[name]
        write_registry(self.registry_path, records)
        return self._changed(name)

    def set_enabled(self, name: str, enabled: bool) -> dict:
        records = read_registry(self.registry_path)
        if name not in records:
            raise ValueError(f"Unregistered Skill: {name}")
        records[name] = replace(records[name], enabled=enabled)
        write_registry(self.registry_path, records)
        return self._changed(name)

    @staticmethod
    def _changed(name: str) -> dict:
        return dict(name=name, restart_required=True, message="Restart InvestOrch to use the updated Skill catalog.")
