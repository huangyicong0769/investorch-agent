from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from agents import RunContextWrapper
from agents.decorators import tool

from qmt_agent.context import AgentContext

MAX_FULL_READ_BYTES = 64 * 1024
MAX_READ_CHARS = 64 * 1024
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_SNIPPET_CHARS = 300

@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """
    Get the current local date and time.

    Args:
        timezone (str): The IANA timezone to use for the current time. Default is "Asia/Shanghai".

    Returns:
        str: The current local date and time as a string.
    """
    return datetime.now(ZoneInfo(timezone)).isoformat()


EditOperation = Literal[
    "create",
    "append",
    "replace",
]


@tool
def explore(
    context: RunContextWrapper[AgentContext],
    path: str = ".",
    query: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """
    Explore the persistent user workspace.

    The path is always relative to the configured workspace root.

    Behavior:
    - If path is a directory, list its immediate entries.
    - If path is a text file, read it.
    - If query is provided, recursively search text content below path instead of listing or reading.

    Large files are not silently truncated. Use start_line/end_line for bounded reads.

    Args:
        path: Workspace-relative file or directory path. Defaults to the workspace root.

        query: Optional text to search for. Search is case-insensitive and recursive when path is a directory.

        start_line: Optional 1-based first line when reading a file.

        end_line: Optional 1-based inclusive last line when reading a file.

    Returns:
        A dictionary describing the workspace entry or search results.
    """
    workspace_root = context.context.config.workspace_dir

    root = Path(workspace_root).expanduser().resolve()
    target = _resolve_workspace_path(root, path)

    if query is not None:
        if start_line is not None or end_line is not None:
            raise ValueError("start_line/end_line cannot be used with query")

        return _search(
            root=root,
            target=target,
            query=query,
        )

    if not target.exists():
        # An empty workspace is valid before the first edit.
        if target == root:
            return {
                "type": "directory",
                "path": ".",
                "entries": [],
            }

        raise ValueError(f"Workspace path does not exist: {path}")

    if target.is_dir():
        if start_line is not None or end_line is not None:
            raise ValueError("start_line/end_line can only be used for files")

        return _list_directory(
            root=root,
            directory=target,
        )

    if target.is_file():
        return _read_text_file(
            root=root,
            path=target,
            start_line=start_line,
            end_line=end_line,
        )

    raise ValueError(f"Unsupported workspace entry: {path}")


@tool(needs_approval=True)
def edit(
    context: RunContextWrapper[AgentContext],
    path: str,
    operation: EditOperation,
    content: str,
    old_text: str | None = None,
) -> dict[str, Any]:
    """
    Edit a UTF-8 text file in the persistent user workspace.

    This operation always requires user approval.

    Supported operations:
    - create: Create a new file. Fails if it already exists.
    - append: Append content to an existing file.
    - replace: Replace one exact old_text occurrence with content. The old_text must exist exactly once.

    Read the existing file with explore before modifying it unless creating a new file.

    Args:
        path: Workspace-relative file path.

        operation: create, append, or replace.

        content: Text to create, append, or use as replacement.

        old_text: Exact existing text to replace. Required only for replace.

    Returns:
        A dictionary describing the edited file, including its size.
    """
    workspace_root = context.context.config.workspace_dir

    root = Path(workspace_root).expanduser().resolve()
    target = _resolve_workspace_path(root, path)

    if target == root:
        raise ValueError("Workspace root itself cannot be edited as a file")

    if operation == "create":
        if target.exists():
            raise ValueError(f"File already exists: {_display_path(root, target)}")

        target.parent.mkdir(parents=True, exist_ok=True)

        target.write_text(content, encoding="utf-8")

    elif operation == "append":
        _require_existing_file(root=root, path=target)

        _validate_utf8_file(target)

        with target.open("a", encoding="utf-8") as file:
            file.write(content)

    elif operation == "replace":
        _require_existing_file(root=root, path=target)

        if old_text is None or old_text == "":
            raise ValueError("old_text is required for replace")

        try:
            existing = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"File is not valid UTF-8 text: {_display_path(root, target)}") from exc

        count = existing.count(old_text)

        if count != 1:
            raise ValueError(f"replace requires old_text to appear exactly once; found {count} matches")

        updated = existing.replace(old_text, content, 1)

        target.write_text(updated, encoding="utf-8")

    else:
        raise ValueError(f"Unsupported edit operation: {operation}")

    return {
        "path": _display_path(root, target),
        "operation": operation,
        "size": target.stat().st_size,
    }


