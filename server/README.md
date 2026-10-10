# NUCATS Pokerbots table server

The server that runs the poker table. Bots connect to it over TCP using the protocol in [../docs/protocol.md](../docs/protocol.md). Python 3.12 or newer.

## Run

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"

pokerserver --port 8000                        # defaults: 6 seats, 1000 chips, 10/20, 20 hands
pokerserver --port 8000 --config config.toml   # see config.example.toml
pokerserver --port 8000 --hands 20 --timeout-ms 5000 --lobby-wait 30 --seed 42
```

`--port` is required. There is no default and it can't be set in the config file.

### Watching a match live

```bash
pokerserver --port 8000 --web-port 8080 --delay-ms 500
```

Open **http://localhost:8080/** in a browser. The page shows the table, every action as it happens, everyone's hole cards, a live chip count, the action log and the final standings.

- **`--web-port`** turns the spectator page on. Without it there's no web page.
- **With the page on, you start matches yourself.** Bots wait in the lobby until you click **Start match** on the page. They don't start automatically when the table fills or the lobby timer runs out. Any number from 2 to 6 waiting bots will do, and if more than 6 are waiting, the first 6 to connect get seats. Without `--web-port`, matches start automatically (see below).
- **`--delay-ms`** pauses after each action, street and hand (hands pause three times as long). Without it, bots finish a whole match in well under a second. The pause doesn't count against bots' move time limits.
- **The page shows every player's hole cards**, so it only listens on this machine (`127.0.0.1`) by default. `--web-host 0.0.0.0` makes it reachable from the network, but then participants on the same network can see everyone's cards. Use it only for a projector or a trusted machine. The "Show hole cards" switch hides cards on your screen, but it doesn't remove them from the data sent to the page.
- A tab opened partway through a match catches up straight away. When the server shuts down, the page keeps the final standings on screen.

Without the spectator page, a full table (6 bots) starts a match immediately. With 2 to 5 bots, the match starts once the lobby wait has passed (30 seconds by default; change it with `--lobby-wait`). When the match ends, the server prints the leaderboard (rank, name, chips left, net), disconnects every bot and exits. To run several matches in a row, use `--matches N`, or `--matches 0` to keep going until Ctrl+C; bots that are still connected go back to the lobby between matches. Each match's hand history is written to `logs/<match_id>.jsonl`.

Players connect to `<this machine's LAN IP>:<port>`. Make sure the firewall allows incoming connections on that port.

## Test

```bash
pytest
```

The tests cover:

- `tests/test_engine.py`: poker rules (blinds, heads-up order, side and split pots, all-in runouts) plus 2,000 random hands checking that no chips are created or lost
- `tests/test_match.py`: full matches with in-memory bots, covering timeouts, illegal actions, disconnects, busting, standings and reproducible seeds
- `tests/test_server.py`: end-to-end tests over real TCP sockets, including every handshake and protocol error
- `tests/test_web.py`: the spectator page and event stream, catch-up for late tabs, lobby updates and pacing

## Layout

| File | What it does |
|---|---|
| `engine.py` | One hand of poker. Wraps PokerKit and turns its state into protocol events. No I/O. |
| `match.py` | One match: seating, button, hand loop, timeouts and defaults, standings, hand log |
| `server.py` | TCP listener, hello handshake, lobby, and running matches |
| `config.py` | Settings, loaded from TOML and command-line flags |
| `web.py`, `web/index.html` | Spectator page: a small HTTP server streaming table events to the browser (Server-Sent Events). No external dependencies. |
