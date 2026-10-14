import asyncio
from pathlib import Path

import pytest

from investorch.commands import Command, dispatch_command
from investorch.storage import create_spawn_session, list_sessions
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


@pytest.mark.parametrize("change", ["descendant", "selection"])
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
        assert "changed" in result.output
        assert set(ids) <= {record.session_id for record in list_sessions(web.host.config.sessions_db)}
        assert (web.host.state.selected_session_id == root) == (change == "descendant")


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
