"""Offline database migrations without starting the application."""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from investorch.config import ConfigError, load_config
from investorch.portfolio.migration import migrate_portfolio
from investorch.portfolio.schema import PortfolioStorageError


def run_migration_cli(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="investorch migrate")
    commands = parser.add_subparsers(dest="target", required=True)
    portfolio = commands.add_parser("portfolio", help="Back up and migrate Portfolio SQLite to v6")
    portfolio.add_argument("--dry-run", action="store_true", help="Inspect without modifying the database or files")
    portfolio.add_argument("--database", type=Path, help="Database path (default: AppConfig portfolio_db)")
    sessions = commands.add_parser("sessions", help="Back up and migrate Session history to include its system prompt")
    sessions.add_argument("--dry-run", action="store_true", help="Inspect without modifying the database or files")
    sessions.add_argument("--root", type=Path, help="Instance root for configuration and Skills")
    sessions.add_argument("--database", type=Path, help="Database path (default: AppConfig sessions_db)")
    options = parser.parse_args(args)
    if options.target == "sessions":
        from investorch.agents.prompts import main_system_prompt
        from investorch.application.skills import SkillOperations
        from investorch.storage.session_migration import migrate_sessions
        from investorch.storage.sessions import SessionFormatError

        def get_system_prompt() -> str:
            config = load_config(root=options.root)
            return main_system_prompt(SkillOperations(config=config).enabled_catalog())

        try:
            path = options.database or load_config(root=options.root).sessions_db
            result = migrate_sessions(path, get_system_prompt=get_system_prompt, dry_run=options.dry_run)
        except (SessionFormatError, sqlite3.Error, OSError, ValueError) as exc:
            print(f"Session migration failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(json.dumps(asdict(result), indent=2))
        return
    try:
        path = options.database if options.database is not None else load_config().portfolio_db
        result = migrate_portfolio(path, dry_run=options.dry_run)
    except (PortfolioStorageError, sqlite3.Error, OSError, ConfigError) as exc:
        print(f"Portfolio migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(asdict(result), indent=2))
    if not result.safe:
        raise SystemExit(1)
