"""Tests for the pokerbot helpers. Run from bots/ with:  python3 -m pytest tests"""
import pytest

from pokerbot import GameState, all_in, call, check, fold, raise_to
from pokerbot.actions import make_legal
from pokerbot.cards import card_name, describe_hand, hand_strength, rank


@pytest.mark.parametrize("cards,expected", [
    (["As", "Kd", "7c", "4h", "2s"], "High card"),
    (["As", "Ad", "7c", "4h", "2s"], "Pair"),
    (["As", "Ad", "7c", "7h", "2s"], "Two pair"),
    (["As", "Ad", "Ac", "7h", "2s"], "Three of a kind"),
    (["As", "2d", "3c", "4h", "5s"], "Straight"),          # ace low
    (["Ts", "Jd", "Qc", "Kh", "As"], "Straight"),          # ace high
    (["As", "9s", "7s", "4s", "2s"], "Flush"),
    (["As", "Ad", "Ac", "7h", "7s"], "Full house"),
    (["As", "Ad", "Ac", "Ah", "7s"], "Four of a kind"),
    (["5s", "6s", "7s", "8s", "9s"], "Straight flush"),
    # a flush and a straight that aren't the same cards is just a flush
    (["2s", "4s", "6s", "8s", "Ts", "9d", "7h"], "Flush"),
])
def test_describe_hand(cards, expected):
    assert describe_hand(cards[:2], cards[2:]) == expected


def test_card_helpers():
    assert rank("As") == 14 and rank("2c") == 2 and rank("Td") == 10
    assert card_name("Qh") == "Queen of Hearts"
    assert hand_strength(["As", "Ad"], []) > hand_strength(["7c", "2d"], [])
    assert 0 <= hand_strength(["7c", "2d"], []) <= 1


def state(**kw) -> GameState:
    base = dict(my_cards=["As", "Kd"], board=[], street="preflop", pot=30,
                to_call=20, can_check=False, can_raise=True, min_raise=40,
                max_raise=1000, my_stack=1000, my_bet=0, my_seat=0, opponents=[],
                history=[], hand_number=1, hands_in_match=20, button=0)
    base.update(kw)
    return GameState(**base)


@pytest.mark.parametrize("st,move,expected,has_note", [
    (state(), call(), call(), False),
    (state(), raise_to(100), raise_to(100), False),
    (state(), raise_to(5), raise_to(40), True),             # below minimum
    (state(), raise_to(5000), raise_to(1000), True),        # above maximum
    (state(), raise_to(99.9), raise_to(99), True),          # rounded down
    (state(), all_in(), raise_to(1000), False),
    (state(), check(), fold(), True),                       # can't check facing a bet
    (state(to_call=0, can_check=True), fold(), check(), True),
    (state(to_call=0, can_check=True), call(), check(), False),
    (state(can_raise=False, min_raise=0, max_raise=0), raise_to(100), call(), True),
    (state(can_raise=False, min_raise=0, max_raise=0), all_in(), call(), False),
    (state(), None, fold(), True),                          # forgot to return
    (state(to_call=0, can_check=True), "call", check(), True),
    (state(), {"action": "shove"}, fold(), True),
    (state(), raise_to("lots"), fold(), True),
])
def test_make_legal(st, move, expected, has_note):
    fixed, note = make_legal(move, st)
    assert fixed == expected
    assert (note is not None) == has_note


def test_pot_odds():
    assert state(pot=60, to_call=20).pot_odds == 0.25
    assert state(to_call=0, can_check=True).pot_odds == 0
