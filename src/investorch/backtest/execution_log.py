from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import logbook
from logbook.handlers import DEFAULT_FORMAT_STRING, StringFormatter
from rqalpha.utils.exception import CustomError


class NativeExecutionLog:
    """Scope RQAlpha's original Logbook messages and a bounded diagnostic preview to one run."""

    def __init__(self, path: Path, *, secrets: Sequence[str], preview_chars: int) -> None:
        self._path = path
        self._secrets = sorted(filter(None, secrets), key=len, reverse=True)
        self._preview_limit = preview_chars
        self._preview = ""
        self._diagnostic_chars = 0
        self._warning_count = 0
        self._error_count = 0
        self._formatter = StringFormatter(DEFAULT_FORMAT_STRING)

    def _format(self, record, handler) -> str:
        # RQAlpha's CustomError repr and formatted_exception contain strategy
        # source/locals. Keep the original cause, rather than serializing that wrapper.
        if isinstance(record.msg, CustomError):
            cause = record.msg.exc_val
            message = f"{type(cause).__name__}: {cause}"
        else:
            message = record.message
        fields = SimpleNamespace(
            time=record.time, level_name=record.level_name, channel=record.channel, message=message
        )
        line = self._formatter.format_record(fields, handler)
        for secret in self._secrets:
            line = line.replace(secret, "<redacted>")
        if record.level >= logbook.WARNING:
            if record.level >= logbook.ERROR:
                self._error_count += 1
            else:
                self._warning_count += 1
            text = line + "\n"
            self._diagnostic_chars += len(text)
            self._preview += text[: max(0, self._preview_limit - len(self._preview))]
        return line

    @contextmanager
    def capture(self) -> Iterator[None]:
        # extra.log_file installs an application-wide handler without removing it.
        # A native, context-bound handler covers run_file and is always closed here.
        handler = logbook.FileHandler(
            self._path,
            mode="w",
            bubble=True,
            filter=lambda record, _: record.channel in {"system_log", "user_log", "user_system_log"},
        )
        handler.formatter = self._format
        with handler:
            yield

    def diagnostics(self, status: str) -> dict[str, object]:
        return {
            "status": status,
            "warning_count": self._warning_count,
            "error_count": self._error_count,
            "preview": self._preview,
            "preview_truncated": self._diagnostic_chars > len(self._preview),
        }
