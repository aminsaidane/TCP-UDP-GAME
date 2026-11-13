# Pablo — Multiplayer Card Game over TCP & UDP

CS411 Project 2. A real-time multiplayer version of **Pablo**, a memory card game, built on raw sockets to show when to use TCP and when to use UDP.

- **1 server + 3 or 4 clients**, each on its own PC.
- Full rules: [RULES.md](RULES.md).
- Every network message, and why it uses TCP or UDP: [PROTOCOL.md](PROTOCOL.md).

## Project layout

```
common/    settings and code shared by server and client (config, protocol, game rules)
server/    the game server (TCP + UDP)
client/    the pygame client
tests/     automated tests
```

## Requirements

- Python 3.10+
- `pip install -r requirements.txt` (pygame, client only — the server uses only the standard library)

## Running

### Server

On the server PC, from the project folder:

```bash
python -m server.main
```

It listens on every network interface, TCP port 5000, and prints a log line for every connection, login and disconnection. Stop it with Ctrl+C.

| Option | Default | Purpose |
|---|---|---|
| `--port` | `5000` | TCP port |
| `--users-file` | `users.json` | where accounts are saved (passwords are stored as salted hashes) |

### Client

On each player PC (needs pygame: `pip install -r requirements.txt`):

```bash
python -m client.main                         # then type the server IP in the login screen
python -m client.main --server 192.168.1.12   # or pre-fill it
```

Click **Register** the first time (it creates the account and logs you in), **Log in** afterwards. In the lobby, create a room (3 or 4 players) or join one; the host starts the game once at least 3 players are in.

### Controls at the table

| When | Do |
|---|---|
| Start of a round | click 2 of your cards: they are shown for 5 seconds, memorise them |
| Your turn | **Draw**, or **Throw a pair** then click the 2 cards |
| After drawing | **Throw it**, or click one of your cards to swap it with the drawn card |
| Jack power | click one of your cards and one opponent card (or **Skip power**) |
| Queen power | click any card to see it for 3 seconds (or **Skip power**) |
| Your turn, you think your hand is the lowest | **Pablo!** then **Confirm Pablo** (a wrong call costs +50) |
| Cut window (3 s after any throw, anyone) | click one of **your** cards with the same number as the pile. Wrong = penalty card |

After each round everyone's cards are revealed with the scores; the host clicks **Next round**. The match ends when someone reaches 100 points (lowest total wins), and the room is ready for a new match.

## Tests

From the project folder:

```bash
python -m unittest -v
```

## Configuration

All ports and game settings live in [`common/config.py`](common/config.py) and can be overridden with environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PABLO_SERVER_HOST` | `127.0.0.1` | server address the client connects to |
| `PABLO_TCP_PORT` | `5000` | login, lobby, game actions |
| `PABLO_UDP_PORT` | `5001` | presence, timers, heartbeats |
| `PABLO_DISCOVERY_PORT` | `5002` | LAN server discovery |
