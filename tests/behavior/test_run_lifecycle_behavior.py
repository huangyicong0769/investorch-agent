from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.runtime import make_runtime_harness, run_options


@pytest.mark.asyncio
async def test_other_session_can_start_while_first_session_is_running(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    harness.runtime.start_run("session-a", "first", run_options())
    await harness.agent_loop.wait_until_started("session-a")

    harness.runtime.start_run("session-b", "second", run_options())
    await harness.agent_loop.wait_until_started("session-b")

    assert harness.runtime.session_snapshot("session-a").run_id is not None
    assert harness.runtime.session_snapshot("session-b").run_id is not None

    harness.agent_loop.complete("session-b")
    ended = await harness.wait_for_run_ended("session-b")
    assert ended.status == "completed"
    assert harness.runtime.session_snapshot("session-b").run_id is None
    assert harness.runtime.session_snapshot("session-a").run_id is not None

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()
