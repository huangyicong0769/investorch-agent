import asyncio
import sqlite3
from pathlib import Path

import pytest
from textual.widgets import Static

from investorch.commands import Command, dispatch_command
from investorch.storage import create_spawn_session, list_sessions
from investorch.ui.console import ConsoleUI
from investorch.ui.tui.app import Composer, InvestOrchAgentTUI
from investorch.ui.tui.timeline import ChatTimeline
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


@pytest.mark.parametrize("fork_blocked", [False, True])
async def test_clear_without_confirmation_is_corrective_and_preserves_the_tree(tmp_path: Path, fork_blocked):
    async with open_test_web(tmp_path) as web:
        root, ids = await prepare_tree(web.host)
        fork = await web.host.sessions.fork("child") if fork_blocked else None
        result = await dispatch_command(
            Command("clear", ()), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
        )
        assert result.output and ("Fork" if fork else "confirm") in result.output
        assert {record.session_id for record in list_sessions(web.host.config.sessions_db)} == set(ids) | (
            {fork} if fork else set()
        )
        assert web.host.state.selected_session_id == root
        for member in ids:
            assert await web.host.journal.session_exists(member)
        continued = await dispatch_command(
            Command("session", ()), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
        )
        assert root in continued.output


@pytest.mark.parametrize("approved", [False, True])
async def test_command_uses_the_entire_preview_and_changes_selection_only_after_confirmation(tmp_path, approved):
    async with open_test_web(tmp_path) as web:
        root, ids = await prepare_tree(web.host)

        async def confirm(preview):
            assert {member["session_id"] for member in preview["sessions"]} == set(ids)
            assert {member["title"] for member in preview["sessions"]} == {
                "Parent research",
                "Child research",
                "Nested research",
                "Sibling research",
            }
            return approved

        result = await dispatch_command(
            Command("clear", ()),
            web.host.state,
            runtime=web.host.runtime,
            sessions=web.host.sessions,
            confirm_clear_scope=confirm,
        )
        records = list_sessions(web.host.config.sessions_db)
        if approved:
            assert [record.session_id for record in records] == [web.host.state.selected_session_id]
            assert records[0].spawned_by_session_id is None
            for member in ids:
                assert not await web.host.journal.session_exists(member)
        else:
            assert "cancelled" in result.output
            assert {record.session_id for record in records} == set(ids)
            assert web.host.state.selected_session_id == root


@pytest.mark.parametrize("change", ["descendant", "selection", "fork"])
async def test_confirmation_wait_allows_other_operations_and_refuses_changed_scope_or_selection(tmp_path, change):
    async with open_test_web(tmp_path) as web:
        root, ids = await prepare_tree(web.host)

        async def confirm(_preview):
            await asyncio.wait_for(web.host.sessions.set_title(root, "Updated during confirmation"), 2)
            if change == "descendant":
                create_spawn_session(
                    web.host.config.sessions_db,
                    "later",
                    parent_session_id=root,
                    parent_run_id="run",
                    tool_call_id="later",
                )
            elif change == "fork":
                await web.host.sessions.fork("child")
            else:
                await dispatch_command(
                    Command("new", ()), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
                )
            return True

        result = await dispatch_command(
            Command("clear", ()),
            web.host.state,
            runtime=web.host.runtime,
            sessions=web.host.sessions,
            confirm_clear_scope=confirm,
        )
        assert ("Fork" if change == "fork" else "changed") in result.output
        assert set(ids) <= {record.session_id for record in list_sessions(web.host.config.sessions_db)}
        assert (web.host.state.selected_session_id == root) == (change != "selection")


async def test_clear_rejects_arguments_and_keeps_the_single_session_command_compatible(tmp_path):
    async with open_test_web(tmp_path) as web:
        root = await web.host.sessions.create()
        web.host.state.selected_session_id = root
        invalid = await dispatch_command(
            Command("clear", ("unexpected",)), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
        )
        assert invalid.output == "Usage: /clear"
        assert [record.session_id for record in list_sessions(web.host.config.sessions_db)] == [root]
        result = await dispatch_command(
            Command("clear", ()), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
        )
        assert web.host.state.selected_session_id != root
        assert [record.session_id for record in list_sessions(web.host.config.sessions_db)] == [
            web.host.state.selected_session_id
        ]
        assert web.host.state.selected_session_id in result.output


