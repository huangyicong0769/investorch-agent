import argparse
from dataclasses import dataclass


@dataclass(frozen=True)
class StartupOptions:
    sync: bool = False
    sync_force: bool = False


def parse_startup_args(argv: list[str] | None = None) -> StartupOptions:
    parser = argparse.ArgumentParser(description="Run QMT Agent.")
    sync_group = parser.add_mutually_exclusive_group()
    sync_group.add_argument("--sync", action="store_true", help="Merge bootstrap templates with the model and exit.")
    sync_group.add_argument("--sync-force", action="store_true", help="Replace bootstrap targets with project templates and exit.")
    args = parser.parse_args(argv)
    return StartupOptions(sync=args.sync, sync_force=args.sync_force)
