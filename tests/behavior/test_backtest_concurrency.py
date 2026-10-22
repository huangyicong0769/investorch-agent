"""Concurrent Sessions cannot overlap RQAlpha's process-global lifecycle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from investorch.tools.quant import run_backtest
from tests.behavior.test_workspace_capabilities import make_tool_context
from tests.support.rqalpha import write_native_bundle


@pytest.mark.parametrize("fail_first_strategy", [False, True])
async def test_backtests_from_distinct_sessions_finish_the_engine_lifecycle_before_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_first_strategy: bool
) -> None:
    probe_dir = tmp_path / "engine-probe"
    probe_dir.mkdir()
    monkeypatch.syspath_prepend(str(probe_dir))
    # A real RQAlpha mod detects overlap through an atomic filesystem operation.
    # Start first and tear down last, keeping the marker while the analyser builds results.
    (probe_dir / "investorch_lifecycle_probe.py").write_text(
        "import time\n"
        "from pathlib import Path\n"
        "from rqalpha.interface import AbstractMod\n"
        "from rqalpha.environment import Environment\n"
        "class Probe(AbstractMod):\n"
        "    def start_up(self, env, config):\n"
        "        self.marker = Path(config.marker)\n"
        "        self.trace = Path(config.trace)\n"
        "        self.name = config.name\n"
        "        self.marker.mkdir()\n"
        "        self.env = env\n"
        "        with self.trace.open('a') as stream: stream.write(self.name + ':start\\n')\n"
        "        time.sleep(0.15)\n"
        "    def tear_down(self, code, exception=None):\n"
        "        if not hasattr(self, 'env'): return\n"
        "        time.sleep(0.15)\n"
        "        assert Environment.get_instance() is self.env\n"
        "        with self.trace.open('a') as stream: stream.write(self.name + ':end\\n')\n"
        "        self.marker.rmdir()\n"
        "def load_mod(): return Probe()\n"
    )
    marker = probe_dir / "active"
    trace = probe_dir / "trace.txt"
    contexts = []
    for name in ("session-a", "session-b"):
        context = make_tool_context(tmp_path / name, {"backtest": {"use_cnequity": False}})
        context.context.session_id = name
        config = context.context.config
        write_native_bundle(config.rqalpha_bundle_dir)
        plugin = {
            "enabled": True,
            "priority": -100,
            "lib": "investorch_lifecycle_probe",
            "marker": str(marker),
            "trace": str(trace),
            "name": name,
        }
        strategy = (
            f"__config__ = {{'mod': {{'lifecycle_probe': {plugin!r}}}}}\n"
            "def init(context):\n"
            "    subscribe('000001.XSHE')\n"
            "def handle_bar(context, bar_dict):\n"
            "    order_target_percent('000001.XSHE', 0.5)\n"
        )
        if fail_first_strategy and name == "session-a":
            strategy = strategy.replace("subscribe('000001.XSHE')", "raise ValueError('invalid strategy parameter')")
        (config.workspace_dir / "strategy.py").write_text(strategy)
        contexts.append(context)

    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                run_backtest.on_invoke_tool(
                    context,
                    json.dumps({"strategy_path": "strategy.py", "start_date": "2024-05-06", "end_date": "2024-05-10"}),
                )
                for context in contexts
            )
        ),
        # The first real run also imports engine extensions and builds font caches.
        timeout=60,
    )

    if fail_first_strategy:
        assert isinstance(results[0], str) and "ValueError: invalid strategy parameter" in results[0]
        assert isinstance(results[1], dict), results
    else:
        assert all(isinstance(result, dict) for result in results), results
    events = trace.read_text().splitlines()
    assert events in (
        ["session-a:start", "session-a:end", "session-b:start", "session-b:end"],
        ["session-b:start", "session-b:end", "session-a:start", "session-a:end"],
    )
    for context, result in zip(contexts, results, strict=True):
        if isinstance(result, str):
            continue
        assert result["summary"]["strategy_file"] == "strategy.py"
        assert (context.context.config.workspace_dir / result["artifacts"]["trades"]).is_file()
