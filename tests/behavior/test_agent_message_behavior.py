import asyncio
import base64
import json
from functools import partial
from zoneinfo import ZoneInfo

import pytest
from agents.tool_context import ToolContext

from investorch.application import PortfolioOperations
from investorch.application.messaging import MessageImageReference, send_message
from investorch.application.review_context import ReviewContext
from investorch.application.session_queries import read_session
from investorch.context import AgentContext
from investorch.inputs import InputOrigin, UserInput
from investorch.journal import SessionJournal, read_session_journal
from investorch.presentation import serialize_follow_up_event, serialize_queue_item
from investorch.storage import archive_session, create_session, get_session
from investorch.tools.messaging import make_messaging_tools
from tests.behavior.test_interaction_routing_behavior import make_app_state
from tests.support.config import make_test_config
from tests.support.runtime import make_runtime_harness, run_options


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
    page = await read_session(
        "target", config=config, sessions_db=config.sessions_db, journal=journal, snapshot_seq=boundary
    )
    assert [record["type"] for record in page["records"]] == ["user", "user"]
    assert page["records"][1]["origin"] == records[1]["origin"]
    latest = await read_session("target", config=config, sessions_db=config.sessions_db, journal=journal)
    assert [record["text"] for record in latest["records"]] == ["human instruction", "Agent A instruction", "later C"]
    events = await read_session("target", config=config, sessions_db=config.sessions_db, journal=journal, view="events")
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


def message_tool(harness):
    state = make_app_state(harness, "sender")
    return make_messaging_tools(
        submit=partial(send_message, state=state, runtime=harness.runtime, journal=harness.journal)
    )[0]


async def invoke_message(tool, harness, arguments, *, sender="sender", run_id="sender-run"):
    encoded = json.dumps(arguments)
    context = ToolContext(
        context=AgentContext(
            config=harness.config,
            execution=harness.execution,
            session_id=sender,
            run_id=run_id,
            portfolios=PortfolioOperations(config=harness.config),
        ),
        tool_name="send_message",
        tool_call_id="message",
        tool_arguments=encoded,
    )
    return await tool.on_invoke_tool(context, encoded)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"target_session_id": "sender", "text": "self"}, "self_message"),
        ({"target_session_id": "missing", "text": "missing"}, "session_not_found"),
        ({"target_session_id": "tar", "text": "prefix"}, "session_not_found"),
        ({"target_session_id": "archived", "text": "archived"}, "session_archived"),
        ({"target_session_id": "target"}, "text or images"),
        ({"target_session_id": "target", "text": "spoof", "sender_session_id": "forged"}, "accepts only"),
        (
            {"target_session_id": "target", "images": [{"source": "workspace", "path": "../secret.png"}]},
            "image_path_invalid",
        ),
        (
            {"target_session_id": "target", "images": [{"source": "workspace", "path": "external.png"}]},
            "image_path_invalid",
        ),
        (
            {"target_session_id": "target", "images": [{"source": "session_event", "seq": 1, "image_index": 0}]},
            "image_not_effective",
        ),
    ],
)
async def test_corrective_tool_failures_leave_destinations_unchanged(tmp_path, arguments, expected):
    harness = make_runtime_harness(tmp_path)
    for session_id in ("sender", "target", "archived"):
        create_session(harness.config.sessions_db, session_id)
    archive_session(harness.config.sessions_db, "archived")
    private = harness.config.root / "secret.png"
    private.write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    (harness.config.workspace_dir / "external.png").symlink_to(private)
    await harness.journal.record_user_steer("sender", "sender-run", UserInput("pending", ()))
    try:
        error = await invoke_message(message_tool(harness), harness, arguments)
        assert isinstance(error, str) and expected in error
        assert str(harness.config.root) not in error
        assert "data:" not in error
        assert harness.runtime.list_active_runs() == []
        assert harness.runtime.list_queued_inputs("target") == []
        assert (
            await read_session(
                "target", config=harness.config, sessions_db=harness.config.sessions_db, journal=harness.journal
            )
        )["records"] == []
        assert get_session(harness.config.sessions_db, "missing") is None
        assert get_session(harness.config.sessions_db, "archived").archived_at is not None
    finally:
        await harness.runtime.aclose()


async def test_stopping_target_returns_a_safe_corrective_tool_error(tmp_path):
    discard_started = asyncio.Event()
    release_discard = asyncio.Event()

    async def hold_discard(session_id, run_id, seqs):
        discard_started.set()
        await release_discard.wait()
        return await harness.journal.record_user_steers_discarded(session_id, run_id, seqs)

    harness = make_runtime_harness(tmp_path, record_user_steers_discarded=hold_discard)
    create_session(harness.config.sessions_db, "target")
    try:
        harness.runtime.start_run("target", "initial", run_options())
        await harness.agent_loop.wait_until_started("target")
        await harness.runtime.submit_follow_up("target", "pending", run_options())
        harness.runtime.cancel_run("target")
        await asyncio.wait_for(discard_started.wait(), 2)
        error = await invoke_message(
            message_tool(harness), harness, {"target_session_id": "target", "text": "too late"}
        )
        assert isinstance(error, str) and ("target_busy" in error or "queue_paused" in error)
        assert "too late" not in str(
            (
                await read_session(
                    "target", config=harness.config, sessions_db=harness.config.sessions_db, journal=harness.journal
                )
            )["records"]
        )
    finally:
        release_discard.set()
        await harness.runtime.aclose()


