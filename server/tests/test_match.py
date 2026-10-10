import asyncio
import random

import pytest

from pokerserver.config import Config
from pokerserver.match import Match, Seat


def random_reply(request: dict, rng: random.Random) -> dict:
    legal = request["legal_actions"]
    options = []
    if legal["fold"]:
        options.append({"action": "fold"})
    if legal["check"]:
        options.append({"action": "check"})
    if legal["call"] is not None:
        options.append({"action": "call"})
    if legal["raise"]:
        lo, hi = legal["raise"]["min"], legal["raise"]["max"]
        options.append({"action": "raise", "amount": rng.choice([lo, hi])})
    return {"type": "action", "request_id": request["request_id"],
            **rng.choice(options)}


class FakePlayer:
    """In-process stand-in for a TCP connection."""

    def __init__(self, player_id: str, strategy=None, seed: int = 0):
        self.player_id = player_id
        self.name = f"bot-{player_id}"
        self.strategy = strategy or (lambda req, rng: random_reply(req, rng))
        self.rng = random.Random(seed)
        self.received: list[dict] = []
        self._connected = True

    @property
    def connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    async def send(self, message: dict) -> None:
        if self._connected:
            self.received.append(message)

    async def request_action(self, message: dict, timeout: float):
        if not self._connected:
            return None
        self.received.append(message)
        return self.strategy(message, self.rng)

    def of_type(self, kind: str) -> list[dict]:
        return [m for m in self.received if m["type"] == kind]


def run(match: Match) -> list[dict]:
    return asyncio.run(match.run())


def config(**kw) -> Config:
    return Config(**{"action_timeout_ms": 50, **kw})


def test_full_six_player_match_conserves_chips():
    players = [FakePlayer(f"p{i}", seed=i) for i in range(6)]
    match = Match("m1", players, config(), seed=7)
    standings = run(match)

    assert sum(s["stack"] for s in standings) == 6000
    assert sum(s["net"] for s in standings) == 0
    assert sorted(s["seat"] for s in standings) == sorted(s.number for s in match.seats)
    for p in players:
        assert p.of_type("match_start")[0]["your_seat"] in range(6)
        assert len(p.of_type("match_end")) == 1
        # every hand_end keeps the table total at 6000
        for end in p.of_type("hand_end"):
            dealt = {r["seat"] for r in end["results"]}
            out = sum(s.stack for s in match.seats if s.number not in dealt)
            assert out == 0
    assert 1 <= match.hands_played <= 20


def test_hand_count_and_button_rotation():
    players = [FakePlayer(f"p{i}", strategy=lambda r, _: {
        "type": "action", "request_id": r["request_id"],
        "action": "check" if r["legal_actions"]["check"] else "call"}) for i in range(3)]
    match = Match("m1", players, config(hands_per_match=7), seed=3)
    run(match)
    assert match.hands_played == 7
    buttons = [m["button"] for m in players[0].of_type("hand_start")]
    seats = sorted(s.number for s in match.seats)
    for prev, nxt in zip(buttons, buttons[1:]):
        assert nxt == seats[(seats.index(prev) + 1) % len(seats)]


def test_private_hole_cards_only_go_to_their_owner():
    players = [FakePlayer(f"p{i}", seed=i) for i in range(4)]
    match = Match("m1", players, config(hands_per_match=3), seed=1)
    run(match)
    for p in players:
        mine = [tuple(m["hole_cards"]) for m in p.of_type("hand_start")]
        assert all(len(h) in (0, 2) for h in mine)   # empty once busted
        for other in players:
            if other is not p:
                theirs = [tuple(m["hole_cards"]) for m in other.of_type("hand_start")]
                assert all(a != b for a, b in zip(mine, theirs) if a)
        # action requests never leak other players' cards
        for req in p.of_type("action_request"):
            assert "hole_cards" not in str(req["seats"])


