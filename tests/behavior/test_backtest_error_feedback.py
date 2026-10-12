"""Data-source failures give the Agent actionable guidance without downloading."""

from __future__ import annotations

import json
from pathlib import Path

from investorch.tools.quant import inspect_rqalpha_data, run_backtest
from tests.behavior.test_workspace_capabilities import make_tool_context


async def test_missing_bundle_reports_the_required_local_data(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    (context.context.config.workspace_dir / "strategy.py").write_text("def init(context): pass\n")

    result = await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": "strategy.py", "start_date": "2024-05-06", "end_date": "2024-05-17"}),
    )

    assert isinstance(result, str)
    assert "RQAlpha bundle not found" in result and "rqalpha download-bundle" in result
    assert not context.context.config.rqalpha_bundle_dir.exists()


async def test_unreadable_bundle_keeps_replacement_guidance(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    config = context.context.config
    (config.workspace_dir / "strategy.py").write_text("def init(context): pass\n")
    config.rqalpha_bundle_dir.mkdir(parents=True)

    result = await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": "strategy.py", "start_date": "2024-05-06", "end_date": "2024-05-17"}),
    )

    assert isinstance(result, str)
    assert "cannot be loaded" in result and "rqalpha download-bundle" in result
    assert not list(config.rqalpha_bundle_dir.iterdir())


async def test_native_inspection_explains_the_enabled_overlay(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path, {"backtest": {"use_cnequity": True}})

    result = await inspect_rqalpha_data.on_invoke_tool(context, "{}")

    assert isinstance(result, str)
    assert "inspect_rqalpha_data is unavailable" in result and "CNEquity backtest overlay is enabled" in result
