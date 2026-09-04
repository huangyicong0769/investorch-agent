from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

TOKEN = "serve-token-with-at-least-32-characters"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process lifecycle contract")


def environment(local_app_data: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(local_app_data)
    return env


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def write_config(local_app_data: Path, port: int, *, host: str = "127.0.0.1") -> Path:
    root = local_app_data / "InvestOrch" / "QMT"
    root.mkdir(parents=True)
    allowed_hosts = f'allowed_hosts = ["127.0.0.1:{port}"]' if host != "127.0.0.1" else "allowed_hosts = []"
    config = root / "investorch-qmt.toml"
    config.write_text(
        f"""
[server]
host = "{host}"
port = {port}
{allowed_hosts}

[auth]
token = "{TOKEN}"

[logging]
level = "INFO"
max_bytes = 100000
backup_count = 1
""",
        encoding="utf-8",
    )
    return root


def start_server(local_app_data: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["investorch-qmt", "serve"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        env=environment(local_app_data),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_until_healthy(process: subprocess.Popen[str], port: int) -> None:
    with httpx.Client(trust_env=False) as client:
        for _ in range(100):
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(f"server exited early: stdout={stdout!r} stderr={stderr!r}")
            try:
                response = client.get(
                    f"http://127.0.0.1:{port}/healthz",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    timeout=0.2,
                )
            except httpx.TransportError:
                time.sleep(0.05)
                continue
            if response.status_code == 200:
                return
            time.sleep(0.05)
    raise AssertionError("server did not become healthy")


def stop_server(process: subprocess.Popen[str]) -> tuple[str, str]:
    process.send_signal(signal.CTRL_BREAK_EVENT)
    return process.communicate(timeout=10)


def test_serve_starts_authenticated_service_and_stops_cleanly(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    port = available_port()
    root = write_config(local_app_data, port)
    process = start_server(local_app_data)
    try:
        wait_until_healthy(process, port)
        stdout, stderr = stop_server(process)
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 0
    assert f"Listening on http://127.0.0.1:{port}" in stdout
    assert "traceback" not in stderr.lower()
    assert TOKEN not in stderr
    log = (root / "logs" / "investorch-qmt.log").read_text(encoding="utf-8")
    assert "service starting" in log
    assert "service stopped" in log
    assert TOKEN not in log


def test_serve_reports_bind_failure_without_traceback(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        root = write_config(local_app_data, port)
        completed = subprocess.run(
            ["investorch-qmt", "serve"],
            check=False,
            capture_output=True,
            env=environment(local_app_data),
            text=True,
            timeout=30,
        )

    assert completed.returncode == 1
    assert "cannot bind" in completed.stderr.lower()
    assert "traceback" not in completed.stderr.lower()
    assert TOKEN not in (root / "logs" / "investorch-qmt.log").read_text(encoding="utf-8")


def test_serve_reports_missing_configuration_without_traceback(tmp_path: Path) -> None:
    completed = subprocess.run(
        ["investorch-qmt", "serve"],
        check=False,
        capture_output=True,
        env=environment(tmp_path / "LocalAppData"),
        text=True,
        timeout=30,
    )

    assert completed.returncode == 1
    assert "not initialized" in completed.stderr
    assert "traceback" not in completed.stderr.lower()


def test_lan_serve_warns_against_public_internet_exposure(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    port = available_port()
    write_config(local_app_data, port, host="0.0.0.0")
    process = start_server(local_app_data)
    try:
        wait_until_healthy(process, port)
        stdout, _ = stop_server(process)
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 0
    assert "trusted local network or private VPN" in stdout
    assert "Do not expose this endpoint directly to the public Internet." in stdout
