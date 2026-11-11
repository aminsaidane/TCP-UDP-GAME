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

*(Coming in Step 5.)*

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
