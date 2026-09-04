from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from investorch_qmt.config import AppPaths, QMTConfig

_LOGGER_NAME = "investorch_qmt"


class _RedactingFormatter(logging.Formatter):
    def __init__(self, token: str) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        self._token = token

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record).replace(self._token, "[REDACTED]")


def configure_logging(config: QMTConfig, paths: AppPaths) -> logging.Logger:
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(config.logging.level)
    logger.propagate = False
    close_logging(logger)

    handler = RotatingFileHandler(
        paths.log,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(_RedactingFormatter(config.auth.token))
    logger.addHandler(handler)
    return logger


def close_logging(logger: logging.Logger) -> None:
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.flush()
        handler.close()
