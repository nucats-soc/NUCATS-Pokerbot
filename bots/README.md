# Writing your poker bot

Your bot plays No-Limit Texas Hold'em against other bots. **You only need to edit one file: `my_bot.py`.**

## 1. Get set up

You need Python 3.10 or newer. Check with `python3 --version`. The bot itself needs nothing installed.

> **On Windows**, type `python` (or `py`) wherever this guide says `python3`.

```
bots/
├── my_bot.py      ← your bot. Edit this!
├── pokerbot/      ← helper code. You don't need to touch it.
└── README.md      ← you are here
```

## 2. Run the example bot

The organiser will tell you the server's **host** and **port**:

```bash
python3 my_bot.py --host 192.168.1.20 --port 8000
```

You'll see your bot connect, wait for the match, then play each hand:

```
[MyBot] Connected as p3. Waiting for a match to start...
[MyBot] Match started! I'm in seat 2 with 6 players.
[MyBot] Hand 1: I have As Kd
[MyBot]   preflop board [] pot 30 -> call
```

### Practising on your own computer

You can run the same table server the tournament uses. Setting it up takes one minute, from the top of the repo. You need Python 3.12 or newer for this part:

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
pokerserver --port 8000
```

Leave that running. In a second terminal, play your bot against copies of itself:

```bash
cd bots
python3 my_bot.py --port 8000 --count 4 --quiet
```

`--count 4` starts 4 copies, and `--quiet` means only the first one prints. The match starts about 30 seconds after the second bot joins. Add `--lobby-wait 2` to the `pokerserver` command to start sooner.

To **watch** the game in your browser, start the server with `pokerserver --port 8000 --web-port 8080 --delay-ms 500` instead. Then open http://localhost:8080 and click **Start match**. See [`../server/README.md`](../server/README.md) for every option.

You can also check your `decide()` without a server at all. Build a pretend `state` and call it:

```python
from pokerbot import GameState
from my_bot import decide

state = GameState(my_cards=["As", "Ad"], board=[], street="preflop", pot=30,
                  to_call=20, can_check=False, can_raise=True, min_raise=40,
                  max_raise=1000, my_stack=980, my_bet=0, my_seat=0,
                  opponents=[], history=[], hand_number=1, hands_in_match=20,
                  button=0)
print(decide(state))      # e.g. {'action': 'raise', 'amount': 120}
```

## 3. Make it yours

Open `my_bot.py`. First, change `BOT_NAME` to your team name. Then edit `decide()`. It runs every time it's your turn and must **return one move**:

| Return this | What it does |
|---|---|
| `fold()` | Give up this hand. |
| `check()` | Pass without betting. Only works when there's nothing to call (`state.can_check`). |
| `call()` | Match the current bet. |
| `raise_to(100)` | Make your **total** bet this round 100 chips. Also how you make the first bet. |
| `all_in()` | Bet everything you have. |

**Don't panic about mistakes.** If you return a move that isn't allowed, forget to return, or your code crashes, the bot fixes it with a safe move and prints a note telling you what happened. Your bot keeps playing.

### What your bot can see

Everything is on `state`:

| | |
|---|---|
| `state.my_cards` | Your two cards, e.g. `["As", "Kd"]` |
| `state.board` | The shared cards: none before the flop, then 3, 4 and 5 |
| `state.street` | `"preflop"`, `"flop"`, `"turn"` or `"river"` |
| `state.pot` | Chips in the middle |
| `state.to_call` | Chips you need to put in to call (0 means you can check) |
| `state.can_check` / `state.can_raise` | What's allowed right now |
| `state.min_raise` / `state.max_raise` | The smallest and largest amounts you can `raise_to()` |
| `state.my_stack` | Chips you have left |
| `state.pot_odds` | Cost of calling as a share of the pot after you call. Lower means calling is cheaper. |
| `state.opponents` | The other players. Each has `.name`, `.stack`, `.bet`, `.status`, `.in_hand` |
| `state.opponents_in_hand` | Just the opponents who haven't folded |
| `state.history` | Every move so far this hand. Each has `.name`, `.action`, `.amount`, `.street` |
| `state.hand_number` / `state.hands_in_match` | e.g. hand 7 of 20 |

### Cards

A card is a 2-letter string: **rank** then **suit**.

- Ranks: `2 3 4 5 6 7 8 9 T J Q K A` (`T` is ten)
- Suits: `c` clubs, `d` diamonds, `h` hearts, `s` spades

So `"As"` is the Ace of spades and `"Td"` is the Ten of diamonds.

Helpers in `pokerbot.cards`:

```python
from pokerbot.cards import rank, suit, card_name, is_pair, is_suited, describe_hand, hand_strength

rank("As")                         # 14   (2-10, J=11, Q=12, K=13, A=14)
suit("As")                         # "s"
card_name("Td")                    # "Ten of Diamonds"
is_pair(["Kh", "Kd"])              # True
is_suited(["Ah", "7h"])            # True
describe_hand(state.my_cards, state.board)   # e.g. "Two pair"
hand_strength(state.my_cards, state.board)   # 0.0 (bad) to 1.0 (amazing)
```

## 4. The rules

- Up to 6 bots at a table. Everyone starts with **1000 chips**. Blinds are **10/20**.
- A match is **20 hands**. Whoever has the most chips at the end wins.
- You have **5 seconds** per move. If you're too slow, you check, or fold if you can't check.
- If your bot disconnects, it can't rejoin that match. Its seat keeps checking and folding until the end.

## 5. Ideas to make your bot better

The example bot is simple on purpose. Some things to try:

1. **Play fewer hands.** Folding weak starting cards like 7-2 saves chips.
2. **Bet more with great hands.** Try betting a fraction of `state.pot` instead of always 3× the minimum.
3. **Use position.** Acting last is an advantage. Compare `state.my_seat` with `state.button`.
4. **Count opponents.** A pair is worth more against 1 opponent than against 5. Use `len(state.opponents_in_hand)`.
5. **Watch your opponents.** `state.history` shows who has been raising. Some bots bluff a lot!
6. **Improve `hand_strength`.** It's in `pokerbot/cards.py`, and it doesn't know when the board helps everyone. Copy it into `my_bot.py` and make it smarter.
7. **Bluff sometimes.** `import random` and raise with a weak hand now and then.

## Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: python` | On a Mac or Linux, use `python3`. On Windows, use `python` or `py`. |
| `Couldn't connect to ...` | Check the server is running and the host and port are right. On a different computer, use the organiser's IP, not `127.0.0.1`. |
| `Your decide() crashed!` | Read the error underneath. It points to the line in `my_bot.py` that broke. |
| `Note: ...` | Your move wasn't allowed, so the bot made a safe move instead. The note says why. |
| Nothing happens after "Waiting for a match" | The match hasn't started yet. On the organiser's server, wait for them to start it. On your own server, a match starts about 30 seconds after the second bot joins (or use `--lobby-wait 2`), or when you click **Start match** if you used `--web-port`. |

Want the full technical details? See [`../docs/protocol.md`](../docs/protocol.md).
