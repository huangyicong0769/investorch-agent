from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import Enum
from importlib.metadata import version
from pathlib import Path
from typing import Any

from agents import RunContextWrapper
from agents.decorators import tool

from qmt_agent.backtest import (
    inspect_rqalpha_bundle,
)
from qmt_agent.backtest import (
    run_backtest as run_rqalpha_backtest,
)
from qmt_agent.config import AppConfig
from qmt_agent.context import AgentContext

_TABULAR_RESULTS = (
    "portfolio",
    "trades",
    "stock_account",
    "stock_positions",
    "benchmark_portfolio",
)


@tool
def inspect_rqalpha_data(
    context: RunContextWrapper[AgentContext],
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """
    Inspect the local native RQAlpha bundle without reading market prices or modifying data.

    Args:
        symbols: Canonical RQAlpha order-book IDs up to the configured inspection limit. Omit for overall stock metadata.

    Returns:
        Bundle availability and overall or per-instrument metadata with observed daily-bar ranges.
    """
    return _inspect_rqalpha_data(context.context.config, symbols)


def _inspect_rqalpha_data(
    config: AppConfig,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    if config["backtest.use_cnequity"]:
        raise RuntimeError("inspect_rqalpha_data is unavailable while the CNEquity backtest overlay is enabled")
    limit = config["backtest.inspect_max_symbols"]
    if symbols is not None and len(symbols) > limit:
        raise ValueError(f"inspect_rqalpha_data accepts at most {limit} symbols")
    return inspect_rqalpha_bundle(config.rqalpha_bundle_dir, symbols)


@tool(needs_approval=True)
def run_backtest(
    context: RunContextWrapper[AgentContext],
    strategy_path: str,
    start_date: str,
    end_date: str,
    initial_cash: float | None = None,
    benchmark: str | None = None,
) -> dict[str, Any]:
    """
    Run a Workspace RQAlpha Python strategy after user approval.

    The strategy path must be Workspace-relative and dates must use ISO YYYY-MM-DD. An optional benchmark must use its canonical RQAlpha order-book ID. The return contains a compact analyser summary and Workspace-relative result artifact paths. Missing data from the configured backtest source, or a missing required RQAlpha bundle, is reported and is never repaired automatically.

    Args:
        strategy_path: Workspace-relative path to an existing RQAlpha .py strategy.

        start_date: Backtest start date in YYYY-MM-DD format.

        end_date: Backtest end date in YYYY-MM-DD format.

        initial_cash: Positive finite initial stock-account cash. Defaults to backtest.default_initial_cash.

        benchmark: Optional canonical RQAlpha benchmark order-book ID.

    Returns:
        A compact dictionary containing engine metadata, scalar summary, and Workspace-relative artifact paths.
    """
    return _run_backtest(
        config=context.context.config,
        strategy_path=strategy_path,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        benchmark=benchmark,
    )


def _run_backtest(
    config: AppConfig,
    strategy_path: str,
    start_date: str,
    end_date: str,
    initial_cash: float | None = None,
    benchmark: str | None = None,
) -> dict[str, Any]:
    config = config.snapshot()
    workspace = config.workspace_dir
    strategy_file, relative_strategy = _resolve_strategy_path(workspace, strategy_path)
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    cash = _validate_initial_cash(config["backtest.default_initial_cash"] if initial_cash is None else initial_cash)
    if benchmark is not None and not benchmark.strip():
        raise ValueError("benchmark must be a canonical RQAlpha order-book ID or null")

    source = strategy_file.read_bytes()
    strategy_sha256 = hashlib.sha256(source).hexdigest()
    raw_result = run_rqalpha_backtest(
        config=config,
        strategy_file=strategy_file,
        start_date=start,
        end_date=end,
        initial_cash=cash,
        benchmark=benchmark,
    )

    analyser = raw_result.get("sys_analyser")
    if not isinstance(analyser, Mapping):
        raise RuntimeError("RQAlpha result is missing sys_analyser output")
    raw_summary = analyser.get("summary")
    if not isinstance(raw_summary, Mapping):
        raise RuntimeError("RQAlpha analyser result is missing summary output")

    summary = _normalize_json(raw_summary)
    if not isinstance(summary, dict):
        raise TypeError("RQAlpha analyser summary must be a mapping")
    # RQAlpha emits an absolute strategy_file path; this is the sole summary-field
    # rewrite needed to keep every model-facing path Workspace-relative.
    if "strategy_file" in summary:
        summary["strategy_file"] = relative_strategy

    run_id = uuid.uuid4().hex
    engine_version = version("rqalpha")
    request = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "engine": "rqalpha",
        "engine_version": engine_version,
        "data_source": ("cnequity_overlay" if config["backtest.use_cnequity"] else "rqalpha_bundle"),
        "strategy_path": relative_strategy,
        "strategy_sha256": strategy_sha256,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "initial_cash": cash,
        "benchmark": benchmark,
        "runtime": _backtest_runtime_snapshot(config),
    }
    artifacts = _write_backtest_artifacts(
        workspace=workspace,
        artifact_dir=config.backtest_artifact_dir,
        run_id=run_id,
        request=request,
        source=source,
        summary=summary,
        analyser=analyser,
    )

    return {
        "run_id": run_id,
        "engine": "rqalpha",
        "engine_version": engine_version,
        "summary": summary,
        "artifacts": artifacts,
    }


def _backtest_runtime_snapshot(config: AppConfig) -> dict[str, Any]:
    return {
        "stock_t1": config["backtest.stock_t1"],
        "dividend_reinvestment": config["backtest.dividend_reinvestment"],
        "dividend_tax_enabled": config["backtest.dividend_tax_enabled"],
        "capital_gain_tax_rate": config["backtest.capital_gain_tax_rate"],
        "validate_price": config["backtest.validate_price"],
        "matching_type": config["backtest.matching_type"],
        "price_limit": config["backtest.price_limit"],
        "volume_limit": config["backtest.volume_limit"],
        "inactive_limit": config["backtest.inactive_limit"],
        "slippage_model": config["backtest.slippage_model"],
        "slippage": config["backtest.slippage"],
        "stock_min_commission": config["backtest.stock_min_commission"],
        "stock_commission_multiplier": config["backtest.stock_commission_multiplier"],
        "tax_multiplier": config["backtest.tax_multiplier"],
        "pit_tax": config["backtest.pit_tax"],
    }


def _write_backtest_artifacts(
    workspace: Path,
    artifact_dir: Path,
    run_id: str,
    request: dict[str, Any],
    source: bytes,
    summary: dict[str, Any],
    analyser: Mapping[str, Any],
) -> dict[str, str]:
    root = workspace.expanduser().resolve()
    result_dir = artifact_dir / run_id
    result_dir.mkdir(parents=True, exist_ok=False)

    relative_dir = result_dir.relative_to(root)
    artifacts = {"result_dir": relative_dir.as_posix()}
    files = {
        "request": result_dir / "request.json",
        "strategy": result_dir / "strategy.py",
        "summary": result_dir / "summary.json",
    }
    _write_json(files["request"], request)
    files["strategy"].write_bytes(source)
    _write_json(files["summary"], summary)

    for name in _TABULAR_RESULTS:
        table = analyser.get(name)
        if table is None:
            continue
        to_csv = getattr(table, "to_csv", None)
        if not callable(to_csv):
            raise TypeError(f"RQAlpha analyser result {name} is not tabular")
        path = result_dir / f"{name}.csv"
        to_csv(path, index=True)
        files[name] = path

    for name, path in files.items():
        artifacts[name] = path.relative_to(root).as_posix()

    return artifacts


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _resolve_strategy_path(workspace: Path, strategy_path: str) -> tuple[Path, str]:
    if not strategy_path.strip():
        raise ValueError("strategy_path cannot be empty")

    relative = Path(strategy_path)
    if relative.is_absolute():
        raise ValueError("strategy_path must be relative to the Workspace")

    root = workspace.expanduser().resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("strategy_path must remain inside the Workspace")
    if not resolved.exists():
        raise FileNotFoundError(f"strategy file not found: {strategy_path}")
    if not resolved.is_file():
        raise ValueError(f"strategy_path is not a regular file: {strategy_path}")
    if resolved.suffix != ".py":
        raise ValueError("strategy_path must reference a .py file")

    return resolved, resolved.relative_to(root).as_posix()


def _parse_date(value: str, name: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use ISO YYYY-MM-DD format") from exc

    if value != parsed.isoformat():
        raise ValueError(f"{name} must use ISO YYYY-MM-DD format")

    return parsed


def _validate_initial_cash(initial_cash: float) -> float:
    if (
        isinstance(initial_cash, bool)
        or not isinstance(initial_cash, (int, float))
        or not math.isfinite(initial_cash)
        or initial_cash <= 0
    ):
        raise ValueError("initial_cash must be a finite number greater than zero")

    return float(initial_cash)


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalize_json(value.value)
    if isinstance(value, Mapping):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]

    item = getattr(value, "item", None)
    if callable(item):
        return _normalize_json(item())

    raise TypeError(f"Value is not JSON serializable: {type(value).__name__}")
