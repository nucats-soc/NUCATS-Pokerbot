import random

import pytest

from pokerserver.engine import Hand, IllegalAction, new_deck


def stacked_deck(*hole: str, board: str = "") -> list[str]:
    """
    Deck that deals the given hole cards (one string per PokerKit index, e.g.
    'AsKd') and then the given board, filling the rest with unused cards.
    Hole cards are dealt one at a time round the table, and one card is
    burned before each street.
    """
    holes = [[h[:2], h[2:]] for h in hole]
    order = [holes[i][0] for i in range(len(holes))] + \
            [holes[i][1] for i in range(len(holes))]
    used = set(order) | {board[i:i + 2] for i in range(0, len(board), 2)}
    spare = [c for c in new_deck(random.Random(0)) if c not in used]
    b = [board[i:i + 2] for i in range(0, len(board), 2)]
    if b:
        order += [spare.pop()] + b[:3]
        for card in b[3:]:
            order += [spare.pop(), card]
    return order + spare


def actions(hand: Hand) -> list[tuple]:
    return [(e.data["seat"], e.data["action"], e.data["amount"])
            for e in hand.drain_events() if e.type == "player_action"]


def test_blinds_three_handed():
    # Seats listed clockwise from after the button: 0 is SB, 2 is BB, 3 is button.
    hand = Hand([0, 2, 3], [1000] * 3, 10, 20, new_deck(random.Random(1)))
    assert hand.button == 3
    assert hand.blinds() == ({"seat": 0, "amount": 10}, {"seat": 2, "amount": 20})
    assert actions(hand) == [(0, "small_blind", 10), (2, "big_blind", 20)]
    assert hand.actor == 3     # under the gun = button when three-handed
    assert hand.pot == 30


def test_blinds_heads_up_button_posts_small_blind_and_acts_first():
    hand = Hand([1, 4], [1000, 1000], 10, 20, new_deck(random.Random(1)))
    sb, bb = hand.blinds()
    assert sb == {"seat": 4, "amount": 10} and bb == {"seat": 1, "amount": 20}
    assert hand.button == 4
    assert hand.actor == 4


def test_short_stack_posts_partial_blind():
    hand = Hand([0, 1, 2], [1000, 15, 1000], 10, 20, new_deck(random.Random(1)))
    assert hand.blinds()[1] == {"seat": 1, "amount": 15}
    assert hand.seat_status(1) == "all_in"


def test_legal_actions_preflop():
    hand = Hand([0, 2, 3], [1000] * 3, 10, 20, new_deck(random.Random(1)))
    assert hand.legal_actions() == {
        "fold": True, "check": False, "call": 20,
        "raise": {"min": 40, "max": 1000}}


def test_big_blind_option_cannot_fold_for_free():
    hand = Hand([0, 2, 3], [1000] * 3, 10, 20, new_deck(random.Random(1)))
    hand.apply("call")
    hand.apply("call")
    assert hand.actor == 2
    legal = hand.legal_actions()
    assert legal["check"] and not legal["fold"] and legal["call"] is None
    assert hand.default_action() == "check"
    with pytest.raises(IllegalAction):
        hand.apply("fold")


@pytest.mark.parametrize("action,amount", [
    ("raise", 25),        # below the minimum
    ("raise", 5000),      # above the stack
    ("raise", None),      # missing amount
    ("raise", 50.0),      # not an int
    ("raise", True),      # bool is not an int here
    ("check", None),      # facing a bet
    ("shove", None),      # unknown action
])
def test_illegal_actions_leave_state_untouched(action, amount):
    hand = Hand([0, 2, 3], [1000] * 3, 10, 20, new_deck(random.Random(1)))
    hand.drain_events()
    with pytest.raises(IllegalAction):
        hand.apply(action, amount)
    assert hand.actor == 3 and hand.pot == 30 and hand.drain_events() == []


def test_example_hand_from_protocol_doc():
    # Section 11: everyone limps, the big blind raises to 80, everyone folds.
    hand = Hand([0, 2, 3], [1000] * 3, 10, 20, new_deck(random.Random(1)))
    hand.drain_events()
    hand.apply("call")              # seat 3
    hand.apply("call")              # seat 0
    hand.apply("raise", 80)         # seat 2
    hand.apply("fold")              # seat 3
    hand.apply("fold")              # seat 0
    events = [e.data for e in hand.drain_events()]
    assert [(e["seat"], e["action"], e["amount"], e["stack"], e["pot"])
            for e in events] == [
        (3, "call", 20, 980, 50),
        (0, "call", 10, 980, 60),
        (2, "raise", 80, 920, 120),
        (3, "fold", 0, 980, 120),
        (0, "fold", 0, 980, 120),
    ]
    assert hand.is_over
    result = hand.result()
    assert result["showdown"] == []
    assert result["pots"] == [{"amount": 60, "winners": [{"seat": 2, "amount": 60}]}]
    assert result["results"] == [
        {"seat": 0, "net": -20, "stack": 980},
        {"seat": 2, "net": 40, "stack": 1040},
        {"seat": 3, "net": -20, "stack": 980},
    ]


