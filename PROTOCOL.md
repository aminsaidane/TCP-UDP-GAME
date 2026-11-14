# Pablo — Network Protocol

Every message the client and server exchange, its fields, and **which transport carries it and why**.
The code that packs and unpacks messages is in [`common/protocol.py`](common/protocol.py).

## 1. Transports at a glance

| Channel | Port | Used for | Why this transport |
|---|---|---|---|
| **TCP** | 5000 | registration, login, lobby, rooms, **every game action**, scores | Must arrive, exactly once, in order. A lost or reordered "swap card 2" would corrupt the game. |
| **UDP** | 5001 | heartbeats, live "hover" presence, timer ticks | Small, frequent, and replaced by the next packet a moment later. A lost packet costs nothing; TCP's retransmission would only deliver stale data late. |
| **UDP broadcast** | 5002 | LAN server discovery | Broadcast ("anyone there?" to the whole network) only exists for UDP. TCP needs to know the address first. |

## 2. Message format

Every message is a **JSON object** with a `"type"` field. Card values use short codes: rank + suit, e.g. `"7H"`, `"QS"`, `"KH"`.

```json
{"type": "login", "username": "amin", "password": "secret"}
```

### TCP framing: one message per line

TCP is a *byte stream*, not a sequence of messages. One `send()` can arrive split over several `recv()` calls, or merged with the next message. So each message is sent as compact JSON followed by a newline (`\n`), and the receiver buffers bytes until it sees `\n`:

```
{"type":"draw"}\n{"type":"swap","slot":2}\n
```

JSON never contains a raw newline, so `\n` is a safe separator. Lines longer than 64 KB are rejected (protects the server from a client that never sends `\n`).

### UDP framing: one message per datagram

UDP keeps message boundaries, so one datagram = one JSON object, no newline. Datagrams are kept under **1200 bytes** so they are never fragmented by the network. Because UDP has no connection, every client datagram carries:

- `token`: the session token received at login. It tells the server **who** sent the packet (any program could send to port 5001).
- `seq`: a sequence number that increases with every packet. The receiver **drops any packet older than the last one it accepted**, because UDP can deliver out of order.

Malformed or unauthenticated datagrams are silently ignored.

## 3. Replies and errors (TCP)

Every client request gets either its success reply or an error:

```json
{"type": "error", "for": "join_room", "message": "room is full"}
```

`for` is the `type` of the request that failed. Illegal game moves (not your turn, wrong phase…) are reported the same way, with the message from the rules engine.

## 4. Account and login (TCP)

| Direction | Type | Fields | Meaning |
|---|---|---|---|
| C → S | `register` | `username`, `password` | Create an account |
| S → C | `register_ok` | `username` | Account created |
| C → S | `login` | `username`, `password` | Log in |
| S → C | `login_ok` | `username`, `token`, `udp_port` | Logged in; use `token` in every UDP packet |
| C → S | `logout` | – | Leave cleanly (closing the socket has the same effect) |

- Usernames: 3–16 characters, letters, digits and `_`. Passwords: at least 4 characters.
- The server stores only a **salted hash** of each password (PBKDF2), never the password itself.
- The same account cannot be logged in twice at once.

## 5. Lobby and rooms (TCP)

| Direction | Type | Fields | Meaning |
|---|---|---|---|
| C → S | `list_rooms` | – | Ask for the room list |
| S → C | `rooms` | `rooms`: list of *room* | Room list (also pushed to everyone in the lobby whenever it changes) |
| C → S | `create_room` | `name`, `size` (3 or 4) | Create a room and join it as its **host** |
| C → S | `join_room` | `room_id` | Join a waiting room |
| S → C | `room_joined` | `room` | You are now in this room |
| S → C | `room_update` | `room` | Someone joined or left your room |
| C → S | `start_game` | – | Host only, when the room has at least 3 players |
| C → S | `leave_room` | – | Back to the lobby |
| S → C | `room_left` | – | You are back in the lobby |

A *room* object:

```json
{"id": 3, "name": "Friday game", "size": 4, "host": "amin", "players": ["amin", "sara", "omar"],
 "status": "waiting", "can_start": true}
```

- `size` is the maximum number of players (3 or 4), chosen by the host.
- `status` is `"waiting"` or `"playing"`. Nobody can join a room that is playing or full.
- **Start button:** the host's client shows a *Start* button, enabled when `can_start` is true, i.e. at least **3 players** are in the room (`config.MIN_PLAYERS_TO_START`). A 4-player room can therefore start with 3 or 4.
- If the host leaves, the next player in the room becomes the host.

## 6. Game (TCP)

### Server → client

| Type | Fields | Meaning |
|---|---|---|
| `game_start` | `players`, `scores`, `round` | A new round begins (sent for every round) |
| `state` | `state` | The full game state **as this player may see it** (from `Round.state_for`): phase, whose turn, how many cards each player has, top of the pile, cut rank, pending power… Card faces stay hidden. Sent after every change. |
| `event` | `event` | What just happened, e.g. `{"action": "cut", "player": "sara", "card": "5D", "correct": true}`. Everyone gets the public part; only the player concerned also gets `event.private` (the card they drew or peeked at). |
| `round_over` | `round`, `result`, `scores`, `match_over` | Round result. `result` has `reason` (`"pablo"`, `"empty_hand"` or `"disqualification"`), `winner`, `caller`, `pablo_correct`, `totals` (each hand's points), `scores` (this round's scores), `hands` (**every hand revealed**) and `disqualified`. `scores` are the match totals. `match_over` is true if this round ended the match. |
| `match_over` | `scores`, `winners`, `rounds` | Someone reached 100: final totals and winner(s). The room goes back to `"waiting"` (a `room_update` follows) so the host can start a new match. |
| `player_left` | `username`, `reason` | A player dropped out of the game: `"disconnected"` (connection lost, or no UDP heartbeat for 5 s) or `"left"` (Leave button). The round goes on without them. |

