from __future__ import annotations

from pathlib import Path

import pytest

from investorch.mcp import (
    configure_mcp_server_config,
    load_mcp_servers,
    read_mcp_server_configs,
    remove_mcp_server_config,
)


def test_configure_then_read_preserves_server_semantics(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"

    configured = configure_mcp_server_config(
        path,
        "research",
        url="https://example.test/mcp",
        enabled=True,
        cache_tools_list=True,
        headers={"Authorization": "Bearer ${TOKEN}"},
        timeout=12.5,
    )

    assert read_mcp_server_configs(path) == [configured]
    assert configured == {
        "name": "research",
        "enabled": True,
        "transport": "streamable_http",
        "url": "https://example.test/mcp",
        "cache_tools_list": True,
        "headers": {"Authorization": "Bearer ${TOKEN}"},
        "timeout": 12.5,
    }


def test_update_replaces_existing_server_without_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    configure_mcp_server_config(path, "research", url="https://old.test/mcp")

    configure_mcp_server_config(path, "research", url="https://new.test/mcp", enabled=False)

    servers = read_mcp_server_configs(path)
    assert len(servers) == 1
    assert servers[0]["url"] == "https://new.test/mcp"
    assert servers[0]["enabled"] is False


def test_duplicate_server_names_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text(
        """
[[servers]]
name = "duplicate"
transport = "streamable_http"
url = "https://one.test/mcp"

[[servers]]
name = "duplicate"
transport = "streamable_http"
url = "https://two.test/mcp"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate"):
        read_mcp_server_configs(path)


def test_remove_reports_presence_and_persists_absence(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    configure_mcp_server_config(path, "research", url="https://example.test/mcp")

    assert remove_mcp_server_config(path, "research") is True
    assert read_mcp_server_configs(path) == []
    assert remove_mcp_server_config(path, "research") is False


def test_missing_secret_variable_fails_server_loading(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    configure_mcp_server_config(
        path,
        "research",
        url="https://example.test/mcp",
        headers={"Authorization": "Bearer ${TOKEN}"},
    )

    with pytest.raises(ValueError, match="TOKEN"):
        load_mcp_servers(path, variables={}, default_timeout_seconds=5)


def test_disabled_server_is_excluded_from_runtime_collection(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    configure_mcp_server_config(path, "active", url="https://active.test/mcp")
    configure_mcp_server_config(path, "disabled", url="https://disabled.test/mcp", enabled=False)

    servers = load_mcp_servers(path, variables={}, default_timeout_seconds=5)

    assert [server.name for server in servers] == ["active"]
