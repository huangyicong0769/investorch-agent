from pathlib import Path

import pytest

from investorch.config import ConfigError
from investorch.mcp import configure_mcp_server_config
from investorch.qmt.config import resolve_qmt_connection_profile
from tests.support.config import make_test_config


def test_execution_profile_reuses_selected_mcp_connection_and_expanded_headers(tmp_path: Path) -> None:
    config = make_test_config(tmp_path, {"qmt": {"mcp_server": "windows"}, "secrets": {"NODE_TOKEN": "test-secret"}})
    configure_mcp_server_config(
        config.mcp_config_path,
        "windows",
        url="https://node.test:8765/mcp/",
        headers={"Authorization": "Bearer ${NODE_TOKEN}"},
        timeout=12.5,
        require_approval=["start_live_strategy", "stop_live_strategy"],
    )
    profile = resolve_qmt_connection_profile(config)
    assert profile is not None
    assert profile.rest_base_url == "https://node.test:8765/api/v1"
    assert profile.headers == {"Authorization": "Bearer test-secret"}
    assert profile.timeout_seconds == 12.5
    assert "test-secret" not in repr(profile)


def test_unconfigured_execution_node_needs_no_mcp_file(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    assert resolve_qmt_connection_profile(config) is None


def test_unknown_and_disabled_targets_fail_as_static_configuration_errors(tmp_path: Path) -> None:

    config = make_test_config(tmp_path, {"qmt": {"mcp_server": "windows"}})
    with pytest.raises(ConfigError, match="unknown MCP server"):
        resolve_qmt_connection_profile(config)
    configure_mcp_server_config(config.mcp_config_path, "windows", url="http://node.test/mcp", enabled=False)
    with pytest.raises(ConfigError, match="disabled MCP server"):
        resolve_qmt_connection_profile(config)


def test_profile_uses_custom_default_timeout_and_is_an_immutable_snapshot(tmp_path: Path) -> None:

    config = make_test_config(tmp_path, {"qmt": {"mcp_server": "windows"}, "mcp": {"default_timeout_seconds": 17}})
    configure_mcp_server_config(
        config.mcp_config_path,
        "windows",
        url="http://[::1]:8765/mcp",
        headers={"X-Node": "one"},
        require_approval=["start_live_strategy", "stop_live_strategy"],
    )
    profile = resolve_qmt_connection_profile(config)
    assert profile is not None
    assert profile.rest_base_url == "http://[::1]:8765/api/v1"
    assert profile.timeout_seconds == 17
    with pytest.raises(TypeError):
        profile.headers["X-Node"] = "two"
    configure_mcp_server_config(config.mcp_config_path, "windows", headers={"X-Node": "three"})
    assert profile.headers["X-Node"] == "one"


def test_execution_node_reference_update_requires_restart(tmp_path: Path) -> None:

    config = make_test_config(tmp_path)
    with pytest.raises(ConfigError, match="restart"):
        config.update("qmt.mcp_server", "windows", persist=False)
    result = config.update("qmt.mcp_server", "windows", persist=True)
    assert result["requires_restart"] is True
    assert config["qmt.mcp_server"] == ""


@pytest.mark.parametrize(
    "url",
    [
        "ftp://node/mcp",
        "http:///mcp",
        "http://node/other/mcp",
        "http://node/mcp?x=1",
        "http://node/mcp#fragment",
        "http://user:secret@node/mcp",
        "http://node:bad/mcp",
    ],
)
def test_invalid_execution_urls_fail_before_networking(tmp_path: Path, url: str) -> None:
    config = make_test_config(tmp_path, {"qmt": {"mcp_server": "windows"}})
    configure_mcp_server_config(
        config.mcp_config_path, "windows", url=url, require_approval=["start_live_strategy", "stop_live_strategy"]
    )
    with pytest.raises(ConfigError):
        resolve_qmt_connection_profile(config)


@pytest.mark.parametrize("approval", [None, [], ["start_live_strategy"], ["stop_live_strategy"]])
def test_selected_execution_node_requires_both_control_approvals(tmp_path: Path, approval: list[str] | None) -> None:
    config = make_test_config(tmp_path, {"qmt": {"mcp_server": "windows"}})
    configure_mcp_server_config(
        config.mcp_config_path, "windows", url="http://node.test/mcp", require_approval=approval
    )
    with pytest.raises(ConfigError, match=r"require_approval.*start_live_strategy.*stop_live_strategy"):
        resolve_qmt_connection_profile(config)


def test_accepted_qmt_profile_yields_approved_sdk_controls_and_read_only_status(tmp_path: Path) -> None:
    from agents.mcp.util import MCPUtil
    from mcp.types import Tool

    from investorch.mcp import load_mcp_servers

    config = make_test_config(tmp_path, {"qmt": {"mcp_server": "windows"}})
    configure_mcp_server_config(
        config.mcp_config_path,
        "windows",
        url="http://node.test/mcp",
        require_approval=["start_live_strategy", "stop_live_strategy"],
    )
    configure_mcp_server_config(config.mcp_config_path, "research", url="http://research.test/mcp")
    assert resolve_qmt_connection_profile(config) is not None
    servers = load_mcp_servers(config.mcp_config_path, config.secrets, config["mcp.default_timeout_seconds"])
    selected = next(server for server in servers if server.name == "windows")
    for name, expected in [("start_live_strategy", True), ("stop_live_strategy", True), ("get_status", False)]:
        tool = MCPUtil.to_function_tool(
            Tool(name=name, inputSchema={"type": "object", "properties": {}}), selected, False
        )
        assert tool.needs_approval is expected
    research = next(server for server in servers if server.name == "research")
    assert (
        MCPUtil.to_function_tool(
            Tool(name="query", inputSchema={"type": "object", "properties": {}}), research, False
        ).needs_approval
        is False
    )
