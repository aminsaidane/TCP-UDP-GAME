"""UDP: LAN discovery, heartbeats, hover relay, timer ticks, token and sequence checks."""
import queue
import socket
import time
import unittest

from common import protocol as p
from client.udp import UdpClient, discover
from tests import test_game   # (imported as a module so its tests don't run twice)
from tests.test_server import ServerTestCase


def udp_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(1.0)
    return s


def receive_all(sock, seconds=0.5):
    """Every datagram arriving on `sock` during `seconds`."""
    out, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            data, _ = sock.recvfrom(p.MAX_DATAGRAM + 1)
        except socket.timeout:
            break
        out.append(p.decode_datagram(data))
    return out


def wait_until(check, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


class DiscoveryTests(ServerTestCase):
    def test_client_finds_the_server(self):
        inbox = queue.Queue()
        discover(inbox, port=self.server.udp.discovery_port, timeout=1.0)
        msg = inbox.get(timeout=3)
        self.assertEqual(msg["type"], "_discovered")
        self.assertEqual(len(msg["servers"]), 1)
        self.assertEqual(msg["servers"][0]["tcp_port"], self.server.port)

    def test_wrong_game_is_ignored(self):
        s = udp_socket()
        s.sendto(p.encode_datagram(p.make("discover", game="poker")), ("127.0.0.1", self.server.udp.discovery_port))
        self.assertEqual(receive_all(s), [])

    def test_nobody_answers(self):
        inbox = queue.Queue()
        free = udp_socket()                       # a port where no server listens
        port = free.getsockname()[1]
        free.close()
        discover(inbox, port=port, timeout=0.5)
        self.assertEqual(inbox.get(timeout=3)["servers"], [])


class HeartbeatTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = self.logged_in("amin")
        self.token = self.server.sessions["amin"].token
        self.sock = udp_socket()
        self.server_addr = ("127.0.0.1", self.server.udp.port)

    def send(self, msg_type, token=None, **fields):
        msg = p.make(msg_type, token=token or self.token, **fields)
        self.sock.sendto(p.encode_datagram(msg), self.server_addr)

    def test_hello_registers_the_udp_address(self):
        self.send("hello", seq=1)
        session = self.server.sessions["amin"]
        self.assertTrue(wait_until(lambda: session.udp_addr == self.sock.getsockname()))
        self.assertIsNotNone(session.last_heartbeat)

    def test_heartbeat_updates_last_seen(self):
        self.send("hello", seq=1)
        session = self.server.sessions["amin"]
        wait_until(lambda: session.last_heartbeat)
        first = session.last_heartbeat
        time.sleep(0.05)
        self.send("heartbeat", seq=2)
        self.assertTrue(wait_until(lambda: session.last_heartbeat > first))

    def test_unknown_token_is_ignored(self):
        self.send("hello", token="f" * 32, seq=1)
        time.sleep(0.2)
        self.assertIsNone(self.server.sessions["amin"].udp_addr)

    def test_old_and_duplicate_packets_are_dropped(self):
        session = self.server.sessions["amin"]
        self.send("heartbeat", seq=5)
        wait_until(lambda: session.last_heartbeat)
        seen = session.last_heartbeat
        time.sleep(0.05)
        self.send("heartbeat", seq=3)             # older than 5: arrived late
        self.send("heartbeat", seq=5)             # duplicate
        time.sleep(0.2)
        self.assertEqual(session.last_heartbeat, seen)

    def test_junk_does_not_crash_the_server(self):
        self.sock.sendto(b"\x00\xffnot json", self.server_addr)
        self.sock.sendto(p.encode_datagram(p.make("draw", token=self.token, seq=1)), self.server_addr)
        self.send("hello", seq=2)                 # still answering afterwards
        self.assertTrue(wait_until(lambda: self.server.sessions["amin"].udp_addr))

    def test_udp_client_sends_heartbeats(self):
        client = UdpClient(queue.Queue())
        client.start("127.0.0.1", self.server.udp.port, self.token)
        session = self.server.sessions["amin"]
        self.assertTrue(wait_until(lambda: session.last_heartbeat))
        first = session.last_heartbeat
        self.assertTrue(wait_until(lambda: session.last_heartbeat > first, timeout=2.5))  # 1 per second
        client.stop()


class GameUdpTests(test_game.GameTests):
    """UDP during a game (amin, sara, omar are playing)."""

    def setUp(self):
        super().setUp()
        self.socks = {}
        for name in self.players:
            sock = udp_socket()
            token = self.server.sessions[name].token
            sock.sendto(p.encode_datagram(p.make("hello", token=token, seq=1)), ("127.0.0.1", self.server.udp.port))
            self.socks[name] = sock
        self.assertTrue(wait_until(lambda: all(self.server.sessions[n].udp_addr for n in self.players)))
        self.seq = 1

    def udp(self, name, msg_type, **fields):
        self.seq += 1
        msg = p.make(msg_type, token=self.server.sessions[name].token, seq=self.seq, **fields)
        self.socks[name].sendto(p.encode_datagram(msg), ("127.0.0.1", self.server.udp.port))

    def test_hover_is_relayed_to_the_other_players_only(self):
        lina = self.logged_in("lina")                        # in the lobby, not in the game
        lina_sock = udp_socket()
        token = self.server.sessions["lina"].token
        lina_sock.sendto(p.encode_datagram(p.make("hello", token=token, seq=1)), ("127.0.0.1", self.server.udp.port))
        wait_until(lambda: self.server.sessions["lina"].udp_addr)
        self.udp("amin", "hover", target="sara", slot=2)
        for name in ("sara", "omar"):
            got = [m for m in receive_all(self.socks[name]) if m["type"] == "hover"]
            self.assertEqual(len(got), 1)
            self.assertEqual((got[0]["player"], got[0]["target"], got[0]["slot"]), ("amin", "sara", 2))
        self.assertEqual([m for m in receive_all(self.socks["amin"], 0.3) if m["type"] == "hover"], [])
        self.assertEqual(receive_all(lina_sock, 0.3), [])
        del lina

    def test_hover_on_someone_outside_the_room_is_refused(self):
        self.udp("amin", "hover", target="stranger", slot=0)
        self.assertEqual([m for m in receive_all(self.socks["sara"], 0.3) if m["type"] == "hover"], [])

    def test_timer_ticks_during_the_cut_window(self):
        self.peek_all()
        first = self.current()
        self.act(first, "draw")
        self.act(first, "throw_drawn")
        if self.state[first]["phase"] == "power":
            self.act(first, "skip_power")
        ticks = [m for m in receive_all(self.socks["omar"], 1.0) if m["type"] == "timer"]
        self.assertGreaterEqual(len(ticks), 3)                # ~5 per second
        self.assertEqual({t["kind"] for t in ticks}, {"cut"})
        remaining = [t["remaining"] for t in ticks]
        self.assertEqual(remaining, sorted(remaining, reverse=True))   # counting down
        seqs = [t["seq"] for t in ticks]
        self.assertEqual(seqs, sorted(seqs))                           # increasing seq numbers

    def test_no_timer_outside_the_cut_window(self):
        self.assertEqual([m for m in receive_all(self.socks["sara"], 0.5) if m["type"] == "timer"], [])


# GameUdpTests reuses GameTests' setup only, not its tests
for _name in dir(test_game.GameTests):
    if _name.startswith("test_") and _name not in GameUdpTests.__dict__:
        setattr(GameUdpTests, _name, None)


class ClientFilterTests(unittest.TestCase):
    def test_client_drops_late_server_packets(self):
        inbox = queue.Queue()
        fake_server = udp_socket()
        client = UdpClient(inbox)
        client.start("127.0.0.1", fake_server.getsockname()[1], "t" * 32)
        _, client_addr = fake_server.recvfrom(2048)               # the client's hello
        for seq in (10, 4, 10, 11):                               # 4 is late, the 2nd 10 is a duplicate
            fake_server.sendto(p.encode_datagram(p.make("timer", seq=seq, kind="cut", remaining=1.0)), client_addr)
        time.sleep(0.3)
        got = []
        while not inbox.empty():
            got.append(inbox.get()["seq"])
        self.assertEqual(got, [10, 11])
        client.stop()
        fake_server.close()


if __name__ == "__main__":
    unittest.main()
