from __future__ import annotations

import asyncio
import json
from pathlib import Path

import h5py
import numpy as np

from investorch.tools.quant import run_backtest
from tests.behavior.test_workspace_capabilities import make_tool_context
from tests.support.rqalpha import write_native_bundle


def prepare(tmp_path: Path, marker: str = "native-run-marker", **overrides):
    context = make_tool_context(tmp_path, {"backtest": {"use_cnequity": False, **overrides}})
    config = context.context.config
    write_native_bundle(config.rqalpha_bundle_dir)
    (config.workspace_dir / "strategy.py").write_text(
        f"def init(context):\n    logger.info({marker!r})\n    subscribe('000001.XSHE')\n"
        "def handle_bar(context, bar_dict):\n    order_shares('000001.XSHE', 100)\n"
    )
    return context


async def invoke(context):
    return await run_backtest.on_invoke_tool(
        context,
        json.dumps({"strategy_path": "strategy.py", "start_date": "2024-05-06", "end_date": "2024-05-10"}),
    )


async def test_native_suspension_and_matching_rejections_reach_run_artifacts_and_tool_feedback(tmp_path: Path):
    context = prepare(tmp_path)
    config = context.context.config
    with h5py.File(config.rqalpha_bundle_dir / "suspended_days.h5", "a") as store:
        store.create_dataset("000001.XSHE", data=np.array([20240506]))
    with h5py.File(config.rqalpha_bundle_dir / "stocks.h5", "a") as store:
        bars = store["000001.XSHE"][:]
        for field in ("open", "close", "high", "low"):
            bars[field][bars["datetime"] == 20240507000000] = 11
        store["000001.XSHE"][:] = bars

    result = await invoke(context)

    assert isinstance(result, dict), result
    log = (config.workspace_dir / result["artifacts"]["execution_log"]).read_text()
    diagnostics = result["diagnostics"]
    assert diagnostics["status"] == "completed"
    assert diagnostics["warning_count"] == 2
    assert diagnostics["error_count"] == 0
    for reason in (
        "Order Creation Failed: security 000001.XSHE is suspended on 2024-05-06",
        "Order Rejected: current bar [000001.XSHE] reach the limit_up price.",
    ):
        assert reason in log
        assert reason in diagnostics["preview"]
    assert "native-run-marker" in log
    assert "2024-05-06" in log and "user_system_log" in log
    saved = json.loads((config.workspace_dir / result["artifacts"]["diagnostics"]).read_text())
    assert saved == diagnostics


async def test_failure_keeps_run_inputs_native_log_and_original_error_without_traceback_locals(tmp_path: Path):
    context = prepare(tmp_path)
    config = context.context.config
    (config.workspace_dir / "strategy.py").write_text(
        "def init(context):\n"
        "    logger.info('before-native-failure')\n"
        "    confidential_local = 'local-variable-canary'\n"
        "    raise RuntimeError('original-native-failure')\n"
    )

    result = await invoke(context)

    assert isinstance(result, str)
    assert "RuntimeError: original-native-failure" in result
    assert "Backtest artifacts: " in result
    artifacts = json.loads(result.split("Backtest artifacts: ", 1)[1])
    log = (config.workspace_dir / artifacts["execution_log"]).read_text()
    assert "before-native-failure" in log and "RuntimeError: original-native-failure" in log
    assert "local-variable-canary" not in log and "confidential_local" not in log
    assert "Traceback (most recent call last)" not in log
    assert (config.workspace_dir / artifacts["request"]).is_file()
    assert (config.workspace_dir / artifacts["strategy"]).is_file()
    diagnostics = json.loads((config.workspace_dir / artifacts["diagnostics"]).read_text())
    assert diagnostics["status"] == "failed" and diagnostics["error_count"] >= 1
    (config.workspace_dir / "strategy.py").write_text("def init(context):\n    logger.info('next-run-after-failure')\n")
    next_result = await invoke(context)
    assert isinstance(next_result, dict), next_result
    assert (config.workspace_dir / artifacts["execution_log"]).read_text() == log
    next_log = (config.workspace_dir / next_result["artifacts"]["execution_log"]).read_text()
    assert "next-run-after-failure" in next_log and "before-native-failure" not in next_log


async def test_concurrent_runs_do_not_capture_each_others_records(tmp_path: Path):
    contexts = [prepare(tmp_path / name, marker=name) for name in ("first-run-canary", "second-run-canary")]
    results = await asyncio.gather(*(invoke(context) for context in contexts))
    for index, (context, result) in enumerate(zip(contexts, results, strict=True)):
        assert isinstance(result, dict), result
        log = (context.context.config.workspace_dir / result["artifacts"]["execution_log"]).read_text()
        assert ("first-run-canary" if index == 0 else "second-run-canary") in log
        assert ("second-run-canary" if index == 0 else "first-run-canary") not in log


async def test_preview_is_config_bounded_and_full_log_redacts_configured_credentials(tmp_path: Path):
    context = prepare(tmp_path, max_log_preview_chars=40)
    config = context.context.config
    credential = config.secret_values()[0]
    (config.workspace_dir / "strategy.py").write_text(
        "def init(context):\n"
        f"    logger.warning({('native-warning ' + credential)!r})\n"
        "    logger.warning('another-native-warning')\n"
    )
    result = await invoke(context)
    assert isinstance(result, dict), result
    diagnostics = result["diagnostics"]
    log = (config.workspace_dir / result["artifacts"]["execution_log"]).read_text()
    assert len(diagnostics["preview"]) == 40 and diagnostics["preview_truncated"] is True
    assert diagnostics["warning_count"] == 2
    assert "another-native-warning" in log
    assert credential not in log and "<redacted>" in log
