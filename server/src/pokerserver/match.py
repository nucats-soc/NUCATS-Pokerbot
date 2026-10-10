"""
Runs one match: seats the players, plays up to hands_per_match hands, and
returns the standings. Talks to players only through the Player protocol, so
it can be driven by real TCP connections or by in-process fakes in tests.
"""
import asyncio
import json
import logging
import os
import random
from typing import Callable, Protocol

from .config import Config
from .engine import ACTIONS, Hand, IllegalAction, new_deck

logger = logging.getLogger(__name__)


class Player(Protocol):
    player_id: str
    name: str

    @property
    def connected(self) -> bool: ...

    async def send(self, message: dict) -> None:
        """Send a message; silently does nothing once disconnected."""

    async def request_action(self, message: dict, timeout: float) -> dict | None:
        """Send an action_request and wait for the matching action message.
        Returns None on timeout or disconnect."""


class Seat:
    def __init__(self, number: int, player: Player, stack: int):
        self.number = number
        self.player = player
        self.stack = stack
        self.busted_in: int | None = None   # hand number they lost their last chip


class MatchLog:
    """Hand history: every message sent, plus private info, as JSON lines."""

    def __init__(self, log_dir: str | None, match_id: str):
        self._file = None
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            self._file = open(os.path.join(log_dir, f"{match_id}.jsonl"), "w")

    def write(self, record: dict) -> None:
        if self._file:
            self._file.write(json.dumps(record) + "\n")
            self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()


