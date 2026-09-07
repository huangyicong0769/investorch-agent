from __future__ import annotations

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from investorch.config import load_config
from investorch.tools.quant import run_backtest
from tests.behavior.test_portfolio_agent_tools import make_tool_context


@pytest.mark.parametrize("parameters", [None, {"signal": {"windows": [3, 8], "enabled": True}, "optional": None}])
async def test_real_backtest_parameters_are_visible_and_canonical_portfolio_is_unchanged(
    tmp_path: Path, parameters: dict | None
) -> None:
    bundle = load_config().rqalpha_bundle_dir
    if not bundle.is_dir():
        pytest.skip("Requires an installed native RQAlpha bundle")
    context = make_tool_context(tmp_path)
    context.context.config.rqalpha_bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, context.context.config.rqalpha_bundle_dir, copy_function=os.link)
    operations = context.context.portfolios
    portfolio = await operations.create(name="Canonical", base_currency="CNY")
    await operations.initialize(portfolio.id, cash=Decimal("12345"), positions=(), source="backtest-isolation")
    before_state = await operations.get_state(portfolio.id)
    before_ledger = await operations.list_ledger(portfolio.id)
    strategy = context.context.config.workspace_dir / "parameters.py"
    strategy.write_text(
        "def init(context):\n"
        f"    assert context.investorch_parameters == {parameters or {}!r}\n"
        "    if context.investorch_parameters:\n"
        "        context.investorch_parameters['signal']['windows'].append(99)\n"
        "def handle_bar(context, bar_dict):\n"
        "    pass\n"
    )
    arguments = {
        "strategy_path": "parameters.py",
        "start_date": "2025-01-06",
        "end_date": "2025-01-07",
    }
    if parameters is not None:
        arguments["strategy_parameters"] = parameters
    result = await run_backtest.on_invoke_tool(context, json.dumps(arguments))
    assert isinstance(result, dict), result
    request = json.loads((context.context.config.workspace_dir / result["artifacts"]["request"]).read_text())
    assert request["strategy_parameters"] == (parameters or {})
    assert await operations.get_state(portfolio.id) == before_state
    assert await operations.list_ledger(portfolio.id) == before_ledger


@pytest.mark.parametrize(
    "parameters",
    [
        [],
        {"nested": {1: "invalid key"}},
        {"nested": (1, 2)},
        {"nested": {1, 2}},
        {"nested": float("nan")},
        {"nested": float("inf")},
        {"nested": Decimal("1.2")},
    ],
)
def test_backtest_rejects_non_json_parameters_before_execution(tmp_path: Path, parameters: object) -> None:
    from datetime import date

    from investorch.backtest import run_backtest as run_engine
    from tests.support.config import make_test_config

    config = make_test_config(tmp_path)
    with pytest.raises(ValueError, match="strategy_parameters"):
        run_engine(config, tmp_path / "absent.py", date(2025, 1, 6), date(2025, 1, 7), 1000, None, parameters)
