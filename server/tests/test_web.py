"""Spectator web view: page served, live event stream, replay for late tabs."""
import asyncio
import json
import time

from pokerserver.config import Config
from pokerserver.server import Server
from pokerserver.web import Spectators

from test_server import Client, make_config


async def http_get(port: int, path: str) -> tuple[str, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return head.decode().split("\r\n")[0], body


async def read_events(port: int, until: str) -> list[dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /events HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    while (await reader.readline()).strip():
        pass                                  # response headers
    events = []
    while True:
        line = await asyncio.wait_for(reader.readline(), 10)
        if line.startswith(b"data: "):
            events.append(json.loads(line[6:]))
            if events[-1]["type"] == until:
                writer.close()
                return events


async def start(tmp_path, **kw) -> tuple[Server, Spectators]:
    spectators = Spectators()
    await spectators.start("127.0.0.1", 0)
    server = Server(make_config(tmp_path, **kw), port=0, spectators=spectators)
    await server.start()
    return server, spectators


def test_page_and_404(tmp_path):
    async def main():
        server, spectators = await start(tmp_path)
        try:
            status, body = await http_get(spectators.port, "/")
            assert status.endswith("200 OK") and b"<title>NUCATS Pokerbots</title>" in body
            status, _ = await http_get(spectators.port, "/nope")
            assert "404" in status
        finally:
            await server.stop()
            await spectators.stop()

    asyncio.run(main())


def test_stream_shows_every_action_with_hole_cards(tmp_path):
    async def main():
        server, spectators = await start(tmp_path, hands_per_match=3)
        matches = asyncio.create_task(server.run_matches())
        watcher = asyncio.create_task(read_events(spectators.port, "match_end"))
        await asyncio.sleep(0.05)
        clients = [await Client.connect(server.port) for _ in range(3)]
        for i, c in enumerate(clients):
            await c.hello(f"bot{i}")
        ends = await asyncio.gather(*(c.play(i) for i, c in enumerate(clients)))
        events = await watcher
        # a tab opened after the match still gets the whole match replayed
        late = await read_events(spectators.port, "match_end")
        matches.cancel()
        await server.stop()
        await spectators.stop()
        return clients, ends, events, late

    clients, ends, events, late = asyncio.run(main())
    kinds = [e["type"] for e in events]
    assert kinds[0] == "lobby"
    for k in ("match_info", "match_start", "hand_start", "to_act",
              "player_action", "hand_end", "match_end"):
        assert k in kinds, k

    # spectators see every player's hole cards, but never the deck order
    starts = [e for e in events if e["type"] == "hand_start"]
    assert all(len(e["hole_cards"]) >= 2 for e in starts)
    assert all("deck" not in e for e in events)

    # every action a bot saw, the spectator saw too, in the same order
    bot_actions = [(m["hand_number"], m["seat"], m["action"], m["amount"])
                   for m in clients[0].received if m["type"] == "player_action"]
    seen = [(e["hand_number"], e["seat"], e["action"], e["amount"])
            for e in events if e["type"] == "player_action"]
    assert seen == bot_actions

    assert [e for e in late if e["type"] != "lobby"] == \
        [e for e in events if e["type"] != "lobby"]
    assert events[-1]["standings"] == ends[0]["standings"]


def test_lobby_updates_and_server_stopped(tmp_path):
    async def main():
        server, spectators = await start(tmp_path, lobby_wait_s=60)
        reader, writer = await asyncio.open_connection("127.0.0.1", spectators.port)
        writer.write(b"GET /events HTTP/1.1\r\n\r\n")
        while (await reader.readline()).strip():
            pass
        c = await Client.connect(server.port)
        await c.hello("waiting-bot")
        lobbies = []
        while len(lobbies) < 2:
            line = await asyncio.wait_for(reader.readline(), 5)
            if line.startswith(b"data: "):
                lobbies.append(json.loads(line[6:]))
        await server.stop()
        await spectators.stop()
        rest = await asyncio.wait_for(reader.read(), 5)
        return lobbies, rest

    lobbies, rest = asyncio.run(main())
    assert lobbies[0]["type"] == "lobby" and lobbies[0]["players"] == []
    assert lobbies[0]["manual_start"] is False
    assert lobbies[1]["players"][0]["name"] == "waiting-bot"
    assert b'"server_stopped"' in rest


def test_action_delay_paces_the_match(tmp_path):
    async def main():
        cfg = make_config(tmp_path, hands_per_match=1, action_delay_ms=50)
        server = Server(cfg, port=0)
        await server.start()
        task = asyncio.create_task(server.run_matches())
        a, b = await Client.connect(server.port), await Client.connect(server.port)
        await a.hello("a")
        await b.hello("b")
        t0 = time.monotonic()
        ends = await asyncio.gather(a.play(1), b.play(2))
        elapsed = time.monotonic() - t0
        task.cancel()
        await server.stop()
        actions = sum(1 for m in a.received if m["type"] in ("player_action", "street"))
        return elapsed, actions

    elapsed, actions = asyncio.run(main())
    # blinds + at least one action, each followed by a 50 ms pause, plus 150 ms after hand_end
    assert elapsed >= (actions * 0.05 + 0.15) * 0.9


async def post_start(port: int, header: bool = True) -> tuple[str, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    extra = "X-NUCATS-Action: start\r\n" if header else ""
    writer.write(f"POST /start HTTP/1.1\r\nHost: x\r\n{extra}Content-Length: 0\r\n\r\n"
                 .encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return head.decode().split("\r\n")[0], json.loads(body)


def test_manual_start_waits_for_the_button(tmp_path):
    async def main():
        spectators = Spectators()
        await spectators.start("127.0.0.1", 0)
        server = Server(make_config(tmp_path, hands_per_match=2, lobby_wait_s=0),
                        port=0, spectators=spectators, manual_start=True)
        spectators.on_start = server.request_start
        await server.start()
        task = asyncio.create_task(server.run_matches())
        try:
            # not enough bots yet
            first = await Client.connect(server.port)
            await first.hello("first")
            status, body = await post_start(spectators.port)
            assert "409" in status and not body["ok"] and "at least 2" in body["message"]

            # a full table does NOT start on its own in manual mode
            clients = [first] + [await Client.connect(server.port) for _ in range(5)]
            for i, c in enumerate(clients[1:]):
                await c.hello(f"bot{i}")
            await asyncio.sleep(0.3)
            assert not server.match_running
            assert all(m["type"] == "welcome" for c in clients for m in c.received)

            # the button needs the custom header (blocks cross-site requests)
            status, body = await post_start(spectators.port, header=False)
            assert "403" in status
            assert not server.match_running

            status, body = await post_start(spectators.port)
            assert "200" in status and body == {"ok": True,
                                                "message": "starting a match with 6 bots"}
            playing = [asyncio.create_task(c.play(i)) for i, c in enumerate(clients)]
            while not server.match_running:
                await asyncio.sleep(0.01)
            status, body = await post_start(spectators.port)
            assert "409" in status and "already running" in body["message"]
            ends = await asyncio.gather(*playing)
            return ends
        finally:
            task.cancel()
            await server.stop()
            await spectators.stop()

    ends = asyncio.run(main())
    assert len(ends[0]["standings"]) == 6


def test_lobby_event_reports_manual_mode(tmp_path):
    async def main():
        spectators = Spectators()
        server = Server(make_config(tmp_path), port=0, spectators=spectators,
                        manual_start=True)
        await server.start()
        # the mode is announced as soon as the server starts, before any bot
        assert spectators.lobby["manual_start"] is True
        assert spectators.lobby["players"] == []
        c = await Client.connect(server.port)
        await c.hello("x")
        await asyncio.sleep(0.05)
        lobby = spectators.lobby
        await server.stop()
        return lobby

    lobby = asyncio.run(main())
    assert lobby["manual_start"] is True and lobby["match_running"] is False
    assert lobby["min_players"] == 2 and lobby["players"][0]["name"] == "x"


def test_auto_mode_refuses_manual_start(tmp_path):
    server = Server(make_config(tmp_path), port=0)
    ok, message = server.request_start()
    assert not ok and "automatically" in message
