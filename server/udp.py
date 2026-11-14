"""The server's UDP side.

Two UDP sockets, each read by its own thread:

* the GAME socket (port 5001) receives `hello`, `heartbeat` and `hover` from
  clients, and sends them `hover` relays and `timer` ticks. UDP has no
  connection, so every client packet carries the session `token` from login
  (who is it?) and a `seq` number (drop packets that arrive late or twice).
* the DISCOVERY socket (port 5002) answers LAN broadcasts: a client asks the
  whole network "is there a Pablo server?" and the server replies directly.

Nothing here is needed for the game to be correct: every real decision goes
over TCP. UDP carries the fast, disposable extras, where a lost packet is
simply replaced by the next one.
"""
import itertools
import logging
import socket
import threading
import time

from common import protocol as proto
from server.engine import CUT_WINDOW

log = logging.getLogger("pablo")
TICK_SECONDS = 0.2          # timer packets are sent 5 times per second


class UdpService:
    def __init__(self, server, host, port, discovery_port):
        self.server = server
        self.game_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.game_sock.bind((host, port))
        self.port = self.game_sock.getsockname()[1]
        self.discovery_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.discovery_sock.bind(("", discovery_port))   # "" = also receive broadcasts
        self.discovery_port = self.discovery_sock.getsockname()[1]
        self.incoming = proto.SequenceFilter()             # last seq accepted per token
        self.outgoing_seq = itertools.count(1)             # the server's own seq numbers
        self.running = False

    def start(self):
        self.running = True
        for target, name in ((self._game_loop, "udp-game"), (self._discovery_loop, "udp-discovery"),
                             (self._tick_loop, "udp-timer")):
            threading.Thread(target=target, name=name, daemon=True).start()
        log.info("UDP game port %d, LAN discovery port %d", self.port, self.discovery_port)

    def stop(self):
        self.running = False
        self.game_sock.close()
        self.discovery_sock.close()

    def send(self, addr, msg):
        msg["seq"] = next(self.outgoing_seq)
        try:
            self.game_sock.sendto(proto.encode_datagram(msg), addr)
        except OSError:
            pass                                            # UDP: fire and forget

    # ------------------------------------------------------------------ game socket
    def _game_loop(self):
        while self.running:
            try:
                data, addr = self.game_sock.recvfrom(proto.MAX_DATAGRAM + 1)
            except OSError:
                break                                       # socket closed by stop()
            msg = proto.decode_datagram(data)
            if msg is None:
                continue
            try:
                proto.validate(msg, allowed=proto.UDP_TYPES)
            except proto.ProtocolError:
                continue                                    # junk is silently ignored
            self._handle(msg, addr)

    def _handle(self, msg, addr):
        relay = []
        with self.server.lock:
            session = next((s for s in self.server.sessions.values() if s.token == msg["token"]), None)
            if session is None:
                return                                      # unknown token: not one of our players
            if not self.incoming.accept(msg["token"], msg["seq"]):
                return                                      # late or duplicate packet
            if session.udp_addr != addr:
                log.info("%s UDP address is %s:%d", session, addr[0], addr[1])
            session.udp_addr = addr                         # where to send this player's UDP
            session.last_heartbeat = time.monotonic()       # any packet proves the client is alive
            if msg["type"] == "hover":
                room = self.server.lobby.room_of(session.username)
                if room and room.id in self.server.games and (
                        msg["target"] in room.players and (msg["slot"] is None or 0 <= msg["slot"] < 16)):
                    out = proto.make("hover", player=session.username, target=msg["target"], slot=msg["slot"])
                    for name in room.players:
                        other = self.server.sessions.get(name)
                        if name != session.username and other and other.udp_addr:
                            relay.append((other.udp_addr, dict(out)))
        for addr_, out in relay:
            self.send(addr_, out)

    # ------------------------------------------------------------------ timer ticks
    def _tick_loop(self):
        while self.running:
            time.sleep(TICK_SECONDS)
            ticks = []
            with self.server.lock:
                for game in self.server.games.values():
                    if game.round.phase != CUT_WINDOW or game.cut_deadline is None:
                        continue
                    remaining = round(max(0.0, game.cut_deadline - time.monotonic()), 2)
                    for name in game.players:
                        session = self.server.sessions.get(name)
                        if session and session.udp_addr:
                            ticks.append((session.udp_addr, proto.make("timer", kind="cut", remaining=remaining)))
            for addr, msg in ticks:
                self.send(addr, msg)

    # ------------------------------------------------------------------ discovery socket
    def _discovery_loop(self):
        while self.running:
            try:
                data, addr = self.discovery_sock.recvfrom(proto.MAX_DATAGRAM + 1)
            except OSError:
                break
            msg = proto.decode_datagram(data)
            if not msg or msg.get("type") != "discover" or msg.get("game") != "pablo":
                continue
            log.info("LAN discovery request from %s:%d", *addr)
            reply = proto.make("server_here", name=socket.gethostname(),
                               tcp_port=self.server.port, udp_port=self.port)
            try:
                self.discovery_sock.sendto(proto.encode_datagram(reply), addr)
            except OSError:
                pass
