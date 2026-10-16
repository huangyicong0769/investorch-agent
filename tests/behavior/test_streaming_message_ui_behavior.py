from pathlib import Path

import pytest
from textual.widgets import Markdown

from investorch.journal import read_session_journal
from investorch.output import AssistantMessage, AssistantMessageUpdate, ToolCalled, ToolOutput
from investorch.ui.tui.app import InvestOrchAgentTUI
from investorch.ui.tui.timeline import ActivityGroup, ChatTimeline
from tests.support.web import open_test_web


@pytest.mark.asyncio
async def test_terminal_streams_body_before_tools_and_finalizes_without_duplicate_messages(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        session_id = await web.host.sessions.create()
        web.host.state.selected_session_id = session_id
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test() as pilot:
            await pilot.pause()

            async def emit(event, *, persist=True):
                seq = await web.host.journal.record_output(session_id, event) if persist else None
                await app.handle_output(event, session_id=session_id, run_id="run", journal_seq=seq)
                await pilot.pause()

            await emit(AssistantMessageUpdate(message_id="intro", text="I will inspect"), persist=False)
            first = app.query_one(".assistant-markdown", Markdown)
            assert first.source == "I will inspect"
            await emit(AssistantMessageUpdate(message_id="intro", text="I will inspect the portfolio."), persist=False)
            assert app.query_one(".assistant-markdown", Markdown) is first
            assert first.source == "I will inspect the portfolio."
            await emit(AssistantMessage(message_id="intro", text="I will inspect the portfolio."))
            await emit(ToolCalled(name="portfolio_list", arguments="{}"))
            await emit(ToolOutput(output="No portfolios"))
            await emit(AssistantMessageUpdate(message_id="answer", text="No portfolios"), persist=False)
            assert [message.source for message in app.query(Markdown)] == [
                "I will inspect the portfolio.",
                "No portfolios",
            ]
            timeline = app.query_one(ChatTimeline)
            visible = list(timeline.query(".assistant-markdown, .activity-group"))
            assert visible[0] is first
            assert isinstance(visible[1], ActivityGroup)
            assert isinstance(visible[2], Markdown)
            await emit(AssistantMessage(message_id="answer", text="No portfolios found."))
            assert [message.source for message in app.query(Markdown)] == [
                "I will inspect the portfolio.",
                "No portfolios found.",
            ]

            # A history refresh may already contain a completed message when buffered updates arrive.
            await timeline.render_history(read_session_journal(web.host.config.session_journal_dir, session_id))
            await emit(AssistantMessageUpdate(message_id="answer", text="No portfolios"), persist=False)
            assert [message.source for message in app.query(Markdown)] == [
                "I will inspect the portfolio.",
                "No portfolios found.",
            ]


@pytest.mark.asyncio
async def test_terminal_stream_updates_stay_in_their_session_and_reset_with_history(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        session_id = await web.host.sessions.create()
        other_id = await web.host.sessions.create()
        web.host.state.selected_session_id = session_id
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_output(
                AssistantMessageUpdate(message_id="other", text="Other session body"),
                session_id=other_id,
                run_id="other-run",
                journal_seq=None,
            )
            assert not list(app.query(Markdown))
            await app.handle_output(
                AssistantMessageUpdate(message_id="partial", text="Received before stop"),
                session_id=session_id,
                run_id="run",
                journal_seq=None,
            )
            partial = AssistantMessage(message_id="partial", text="Received before stop")
            seq = await web.host.journal.record_output(session_id, partial)
            await app.handle_output(partial, session_id=session_id, run_id="run", journal_seq=seq)
            timeline = app.query_one(ChatTimeline)
            await timeline.render_history(read_session_journal(web.host.config.session_journal_dir, session_id))
            assert [message.source for message in app.query(Markdown)] == ["Received before stop"]
            await timeline.render_history([])
            await timeline.handle_output(AssistantMessageUpdate(message_id="partial", text="Fresh timeline"))
            assert [message.source for message in app.query(Markdown)] == ["Fresh timeline"]
