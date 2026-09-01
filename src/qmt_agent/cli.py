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


@dataclass(frozen=True)
class WebOptions:
    port: int


def parse_startup_args(argv: list[str] | None = None) -> StartupOptions:
    parser = argparse.ArgumentParser(
        description="Run QMT Agent.", epilog="Other commands: qmt-agent web, qmt-agent data"
    )
    sync_group = parser.add_mutually_exclusive_group()
    sync_group.add_argument("--sync", action="store_true", help="Merge bootstrap templates with the model and exit.")
    sync_group.add_argument(
        "--sync-force", action="store_true", help="Replace bootstrap targets with project templates and exit."
    )
    parser.add_argument(
        "--plain", action="store_true", help="Use the verbose plain console instead of the Textual workspace."
    )
    args = parser.parse_args(argv)
    return StartupOptions(sync=args.sync, sync_force=args.sync_force, plain=args.plain)


def _web_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def parse_web_args(argv: list[str] | None = None) -> WebOptions:
    from qmt_agent.web import DEFAULT_WEB_PORT

    parser = argparse.ArgumentParser(prog="qmt-agent web", description="Run the local QMT Agent Web interface.")
    parser.add_argument(
        "--port", type=_web_port, default=DEFAULT_WEB_PORT, help=f"Loopback port (default: {DEFAULT_WEB_PORT})."
    )
    args = parser.parse_args(argv)
    return WebOptions(port=args.port)


def run_data_cli(args: list[str]) -> None:
    config = load_config()
    config.root.mkdir(parents=True, exist_ok=True)
    os.chdir(config.root)
    os.execv(sys.executable, [sys.executable, "-m", "cnequity", *args])


def run_web_cli(args: list[str]) -> None:
    from qmt_agent.web import run_web

    options = parse_web_args(args)
    run_web(port=options.port)


def entrypoint() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "data":
        run_data_cli(sys.argv[2:])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "web":
        run_web_cli(sys.argv[2:])
        return

    startup_options = parse_startup_args()
    asyncio.run(
        run_app(
            sync=startup_options.sync,
            sync_force=startup_options.sync_force,
            plain=startup_options.plain,
        )
    )
