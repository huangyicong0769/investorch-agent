"""Standalone Portfolio migration command; does not initialize an application."""

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
    options = parser.parse_args(args)
    try:
        path = options.database if options.database is not None else load_config().portfolio_db
        result = migrate_portfolio(path, dry_run=options.dry_run)
    except (PortfolioStorageError, sqlite3.Error, OSError, ConfigError) as exc:
        print(f"Portfolio migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(asdict(result), indent=2))
    if not result.safe:
        raise SystemExit(1)
