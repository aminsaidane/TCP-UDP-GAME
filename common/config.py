"""Shared settings for the Pablo server and client.

Every value can be overridden with an environment variable of the same name,
e.g. `PABLO_SERVER_HOST=192.168.1.10 python -m client.main`.
"""
import os


def _env(name, default, cast=str):
    value = os.environ.get(name)
    return default if value is None else cast(value)


# --- Network -----------------------------------------------------------------
# Address the server binds to ("0.0.0.0" = every network interface).
SERVER_BIND = _env("PABLO_SERVER_BIND", "0.0.0.0")
# Address clients connect to (overridden by LAN discovery once step 9 lands).
SERVER_HOST = _env("PABLO_SERVER_HOST", "127.0.0.1")

TCP_PORT = _env("PABLO_TCP_PORT", 5000, int)        # login, lobby, every game action
UDP_PORT = _env("PABLO_UDP_PORT", 5001, int)        # presence, timer ticks, heartbeats
DISCOVERY_PORT = _env("PABLO_DISCOVERY_PORT", 5002, int)  # LAN broadcast "where is the server?"

HEARTBEAT_INTERVAL = 1.0   # seconds between client heartbeats (UDP)
HEARTBEAT_TIMEOUT = 5.0    # seconds of silence before a player counts as disconnected

# --- Rooms -------------------------------------------------------------------
ALLOWED_ROOM_SIZES = (3, 4)  # the room creator picks one of these
DEFAULT_ROOM_SIZE = 3

# --- Game rules (see RULES.md) -------------------------------------------------
CARDS_PER_PLAYER = 4
INITIAL_PEEK = 2             # cards each player may look at when a round starts
CUT_WINDOW_SECONDS = 3.0     # pause after every throw during which anyone may cut
TURN_TIMEOUT_SECONDS = 60.0  # a player who does nothing for this long is skipped
TARGET_SCORE = 100           # the match ends when someone reaches this
ROUND_WINNER_SCORE = -5      # what the round winner scores that round
WRONG_PABLO_PENALTY = 50     # added to a player who calls Pablo wrongly
