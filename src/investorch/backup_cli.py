"""Standalone offline backup commands."""

import argparse
import json
import sys
from pathlib import Path

from investorch.backup import BackupError, create_backup, verify_backup
from investorch.config import ConfigError, load_config


def run_backup_cli(arguments: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="investorch backup")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Back up an offline InvestOrch root")
    create.add_argument("--root", type=Path, help="Explicit root directory, for example an isolated recovery fixture")
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--include-sensitive-config", action="store_true")
    create.add_argument("--exclude-artifacts", action="store_true")
    verify = commands.add_parser("verify", help="Verify archive integrity without loading application configuration")
    verify.add_argument("archive", type=Path)
    options = parser.parse_args(arguments)
    try:
        if options.command == "verify":
            manifest = verify_backup(options.archive)
            print(json.dumps(dict(verified=True, manifest=manifest), indent=2))
            print(
                "Archive integrity passed; external model and market-data connectivity has not been verified.",
                file=sys.stderr,
            )
            return
        print("Offline backup: stop all InvestOrch clients and other writers before continuing.", file=sys.stderr)
        if options.include_sensitive_config:
            print("Warning: this unencrypted backup will contain raw configuration credentials.", file=sys.stderr)
        result = create_backup(
            load_config(options.root, migrate=False),
            options.output,
            include_sensitive_config=options.include_sensitive_config,
            exclude_artifacts=options.exclude_artifacts,
        )
        for warning in result["manifest"]["warnings"]:
            print(warning, file=sys.stderr)
        print(json.dumps(result, indent=2))
    except (BackupError, ConfigError, OSError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
