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
        config.mcp_config_path, "windows", url="http://[::1]:8765/mcp", headers={"X-Node": "one"}
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
    configure_mcp_server_config(config.mcp_config_path, "windows", url=url)
    with pytest.raises(ConfigError):
        resolve_qmt_connection_profile(config)
