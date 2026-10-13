"""Data-source failures give the Agent actionable guidance without downloading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from investorch.tools.quant import inspect_rqalpha_data, run_backtest
from tests.behavior.test_workspace_capabilities import make_tool_context


@pytest.mark.parametrize(
    ("path_kind", "expected"),
    [
        ("traversal", "strategy_path must remain inside the Workspace"),
        ("absolute", "strategy_path must be relative to the Workspace"),
        ("symlink", "strategy_path must remain inside the Workspace"),
        ("missing", "strategy file not found"),
        ("directory", "strategy_path is not a regular file"),
        ("extension", "strategy_path must reference a .py file"),
    ],
)
async def test_invalid_strategy_paths_keep_actionable_feedback_without_running(
    tmp_path: Path, path_kind: str, expected: str
) -> None:
    context = make_tool_context(tmp_path)
    config = context.context.config
    outside = config.workspace_dir.parent / "outside.py"
    outside.write_text("raise RuntimeError('must not run')\n")
    (config.workspace_dir / "linked.py").symlink_to(outside)
    (config.workspace_dir / "strategy.txt").write_text("raise RuntimeError('must not run')\n")
    paths = {
        "traversal": "../outside.py",
        "absolute": str(outside),
        "symlink": "linked.py",
        "missing": "missing.py",
        "directory": ".",
        "extension": "strategy.txt",
    }

    result = await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": paths[path_kind], "start_date": "2024-05-06", "end_date": "2024-05-17"}),
    )

    assert isinstance(result, str) and expected in result
    assert not config.backtest_artifact_dir.exists()


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
