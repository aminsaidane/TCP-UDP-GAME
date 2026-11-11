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


# The reply each request is waiting for (anything else that arrives meanwhile,
# like a pushed room list, is kept in the client's inbox).
REPLIES = {"register": "register_ok", "login": "login_ok", "list_rooms": "rooms",
           "create_room": "room_joined", "join_room": "room_joined", "leave_room": "room_left",
           "start_game": "room_update"}


class Client:
    """A minimal test client speaking the real protocol."""

    def __init__(self, port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.conn = p.Connection(sock)
        self.inbox = []

    def request(self, msg_type, **fields):
        """Sends a request and returns its reply (or its error)."""
        self.conn.send(p.make(msg_type, **fields))
        expected = REPLIES.get(msg_type)
        while True:
            msg = self.conn.recv()
            if msg is None or msg["type"] == expected or (
                    msg["type"] == "error" and msg.get("for") == msg_type) or expected is None:
                return msg
            self.inbox.append(msg)

    def expect(self, msg_type):
        """The next message of this type (from the inbox first, then the network)."""
        for i, msg in enumerate(self.inbox):
            if msg["type"] == msg_type:
                return self.inbox.pop(i)
        while True:
            msg = self.conn.recv()
            if msg is None:
                raise AssertionError(f"connection closed while waiting for '{msg_type}'")
            if msg["type"] == msg_type:
                return msg
            self.inbox.append(msg)

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
        self.assertEqual(c.request("draw")["message"], "not available yet")


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


class LobbyTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.amin, self.sara, self.omar, self.lina = (
            self.logged_in(n) for n in ("amin", "sara", "omar", "lina"))

    def test_create_room(self):
        reply = self.amin.request("create_room", name="Friday", size=3)
        room = reply["room"]
        self.assertEqual((room["name"], room["size"], room["host"], room["players"], room["status"]),
                         ("Friday", 3, "amin", ["amin"], "waiting"))
        self.assertFalse(room["can_start"])

    def test_room_list_is_pushed_to_the_lobby(self):
        self.amin.request("create_room", name="Friday", size=3)
        rooms = self.sara.expect("rooms")["rooms"]        # sara did not ask: it was pushed
        self.assertEqual([r["name"] for r in rooms], ["Friday"])
        self.assertEqual(self.omar.request("list_rooms")["rooms"][0]["host"], "amin")

    def test_join_and_room_update(self):
        room_id = self.amin.request("create_room", name="Friday", size=3)["room"]["id"]
        self.assertEqual(self.sara.request("join_room", room_id=room_id)["room"]["players"], ["amin", "sara"])
        self.assertEqual(self.amin.expect("room_update")["room"]["players"], ["amin", "sara"])

    def test_start_button_enabled_at_three(self):
        room_id = self.amin.request("create_room", name="Friday", size=4)["room"]["id"]
        self.sara.request("join_room", room_id=room_id)
        self.assertFalse(self.amin.expect("room_update")["room"]["can_start"])
        self.assertEqual(self.amin.request("start_game")["message"], "at least 3 players are needed")
        self.omar.request("join_room", room_id=room_id)
        self.assertTrue(self.amin.expect("room_update")["room"]["can_start"])

    def test_only_host_can_start(self):
        room_id = self.amin.request("create_room", name="Friday", size=3)["room"]["id"]
        self.sara.request("join_room", room_id=room_id)
        self.omar.request("join_room", room_id=room_id)
        self.sara.expect("room_update")                     # omar joined
        self.amin.expect("room_update")
        self.amin.expect("room_update")
        self.assertEqual(self.sara.request("start_game")["message"], "only the host can start the game")
        self.assertEqual(self.amin.request("start_game")["room"]["status"], "playing")
        self.assertEqual(self.omar.expect("room_update")["room"]["players"], ["amin", "sara", "omar"])

    def test_cannot_join_full_or_playing_room(self):
        room_id = self.amin.request("create_room", name="Small", size=3)["room"]["id"]
        self.sara.request("join_room", room_id=room_id)
        self.omar.request("join_room", room_id=room_id)
        self.assertEqual(self.lina.request("join_room", room_id=room_id)["message"], "room is full")
        room_id = self.lina.request("create_room", name="Big", size=4)["room"]["id"]
        self.amin.request("leave_room")
        self.amin.request("join_room", room_id=room_id)
        self.sara.request("leave_room")
        self.sara.request("join_room", room_id=room_id)
        self.lina.request("start_game")
        self.assertEqual(self.omar.request("join_room", room_id=room_id)["message"],
                         "you are already in a room")
        self.omar.request("leave_room")
        self.assertEqual(self.omar.request("join_room", room_id=room_id)["message"],
                         "this game has already started")

    def test_one_room_at_a_time(self):
        self.amin.request("create_room", name="A", size=3)
        self.assertEqual(self.amin.request("create_room", name="B", size=3)["type"], "error")

    def test_invalid_room_settings(self):
        self.assertEqual(self.amin.request("create_room", name="X", size=5)["type"], "error")
        self.assertEqual(self.amin.request("create_room", name="   ", size=3)["type"], "error")
        self.assertEqual(self.amin.request("join_room", room_id=999)["message"], "this room does not exist")

    def test_host_leaves_next_player_becomes_host(self):
        room_id = self.amin.request("create_room", name="Friday", size=3)["room"]["id"]
        self.sara.request("join_room", room_id=room_id)
        self.assertEqual(self.amin.request("leave_room")["type"], "room_left")
        self.assertEqual(self.sara.expect("room_update")["room"]["host"], "sara")

    def test_last_player_leaving_deletes_room(self):
        self.amin.request("create_room", name="Friday", size=3)
        self.amin.request("leave_room")
        self.assertEqual(self.amin.expect("rooms")["rooms"], [])

    def test_disconnect_removes_player_from_room(self):
        room_id = self.amin.request("create_room", name="Friday", size=3)["room"]["id"]
        self.sara.request("join_room", room_id=room_id)
        self.amin.expect("room_update")
        self.sara.close()                                   # sara's PC crashes
        update = self.amin.expect("room_update")["room"]
        self.assertEqual(update["players"], ["amin"])

    def test_many_players_join_at_once(self):
        """4 players race for the 3 free seats of a 4-player room: exactly 3 get in."""
        room_id = self.amin.request("create_room", name="Race", size=4)["room"]["id"]
        others = [self.sara, self.omar, self.lina, self.logged_in("zied")]
        results = {}

        def join(c):
            results[c] = c.request("join_room", room_id=room_id)["type"]

        threads = [threading.Thread(target=join, args=(c,)) for c in others]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results.values()), ["error", "room_joined", "room_joined", "room_joined"])
        self.assertEqual(len(self.amin.request("list_rooms")["rooms"][0]["players"]), 4)

    def test_room_updates_arrive_in_order(self):
        """Regression: two joins at the same moment must not leave the host with a stale room."""
        for _ in range(20):
            room_id = self.amin.request("create_room", name="Order", size=3)["room"]["id"]
            threads = [threading.Thread(target=c.request, args=("join_room",), kwargs={"room_id": room_id})
                       for c in (self.sara, self.omar)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.amin.expect("room_update")
            last = self.amin.expect("room_update")["room"]       # the 2nd update must be the newest
            self.assertEqual(len(last["players"]), 3)
            for c in (self.sara, self.omar, self.amin):
                c.request("leave_room")
            self.amin.inbox.clear()


if __name__ == "__main__":
    unittest.main()
