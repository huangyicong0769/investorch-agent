from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .domain import SkillRegistration, SkillSource
from .validation import validate_name


def read_registry(path: Path) -> dict[str, SkillRegistration]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Cannot read Skill registry: {path}") from exc
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported Skill registry schema version")
    if set(data) != {"schema_version", "skills"} or not isinstance(data["skills"], dict):
        raise ValueError("Malformed Skill registry")
    records = {}
    for name, entry in data["skills"].items():
        validate_name(name)
        if not isinstance(entry, dict) or set(entry) != {"enabled", "source"} or type(entry["enabled"]) is not bool:
            raise ValueError(f"Malformed Skill registration: {name}")
        source = entry["source"]
        if (
            not isinstance(source, dict)
            or source.get("type") not in ("builtin", "created", "external")
            or set(source) - {"type", "origin"}
        ):
            raise ValueError(f"Malformed Skill source: {name}")
        origin = source.get("origin")
        if origin is not None and (source["type"] != "external" or not isinstance(origin, str) or not origin.strip()):
            raise ValueError(f"Malformed Skill origin: {name}")
        records[name] = SkillRegistration(name, entry["enabled"], SkillSource(source["type"], origin))
    return records


def write_registry(path: Path, records: dict[str, SkillRegistration]) -> None:
    skills = {}
    for name, record in records.items():
        source = {"type": record.source.type}
        if record.source.origin is not None:
            source["origin"] = record.source.origin
        skills[name] = {"enabled": record.enabled, "source": source}
    content = json.dumps({"schema_version": 1, "skills": skills}, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
