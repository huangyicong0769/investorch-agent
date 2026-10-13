from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Label, Static

from investorch.images import ImageContent, InputOrigin, UserInput
from investorch.journal import read_session_journal
from investorch.runtime import RuntimeFollowUpEvent
from investorch.ui.tui.app import Composer, InvestOrchAgentTUI
from investorch.ui.tui.timeline import ChatTimeline
from tests.support.runtime import run_options
from tests.support.web import open_test_web

IMAGE = ImageContent("data:image/png;base64,iVBORw0KGgo=", filename="chart.png", media_type="image/png")
ORIGIN = InputOrigin("agent", "sender-session-full-id", "sender-run")


@pytest.mark.asyncio
async def test_terminal_history_and_live_steer_show_source_in_the_user_message_view(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        session_id = await web.host.sessions.create()
        web.host.state.selected_session_id = session_id
        await web.host.journal.record_user_message(session_id, "Original user input")
        await web.host.journal.record_user_message(session_id, UserInput("Agent body", (IMAGE,), ORIGIN))
        await web.host.journal.record_user_steer(session_id, "target-run", UserInput("Agent steer", origin=ORIGIN))
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            authors = list(app.query(".user-message .message-author").results(Label))
            assert [str(author.content) for author in authors] == [
                "You",
                "Agent · sender-session-full-id",
                "Agent · sender-session-full-id · Steer",
            ]
            assert "/resume sender-session-full-id" in str(authors[1].tooltip)
            assert [
                str(content.content) for content in app.query(".user-message .message-content").results(Static)
            ] == [
                "Original user input",
                "Agent body\n[image: chart.png]",
                "Agent steer",
            ]
            user_input = UserInput("Live Agent steer", origin=ORIGIN)
            seq = await web.host.journal.record_user_steer(session_id, "target-run", user_input)
            await app.handle_follow_up(
                RuntimeFollowUpEvent(
                    "steer_submitted", session_id, "target-run", "target-run", "steer", user_input, seq
                )
            )
            live_authors = [str(author.content) for author in app.query(".user-message .message-author").results(Label)]
            assert live_authors[-1] == "Agent · sender-session-full-id · Steer"
            await app.query_one(ChatTimeline).render_history(
                read_session_journal(web.host.config.session_journal_dir, session_id)
            )
            assert [
                str(author.content) for author in app.query(".user-message .message-author").results(Label)
            ] == live_authors


@pytest.mark.asyncio
async def test_terminal_committed_inputs_are_unique_and_keep_target_session_ownership(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        sender_id = await web.host.sessions.create()
        target_id = await web.host.sessions.create()
        web.host.state.selected_session_id = target_id
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            user_input = UserInput("Committed Agent input", (IMAGE,), InputOrigin("agent", sender_id, "sender-run"))
            seq = await web.host.journal.record_user_message(target_id, user_input)
            await app.handle_user_input_committed(target_id, user_input, seq)
            await app.handle_user_input_committed(target_id, user_input, seq)
            await app.handle_follow_up(
                RuntimeFollowUpEvent(
                    "queue_promoted", target_id, "target-run", "previous-run", "queue", user_input, seq
                )
            )
            authors = list(app.query(".user-message .message-author").results(Label))
            assert [str(author.content) for author in authors] == [f"Agent · {sender_id}"]
            assert "/unarchive" in str(authors[0].tooltip)
            assert app.query_one(".user-message .message-content", Static).content == (
                "Committed Agent input\n[1 images attached]"
            )
            assert web.host.state.selected_session_id == target_id

            other_input = UserInput(
                "Agent input for the other Session", origin=InputOrigin("agent", target_id, "reply-run")
            )
            other_seq = await web.host.journal.record_user_message(sender_id, other_input)
            await app.handle_user_input_committed(sender_id, other_input, other_seq)
            assert len(app.query(".user-message")) == 1
            await web.host.sessions.archive(sender_id)
            await app.on_composer_submitted(Composer.Submitted(f"/resume {sender_id}"))
            assert web.host.state.selected_session_id == target_id, (
                "archived sources require the existing unarchive command"
            )
            await app.on_composer_submitted(Composer.Submitted(f"/unarchive {sender_id}"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert web.host.state.selected_session_id == sender_id
            assert app.query_one(".user-message .message-content", Static).content == other_input.text
            await app.handle_user_input_committed(sender_id, other_input, other_seq)
            assert len(app.query(".user-message")) == 1, (
                "history replay and a committed notification do not duplicate the input"
            )


@pytest.mark.asyncio
async def test_terminal_queue_preview_shows_agent_source_and_the_shared_image_summary(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        session_id = await web.host.sessions.create()
        web.host.state.selected_session_id = session_id
        web.host.runtime.start_run(session_id, "Initial input", run_options("queue"))
        await web.runtime.agent_loop.wait_until_started(session_id)
        await web.host.runtime.submit_follow_up(session_id, "User queued body", run_options())
        await web.host.runtime.submit_follow_up(session_id, UserInput("", (IMAGE,), ORIGIN), run_options())
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#queue-preview-1", Label).content == "1. User queued body"
            preview = app.query_one("#queue-preview-2", Label)
            assert preview.content == "2. Agent · sender-session-full-id · [1 images attached]"
            assert "/resume sender-session-full-id" in str(preview.tooltip)
