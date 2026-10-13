from zoneinfo import ZoneInfo

from investorch.application.review_context import ReviewContext
from investorch.application.session_queries import read_session
from investorch.images import InputOrigin, UserInput
from investorch.journal import SessionJournal, read_session_journal
from investorch.storage import create_session
from tests.support.config import make_test_config


async def test_mixed_origins_preserve_target_ownership_and_frozen_effective_history(tmp_path):
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "target")
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    sender_a = InputOrigin("agent", "session-a", "run-a")
    sender_c = InputOrigin("agent", "session-c", "run-c")
    await journal.record_user_message("target", "human instruction")
    effective = await journal.record_user_steer("target", "target-run", UserInput("Agent A instruction", (), sender_a))
    boundary = await journal.record_user_steers_activated("target", "target-run", (effective,))
    discarded = await journal.record_user_steer("target", "target-run", UserInput("discarded C", (), sender_c))
    await journal.record_user_steers_discarded("target", "target-run", (discarded,))
    await journal.record_user_message("target", UserInput("later C", (), sender_c))

    records = read_session_journal(config.session_journal_dir, "target")
    assert "origin" not in records[0]
    assert records[1]["run_id"] == "target-run"
    assert records[1]["origin"] == {"kind": "agent", "sender_session_id": "session-a", "sender_run_id": "run-a"}
    prepared = await ReviewContext(config=config).prepare("target", boundary)
    assert prepared.instruction_count == 2
    assert 'Session "session-a" (Run "run-a")' in prepared.text
    assert "Agent A instruction" in prepared.text
    assert "later C" not in prepared.text
    assert "discarded C" not in prepared.text
    page = await read_session("target", sessions_db=config.sessions_db, journal=journal, snapshot_seq=boundary)
    assert [record["type"] for record in page["records"]] == ["user", "user"]
    assert page["records"][1]["origin"] == records[1]["origin"]
    latest = await read_session("target", sessions_db=config.sessions_db, journal=journal)
    assert [record["text"] for record in latest["records"]] == ["human instruction", "Agent A instruction", "later C"]
    events = await read_session("target", sessions_db=config.sessions_db, journal=journal, view="events")
    assert events["records"] == records
