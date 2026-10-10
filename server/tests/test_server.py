"""End-to-end tests over real TCP sockets."""
import asyncio
import json
import random

from pokerserver.config import Config
from pokerserver.server import Server

from test_match import random_reply


def make_config(tmp_path, **kw) -> Config:
    return Config(**{"host": "127.0.0.1", "action_timeout_ms": 500,
                     "hello_timeout_s": 1.0, "lobby_wait_s": 0.2,
                     "log_dir": str(tmp_path), **kw})


class Client:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.received: list[dict] = []

    @classmethod
    async def connect(cls, port: int) -> "Client":
        return cls(*await asyncio.open_connection("127.0.0.1", port))

    async def send(self, message) -> None:
        raw = message if isinstance(message, (bytes, str)) else json.dumps(message)
        self.writer.write((raw.encode() if isinstance(raw, str) else raw) + b"\n")
        await self.writer.drain()

    async def recv(self, timeout: float = 2.0) -> dict | None:
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        if not line:
            return None
        message = json.loads(line)
        self.received.append(message)
        return message

    async def hello(self, name: str = "bot") -> dict:
        await self.send({"type": "hello", "protocol": 1, "name": name})
        return await self.recv()

    async def close(self) -> None:
        self.writer.close()

    async def play(self, seed: int = 0) -> dict:
        """Play random legal actions until match_end; return it."""
        rng = random.Random(seed)
        while True:
            message = await self.recv(timeout=10)
            assert message is not None, "server closed the connection"
            if message["type"] == "action_request":
                await self.send(random_reply(message, rng))
            elif message["type"] == "match_end":
                return message


async def with_server(config: Config, body):
    server = Server(config, port=0)   # 0 = any free port
    await server.start()
    task = asyncio.create_task(server.run_matches())
    try:
        return await body(server)
    finally:
        task.cancel()
        await server.stop()


def test_six_bots_play_a_full_match(tmp_path):
    async def body(server):
        clients = [await Client.connect(server.port) for _ in range(6)]
        welcomes = [await c.hello(f"bot{i}") for i, c in enumerate(clients)]
        assert len({w["player_id"] for w in welcomes}) == 6
        assert welcomes[0]["config"]["starting_stack"] == 1000
        ends = await asyncio.gather(*(c.play(i) for i, c in enumerate(clients)))
        return clients, ends

    clients, ends = asyncio.run(with_server(make_config(tmp_path), body))
    standings = ends[0]["standings"]
    assert all(e == ends[0] for e in ends)
    assert len(standings) == 6
    assert sum(s["stack"] for s in standings) == 6000
    # every hand keeps the table total at 6000
    stacks = {}
    for m in clients[0].received:
        if m["type"] == "hand_end":
            for r in m["results"]:
                stacks[r["seat"]] = r["stack"]
            assert sum(stacks.values()) == 6000 or len(stacks) < 6
    # a hand history was written
    log = (tmp_path / f"{ends[0]['match_id']}.jsonl").read_text().splitlines()
    assert json.loads(log[0])["type"] == "match_info"


def test_match_starts_with_two_after_lobby_wait(tmp_path):
    async def body(server):
        a, b = await Client.connect(server.port), await Client.connect(server.port)
        await a.hello("same")
        await b.hello("same")   # names needn't be unique
        ends = await asyncio.gather(a.play(1), b.play(2))
        return a, ends

    a, ends = asyncio.run(with_server(make_config(tmp_path, hands_per_match=3), body))
    ids = [s["player_id"] for s in ends[0]["standings"]]
    assert len(set(ids)) == 2
    assert ends[0]["hands_played"] <= 3


def test_survivors_return_to_lobby_for_next_match(tmp_path):
    async def body(server):
        a, b = await Client.connect(server.port), await Client.connect(server.port)
        await a.hello("a")
        await b.hello("b")
        first = await asyncio.gather(a.play(1), b.play(2))
        second = await asyncio.gather(a.play(3), b.play(4))
        return first, second

    first, second = asyncio.run(
        with_server(make_config(tmp_path, hands_per_match=2, max_matches=2), body))
    assert first[0]["match_id"] != second[0]["match_id"]


