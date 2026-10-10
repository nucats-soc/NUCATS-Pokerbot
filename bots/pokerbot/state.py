"""
GameState: everything your bot can see when it's your turn.
"""
from dataclasses import dataclass, field


@dataclass
class Player:
    """Another player at the table (or you)."""
    name: str
    seat: int
    stack: int            # chips they have left (not counting what they've bet)
    bet: int              # chips they've put in during this betting round
    status: str           # "active", "folded", "all_in" or "out" (busted)

    @property
    def in_hand(self) -> bool:
        """Still playing this hand (hasn't folded or busted)."""
        return self.status in ("active", "all_in")


@dataclass
class PastMove:
    """Something a player did earlier in this hand."""
    name: str
    seat: int
    action: str           # "small_blind", "big_blind", "fold", "check", "call" or "raise"
    amount: int           # for "raise" this is the raise-to total
    street: str


@dataclass
class GameState:
    # --- your cards and the table ---
    my_cards: list[str]           # your two hole cards, e.g. ["As", "Kd"]
    board: list[str]              # shared cards: [] preflop, then 3, 4, 5
    street: str                   # "preflop", "flop", "turn" or "river"
    pot: int                      # all chips in the middle, including this round's bets

    # --- what you can do ---
    to_call: int                  # chips needed to call (0 = you can check)
    can_check: bool
    can_raise: bool
    min_raise: int                # smallest raise_to() amount allowed (0 if you can't raise)
    max_raise: int                # largest raise_to() amount: this is all-in

    # --- you ---
    my_stack: int                 # chips you have left
    my_bet: int                   # chips you've put in this betting round
    my_seat: int

    # --- everyone else ---
    opponents: list[Player]       # every other player at the table
    history: list[PastMove]       # every move so far this hand, oldest first

    # --- match info ---
    hand_number: int
    hands_in_match: int
    button: int                   # seat number of the dealer button

    # The original message from the server, in case you want anything else.
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def opponents_in_hand(self) -> list[Player]:
        """Opponents who haven't folded this hand."""
        return [p for p in self.opponents if p.in_hand]

    @property
    def pot_odds(self) -> float:
        """The share of the final pot you'd be paying to call.
        E.g. 0.25 means calling costs a quarter of what you could win."""
        if self.to_call == 0:
            return 0.0
        return self.to_call / (self.pot + self.to_call)

    @classmethod
    def from_request(cls, req: dict, history: list[PastMove],
                     hands_in_match: int) -> "GameState":
        legal = req["legal_actions"]
        me = next(s for s in req["seats"] if s["seat"] == req["your_seat"])
        to_call = legal["call"] or 0
        raise_range = legal["raise"] or {"min": 0, "max": 0}
        return cls(
            my_cards=req["hole_cards"],
            board=req["board"],
            street=req["street"],
            pot=req["pot"],
            to_call=to_call,
            can_check=legal["check"],
            can_raise=legal["raise"] is not None,
            min_raise=raise_range["min"],
            max_raise=raise_range["max"],
            my_stack=me["stack"],
            my_bet=me["bet"],
            my_seat=me["seat"],
            opponents=[Player(s["name"], s["seat"], s["stack"], s["bet"], s["status"])
                       for s in req["seats"] if s["seat"] != req["your_seat"]],
            history=list(history),
            hand_number=req["hand_number"],
            hands_in_match=hands_in_match,
            button=req["button"],
            raw=req,
        )
