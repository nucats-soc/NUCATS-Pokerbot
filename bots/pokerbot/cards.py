"""
Card helpers.

A card is a 2-character string: rank then suit.
    "As" = Ace of spades, "Td" = Ten of diamonds, "2c" = Two of clubs

Ranks: 2 3 4 5 6 7 8 9 T J Q K A
Suits: c (clubs), d (diamonds), h (hearts), s (spades)
"""
from collections import Counter

RANKS = "23456789TJQKA"
RANK_NAMES = {"2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six",
              "7": "Seven", "8": "Eight", "9": "Nine", "T": "Ten", "J": "Jack",
              "Q": "Queen", "K": "King", "A": "Ace"}
SUIT_NAMES = {"c": "Clubs", "d": "Diamonds", "h": "Hearts", "s": "Spades"}

# Hand types from weakest to strongest
HAND_TYPES = ["High card", "Pair", "Two pair", "Three of a kind", "Straight",
              "Flush", "Full house", "Four of a kind", "Straight flush"]


def rank(card: str) -> int:
    """The card's rank as a number: 2-10, Jack = 11, Queen = 12, King = 13, Ace = 14.

    >>> rank("As")
    14
    """
    return RANKS.index(card[0]) + 2


def suit(card: str) -> str:
    """The card's suit letter: 'c', 'd', 'h' or 's'."""
    return card[1]


def card_name(card: str) -> str:
    """A readable name, e.g. 'Ace of Spades'."""
    return f"{RANK_NAMES[card[0]]} of {SUIT_NAMES[card[1]]}"


def is_pair(cards: list[str]) -> bool:
    """True if the two cards have the same rank, e.g. ["Kh", "Kd"]."""
    return len(cards) == 2 and rank(cards[0]) == rank(cards[1])


def is_suited(cards: list[str]) -> bool:
    """True if the two cards have the same suit, e.g. ["Ah", "7h"]."""
    return len(cards) == 2 and suit(cards[0]) == suit(cards[1])


def _has_straight(ranks: set[int]) -> bool:
    if 14 in ranks:
        ranks = ranks | {1}          # an Ace can also be low: A-2-3-4-5
    return any(all(r + i in ranks for i in range(5)) for r in range(1, 11))


def hand_type(cards: list[str]) -> int:
    """
    The best hand type you can make from these cards, as an index into
    HAND_TYPES (0 = High card ... 8 = Straight flush).
    Pass in your hole cards plus the board.
    """
    if not cards:
        return 0
    counts = sorted(Counter(rank(c) for c in cards).values(), reverse=True)
    by_suit = Counter(suit(c) for c in cards)
    flush_suit, flush_count = by_suit.most_common(1)[0]
    flush = flush_count >= 5
    straight = _has_straight({rank(c) for c in cards})

    if flush and _has_straight({rank(c) for c in cards if suit(c) == flush_suit}):
        return 8
    if counts[0] == 4:
        return 7
    if counts[0] == 3 and len(counts) > 1 and counts[1] >= 2:
        return 6
    if flush:
        return 5
    if straight:
        return 4
    if counts[0] == 3:
        return 3
    if counts[0] == 2 and len(counts) > 1 and counts[1] == 2:
        return 2
    if counts[0] == 2:
        return 1
    return 0


def describe_hand(my_cards: list[str], board: list[str]) -> str:
    """The name of your best hand so far, e.g. 'Two pair'."""
    return HAND_TYPES[hand_type(my_cards + board)]


def hand_strength(my_cards: list[str], board: list[str]) -> float:
    """
    A rough score for your hand from 0 (terrible) to 1 (unbeatable).

    This is deliberately simple, so improving it is a great place to start.
    It doesn't know whether the board helps your opponents just as much.
    """
    kind = hand_type(my_cards + board)
    high = max(rank(c) for c in my_cards) if my_cards else 2
    low = min(rank(c) for c in my_cards) if my_cards else 2

    if kind >= 2:
        # Two pair or better: 0.6 up to 1.0
        return 0.6 + 0.05 * kind
    if kind == 1:
        # One pair: 0.4-0.5, higher for a higher card in your hand
        return 0.4 + 0.1 * (high - 2) / 12

    # Nothing made yet: judge your two cards
    score = 0.1 + 0.15 * (high - 2) / 12 + 0.1 * (low - 2) / 12
    if is_suited(my_cards):
        score += 0.05
    if abs(high - low) == 1:
        score += 0.05                # connected, e.g. 9 and 10
    return score
