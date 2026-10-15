"""Qualify macOS command execution with native tools and synthetic temporary state.

Run with `uv run --locked python scripts/qualify_execution_sandbox.py` outside an
enclosing sandbox that prevents macOS sandbox-exec from starting.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
import tempfile
import uuid
from pathlib import Path

from agents.tool_context import ToolContext

from investorch.application import PortfolioOperations
from investorch.config import load_config
from investorch.context import AgentContext, ExecutionState
from investorch.tools.base import close_execution, exec_command


async def qualify() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Native command sandbox qualification requires macOS")

    with tempfile.TemporaryDirectory(prefix="investorch-execution-qualification-") as directory:
        root = Path(directory).resolve()
        config = load_config(root=root / "app")
        workspace = config.workspace_dir
        workspace.mkdir(parents=True)
        sample = "你好, UTF-8\n"
        (workspace / "sample.txt").write_text(sample, encoding="utf-8")
        private_file = root / "private-canary.txt"
        private_file.write_text("synthetic private data", encoding="utf-8")
        (workspace / "outside-link").symlink_to(private_file)
        execution = ExecutionState()
        context = ToolContext(
            context=AgentContext(
                config=config,
                execution=execution,
                session_id="qualification",
                run_id="qualification",
                portfolios=PortfolioOperations(config=config),
            ),
            tool_name="exec_command",
            tool_call_id="qualification",
            tool_arguments="{}",
        )

        def python_command(code: str) -> str:
            # -S excludes installed tzdata fallback so this checks the macOS data.
            return f"PYTHONTZPATH=/usr/share/zoneinfo {shlex.quote(sys.executable)} -S -c {shlex.quote(code)}"

        checks: dict[str, bool] = {}

        async def check(name: str, command: str, expected: str) -> None:
            result = await exec_command.on_invoke_tool(context, json.dumps({"command": command}))
            passed = (
                isinstance(result, dict)
                and result["exit_code"] == 0
                and result["stdout"] == expected
                and not result["stderr"]
            )
            checks[name] = passed
            print(json.dumps({"check": name, "passed": passed, "result": result}, ensure_ascii=False), flush=True)

        try:
            await check("file_utf8", "/usr/bin/file --mime-encoding sample.txt", "sample.txt: utf-8\n")
            await check(
                "iconv_utf8_roundtrip",
                "/usr/bin/iconv -f UTF-8 -t UTF-16LE sample.txt > sample.utf16 && "
                "/usr/bin/iconv -f UTF-16LE -t UTF-8 sample.utf16",
                sample,
            )
            await check(
                "zoneinfo_winter_and_summer",
                python_command(
                    "from datetime import datetime; from zoneinfo import ZoneInfo; "
                    "tz = ZoneInfo('America/New_York'); "
                    "print([int(datetime(2026, month, 1, tzinfo=tz).utcoffset().total_seconds()) "
                    "for month in (1, 7)])"
                ),
                "[-18000, -14400]\n",
            )

            # These canaries live outside the workspace, including a symlink escape.
            # O_WRONLY checks access without truncating or changing the canary.
            denied_reads = [str(private_file), str(workspace / "outside-link")]
            denied_writes = [*denied_reads, str(root / "outside-created.txt")]
            # Use new names: even an unexpectedly permissive sandbox cannot overwrite
            # existing system data. Clean up a created probe before failing.
            probe_name = f".investorch-qualification-{uuid.uuid4().hex}"
            resources = ("/usr/share/file", "/usr/share/i18n", "/usr/share/zoneinfo")
            denied_writes.extend(str(Path(path) / probe_name) for path in resources)
            denied_writes.extend(str(Path(path).resolve() / probe_name) for path in resources)
            await check(
                "outside_data_and_system_writes_denied",
                python_command(
                    "import errno, os\n"
                    f"reads = {denied_reads!r}\n"
                    f"writes = {denied_writes!r}\n"
                    "for path in reads:\n"
                    "    try:\n"
                    "        fd = os.open(path, os.O_RDONLY)\n"
                    "    except PermissionError:\n"
                    "        continue\n"
                    "    os.close(fd)\n"
                    "    raise AssertionError('outside read permitted')\n"
                    "for path in writes:\n"
                    "    exists = path in reads\n"
                    "    try:\n"
                    "        flags = os.O_WRONLY if exists else os.O_WRONLY | os.O_CREAT | os.O_EXCL\n"
                    "        fd = os.open(path, flags, 0o600)\n"
                    "    except OSError as exc:\n"
                    "        assert exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS), exc\n"
                    "        continue\n"
                    "    os.close(fd)\n"
                    "    if not exists:\n"
                    "        os.unlink(path)\n"
                    "    raise AssertionError('outside write permitted')\n"
                    "print('denied')\n"
                ),
                "denied\n",
            )
            assert private_file.read_text(encoding="utf-8") == "synthetic private data"
            assert not (root / "outside-created.txt").exists()
        finally:
            await close_execution(execution)

        print(json.dumps({"checks": checks, "passed": all(checks.values())}), flush=True)
        if not all(checks.values()):
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(qualify())
