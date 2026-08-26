import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import TextIO
from zoneinfo import ZoneInfo

from qmt_agent.config import AppConfig


class _SecureRotatingFileHandler(RotatingFileHandler):
    def _open(self) -> TextIO:
        descriptor = os.open(self.baseFilename, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        return os.fdopen(descriptor, self.mode, encoding=self.encoding, errors=self.errors)


class _TimezoneFormatter(logging.Formatter):
    def __init__(self, timezone: ZoneInfo) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self._timezone = timezone

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, self._timezone).isoformat(timespec="milliseconds")


def configure_system_logging(config: AppConfig) -> None:
    config.system_log_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        config.system_log_dir.chmod(0o700)

    app_logger = logging.getLogger("qmt_agent")

    for handler in app_logger.handlers[:]:
        if getattr(handler, "_qmt_agent_system_log", False):
            app_logger.removeHandler(handler)
            handler.close()

    level = getattr(logging, config["logging.level"])
    handler = _SecureRotatingFileHandler(
        config.system_log_path,
        maxBytes=config["logging.max_bytes"],
        backupCount=config["logging.backup_count"],
        encoding="utf-8",
    )
    handler._qmt_agent_system_log = True
    handler.setLevel(level)
    handler.setFormatter(_TimezoneFormatter(ZoneInfo(config["runtime.default_timezone"])))

    if os.name == "posix":
        config.system_log_path.chmod(0o600)

    app_logger.setLevel(level)
    app_logger.addHandler(handler)
    app_logger.propagate = False
