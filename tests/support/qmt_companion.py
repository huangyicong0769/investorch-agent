"""Shared lifecycle for the separately installed companion acceptance process."""

import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from investorch.qmt.config import QMTConnectionProfile

_REPO = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def _companion_process(tmp_path, *, lose_response="", delayed=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ready, start = tmp_path / "ready.json", tmp_path / "start"
    published = ready.with_suffix(".prepared.json") if delayed else ready
    log_path = tmp_path / "node.log"
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            shutil.which("uv") or "uv",
            "run",
            "--project",
            str(_REPO / "packages/investorch-qmt"),
            "--locked",
            "python",
            str(_REPO / "tests/support/qmt_node_process.py"),
            str(tmp_path / "node"),
            str(ready),
            lose_response,
            *([str(start)] if delayed else []),
            stdout=log,
            stderr=log,
            env=env,
        )

        async def wait_file(path):
            async with asyncio.timeout(90):
                while not path.exists():
                    if process.returncode is not None:
                        raise AssertionError(log_path.read_text())
                    await asyncio.sleep(0.01)

        async def start_serving():
            start.touch()
            await wait_file(ready)

        try:
            await wait_file(published)
            data = json.loads(published.read_text())
            profile = QMTConnectionProfile(
                "node",
                data["url"] + "/mcp",
                data["url"] + "/api/v1",
                {"Authorization": "Bearer " + data["token"]},
                0.5 if lose_response else (1 if delayed else 5),
            )
            yield profile, start_serving
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()


@asynccontextmanager
async def companion_node(tmp_path, *, lose_response=""):
    async with _companion_process(tmp_path, lose_response=lose_response) as (profile, _start):
        yield profile


@asynccontextmanager
async def delayed_companion_node(tmp_path):
    """Publish credentials/port first, then let the caller start the actual listener."""
    async with _companion_process(tmp_path, delayed=True) as prepared:
        yield prepared
