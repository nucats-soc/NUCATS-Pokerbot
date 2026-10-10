"""
TCP front end: accepts connections, runs the hello handshake, keeps the lobby,
and starts matches. Identity is the TCP connection: every connection gets its
own player_id, and a dropped connection is never re-attached to a seat.
"""
import asyncio
import itertools
import json
import logging
import re

from . import PROTOCOL_VERSION
from .config import Config
from .match import Match
from .web import Spectators

logger = logging.getLogger(__name__)

MAX_LINE = 65536
NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,32}$")


def format_leaderboard(match_id: str, hands_played: int,
                       standings: list[dict]) -> str:
    rows = [f"Rank  {'Name':<32}  {'Player':<6}  Seat   Chips     Net"]
    for s in standings:
        note = "  (disconnected)" if s.get("connected") is False else ""
        rows.append(f"{s['rank']:>4}  {s['name']:<32}  {s['player_id']:<6}  "
                    f"{s['seat']:>4}  {s['stack']:>6}  {s['net']:>+6}{note}")
    width = max(len(r) for r in rows)
    title = f" Leaderboard: match {match_id}, {hands_played} hands "
    return "\n".join([title.center(width, "="), *rows, "=" * width])


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Connection:
    """One bot's TCP connection. Implements match.Player."""

    def __init__(self, player_id: str, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.player_id = player_id
        self.name = ""
        self.reader = reader
        self.writer = writer
        self.peer = writer.get_extra_info("peername")
        self._connected = True
        self._pending_id: int | None = None
        self._pending: asyncio.Future | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    def __repr__(self) -> str:
        return f"<{self.player_id} {self.name!r} {self.peer}>"

    # ----------------------------------------------------------------- output

    async def send(self, message: dict) -> None:
        if not self._connected:
            return
        try:
            self.writer.write(json.dumps(message, separators=(",", ":")).encode()
                              + b"\n")
            await self.writer.drain()
        except (ConnectionError, OSError):
            self._mark_disconnected()

    async def send_error(self, code: str, message: str, fatal: bool = False) -> None:
        await self.send({"type": "error", "code": code, "message": message,
                         "fatal": fatal})

    async def close(self) -> None:
        self._mark_disconnected()
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    def _mark_disconnected(self) -> None:
        self._connected = False
        if self._pending and not self._pending.done():
            self._pending.set_result(None)

    # ------------------------------------------------------------------ input

    async def read_message(self) -> dict | None:
        """Next well-formed message, or None at EOF. Raises ProtocolError."""
        while True:
            try:
                line = await self.reader.readuntil(b"\n")
            except asyncio.IncompleteReadError as e:
                if not e.partial.strip():
                    return None
                line = e.partial
            except asyncio.LimitOverrunError:
                raise ProtocolError("line_too_long",
                                    f"lines must be at most {MAX_LINE} bytes")
            except (ConnectionError, OSError):
                return None
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = None
            if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                await self.send_error("bad_json", "each line must be a JSON object "
                                                  "with a string 'type'")
                continue
            return message

    async def request_action(self, message: dict, timeout: float) -> dict | None:
        if not self._connected:
            return None
        loop = asyncio.get_running_loop()
        self._pending_id = message["request_id"]
        self._pending = loop.create_future()
        try:
            await self.send(message)
            return await asyncio.wait_for(self._pending, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_id = None
            self._pending = None

    async def handle(self, message: dict) -> None:
        """Dispatch a message received after the handshake."""
        kind = message["type"]
        if kind == "action":
            if (self._pending is not None and not self._pending.done()
                    and message.get("request_id") == self._pending_id):
                self._pending.set_result(message)
            else:
                await self.send_error("stale_request",
                                      "no action_request is waiting for that "
                                      "request_id")
        elif kind == "hello":
            await self.send_error("duplicate_hello", "already said hello; ignored")
        else:
            await self.send_error("unknown_type", f"unknown message type {kind!r}")


class Server:
    def __init__(self, config: Config, port: int,
                 spectators: Spectators | None = None,
                 manual_start: bool = False):
        """manual_start: matches begin only when request_start() is called
        (the spectator page's Start button), never automatically."""
        self.config = config
        self._port = port
        self.spectators = spectators
        self.manual_start = manual_start
        self.match_running = False
        self._start_requested = False
        self.lobby: list[Connection] = []
        self._lobby_changed = asyncio.Event()
        self._player_ids = itertools.count(1)
        self._match_ids = itertools.count(1)
        self._server: asyncio.base_events.Server | None = None
        self.results: list[list[dict]] = []
        self._connections: set[Connection] = set()

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_connect, self.config.host, self._port, limit=MAX_LINE)
        logger.info("Listening on %s:%s", self.config.host, self.port)
        self._lobby_updated()      # tell any spectator page the server's mode

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            # wait_closed() waits for every open connection, so close them.
            for conn in list(self._connections):
                await conn.close()
            await self._server.wait_closed()

    async def serve(self) -> None:
        """Run matches until max_matches is reached (or forever), then
        disconnect everyone and stop."""
        await self.start()
        try:
            await self.run_matches()
        finally:
            if self._connections:
                logger.info("Disconnecting %d bot(s) and shutting down",
                            len(self._connections))
            await self.stop()
            if self.spectators:
                await self.spectators.stop()

    # ------------------------------------------------------------ connections

    async def _on_connect(self, reader, writer) -> None:
        conn = Connection(f"p{next(self._player_ids)}", reader, writer)
        self._connections.add(conn)
        logger.info("Connection from %s as %s", conn.peer, conn.player_id)
        try:
            await self._handshake(conn)
            self.lobby.append(conn)
            self._lobby_updated()
            logger.info("%r joined the lobby (%d waiting)", conn, len(self.lobby))
            while True:
                message = await conn.read_message()
                if message is None:
                    break
                await conn.handle(message)
        except ProtocolError as e:
            await conn.send_error(e.code, str(e), fatal=True)
        except Exception:
            logger.exception("Error handling %r", conn)
        finally:
            await conn.close()
            self._connections.discard(conn)
            if conn in self.lobby:
                self.lobby.remove(conn)
                self._lobby_updated()
            logger.info("%r disconnected", conn)

    async def _handshake(self, conn: Connection) -> None:
        try:
            message = await asyncio.wait_for(conn.read_message(),
                                             self.config.hello_timeout_s)
        except asyncio.TimeoutError:
            raise ProtocolError("hello_timeout", "no hello received in time")
        if message is None:
            raise ProtocolError("hello_required", "connection closed before hello")
        if message["type"] != "hello":
            raise ProtocolError("hello_required", "the first message must be hello")
        if message.get("protocol") != PROTOCOL_VERSION:
            raise ProtocolError("protocol_mismatch",
                                f"server speaks protocol {PROTOCOL_VERSION}")
        name = message.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ProtocolError("bad_name", "name must be 1-32 characters of "
                                            "letters, digits, space, _ - .")
        conn.name = name
        await conn.send({"type": "welcome", "protocol": PROTOCOL_VERSION,
                         "player_id": conn.player_id, "name": name,
                         "config": self.config.public()})

    # ---------------------------------------------------------------- matches

    async def run_matches(self) -> None:
        played = 0
        while not self.config.max_matches or played < self.config.max_matches:
            players = await self._wait_for_players()
            match_id = f"m{next(self._match_ids)}"
            seed = (None if self.config.seed is None
                    else self.config.seed + played)
            match = Match(match_id, players, self.config, seed=seed,
                          log_dir=self.config.log_dir,
                          on_event=self.spectators.publish
                          if self.spectators else None)
            logger.info("Starting %s with %s (seed %s)", match_id,
                        ", ".join(repr(p) for p in players), match.seed)
            self.match_running = True
            self._lobby_updated()
            try:
                standings = await match.run()
            finally:
                self.match_running = False
            self.results.append(standings)
            played += 1
            print("\n" + format_leaderboard(match_id, match.hands_played,
                                            standings) + "\n", flush=True)
            # Survivors go back to the front of the queue, in seat order.
            survivors = [p for p in players if p.connected]
            self.lobby[:0] = survivors
            self._lobby_updated()

    def request_start(self) -> tuple[bool, str]:
        """Start a match now with whoever is waiting (manual-start mode)."""
        if not self.manual_start:
            return False, "this server starts matches automatically"
        if self.match_running:
            return False, "a match is already running"
        waiting = [c for c in self.lobby if c.connected]
        if len(waiting) < self.config.min_players:
            return False, (f"need at least {self.config.min_players} bots, "
                           f"{len(waiting)} waiting")
        self._start_requested = True
        self._lobby_changed.set()
        seated = min(len(waiting), self.config.max_players)
        logger.info("Start requested: starting a match with %d bot(s)", seated)
        return True, f"starting a match with {seated} bots"

    async def _wait_for_players(self) -> list[Connection]:
        """Block until a match can start, then take players off the lobby."""
        cfg = self.config
        loop = asyncio.get_running_loop()
        ready_since: float | None = None
        while True:
            waiting = [c for c in self.lobby if c.connected]
            timeout = None
            if self.manual_start:
                # Only the Start button begins a match. A request made when
                # too few bots are left (someone dropped) is cancelled.
                if self._start_requested and len(waiting) >= cfg.min_players:
                    self._start_requested = False
                    break
                self._start_requested = False
            elif len(waiting) >= cfg.max_players:
                break
            elif len(waiting) >= cfg.min_players:
                now = loop.time()
                ready_since = ready_since if ready_since is not None else now
                if now - ready_since >= cfg.lobby_wait_s:
                    break
                timeout = cfg.lobby_wait_s - (now - ready_since)
            else:
                ready_since = None
            self._lobby_changed.clear()
            try:
                await asyncio.wait_for(self._lobby_changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        players = waiting[:cfg.max_players]
        for p in players:
            self.lobby.remove(p)
        self._lobby_updated()
        return players

    def _lobby_updated(self) -> None:
        self._lobby_changed.set()
        if self.spectators:
            self.spectators.publish({
                "type": "lobby",
                "players": [{"player_id": c.player_id, "name": c.name}
                            for c in self.lobby],
                "manual_start": self.manual_start,
                "match_running": self.match_running,
                "min_players": self.config.min_players,
                "max_players": self.config.max_players,
            })
