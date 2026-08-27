import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

from qmt_agent.app import run_app
from qmt_agent.config import load_config


@dataclass(frozen=True)
class StartupOptions:
    sync: bool = False
    sync_force: bool = False
    plain: bool = False


def parse_startup_args(argv: list[str] | None = None) -> StartupOptions:
    parser = argparse.ArgumentParser(description="Run QMT Agent.")
    sync_group = parser.add_mutually_exclusive_group()
    sync_group.add_argument("--sync", action="store_true", help="Merge bootstrap templates with the model and exit.")
    sync_group.add_argument("--sync-force", action="store_true", help="Replace bootstrap targets with project templates and exit.")
    parser.add_argument("--plain", action="store_true", help="Use the verbose plain console instead of the Textual workspace.")
    args = parser.parse_args(argv)
    return StartupOptions(sync=args.sync, sync_force=args.sync_force, plain=args.plain)


def run_data_cli(args: list[str]) -> None:
    config = load_config()
    config.root.mkdir(parents=True, exist_ok=True)
    os.chdir(config.root)
    os.execv(sys.executable, [sys.executable, "-m", "cnequity", *args])


def entrypoint() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "data":
        run_data_cli(sys.argv[2:])
        return

    startup_options = parse_startup_args()
    asyncio.run(
        run_app(
            sync=startup_options.sync,
            sync_force=startup_options.sync_force,
            plain=startup_options.plain,
        )
    )
