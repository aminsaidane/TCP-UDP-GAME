"""Tests for message framing and validation. Run with:  python -m unittest -v"""
import socket
import threading
import unittest

from common import protocol as p


class LineBufferTests(unittest.TestCase):
    def test_message_split_over_many_recvs(self):
        data = p.encode_line(p.make("swap", slot=2))
        buf = p.LineBuffer()
        out = []
        for i in range(len(data)):              # worst case: one byte per recv()
            out += buf.feed(data[i:i + 1])
        self.assertEqual(out, [{"type": "swap", "slot": 2}])

    def test_several_messages_in_one_recv(self):
        data = p.encode_line(p.make("draw")) + p.encode_line(p.make("swap", slot=1))
        self.assertEqual(p.LineBuffer().feed(data), [{"type": "draw"}, {"type": "swap", "slot": 1}])

    def test_partial_message_waits_for_the_rest(self):
        buf = p.LineBuffer()
        self.assertEqual(buf.feed(b'{"type":"dr'), [])
        self.assertEqual(buf.feed(b'aw"}\n'), [{"type": "draw"}])

    def test_bad_line_does_not_lose_neighbours(self):
        out = p.LineBuffer().feed(b'{"type":"draw"}\nnot json\n{"type":"pablo"}\n')
        self.assertEqual(out[0], {"type": "draw"})
        self.assertIsInstance(out[1], p.ProtocolError)
        self.assertEqual(out[2], {"type": "pablo"})

    def test_line_without_newline_is_capped(self):
        out = p.LineBuffer().feed(b"x" * (p.MAX_LINE + 1))
        self.assertIsInstance(out[-1], p.ProtocolError)

    def test_message_needs_a_type(self):
        out = p.LineBuffer().feed(b'{"slot":1}\n[1,2]\n')
        self.assertTrue(all(isinstance(m, p.ProtocolError) for m in out))

    def test_unicode_usernames(self):
        msg = p.make("login", username="أمين", password="x")
        self.assertEqual(p.LineBuffer().feed(p.encode_line(msg)), [msg])


class ConnectionTests(unittest.TestCase):
    """Real TCP sockets on this machine."""

    def setUp(self):
        server = socket.create_server(("127.0.0.1", 0))
        port = server.getsockname()[1]
        self.client = p.Connection(socket.create_connection(("127.0.0.1", port)))
        self.server = p.Connection(server.accept()[0])
        server.close()

    def tearDown(self):
        self.client.close()
        self.server.close()

    def test_round_trip(self):
        self.client.send(p.make("join_room", room_id=3))
        self.assertEqual(self.server.recv(), {"type": "join_room", "room_id": 3})

    def test_many_messages_arrive_in_order(self):
        for i in range(500):
            self.client.send(p.make("swap", slot=i))
        self.assertEqual([self.server.recv()["slot"] for _ in range(500)], list(range(500)))

    def test_concurrent_senders_never_interleave(self):
        def spam(name):
            for i in range(200):
                self.server.send(p.make("event", who=name, i=i, pad="x" * 3000))
        threads = [threading.Thread(target=spam, args=(n,)) for n in "abc"]
        for t in threads:
            t.start()
        got = [self.client.recv() for _ in range(600)]   # would raise if two messages mixed
        for t in threads:
            t.join()
        for name in "abc":
            self.assertEqual([m["i"] for m in got if m["who"] == name], list(range(200)))

    def test_recv_returns_none_when_peer_closes(self):
        self.client.close()
        self.assertIsNone(self.server.recv())


class DatagramTests(unittest.TestCase):
    def test_round_trip(self):
        msg = p.make("heartbeat", token="abc", seq=7)
        self.assertEqual(p.decode_datagram(p.encode_datagram(msg)), msg)

    def test_junk_is_ignored(self):
        self.assertIsNone(p.decode_datagram(b"\xff\x00garbage"))
        self.assertIsNone(p.decode_datagram(b"x" * (p.MAX_DATAGRAM + 1)))

    def test_too_big_to_send(self):
        with self.assertRaises(p.ProtocolError):
            p.encode_datagram(p.make("hover", pad="x" * 2000))

    def test_real_udp_socket(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(p.encode_datagram(p.make("timer", seq=1, kind="cut", remaining=2.5)), rx.getsockname())
        rx.settimeout(2)
        data, _ = rx.recvfrom(p.MAX_DATAGRAM)
        self.assertEqual(p.decode_datagram(data)["remaining"], 2.5)
        rx.close()
        tx.close()


class SequenceFilterTests(unittest.TestCase):
    def test_drops_old_and_duplicate_packets(self):
        f = p.SequenceFilter()
        self.assertTrue(f.accept("alice", 1))
        self.assertTrue(f.accept("alice", 5))
        self.assertFalse(f.accept("alice", 3))      # arrived late
        self.assertFalse(f.accept("alice", 5))      # duplicate
        self.assertTrue(f.accept("bob", 2))         # each sender is tracked separately

    def test_rejects_non_numbers(self):
        f = p.SequenceFilter()
        self.assertFalse(f.accept("alice", "1"))
        self.assertFalse(f.accept("alice", True))


class ValidationTests(unittest.TestCase):
    def test_valid_messages(self):
        p.validate(p.make("jack", slot=0, target="bob", target_slot=2))
        p.validate(p.make("hover", token="t", seq=1, target="bob", slot=None))
        p.validate(p.make("throw_pair", slots=[0, 3]))
        p.validate(p.make("start_game"), allowed=p.TCP_TYPES)

    def test_missing_field(self):
        with self.assertRaisesRegex(p.ProtocolError, "slot"):
            p.validate(p.make("swap"))

    def test_wrong_type_of_value(self):
        for bad in (p.make("swap", slot="2"), p.make("swap", slot=True),
                    p.make("throw_pair", slots=[1]), p.make("join_room", room_id=None)):
            with self.assertRaises(p.ProtocolError):
                p.validate(bad)

    def test_unknown_type(self):
        with self.assertRaises(p.ProtocolError):
            p.validate(p.make("cheat"))

    def test_udp_messages_refused_on_tcp(self):
        with self.assertRaises(p.ProtocolError):
            p.validate(p.make("heartbeat", token="t", seq=1), allowed=p.TCP_TYPES)

    def test_every_engine_action_has_a_message(self):
        from server.engine import Round
        engine_methods = {"peek", "draw", "throw_drawn", "swap_drawn", "throw_pair",
                          "use_jack", "use_queen", "skip_power", "cut", "call_pablo"}
        for m in engine_methods:
            self.assertTrue(hasattr(Round, m))
        self.assertEqual(len(p.GAME_ACTIONS), len(engine_methods))


if __name__ == "__main__":
    unittest.main()
