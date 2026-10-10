# NUCATS Pokerbots Protocol, version 1

This document is everything you need to write a bot that plays on the NUCATS table server, in any language. If you can open a TCP socket and read and write JSON, you can play.

- [1. Overview](#1-overview)
- [2. Transport and framing](#2-transport-and-framing)
- [3. Identity](#3-identity)
- [4. Connection lifecycle](#4-connection-lifecycle)
- [5. Game rules the server enforces](#5-game-rules-the-server-enforces)
- [6. Data formats](#6-data-formats)
- [7. Client → server messages](#7-client--server-messages)
- [8. Server → client messages](#8-server--client-messages)
- [9. Taking a turn](#9-taking-a-turn)
- [10. Errors, timeouts and disconnects](#10-errors-timeouts-and-disconnects)
- [11. Example session](#11-example-session)
- [12. Testing by hand](#12-testing-by-hand)

---

## 1. Overview

The **table server** runs the game. It is the single source of truth: it shuffles, deals, tracks every chip, checks every action and decides every pot. Your bot is a **client**. It connects to the server, gets told what is happening, and answers when it is asked for an action.

The conversation is mostly the server talking. Your bot only ever sends two kinds of message:

1. `hello`, once, straight after connecting
2. `action`, each time the server sends you an `action_request`

## 2. Transport and framing

| | |
|---|---|
| Transport | Plain TCP. The organiser gives you the host and port. There is no default port. |
| Encoding | UTF-8 |
| Framing | **One JSON object per line.** Each message is a single JSON object followed by a newline (`\n`). `\r\n` is also accepted from clients. |
| Max line length | 65,536 bytes. A longer line is a fatal error (§10). |

Rules for both sides:

- A message must be a JSON **object** with a string field `"type"`.
- Field order doesn't matter.
- **Ignore fields you don't recognise.** Later versions may add fields to existing messages without changing the protocol version.
- Don't pretty-print. A message must not contain a raw newline. JSON encoders escape newlines inside strings, so this only matters if you build JSON by hand.

## 3. Identity

**There are no accounts or API keys. Your identity is your TCP connection.**

- When you connect, the server gives your connection a `player_id` (e.g. `"p7"`). It is unique for as long as the server runs, and every message refers to you by it (or by your seat number during a match).
- The `name` you send in `hello` is for display only. Two bots may use the same name, but they will still have different `player_id`s.
- **If your connection drops, that player is gone.** Reconnecting gives you a brand-new `player_id`, and you can't get your old seat or chips back. See §10.3 for what happens to a seat whose bot has disconnected.

## 4. Connection lifecycle

```
 client                                  server
   │ ── TCP connect ───────────────────────► │
   │ ── hello ─────────────────────────────► │   (within 10 s)
   │ ◄──────────────────────────── welcome ── │
   │                                          │   ... waiting in the lobby ...
   │ ◄──────────────────────── match_start ── │
   │ ┌─ for each hand (up to 20) ───────────┐ │
   │ │ ◄───────────────────── hand_start ── │ │
   │ │ ◄─────────── player_action (blinds) ─│ │
   │ │   repeat until the hand ends:        │ │
   │ │ ◄─── action_request (your turn) ──── │ │
   │ │ ── action ─────────────────────────► │ │
   │ │ ◄──────────────── player_action ──── │ │   (everyone's actions, including yours)
   │ │ ◄─────────── street (flop/turn/river)│ │
   │ │ ◄─────────────────────── hand_end ── │ │
   │ └──────────────────────────────────────┘ │
   │ ◄────────────────────────── match_end ── │
   │ ◄──────────────────── connection closed ── │   (unless the server runs more matches: see step 6)
```

1. **Connect** to the server's host and port.
2. **Send `hello`** within **10 seconds**, or the server closes the connection.
3. **Receive `welcome`.** You're now in the lobby.
4. **Wait for a match.** Depending on how the organiser runs the server, a match starts either when the organiser presses Start, or automatically: when the table is full (6 bots), or when at least 2 bots have waited for the lobby wait time. Either way, your bot just waits for `match_start`. If more bots are waiting than there are seats, the ones that arrived first get seated.
5. **Play the match** (§5).
6. **After `match_end`**, by default the server closes every connection and shuts down, so your bot should exit when the socket closes. If the organiser has set the server to run more matches, connected bots go back to the lobby instead and may be seated in the next one.
7. You can **disconnect** at any time by closing the socket.

## 5. Game rules the server enforces

| Setting | Value |
|---|---|
| Game | No-limit Texas Hold'em |
| Seats | Up to 6, numbered `0`–`5` |
| Starting stack | 1000 chips |
| Blinds | 10 small / 20 big, fixed for the whole match |
| Antes | None |
| Hands per match | 20 |

The organiser can change these values. The ones in force are always sent to you in `welcome` and `match_start`, so read them from there rather than hard-coding them.

- **Seating** is random at the start of each match.
- **Chips carry over** from hand to hand for the whole match. Stacks are reset only when a new match starts.
- **Busting:** a player with 0 chips at the start of a hand is out. They are not dealt in and take no further part in the match.
- **The button** starts on a random occupied seat for hand 1. It then moves clockwise (to the next higher seat number, wrapping from 5 to 0) to the next player who still has chips.
- **Blinds:** with 3 or more players, the small blind is the first player with chips clockwise of the button, and the big blind is the next one after that. **Heads-up** (2 players), the button posts the small blind and acts first before the flop. A player who can't cover a blind posts everything they have and is all-in.
- **Minimum raise:** the first bet on a street must be at least the big blind. A raise must increase the bet by at least as much as the previous bet or raise on that street. You may always go all-in for less.
- **The match ends** after the configured number of hands, or earlier if only one player has chips left.
- **Standings** are ranked by final stack, highest first. Players with equal final stacks share a rank. Players who busted are ranked below everyone still holding chips. A player who busted in a later hand ranks above one who busted earlier, and players who busted in the same hand share a rank.
- **Information:** you see your own hole cards, every public action, and every card shown at showdown. You **never** see cards that were folded or mucked.

## 6. Data formats

### 6.1 Cards

A card is a **2-character string**: rank, then suit.

| Ranks | `2 3 4 5 6 7 8 9 T J Q K A` |
|---|---|
| Suits | `c` clubs, `d` diamonds, `h` hearts, `s` spades |

Examples: `"As"` (ace of spades), `"Td"` (ten of diamonds), `"2c"` (two of clubs).

Hole cards and the board are arrays of cards, e.g. `["As", "Kd"]`. The board grows from `[]` (preflop) to 3 cards (flop), 4 (turn) and 5 (river).

### 6.2 Chips

All chip amounts are **non-negative integers**.

### 6.3 Streets

`"preflop"`, `"flop"`, `"turn"`, `"river"`

### 6.4 Seat status

Status values used in the `seats` arrays:

| Status | Meaning |
|---|---|
| `"active"` | Still in the hand and able to act |
| `"all_in"` | Still in the hand, no chips left to bet |
| `"folded"` | Folded this hand |
| `"out"` | Not dealt into this hand because the player has busted |

A seat object can also carry `"connected": false` if its bot has disconnected (§10.3).

## 7. Client → server messages

### 7.1 `hello`

Must be the first message you send.

```json
{"type": "hello", "protocol": 1, "name": "MyBot"}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `protocol` | int | yes | Must be `1`. Any other value gets a fatal `protocol_mismatch` error. |
| `name` | string | yes | Display name, 1–32 characters: letters, digits, space, `_`, `-`, `.`. |

### 7.2 `action`

Your answer to an `action_request`. Send exactly one per request.

```json
{"type": "action", "request_id": 42, "action": "raise", "amount": 120}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `request_id` | int | yes | Copy it from the `action_request` you're answering. |
| `action` | string | yes | `"fold"`, `"check"`, `"call"` or `"raise"` |
| `amount` | int | only for `raise` | The **total** you want your bet to be on this street ("raise to"), **not** the amount you're adding. Must be between `legal_actions.raise.min` and `legal_actions.raise.max` inclusive. |

`raise` is also used for the first bet on a street. "Bet 60" is sent as `"raise"` with `"amount": 60`.

## 8. Server → client messages

Every server message carries `"type"`. Messages about a match also carry `"match_id"`. Messages about a hand also carry `"hand_number"`, which starts at 1.

### 8.1 `welcome`

Your reply to `hello`.

```json
{
  "type": "welcome",
  "protocol": 1,
  "player_id": "p7",
  "name": "MyBot",
  "config": {
    "max_players": 6,
    "starting_stack": 1000,
    "small_blind": 10,
    "big_blind": 20,
    "hands_per_match": 20,
    "action_timeout_ms": 5000
  }
}
```

### 8.2 `match_start`

```json
{
  "type": "match_start",
  "match_id": "m3",
  "your_seat": 2,
  "seats": [
    {"seat": 0, "player_id": "p4", "name": "Alice"},
    {"seat": 2, "player_id": "p7", "name": "MyBot"},
    {"seat": 3, "player_id": "p5", "name": "Bob"}
  ],
  "config": { "...same shape as in welcome..." : 0 }
}
```

Only occupied seats are listed. Seat numbers may have gaps.

### 8.3 `hand_start`

```json
{
  "type": "hand_start",
  "match_id": "m3",
  "hand_number": 1,
  "button": 3,
  "small_blind": {"seat": 0, "amount": 10},
  "big_blind": {"seat": 2, "amount": 20},
  "hole_cards": ["As", "Kd"],
  "seats": [
    {"seat": 0, "player_id": "p4", "name": "Alice", "stack": 1000, "status": "active"},
    {"seat": 2, "player_id": "p7", "name": "MyBot", "stack": 1000, "status": "active"},
    {"seat": 3, "player_id": "p5", "name": "Bob",   "stack": 1000, "status": "active"}
  ]
}
```

- `stack` is the player's stack **before** blinds are posted.
- `hole_cards` is `[]` if you aren't dealt in (you've busted).
- `small_blind.amount` / `big_blind.amount` show what is actually posted, which can be less than the blind for a short-stacked player.

### 8.4 `player_action`

Sent to **everyone** for every action, including blinds, your own actions and actions the server takes for a bot.

```json
{
  "type": "player_action",
  "match_id": "m3",
  "hand_number": 1,
  "seat": 3,
  "action": "raise",
  "amount": 60,
  "stack": 940,
  "pot": 90,
  "auto": null
}
```

| `action` | `amount` means |
|---|---|
| `"small_blind"` / `"big_blind"` | Chips posted |
| `"fold"` / `"check"` | Always `0` |
| `"call"` | Chips **added** to call |
| `"raise"` | The player's **total** bet on this street after raising ("raise to") |

- `stack`: the player's stack after the action.
- `pot`: total chips in the middle after the action, including bets on the current street.
- `auto`: `null` if the bot chose the action. Otherwise it says why the server chose it: `"timeout"`, `"invalid"` or `"disconnected"` (§10).

### 8.5 `street`

Sent to everyone when new board cards are dealt.

```json
{"type": "street", "match_id": "m3", "hand_number": 1, "street": "flop", "board": ["7c", "Td", "2h"], "pot": 120}
```

`board` is the **whole** board so far, not just the new cards. If players are all-in, several `street` messages may arrive back-to-back with no betting in between.

### 8.6 `action_request`

Sent **only to the player whose turn it is**. It carries everything you need to decide.

```json
{
  "type": "action_request",
  "match_id": "m3",
  "hand_number": 1,
  "request_id": 42,
  "timeout_ms": 5000,
  "street": "flop",
  "board": ["7c", "Td", "2h"],
  "hole_cards": ["As", "Kd"],
  "pot": 120,
  "button": 3,
  "your_seat": 2,
  "seats": [
    {"seat": 0, "player_id": "p4", "name": "Alice", "stack": 960, "bet": 0,  "status": "folded"},
    {"seat": 2, "player_id": "p7", "name": "MyBot", "stack": 940, "bet": 0,  "status": "active"},
    {"seat": 3, "player_id": "p5", "name": "Bob",   "stack": 900, "bet": 40, "status": "active"}
  ],
  "legal_actions": {
    "fold": true,
    "check": false,
    "call": 40,
    "raise": {"min": 80, "max": 940}
  }
}
```

| Field | Notes |
|---|---|
| `request_id` | Echo this in your `action`. |
| `timeout_ms` | How long you have to respond, measured from when the server sent this message. |
| `pot` | Total chips in the middle, **including** bets on this street. |
| `seats[].bet` | Chips that seat has put in **on this street** so far. |
| `seats[].stack` | Chips that seat has behind (not yet bet). |
| `legal_actions.fold` | `true` when there is a bet to call. `false` when you can check for free, because folding then would only throw the hand away. |
| `legal_actions.check` | `true` if you can check (nothing to call). |
| `legal_actions.call` | Chips you'd add to call, or `null` if there's nothing to call. If you can't cover the full bet, this is your whole stack (calling all-in). |
| `legal_actions.raise` | `null` if you can't raise. Otherwise `min`/`max` are the allowed "raise to" totals for this street. `max` is always an all-in. |

### 8.7 `hand_end`

Sent to everyone when a hand finishes.

```json
{
  "type": "hand_end",
  "match_id": "m3",
  "hand_number": 1,
  "board": ["7c", "Td", "2h", "9s", "Ks"],
  "showdown": [
    {"seat": 2, "cards": ["As", "Kd"]},
    {"seat": 3, "cards": ["Qh", "Qc"]}
  ],
  "pots": [
    {"amount": 300, "winners": [{"seat": 2, "amount": 300}]}
  ],
  "results": [
    {"seat": 0, "net": -20,  "stack": 980},
    {"seat": 2, "net": 150,  "stack": 1150},
    {"seat": 3, "net": -130, "stack": 870}
  ]
}
```

- `showdown`: cards that were shown. Empty if the hand ended without a showdown, e.g. everyone else folded. A player who lost at showdown may muck, and won't be listed.
- `pots`: the main pot first, then any side pots. Each pot lists who won how much. Split pots have several winners. A bet that nobody called is returned to its owner and does not appear here.
- `results`: every seat that was dealt in this hand, with net chips won or lost (`net`) and the stack after the hand (`stack`).

### 8.8 `match_end`

```json
{
  "type": "match_end",
  "match_id": "m3",
  "hands_played": 20,
  "standings": [
    {"rank": 1, "seat": 2, "player_id": "p7", "name": "MyBot", "stack": 1410, "net": 410},
    {"rank": 2, "seat": 3, "player_id": "p5", "name": "Bob",   "stack": 1040, "net": 40},
    {"rank": 3, "seat": 0, "player_id": "p4", "name": "Alice", "stack": 550,  "net": -450}
  ]
}
```

### 8.9 `error`

```json
{"type": "error", "code": "illegal_action", "message": "raise to 25 is below the minimum of 40", "fatal": false}
```

If `fatal` is `true`, the server closes the connection right after sending it. Error codes are listed in §10.1.

## 9. Taking a turn

1. You receive `action_request`.
2. Decide using the state in the message. It's self-contained, so you don't need to have tracked earlier messages.
3. Send **one** `action` with the same `request_id` before `timeout_ms` runs out.
4. Everyone, you included, then receives a `player_action` showing what was applied.

Rules:

- **One shot.** The first `action` you send for a request is final. If it's illegal, the server applies the default action (step 5) and does **not** ask again.
- **Default action:** `check` if checking is legal, otherwise `fold`.
- An `action` with an old or unknown `request_id`, or sent when it isn't your turn, gets a non-fatal `stale_request` error and is ignored.
- `raise` with `amount` equal to `legal_actions.raise.max` is an all-in.

Pick an action that `legal_actions` marks as allowed (`true`, a number, or a raise range) and it is guaranteed to be legal.

## 10. Errors, timeouts and disconnects

### 10.1 Error codes

| Code | Fatal | When |
|---|---|---|
| `bad_json` | no | A line wasn't valid JSON, or wasn't a JSON object with a string `type`. |
| `unknown_type` | no | `type` isn't a message the server accepts. |
| `hello_required` | yes | You sent something other than `hello` first. |
| `hello_timeout` | yes | No `hello` within 10 seconds of connecting. |
| `duplicate_hello` | no | You sent `hello` again. Ignored. |
| `protocol_mismatch` | yes | `protocol` isn't `1`. |
| `bad_name` | yes | `name` is missing or doesn't follow the rules in §7.1. |
| `line_too_long` | yes | A line exceeded 65,536 bytes. |
| `stale_request` | no | The `action` didn't match your current `action_request`. |
| `illegal_action` | no | The action or amount wasn't allowed. The default action was applied. |
| `timeout` | no | You didn't answer in time. The default action was applied. |

`illegal_action` and `timeout` are sent only to the bot concerned. Everyone sees the resulting `player_action`, with `auto` set to `"invalid"` or `"timeout"`.

### 10.2 Timeouts

If no valid answer arrives within `timeout_ms`, the server applies the default action (check, otherwise fold). There is no time bank: each request has its own fresh limit.

### 10.3 Disconnects

If your bot disconnects **during a match**:

- Its seat stays at the table and keeps its chips.
- From then on the server acts for it **instantly** with the default action (`auto: "disconnected"`). It still posts blinds, so its stack slowly shrinks.
- It appears in `match_end` standings like everyone else.
- Its `seats` entries carry `"connected": false`.

You **can't reconnect to a seat**. Identity is the TCP connection (§3), so a new connection is a new player and joins the lobby for the next match.

If you disconnect while waiting in the lobby, you're simply removed from the queue.

## 11. Example session

`>` is what the client sends, `<` is what the server sends. Three bots, hand 1. Our bot is seat 2.

```
> {"type":"hello","protocol":1,"name":"MyBot"}
< {"type":"welcome","protocol":1,"player_id":"p7","name":"MyBot","config":{...}}
< {"type":"match_start","match_id":"m3","your_seat":2,"seats":[...],"config":{...}}
< {"type":"hand_start","match_id":"m3","hand_number":1,"button":3,"small_blind":{"seat":0,"amount":10},"big_blind":{"seat":2,"amount":20},"hole_cards":["As","Kd"],"seats":[...]}
< {"type":"player_action",...,"seat":0,"action":"small_blind","amount":10,"stack":990,"pot":10,"auto":null}
< {"type":"player_action",...,"seat":2,"action":"big_blind","amount":20,"stack":980,"pot":30,"auto":null}
< {"type":"player_action",...,"seat":3,"action":"call","amount":20,"stack":980,"pot":50,"auto":null}
< {"type":"player_action",...,"seat":0,"action":"call","amount":10,"stack":980,"pot":60,"auto":null}
< {"type":"action_request",...,"request_id":1,"street":"preflop","legal_actions":{"fold":false,"check":true,"call":null,"raise":{"min":40,"max":1000}}}
> {"type":"action","request_id":1,"action":"raise","amount":80}
< {"type":"player_action",...,"seat":2,"action":"raise","amount":80,"stack":920,"pot":120,"auto":null}
< {"type":"player_action",...,"seat":3,"action":"fold","amount":0,"stack":980,"pot":120,"auto":null}
< {"type":"player_action",...,"seat":0,"action":"fold","amount":0,"stack":980,"pot":120,"auto":null}
< {"type":"hand_end",...,"hand_number":1,"board":[],"showdown":[],"pots":[{"amount":60,"winners":[{"seat":2,"amount":60}]}],"results":[{"seat":0,"net":-20,"stack":980},{"seat":2,"net":40,"stack":1040},{"seat":3,"net":-20,"stack":980}]}
< {"type":"hand_start",...,"hand_number":2,...}
```

In hand 1, our raise to 80 wasn't called. The 60 chips we put in beyond the others' 20 each came back to us, so the pot we won was 60 and our net is +40.

## 12. Testing by hand

You can talk to the server yourself with `nc`, which helps when debugging:

```bash
nc localhost <port>
{"type":"hello","protocol":1,"name":"me"}
```

Then type `action` lines when you get an `action_request`. Five seconds isn't long enough to type a move, so if you run the server yourself, give it a longer time limit, e.g. `pokerserver --port 8000 --timeout-ms 60000`.
