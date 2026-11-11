"""Tests against a real running server (TCP, login). Run with:  python -m unittest -v"""
import logging
import os
import socket
import tempfile
import threading
import unittest

from common import protocol as p
from server.main import MAX_BAD_MESSAGES, PabloServer

logging.disable(logging.CRITICAL)   # keep the server's console log out of the test output


class Client:
    """A minimal test client speaking the real protocol."""

    def __init__(self, port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.conn = p.Connection(sock)

    def request(self, msg_type, **fields):
        self.conn.send(p.make(msg_type, **fields))
        return self.conn.recv()

    def close(self):
        self.conn.close()


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.users_file = os.path.join(self.tmp.name, "users.json")
        self.server = PabloServer("127.0.0.1", 0, self.users_file)   # port 0 = any free port
        self.server.start()
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.server.stop()
        self.tmp.cleanup()

    def client(self):
        c = Client(self.server.port)
        self.clients.append(c)
        return c

    def logged_in(self, name, password="pass1234"):
        c = self.client()
        c.request("register", username=name, password=password)
        reply = c.request("login", username=name, password=password)
        self.assertEqual(reply["type"], "login_ok", reply)
        return c


class AccountTests(ServerTestCase):
    def test_register_and_login(self):
        c = self.client()
        self.assertEqual(c.request("register", username="amin", password="secret")["type"], "register_ok")
        reply = c.request("login", username="amin", password="secret")
        self.assertEqual(reply["type"], "login_ok")
        self.assertEqual(len(reply["token"]), 32)
        self.assertEqual(reply["udp_port"], 5001)

    def test_password_is_not_stored(self):
        self.client().request("register", username="amin", password="supersecret")
        with open(self.users_file, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("amin", content)
        self.assertNotIn("supersecret", content)

    def test_accounts_survive_restart(self):
        self.client().request("register", username="amin", password="secret")
        self.server.stop()
        self.server = PabloServer("127.0.0.1", 0, self.users_file)
        self.server.start()
        self.assertEqual(self.client().request("login", username="amin", password="secret")["type"], "login_ok")

    def test_wrong_password(self):
        c = self.client()
        c.request("register", username="amin", password="secret")
        reply = c.request("login", username="amin", password="nope")
        self.assertEqual((reply["type"], reply["for"]), ("error", "login"))

    def test_unknown_user(self):
        self.assertEqual(self.client().request("login", username="ghost", password="xxxx")["type"], "error")

    def test_username_taken_case_insensitive(self):
        c = self.client()
        c.request("register", username="amin", password="secret")
        self.assertEqual(c.request("register", username="AMIN", password="secret")["type"], "error")

    def test_bad_username_and_password(self):
        c = self.client()
        for name, pw in (("ab", "secret"), ("has space", "secret"), ("x" * 17, "secret"), ("amin", "abc")):
            self.assertEqual(c.request("register", username=name, password=pw)["type"], "error", name)

    def test_no_double_login(self):
        self.logged_in("amin")
        reply = self.client().request("login", username="amin", password="pass1234")
        self.assertIn("already logged in", reply["message"])

    def test_can_log_in_again_after_disconnect(self):
        c = self.logged_in("amin")
        c.close()
        for _ in range(50):     # the server notices the disconnect in its own thread
            if "amin" not in self.server.sessions:
                break
            threading.Event().wait(0.02)
        reply = self.client().request("login", username="amin", password="pass1234")
        self.assertEqual(reply["type"], "login_ok")

    def test_logout_closes_connection(self):
        c = self.logged_in("amin")
        c.conn.send(p.make("logout"))
        self.assertIsNone(c.conn.recv())


class MessageHandlingTests(ServerTestCase):
    def test_must_log_in_first(self):
        reply = self.client().request("list_rooms")
        self.assertEqual(reply["message"], "log in first")

    def test_invalid_message_gets_error_and_connection_stays_open(self):
        c = self.client()
        self.assertEqual(c.request("swap", slot="abc")["type"], "error")
        self.assertEqual(c.request("register", username="amin", password="secret")["type"], "register_ok")

    def test_garbage_bytes_get_error(self):
        c = self.client()
        c.conn.sock.sendall(b"this is not json\n")
        self.assertEqual(c.conn.recv()["type"], "error")

    def test_udp_message_refused_on_tcp(self):
        reply = self.client().request("heartbeat", token="x", seq=1)
        self.assertEqual(reply["type"], "error")

    def test_spammer_is_disconnected(self):
        c = self.client()
        for _ in range(MAX_BAD_MESSAGES):
            c.conn.sock.sendall(b"junk\n")
        replies = [c.conn.recv() for _ in range(MAX_BAD_MESSAGES)]
        self.assertTrue(all(r["type"] == "error" for r in replies))
        self.assertIsNone(c.conn.recv())          # then the server hangs up

    def test_feature_not_built_yet(self):
        c = self.logged_in("amin")
        self.assertEqual(c.request("list_rooms")["message"], "not available yet")


class ConcurrencyTests(ServerTestCase):
    def test_many_clients_at_the_same_time(self):
        results = {}

        def player(i):
            c = Client(self.server.port)
            try:
                c.request("register", username=f"player{i}", password="pass1234")
                results[i] = c.request("login", username=f"player{i}", password="pass1234")["type"]
            finally:
                c.close()

        threads = [threading.Thread(target=player, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, {i: "login_ok" for i in range(10)})

    def test_one_client_crashing_does_not_affect_others(self):
        a = self.logged_in("alice")
        b = self.logged_in("bob")
        b.conn.sock.close()                          # bob's program crashes
        self.assertEqual(a.request("register", username="carol", password="pass1234")["type"], "register_ok")

    def test_silent_client_does_not_block_others(self):
        self.client()                               # connects and never sends anything
        self.logged_in("alice")                     # alice is still served immediately


if __name__ == "__main__":
    unittest.main()