def test_timeout_applies_default_action():
    slow = FakePlayer("slow", strategy=lambda r, _: None)   # never answers
    players = [slow, FakePlayer("a"), FakePlayer("b")]
    match = Match("m1", players, config(hands_per_match=2), seed=5)
    run(match)
    errors = slow.of_type("error")
    assert errors and all(e["code"] == "timeout" and not e["fatal"] for e in errors)
    autos = [m for m in players[1].of_type("player_action") if m["auto"] == "timeout"]
    assert len(autos) == len(errors)
    assert all(a["action"] in ("check", "fold") for a in autos)


@pytest.mark.parametrize("bad", [
    {"action": "raise", "amount": 1},
    {"action": "raise"},
    {"action": "dance"},
    {},
])
def test_illegal_action_applies_default(bad):
    cheat = FakePlayer("cheat", strategy=lambda r, _: {
        "type": "action", "request_id": r["request_id"], **bad})
    players = [cheat, FakePlayer("a")]
    match = Match("m1", players, config(hands_per_match=1), seed=2)
    run(match)
    errors = cheat.of_type("error")
    assert errors and errors[0]["code"] == "illegal_action"
    autos = [m for m in players[1].of_type("player_action") if m["auto"] == "invalid"]
    assert autos


def test_disconnected_player_keeps_seat_and_auto_acts():
    quitter = FakePlayer("quitter")
    players = [quitter, FakePlayer("a", seed=1), FakePlayer("b", seed=2)]

    def strategy(req, rng):
        quitter.disconnect()     # drop on the first request
        return None

    quitter.strategy = strategy
    match = Match("m1", players, config(hands_per_match=5), seed=9)
    standings = run(match)
    autos = [m for m in players[1].of_type("player_action")
             if m["auto"] == "disconnected"]
    assert autos
    entry = next(s for s in standings if s["player_id"] == "quitter")
    assert entry["connected"] is False
    assert sum(s["stack"] for s in standings) == 3000


def test_match_ends_early_when_one_player_has_chips():
    shover = lambda r, _: {"type": "action", "request_id": r["request_id"],
                           **({"action": "raise", "amount": r["legal_actions"]["raise"]["max"]}
                              if r["legal_actions"]["raise"] else
                              {"action": "call"} if r["legal_actions"]["call"] else
                              {"action": "check"})}
    players = [FakePlayer(f"p{i}", strategy=shover) for i in range(2)]
    match = Match("m1", players, config(), seed=4)
    standings = run(match)
    assert match.hands_played < 20
    assert [s["stack"] for s in standings] == [2000, 0]
    assert [s["rank"] for s in standings] == [1, 2]


def test_standings_ranks_ties_and_bust_order():
    cfg = config()
    players = [FakePlayer(f"p{i}") for i in range(5)]
    match = Match("m1", players, cfg, seed=0)
    stacks = {0: (1500, None), 1: (1500, None), 2: (0, 4), 3: (0, 9), 4: (0, 9)}
    match.seats = []
    for n, (stack, busted) in stacks.items():
        seat = Seat(n, players[n], stack)
        seat.busted_in = busted
        match.seats.append(seat)
    by_seat = {s["seat"]: s["rank"] for s in match.standings()}
    assert by_seat == {0: 1, 1: 1, 3: 3, 4: 3, 2: 5}


def test_seed_makes_matches_reproducible():
    def play():
        players = [FakePlayer(f"p{i}", seed=i) for i in range(4)]
        run(Match("m1", players, config(hands_per_match=5), seed=123))
        return players[0].received

    assert play() == play()


def test_heads_up_preflop_order():
    players = [FakePlayer(f"p{i}", seed=i) for i in range(2)]
    run(Match("m1", players, config(hands_per_match=4), seed=8))
    for p in players:
        for start in p.of_type("hand_start"):
            assert start["small_blind"]["seat"] == start["button"]
    # first preflop action after the blinds is always the button's
    msgs = players[0].received
    for i, m in enumerate(msgs):
        if m["type"] == "hand_start":
            acts = [x for x in msgs[i + 1:] if x["type"] == "player_action"]
            assert acts[2]["seat"] == m["button"]