def test_streets_and_showdown():
    # Index order: seat 0 (SB) AsAd, seat 1 (BB) KsKd, seat 2 (button) 2c7h.
    deck = stacked_deck("AsAd", "KsKd", "2c7h", board="Qc8d3h4s9c")
    hand = Hand([0, 1, 2], [1000] * 3, 10, 20, deck)
    assert hand.hole_cards(0) == ["As", "Ad"]
    hand.apply("fold")     # button
    hand.apply("call")     # SB
    hand.apply("check")    # BB
    streets = [e.data for e in hand.drain_events() if e.type == "street"]
    assert streets == [{"street": "flop", "board": ["Qc", "8d", "3h"], "pot": 40}]
    for _ in range(3):
        hand.apply("check")
        hand.apply("check")
    assert hand.is_over
    result = hand.result()
    assert result["board"] == ["Qc", "8d", "3h", "4s", "9c"]
    assert {"seat": 0, "cards": ["As", "Ad"]} in result["showdown"]
    assert all(s["seat"] != 2 for s in result["showdown"])   # folded, never shown
    assert result["pots"] == [{"amount": 40, "winners": [{"seat": 0, "amount": 40}]}]


def test_split_pot():
    deck = stacked_deck("2c3d", "2d3c", "4h5h", board="AsKsQsJsTs")
    hand = Hand([0, 1, 2], [1000] * 3, 10, 20, deck)
    hand.apply("fold")
    hand.apply("call")
    hand.apply("check")
    while not hand.is_over:
        hand.apply("check")
    assert hand.result()["pots"] == [{"amount": 40, "winners": [
        {"seat": 0, "amount": 20}, {"seat": 1, "amount": 20}]}]


def test_side_pots_and_all_in_runout():
    # Short stack AA wins the main pot, KK wins the side pot, QQ (covering) loses.
    deck = stacked_deck("AsAd", "KsKd", "QsQd", board="2c7h9d3c8s")
    hand = Hand([0, 1, 2], [100, 300, 1000], 10, 20, deck)
    hand.apply("raise", 1000)   # button, QQ
    hand.apply("call")          # SB all-in for 100
    hand.apply("call")          # BB all-in for 300
    assert hand.is_over
    streets = [e.data["street"] for e in hand.drain_events() if e.type == "street"]
    assert streets == ["flop", "turn", "river"]
    result = hand.result()
    assert result["pots"] == [
        {"amount": 300, "winners": [{"seat": 0, "amount": 300}]},
        {"amount": 400, "winners": [{"seat": 1, "amount": 400}]},
    ]
    assert {r["seat"]: r["stack"] for r in result["results"]} == {0: 300, 1: 400, 2: 700}
    assert len(result["showdown"]) == 3


def test_call_all_in_for_less():
    hand = Hand([0, 1, 2], [1000, 1000, 50], 10, 20, new_deck(random.Random(3)))
    hand.apply("call")         # button (50 behind) limps
    hand.apply("raise", 200)   # SB raises
    hand.apply("fold")         # BB
    assert hand.actor == 2
    assert hand.legal_actions()["call"] == 30   # all it has left
    assert hand.legal_actions()["raise"] is None


def random_action(hand: Hand, rng: random.Random) -> tuple:
    legal = hand.legal_actions()
    options = [("fold", None)] if legal["fold"] else []
    if legal["check"]:
        options.append(("check", None))
    if legal["call"] is not None:
        options.append(("call", None))
    if legal["raise"]:
        lo, hi = legal["raise"]["min"], legal["raise"]["max"]
        options.append(("raise", rng.choice([lo, hi, rng.randint(lo, hi)])))
    return rng.choice(options)


def test_fuzz_chip_conservation():
    """Thousands of random hands: chips are never created or destroyed, and
    every event the engine reports adds up."""
    rng = random.Random(42)
    for _ in range(2000):
        n = rng.randint(2, 6)
        stacks = [rng.choice([1, 15, 20, 35, 500, 1000, 3000]) for _ in range(n)]
        hand = Hand(list(range(n)), stacks, 10, 20, new_deck(rng))
        steps = 0
        while not hand.is_over:
            hand.apply(*random_action(hand, rng))
            steps += 1
            assert steps < 200
        result = hand.result()
        assert sum(r["stack"] for r in result["results"]) == sum(stacks)
        assert sum(r["net"] for r in result["results"]) == 0
        assert all(r["stack"] >= 0 for r in result["results"])
        events = hand.drain_events()
        last_pot = [e.data["pot"] for e in events if e.type == "player_action"][-1]
        won = sum(p["amount"] for p in result["pots"])
        assert won <= last_pot      # the difference is any uncalled bet returned
        assert won >= sum(r["net"] for r in result["results"] if r["net"] > 0)
        for r in result["results"]:
            from_pots = sum(w["amount"] for p in result["pots"]
                            for w in p["winners"] if w["seat"] == r["seat"])
            assert from_pots >= r["net"]
