import sqlite3
from pathlib import Path

import pytest
from textual.widgets import Static

from investorch.storage import create_spawn_session, list_sessions
from investorch.ui.tui.app import InvestOrchAgentTUI
from investorch.ui.tui.timeline import ChatTimeline
from tests.support.runtime import run_options
from tests.support.web import open_test_web


async def prepare_tree(host):
    root = await host.sessions.create()
    await host.sessions.set_title(root, "Parent research")
    for child, parent, title in (
        ("child", root, "Child research"),
        ("grandchild", "child", "Nested research"),
        ("sibling", root, "Sibling research"),
    ):
        create_spawn_session(
            host.config.sessions_db,
            child,
            parent_session_id=parent,
            parent_run_id="run",
            tool_call_id=child,
            title=title,
        )
    ids = (root, "child", "grandchild", "sibling")
    for member in ids:
        await host.journal.record_user_message(member, "Original history")
    host.state.selected_session_id = root
    return root, ids


@pytest.mark.parametrize("action", ["cancel", "escape", "confirm"])
async def test_tui_clear_shows_recursive_scope_and_waits_for_an_explicit_action(tmp_path, action):
    async with open_test_web(tmp_path) as web:
        root, ids = await prepare_tree(web.host)
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test(size=(100, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#composer-input")
            await pilot.press(*"/clear", "ctrl+s")
            await pilot.pause()
            scope = str(app.screen.query_one("#clear-scope-members", Static).content)
            assert all(member in scope for member in ids)
            assert "Nested research" in scope
            if action == "escape":
                await pilot.press("escape")
            else:
                await pilot.click(f"#clear-{action}")
            await app.workers.wait_for_complete()
            await pilot.pause()
            records = list_sessions(web.host.config.sessions_db)
            if action == "confirm":
                assert [record.session_id for record in records] == [web.host.state.selected_session_id]
                assert records[0].spawned_by_session_id is None
            else:
                assert {record.session_id for record in records} == set(ids)
                assert web.host.state.selected_session_id == root
                for member in ids:
                    assert await web.host.journal.session_exists(member)


@pytest.mark.parametrize("stage", ["replacement", "cleanup"])
async def test_tui_partial_clear_shows_real_failure_and_disables_the_deleted_conversation(tmp_path, monkeypatch, stage):
    async with open_test_web(tmp_path) as web:
        _root, ids = await prepare_tree(web.host)
        original_create = web.host.sessions.create
        original_unlink = Path.unlink

        async def failed_create():
            raise sqlite3.OperationalError("controlled replacement failure")

        def failed_cleanup(path, *args, **kwargs):
            if path.name == "child.jsonl":
                raise OSError("controlled cleanup failure")
            return original_unlink(path, *args, **kwargs)

        if stage == "replacement":
            monkeypatch.setattr(web.host.sessions, "create", failed_create)
        else:
            monkeypatch.setattr(Path, "unlink", failed_cleanup)
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test(size=(100, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#composer-input")
            await pilot.press(*"/clear", "ctrl+s")
            await pilot.pause()
            await pilot.click("#clear-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert web.host.state.selected_session_id is None
            assert str(app.query_one("#session-heading", Static).content) == "No Session selected"
            assert app.query_one("#composer-input").disabled
            visible = "\n".join(str(widget.content) for widget in app.query_one(ChatTimeline).query(Static))
            assert all(member in visible for member in ids) and "Original history" not in visible
            await pilot.press(*"blocked", "ctrl+s")
            assert list_sessions(web.host.config.sessions_db) == []
            monkeypatch.setattr(web.host.sessions, "create", original_create)
            monkeypatch.setattr(Path, "unlink", original_unlink)
            if stage == "cleanup":
                await web.host.sessions.cleanup_deleted_sessions(ids)
            await pilot.press("ctrl+n")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert web.host.state.selected_session_id and not app.query_one("#composer-input").disabled


@pytest.mark.parametrize("blocker", ["fork", "active"])
async def test_tui_clear_block_keeps_history_and_accepts_the_next_command(tmp_path, blocker):
    async with open_test_web(tmp_path) as web:
        root, ids = await prepare_tree(web.host)
        fork = await web.host.sessions.fork("child") if blocker == "fork" else None
        if blocker == "active":
            web.host.runtime.start_run("child", "Keep running", run_options())
            await web.runtime.agent_loop.wait_until_started("child")
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test(size=(100, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#composer-input")
            await pilot.press(*"/clear", "ctrl+s")
            await app.workers.wait_for_complete()
            await pilot.click("#composer-input")
            await pilot.press(*"/session", "ctrl+s")
            await pilot.pause()
            visible = "\n".join(str(widget.content) for widget in app.query_one(ChatTimeline).query(Static))
            assert ("Fork" if fork else "Stop its Run") in visible and f"Current session ID: {root}" in visible
            assert web.host.state.selected_session_id == root
            assert {record.session_id for record in list_sessions(web.host.config.sessions_db)} == set(ids) | (
                {fork} if fork else set()
            )
            for member in ids:
                assert await web.host.journal.session_exists(member)
            if blocker == "active":
                assert web.host.runtime.session_snapshot("child").run_phase == "running"
