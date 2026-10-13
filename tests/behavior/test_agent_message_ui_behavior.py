from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Label, Static

from investorch.images import ImageContent, InputOrigin, UserInput
from investorch.journal import read_session_journal
from investorch.runtime import RuntimeFollowUpEvent
from investorch.ui.tui.app import InvestOrchAgentTUI
from investorch.ui.tui.timeline import ChatTimeline
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
