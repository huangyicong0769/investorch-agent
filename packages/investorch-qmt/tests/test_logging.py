from __future__ import annotations

import re
from pathlib import Path

from investorch_qmt.config import default_paths, load_config
from investorch_qmt.log import close_logging, configure_logging

TOKEN = "secret-token-that-must-never-be-logged"


def configured_logger(tmp_path: Path, *, max_bytes: int = 10_000, backup_count: int = 2):
    paths = default_paths(tmp_path / "QMT")
    paths.root.mkdir(parents=True)
    paths.config.write_text(
        f"""
[auth]
token = "{TOKEN}"

[logging]
level = "DEBUG"
max_bytes = {max_bytes}
backup_count = {backup_count}
""",
        encoding="utf-8",
    )
    config = load_config(paths.config)
    return paths, config, configure_logging(config, paths)


def test_operational_log_writes_an_offset_timestamp(tmp_path: Path) -> None:
    paths, _, logger = configured_logger(tmp_path)
    try:
        logger.info("service started")
    finally:
        close_logging(logger)

    content = paths.log.read_text(encoding="utf-8")
    assert "service started" in content
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}", content)


def test_operational_log_redacts_token_from_messages_headers_and_config(tmp_path: Path) -> None:
    paths, config, logger = configured_logger(tmp_path)
    try:
        logger.error("token=%s", TOKEN)
        logger.error("Authorization: Bearer %s", TOKEN)
        logger.error("config=%r", config)
    finally:
        close_logging(logger)

    content = paths.log.read_text(encoding="utf-8")
    assert TOKEN not in content
    assert "[REDACTED]" in content


def test_operational_log_rotates_at_configured_size(tmp_path: Path) -> None:
    paths, _, logger = configured_logger(tmp_path, max_bytes=200, backup_count=1)
    try:
        for index in range(20):
            logger.info("rotation record %02d %s", index, "x" * 40)
    finally:
        close_logging(logger)

    assert paths.log.is_file()
    assert paths.log.with_name(f"{paths.log.name}.1").is_file()
