from dataclasses import dataclass
from typing import Literal

SkillSourceType = Literal["builtin", "created", "external"]


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    version: str | None


@dataclass(frozen=True, slots=True)
class SkillSource:
    type: SkillSourceType
    origin: str | None = None


@dataclass(frozen=True, slots=True)
class SkillRegistration:
    name: str
    enabled: bool
    source: SkillSource


@dataclass(frozen=True, slots=True)
class ValidatedSkill:
    metadata: SkillMetadata
    content: str
    resources: tuple[str, ...]
    files: tuple[str, ...]
