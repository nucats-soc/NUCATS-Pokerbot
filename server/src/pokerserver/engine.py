"""
Single-hand poker engine.

Wraps a PokerKit state for one hand of no-limit Texas Hold'em and translates
it into protocol terms (seat numbers, card strings, event dicts). Has no I/O
and no clock: the match runner drives it and decides what to send to whom.

PokerKit orders players so that the button is last: index 0 is the small
blind (or the big blind heads-up), and so on round the table. We build that
order from our seat numbers and map back with ``self.seats``.
"""
from collections import deque
from dataclasses import dataclass

from pokerkit import Automation, Card, NoLimitTexasHoldem
from pokerkit.state import (
    BlindOrStraddlePosting,
    ChipsPushing,
    Folding,
    HoleCardsShowingOrMucking,
)

STREETS = ("preflop", "flop", "turn", "river")
ACTIONS = ("fold", "check", "call", "raise")

AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.CARD_BURNING,
    Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
    Automation.HAND_KILLING,
    Automation.CHIPS_PUSHING,
    Automation.CHIPS_PULLING,
)


class IllegalAction(Exception):
    """Raised when an action is not allowed in the current state."""


def card_str(card: Card) -> str:
    return f"{card.rank}{card.suit}"


def new_deck(rng) -> list[str]:
    """A freshly shuffled 52-card deck as card strings, e.g. 'As'."""
    deck = [rank + suit for suit in "cdhs" for rank in "23456789TJQKA"]
    rng.shuffle(deck)
    return deck


@dataclass
class Event:
    """Something every player at the table should be told about."""
    type: str          # 'player_action' or 'street'
    data: dict


