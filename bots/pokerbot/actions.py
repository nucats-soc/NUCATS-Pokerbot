"""
The moves your bot can make. Return one of these from decide().

    fold()           give up this hand
    check()          pass without betting (only when there's nothing to call)
    call()           match the current bet
    raise_to(100)    make your total bet this round 100 chips
    all_in()         bet everything you have

If you return a move that isn't allowed right now, it gets fixed for you
(and a note is printed) instead of being thrown away by the server.
"""


def fold() -> dict:
    return {"action": "fold"}


def check() -> dict:
    return {"action": "check"}


def call() -> dict:
    return {"action": "call"}


def raise_to(amount: int) -> dict:
    """Make your total bet for this betting round `amount` chips.
    Also used to make the first bet in a round."""
    return {"action": "raise", "amount": amount}


def all_in() -> dict:
    return {"action": "all_in"}


def make_legal(move, state) -> tuple[dict, str | None]:
    """
    Turn whatever decide() returned into a move the server will accept.
    Returns (move, note); note explains any change that was made.
    """
    safe = check() if state.can_check else fold()

    if not isinstance(move, dict) or "action" not in move:
        return safe, f"decide() returned {move!r}, not a move; used {safe['action']}"

    action = move["action"]

    if action == "all_in":
        if state.can_raise:
            return raise_to(state.max_raise), None
        return (call(), None) if state.to_call else (check(), None)

    if action == "fold":
        if state.can_check:
            return check(), "folding is pointless when you can check for free; checked instead"
        return move, None

    if action == "check":
        if not state.can_check:
            return fold(), f"can't check, there's {state.to_call} to call; folded instead"
        return move, None

    if action == "call":
        if state.can_check:
            return check(), None     # nothing to call: a check is the same thing
        return move, None

    if action == "raise":
        amount = move.get("amount")
        if not state.can_raise:
            fallback = call() if state.to_call else check()
            return fallback, f"raising isn't allowed here; used {fallback['action']}"
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            return safe, f"raise_to() needs a number, got {amount!r}; used {safe['action']}"
        fixed = max(state.min_raise, min(state.max_raise, int(amount)))
        if fixed != amount:
            return raise_to(fixed), (f"raise_to({amount}) is outside "
                                     f"{state.min_raise}-{state.max_raise}; used {fixed}")
        return raise_to(fixed), None

    return safe, f"unknown move {action!r}; used {safe['action']}"
