import json
from pathlib import Path


def read_session_journal(
    directory: Path,
    session_id: str,
) -> list[dict[str, object]]:
    path = _session_path(directory, session_id)

    if path.is_symlink():
        raise RuntimeError(f"Session journal is not a regular file: {path}")
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise RuntimeError(f"Session journal is not a regular file: {path}")

    records: list[dict[str, object]] = []
    previous_seq = 0

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.endswith("\n"):
                raise RuntimeError(f"Session journal has an incomplete line {line_number}: {path}")

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Session journal has invalid JSON on line {line_number}: {path}") from exc

            if not isinstance(record, dict):
                raise RuntimeError(f"Session journal line {line_number} is not an object: {path}")

            seq = record.get("seq")
            if type(seq) is not int or seq < 1:
                raise RuntimeError(f"Session journal has an invalid sequence on line {line_number}: {path}")
            if seq <= previous_seq:
                raise RuntimeError(f"Session journal sequence is not increasing on line {line_number}: {path}")

            event_type = record.get("type")
            if not isinstance(event_type, str):
                raise RuntimeError(f"Session journal has an invalid type on line {line_number}: {path}")

            records.append(record)
            previous_seq = seq

    return records


def _session_path(directory: Path, session_id: str) -> Path:
    if (
        not session_id
        or session_id in {".", ".."}
        or "/" in session_id
        or "\\" in session_id
        or Path(session_id).name != session_id
    ):
        raise ValueError("session_id must be a non-empty filename-safe value")

    return directory / f"{session_id}.jsonl"
