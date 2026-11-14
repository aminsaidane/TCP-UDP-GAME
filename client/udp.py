"""The client's UDP side: heartbeats, hover, timer ticks, and LAN discovery.

After login the client opens one UDP socket and:
  * sends `hello`, then a `heartbeat` every second (the server uses them to
    notice a client that has silently disappeared);
  * sends `hover` when the mouse moves onto another card;
  * receives `hover` relays and `timer` ticks in a background thread, which
    puts them in the same queue as the TCP messages.

Every packet carries the session token and an increasing seq number; received
packets older than the newest one already seen are dropped.
"""
import socket
import threading
import time

from common import config
from common import protocol as proto

DISCOVERY_TIMEOUT = 2.0


class UdpClient:
    def __init__(self, inbox):
        self.inbox = inbox                       # the NetworkClient queue, shared with TCP messages
        self.sock = None
        self.server_addr = None
        self.token = None
        self.seq = 0
        self.filter = proto.SequenceFilter()
        self._stop = threading.Event()
        self._lock = threading.Lock()            # heartbeat thread and game loop both send

    @property
    def active(self):
        return self.sock is not None

    def start(self, server_host, udp_port, token):
        self.stop()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))                  # any free port; the server learns it from our packets
        self.server_addr, self.token = (server_host, udp_port), token
        self._stop.clear()
        self.send("hello")
        threading.Thread(target=self._receive_loop, args=(self.sock,), name="udp-receive", daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, name="udp-heartbeat", daemon=True).start()

    def send(self, msg_type, **fields):
        with self._lock:
            if not self.sock:
                return
            self.seq += 1
            msg = proto.make(msg_type, token=self.token, seq=self.seq, **fields)
            try:
                self.sock.sendto(proto.encode_datagram(msg), self.server_addr)
            except OSError:
                pass                             # UDP: a lost packet is fine

    def _heartbeat_loop(self):
        while not self._stop.wait(config.HEARTBEAT_INTERVAL):
            self.send("heartbeat")

    def _receive_loop(self, sock):
        while True:
            try:
                data, _ = sock.recvfrom(proto.MAX_DATAGRAM + 1)
            except OSError:
                return                           # socket closed
            msg = proto.decode_datagram(data)
            if msg and msg.get("type") in ("hover", "timer") and self.filter.accept("server", msg.get("seq")):
                msg["_udp"] = True
                self.inbox.put(msg)

    def stop(self):
        self._stop.set()
        with self._lock:
            if self.sock:
                self.sock.close()
            self.sock = None
        self.filter = proto.SequenceFilter()


def discover(inbox, port=None, timeout=DISCOVERY_TIMEOUT):
    """Broadcasts 'is there a Pablo server?' on the LAN, in the background.

    Puts {"type": "_discovered", "servers": [{"host", "tcp_port", "name"}, ...]} in `inbox`.
    """
    port = port or config.DISCOVERY_PORT

    def run():
        found = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.25)
        request = proto.encode_datagram(proto.make("discover", game="pablo"))
        # 255.255.255.255 reaches every PC on the local network; 127.0.0.1 covers a
        # server running on this same PC (some systems don't loop broadcasts back).
        for target in ("255.255.255.255", "127.0.0.1"):
            try:
                sock.sendto(request, (target, port))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = sock.recvfrom(proto.MAX_DATAGRAM + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = proto.decode_datagram(data)
            if msg and msg.get("type") == "server_here":
                key = (addr[0], msg.get("tcp_port"))
                found[key] = {"host": addr[0], "tcp_port": msg.get("tcp_port"), "name": msg.get("name", "?")}
        sock.close()
        servers = list(found.values())
        # one server answering on both the LAN address and 127.0.0.1: keep the LAN address
        if len(servers) > 1:
            lan = [s for s in servers if s["host"] != "127.0.0.1"]
            servers = lan or servers
        inbox.put({"type": "_discovered", "servers": servers})

    threading.Thread(target=run, name="discovery", daemon=True).start()
