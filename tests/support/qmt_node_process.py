"""Run only in the separate companion interpreter for HTTP bridge acceptance tests."""

import asyncio
import importlib.util
import json
import socket
import sys
from pathlib import Path

import uvicorn
from investorch_qmt.config import default_paths, initialize_config
from investorch_qmt.execution.service import ExecutionNodeService
from investorch_qmt.runtime.supervisor import RuntimeSupervisor
from investorch_qmt.server import create_app
from starlette.requests import Request
from starlette.responses import JSONResponse


def market_unavailable_worker(spec, pipe):
    """Deterministic external market boundary, executed in a real spawned child."""
    assert (Path(spec.deployment_dir) / "strategy.py").is_file()
    try:
        pipe.send({"phase": "FAILED", "reason": "MARKET_DATA_NOT_READY", "retryable": True})
    finally:
        pipe.close()


def unavailable_market_runtime(on_event, gate):
    return RuntimeSupervisor(on_event, gate, worker_target=market_unavailable_worker)


async def main() -> None:
    # The runtime boundary is deliberately exercised without the Core installed.
    assert importlib.util.find_spec("investorch") is None
    root, ready_file = map(Path, sys.argv[1:3])
    lose_response = sys.argv[3]
    paths = default_paths(root)
    config = initialize_config(paths)
    service = ExecutionNodeService(paths, runtime_factory=unavailable_market_runtime)
    app = create_app(config, paths, service)

    async def fixture_app(scope, receive, send):
        nonlocal lose_response
        if scope["type"] == "http" and (
            (lose_response == "stage" and scope["method"] == "PUT")
            or (lose_response == "ack" and scope["path"].endswith("/ack"))
        ):
            lose_response = ""
            buffered = []

            async def capture(message):
                buffered.append(message)

            # Commit through the real adapter, then withhold its response past the client timeout.
            await app(scope, receive, capture)
            await asyncio.sleep(1)
            for message in buffered:
                await send(message)
            return
        if scope["type"] == "http" and scope["path"] == "/__test/enqueue":
            request = Request(scope, receive)
            if request.headers.get("Authorization") != f"Bearer {config.auth.token}":
                response = JSONResponse({}, status_code=401)
            else:
                response = JSONResponse(service.enqueue_trade_fact(await request.json()))
            await response(scope, receive, send)
        else:
            await app(scope, receive, send)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    if len(sys.argv) > 4:
        # Reserve the endpoint without listening: Core startup cannot connect.
        start_file = Path(sys.argv[4])
        ready_file.with_suffix(".prepared.json").write_text(
            json.dumps({"url": f"http://127.0.0.1:{port}", "token": config.auth.token})
        )
        while not start_file.exists():
            await asyncio.sleep(0.01)
    server = uvicorn.Server(uvicorn.Config(fixture_app, log_level="warning", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if task.done():
            await task
            raise RuntimeError("Companion failed to start")
        await asyncio.sleep(0.01)
    ready_file.write_text(json.dumps({"url": f"http://127.0.0.1:{port}", "token": config.auth.token}))
    await task


if __name__ == "__main__":
    asyncio.run(main())
