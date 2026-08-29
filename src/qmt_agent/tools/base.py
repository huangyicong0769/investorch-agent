from __future__ import annotations

import ast
import math
import operator
import shlex
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from agents import RunContextWrapper
from agents.decorators import tool
from agents.sandbox import Manifest
from agents.sandbox.errors import PtySessionNotFoundError
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

from qmt_agent.context import AgentContext, BackgroundJob, ExecutionState

BACKGROUND_PID_MARKER = "__QMT_PID__="

_CALCULATE_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CALCULATE_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_CALCULATE_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_CALCULATE_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "floor": math.floor,
    "ceil": math.ceil,
}


@tool
def get_current_time(context: RunContextWrapper[AgentContext], timezone: str | None = None) -> str:
    """
    Get the current local date and time.

    Args:
        timezone (str): The IANA timezone to use for the current time. Defaults to runtime.default_timezone.

    Returns:
        str: The current local date and time as a string.
    """
    if timezone is None:
        timezone = context.context.config["runtime.default_timezone"]

    return datetime.now(ZoneInfo(timezone)).isoformat()


@tool(needs_approval=True)
async def exec_command(
    context: RunContextWrapper[AgentContext],
    command: str,
    background: bool = False,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """
    Execute a shell command in the persistent user workspace.

    The command runs with the configured workspace as its current directory and filesystem root. Foreground commands wait for completion. Background commands return a process ID and workspace-relative log paths. When background=true, pass the foreground form of the command. Do not append &, nohup, or setsid; the runtime manages backgrounding. This operation always requires user approval.

    Args:
        command: Shell command to execute.

        background: Start the command in the background and return immediately. Defaults to false.

        timeout_seconds: Maximum wall-clock execution time for foreground commands in seconds. It is ignored for background commands. Defaults to execution.default_timeout_seconds.

    Returns:
        Foreground commands return stdout, stderr, and exit_code. Background commands return the PID, running state, and log paths.
    """
    if sys.platform != "darwin":
        raise RuntimeError("exec_command is currently supported on macOS only")

    if not command.strip():
        raise ValueError("command cannot be empty")

    config = context.context.config
    if timeout_seconds is None:
        timeout_seconds = config["execution.default_timeout_seconds"]

    if not background:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1")

        max_timeout_seconds = config["execution.max_timeout_seconds"]
        if timeout_seconds > max_timeout_seconds:
            raise ValueError(f"timeout_seconds cannot exceed {max_timeout_seconds}")

    workspace = context.context.config.workspace_dir.expanduser().resolve()
    execution = context.context.execution
    sandbox = await _ensure_sandbox(execution, workspace)

    if background:
        return await _start_background_command(
            execution=execution,
            sandbox=sandbox,
            workspace=workspace,
            background_job_dir=config.background_job_dir,
            command=command,
            owner_session_id=context.context.session_id,
            owner_run_id=context.context.run_id,
        )

    result = await sandbox.exec(
        command,
        timeout=timeout_seconds,
        shell=True,
    )

    return {
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
        "exit_code": result.exit_code,
    }


ExploreOperation = Literal["list", "read", "search"]

EditOperation = Literal["create", "append", "replace"]


@tool
def calculate(context: RunContextWrapper[AgentContext], expression: str) -> dict[str, Any]:
    """
    Evaluate a mathematical expression safely and deterministically.

    Supported syntax:
    - Arithmetic: +, -, *, /, //, %, **
    - Unary signs: +x, -x
    - Constants: pi, e, tau
    - Functions: abs, round, sqrt, log, log10, exp,
      sin, cos, tan, floor, ceil

    Python statements, variables, attribute access, indexing,
    comprehensions, imports, and arbitrary function calls are not allowed.

    Args:
        expression: Mathematical expression to evaluate. Safety limits come from the calculate config section.

    Returns:
        A dictionary containing the original expression and numeric result.
    """
    expression = expression.strip()

    if not expression:
        raise ValueError("expression cannot be empty")

    config = context.context.config
    max_expression_chars = config["calculate.max_expression_chars"]
    max_nodes = config["calculate.max_nodes"]
    max_integer_bits = config["calculate.max_integer_bits"]
    max_abs_exponent = config["calculate.max_abs_exponent"]

    if len(expression) > max_expression_chars:
        raise ValueError(f"expression exceeds {max_expression_chars} characters")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid mathematical expression: {exc.msg}") from exc

    node_count = sum(1 for _ in ast.walk(tree))

    if node_count > max_nodes:
        raise ValueError(f"expression is too complex; maximum AST nodes is {max_nodes}")

    result = _evaluate_calculation_node(tree.body, max_abs_exponent, max_integer_bits)
    _validate_calculation_number(result, max_integer_bits)

    return {
        "expression": expression,
        "result": result,
    }


@tool
def explore(
    context: RunContextWrapper[AgentContext],
    operation: ExploreOperation,
    path: str = ".",
    query: str = "",
    start_line: int = 0,
    end_line: int = 0,
) -> dict[str, Any]:
    """
    Explore the persistent user workspace.

    The path is always relative to the configured workspace root.

    Operations:
    - list: List the immediate entries of a directory.
    - read: Read a UTF-8 text file.
    - search: Search text content recursively below a path.

    Large files are not silently truncated. Use start_line/end_line for bounded reads. Read and search limits come from the explore config section.

    Args:
        operation: list, read, or search.

        path: Workspace-relative file or directory path. Defaults to the workspace root.

        query: Text to search for when operation is "search". Leave empty for other operations.

        start_line: 1-based first line for "read". use 0 to start from the beginning. Defaults to 0.

        end_line: 1-based inclusive last line for "read". use 0 to read until the end of the file. Defaults to 0.

    Returns:
        A dictionary describing the workspace entry or search results.
    """
    config = context.context.config
    workspace_root = config.workspace_dir

    root = Path(workspace_root).expanduser().resolve()
    target = _resolve_workspace_path(root, path)

    if operation == "list":
        if not target.exists():
            if target == root:
                return {
                    "type": "directory",
                    "path": ".",
                    "entries": [],
                }

            raise ValueError(f"Workspace path does not exist: {path}")

        if not target.is_dir():
            raise ValueError( f"Path is not a directory: {path}")

        return _list_directory(
            root=root,
            directory=target,
        )

    if operation == "read":
        if not target.exists():
            raise ValueError(f"Workspace path does not exist: {path}")

        if not target.is_file():
            raise ValueError(f"Path is not a file: {path}")

        return _read_text_file(
            root=root,
            path=target,
            start_line=(start_line if start_line > 0 else None),
            end_line=(end_line if end_line > 0 else None),
            max_full_read_bytes=config["explore.max_full_read_bytes"],
            max_read_chars=config["explore.max_read_chars"],
        )

    if operation == "search":
        if not query.strip():
            raise ValueError("query is required for search")

        return _search(
            root=root,
            target=target,
            query=query,
            max_results=config["explore.max_search_results"],
            max_snippet_chars=config["explore.max_search_snippet_chars"],
        )

    raise ValueError(f"Unsupported explore operation: {operation}")


@tool(needs_approval=True)
def edit(
    context: RunContextWrapper[AgentContext],
    path: str,
    operation: EditOperation,
    content: str,
    old_text: str = "",
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

        old_text: Exact existing text to replace. Required only for replace. Leave empty for create and append.

    Returns:
        A dictionary describing the edited file, including its size.
    """
    config = context.context.config
    workspace_root = config.workspace_dir

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

        if not old_text:
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


@tool(needs_approval=True)
def delete(
    context: RunContextWrapper[AgentContext],
    path: str,
    recursive: bool = False,
) -> dict[str, Any]:
    """
    Delete a file or directory from the persistent user workspace.

    This operation always requires user approval.

    Behavior:
    - Files are deleted directly.
    - Empty directories are deleted directly.
    - Non-empty directories require recursive=true.
    - The workspace root itself can never be deleted.
    - Symbolic links are not supported.

    Args:
        path: Workspace-relative file or directory path.

        recursive: If true, allow deletion of a non-empty directory. Has no effect when deleting a file.

    Returns:
        A dictionary describing the deleted workspace entry.
    """
    root = context.context.config.workspace_dir.expanduser().resolve()

    relative = Path(path)

    if relative.is_absolute():
        raise ValueError("Workspace paths must be relative")

    lexical_target = root / relative

    # Do not follow a final symlink during deletion.
    if lexical_target.is_symlink():
        raise ValueError("Deleting symbolic links is not supported")

    resolved = _resolve_workspace_path(root, path)

    if resolved == root:
        raise ValueError("Workspace root cannot be deleted")

    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {path}")

    if not resolved.exists():
        raise ValueError(f"Workspace path does not exist: {path}")

    display_path = _display_path(root, resolved,)

    if resolved.is_file():
        resolved.unlink()

        return {
            "path": display_path,
            "type": "file",
            "deleted": True,
        }

    if resolved.is_dir():
        if recursive:
            shutil.rmtree(resolved)
        else:
            try:
                resolved.rmdir()
            except OSError as exc:
                raise ValueError("Directory is not empty. Set recursive=true to delete it and all contents.") from exc

        return {
            "path": display_path,
            "type": "directory",
            "recursive": recursive,
            "deleted": True,
        }

    raise ValueError(f"Unsupported workspace entry: {path}")


def _evaluate_calculation_node(node: ast.AST, max_abs_exponent: int, max_integer_bits: int) -> int | float:
    if isinstance(node, ast.Constant):
        value = node.value

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Only integer and floating-point literals are allowed")

        _validate_calculation_number(value, max_integer_bits)
        return value

    if isinstance(node, ast.BinOp):
        operator_function = _CALCULATE_BINARY_OPERATORS.get(type(node.op))

        if operator_function is None:
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")

        left = _evaluate_calculation_node(node.left, max_abs_exponent, max_integer_bits)
        right = _evaluate_calculation_node(node.right, max_abs_exponent, max_integer_bits)

        if isinstance(node.op, ast.Pow) and abs(right) > max_abs_exponent:
            raise ValueError(f"Exponent is too large; maximum absolute exponent is {max_abs_exponent}")

        try:
            result = operator_function(left, right)
        except (ArithmeticError, ValueError, OverflowError) as exc:
            raise ValueError(f"Calculation failed: {exc}") from exc

        _validate_calculation_number(result, max_integer_bits)
        return result

    if isinstance(node, ast.UnaryOp):
        operator_function = _CALCULATE_UNARY_OPERATORS.get(type(node.op))

        if operator_function is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

        operand = _evaluate_calculation_node(node.operand, max_abs_exponent, max_integer_bits)
        result = operator_function(operand)

        _validate_calculation_number(result, max_integer_bits)
        return result

    if isinstance(node, ast.Name):
        if node.id not in _CALCULATE_CONSTANTS:
            raise ValueError(
                f"Unknown constant: {node.id}"
            )

        return _CALCULATE_CONSTANTS[node.id]

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise TypeError("Only direct calls to supported functions are allowed")

        function = _CALCULATE_FUNCTIONS.get(node.func.id)

        if function is None:
            raise ValueError(f"Unsupported function: {node.func.id}")

        if node.keywords:
            raise ValueError("Keyword arguments are not supported")

        arguments = [_evaluate_calculation_node(argument, max_abs_exponent, max_integer_bits) for argument in node.args]

        try:
            result = function(*arguments)
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise ValueError(f"{node.func.id} failed: {exc}") from exc

        _validate_calculation_number(result, max_integer_bits)
        return result

    raise ValueError(f"Unsupported expression syntax: {type(node).__name__}")


def _validate_calculation_number(value: int | float, max_integer_bits: int) -> None:
    if isinstance(value, bool):
        raise TypeError("Boolean values are not supported")

    if isinstance(value, int):
        if value.bit_length() > max_integer_bits:
            raise ValueError(f"Integer result is too large; maximum bit length is {max_integer_bits}")

        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Calculation produced a non-finite result")

        return

    raise ValueError(f"Calculation produced unsupported type: {type(value).__name__}")


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
    max_full_read_bytes: int,
    max_read_chars: int,
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
    if size > max_full_read_bytes and end_line is None:
        raise ValueError(f"File is {size} bytes. Specify start_line and end_line to read a bounded range.")

    selected: list[str] = []
    selected_chars = 0
    total_lines = 0

    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                total_lines = line_number

                if line_number < effective_start:
                    continue

                if end_line is not None and line_number > end_line:
                    continue

                selected_chars += len(line)

                if selected_chars > max_read_chars:
                    raise ValueError("Requested text range is too large. Use a smaller start_line/end_line range.")

                selected.append(line)

    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {_display_path(root, path)}") from exc

    if total_lines > 0 and effective_start > total_lines:
        raise ValueError(f"start_line {effective_start} exceeds file length {total_lines}")

    content = "".join(selected)

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


def _search(root: Path, target: Path, query: str, *, max_results: int, max_snippet_chars: int) -> dict[str, Any]:
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

                    if len(results) >= max_results:
                        truncated = True
                        break

                    snippet = line.rstrip("\r\n")

                    if len(snippet) > max_snippet_chars:
                        snippet = (snippet[:max_snippet_chars] + "...")

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


async def start_execution(execution: ExecutionState, workspace: Path) -> None:
    await _ensure_sandbox(execution, workspace.expanduser().resolve())


async def close_execution(execution: ExecutionState) -> None:
    sandbox = execution.sandbox
    execution.sandbox = None
    execution.workspace_root = None

    if sandbox is not None:
        await sandbox.aclose()


async def list_background_jobs(execution: ExecutionState) -> list[BackgroundJob]:
    sandbox = execution.sandbox

    if sandbox is None:
        return _sorted_background_jobs(execution)

    for job in execution.background_jobs.values():
        if job.status != "running":
            continue

        if job.process_id is None:
            job.status = "lost"
            job.finished_at = datetime.now(UTC)
            continue

        try:
            update = await sandbox.pty_write_stdin(
                session_id=job.process_id,
                chars="",
                yield_time_s=0,
            )
        except PtySessionNotFoundError:
            job.process_id = None
            job.status = "lost"
            job.finished_at = datetime.now(UTC)
            continue

        if update.exit_code is not None:
            job.exit_code = update.exit_code
            job.finished_at = datetime.now(UTC)
            job.process_id = None
            job.status = "exited"

    return _sorted_background_jobs(execution)


def format_background_jobs(jobs: list[BackgroundJob]) -> str:
    if not jobs:
        return "QMT background processes:\nNo background processes."

    lines = [
        "QMT background processes:",
        "PID\tSTATUS\tSESSION\tELAPSED\tCOMMAND",
    ]

    for job in jobs:
        if job.status == "running":
            status = "running"
        elif job.status == "lost":
            status = "lost"
        else:
            status = f"exited({job.exit_code})"

        command = job.command.replace("\n", "\\n")
        lines.append(
            f"{job.pid}\t{status}\t{job.owner_session_id[:8]}\t{_format_elapsed(job)}\t{command}"
        )

    return "\n".join(lines)


def _sorted_background_jobs(execution: ExecutionState) -> list[BackgroundJob]:
    return sorted(execution.background_jobs.values(), key=lambda job: job.started_at)


def _format_elapsed(job: BackgroundJob) -> str:
    end = job.finished_at or datetime.now(UTC)
    elapsed_seconds = max(0, int((end - job.started_at).total_seconds()))
    hours, remainder = divmod(elapsed_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


async def _ensure_sandbox(execution: ExecutionState, workspace: Path) -> Any:
    if sys.platform != "darwin":
        raise RuntimeError("exec_command is currently supported on macOS only")

    if execution.sandbox is not None:
        if execution.workspace_root != workspace:
            raise RuntimeError("ExecutionState is already bound to another workspace")

        return execution.sandbox

    client = UnixLocalSandboxClient()
    sandbox = await client.create(
        manifest=Manifest(root=str(workspace)),
    )
    await sandbox.start()
    execution.workspace_root = workspace
    execution.sandbox = sandbox

    return sandbox


async def _start_background_command(
    execution: ExecutionState,
    sandbox: Any,
    workspace: Path,
    background_job_dir: Path,
    command: str,
    owner_session_id: str,
    owner_run_id: str,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job_directory = background_job_dir / job_id
    stdout_log = job_directory / "stdout.log"
    stderr_log = job_directory / "stderr.log"

    wrapped_command = (
        f"mkdir -p {shlex.quote(str(job_directory))} && "
        f"printf '%s%s\\n' {shlex.quote(BACKGROUND_PID_MARKER)} \"$$\" && "
        "trap 'trap - TERM INT; kill -TERM -- -$$' TERM INT; "
        f"/bin/sh -c {shlex.quote(command)} "
        f"> {shlex.quote(str(stdout_log))} 2> {shlex.quote(str(stderr_log))} & "
        "child=$!; wait \"$child\"; exit \"$?\""
    )

    update = await sandbox.pty_exec_start(
        wrapped_command,
        shell=True,
        tty=False,
        yield_time_s=1,
    )

    pid = _parse_background_pid(update.output)

    if update.process_id is None and update.exit_code is None:
        raise RuntimeError("background command did not return a managed process")

    now = datetime.now(UTC)
    job = BackgroundJob(
        job_id=job_id,
        owner_session_id=owner_session_id,
        owner_run_id=owner_run_id,
        process_id=update.process_id,
        pid=pid,
        command=command,
        started_at=now,
        stdout_log=_display_path(workspace, stdout_log),
        stderr_log=_display_path(workspace, stderr_log),
        status=("running" if update.exit_code is None else "exited"),
        exit_code=update.exit_code,
        finished_at=(now if update.exit_code is not None else None),
    )
    execution.background_jobs[job_id] = job

    result = {
        "background": True,
        "pid": pid,
        "running": update.exit_code is None,
        "stdout_log": job.stdout_log,
        "stderr_log": job.stderr_log,
    }

    if update.exit_code is not None:
        result["exit_code"] = update.exit_code

    return result


def _parse_background_pid(output: bytes) -> int:
    text = output.decode("utf-8", errors="replace")

    for line in text.splitlines():
        if not line.startswith(BACKGROUND_PID_MARKER):
            continue

        value = line[len(BACKGROUND_PID_MARKER):].strip()

        try:
            pid = int(value)
        except ValueError as exc:
            raise RuntimeError("background command returned an invalid process ID") from exc

        if pid > 0:
            return pid

        raise RuntimeError("background command returned an invalid process ID")

    raise RuntimeError("background command did not return a process ID")


def _display_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)

    if str(relative) == ".":
        return "."

    return relative.as_posix()
