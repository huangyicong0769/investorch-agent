from __future__ import annotations

import argparse
from collections.abc import Sequence
from importlib.metadata import version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investorch-qmt")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('investorch-qmt')}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


def entrypoint() -> int:
    return main()