class Hand:
    def __init__(self, seats: list[int], stacks: list[int], small_blind: int,
                 big_blind: int, deck: list[str]):
        """
        seats:  seat numbers dealt into this hand, clockwise from the seat
                after the button, ending with the button.
        stacks: the matching stacks before blinds.
        deck:   card order to deal from (index 0 is dealt first).
        """
        if len(seats) < 2:
            raise ValueError("a hand needs at least two players")
        self.seats = list(seats)
        self.starting_stacks = list(stacks)
        self._index = {seat: i for i, seat in enumerate(self.seats)}
        self.events: list[Event] = []

        self.state = NoLimitTexasHoldem.create_state(
            AUTOMATIONS,
            True,                       # ante trimming (no antes anyway)
            0,                          # antes
            (small_blind, big_blind),
            big_blind,                  # minimum bet
            tuple(stacks),
            len(seats),
        )
        self.state.deck_cards = deque(Card.parse("".join(deck)))

        for op in self.state.operations:
            if isinstance(op, BlindOrStraddlePosting):
                self._blind_event(op)
        while self.state.can_deal_hole():
            self.state.deal_hole(1)
        self._advance()

    # ------------------------------------------------------------------ state

    @property
    def is_over(self) -> bool:
        return not self.state.status

    @property
    def button(self) -> int:
        return self.seats[-1]

    @property
    def actor(self) -> int | None:
        i = self.state.actor_index
        return None if i is None else self.seats[i]

    @property
    def street(self) -> str:
        i = self.state.street_index
        return STREETS[i] if i is not None else "river"

    @property
    def board(self) -> list[str]:
        return [card_str(c) for cards in self.state.board_cards for c in cards]

    @property
    def pot(self) -> int:
        """Chips committed this hand so far, including current-street bets."""
        return sum(self.starting_stacks) - sum(self.state.stacks)

    def hole_cards(self, seat: int) -> list[str]:
        if seat not in self._index:
            return []
        return [card_str(c) for c in self.state.hole_cards[self._index[seat]]]

    def blinds(self) -> tuple[dict, dict]:
        """(small_blind, big_blind) as {'seat', 'amount'} dicts."""
        posted = {self.seats[op.player_index]: op.amount
                  for op in self.state.operations
                  if isinstance(op, BlindOrStraddlePosting)}
        # Heads-up PokerKit puts the big blind at index 0 and the button
        # (small blind) at index 1; otherwise the small blind is index 0.
        sb, bb = ((self.seats[1], self.seats[0]) if len(self.seats) == 2
                  else (self.seats[0], self.seats[1]))
        return ({"seat": sb, "amount": posted.get(sb, 0)},
                {"seat": bb, "amount": posted.get(bb, 0)})

    def seat_status(self, seat: int) -> str:
        i = self._index[seat]
        if not self.state.statuses[i]:
            return "folded"
        if self.state.stacks[i] == 0:
            return "all_in"
        return "active"

    def seat_view(self, seat: int) -> dict:
        i = self._index[seat]
        return {"seat": seat, "stack": self.state.stacks[i],
                "bet": self.state.bets[i], "status": self.seat_status(seat)}

    # ---------------------------------------------------------------- actions

    def legal_actions(self) -> dict:
        s = self.state
        call = s.checking_or_calling_amount if s.can_check_or_call() else None
        can_raise = s.can_complete_bet_or_raise_to()
        return {
            "fold": s.can_fold(),
            "check": call == 0,
            "call": call if call else None,
            "raise": ({"min": s.min_completion_betting_or_raising_to_amount,
                       "max": s.max_completion_betting_or_raising_to_amount}
                      if can_raise else None),
        }

    def default_action(self) -> str:
        return "check" if self.legal_actions()["check"] else "fold"

    def apply(self, action: str, amount=None, auto: str | None = None) -> None:
        """
        Apply an action for the current actor. Raises IllegalAction (leaving
        the state untouched) if the action isn't allowed.
        """
        if self.is_over or self.state.actor_index is None:
            raise IllegalAction("no player is due to act")
        legal = self.legal_actions()
        i = self.state.actor_index
        stack, bet = self.state.stacks[i], self.state.bets[i]
        pot = self.pot

        if action == "fold":
            if not legal["fold"]:
                raise IllegalAction("cannot fold when checking is free")
            self.state.fold()
            added, amount = 0, 0
        elif action == "check":
            if not legal["check"]:
                raise IllegalAction("cannot check, there is a bet to call")
            self.state.check_or_call()
            added, amount = 0, 0
        elif action == "call":
            if legal["call"] is None:
                raise IllegalAction("nothing to call, check instead")
            added = amount = legal["call"]
            self.state.check_or_call()
        elif action == "raise":
            bounds = legal["raise"]
            if bounds is None:
                raise IllegalAction("raising is not allowed here")
            if type(amount) is not int:
                raise IllegalAction("raise needs an integer 'amount'")
            if not bounds["min"] <= amount <= bounds["max"]:
                raise IllegalAction(
                    f"raise to {amount} is outside the allowed range "
                    f"{bounds['min']}-{bounds['max']}")
            self.state.complete_bet_or_raise_to(amount)
            added = amount - bet
        else:
            raise IllegalAction(f"unknown action {action!r}")

        # Computed by hand: if this action ends the hand, PokerKit has already
        # paid out and its stacks no longer show the mid-hand position.
        self.events.append(Event("player_action", {
            "seat": self.seats[i], "action": action, "amount": amount,
            "stack": stack - added, "pot": pot + added, "auto": auto,
        }))
        self._advance()

    def drain_events(self) -> list[Event]:
        events, self.events = self.events, []
        return events

    # ----------------------------------------------------------------- result

    def result(self) -> dict:
        """Showdown, pots and per-seat results. Only valid once is_over."""
        shown = []
        pots: dict[int, dict] = {}
        payoffs = self.state.payoffs
        folds = sum(isinstance(op, Folding) for op in self.state.operations)
        if folds == len(self.seats) - 1:
            # Everyone else folded. PokerKit hands the winner's own bet on the
            # final street back rather than pushing it, so work the pot out
            # here: the winner matched the biggest loser's contribution, and
            # anything beyond that was an uncalled bet returned to them.
            winner = next(i for i, p in enumerate(payoffs) if p > 0) \
                if any(p > 0 for p in payoffs) else None
            lost = [-p for p in payoffs if p < 0]
            amount = sum(lost) + max(lost, default=0)
            return self._result([], [{"amount": amount, "winners": [
                {"seat": self.seats[winner], "amount": amount}]}]
                if winner is not None else [])

        for op in self.state.operations:
            if isinstance(op, HoleCardsShowingOrMucking) and op.hole_cards:
                shown.append({"seat": self.seats[op.player_index],
                              "cards": [card_str(c) for c in op.hole_cards]})
            elif isinstance(op, ChipsPushing):
                pot = pots.setdefault(op.pot_index, {"amount": 0, "winners": []})
                for i, won in enumerate(op.amounts):
                    if won:
                        pot["amount"] += won
                        pot["winners"].append({"seat": self.seats[i], "amount": won})
        return self._result(shown, [pots[k] for k in sorted(pots)])

    def _result(self, shown: list[dict], pots: list[dict]) -> dict:
        return {
            "board": self.board,
            "showdown": shown,
            "pots": pots,
            "results": sorted(
                ({"seat": seat, "net": self.state.payoffs[i],
                  "stack": self.state.stacks[i]}
                 for i, seat in enumerate(self.seats)),
                key=lambda r: r["seat"]),
        }

    # -------------------------------------------------------------- internals

    def _blind_event(self, op: BlindOrStraddlePosting) -> None:
        sb, _ = self.blinds()
        seat = self.seats[op.player_index]
        # Blinds are posted before anything else, so the pot is just the
        # blinds posted so far.
        pot = sum(e.data["amount"] for e in self.events) + op.amount
        self.events.append(Event("player_action", {
            "seat": seat,
            "action": "small_blind" if seat == sb["seat"] else "big_blind",
            "amount": op.amount,
            "stack": self.starting_stacks[op.player_index] - op.amount,
            "pot": pot, "auto": None,
        }))

    def _advance(self) -> None:
        """Deal any board cards that are due (including all-in run-outs)."""
        while self.state.status and self.state.actor_index is None \
                and self.state.can_deal_board():
            pot = self.pot
            self.state.deal_board(self.state.board_dealing_count)
            self.events.append(Event("street", {
                # 3 board cards = flop, 4 = turn, 5 = river
                "street": STREETS[len(self.state.board_cards) - 2],
                "board": self.board, "pot": pot,
            }))
