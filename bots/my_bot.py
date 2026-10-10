"""
My poker bot!

This is the only file you need to edit. Change decide() to change how your
bot plays.

Run it (the organiser will tell you the host and port):
    python3 my_bot.py --host 192.168.1.20 --port 8000

Practise against 3 copies of itself on your own computer (start the server
first: see "Practising on your own computer" in README.md):
    python3 my_bot.py --port 8000 --count 4 --quiet

On Windows, type `python` (or `py`) instead of `python3`.
"""
from pokerbot import all_in, call, check, fold, raise_to, run
from pokerbot.cards import describe_hand, hand_strength, is_pair, rank

BOT_NAME = "MyBot"   # change this to your team's name!


def decide(state):
    """
    Called every time it's your turn. Look at `state`, then return ONE of:

        fold()          give up this hand
        check()         pass without betting (only if state.can_check)
        call()          match the current bet (costs state.to_call chips)
        raise_to(100)   make your total bet this round 100 chips
        all_in()        bet everything

    Handy things in `state`:
        state.my_cards     your two cards, e.g. ["As", "Kd"]
        state.board        the shared cards on the table
        state.street       "preflop", "flop", "turn" or "river"
        state.pot          chips in the middle
        state.to_call      chips you need to call (0 means you can check)
        state.can_check    True if checking is allowed
        state.can_raise    True if raising is allowed
        state.min_raise    smallest amount you can raise_to()
        state.max_raise    largest amount you can raise_to() (all-in)
        state.my_stack     chips you have left
        state.opponents    the other players (name, stack, bet, status)
        state.history      everything that has happened this hand
        state.pot_odds     how much calling costs compared to the pot

    And from pokerbot.cards:
        hand_strength(my_cards, board)   a score from 0 (bad) to 1 (amazing)
        describe_hand(my_cards, board)   e.g. "Two pair"
        rank("As") -> 14,  is_pair(["Kh", "Kd"]) -> True

    Don't worry about making an illegal move: it gets fixed for you, and a
    note is printed so you can see what happened.
    """
    strength = hand_strength(state.my_cards, state.board)

    # 1. Big hand: raise! Before the flop, a high pair (nines or better) is
    #    strong. After the flop, look for three of a kind or better.
    if state.street == "preflop":
        strong = is_pair(state.my_cards) and rank(state.my_cards[0]) >= 9
    else:
        strong = strength >= 0.7

    if strong and state.can_raise:
        # Raise to three times the minimum, but never more than we're allowed
        return raise_to(min(state.min_raise * 3, state.max_raise))

    # 2. Free to stay in? Then check.
    if state.can_check:
        return check()

    # 3. Decent hand, or calling is cheap compared to the pot: call.
    if strength >= 0.3 or state.pot_odds <= 0.2:
        return call()

    # 4. Otherwise, give up.
    return fold()


# ----------------------------------------------------------------------
# You don't need to change anything below this line.
if __name__ == "__main__":
    run(decide, name=BOT_NAME)
