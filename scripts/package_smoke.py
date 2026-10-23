from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

import tomlkit
from agents.mcp import MCPServerStdio

import investorch
from investorch.config import load_config
from investorch.skills.validation import validate_skill
from investorch.web.assets import STATIC_DIR

BUILTIN_SKILLS = (
    "investorch-configuration",
    "investorch-portfolio",
    "qmt-strategy",
    "rqalpha-strategy",
    "skill-creator",
    "skill-installer",
)


def _run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, env=env, text=True, capture_output=True, timeout=60)


def request(origin: str, path: str, *, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Origin": origin, **({"Content-Type": "application/json"} if data is not None else {})}
    message = Request(origin + path, data=data, headers=headers, method=method)
    with build_opener(ProxyHandler({})).open(message, timeout=30) as response:
        payload = response.read()
        return json.loads(payload) if "application/json" in response.headers.get("Content-Type", "") else payload


@contextmanager
def running_web(executable: str, home: Path):
    """Run the installed production entrypoint under a disposable HOME."""
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile(mode="w+b") as log:
        process = subprocess.Popen(
            [executable, "web", "--port", str(port)],
            env={**os.environ, "HOME": str(home)},
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    raise AssertionError(f"Web exited before readiness: {log.read().decode(errors='replace')}")
                try:
                    request(origin, "/api/setup/state")
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise AssertionError("Web did not start within 45 seconds") from None
                    time.sleep(0.1)
            yield process, origin
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise AssertionError("Web failed to shut down gracefully") from None


def complete_neutral_setup(origin: str) -> None:
    changes = {
        "secrets.PACKAGE_SMOKE_KEY": "not-a-real-provider-key",
        "models.primary": {
            "type": "openai_compatible",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key_secret": "PACKAGE_SMOKE_KEY",
        },
    }
    for role in ("main", "title", "activity", "permission", "compact"):
        changes.update(
            {
                f"agents.{role}.model": "primary",
                f"agents.{role}.name": "package-smoke-model",
                f"agents.{role}.context_window_tokens": 131072,
            }
        )
    request(origin, "/api/setup/config", method="PATCH", body={"changes": changes})
    assert request(origin, "/api/setup/complete", method="POST")["status"] == "ready"


async def check_cnequity_mcp(temp: Path, home: Path) -> dict:
    """Check the installed optional backend through its native read-only MCP."""
    import polars as pl
    from cnequity.domain.schemas import DAILY_BARS_SCHEMA

    # The native CLI requires a curated Parquet file before serving MCP.
    # A schema-only dataset satisfies that contract without inventing market data.
    bars = temp / "lake" / "curated" / "daily_bars"
    bars.mkdir(parents=True)
    pl.DataFrame(schema=DAILY_BARS_SCHEMA).write_parquet(bars / "smoke.parquet")
    config_path = temp / "cnequity.toml"
    config_path.write_text(
        tomlkit.dumps({"data": {"root": str(temp / "lake")}, "on_demand": {"enabled": False}}),
        encoding="utf-8",
    )
    async with MCPServerStdio(
        params={
            "command": sys.executable,
            "args": ["-m", "cnequity", "mcp", "--config", str(config_path)],
            "cwd": str(temp),
            "env": {**os.environ, "HOME": str(home)},
        }
    ) as server:
        result = await server.call_tool("run_sql", {"sql": "SELECT 42 AS answer"})
        content = "\n".join(item.text for item in result.content if item.type == "text")
        assert not result.is_error, f"CNEquity MCP SQL failed: {content}"
        payload = json.loads(content)
        assert payload["columns"] == ["answer"] and payload["rows"] == [[42]], payload
        assert payload["returned"] == payload["total"] == 1 and not payload["truncated"], payload
    return {"transport": "stdio", "read_only_sql": "passed"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test an installed InvestOrch distribution in disposable state.")
    parser.add_argument(
        "--expect-cnequity",
        action="store_true",
        help="Validate an explicit CNEquity extra installation; core installs must omit CNEquity by default.",
    )
    options = parser.parse_args()
    package_file = Path(investorch.__file__ or "").resolve()
    package_distribution = distribution("investorch")
    installed_package = Path(package_distribution.locate_file("investorch")).resolve()
    assert package_file.parent == installed_package
    assert package_distribution.metadata["Name"] == "investorch"
    assert package_distribution.metadata["Author"] == "InvestOrch contributors"
    assert package_distribution.metadata["License-Expression"] == "Apache-2.0"
    assert set(package_distribution.metadata.get_all("License-File") or []) == {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
    }
    project_urls = dict(value.split(", ", 1) for value in package_distribution.metadata.get_all("Project-URL") or [])
    assert project_urls == {
        "Documentation": "https://github.com/huangyicong0769/investorch-agent#readme",
        "Homepage": "https://github.com/huangyicong0769/investorch-agent",
        "Issues": "https://github.com/huangyicong0769/investorch-agent/issues",
        "Repository": "https://github.com/huangyicong0769/investorch-agent.git",
    }
    assert {entry.name for entry in package_distribution.entry_points if entry.group == "console_scripts"} == {
        "investorch"
    }
    cnequity_requirements = [
        requirement for requirement in package_distribution.requires or [] if requirement.lower().startswith("cnequity")
    ]
    assert len(cnequity_requirements) == 1
    assert "cnequity==0.7.3" in cnequity_requirements[0]
    assert "extra ==" in cnequity_requirements[0]
    cnequity_version = None
    try:
        cnequity_distribution = distribution("cnequity")
    except PackageNotFoundError:
        assert not options.expect_cnequity, "Explicit CNEquity extra installation is missing"
    else:
        assert options.expect_cnequity, "CNEquity must not be installed by default"
        cnequity_version = cnequity_distribution.version
        assert cnequity_version == "0.7.3", "Explicit CNEquity extra must install version 0.7.3"
    assert not (Path.cwd() / "pyproject.toml").exists()
    assert not (Path.cwd() / "src").exists()

    resources = package_file.parent / "resources"
    assert not (resources / "investorch.toml").exists()
    assert (resources / "MEMORY.md.template").is_file()
    bundled_skills = resources / "skills"
    assert sorted(path.name for path in bundled_skills.iterdir() if path.is_dir()) == list(BUILTIN_SKILLS)
    for name in BUILTIN_SKILLS:
        skill = validate_skill(bundled_skills / name, source_type="builtin")
        assert skill.metadata.name == name

    assert (STATIC_DIR / "index.html").is_file()
    assert any(path.is_file() for path in (STATIC_DIR / "assets").iterdir())
    assert (STATIC_DIR / "THIRD_PARTY_LICENSES.txt").is_file()

    executable = shutil.which("investorch")
    assert executable is not None
    _run([executable, "--help"])
    _run([executable, "web", "--help"])

    with tempfile.TemporaryDirectory(prefix="investorch-package-smoke-") as temp_dir:
        temp = Path(temp_dir)
        home = temp / "home"
        home.mkdir()
        root = home / ".investorch"
        cnequity_mcp = asyncio.run(check_cnequity_mcp(temp, home)) if options.expect_cnequity else None
        with running_web(executable, home) as (process, origin):
            pid = process.pid
            assert request(origin, "/api/setup/state")["status"] == "setup_required"
            assert b"<html" in request(origin, "/setup")
            schema = request(origin, "/api/config/schema")
            assert "properties" in schema
            config = load_config(root=root)
            assert config["runtime.max_turns"] == 100
            assert config["agents.main.name"] is None
            assert config.readiness()
            complete_neutral_setup(origin)
            assert request(origin, "/api/setup/state")["status"] == "ready"
            assert process.pid == pid and process.poll() is None
            created = request(origin, "/api/sessions", method="POST")
            assert created["session"]["session_id"]
            assert request(origin, "/api/setup/complete", method="POST")["status"] == "ready"
        assert (root / "investorch.toml").is_file()
        assert (root / "mcp.toml").is_file()
        assert (root / "state").is_dir()

        workspace = root / "workspace"
        assert (workspace / "MEMORY.md").read_bytes() == (resources / "MEMORY.md.template").read_bytes()
        assert not (workspace / "memory" / "configuration.md").exists()
        assert not (workspace / "memory" / "rqalpha.md").exists()
        registry_path = root / "state" / "skills.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry == {
            "schema_version": 1,
            "skills": {name: {"enabled": True, "source": {"type": "builtin"}} for name in BUILTIN_SKILLS},
        }
        for name in BUILTIN_SKILLS:
            installed = validate_skill(workspace / "skills" / name, source_type="builtin")
            bundled = validate_skill(bundled_skills / name, source_type="builtin")
            assert installed.metadata == bundled.metadata
            assert installed.files == bundled.files
            for relative_path in bundled.files:
                assert (workspace / "skills" / name / relative_path).read_bytes() == (
                    bundled_skills / name / relative_path
                ).read_bytes()
    print(
        json.dumps(
            {
                "package": "passed",
                "production_setup": "passed",
                "same_process_host": "passed",
                "provider_requests": 0,
                "cnequity": {
                    "expected": options.expect_cnequity,
                    "installed": cnequity_version is not None,
                    "version": cnequity_version,
                    "mcp": cnequity_mcp,
                },
            }
        )
    )


if __name__ == "__main__":
    main()
