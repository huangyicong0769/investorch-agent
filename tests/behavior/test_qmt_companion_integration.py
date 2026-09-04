from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import tomlkit
from agents.mcp import MCPServerStreamableHttp

from investorch.mcp import load_mcp_servers

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QMT_PROJECT = PROJECT_ROOT / "packages" / "investorch-qmt"


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def qmt_command(*args: str) -> list[str]:
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = QMT_PROJECT / ".venv" / scripts / ("investorch-qmt.exe" if os.name == "nt" else "investorch-qmt")
    if not executable.is_file():
        pytest.skip("Run `uv sync --project packages/investorch-qmt --locked --dev` for Core integration")
    return [str(executable), *args]


def isolated_environment(tmp_path: Path) -> dict[str, str]:
    data_root = tmp_path / "data"
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(data_root)
    env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(data_root)
    return env


def wait_until_healthy(process: subprocess.Popen[str], url: str, token: str) -> None:
    with httpx.Client(trust_env=False) as client:
        for _ in range(100):
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"companion exited early: stdout={stdout!r} stderr={stderr!r}")
            try:
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=0.2,
                )
            except httpx.TransportError:
                time.sleep(0.05)
                continue
            if response.status_code == 200:
                return
            time.sleep(0.05)
    raise AssertionError("companion did not become healthy")


@pytest.mark.asyncio
async def test_core_connects_to_real_companion_with_expanded_bearer_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    env = isolated_environment(tmp_path)
    initialized = subprocess.run(
        qmt_command("init"),
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=60,
    )
    config_paths = list((tmp_path / "data").rglob("investorch-qmt.toml"))
    assert len(config_paths) == 1
    config_path = config_paths[0]
    token = subprocess.run(
        qmt_command("token", "show"),
        check=True,
        capture_output=True,
        env=env,
        text=True,
        timeout=60,
    ).stdout.strip()
    port = available_port()
    document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    document["server"]["port"] = port
    config_path.write_text(tomlkit.dumps(document), encoding="utf-8")

    process = subprocess.Popen(
        qmt_command("serve"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    health_url = f"http://127.0.0.1:{port}/healthz"
    try:
        wait_until_healthy(process, health_url, token)
        mcp_config = tmp_path / "mcp.toml"
        mcp_config.write_text(
            f"""
[[servers]]
name = "qmt"
enabled = true
transport = "streamable_http"
url = "http://127.0.0.1:{port}/mcp"
cache_tools_list = false

[servers.headers]
Authorization = "Bearer ${{QMT_MCP_TOKEN}}"
""",
            encoding="utf-8",
        )
        servers = load_mcp_servers(mcp_config, {"QMT_MCP_TOKEN": token}, default_timeout_seconds=5)
        assert len(servers) == 1
        wrong_secret_servers = load_mcp_servers(
            mcp_config,
            {"QMT_MCP_TOKEN": "definitely-wrong-token-value"},
            default_timeout_seconds=5,
        )
        assert len(wrong_secret_servers) == 1
        wrong_secret_server = wrong_secret_servers[0]
        assert isinstance(wrong_secret_server, MCPServerStreamableHttp)
        with httpx.Client(trust_env=False) as client:
            rejected = client.get(
                wrong_secret_server.params["url"],
                headers=wrong_secret_server.params["headers"],
            )
        assert rejected.status_code == 401

        async with servers[0] as server:
            tools = await server.list_tools()
            result = await server.call_tool("get_status", {})

        assert [tool.name for tool in tools] == ["get_status"]
        assert result.is_error is False
        assert result.structured_content["service"]["status"] == "ready"
        assert result.structured_content["qmt"]["status"] == "not_connected"

    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        process.communicate()

    assert initialized.stderr == ""
    assert process.returncode == 0