def test_serve_plays_one_match_prints_leaderboard_and_disconnects(tmp_path, capsys):
    async def main():
        server = Server(make_config(tmp_path, hands_per_match=3), port=0)
        serving = asyncio.create_task(server.serve())
        while server._server is None:
            await asyncio.sleep(0.01)
        clients = [await Client.connect(server.port) for _ in range(3)]
        for i, c in enumerate(clients):
            await c.hello(f"bot{i}")
        ends = await asyncio.gather(*(c.play(i) for i, c in enumerate(clients)))
        # after match_end every connection is closed and serve() returns
        assert [await c.recv() for c in clients] == [None, None, None]
        await asyncio.wait_for(serving, 5)
        return ends

    ends = asyncio.run(main())
    out = capsys.readouterr().out
    assert f"Leaderboard: match {ends[0]['match_id']}" in out
    for s in ends[0]["standings"]:
        assert s["name"] in out and str(s["stack"]) in out


def test_disconnect_mid_match_keeps_seat(tmp_path):
    async def body(server):
        clients = [await Client.connect(server.port) for _ in range(3)]
        for i, c in enumerate(clients):
            await c.hello(f"bot{i}")
        quitter = clients[0]
        while (await quitter.recv())["type"] != "hand_start":
            pass
        await quitter.close()
        return await asyncio.gather(clients[1].play(1), clients[2].play(2))

    ends = asyncio.run(with_server(make_config(tmp_path, hands_per_match=4), body))
    standings = ends[0]["standings"]
    assert len(standings) == 3
    assert sum(s["stack"] for s in standings) == 3000
    assert any(s.get("connected") is False for s in standings)


async def expect_error(client: Client, code: str, fatal: bool) -> None:
    message = await client.recv()
    assert message["type"] == "error", message
    assert (message["code"], message["fatal"]) == (code, fatal)
    if fatal:
        assert await client.recv() is None     # connection closed


def test_handshake_errors(tmp_path):
    async def body(server):
        c = await Client.connect(server.port)
        await c.send({"type": "action", "request_id": 1, "action": "fold"})
        await expect_error(c, "hello_required", True)

        c = await Client.connect(server.port)
        await c.send({"type": "hello", "protocol": 2, "name": "x"})
        await expect_error(c, "protocol_mismatch", True)

        for bad in ("", "x" * 33, "no\nnewlines", "emoji🙂", 7):
            c = await Client.connect(server.port)
            await c.send({"type": "hello", "protocol": 1, "name": bad})
            await expect_error(c, "bad_name", True)

        c = await Client.connect(server.port)
        await expect_error(c, "hello_timeout", True)

        c = await Client.connect(server.port)
        await c.send(b"x" * 70000)
        await expect_error(c, "line_too_long", True)

        # bad JSON is not fatal: the client can still say hello afterwards
        c = await Client.connect(server.port)
        await c.send("this is not json")
        await expect_error(c, "bad_json", False)
        await c.send("[1, 2, 3]")
        await expect_error(c, "bad_json", False)
        assert (await c.hello())["type"] == "welcome"

    asyncio.run(with_server(make_config(tmp_path, max_players=6, lobby_wait_s=60),
                            body))


def test_in_game_protocol_errors(tmp_path):
    async def body(server):
        a, b = await Client.connect(server.port), await Client.connect(server.port)
        await a.hello("a")
        await b.hello("b")

        await a.send({"type": "hello", "protocol": 1, "name": "a"})
        await a.send({"type": "chat", "text": "hi"})
        await a.send({"type": "action", "request_id": 999, "action": "fold"})
        codes = []
        while len(codes) < 3:
            m = await a.recv()
            if m["type"] == "error":
                codes.append(m["code"])
        assert codes == ["duplicate_hello", "unknown_type", "stale_request"]

        # Let a time out on every request; b plays normally.
        async def idle(client):
            while (m := await client.recv(timeout=10))["type"] != "match_end":
                pass
            return m
        return await asyncio.gather(idle(a), b.play(1)), a

    (ends, a) = asyncio.run(with_server(
        make_config(tmp_path, hands_per_match=2, action_timeout_ms=100), body))
    assert any(m["type"] == "error" and m["code"] == "timeout" for m in a.received)
    assert sum(s["stack"] for s in ends[0]["standings"]) == 2000
