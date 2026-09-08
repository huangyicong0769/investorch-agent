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


def test_mcp_controls_require_approval_while_status_remains_read_only(tmp_path: Path) -> None:
    from agents.mcp.util import MCPUtil
    from mcp.types import Tool

    path = tmp_path / "mcp.toml"
    configure_mcp_server_config(
        path,
        "qmt",
        url="http://node.test/mcp",
        require_approval=["start_live_strategy", "stop_live_strategy"],
    )
    server = load_mcp_servers(path, variables={}, default_timeout_seconds=5)[0]
    for name, approval in [("get_status", False), ("start_live_strategy", True), ("stop_live_strategy", True)]:
        tool = MCPUtil.to_function_tool(
            Tool(name=name, inputSchema={"type": "object", "properties": {}}), server, False
        )
        assert tool.needs_approval is approval


@pytest.mark.parametrize("value", ['"start"', '["start", "start"]', '[""]', '[" start "]', "[1]"])
def test_invalid_approval_lists_are_rejected(tmp_path: Path, value: str) -> None:
    path = tmp_path / "mcp.toml"
    path.write_text(
        "[ [servers] ]".replace(" ", "")
        + '\nname="qmt"\ntransport="streamable_http"\nurl="http://node/mcp"\nrequire_approval='
        + value
        + "\n"
    )
    with pytest.raises(ValueError, match="require_approval"):
        read_mcp_server_configs(path)


def test_mcp_update_preserves_and_can_clear_approval_policy(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    configure_mcp_server_config(path, "qmt", url="http://node/mcp", require_approval=["start_live_strategy"])
    configure_mcp_server_config(path, "qmt", timeout=9)
    assert read_mcp_server_configs(path)[0]["require_approval"] == ["start_live_strategy"]
    configure_mcp_server_config(path, "qmt", require_approval=[])
    assert read_mcp_server_configs(path)[0]["require_approval"] == []


@pytest.mark.asyncio
async def test_qmt_authority_tracks_current_session_on_existing_http_client() -> None:
    import httpx2

    from investorch.mcp import ControlSessionAuth

    session = None
    observed = []

    def capture(request):
        observed.append(dict(request.headers))
        return httpx2.Response(200, json={})

    async with httpx2.AsyncClient(
        auth=ControlSessionAuth(lambda: session),
        headers={"Authorization": "Bearer secret", "X-InvestOrch-Control-Session": "configured-stale"},
        transport=httpx2.MockTransport(capture),
    ) as client:
        for current in (None, "first", "second", None):
            session = current
            await client.post("http://node/mcp")

    assert [headers.get("x-investorch-control-session") for headers in observed] == [None, "first", "second", None]
    assert all(headers["authorization"] == "Bearer secret" for headers in observed)


@pytest.mark.asyncio
async def test_qmt_recovery_leaves_other_failed_servers_alone_and_preserves_task_affinity(monkeypatch) -> None:
    import asyncio

    from agents.mcp import MCPServerStreamableHttp

    from investorch.mcp import open_agent_mcp_servers

    qmt = MCPServerStreamableHttp(params={"url": "http://unused/mcp"}, name="execution")
    research = MCPServerStreamableHttp(params={"url": "http://unused/research"}, name="research")
    attempts = []
    qmt_tasks = []
    online = False

    async def qmt_connect():
        attempts.append("execution")
        qmt_tasks.append(asyncio.current_task())
        if not online:
            raise ConnectionError("offline")

    async def qmt_cleanup():
        qmt_tasks.append(asyncio.current_task())

    async def research_connect():
        attempts.append("research")
        raise ConnectionError("offline")

    async def research_cleanup():
        pass

    monkeypatch.setattr(qmt, "connect", qmt_connect)
    monkeypatch.setattr(qmt, "cleanup", qmt_cleanup)
    monkeypatch.setattr(research, "connect", research_connect)
    monkeypatch.setattr(research, "cleanup", research_cleanup)
    async with open_agent_mcp_servers(
        [research, qmt],
        qmt_server_name="execution",
        control_session_id=lambda: None,
        drop_failed_servers=True,
    ) as lifecycle:
        assert lifecycle.active_servers == []
        assert sorted(attempts) == ["execution", "research"]
        online = True
        await asyncio.create_task(lifecycle.refresh_qmt())
        assert lifecycle.active_servers == [qmt]
        await lifecycle.refresh_qmt()
        assert attempts.count("execution") == 2
        assert attempts.count("research") == 1

    # Each connect's AnyIO scopes are closed by that same worker, even though
    # the foreground reconnect runs in another task.
    assert qmt_tasks[0] is qmt_tasks[1]
    assert qmt_tasks[2] is qmt_tasks[3]
