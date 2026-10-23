import ast
import asyncio
import json
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import ModelSettings
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from agents.tool_context import ToolContext

from investorch.agents import create_agent
from investorch.application import ApplicationCallbacks, PortfolioOperations, open_application_host
from investorch.application.session_queries import list_sessions as list_sessions_query
from investorch.application.session_queries import read_session as read_session_query
from investorch.context import AgentContext
from investorch.journal import SessionJournal, read_session_journal
from investorch.output import ToolOutput
from investorch.storage import create_session, get_session, set_session_title
from investorch.tools import make_session_tools
from tests.support.config import make_test_config
from tests.support.model_connections import install_host_models
from tests.support.runtime import make_runtime_harness, run_options


@pytest.mark.asyncio
async def test_session_tools_are_read_only_and_surface_actionable_safe_errors(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    context = ToolContext(
        context=AgentContext(
            config=harness.config,
            execution=harness.execution,
            session_id="a",
            run_id="reader",
            portfolios=PortfolioOperations(config=harness.config),
        ),
        tool_name="session-test",
        tool_call_id="call-a",
        tool_arguments="{}",
    )
    list_sessions, read_session = make_session_tools(
        list_query=partial(list_sessions_query, sessions_db=harness.config.sessions_db, runtime=harness.runtime),
        read_query=partial(read_session_query, sessions_db=harness.config.sessions_db, journal=harness.journal),
    )
    assert list_sessions.needs_approval is read_session.needs_approval is False
    assert set(list_sessions.params_json_schema["properties"]) == {"query", "include_archived", "limit", "offset"}
    assert (await list_sessions.on_invoke_tool(context, "{}"))["sessions"][0]["session_id"] == "a"
    assert (await read_session.on_invoke_tool(context, '{"session_id":"a"}'))["records"] == []
    assert "session_not_found" in await read_session.on_invoke_tool(context, '{"session_id":"unknown"}')
    assert "limit" in await list_sessions.on_invoke_tool(context, '{"limit":0}')
    assert "filename-safe" in await read_session.on_invoke_tool(context, '{"session_id":"../private-canary"}')
    for tool, arguments in [
        (list_sessions, {"limit": True}),
        (list_sessions, {"limit": "2"}),
        (list_sessions, {"offset": False}),
        (list_sessions, {"include_archived": 1}),
        (read_session, {"session_id": "a", "limit": True}),
        (read_session, {"session_id": "a", "before_seq": True}),
        (read_session, {"session_id": "a", "snapshot_seq": False}),
    ]:
        feedback = await tool.on_invoke_tool(context, json.dumps(arguments))
        assert isinstance(feedback, str)
        assert "Invalid JSON input" in feedback
    (harness.config.session_journal_dir / "a.jsonl").write_text("private-content-canary\n", encoding="utf-8")
    error = await read_session.on_invoke_tool(context, '{"session_id":"a"}')
    assert "journal_invalid" in error
    assert "private-content-canary" not in error
    assert str(harness.config.root) not in error
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_real_host_agent_reads_self_and_target_states_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    for session_id in ["a", "b", "c"]:
        create_session(config.sessions_db, session_id)
        set_session_title(config.sessions_db, session_id, f"Session {session_id}")
    b_started = asyncio.Event()
    release_b = asyncio.Event()
    c_waiting = asyncio.Event()
    release_c = asyncio.Event()
    outputs = []

    async def wait_b(_call: object) -> tuple:
        b_started.set()
        await release_b.wait()
        return (assistant_message("B complete"),)

    model = ScriptedModel(
        (
            ModelStep.respond(wait_b),
            (
                function_call(
                    "create_portfolio", {"name": "Approval probe", "base_currency": "CNY"}, call_id="c-create"
                ),
            ),
            (
                function_call("list_sessions", {}, call_id="list"),
                function_call("read_session", {"session_id": "b"}, call_id="other"),
                function_call("read_session", {"session_id": "a"}, call_id="self"),
            ),
            (assistant_message("A complete"),),
            (assistant_message("C rejected and complete"),),
        )
    )

    def model_for_role(_config: object, role: str) -> tuple:
        return (model if role == "main" else ScriptedModel()), ModelSettings()

    async def capture(output: object, _seq: int | None) -> None:
        outputs.append(output)

    async def approve_manually(request: object, _reason: str | None) -> bool:
        assert request.session_id == "c"
        c_waiting.set()
        await release_c.wait()
        return False

    install_host_models(monkeypatch, model_for_role)
    async with open_application_host(
        config,
        manual_approval_handler=approve_manually,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        selected = host.state.selected_session_id
        b = host.runtime.start_run("b", "B input", run_options())
        await asyncio.wait_for(b_started.wait(), 3)
        c = host.runtime.start_run("c", "C input", run_options())
        await asyncio.wait_for(c_waiting.wait(), 3)
        assert host.runtime.session_snapshot("c").run_phase == "waiting_approval"
        b_journal = read_session_journal(config.session_journal_dir, "b")
        b_metadata = get_session(config.sessions_db, "b")
        c_journal = read_session_journal(config.session_journal_dir, "c")
        c_metadata = get_session(config.sessions_db, "c")
        a = host.runtime.start_run("a", "A input", run_options())
        await asyncio.wait_for(a.task, 3)

        answers = [ast.literal_eval(output.event.output) for output in outputs if isinstance(output.event, ToolOutput)]
        listing = next(answer for answer in answers if "sessions" in answer)
        other = next(answer for answer in answers if answer.get("session_id") == "b")
        own = next(answer for answer in answers if answer.get("session_id") == "a")
        assert {item["session_id"]: item["status"] for item in listing["sessions"]} == {
            "a": "running",
            "b": "running",
            "c": "waiting_approval",
        }
        assert other["records"][0]["text"] == "B input"
        assert own["records"][0]["text"] == "A input"
        assert host.runtime.session_snapshot("b").run_id == b.run_id
        assert host.state.selected_session_id == selected
        assert get_session(config.sessions_db, "b") == b_metadata
        assert read_session_journal(config.session_journal_dir, "b") == b_journal
        assert get_session(config.sessions_db, "c") == c_metadata
        assert read_session_journal(config.session_journal_dir, "c") == c_journal
        listing = await list_sessions_query(sessions_db=host.config.sessions_db, runtime=host.runtime)
        assert {item["session_id"] for item in listing["sessions"]} == {"a", "b", "c"}
        release_c.set()
        await asyncio.wait_for(c.task, 3)
        release_b.set()
        await asyncio.wait_for(b.task, 3)
        model.assert_complete()


@pytest.mark.asyncio
async def test_sdk_output_stays_bounded_when_python_representation_expands_quoted_text(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    for _ in range(3):
        await harness.journal.record_user_message("a", "'" * 120000 + '"')
    read_query = partial(read_session_query, sessions_db=harness.config.sessions_db, journal=harness.journal)

    result = await read_query("a")

    assert len(json.dumps(result)) <= 262144
    assert len(str(result)) <= 262144
    assert result["has_older"] is True
    assert [record["seq"] for record in result["records"]] == [3]
    await harness.runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["cancelled", "failed"])
async def test_paused_accepted_queue_is_pending_but_host_restart_reports_current_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_status: str
) -> None:
    harness = make_runtime_harness(
        tmp_path, config_overrides={"backtest": {"use_cnequity": False}}, journal_run_ended=True
    )
    create_session(harness.config.sessions_db, "a")
    set_session_title(harness.config.sessions_db, "a", "Persisted")
    if terminal_status == "failed":
        harness.agent_loop.fail_input("initial")
    active = harness.runtime.start_run("a", "initial", run_options("queue"))
    await harness.agent_loop.wait_until_started("a")
    await harness.runtime.submit_follow_up("a", "accepted queue", run_options("queue"))
    if terminal_status == "cancelled":
        harness.runtime.cancel_run("a")
        with pytest.raises(asyncio.CancelledError):
            await active.task
    else:
        harness.agent_loop.complete("a")
        with pytest.raises(RuntimeError, match="controlled Agent failure"):
            await active.task
    list_query = partial(list_sessions_query, sessions_db=harness.config.sessions_db, runtime=harness.runtime)
    read_query = partial(read_session_query, sessions_db=harness.config.sessions_db, journal=harness.journal)
    assert (await list_query())["sessions"][0]["status"] == "pending"
    assert harness.runtime.session_snapshot("a").queue_paused is True
    assert (await read_query("a"))["records"][0]["text"] == "initial"
    await harness.runtime.aclose()

    def unused_model(_config: object, _role: str) -> tuple:
        return ScriptedModel(), ModelSettings()

    async def unused_approval(_request: object, _reason: str | None) -> bool:
        raise AssertionError("Reading after restart must not request approval")

    install_host_models(monkeypatch, unused_model)
    async with open_application_host(
        harness.config, manual_approval_handler=unused_approval, create_initial_session=False, enable_activity=False
    ) as host:
        listing = await list_sessions_query(sessions_db=host.config.sessions_db, runtime=host.runtime)
        assert listing["sessions"][0]["status"] == "idle"
        history = await read_session_query(
            "a", sessions_db=host.config.sessions_db, journal=host.journal, view="events"
        )
        assert history["records"][-1]["status"] == terminal_status


@pytest.mark.asyncio
async def test_first_and_repeated_host_runs_publish_unique_session_tools_and_keep_base_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_test_config(tmp_path, {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}})
    create_session(config.sessions_db, "a")
    set_session_title(config.sessions_db, "a", "Reader")
    model = ScriptedModel(
        (
            (
                function_call("list_sessions", {}, call_id="first-list"),
                function_call("read_session", {"session_id": "a"}, call_id="first-read"),
            ),
            (assistant_message("First complete"),),
            (
                function_call("list_sessions", {}, call_id="second-list"),
                function_call("read_session", {"session_id": "a"}, call_id="second-read"),
            ),
            (assistant_message("Second complete"),),
        )
    )
    base_names = {tool.name for tool in create_agent(model=model, model_settings=ModelSettings(), config=config).tools}
    answers = []

    def model_for_role(_config: object, role: str) -> tuple:
        return (model if role == "main" else ScriptedModel()), ModelSettings()

    async def capture(output: object, _seq: int | None) -> None:
        if isinstance(output.event, ToolOutput):
            answers.append(ast.literal_eval(output.event.output))

    async def unexpected_approval(_request: object, _reason: str | None) -> bool:
        raise AssertionError("Session reads must not request approval")

    install_host_models(monkeypatch, model_for_role)
    async with open_application_host(
        config,
        manual_approval_handler=unexpected_approval,
        callbacks=ApplicationCallbacks(handle_output=capture),
        create_initial_session=False,
        enable_activity=False,
    ) as host:
        for message in ["First input", "Second input"]:
            run = host.runtime.start_run("a", message, run_options())
            await asyncio.wait_for(run.task, 3)
        assert len(answers) == 4
        assert all(isinstance(answer, dict) for answer in answers)
        assert len(model.calls) == 4
        for call in model.calls:
            names = [tool.name for tool in call.tools]
            assert len(names) == len(set(names))
            assert set(names) == base_names | {"list_sessions", "read_session", "send_message", "spawn_agent"}
        model.assert_complete()


