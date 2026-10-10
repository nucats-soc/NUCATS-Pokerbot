"""
Spectator web view: a tiny HTTP server that serves one page and streams table
events to it with Server-Sent Events.

The page shows every player's hole cards, so by default it only listens on
127.0.0.1. Don't expose it to the bots' network unless you mean to.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "web" / "index.html"
HEARTBEAT_S = 15


class Spectators:
    """Fan-out of table events to every open browser tab, with replay of the
    current match for tabs that open late. Also takes the page's Start button
    (POST /start) and passes it to on_start."""

    def __init__(self):
        # Set by whoever runs matches; returns (started, message).
        self.on_start: Callable[[], tuple[bool, str]] | None = None
        self.history: list[dict] = []
        self.lobby: dict = {"type": "lobby", "players": []}
        self._queues: set[asyncio.Queue] = set()
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    # ------------------------------------------------------------------ feed

    def publish(self, event: dict) -> None:
        if event["type"] == "lobby":
            self.lobby = event
        else:
            if event["type"] == "match_info":
                self.history = []          # new match: start replay afresh
            self.history.append(event)
        for queue in self._queues:
            queue.put_nowait(event)

    # ------------------------------------------------------------------- http

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._handle, host, port)
        logger.info("Spectator view on http://%s:%s/", host, self.port)

    async def stop(self) -> None:
        if not self._server:
            return
        self.publish({"type": "server_stopped"})
        await asyncio.sleep(0.1)           # let the last events flush
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            request = await asyncio.wait_for(reader.readline(), 10)
            headers = {}
            while (line := await asyncio.wait_for(reader.readline(), 10)).strip():
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            parts = request.decode("latin-1").split()
            method, path = (parts + ["", ""])[:2]
            path = path.split("?")[0]
            if path == "/start":
                await self._start(writer, method, headers)
            elif method != "GET":
                await self._respond(writer, 405, "text/plain", b"GET only\n")
            elif path in ("/", "/index.html"):
                await self._respond(writer, 200, "text/html; charset=utf-8",
                                    PAGE.read_bytes())
            elif path == "/events":
                await self._stream(writer)
            else:
                await self._respond(writer, 404, "text/plain", b"not found\n")
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _start(self, writer, method: str, headers: dict) -> None:
        # The custom header can't be sent cross-site without a CORS preflight,
        # which we never approve, so other web pages can't press Start.
        if method != "POST" or headers.get("x-nucats-action") != "start":
            await self._json(writer, 403, {"ok": False, "message": "forbidden"})
        elif self.on_start is None:
            await self._json(writer, 409, {"ok": False,
                                           "message": "manual start is not enabled"})
        else:
            ok, message = self.on_start()
            await self._json(writer, 200 if ok else 409,
                             {"ok": ok, "message": message})

    async def _json(self, writer, status: int, body: dict) -> None:
        await self._respond(writer, status, "application/json",
                            json.dumps(body).encode())

    async def _respond(self, writer, status: int, content_type: str,
                       body: bytes) -> None:
        reason = {200: "OK", 403: "Forbidden", 404: "Not Found",
                  405: "Method Not Allowed", 409: "Conflict"}[status]
        writer.write(f"HTTP/1.1 {status} {reason}\r\n"
                     f"Content-Type: {content_type}\r\n"
                     f"Content-Length: {len(body)}\r\n"
                     "Cache-Control: no-store\r\n"
                     "Connection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\n"
                     b"Content-Type: text/event-stream\r\n"
                     b"Cache-Control: no-store\r\n"
                     b"Connection: keep-alive\r\n\r\n")
        queue: asyncio.Queue = asyncio.Queue()
        # Catch-up first, then live. Registering before sending the replay
        # (with no await in between) means no event is missed or doubled.
        backlog = [self.lobby, *self.history]
        self._queues.add(queue)
        try:
            for event in backlog:
                writer.write(self._frame(event))
            await writer.drain()
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), HEARTBEAT_S)
                    writer.write(self._frame(event))
                except asyncio.TimeoutError:
                    event = {}
                    writer.write(b": ping\n\n")
                await writer.drain()
                if event.get("type") == "server_stopped":
                    return
        finally:
            self._queues.discard(queue)

    @staticmethod
    def _frame(event: dict) -> bytes:
        return f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()
