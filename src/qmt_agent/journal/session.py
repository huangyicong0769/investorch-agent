import asyncio
import json
import os
import stat
import tempfile
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path
from typing import TypeVar
from zoneinfo import ZoneInfo

from qmt_agent.output.events import (
    AgentChanged,
    AssistantMessage,
    OutputEvent,
    Reasoning,
    ToolCalled,
    ToolOutput,
)

_T = TypeVar("_T")


async def _await_filesystem_operation(awaitable: Awaitable[_T]) -> _T:
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error

    try:
        result = task.result()
    except BaseException:
        if cancellation is not None:
            raise cancellation from None
        raise
    if cancellation is not None:
        raise cancellation from None
    return result


class SessionJournal:
    def __init__(self, directory: Path, timezone: ZoneInfo) -> None:
        self._directory = directory
        self._timezone = timezone
        self._lock = asyncio.Lock()
        self._next_seq: dict[str, int] = {}

        self._directory.mkdir(parents=True, exist_ok=True)
        if not self._directory.is_dir():
            raise RuntimeError(f"Session journal path is not a directory: {self._directory}")
        if os.name == "posix":
            self._directory.chmod(0o700)

    async def record_user_message(self, session_id: str, text: str) -> int:
        return await self._record(session_id, {"type": "user_message", "text": text})

    async def record_user_steer(self, session_id: str, run_id: str, text: str) -> int:
        return await self._record(
            session_id,
            {
                "type": "user_steer",
                "run_id": run_id,
                "text": text,
            },
        )

    async def record_output(self, session_id: str, event: OutputEvent) -> int:
        return await self._record(session_id, _serialize_output_event(event))

    async def record_approval(
        self,
        session_id: str,
        tool_name: str,
        arguments: str | None,
        approved: bool,
        *,
        source: str = "user",
        review_decision: str | None = None,
        review_reason: str | None = None,
    ) -> int:
        if source not in {"user", "permission"}:
            raise ValueError("approval source must be user or permission")
        if review_decision not in {None, "approve", "ask", "reject"}:
            raise ValueError("review_decision must be approve, ask, reject, or None")
        if source == "permission" and review_decision is None:
            raise ValueError("permission approval source requires a review_decision")
        if review_reason is not None:
            review_reason = review_reason.strip()
            if not review_reason:
                raise ValueError("review_reason must not be empty")

        record: dict[str, object] = {
            "type": "approval",
            "tool_name": tool_name,
            "arguments": arguments,
            "approved": approved,
            "source": source,
        }
        if review_decision is not None:
            record["review_decision"] = review_decision
        if review_reason is not None:
            record["review_reason"] = review_reason

        return await self._record(
            session_id,
            record,
        )

    async def record_activity_label(
        self,
        session_id: str,
        target_seq: int,
        text: str,
    ) -> int:
        if type(target_seq) is not int or target_seq < 1:
            raise ValueError("target_seq must be a positive integer")

        label = text.strip()
        if not label:
            raise ValueError("activity label text must not be empty")

        return await self._record(
            session_id,
            {
                "type": "activity_label",
                "target_seq": target_seq,
                "text": label,
            },
        )

    async def clone_session(
        self,
        source_session_id: str,
        target_session_id: str,
    ) -> bool:
        async with self._lock:
            source = self._session_path(source_session_id)
            target = self._session_path(target_session_id)
            if source == target:
                raise ValueError("source and target session IDs must be different")

            cloned = await _await_filesystem_operation(
                asyncio.to_thread(self._clone_session_file, source, target)
            )

            self._next_seq.pop(target_session_id, None)
            return cloned

    async def delete_session(self, session_id: str) -> None:
        async with self._lock:
            path = self._session_path(session_id)
            try:
                await _await_filesystem_operation(
                    asyncio.to_thread(self._delete_session_file, path)
                )
            finally:
                self._next_seq.pop(session_id, None)

    async def session_exists(self, session_id: str) -> bool:
        async with self._lock:
            path = self._session_path(session_id)
            if path.is_symlink():
                raise RuntimeError(f"Session journal is not a regular file: {path}")
            if not path.exists():
                return False
            if not path.is_file():
                raise RuntimeError(f"Session journal is not a regular file: {path}")
            return True

    async def _record(self, session_id: str, event: dict[str, object]) -> int:
        async with self._lock:
            path = self._session_path(session_id)

            try:
                next_seq = self._next_seq.get(session_id)
                if next_seq is None:
                    next_seq = await asyncio.to_thread(self._recover_next_seq, path)

                record = {
                    "seq": next_seq,
                    "timestamp": datetime.now(self._timezone).isoformat(timespec="milliseconds"),
                    **event,
                }
                await _await_filesystem_operation(
                    asyncio.to_thread(self._append, path, record)
                )
            except BaseException:
                self._next_seq.pop(session_id, None)
                raise

            self._next_seq[session_id] = next_seq + 1
            return next_seq

    def _session_path(self, session_id: str) -> Path:
        if (
            not session_id
            or session_id in {".", ".."}
            or "/" in session_id
            or "\\" in session_id
            or Path(session_id).name != session_id
        ):
            raise ValueError("session_id must be a non-empty filename-safe value")

        return self._directory / f"{session_id}.jsonl"

    def _recover_next_seq(self, path: Path) -> int:
        if not path.exists():
            return 1
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Session journal is not a regular file: {path}")

        last_line: str | None = None
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    last_line = line

        if last_line is None:
            return 1
        if not last_line.endswith("\n"):
            raise RuntimeError(f"Session journal has an incomplete final line: {path}")

        try:
            record = json.loads(last_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Session journal has an invalid final line: {path}") from exc

        if not isinstance(record, dict) or type(record.get("seq")) is not int or record["seq"] < 1:
            raise RuntimeError(f"Session journal has an invalid final sequence: {path}")

        return record["seq"] + 1

    def _append(self, path: Path, record: dict[str, object]) -> None:
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise RuntimeError(f"Session journal is not a regular file: {path}")

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)

        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RuntimeError(f"Session journal is not a regular file: {path}")
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)

            with os.fdopen(descriptor, "a", encoding="utf-8") as file:
                descriptor = -1
                file.write(line)
                file.flush()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _clone_session_file(self, source: Path, target: Path) -> bool:
        if target.is_symlink() or target.exists():
            raise FileExistsError(f"Target session journal already exists: {target}")
        if source.is_symlink():
            raise RuntimeError(f"Session journal is not a regular file: {source}")
        if not source.exists():
            return False
        if not source.is_file():
            raise RuntimeError(f"Session journal is not a regular file: {source}")

        data = source.read_bytes()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=self._directory,
        )
        temporary = Path(temporary_name)

        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                descriptor = -1
                file.write(data)
                file.flush()
                os.fsync(file.fileno())

            if target.is_symlink() or target.exists():
                raise FileExistsError(f"Target session journal already exists: {target}")
            os.replace(temporary, target)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

        return True

    def _delete_session_file(self, path: Path) -> None:
        if path.is_symlink():
            raise RuntimeError(f"Session journal is not a regular file: {path}")
        if not path.exists():
            return
        if not path.is_file():
            raise RuntimeError(f"Session journal is not a regular file: {path}")
        path.unlink()


def _serialize_output_event(event: OutputEvent) -> dict[str, object]:
    if isinstance(event, AgentChanged):
        return {"type": "agent_changed", "name": event.name}
    if isinstance(event, Reasoning):
        return {"type": "reasoning", "text": event.text}
    if isinstance(event, ToolCalled):
        return {"type": "tool_called", "name": event.name, "arguments": event.arguments}
    if isinstance(event, ToolOutput):
        return {"type": "tool_output", "output": event.output}
    if isinstance(event, AssistantMessage):
        return {"type": "assistant_message", "text": event.text}

    raise TypeError(f"Unsupported output event: {type(event).__name__}")