@pytest.mark.parametrize("answer", ["DELETE", "no", None])
async def test_plain_confirmation_displays_the_complete_scope_and_treats_eof_as_cancellation(
    tmp_path, monkeypatch, capsys, answer
):
    async with open_test_web(tmp_path) as web:
        root, ids = await prepare_tree(web.host)

        def input_answer(_prompt):
            if answer is None:
                raise EOFError
            return answer

        monkeypatch.setattr("builtins.input", input_answer)
        ui = ConsoleUI()
        result = await dispatch_command(
            Command("clear", ()),
            web.host.state,
            runtime=web.host.runtime,
            sessions=web.host.sessions,
            confirm_clear_scope=ui.confirm_clear_scope,
        )
        visible = capsys.readouterr().out
        assert "4 Sessions" in visible and "cannot be undone" in visible
        for member in ids:
            assert member in visible
        for title in ("Parent research", "Child research", "Nested research", "Sibling research"):
            assert title in visible
        remaining = {record.session_id for record in list_sessions(web.host.config.sessions_db)}
        if answer == "DELETE":
            assert remaining == {web.host.state.selected_session_id}
            assert not remaining.intersection(ids)
        else:
            assert remaining == set(ids)
            assert web.host.state.selected_session_id == root and "cancelled" in result.output


@pytest.mark.parametrize("action", ["cancel", "escape", "confirm", "switch"])
async def test_tui_clear_shows_recursive_scope_and_waits_for_an_explicit_action(tmp_path, action):
    async with open_test_web(tmp_path) as web:
        root, ids = await prepare_tree(web.host)
        app = InvestOrchAgentTUI(web.host.state, web.host.config.session_journal_dir, web.host.journal)
        app.bind_runtime(web.host.runtime, web.host.sessions)
        async with app.run_test(size=(100, 36)) as pilot:
            await app.workers.wait_for_complete()
            await app.on_composer_submitted(Composer.Submitted("/clear"))
            await app.on_composer_submitted(Composer.Submitted("/clear"))
            await pilot.pause()
            scope = str(app.screen.query_one("#clear-scope-members", Static).content)
            assert all(member in scope for member in ids)
            assert "Nested research" in scope
            if action == "escape":
                await pilot.press("escape")
            else:
                if action == "switch":
                    await app.on_composer_submitted(Composer.Submitted("/new"))
                await pilot.click(
                    f"#clear-{'confirm' if action == 'switch' else action}", times=2 if action == "confirm" else 1
                )
            await app.workers.wait_for_complete()
            await pilot.pause()
            records = list_sessions(web.host.config.sessions_db)
            if action == "confirm":
                assert [record.session_id for record in records] == [web.host.state.selected_session_id]
                assert records[0].spawned_by_session_id is None
            elif action == "switch":
                assert {record.session_id for record in records} == set(ids) | {web.host.state.selected_session_id}
                assert web.host.state.selected_session_id != root
            else:
                assert {record.session_id for record in records} == set(ids)
                assert web.host.state.selected_session_id == root
                for member in ids:
                    assert await web.host.journal.session_exists(member)


@pytest.mark.parametrize("stage", ["replacement", "cleanup"])
async def test_clear_partial_success_leaves_no_deleted_selection_and_can_recover_with_new(tmp_path, monkeypatch, stage):
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

        async def approve(_preview):
            return True

        result = await dispatch_command(
            Command("clear", ()),
            web.host.state,
            runtime=web.host.runtime,
            sessions=web.host.sessions,
            confirm_clear_scope=approve,
        )
        assert web.host.state.selected_session_id is None and list_sessions(web.host.config.sessions_db) == []
        assert all(member in result.output for member in ids)
        assert ("creating" if stage == "replacement" else "cleanup") in result.output
        missing = await dispatch_command(
            Command("session", ()), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions
        )
        assert "/new" in missing.output
        monkeypatch.setattr(web.host.sessions, "create", original_create)
        monkeypatch.setattr(Path, "unlink", original_unlink)
        if stage == "cleanup":
            await web.host.sessions.cleanup_deleted_sessions(ids)
        await dispatch_command(Command("new", ()), web.host.state, runtime=web.host.runtime, sessions=web.host.sessions)
        assert [record.session_id for record in list_sessions(web.host.config.sessions_db)] == [
            web.host.state.selected_session_id
        ]


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
            await app.on_composer_submitted(Composer.Submitted("/clear"))
            await pilot.pause()
            await pilot.click("#clear-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert web.host.state.selected_session_id is None
            assert str(app.query_one("#session-heading", Static).content) == "No Session selected"
            assert app.query_one("#composer-input").disabled
            visible = "\n".join(str(widget.content) for widget in app.query_one(ChatTimeline).query(Static))
            assert all(member in visible for member in ids) and "Original history" not in visible
            await app.on_composer_submitted(Composer.Submitted("Do not resurrect a deleted Session"))
            assert list_sessions(web.host.config.sessions_db) == []
            monkeypatch.setattr(web.host.sessions, "create", original_create)
            monkeypatch.setattr(Path, "unlink", original_unlink)
            if stage == "cleanup":
                await web.host.sessions.cleanup_deleted_sessions(ids)
            await pilot.press("ctrl+n")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert web.host.state.selected_session_id and not app.query_one("#composer-input").disabled
