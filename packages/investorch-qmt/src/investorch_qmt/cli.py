from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version

from investorch_qmt.config import ConfigError, default_paths, initialize_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investorch-qmt")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('investorch-qmt')}",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init", help="Initialize local configuration")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0

    try:
        if arguments.command == "init":
            paths = default_paths()
            initialize_config(paths)
            print("Initialized InvestOrch QMT.")
            print(f"Config: {paths.config}")
            print("Use `investorch-qmt token show` to configure the Core MCP secret.")
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


def entrypoint() -> int:
    return main()
