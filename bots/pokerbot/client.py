"""
Connects your bot to the table server and runs the game loop.
You don't need to read or change this file.
"""
import argparse
import json
import socket
import sys
import threading
import traceback

from .actions import make_legal
from .state import GameState, PastMove


def play(decide, host: str, port: int, name: str, verbose: bool = True) -> None:
    """Connect, play until the server closes the connection, then return."""
    log = (lambda *a: print(f"[{name}]", *a, flush=True)) if verbose else (lambda *a: None)

    try:
        sock = socket.create_connection((host, port))
    except OSError as e:
        print(f"[{name}] Couldn't connect to {host}:{port} ({e}).\n"
              f"        Is the server running, and are the host and port right?")
        return
    f = sock.makefile("rw", encoding="utf-8")

    def send(message: dict) -> None:
        f.write(json.dumps(message) + "\n")
        f.flush()

    send({"type": "hello", "protocol": 1, "name": name})

    my_seat = None
    names: dict[int, str] = {}
    history: list[PastMove] = []
    street = "preflop"
    hands_in_match = 0

    for line in f:
        msg = json.loads(line)
        kind = msg["type"]

        if kind == "welcome":
            hands_in_match = msg["config"]["hands_per_match"]
            log(f"Connected as {msg['player_id']}. Waiting for a match to start...")

        elif kind == "match_start":
            my_seat = msg["your_seat"]
            names = {s["seat"]: s["name"] for s in msg["seats"]}
            log(f"Match started! I'm in seat {my_seat} with {len(names)} players.")

        elif kind == "hand_start":
            history, street = [], "preflop"
            cards = " ".join(msg["hole_cards"]) or "(no cards, I'm out)"
            log(f"Hand {msg['hand_number']}: I have {cards}")

        elif kind == "street":
            street = msg["street"]

        elif kind == "player_action":
            history.append(PastMove(names.get(msg["seat"], "?"), msg["seat"],
                                    msg["action"], msg["amount"], street))

        elif kind == "action_request":
            state = GameState.from_request(msg, history, hands_in_match)
            try:
                move, note = make_legal(decide(state), state)
            except Exception:
                print(f"[{name}] Your decide() crashed! Here's the error:", flush=True)
                traceback.print_exc(file=sys.stdout)
                move, note = make_legal(None, state)
                note = f"decide() crashed, so used {move['action']}"
            send({"type": "action", "request_id": msg["request_id"], **move})
            if note:
                log(f"  Note: {note}")
            amount = f" {move['amount']}" if move["action"] == "raise" else ""
            log(f"  {state.street:7} board [{' '.join(state.board)}] "
                f"pot {state.pot} -> {move['action']}{amount}")

        elif kind == "hand_end":
            me = next((r for r in msg["results"] if r["seat"] == my_seat), None)
            if me:
                log(f"  Result: {me['net']:+d} chips, I now have {me['stack']}")

        elif kind == "match_end":
            log("Match over! Final standings:")
            for s in msg["standings"]:
                log(f"  #{s['rank']} {s['name']:12} {s['stack']:5} ({s['net']:+d})")

        elif kind == "error":
            log(f"  Server says: {msg['code']}: {msg['message']}")

    log("Disconnected.")


def run(decide, name: str = "MyBot") -> None:
    """Read the command line options and start the bot."""
    parser = argparse.ArgumentParser(description="Run your NUCATS Pokerbots bot")
    parser.add_argument("--host", default="127.0.0.1",
                        help="server address (default: this computer)")
    parser.add_argument("--port", type=int, required=True, help="server port")
    parser.add_argument("--name", default=name, help=f"bot name (default: {name})")
    parser.add_argument("--count", type=int, default=1,
                        help="run this many copies at once, for testing")
    parser.add_argument("--quiet", action="store_true",
                        help="with --count, only the first copy prints")
    args = parser.parse_args()

    threads = []
    for i in range(args.count):
        bot_name = args.name if args.count == 1 else f"{args.name}{i + 1}"
        verbose = not args.quiet or i == 0
        t = threading.Thread(target=play, daemon=True,
                             args=(decide, args.host, args.port, bot_name, verbose))
        t.start()
        threads.append(t)
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        pass
