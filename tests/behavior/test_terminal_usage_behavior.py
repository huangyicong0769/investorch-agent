from datetime import UTC, datetime
from pathlib import Path

from textual.widgets import Static

from investorch.agents import TokenUsage
from investorch.application import ActivityLabelEvent, SessionPresentationStore
from investorch.runtime import RuntimeRunEnded
from investorch.ui.tui.app import InvestOrchAgentTUI
from tests.support.web import open_test_web


async def test_terminal_usage_uses_shared_snapshots_without_recounting_events(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        session_id = await web.host.sessions.create()
        other_id = await web.host.sessions.create()
        web.host.state.selected_session_id = session_id
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        store = SessionPresentationStore(app.handle_presentation_state)
        model = web.host.state.session_models.view(session_id).effective
        main = TokenUsage(
            requests=1, input_tokens=2000, output_tokens=200, total_tokens=2200, last_request_total_tokens=2200
        )
        auxiliary = TokenUsage(requests=1, input_tokens=100, output_tokens=20, total_tokens=120)
        async with app.run_test() as pilot:
            await pilot.pause()
            store.add_usage(session_id, main, model)
            store.add_usage(session_id, auxiliary)
            before = store.get(session_id)
            web.host.state.session_models.update(session_id, name="next-model", context_window_tokens=900000)
            await pilot.pause()
            rendered = str(app.query_one("#usage-status", Static).render())
            assert "Main · req 1" in rendered and "total 2.2K" in rendered
            assert "Auxiliary · req 1" in rendered and "total 120" in rendered
            assert "Main context · 2.2K / 1.00M · test-model" in rendered
            assert "next-model" not in rendered

            await app.handle_activity_label(ActivityLabelEvent(session_id, "run", 1, 2, "Inspecting", auxiliary))
            now = datetime.now(UTC)
            await app.handle_run_ended(
                RuntimeRunEnded(
                    session_id,
                    "run",
                    "failed",
                    now,
                    now,
                    None,
                    partial_auxiliary_usage=auxiliary,
                )
            )
            assert str(app.query_one("#usage-status", Static).render()) == rendered
            store.add_usage(other_id, main, model)
            assert str(app.query_one("#usage-status", Static).render()) == rendered

            store.observe_context(session_id, 800, model, compacting=True)
            await pilot.pause()
            rendered = str(app.query_one("#usage-status", Static).render())
            assert "Main context (estimated) · 800 / 1.00M · test-model · Compacting…" in rendered
            app.handle_presentation_state(session_id, before)
            assert str(app.query_one("#usage-status", Static).render()) == rendered
            store.observe_context(session_id, 300, model)
            await pilot.pause()
            rendered = str(app.query_one("#usage-status", Static).render())
            assert "Main context (estimated) · 300 / 1.00M" in rendered
            assert "Compacting" not in rendered