async def test_active_target_preserves_origin_through_existing_queue_promotion_and_receipts(tmp_path):
    behavior = "queue"
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "target")
    tool = message_tool(harness)
    try:
        active = harness.runtime.start_run("target", "human instruction", run_options(behavior))
        await harness.agent_loop.wait_until_started("target")
        receipt = await invoke_message(tool, harness, {"target_session_id": "target", "text": "Agent follow-up"})
        assert receipt["disposition"] == f"{behavior}_submitted"
        assert receipt["run_id"] == active.run_id
        assert receipt["follow_up_id"]
        origin = {"kind": "agent", "sender_session_id": "sender", "sender_run_id": "sender-run"}
        event = await harness.wait_for_follow_up(f"{behavior}_submitted")
        assert serialize_follow_up_event(event)["origin"] == origin
        if behavior == "queue":
            assert serialize_queue_item(harness.runtime.list_queued_inputs("target")[0])["origin"] == origin
            assert (
                len(
                    (
                        await read_session(
                            "target",
                            config=harness.config,
                            sessions_db=harness.config.sessions_db,
                            journal=harness.journal,
                        )
                    )["records"]
                )
                == 1
            )
        harness.agent_loop.complete("target")
        await harness.agent_loop.wait_until_started("target", 2)
        promoted = harness.agent_loop.input_for("target").structured_input
        assert promoted.text == "Agent follow-up"
        assert promoted.origin == InputOrigin("agent", "sender", "sender-run")
        records = (
            await read_session(
                "target", config=harness.config, sessions_db=harness.config.sessions_db, journal=harness.journal
            )
        )["records"]
        assert [record["text"] for record in records] == ["human instruction", "Agent follow-up"]
        assert records[1]["origin"] == origin
        harness.agent_loop.complete("target")
        await harness.wait_for_run_ended("target", 2)
    finally:
        await harness.runtime.aclose()


async def test_message_uses_global_capacity_for_idle_targets_and_keeps_active_queue_submission(tmp_path):
    harness = make_runtime_harness(tmp_path, config_overrides={"runtime": {"max_concurrent_runs": 1}})
    for session_id in ("target", "idle"):
        create_session(harness.config.sessions_db, session_id)
    try:
        harness.runtime.start_run("target", "occupy", run_options("queue"))
        await harness.agent_loop.wait_until_started("target")
        error = await invoke_message(message_tool(harness), harness, {"target_session_id": "idle", "text": "new"})
        assert isinstance(error, str) and "capacity_exceeded" in error
        assert harness.runtime.session_snapshot("idle").run_id is None
        receipt = await invoke_message(
            message_tool(harness), harness, {"target_session_id": "target", "text": "queued"}
        )
        assert receipt["disposition"] == "queue_submitted"
        assert len(harness.runtime.list_active_runs()) == 1
    finally:
        await harness.runtime.aclose()


async def test_paused_queue_and_failed_steer_write_do_not_accept_another_message(tmp_path):
    async def failed_steer(_session_id, _run_id, _input):
        raise OSError("private Journal path canary")

    harness = make_runtime_harness(tmp_path, record_user_steer=failed_steer)
    create_session(harness.config.sessions_db, "steer")
    create_session(harness.config.sessions_db, "queue")
    try:
        harness.runtime.start_run("steer", "initial", run_options())
        await harness.agent_loop.wait_until_started("steer")
        error = await invoke_message(
            message_tool(harness), harness, {"target_session_id": "steer", "text": "failed input"}
        )
        assert isinstance(error, str) and "input_rejected" in error
        assert "private Journal path canary" not in error
        harness.agent_loop.complete("steer")
        await harness.wait_for_run_ended("steer")
        assert [
            record["text"]
            for record in (
                await read_session(
                    "steer", config=harness.config, sessions_db=harness.config.sessions_db, journal=harness.journal
                )
            )["records"]
        ] == ["initial"]
        active = harness.runtime.start_run("queue", "initial", run_options("queue"))
        await harness.agent_loop.wait_until_started("queue")
        await harness.runtime.submit_follow_up("queue", "accepted", run_options())
        harness.runtime.cancel_run("queue")
        with pytest.raises(asyncio.CancelledError):
            await active.task
        error = await invoke_message(message_tool(harness), harness, {"target_session_id": "queue", "text": "rejected"})
        assert isinstance(error, str) and "queue_paused" in error
        assert [item.user_input.text for item in harness.runtime.list_queued_inputs("queue")] == ["accepted"]
    finally:
        await harness.runtime.aclose()


async def test_host_bound_tool_does_not_route_identical_ids_to_another_root_and_reply_is_explicit(tmp_path):
    first = make_runtime_harness(tmp_path / "first")
    second = make_runtime_harness(tmp_path / "second")
    for harness in (first, second):
        for session_id in ("sender", "target"):
            create_session(harness.config.sessions_db, session_id)
    try:
        tool = message_tool(first)
        receipt = await invoke_message(tool, first, {"target_session_id": "target", "text": "first Root only"})
        assert receipt["disposition"] == "run_started"
        await first.agent_loop.wait_until_started("target")
        assert second.runtime.is_session_active("target") is False
        assert (
            await read_session(
                "target", config=second.config, sessions_db=second.config.sessions_db, journal=second.journal
            )
        )["records"] == []
        reply = await invoke_message(
            tool, first, {"target_session_id": "sender", "text": "explicit reply"}, sender="target", run_id="reply-run"
        )
        assert reply["disposition"] == "run_started"
        await first.agent_loop.wait_until_started("sender")
        assert first.agent_loop.input_for("sender").structured_input.origin == InputOrigin(
            "agent", "target", "reply-run"
        )
    finally:
        await first.runtime.aclose()
        await second.runtime.aclose()
