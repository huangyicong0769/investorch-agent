"""Qualify native release clients, macOS sandbox and both backtest paths.

Run with ``uv run --locked --all-extras python scripts/qualify_release_runtime.py
--bundle-path ~/.investorch/.rqalpha/bundle --evidence-dir /private/tmp/investorch-release-evidence``.
The bundle is cloned on macOS before use. All mutable state and evidence live in
a private disposable root; no provider credentials or market downloads are used.
CNEquity receives a small, explicitly labelled export of the native bundle, so
this checks integration compatibility, not ingestion or production-lake quality.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import tomlkit
from agents import set_tracing_disabled
from agents.mcp import MCPServerStdio
from agents.tool_context import ToolContext
from cnequity.config import Config
from cnequity.domain.schemas import ADJ_FACTORS_SCHEMA, DAILY_BARS_SCHEMA
from cnequity.query import load
from rqalpha.const import INSTRUMENT_TYPE, MARKET
from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.utils.datetime_func import convert_date_to_int, convert_int_to_date

from investorch.application import PortfolioOperations
from investorch.config import AppConfig
from investorch.config_schema import REQUIRED_AGENT_ROLES
from investorch.context import AgentContext, ExecutionState
from investorch.initializer import initialize
from investorch.tools import base
from investorch.tools.quant import _run_backtest

START, END = date(2024, 5, 6), date(2024, 5, 17)
SYMBOL = "000001.XSHE"
STRATEGY = """from rqalpha.api import history_bars, order_shares

def init(context):
    context.day = 0

def handle_bar(context, bar_dict):
    context.day += 1
    history = history_bars("000001.XSHE", 3, "1d", "close", include_now=True)
    assert len(history) == 3
    assert bar_dict["000001.XSHE"].close > 0
    if context.day == 1:
        order_shares("000001.XSHE", 100)
    elif context.day == 5:
        order_shares("000001.XSHE", -100)
