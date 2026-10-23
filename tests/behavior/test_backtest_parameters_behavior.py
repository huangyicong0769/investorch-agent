"""Per-run controls reach the native engine without changing application defaults."""

from __future__ import annotations

import ast
import asyncio
import csv
import json
from pathlib import Path

import pytest

from investorch.tools.quant import run_backtest
from tests.behavior.test_workspace_capabilities import make_tool_context
from tests.support.rqalpha import write_native_bundle

STRATEGY = """from rqalpha.api import get_position, order_shares, order_target_percent, update_universe


def init(context):
    context.entered = False
    update_universe(['000001.XSHE'])


def handle_bar(context, bar_dict):
    if not context.entered:
        order_shares('000001.XSHE', 100)
        context.entered = True
    if get_position('000001.XSHE').quantity:
        order_target_percent('000001.XSHE', 0)
"""


def prepare(tmp_path: Path):
    context = make_tool_context(
        tmp_path,
        {"backtest": {"use_cnequity": False, "stock_min_commission": 25.0, "slippage": 0.001}},
    )
    config = context.context.config
    write_native_bundle(config.rqalpha_bundle_dir)
    (config.workspace_dir / "round_trip.py").write_text(STRATEGY)
    return context


async def invoke(context, parameters):
    arguments = {
        "strategy_path": "round_trip.py",
        "start_date": "2024-05-06",
        "end_date": "2024-05-10",
        "initial_cash": 100000,
        "parameters": parameters,
    }
    output = await run_backtest.on_invoke_tool(context, json.dumps(arguments))
    assert isinstance(output, dict) or not output.startswith("Error:"), output
    result = output if isinstance(output, dict) else ast.literal_eval(output)
    root = context.context.config.workspace_dir
    request = json.loads((root / result["artifacts"]["request"]).read_text())
    with (root / result["artifacts"]["trades"]).open() as handle:
        trades = list(csv.DictReader(handle))
    return result, request, trades


async def test_per_run_costs_and_t0_do_not_change_defaults_or_later_runs(tmp_path: Path) -> None:
    context = prepare(tmp_path)
    config = context.context.config
    before_active, before_saved = config.public(), config.public(saved=True)
    before_file = config.root_config_path.read_bytes()

    baseline, baseline_request, baseline_trades = await invoke(context, None)
    free, free_request, free_trades = await invoke(
        context,
        {
            "stock_t1": False,
            "stock_min_commission": 0,
            "stock_commission_multiplier": 0,
            "tax_multiplier": 0,
            "slippage": 0,
        },
    )
    repeated, repeated_request, repeated_trades = await invoke(
        context, {"stock_t1": None, "stock_min_commission": None, "slippage": None}
    )

    assert baseline_trades[0]["datetime"].startswith("2024-05-06")
    assert baseline_trades[1]["datetime"].startswith("2024-05-07")
    assert [trade["side"] for trade in free_trades] == ["BUY", "SELL"]
    assert all(trade["datetime"].startswith("2024-05-06") for trade in free_trades)
    assert all(float(trade["transaction_cost"]) == 0 for trade in free_trades)
    assert free["summary"]["total_returns"] == 0
    assert baseline["summary"]["total_returns"] < 0
    assert repeated["summary"]["total_returns"] == baseline["summary"]["total_returns"]
    assert [trade["datetime"] for trade in repeated_trades] == [trade["datetime"] for trade in baseline_trades]
    assert baseline_request["runtime"] == repeated_request["runtime"]
    assert baseline_request["runtime"]["stock_min_commission"] == 25
    assert baseline_request["runtime"]["slippage"] == 0.001
    assert free_request["runtime"]["stock_t1"] is False
    for key in ["stock_min_commission", "stock_commission_multiplier", "tax_multiplier", "slippage"]:
        assert free_request["runtime"][key] == 0
    assert free["runtime"] == free_request["runtime"]
    assert config.public() == before_active
    assert config.public(saved=True) == before_saved
    assert config.root_config_path.read_bytes() == before_file


async def test_concurrent_calls_keep_different_run_costs_and_unchanged_defaults(tmp_path: Path) -> None:
    context = prepare(tmp_path)
    config = context.context.config
    before, disk = config.public(), config.root_config_path.read_bytes()
    common = {"stock_t1": False, "stock_min_commission": 0, "tax_multiplier": 0, "slippage": 0}
    free, charged = await asyncio.gather(
        invoke(context, {**common, "stock_commission_multiplier": 0}),
        invoke(context, {**common, "stock_commission_multiplier": 2}),
    )
    assert free[0]["summary"]["total_returns"] == 0
    assert sum(float(t["transaction_cost"]) for t in charged[2]) == pytest.approx(3.2)
    assert charged[0]["summary"]["total_returns"] == pytest.approx(-3.2 / 100000)
    assert free[1]["runtime"]["stock_commission_multiplier"] == 0
    assert charged[1]["runtime"]["stock_commission_multiplier"] == 2
    assert config.public() == before
    assert config.root_config_path.read_bytes() == disk


@pytest.mark.parametrize(
    "parameters",
    [
        {"slippage": -0.1},
        {"slippage": 1},
        {"slippage": float("nan")},
        {"slippage": float("inf")},
        {"slippage": "0.01"},
        {"slippage": True},
        {"stock_t1": "false"},
        {"stock_t1": 0},
        {"stock_min_commission": -1},
        {"stock_commission_multiplier": -1},
        {"tax_multiplier": -1},
        {"capital_gain_tax_rate": -1},
        {"matching_type": "next_bar"},
        {"slippage_model": "custom.module.Slippage"},
        {"artifact_dir": "../escape"},
    ],
)
async def test_invalid_run_parameters_fail_before_execution_and_preserve_defaults(
    tmp_path: Path, parameters: dict
) -> None:
    context = make_tool_context(tmp_path)
    config = context.context.config
    before = config.public()
    disk = config.root_config_path.read_bytes()
    output = await run_backtest.on_invoke_tool(
        context,
        json.dumps(
            {
                "strategy_path": "must-not-run.py",
                "start_date": "2024-05-06",
                "end_date": "2024-05-10",
                "parameters": parameters,
            }
        ),
    )
    assert isinstance(output, str) and output.startswith("Error:"), output
    assert "Invalid JSON input" in output
    assert not config.backtest_artifact_dir.exists()
    assert config.public() == before
    assert config.root_config_path.read_bytes() == disk


async def test_strategy_failure_with_overrides_preserves_application_defaults(tmp_path: Path) -> None:
    context = prepare(tmp_path)
    config = context.context.config
    before, saved = config.public(), config.public(saved=True)
    disk = config.root_config_path.read_bytes()
    (config.workspace_dir / "round_trip.py").write_text('def init(context):\n    raise ValueError("fixture failure")\n')
    output = await run_backtest.on_invoke_tool(
        context,
        json.dumps(
            {
                "strategy_path": "round_trip.py",
                "start_date": "2024-05-06",
                "end_date": "2024-05-10",
                "parameters": {"stock_t1": False, "stock_min_commission": 0, "slippage": 0},
            }
        ),
    )
    assert isinstance(output, str) and "Strategy error at round_trip.py:2" in output
    assert "fixture failure" in output
    artifacts = json.loads(output.split("Backtest artifacts: ", 1)[1])
    assert json.loads((config.workspace_dir / artifacts["diagnostics"]).read_text())["status"] == "failed"
    assert config.public() == before
    assert config.public(saved=True) == saved
    assert config.root_config_path.read_bytes() == disk
