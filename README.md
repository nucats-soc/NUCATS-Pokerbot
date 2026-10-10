# NUCATS Pokerbots

Write a bot that plays No-Limit Texas Hold'em, then watch it play against everyone else's bots on the NUCATS table.

## Start here

**→ [`bots/README.md`](bots/README.md)**

That guide takes you from nothing to a working bot. In short:

```bash
cd bots
python3 my_bot.py --host <server address> --port <port>
```

Then open `bots/my_bot.py` and change `decide()` to make the bot your own.

## What's in here

| | |
|---|---|
| [`bots/`](bots/) | Your starter kit. **`my_bot.py` is the only file you need to edit.** |
| [`server/`](server/) | The table server the tournament runs on. Run it on your own computer to test your bot ([how](bots/README.md#practising-on-your-own-computer)). |
| [`docs/protocol.md`](docs/protocol.md) | The full technical spec for how bots talk to the server. Read this if you want to write your bot in a language other than Python. |

## The rules in one minute

- No-Limit Texas Hold'em, up to **6 bots** at a table.
- Everyone starts with **1000 chips**. Blinds are **10/20**.
- A match is **20 hands**, and the most chips at the end wins.
- **5 seconds** per move. If you're too slow, you check, or fold if you can't check.
- Get the server **address and port** from the organisers.