@pytest.mark.asyncio
async def test_two_hosts_bind_same_session_ids_to_their_own_root_history_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = [
        make_test_config(
            tmp_path / str(index), {"backtest": {"use_cnequity": False}, "compaction": {"auto_enabled": False}}
        )
        for index in range(2)
    ]
    for index, config in enumerate(configs):
        for session_id in ["a", "b"]:
            create_session(config.sessions_db, session_id)
            set_session_title(config.sessions_db, session_id, f"Root {index} Session {session_id}")
        await SessionJournal(config.session_journal_dir, ZoneInfo("UTC")).record_user_message(
            "b", f"Root {index} history"
        )
    b_started = asyncio.Event()
    release_b = asyncio.Event()

    async def wait_b(_call: object) -> tuple:
        b_started.set()
        await release_b.wait()
        return (assistant_message("B complete"),)

    models = {}
    for index, config in enumerate(configs):
        steps = [
            (
                function_call("list_sessions", {}, call_id=f"list-{index}"),
                function_call("read_session", {"session_id": "b"}, call_id=f"read-{index}"),
            ),
            (assistant_message(f"Root {index} complete"),),
        ]
        if index == 0:
            steps.insert(0, ModelStep.respond(wait_b))
        models[config.root] = ScriptedModel(steps)
    answers = [[], []]

    def model_for_role(config: object, role: str) -> tuple:
        return (models[config.root] if role == "main" else ScriptedModel()), ModelSettings()

    async def capture(results: list, output: object, _seq: int | None) -> None:
        if isinstance(output.event, ToolOutput):
            results.append(ast.literal_eval(output.event.output))

    async def unexpected_approval(_request: object, _reason: str | None) -> bool:
        raise AssertionError("Session reads must not request approval")

    install_host_models(monkeypatch, model_for_role)
    async with (
        open_application_host(
            configs[0],
            manual_approval_handler=unexpected_approval,
            callbacks=ApplicationCallbacks(handle_output=partial(capture, answers[0])),
            create_initial_session=False,
            enable_activity=False,
        ) as first_host,
        open_application_host(
            configs[1],
            manual_approval_handler=unexpected_approval,
            callbacks=ApplicationCallbacks(handle_output=partial(capture, answers[1])),
            create_initial_session=False,
            enable_activity=False,
        ) as second_host,
    ):
        b = first_host.runtime.start_run("b", "B input", run_options())
        await asyncio.wait_for(b_started.wait(), 3)
        for host in [first_host, second_host]:
            a = host.runtime.start_run("a", "A input", run_options())
            await asyncio.wait_for(a.task, 3)
        for index, expected_status in enumerate(["running", "idle"]):
            listing = next(answer for answer in answers[index] if "sessions" in answer)
            history = next(answer for answer in answers[index] if answer.get("session_id") == "b")
            target = next(item for item in listing["sessions"] if item["session_id"] == "b")
            assert target["status"] == expected_status
            assert target["title"] == f"Root {index} Session b"
            assert history["records"][0]["text"] == f"Root {index} history"
            assert f"Root {1 - index} history" not in str(history)
        assert second_host.runtime.session_snapshot("b").run_id is None
        release_b.set()
        await asyncio.wait_for(b.task, 3)
        for model in models.values():
            model.assert_complete()
