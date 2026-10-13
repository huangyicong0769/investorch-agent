from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from investorch.application.session_queries import read_session
from investorch.journal import SessionJournal
from investorch.output import AssistantMessage
from investorch.storage import archive_session, create_session
from tests.support.config import make_test_config
from tests.support.session_history import record_mixed_history


@pytest.mark.asyncio
async def test_history_queries_need_no_runtime_and_repeated_frozen_reads_have_no_hidden_position(
    tmp_path: Path,
) -> None:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    create_session(config.sessions_db, "b")
    await record_mixed_history(journal, "b")
    query = partial(read_session, sessions_db=config.sessions_db, journal=journal)

    first = await query("b", limit=2)
    await journal.record_output("b", AssistantMessage("later append"))
    archive_session(config.sessions_db, "b")
    repeated = await query("b", limit=2, snapshot_seq=first["snapshot_seq"])
    older = await query("b", limit=2, before_seq=first["next_before_seq"], snapshot_seq=first["snapshot_seq"])

    assert first == repeated
    assert first["snapshot_seq"] == 16
    assert [record["seq"] for record in first["records"]] == [14, 15]
    assert [record["seq"] for record in older["records"]] == [5, 7]
    assert older["next_before_seq"] == 5
    assert older["has_older"] is True
