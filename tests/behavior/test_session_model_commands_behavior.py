from investorch.commands.handlers import dispatch_command
from investorch.commands.parser import parse_command
from investorch.context import AppState
from tests.support.runtime import run_options
from tests.support.sessions import make_session_harness


async def test_model_and_effort_commands_change_only_selected_sessions_next_run(tmp_path):
    harness = make_session_harness(tmp_path)
    runtime = harness.runtime.runtime
    session = await harness.operations.create()
    other = await harness.operations.create()
    state = AppState(harness.runtime.config, harness.runtime.execution, session, runtime.session_models)

    async def command(text):
        return await dispatch_command(parse_command(text), state, runtime=runtime, sessions=harness.operations)

    try:
        active = runtime.start_run(session, "running", run_options())
        await harness.runtime.agent_loop.wait_until_started(session)
        before = active.options.model
        await command("/effort low")
        assert runtime.session_models.view(session).draft.reasoning_effort == "low"
        assert active.options.model == before
        assert runtime.session_models.view(other).draft is None
        shown = (await command("/model")).output
        assert "Active:" in shown and "Saved:" in shown and "Next Run:" in shown
        assert "effort low" in shown
        await command("/model test replacement-model high 80000")
        assert runtime.session_models.view(session).draft.name == "replacement-model"
        assert runtime.session_models.view(session).persisted == before
        rejected = await command("/model test replacement-model high 0")
        assert "positive integer" in rejected.output
        assert runtime.session_models.view(session).draft.context_window_tokens == 80000
    finally:
        await runtime.aclose()
