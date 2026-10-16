from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI

from .auth_routes import router
from .errors import install_error_handlers
from .security import install_http_security

if TYPE_CHECKING:
    from investorch.llm.oauth import ChatGPTAuth


class _CallbackServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The enclosing TUI/Plain Host owns process signals and shutdown.
        yield


class AuthCallbackServer:
    """A callback-only listener sharing the existing Host's credential manager."""

    def __init__(self, auth: ChatGPTAuth):
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        app.state.config = auth.config
        app.state.chatgpt_auth = auth
        app.state.recovery_error = None
        app.include_router(router)
        install_error_handlers(app)
        install_http_security(app)
        self._server = _CallbackServer(
            uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", access_log=False, log_config=None)
        )
        self._socket: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self.base_url: str | None = None

    async def start(self) -> str:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket = listener
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            listener.setblocking(False)
            self.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            self._task = asyncio.create_task(self._server.serve(sockets=[listener]))
            async with asyncio.timeout(5):
                while not self._server.started:
                    if self._task.done():
                        await self._task
                        raise RuntimeError("ChatGPT callback listener could not start.")
                    await asyncio.sleep(0.01)
            return self.base_url
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            try:
                async with asyncio.timeout(5):
                    await asyncio.shield(self._task)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        if self._socket is not None:
            self._socket.close()
        self._task = None
        self._socket = None
