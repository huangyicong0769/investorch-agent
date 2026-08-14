import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    args: tuple[str, ...]


def parse_command(text: str) -> Command | None:
    if not text.startswith("/"):
        return None

    parts = shlex.split(text)

    if not parts:
        return None

    return Command(
        name=parts[0][1:].lower(),
        args=tuple(parts[1:]),
    )