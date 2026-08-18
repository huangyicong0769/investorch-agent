import argparse
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class StartupOptions:
    sync: bool = False


def parse_startup_args(argv: list[str] | None = None) -> StartupOptions:
    parser = argparse.ArgumentParser(description="Run QMT Agent.")
    parser.add_argument("--sync", action="store_true", help="Merge bootstrap templates with the model and exit.")
    args = parser.parse_args(argv)
    return StartupOptions(sync=args.sync)


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
