from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents.tool import FunctionTool
from agents.tool_context import ToolContext

from investorch.context import AgentContext, ExecutionState
from investorch.tools.base import calculate, delete, edit, explore
from tests.support.config import make_test_config


def make_tool_context(tmp_path: Path, overrides: dict[str, dict[str, Any]] | None = None) -> ToolContext[AgentContext]:
    config = make_test_config(tmp_path, overrides)
    context = AgentContext(
        config=config,
        execution=ExecutionState(workspace_root=config.workspace_dir),
        session_id="session-a",
        run_id="run-a",
    )
    return ToolContext(
        context=context,
        tool_name="behavior-test",
        tool_call_id="call-a",
        tool_arguments="{}",
    )


async def invoke(tool: FunctionTool, context: ToolContext[AgentContext], **arguments: object) -> object:
    return await tool.on_invoke_tool(context, json.dumps(arguments))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expression", "expected"),
    [("2 + 3 * 4", 14), ("sqrt(9)", 3), ("round(pi, 5)", 3.14159)],
)
async def test_calculate_evaluates_supported_math(tmp_path: Path, expression: str, expected: int | float) -> None:
    result = await invoke(calculate, make_tool_context(tmp_path), expression=expression)

    assert isinstance(result, dict)
    assert result["result"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    ['__import__("os")', "(1).real", "[1][0]", "value = 1", "2 ** 10001", "1e309"],
)
async def test_calculate_rejects_unsafe_or_unbounded_expressions(tmp_path: Path, expression: str) -> None:
    result = await invoke(calculate, make_tool_context(tmp_path), expression=expression)

    assert isinstance(result, str)
    assert "Error:" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../escape.txt", "/tmp/escape.txt"])
async def test_workspace_edit_rejects_paths_outside_root(tmp_path: Path, path: str) -> None:
    context = make_tool_context(tmp_path)

    result = await invoke(edit, context, path=path, operation="create", content="forbidden", old_text="")

    assert isinstance(result, str)
    assert "Error:" in result
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.asyncio
async def test_large_full_read_fails_but_bounded_line_read_succeeds(tmp_path: Path) -> None:
    context = make_tool_context(
        tmp_path,
        {"explore": {"max_full_read_bytes": 10, "max_read_chars": 100}},
    )
    note = context.context.config.workspace_dir / "note.txt"
    note.write_text("line one\nline two\nline three\n", encoding="utf-8")

    full = await invoke(explore, context, operation="read", path="note.txt", query="", start_line=0, end_line=0)
    bounded = await invoke(explore, context, operation="read", path="note.txt", query="", start_line=2, end_line=2)

    assert isinstance(full, str)
    assert "Error:" in full
    assert isinstance(bounded, dict)
    assert bounded["content"] == "line two\n"


@pytest.mark.asyncio
async def test_create_does_not_overwrite_and_replace_requires_one_match(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)

    created = await invoke(edit, context, path="note.txt", operation="create", content="same same", old_text="")
    duplicate_create = await invoke(edit, context, path="note.txt", operation="create", content="lost", old_text="")
    ambiguous_replace = await invoke(
        edit, context, path="note.txt", operation="replace", content="new", old_text="same"
    )

    assert isinstance(created, dict)
    assert isinstance(duplicate_create, str)
    assert isinstance(ambiguous_replace, str)
    assert (context.context.config.workspace_dir / "note.txt").read_text(encoding="utf-8") == "same same"


@pytest.mark.asyncio
async def test_delete_rejects_workspace_root_and_nonempty_directory_without_recursive(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    directory = context.context.config.workspace_dir / "notes"
    directory.mkdir()
    (directory / "one.txt").write_text("keep", encoding="utf-8")

    root_result = await invoke(delete, context, path=".", recursive=True)
    directory_result = await invoke(delete, context, path="notes", recursive=False)

    assert isinstance(root_result, str)
    assert isinstance(directory_result, str)
    assert context.context.config.workspace_dir.is_dir()
    assert (directory / "one.txt").is_file()
