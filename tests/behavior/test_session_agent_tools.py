import ast
import asyncio
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agents import ModelSettings
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.agents import create_agent
from investorch.application import ApplicationCallbacks, open_application_host
from investorch.application.session_queries import list_sessions as list_sessions_query
from investorch.journal import SessionJournal, read_session_journal
from investorch.output import ToolOutput
from investorch.storage import create_session, get_session, set_session_title
from tests.support.config import make_test_config
from tests.support.model_connections import install_host_models
from tests.support.runtime import run_options


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
        listing = await list_sessions_query(
            config=host.config, sessions_db=host.config.sessions_db, runtime=host.runtime
        )
        assert {item["session_id"] for item in listing["sessions"]} == {"a", "b", "c"}
        release_c.set()
        await asyncio.wait_for(c.task, 3)
        release_b.set()
        await asyncio.wait_for(b.task, 3)
        model.assert_complete()


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
            assert set(names) == base_names | {
                "list_sessions",
                "read_session",
                "send_message",
                "spawn_agent",
                "wait_agents",
            }
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