class Match:
    def __init__(self, match_id: str, players: list[Player], config: Config,
                 seed: int | None = None, log_dir: str | None = None,
                 on_event: Callable[[dict], None] | None = None):
        """on_event, if given, receives every logged record (all hole cards
        included, deck order excluded) plus 'to_act' records, for spectators."""
        if not 2 <= len(players) <= config.max_players:
            raise ValueError(f"a match needs 2-{config.max_players} players")
        self.match_id = match_id
        self.config = config
        self.seed = seed if seed is not None else random.randrange(2**63)
        self.rng = random.Random(self.seed)
        self.log = MatchLog(log_dir, match_id)
        self.on_event = on_event

        numbers = self.rng.sample(range(config.max_players), len(players))
        self.seats = sorted(
            (Seat(n, p, config.starting_stack) for n, p in zip(numbers, players)),
            key=lambda s: s.number)
        self.button: int | None = None
        self.hands_played = 0
        self._request_id = 0

    # ------------------------------------------------------------------ comms

    def _seat_info(self, seat: Seat) -> dict:
        info = {"seat": seat.number, "player_id": seat.player.player_id,
                "name": seat.player.name}
        if not seat.player.connected:
            info["connected"] = False
        return info

    def _record(self, record: dict) -> None:
        """Write to the hand history and tell any spectators."""
        self.log.write(record)
        if self.on_event:
            self.on_event({k: v for k, v in record.items() if k != "deck"})

    async def _broadcast(self, message: dict) -> None:
        message = {"type": message["type"], "match_id": self.match_id, **message}
        self._record(message)
        for seat in self.seats:
            await seat.player.send(message)
        # Optional pause so people watching can follow along.
        if self.config.action_delay_ms and message["type"] in (
                "player_action", "street", "hand_end"):
            pause = self.config.action_delay_ms / 1000
            await asyncio.sleep(pause * 3 if message["type"] == "hand_end" else pause)

    async def _send_events(self, hand: Hand, hand_number: int) -> None:
        for event in hand.drain_events():
            await self._broadcast({"type": event.type, "hand_number": hand_number,
                                   **event.data})

    # ------------------------------------------------------------------- flow

    async def run(self) -> list[dict]:
        self._record({"type": "match_info", "match_id": self.match_id,
                      "seed": self.seed, "config": self.config.public()})
        try:
            await self._broadcast_match_start()
            for hand_number in range(1, self.config.hands_per_match + 1):
                if sum(1 for s in self.seats if s.stack > 0) < 2:
                    break
                await self._play_hand(hand_number)
                self.hands_played = hand_number
            standings = self.standings()
            await self._broadcast({"type": "match_end",
                                   "hands_played": self.hands_played,
                                   "standings": standings})
            return standings
        finally:
            self.log.close()

    async def _broadcast_match_start(self) -> None:
        seats = [self._seat_info(s) for s in self.seats]
        self._record({"type": "match_start", "match_id": self.match_id,
                      "seats": seats})
        for seat in self.seats:
            await seat.player.send({
                "type": "match_start", "match_id": self.match_id,
                "your_seat": seat.number, "seats": seats,
                "config": self.config.public(),
            })

    def _next_button(self) -> int:
        live = [s.number for s in self.seats if s.stack > 0]
        if self.button is None:
            return self.rng.choice(live)
        later = [n for n in live if n > self.button]
        return later[0] if later else live[0]

    async def _play_hand(self, hand_number: int) -> None:
        self.button = self._next_button()
        live = [s for s in self.seats if s.stack > 0]
        # Rotate so the order starts after the button and ends on it.
        b = next(i for i, s in enumerate(live) if s.number == self.button)
        order = live[b + 1:] + live[:b + 1]
        deck = new_deck(self.rng)

        hand = Hand([s.number for s in order], [s.stack for s in order],
                    self.config.small_blind, self.config.big_blind, deck)
        by_number = {s.number: s for s in self.seats}
        sb, bb = hand.blinds()

        seats = []
        for s in self.seats:
            seats.append({**self._seat_info(s), "stack": s.stack,
                          "status": "active" if s.stack > 0 else "out"})
        base = {"type": "hand_start", "match_id": self.match_id,
                "hand_number": hand_number, "button": self.button,
                "small_blind": sb, "big_blind": bb, "seats": seats}
        self._record({**base, "deck": deck,
                      "hole_cards": {s.number: hand.hole_cards(s.number)
                                     for s in order}})
        for s in self.seats:
            await s.player.send({**base, "hole_cards": hand.hole_cards(s.number)})
        await self._send_events(hand, hand_number)

        while not hand.is_over:
            seat = by_number[hand.actor]
            await self._take_turn(hand, hand_number, seat)
            await self._send_events(hand, hand_number)

        result = hand.result()
        for r in result["results"]:
            seat = by_number[r["seat"]]
            seat.stack = r["stack"]
            if seat.stack == 0:
                seat.busted_in = hand_number
        await self._broadcast({"type": "hand_end", "hand_number": hand_number,
                               **result})

    async def _take_turn(self, hand: Hand, hand_number: int, seat: Seat) -> None:
        player = seat.player
        if not player.connected:
            hand.apply(hand.default_action(), auto="disconnected")
            return

        self._request_id += 1
        request_id = self._request_id
        seats = []
        for s in self.seats:
            if s.number in hand.seats:
                seats.append({**self._seat_info(s), **hand.seat_view(s.number)})
            else:
                seats.append({**self._seat_info(s), "stack": s.stack, "bet": 0,
                              "status": "out"})
        request = {
            "type": "action_request", "match_id": self.match_id,
            "hand_number": hand_number, "request_id": request_id,
            "timeout_ms": self.config.action_timeout_ms,
            "street": hand.street, "board": hand.board,
            "hole_cards": hand.hole_cards(seat.number), "pot": hand.pot,
            "button": hand.button, "your_seat": seat.number, "seats": seats,
            "legal_actions": hand.legal_actions(),
        }
        if self.on_event:
            self.on_event({"type": "to_act", "match_id": self.match_id,
                           "hand_number": hand_number, "seat": seat.number,
                           "timeout_ms": self.config.action_timeout_ms,
                           "legal_actions": request["legal_actions"]})
        reply = await player.request_action(
            request, self.config.action_timeout_ms / 1000)

        if reply is None:
            if not player.connected:
                hand.apply(hand.default_action(), auto="disconnected")
            else:
                await player.send({"type": "error", "code": "timeout",
                                   "message": "no action received in time; "
                                              "default action applied",
                                   "fatal": False})
                hand.apply(hand.default_action(), auto="timeout")
            return

        action, amount = reply.get("action"), reply.get("amount")
        try:
            if action not in ACTIONS:
                raise IllegalAction(f"action must be one of {', '.join(ACTIONS)}")
            hand.apply(action, amount)
        except IllegalAction as e:
            await player.send({"type": "error", "code": "illegal_action",
                               "message": f"{e}; default action applied",
                               "fatal": False})
            hand.apply(hand.default_action(), auto="invalid")

    # -------------------------------------------------------------- standings

    def standings(self) -> list[dict]:
        def key(s: Seat):
            # Anyone with chips beats anyone busted; busting later beats earlier.
            return (s.stack, float("inf") if s.busted_in is None else s.busted_in)

        ordered = sorted(self.seats, key=key, reverse=True)
        standings, rank = [], 0
        for i, s in enumerate(ordered):
            if i == 0 or key(s) != key(ordered[i - 1]):
                rank = i + 1
            standings.append({**self._seat_info(s), "rank": rank, "stack": s.stack,
                              "net": s.stack - self.config.starting_stack})
        return standings