def _resolve_workspace_path(workspace_root: str | Path, path: str) -> Path:
    """
    Resolve a user-provided path inside the workspace.

    Absolute paths and paths that escape the workspace through traversal or symlinks are rejected.
    """
    root = Path(workspace_root).expanduser().resolve()
    relative = Path(path)

    if relative.is_absolute():
        raise ValueError("Workspace paths must be relative")

    resolved = (root / relative).resolve()

    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {path}")

    return resolved


def _list_directory(root: Path, directory: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []

    for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
        try:
            resolved = entry.resolve()
        except OSError:
            entries.append(
                {
                    "name": entry.name,
                    "type": "unavailable",
                }
            )
            continue

        if not resolved.is_relative_to(root):
            entries.append(
                {
                    "name": entry.name,
                    "type": "blocked_symlink",
                }
            )
            continue

        if entry.is_dir():
            entry_type = "directory"
        elif entry.is_file():
            entry_type = "file"
        else:
            entry_type = "other"

        item: dict[str, Any] = {
            "name": entry.name,
            "type": entry_type,
        }

        if entry_type == "file":
            item["size"] = entry.stat().st_size

        entries.append(item)

    return {
        "type": "directory",
        "path": _display_path(root, directory),
        "entries": entries,
    }


def _read_text_file(
    root: Path,
    path: Path,
    *,
    start_line: int | None,
    end_line: int | None,
) -> dict[str, Any]:
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be >= 1")

    if end_line is not None and end_line < 1:
        raise ValueError("end_line must be >= 1")

    effective_start = start_line if start_line is not None else 1

    if end_line is not None and end_line < effective_start:
        raise ValueError("end_line must be >= start_line")

    size = path.stat().st_size

    # Do not silently truncate a large full-file read.
    if size > MAX_FULL_READ_BYTES and end_line is None:
        raise ValueError(f"File is {size} bytes. Specify start_line and end_line to read a bounded range.")

    selected: list[str] = []
    total_lines = 0

    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                total_lines = line_number

                if line_number < effective_start:
                    continue

                if end_line is not None and line_number > end_line:
                    continue

                selected.append(line)

    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {_display_path(root, path)}") from exc

    if total_lines > 0 and effective_start > total_lines:
        raise ValueError(f"start_line {effective_start} exceeds file length {total_lines}")

    content = "".join(selected)

    if len(content) > MAX_READ_CHARS:
        raise ValueError("Requested text range is too large. Use a smaller start_line/end_line range.")

    if total_lines == 0:
        actual_start = 0
        actual_end = 0
    else:
        actual_start = effective_start
        actual_end = min(
            end_line if end_line is not None else total_lines,
            total_lines,
        )

    return {
        "type": "file",
        "path": _display_path(root, path),
        "total_lines": total_lines,
        "start_line": actual_start,
        "end_line": actual_end,
        "content": content,
    }


def _search(root: Path, target: Path, query: str) -> dict[str, Any]:
    if not query:
        raise ValueError("Search query cannot be empty")

    if not target.exists():
        if target == root:
            return {
                "type": "search",
                "path": ".",
                "query": query,
                "results": [],
                "truncated": False,
            }

        raise ValueError(f"Search path does not exist: {_display_path(root, target)}")

    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(
            (path for path in target.rglob("*") if path.is_file()),
            key=lambda path: str(path).casefold(),
        )
    else:
        raise ValueError("Search path must be a file or directory")

    results: list[dict[str, Any]] = []
    folded_query = query.casefold()
    truncated = False

    for path in files:
        resolved = path.resolve()

        # Do not search through a symlink that leaves
        # the workspace.
        if not resolved.is_relative_to(root):
            continue

        try:
            with resolved.open("r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    if folded_query not in line.casefold():
                        continue

                    if len(results) >= MAX_SEARCH_RESULTS:
                        truncated = True
                        break

                    snippet = line.rstrip("\r\n")

                    if len(snippet) > MAX_SEARCH_SNIPPET_CHARS:
                        snippet = (snippet[:MAX_SEARCH_SNIPPET_CHARS] + "...")

                    results.append(
                        {
                            "path": _display_path(root, resolved),
                            "line": line_number,
                            "snippet": snippet,
                        }
                    )

        except UnicodeDecodeError:
            # Workspace may contain binary files.
            # They are simply not text-searchable.
            continue

        if truncated:
            break

    return {
        "type": "search",
        "path": _display_path(root, target),
        "query": query,
        "results": results,
        "truncated": truncated,
    }


def _require_existing_file(root: Path, path: Path) -> None:
    if not path.exists():
        raise ValueError(f"File does not exist: {_display_path(root, path)}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {_display_path(root, path)}")


def _validate_utf8_file(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as file:
            while file.read(64 * 1024):
                pass
    except UnicodeDecodeError as exc:
        raise ValueError("File is not valid UTF-8 text") from exc


def _display_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)

    if str(relative) == ".":
        return "."

    return relative.as_posix()