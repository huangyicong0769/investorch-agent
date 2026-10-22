"""Data-source failures give the Agent actionable guidance without downloading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from investorch.tools.quant import inspect_rqalpha_data, run_backtest
from tests.behavior.test_workspace_capabilities import make_tool_context
from tests.support.rqalpha import write_native_bundle


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


async def test_strategy_bar_error_reports_the_source_location_and_cause(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path, {"backtest": {"use_cnequity": False}})
    config = context.context.config
    write_native_bundle(config.rqalpha_bundle_dir)
    directory = config.workspace_dir / "strategies"
    directory.mkdir()
    (directory / "broken.py").write_text(
        "def init(context):\n"
        "    subscribe('000001.XSHE')\n"
        "def handle_bar(context, bar_dict):\n"
        "    confidential_local = 'local-variable-canary'\n"
        "    return bar_dict['000001.XSHE'].is_suspended\n"
    )

    result = await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": "strategies/broken.py", "start_date": "2024-05-06", "end_date": "2024-05-10"}),
    )

    assert isinstance(result, str)
    assert "strategies/broken.py:5" in result
    assert "ValueError: no field of name is_suspended" in result
    assert "local-variable-canary" not in result and "confidential_local" not in result
    assert str(tmp_path) not in result
    assert not config.backtest_artifact_dir.exists()


@pytest.mark.parametrize(
    "detail",
    [
        "configured_credential_canary '/Users/private folder/research.csv' C:\\private\\provider.json",
        "configured_credential_canary /Users/private folder/research.csv",
        "first line\n" + "x" * 975 + "configured_credential_canary" + "x" * 1000,
    ],
)
async def test_strategy_exception_feedback_hides_credentials_and_host_paths(tmp_path: Path, detail: str) -> None:
    context = make_tool_context(
        tmp_path,
        {"backtest": {"use_cnequity": False}, "secrets": {"DIAGNOSTIC_CANARY": "configured_credential_canary"}},
    )
    config = context.context.config
    write_native_bundle(config.rqalpha_bundle_dir)
    (config.workspace_dir / "broken.py").write_text(f"def init(context):\n    raise RuntimeError({detail!r})\n")

    result = await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": "broken.py", "start_date": "2024-05-06", "end_date": "2024-05-10"}),
    )

    assert isinstance(result, str)
    assert "broken.py:2: RuntimeError:" in result and "<redacted>" in result
    assert "configured_credential_canary" not in result
    assert "private folder" not in result and "research.csv" not in result and "provider.json" not in result
    assert str(tmp_path) not in result
    assert "\n" not in result and len(result) < 1100


async def test_strategy_syntax_error_reports_the_workspace_source_without_code(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path, {"backtest": {"use_cnequity": False}})
    config = context.context.config
    write_native_bundle(config.rqalpha_bundle_dir)
    (config.workspace_dir / "broken.py").write_text("def init(context):\n    private_source_canary =\n")

    result = await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": "broken.py", "start_date": "2024-05-06", "end_date": "2024-05-10"}),
    )

    assert isinstance(result, str)
    assert "broken.py:2: SyntaxError:" in result
    assert "private_source_canary" not in result and str(tmp_path) not in result


async def test_engine_initialization_failure_is_not_presented_as_a_strategy_error(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path, {"backtest": {"use_cnequity": False}})
    config = context.context.config
    write_native_bundle(config.rqalpha_bundle_dir)
    # Invalid engine logger configuration fails before any user strategy frame.
    (config.workspace_dir / "strategy.py").write_text(
        "__config__ = {'extra': {'logger': [('private_engine_canary', 'ERROR')]}}\ndef init(context): pass\n"
    )

    result = await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": "strategy.py", "start_date": "2024-05-06", "end_date": "2024-05-10"}),
    )

    assert isinstance(result, str)
    assert "System Status" in result
    assert "Strategy error" not in result and "private_engine_canary" not in result
