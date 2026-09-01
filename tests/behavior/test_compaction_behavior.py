from __future__ import annotations

from pathlib import Path

import pytest
from agents import Agent, SQLiteSession

from investorch.agents import TokenUsage, compact_session
from investorch.storage import create_session
from tests.support.config import make_test_config


@pytest.mark.asyncio
async def test_empty_session_compaction_is_a_no_op(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session-a")
    session = SQLiteSession("session-a", config.sessions_db)
    try:
        result = await compact_session(
            Agent(name="Unreachable Compaction Agent", instructions="Must not run for empty history."),
            session,
            config,
        )
        history = await session.get_items()
    finally:
        session.close()

    assert result.changed is False
    assert result.source_items == 0
    assert result.summary_chars == 0
    assert result.usage == TokenUsage()
    assert history == []
