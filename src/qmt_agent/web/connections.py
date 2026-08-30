from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket
from starlette import status
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)

DEFAULT_CONNECTION_QUEUE_CAPACITY = 1000
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

WebPayload = dict[str, object]


@dataclass(eq=False, slots=True)
class _WebConnection:
    websocket: WebSocket
    queue: asyncio.Queue[WebPayload]
    handler_task: asyncio.Task[None] | None = None
    writer_task: asyncio.Task[None] | None = None
    close_task: asyncio.Task[None] | None = None
    close_code: int = status.WS_1000_NORMAL_CLOSURE
    dropping: bool = False
    close_sent: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)


class WebConnectionHub:
    def __init__(self, *, queue_capacity: int = DEFAULT_CONNECTION_QUEUE_CAPACITY) -> None:
        if type(queue_capacity) is not int or queue_capacity < 1:
            raise ValueError("queue_capacity must be a positive integer")
        self._queue_capacity = queue_capacity
        self._connections: set[_WebConnection] = set()
        self._closing = False

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def publish(self, payload: WebPayload) -> None:
        if self._closing:
            return
        for connection in tuple(self._connections):
            if connection.dropping:
                continue
            try:
                connection.queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("Dropping lagging WebSocket connection because its event queue is full")
                self._begin_drop(connection, status.WS_1013_TRY_AGAIN_LATER)

    async def serve(self, websocket: WebSocket) -> None:
        if self._closing:
            await self._safe_close(websocket, status.WS_1013_TRY_AGAIN_LATER)
            return

        await websocket.accept()
        if self._closing:
            await self._safe_close(websocket, status.WS_1001_GOING_AWAY)
            return

        connection = _WebConnection(
            websocket=websocket,
            queue=asyncio.Queue(maxsize=self._queue_capacity),
        )
        handler_task = asyncio.current_task()
        if handler_task is None:
            raise RuntimeError("WebSocket handler requires an asyncio task")
        connection.handler_task = handler_task
        connection.writer_task = asyncio.create_task(self._write(connection), name="websocket-writer")
        connection.writer_task.add_done_callback(lambda task: self._writer_finished(connection, task))
        self._connections.add(connection)
        disconnected = False

        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    disconnected = True
                    break
                if message["type"] == "websocket.receive":
                    connection.close_code = status.WS_1008_POLICY_VIOLATION
                    break
        except (WebSocketDisconnect, RuntimeError, OSError):
            disconnected = True
        except asyncio.CancelledError:
            if not connection.dropping:
                disconnected = True
        finally:
            self._connections.discard(connection)
            if connection.writer_task is not None:
                connection.writer_task.cancel()
                try:
                    await asyncio.gather(connection.writer_task, return_exceptions=True)
                except asyncio.CancelledError:
                    pass
            if connection.close_task is not None:
                try:
                    await asyncio.gather(connection.close_task, return_exceptions=True)
                except asyncio.CancelledError:
                    pass
            if not disconnected and not connection.close_sent:
                try:
                    await self._safe_close(websocket, connection.close_code)
                except asyncio.CancelledError:
                    pass
            connection.done.set()

    async def aclose(self) -> None:
        self._closing = True
        connections = tuple(self._connections)
        for connection in connections:
            self._begin_drop(connection, status.WS_1001_GOING_AWAY)
        if connections:
            await asyncio.gather(*(connection.done.wait() for connection in connections))

    @staticmethod
    async def _write(connection: _WebConnection) -> None:
        while True:
            payload = await connection.queue.get()
            await connection.websocket.send_json(payload)

    def _writer_finished(self, connection: _WebConnection, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and connection in self._connections and not connection.dropping:
            logger.debug(
                "Dropping WebSocket connection after writer failure",
                exc_info=(type(error), error, error.__traceback__),
            )
            self._begin_drop(connection, status.WS_1001_GOING_AWAY)

    def _begin_drop(self, connection: _WebConnection, code: int) -> None:
        if connection.dropping:
            return
        connection.dropping = True
        connection.close_code = code
        if connection.writer_task is not None:
            connection.writer_task.cancel()
        connection.close_task = asyncio.create_task(self._close_and_cancel(connection), name="websocket-close")

    async def _close_and_cancel(self, connection: _WebConnection) -> None:
        await self._safe_close(connection.websocket, connection.close_code)
        connection.close_sent = True
        if connection.handler_task is not None:
            connection.handler_task.cancel()

    @staticmethod
    async def _safe_close(websocket: WebSocket, code: int) -> None:
        try:
            await websocket.close(code=code)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass


def websocket_origin_allowed(origin: str | None) -> bool:
    if origin is None:
        return False
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in _LOOPBACK_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


websocket_router = APIRouter()


@websocket_router.websocket("/ws")
async def stream_events(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if not websocket_origin_allowed(origin):
        logger.warning("Rejected WebSocket connection with disallowed Origin")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    connections = getattr(websocket.app.state, "connections", None)
    if not isinstance(connections, WebConnectionHub):
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return
    await connections.serve(websocket)