### Client → server (game actions)

Each one is an engine method from `server/engine.py`. `slot` numbers are positions in a hand (they never shift).

| Type | Fields | When |
|---|---|---|
| `peek` | `slots`: two slot numbers | start of the round |
| `draw` | – | your turn |
| `throw_drawn` | – | after `draw` |
| `swap` | `slot` | after `draw`: replace this card with the drawn one |
| `throw_pair` | `slots`: two slot numbers | your turn |
| `jack` | `slot`, `target`, `target_slot` | Jack power: swap your `slot` with `target`'s `target_slot` |
| `queen` | `target`, `target_slot` | Queen power: look at that card |
| `skip_power` | – | give up the power |
| `cut` | `slot` | during the cut window, **anyone** |
| `pablo` | – | your turn, before drawing |
| `next_round` | – | **host only**, after `round_over`: deal the next round (the first player rotates) |

**Why cuts go over TCP even though they are a race:** a lost cut packet would silently cost a player a move they made, which is unfair. TCP guarantees the cut arrives; the race is decided by the order in which the server **receives** cuts from the different TCP connections, so network speed still decides who was first.

### Disconnections, rejoining, timeouts

- **Connection lost mid-game** (TCP closed, or no UDP heartbeat for 5 s): the player is removed from the current round (their cards go back into the deck, the turn passes if it was theirs) and scores nothing for it. Their seat in the room is kept (`room.disconnected`).
- **Logging back in**: the server sends `room_joined`, then `game_start` with `"watching": true` and the current `state` (also `"watching": true`). They see the rest of the round and are **dealt in again at the next round**.
- **Leave button** (`leave_room`) during a game: same removal, but the seat is freed; no coming back to that match.
- **Fewer than 2 connected players**: the match ends (`match_over`).
- **Turn timeout** (60 s, `state.turn_remaining`): the server skips the player: a turn passes, a drawn card is thrown (no power, no cut), a power is skipped, unfinished peeks are cancelled. Everyone gets an `event` `{"action": "timeout", ...}`.

## 7. Real-time layer (UDP, port 5001)

| Direction | Type | Fields | Meaning |
|---|---|---|---|
| C → S | `hello` | `token`, `seq` | Sent right after login so the server learns the client's UDP address |
| C → S | `heartbeat` | `token`, `seq` | Every 1 s. No heartbeat for 5 s → the player counts as disconnected |
| C → S | `hover` | `token`, `seq`, `target`, `slot` | The card under my mouse (`target` = whose hand; `slot` = null when not hovering) |
| S → C | `hover` | `seq`, `player`, `target`, `slot` | Relayed to the other players in the room, who see the highlighted card |
| S → C | `timer` | `seq`, `kind` (`"cut"` or `"turn"`), `remaining` (seconds) | Countdown ticks, several per second |

Hover and timer updates are pure display: if one is lost, the next one fixes the screen. The **real** decisions (the cut window closing, a turn timing out) are made by the server clock, and their results arrive over TCP.

## 8. LAN discovery (UDP broadcast, port 5002)

| Direction | Type | Fields | Meaning |
|---|---|---|---|
| C → broadcast | `discover` | `game`: `"pablo"` | Sent to `255.255.255.255:5002`: "is there a Pablo server on this network?" |
| S → C | `server_here` | `name`, `tcp_port`, `udp_port` | Reply sent straight back to the asker; the client reads the server IP from the packet's source address |

If no server answers within 2 seconds, the client falls back to the address in `config.py` (`PABLO_SERVER_HOST`).

## 9. Example session

```
alice → S  TCP  {"type":"login","username":"alice","password":"••••"}
S → alice  TCP  {"type":"login_ok","username":"alice","token":"f3a9…","udp_port":5001}
alice → S  UDP  {"type":"hello","token":"f3a9…","seq":1}
alice → S  TCP  {"type":"create_room","name":"Friday","size":3}
S → alice  TCP  {"type":"room_joined","room":{"id":1,"name":"Friday","size":3,"host":"alice","players":["alice"],"status":"waiting","can_start":false}}
        … bob and carol join: 3 players, the host's Start button is enabled …
alice → S  TCP  {"type":"start_game"}
S → all    TCP  {"type":"game_start","players":["alice","bob","carol"],"scores":{…},"round":1}
alice → S  TCP  {"type":"peek","slots":[0,3]}
S → alice  TCP  {"type":"event","event":{"action":"peek","player":"alice","private":{"cards":{"0":"7H","3":"KS"}}}}
S → all    TCP  {"type":"event","event":{"action":"peek","player":"alice"}}
        … everyone peeked, alice's turn …
alice → S  TCP  {"type":"draw"}
alice → S  TCP  {"type":"throw_drawn"}            (a 5)
S → all    UDP  {"type":"timer","seq":41,"kind":"cut","remaining":2.8}
bob → S    TCP  {"type":"cut","slot":1}           (arrives first, correct)
carol → S  TCP  {"type":"cut","slot":0}           (arrives second, correct → carol threw the last card)
S → all    TCP  {"type":"event","event":{"action":"cut_window_closed","next":"alice"}}
```

Carol's cut reached the server last, so play continues with the player after Carol, who is Alice again.
