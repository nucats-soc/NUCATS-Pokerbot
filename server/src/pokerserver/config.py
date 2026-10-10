import tomllib
from dataclasses import dataclass, fields


@dataclass
class Config:
    host: str = "0.0.0.0"

    max_players: int = 6
    min_players: int = 2
    starting_stack: int = 1000
    small_blind: int = 10
    big_blind: int = 20
    hands_per_match: int = 20

    action_timeout_ms: int = 5000
    hello_timeout_s: float = 10.0
    # Once min_players are waiting, start after this long even if the table
    # isn't full. A full table always starts straight away.
    lobby_wait_s: float = 30.0
    # Pause after each action, street and hand (x3) so humans can watch.
    # Doesn't count against bots' action timeouts. 0 = full speed.
    action_delay_ms: int = 0
    # Stop after this many matches and disconnect everyone; 0 means keep running.
    max_matches: int = 1

    log_dir: str = "logs"
    # Fix for reproducible matches; None draws a fresh seed per match.
    seed: int | None = None

    def public(self) -> dict:
        """The subset sent to bots in welcome and match_start."""
        return {
            "max_players": self.max_players,
            "starting_stack": self.starting_stack,
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
            "hands_per_match": self.hands_per_match,
            "action_timeout_ms": self.action_timeout_ms,
        }

    def validate(self) -> None:
        if not 2 <= self.min_players <= self.max_players <= 6:
            raise ValueError("need 2 <= min_players <= max_players <= 6")
        if not 0 < self.small_blind <= self.big_blind:
            raise ValueError("need 0 < small_blind <= big_blind")
        if self.starting_stack <= 0 or self.hands_per_match <= 0:
            raise ValueError("starting_stack and hands_per_match must be positive")
        if self.action_delay_ms < 0:
            raise ValueError("action_delay_ms can't be negative")
        if self.action_timeout_ms <= 0:
            raise ValueError("action_timeout_ms must be positive")

    @classmethod
    def load(cls, path: str | None = None, **overrides) -> "Config":
        values = {}
        if path:
            with open(path, "rb") as f:
                values.update(tomllib.load(f))
        values.update({k: v for k, v in overrides.items() if v is not None})
        known = {f.name for f in fields(cls)}
        unknown = set(values) - known
        if unknown:
            hint = " (the port is set with --port, not in the file)" \
                if "port" in unknown else ""
            raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}{hint}")
        config = cls(**values)
        config.validate()
        return config