"""


def manifest(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def make_config(root: Path) -> AppConfig:
    models = {"primary": {"type": "openai_compatible", "base_url": "http://127.0.0.1:1/v1", "api_key_secret": "unused"}}
    agents = {
        role: {"model": "primary", "name": "qualification-no-provider-call", "context_window_tokens": 131072}
        for role in REQUIRED_AGENT_ROLES
    }
    config = AppConfig(
        {"paths": {"root": str(root)}, "models": models, "agents": agents, "secrets": {"unused": "unused"}}
    )
    initialize(config)
    config.root_config_path.write_text(
        tomlkit.dumps({"models": models, "agents": agents, "secrets": {"unused": "unused"}})
    )
    config.root_config_path.chmod(0o600)
    return config


def qualify_terminal(config: AppConfig, evidence: Path, *, plain: bool) -> dict:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
    env = dict(os.environ, HOME=str(config.root.parent), TERM="xterm-256color", NO_PROXY="127.0.0.1,localhost")
    command = [sys.executable, "-m", "investorch", *(["--plain"] if plain else [])]
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
    os.close(slave)
    output = bytearray()

    def read_until(marker: bytes | None, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if marker is not None and marker in output:
                    return
            if process.poll() is not None:
                break
        if marker is not None and marker not in output:
            raise RuntimeError(f"{'plain' if plain else 'TUI'} did not reach readiness marker")

    try:
        read_until(b"You: " if plain else b"InvestOrch")
        if plain:
            os.write(master, b"/help\n")
            read_until(b"Show this help.")
            os.write(master, b"/exit\n")
        else:
            # Let the first Textual render finish before sending its Quit binding.
            time.sleep(0.5)
            os.write(master, b"\x11")
        read_until(None)
        code = process.wait(timeout=10)
        if code != 0 or b"Traceback" in output:
            raise RuntimeError(f"terminal client failed (exit {code})")
        if plain and b"Exiting..." not in output:
            raise RuntimeError("plain client did not acknowledge /exit")
        return {
            "exit_code": code,
            "terminal": "native PTY",
            "output_bytes": len(output),
            "provider_inference": "not requested",
        }
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)
        (evidence / ("plain.txt" if plain else "tui.txt")).write_bytes(output)


async def qualify_sandbox(config: AppConfig) -> dict:
    execution = ExecutionState()
    context = ToolContext(
        context=AgentContext(config, execution, "release-qualification", "sandbox", PortfolioOperations(config=config)),
        tool_name="exec_command",
        tool_call_id="native-qualification",
        tool_arguments="{}",
    )

    async def invoke(command: str, *, background: bool = False):
        return await base.exec_command.on_invoke_tool(
            context, json.dumps({"command": command, "background": background})
        )

    outside = config.root / "outside-workspace.txt"
    try:
        invalid = await invoke("")
        if not isinstance(invalid, str) or "command cannot be empty" not in invalid:
            raise RuntimeError("tool validation did not retain corrective error feedback")
        success = await invoke("printf native-sandbox > native.txt; cat native.txt")
        if not isinstance(success, dict) or success["exit_code"] != 0 or success["stdout"] != "native-sandbox":
            raise RuntimeError("native sandbox foreground command failed")
        failure = await invoke("printf expected-stderr >&2; exit 7")
        if not isinstance(failure, dict) or failure["exit_code"] != 7 or "expected-stderr" not in failure["stderr"]:
            raise RuntimeError("native sandbox lost command failure/stderr")
        denied = await invoke(f"printf forbidden > {outside}")
        if not isinstance(denied, dict) or denied["exit_code"] == 0 or outside.exists():
            raise RuntimeError("native sandbox allowed a write outside workspace")
        job = await invoke("sleep 0.2; printf background-native; printf background-stderr >&2", background=True)
        if not isinstance(job, dict):
            raise RuntimeError("native sandbox did not start background command")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            jobs = await base.list_background_jobs(execution)
            if jobs and jobs[0].status == "exited":
                break
            await asyncio.sleep(0.1)
        if not jobs or jobs[0].exit_code != 0:
            raise RuntimeError("background command did not finish successfully")
        stdout = (config.workspace_dir / jobs[0].stdout_log).read_text()
        stderr = (config.workspace_dir / jobs[0].stderr_log).read_text()
        if stdout != "background-native" or stderr != "background-stderr":
            raise RuntimeError("background logs did not retain both output streams")
        live_job = await invoke("sleep 30", background=True)
        if not isinstance(live_job, dict) or not live_job["running"]:
            raise RuntimeError("sandbox did not retain the live background process")
        await base.close_execution(execution)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(live_job["pid"], 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("sandbox shutdown left a background process alive")
        return {
            "foreground_exit": 0,
            "failure_exit": 7,
            "outside_write_denied": True,
            "background_exit": 0,
            "validation_feedback": "command cannot be empty",
            "shutdown_stopped_live_process": True,
        }
    finally:
        await base.close_execution(execution)


def export_native_parquet(config: AppConfig) -> dict:
    """Export observed historical bars/factors without inventing market values."""
    source = BaseDataSource(SimpleNamespace(data_bundle_path=str(config.rqalpha_bundle_dir), future_info={}))
    native = source._day_bar_stores[INSTRUMENT_TYPE.CS, MARKET.CN].get_bars(SYMBOL)
    # Include preceding bars for history_bars and every day within CN coverage.
    selection = native[
        (native["datetime"] >= convert_date_to_int(date(2024, 4, 22)))
        & (native["datetime"] <= convert_date_to_int(END))
    ]
    dates = [convert_int_to_date(value).date() for value in selection["datetime"]]
    fetched = datetime.now(UTC)
    provenance = {"source": "rqalpha-native-qualification-export", "data_version": "v2", "fetched_at": fetched}
    frame = pl.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "trade_date": day,
                **{key: float(row[key]) for key in ("open", "high", "low", "close")},
                "volume": int(row["volume"]),
                "amount": float(row["total_turnover"]),
                **provenance,
            }
            for row, day in zip(selection, dates, strict=True)
        ],
        schema=DAILY_BARS_SCHEMA,
    )
    cn = Config(data_root=config.root / "cnequity-qualification")
    for folder in (cn.curated_root / "daily_bars", cn.derived_root / "adj_factors"):
        folder.mkdir(parents=True)
    frame.write_parquet(cn.curated_root / "daily_bars" / "native-export.parquet")
    native_factors = source._ex_factor_stores[INSTRUMENT_TYPE.CS, MARKET.CN].get_factors(SYMBOL)
    positions = np.searchsorted(native_factors["start_date"], selection["datetime"], side="right") - 1
    factors = pl.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "trade_date": day,
                "adjust_type": "hfq",
                "factor": float(native_factors["ex_cum_factor"][position]),
                **(provenance | {"data_version": "v1"}),
            }
            for day, position in zip(dates, positions, strict=True)
        ],
        schema=ADJ_FACTORS_SCHEMA,
    )
    factors.write_parquet(cn.derived_root / "adj_factors" / "native-export.parquet")
    config.cnequity_config_path.parent.mkdir(parents=True)
    config.cnequity_config_path.write_text(
        tomlkit.dumps({"data": {"root": str(cn.data_root)}, "on_demand": {"enabled": False}})
    )
    observed = load("daily_bars", symbols=["000001.SZ"], config=cn)
    if observed.height != len(selection):
        raise RuntimeError("CNEquity native-parquet read lost bars")
    return {
        "provenance": "export of existing native RQAlpha historical bundle",
        "rows": observed.height,
        "coverage_start": dates[0].isoformat(),
        "coverage_end": dates[-1].isoformat(),
    }


async def qualify_cnequity_mcp(config: AppConfig) -> dict:
    async with MCPServerStdio(
        params={
            "command": sys.executable,
            "args": ["-m", "cnequity", "mcp", "--config", str(config.cnequity_config_path)],
            "cwd": str(config.root),
        }
    ) as server:
        tools = await server.list_tools()
        catalog = next(tool for tool in tools if tool.name == "describe_lake")
        result = await server.call_tool(catalog.name, {})
        if result.is_error:
            raise RuntimeError("CNEquity MCP catalog returned an error")
        rows = await server.call_tool(
            "query_bars",
            {"symbols": ["000001.SZ"], "start": START.isoformat(), "end": END.isoformat(), "adjust": "hfq"},
        )
        if rows.is_error:
            raise RuntimeError("CNEquity MCP bars returned an error")
        payload = json.loads(next(item.text for item in rows.content if item.type == "text"))
        if payload["origin"] != "lake" or payload["returned"] != 10:
            raise RuntimeError("CNEquity MCP did not return ten stored trading days")
        sql = await server.call_tool("run_sql", {"sql": "select count(*) as bar_count from daily_bars"})
        if sql.is_error:
            raise RuntimeError("CNEquity MCP DuckDB query returned an error")
        sql_payload = json.loads(next(item.text for item in sql.content if item.type == "text"))
        if sql_payload["rows"] != [[17]]:
            raise RuntimeError("CNEquity DuckDB did not observe the exported parquet rows")
        return {
            "transport": "real stdio subprocess",
            "tool_count": len(tools),
            "catalog_tool": catalog.name,
            "adjusted_bars": payload["returned"],
            "duckdb_query": "select count(*) from daily_bars",
        }


def qualify_backtest(config: AppConfig, *, overlay: bool) -> dict:
    data = config.public()
    data["secrets"] = {"unused": "unused"}
    data["backtest"]["use_cnequity"] = overlay
    selected = AppConfig(data)
    (selected.workspace_dir / "native_strategy.py").write_text(STRATEGY)
    result = _run_backtest(selected, "native_strategy.py", START.isoformat(), END.isoformat(), 100000, "000300.XSHG")
    trades = pl.read_csv(selected.workspace_dir / result["artifacts"]["trades"], infer_schema_length=0)
    if trades.height != 2:
        raise RuntimeError(f"expected buy and sell; observed {trades.height} trades")
    if float(result["summary"]["total_value"]) <= 0:
        raise RuntimeError("backtest did not return a positive ending value")
    return {
        "engine_version": result["engine_version"],
        "data_source": "cnequity_overlay" if overlay else "rqalpha_bundle",
        "trade_count": trades.height,
        "summary": result["summary"],
        "artifacts": result["artifacts"],
    }


def main() -> int:
    set_tracing_disabled(True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-path", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    options = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("native sandbox and clone qualification requires macOS")
    source = options.bundle_path.expanduser().resolve()
    before = manifest(source)
    if not before:
        parser.error("a populated native bundle is required; this script never downloads one")
    options.evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="native-runtime-", dir=options.evidence_dir)).resolve()
    evidence.chmod(0o700)
    root = evidence / "home" / ".investorch"
    root.mkdir(parents=True)
    config = make_config(root)
    config.rqalpha_bundle_dir.parent.mkdir(parents=True)
    report = {
        "status": "running",
        "evidence_dir": str(evidence),
        "platform": sys.platform,
        "versions": {name: version(name) for name in ("investorch", "openai-agents", "textual", "rqalpha", "cnequity")},
        "checks": {},
        "limits": [
            "No provider inference in terminal lifecycle checks; qualified separately.",
            "CNEquity data is a native historical export; production lake and ingestion are not qualified.",
            "macOS only; native Linux/Windows clients are not qualified by this script.",
        ],
    }
    try:
        subprocess.run(["cp", "-cR", str(source), str(config.rqalpha_bundle_dir)], check=True)
        report["checks"]["sandbox"] = asyncio.run(qualify_sandbox(config))
        report["checks"]["plain"] = qualify_terminal(config, evidence, plain=True)
        report["checks"]["tui"] = qualify_terminal(config, evidence, plain=False)
        report["checks"]["native_backtest"] = qualify_backtest(config, overlay=False)
        report["checks"]["cnequity_export"] = export_native_parquet(config)
        report["checks"]["cnequity_mcp"] = asyncio.run(qualify_cnequity_mcp(config))
        report["checks"]["overlay_backtest"] = qualify_backtest(config, overlay=True)
        native_total = report["checks"]["native_backtest"]["summary"]["total_value"]
        overlay_total = report["checks"]["overlay_backtest"]["summary"]["total_value"]
        if abs(native_total - overlay_total) > 1e-8:
            raise RuntimeError("overlay and native backtest ending values diverged")
        report["checks"]["native_overlay_equivalence"] = {"ending_value": native_total}
        env = dict(os.environ, HOME=str(root.parent))
        cli = subprocess.run(
            [sys.executable, "-m", "investorch", "data", "--version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        report["checks"]["cnequity_cli"] = {"output": cli.stdout.strip(), "exit_code": cli.returncode}
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        report["source_bundle_unchanged"] = before == manifest(source)
        if not report["source_bundle_unchanged"]:
            report.update(status="failed", error="source bundle manifest changed")
        (evidence / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(evidence / "report.json"),
                "completed_checks": list(report["checks"]),
                "error": report.get("error"),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
