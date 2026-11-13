"""Message packing for Pablo. See PROTOCOL.md for every message type.

TCP is a byte stream: one send() can arrive in pieces or glued to the next
message. `Connection` fixes that by sending one JSON object per line and
buffering received bytes until a full line is there.

UDP keeps message boundaries, so a datagram is simply one JSON object
(`encode_datagram` / `decode_datagram`).
"""
import json
import socket
import threading

MAX_LINE = 64 * 1024     # longest accepted TCP message, in bytes
MAX_DATAGRAM = 1200      # stay below the typical MTU so UDP packets are never fragmented
RECV_SIZE = 4096


class ProtocolError(Exception):
    """A malformed or invalid message."""


# ------------------------------------------------------------------ messages
def make(msg_type, **fields):
    """make("join_room", room_id=3) -> {"type": "join_room", "room_id": 3}"""
    return {"type": msg_type, **fields}


def error(for_type, message):
    return make("error", **{"for": for_type, "message": message})


def _to_dict(raw: bytes) -> dict:
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("a message must be a JSON object with a 'type'")
    return msg


def _to_bytes(msg: dict) -> bytes:
    if not isinstance(msg, dict) or "type" not in msg:
        raise ProtocolError("a message must be a dict with a 'type'")
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


# ------------------------------------------------------------------ TCP
def encode_line(msg: dict) -> bytes:
    """One message as a line of compact JSON (JSON never contains a raw newline)."""
    data = _to_bytes(msg) + b"\n"
    if len(data) > MAX_LINE:
        raise ProtocolError("message too long")
    return data


class LineBuffer:
    """Collects TCP bytes and hands back complete messages, however the bytes were split."""

    def __init__(self):
        self._buf = b""

    def feed(self, data: bytes) -> list:
        """Returns the complete messages received so far, in order.

        A malformed line becomes a ProtocolError object in the list (instead of
        being raised here) so the valid messages around it are not lost.
        """
        self._buf += data
        messages = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if line.strip():
                try:
                    messages.append(_to_dict(line))
                except ProtocolError as exc:
                    messages.append(exc)
        if len(self._buf) > MAX_LINE:
            self._buf = b""
            messages.append(ProtocolError("line too long"))
        return messages


class Connection:
    """A TCP socket that sends and receives whole messages.

    `send` may be called from several threads at once (the server's game
    timer and the client's own handler thread both send to the same client),
    so it is protected by a lock to keep two messages from interleaving.
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buffer = LineBuffer()
        self._pending = []
        self._send_lock = threading.Lock()
        self.closed = False

    def send(self, msg: dict):
        data = encode_line(msg)
        with self._send_lock:
            self.sock.sendall(data)

    def recv(self):
        """Next message, blocking. Returns None when the other side closed the connection.

        Raises ProtocolError on a malformed message (the caller decides whether to
        answer with an error or drop the client).
        """
        while not self._pending:
            data = self.sock.recv(RECV_SIZE)
            if not data:
                self.closed = True
                return None
            self._pending.extend(self._buffer.feed(data))
        item = self._pending.pop(0)
        if isinstance(item, ProtocolError):
            raise item
        return item

    def close(self):
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


# ------------------------------------------------------------------ UDP
def encode_datagram(msg: dict) -> bytes:
    data = _to_bytes(msg)
    if len(data) > MAX_DATAGRAM:
        raise ProtocolError("datagram too long")
    return data


def decode_datagram(data: bytes):
    """The message in a datagram, or None if it is malformed (UDP junk is just ignored)."""
    if len(data) > MAX_DATAGRAM:
        return None
    try:
        return _to_dict(data)
    except ProtocolError:
        return None


class SequenceFilter:
    """Drops UDP packets that arrive late or twice.

    Keeps the highest `seq` seen per sender; a packet is accepted only if its
    seq is higher. Used by both server (per player) and client (per server stream).
    """

    def __init__(self):
        self._last = {}

    def accept(self, sender, seq) -> bool:
        if not isinstance(seq, int) or isinstance(seq, bool):
            return False
        if seq <= self._last.get(sender, -1):
            return False
        self._last[sender] = seq
        return True

    def forget(self, sender):
        self._last.pop(sender, None)


# ------------------------------------------------------------------ validation
def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _str(v):
    return isinstance(v, str)


def _two_ints(v):
    return isinstance(v, list) and len(v) == 2 and all(_int(x) for x in v)


def _int_or_none(v):
    return v is None or _int(v)


# Required fields of every message a CLIENT may send, with a check for each value.
CLIENT_MESSAGES = {
    # TCP: account
    "register":    {"username": _str, "password": _str},
    "login":       {"username": _str, "password": _str},
    "logout":      {},
    # TCP: lobby
    "list_rooms":  {},
    "create_room": {"name": _str, "size": _int},
    "join_room":   {"room_id": _int},
    "leave_room":  {},
    "start_game":  {},
    "next_round":  {},
    # TCP: game actions
    "peek":        {"slots": _two_ints},
    "draw":        {},
    "throw_drawn": {},
    "swap":        {"slot": _int},
    "throw_pair":  {"slots": _two_ints},
    "jack":        {"slot": _int, "target": _str, "target_slot": _int},
    "queen":       {"target": _str, "target_slot": _int},
    "skip_power":  {},
    "cut":         {"slot": _int},
    "pablo":       {},
    # UDP
    "hello":       {"token": _str, "seq": _int},
    "heartbeat":   {"token": _str, "seq": _int},
    "hover":       {"token": _str, "seq": _int, "target": _str, "slot": _int_or_none},
    "discover":    {"game": _str},
}

TCP_TYPES = {"register", "login", "logout", "list_rooms", "create_room", "join_room", "leave_room", "start_game", "next_round",
             "peek", "draw", "throw_drawn", "swap", "throw_pair", "jack", "queen",
             "skip_power", "cut", "pablo"}
GAME_ACTIONS = {"peek", "draw", "throw_drawn", "swap", "throw_pair", "jack", "queen",
                "skip_power", "cut", "pablo"}
UDP_TYPES = {"hello", "heartbeat", "hover"}


def validate(msg: dict, allowed=None):
    """Checks a client message. Raises ProtocolError with a readable reason.

    `allowed` restricts the accepted types (e.g. TCP_TYPES on the TCP socket).
    """
    msg_type = msg.get("type")
    if msg_type not in CLIENT_MESSAGES or (allowed is not None and msg_type not in allowed):
        raise ProtocolError(f"unknown message type '{msg_type}'")
    for field, check in CLIENT_MESSAGES[msg_type].items():
        if field not in msg:
            raise ProtocolError(f"'{msg_type}' needs the field '{field}'")
        if not check(msg[field]):
            raise ProtocolError(f"'{msg_type}': invalid value for '{field}'")
    return msg
