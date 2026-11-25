"""Qualify the macOS terminal clients in a disposable root.

Run with ``uv run --locked python scripts/qualify_release_runtime.py
--evidence-dir /private/tmp/investorch-release-evidence``.
Pass ``--check-cnequity`` to test the data CLI with CNEquity installed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from importlib.metadata import version
from pathlib import Path

import tomlkit
from agents import set_tracing_disabled

from investorch.config import AppConfig
from investorch.config_schema import REQUIRED_AGENT_ROLES
from investorch.initializer import initialize


def make_config(root: Path) -> AppConfig:
    models = {"primary": {"type": "openai_compatible", "base_url": "http://127.0.0.1:1/v1", "api_key_secret": "unused"}}
    agents = {
        role: {"model": "primary", "name": "qualification-no-provider-call", "context_window_tokens": 131072}
        for role in REQUIRED_AGENT_ROLES
    }
    config = AppConfig(
        {"paths": {"root": str(root)}, "models": models, "agents": agents, "secrets": {"unused": "unused"}}
    )
    initialize(config)
    config.root_config_path.write_text(
        tomlkit.dumps({"models": models, "agents": agents, "secrets": {"unused": "unused"}})
    )
    config.root_config_path.chmod(0o600)
    return config


def qualify_terminal(config: AppConfig, evidence: Path, *, plain: bool) -> dict:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
    env = dict(os.environ, HOME=str(config.root.parent), TERM="xterm-256color", NO_PROXY="127.0.0.1,localhost")
    command = [sys.executable, "-m", "investorch", *(["--plain"] if plain else [])]
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
    os.close(slave)
    output = bytearray()

    def read_until(marker: bytes | None, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if marker is not None and marker in output:
                    return
            if process.poll() is not None:
                break
        if marker is not None and marker not in output:
            raise RuntimeError(f"{'plain' if plain else 'TUI'} did not reach readiness marker")

    try:
        read_until(b"You: " if plain else b"InvestOrch")
        if plain:
            os.write(master, b"/help\n")
            read_until(b"Show this help.")
            os.write(master, b"/exit\n")
        else:
            # Let the first Textual render finish before sending its Quit binding.
            time.sleep(0.5)
            os.write(master, b"\x11")
        read_until(None)
        code = process.wait(timeout=10)
        if code != 0 or b"Traceback" in output:
            raise RuntimeError(f"terminal client failed (exit {code})")
        if plain and b"Exiting..." not in output:
            raise RuntimeError("plain client did not acknowledge /exit")
        return {
            "exit_code": code,
            "terminal": "native PTY",
            "output_bytes": len(output),
            "provider_inference": "not requested",
        }
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)
        (evidence / ("plain.txt" if plain else "tui.txt")).write_bytes(output)


def main() -> int:
    set_tracing_disabled(True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--check-cnequity", action="store_true", help="Also test the data CLI with CNEquity installed")
    options = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("native terminal qualification requires macOS")
    options.evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="native-runtime-", dir=options.evidence_dir)).resolve()
    evidence.chmod(0o700)
    root = evidence / "home" / ".investorch"
    root.mkdir(parents=True)
    config = make_config(root)
    report = {
        "status": "running",
        "evidence_dir": str(evidence),
        "platform": sys.platform,
        "versions": {name: version(name) for name in ("investorch", "openai-agents", "textual")},
        "checks": {},
    }
    try:
        report["checks"]["plain"] = qualify_terminal(config, evidence, plain=True)
        report["checks"]["tui"] = qualify_terminal(config, evidence, plain=False)
        if options.check_cnequity:
            report["versions"]["cnequity"] = version("cnequity")
            cli = subprocess.run(
                [sys.executable, "-m", "investorch", "data", "--version"],
                env={**os.environ, "HOME": str(root.parent)},
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            report["checks"]["cnequity_cli"] = {"output": cli.stdout.strip(), "exit_code": cli.returncode}
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        (evidence / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(evidence / "report.json"),
                "completed_checks": list(report["checks"]),
                "error": report.get("error"),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
