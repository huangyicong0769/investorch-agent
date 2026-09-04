from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from investorch_qmt.config import ConfigError, default_paths, load_config

VALID_TOKEN = "a" * 32


def write_config(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_local_overrides_merge_with_bundled_defaults_into_frozen_config(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "investorch-qmt.toml",
        f"""
[server]
port = 9123

[auth]
token = "{VALID_TOKEN}"
""",
    )

    config = load_config(path)

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9123
    assert config.server.allowed_hosts == ()
    assert config.auth.token == VALID_TOKEN
    assert config.logging.level == "INFO"
    assert config.logging.max_bytes == 10_485_760
    assert config.logging.backup_count == 5
    with pytest.raises(FrozenInstanceError):
        config.server.port = 9124  # type: ignore[misc]


@pytest.mark.parametrize(
    "content",
    [
        "[sever]\nhost = '127.0.0.1'",
        "[server]\nunknown = true",
        "[auth]\nunknown = true",
        "[logging]\nunknown = true",
        "[server\nport = 8765",
        f"[server]\nport = true\n[auth]\ntoken = '{VALID_TOKEN}'",
        f"[server]\nport = 0\n[auth]\ntoken = '{VALID_TOKEN}'",
        f"[server]\nport = 65536\n[auth]\ntoken = '{VALID_TOKEN}'",
        f"[server]\nhost = ''\n[auth]\ntoken = '{VALID_TOKEN}'",
        f"[server]\nallowed_hosts = [1]\n[auth]\ntoken = '{VALID_TOKEN}'",
        f"[server]\nhost = '0.0.0.0'\n[auth]\ntoken = '{VALID_TOKEN}'",
        f"[server]\nhost = '0.0.0.0'\nallowed_hosts = ['*']\n[auth]\ntoken = '{VALID_TOKEN}'",
        "[auth]\ntoken = 'short'",
        f"[auth]\ntoken = '{'a' * 31} '",
        f"[auth]\ntoken = '{'a' * 31}é'",
        f"[auth]\ntoken = '{VALID_TOKEN}'\n[logging]\nlevel = 'TRACE'",
        f"[auth]\ntoken = '{VALID_TOKEN}'\n[logging]\nmax_bytes = true",
        f"[auth]\ntoken = '{VALID_TOKEN}'\n[logging]\nbackup_count = -1",
    ],
)
def test_invalid_configuration_fails_fast(tmp_path: Path, content: str) -> None:
    path = write_config(tmp_path / "investorch-qmt.toml", content)

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_accept_empty_allowlist(tmp_path: Path, host: str) -> None:
    path = write_config(
        tmp_path / "investorch-qmt.toml",
        f"""
[server]
host = "{host}"

[auth]
token = "{VALID_TOKEN}"
""",
    )

    assert load_config(path).server.allowed_hosts == ()


def test_lan_host_requires_and_preserves_explicit_allowlist(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "investorch-qmt.toml",
        f"""
[server]
host = "0.0.0.0"
allowed_hosts = ["192.168.1.20:8765", "qmt-pc:8765"]

[auth]
token = "{VALID_TOKEN}"
""",
    )

    assert load_config(path).server.allowed_hosts == ("192.168.1.20:8765", "qmt-pc:8765")


def test_missing_local_configuration_is_actionable(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"

    with pytest.raises(ConfigError, match="not initialized"):
        load_config(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows deployment path contract")
def test_default_paths_use_windows_local_app_data() -> None:
    paths = default_paths()
    expected_root = Path(os.environ["LOCALAPPDATA"]) / "InvestOrch" / "QMT"

    assert paths.root == expected_root
    assert paths.config == expected_root / "investorch-qmt.toml"
    assert paths.log == expected_root / "logs" / "investorch-qmt.log"
