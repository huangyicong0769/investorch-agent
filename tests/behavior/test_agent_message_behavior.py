import base64
from zoneinfo import ZoneInfo

from investorch.application.messaging import MessageImageReference, send_message
from investorch.application.review_context import ReviewContext
from investorch.application.session_queries import read_session
from investorch.images import InputOrigin, UserInput
from investorch.journal import SessionJournal, read_session_journal
from investorch.storage import create_session
from tests.behavior.test_interaction_routing_behavior import make_app_state
from tests.support.config import make_test_config
from tests.support.runtime import make_runtime_harness


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


async def test_workspace_and_sender_history_images_arrive_as_normalized_inputs(tmp_path):
    harness = make_runtime_harness(tmp_path)
    state = make_app_state(harness, "sender")
    for session_id in ("sender", "target", "image-target"):
        create_session(harness.config.sessions_db, session_id)
    data = b"\x89PNG\r\n\x1a\n" + b"small chart"
    (harness.config.workspace_dir / "chart.jpg").write_bytes(data)
    try:
        receipt = await send_message(
            state=state,
            runtime=harness.runtime,
            journal=harness.journal,
            source_session_id="sender",
            source_run_id="sender-run",
            target_session_id="target",
            text="  inspect this\n",
            images=[MessageImageReference(source="workspace", path="chart.jpg", detail="high")],
        )
        assert receipt.disposition == "run_started"
        await harness.agent_loop.wait_until_started("target")
        delivered = harness.agent_loop.input_for("target").structured_input
        assert delivered.text == "  inspect this\n"
        assert delivered.origin == InputOrigin("agent", "sender", "sender-run")
        assert delivered.images[0].image_url == "data:image/png;base64," + base64.b64encode(data).decode()
        assert delivered.images[0].media_type == "image/png"
        assert delivered.images[0].detail == "high"
        seq = await harness.journal.record_user_message("sender", UserInput("upload", delivered.images))
        image_receipt = await send_message(
            state=state,
            runtime=harness.runtime,
            journal=harness.journal,
            source_session_id="sender",
            source_run_id="sender-run",
            target_session_id="image-target",
            images=[MessageImageReference(source="session_event", seq=seq, image_index=0)],
        )
        assert image_receipt.disposition == "run_started"
        await harness.agent_loop.wait_until_started("image-target")
        image_input = harness.agent_loop.input_for("image-target").structured_input
        assert image_input.text == ""
        assert image_input.images == delivered.images
    finally:
        await harness.runtime.aclose()
