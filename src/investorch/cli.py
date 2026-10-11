import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from investorch.config import load_config


@dataclass(frozen=True)
class StartupOptions:
    update: bool = False
    plain: bool = False


@dataclass(frozen=True)
class WebOptions:
    port: int | None


def parse_startup_args(argv: list[str] | None = None) -> StartupOptions:
    parser = argparse.ArgumentParser(
        description="Run InvestOrch Agent.",
        epilog="Other commands: investorch web, data, doctor, upgrade check, backup, migrate portfolio",
    )
    parser.add_argument("--update", action="store_true", help="Update bundled built-in Skills and exit.")
    parser.add_argument(
        "--plain", action="store_true", help="Use the verbose plain console instead of the Textual workspace."
    )
    args = parser.parse_args(argv)
    return StartupOptions(update=args.update, plain=args.plain)


def _web_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def parse_web_args(argv: list[str] | None = None) -> WebOptions:
    parser = argparse.ArgumentParser(prog="investorch web", description="Run the local InvestOrch Agent Web interface.")
    parser.add_argument(
        "--port", type=_web_port, help="Override the AppConfig web.default_port value for this process."
    )
    args = parser.parse_args(argv)
    return WebOptions(port=args.port)


def run_data_cli(args: list[str]) -> None:
    config = load_config()
    config.root.mkdir(parents=True, exist_ok=True)
    os.chdir(config.root)
    os.execv(sys.executable, [sys.executable, "-m", "cnequity", *args])


def run_web_cli(args: list[str]) -> None:
    from investorch.web import run_web

    options = parse_web_args(args)
    run_web(port=options.port)


def run_diagnostics_cli(args: list[str], *, upgrade: bool = False) -> None:
    from investorch.diagnostics import doctor_checks, doctor_exit_code

    parser = argparse.ArgumentParser(prog="investorch upgrade" if upgrade else "investorch doctor")
    options_parser = parser
    if upgrade:
        commands = parser.add_subparsers(dest="command", required=True)
        options_parser = commands.add_parser("check", help="Inspect upgrade readiness without modifying local state")
    options_parser.add_argument("--root", type=Path, help="Inspect an explicit root directory")
    options_parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    options = parser.parse_args(args)
    checks = doctor_checks(root=options.root, upgrade=upgrade)
    exit_code = doctor_exit_code(checks)
    if options.json:
        print(json.dumps(dict(checks=checks, exit_code=exit_code), indent=2))
    else:
        for check in checks:
            print(f"[{check['status'].upper()}] {check['code']}: {check['summary']}")
            for field in ("detail", "remediation"):
                if check[field]:
                    print(f"  {check[field]}")
        print(f"Exit status: {exit_code} (0: healthy/disabled, 1: warning/unknown, 2: error)")
    raise SystemExit(exit_code)


def entrypoint() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("doctor", "upgrade"):
        run_diagnostics_cli(sys.argv[2:], upgrade=sys.argv[1] == "upgrade")
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "backup":
        from investorch.backup_cli import run_backup_cli

        run_backup_cli(sys.argv[2:])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "migrate":
        from investorch.portfolio.migration_cli import run_migration_cli

        run_migration_cli(sys.argv[2:])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "data":
        run_data_cli(sys.argv[2:])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "web":
        run_web_cli(sys.argv[2:])
        return

    startup_options = parse_startup_args()
    if startup_options.update:
        from investorch.skills.bundled import update_builtins

        config = load_config(migrate=False)
        result = update_builtins(config)
        print(
            f"Built-in Skills updated: installed={result['installed']} "
            f"updated={result['updated']} unchanged={result['unchanged']}"
        )
        print("Restart InvestOrch to use the updated Skill catalog.")
        return

    from investorch.app import run_app

    asyncio.run(
        run_app(
            plain=startup_options.plain,
        )
    )
