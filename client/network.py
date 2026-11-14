"""The client's network layer.

pygame must redraw the window ~30 times a second, but `recv()` blocks until
the server sends something. So the socket is read by a separate *receiver
thread* that puts every message into a queue; the game loop empties the queue
once per frame without ever waiting. Connecting also happens in a thread, so
the window doesn't freeze while it tries to reach the server.

Besides real server messages, the queue carries three local events:
  {"type": "_connected"}, {"type": "_connect_failed", "message": ...},
  {"type": "_disconnected"}
"""
import queue
import socket
import threading

from common import config
from common import protocol as proto

CONNECT_TIMEOUT = 4.0


def parse_address(text):
    """'192.168.1.12' or '192.168.1.12:6000' -> (host, port)."""
    text = text.strip() or config.SERVER_HOST
    if text.count(":") == 1:
        host, port = text.split(":")
        if port.isdigit():
            return host, int(port)
    return text, config.TCP_PORT


class NetworkClient:
    def __init__(self):
        self.inbox = queue.Queue()
        self.conn = None
        self.host = None               # server address (the UDP socket sends there too)
        self._closing = False

    @property
    def connected(self):
        return self.conn is not None and not self.conn.closed

    def connect(self, host, port, first_message=None):
        """Connects in the background, then sends `first_message` (e.g. a login)."""
        self.host = host
        def run():
            try:
                sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError as exc:
                self.inbox.put({"type": "_connect_failed",
                                "message": f"cannot reach {host}:{port} ({exc.strerror or exc})"})
                return
            self.conn = proto.Connection(sock)
            self._closing = False
            self.inbox.put({"type": "_connected"})
            if first_message:
                self.send(first_message)
            self._receive_loop(self.conn)

        threading.Thread(target=run, name="network", daemon=True).start()

    def _receive_loop(self, conn):
        try:
            while True:
                try:
                    msg = conn.recv()
                except proto.ProtocolError:
                    continue            # skip a corrupted message, keep the connection
                if msg is None:
                    break
                self.inbox.put(msg)
        except OSError:
            pass
        if not self._closing:
            self.inbox.put({"type": "_disconnected"})
        conn.closed = True

    def send(self, msg):
        if not self.connected:
            return False
        try:
            self.conn.send(msg)
            return True
        except OSError:
            return False

    def messages(self):
        """Every message received since the last call (never blocks)."""
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def close(self):
        self._closing = True
        if self.conn:
            self.conn.close()
            self.conn = None
