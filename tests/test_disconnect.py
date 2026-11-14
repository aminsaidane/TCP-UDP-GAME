"""Disconnections, rejoining as a watcher, and timeouts, against a running server."""
import socket
import time
from unittest import mock

from common import config
from common import protocol as p
from tests import test_game          # imported as a module so its tests don't run twice
from tests.test_server import Client


class DisconnectTests(test_game.GameTests):
    """amin (host), sara and omar are playing; amin plays first."""

    def setUp(self):
        super().setUp()
        self.order = ["amin", "sara", "omar"]

    def drop(self, name):
        """Simulates a crash: the connection dies without a logout."""
        self.players[name].conn.sock.close()
        del self.players[name]
        self.state.pop(name, None)

    def next_state(self, name, until):
        """Reads states until `until(state)` is true."""
        for _ in range(20):
            s = self.players[name].expect("state")["state"]
            if until(s):
                self.state[name] = s
                return s
        raise AssertionError("state never matched")

    def relogin(self, name):
        c = Client(self.server.port)
        self.clients.append(c)
        self.assertEqual(c.request("login", username=name, password="pass1234")["type"], "login_ok")
        self.players[name] = c
        return c

    # ------------------------------------------------------------------ dropping out
    def test_drop_on_your_turn_passes_the_turn(self):
        self.peek_all()
        self.drop("amin")
        notice = self.players["sara"].expect("player_left")
        self.assertEqual((notice["username"], notice["reason"]), ("amin", "disconnected"))
        s = self.next_state("omar", lambda s: "amin" in s["left"])
        self.assertEqual(s["current"], "sara")
        self.assertEqual(s["phase"], "turn")
        self.assertEqual(s["slots"]["amin"], [False] * 4)            # cards back into the deck
        self.assertEqual(s["deck_count"], 40 - 8)

    def test_drop_while_holding_a_drawn_card(self):
        self.peek_all()
        self.act("amin", "draw")
        self.drop("amin")
        s = self.next_state("sara", lambda s: "amin" in s["left"])
        self.assertEqual((s["phase"], s["current"], s["holding_drawn"]), ("turn", "sara", False))
        self.assertEqual(s["deck_count"], 40 - 8)                   # the drawn card went back too

    def test_drop_while_using_a_power(self):
        self.peek_all()
        self.rig_top("QS")
        self.act("amin", "draw")
        self.act("amin", "throw_drawn")
        self.assertEqual(self.state["sara"]["phase"], "power")
        self.drop("amin")
        s = self.next_state("sara", lambda s: "amin" in s["left"])
        self.assertEqual((s["phase"], s["current"]), ("turn", "sara"))

    def test_drop_when_not_your_turn(self):
        self.peek_all()
        self.drop("omar")
        s = self.next_state("amin", lambda s: "omar" in s["left"])
        self.assertEqual((s["phase"], s["current"]), ("turn", "amin"))
        self.act("amin", "draw")                                     # the game goes on

    def test_drop_during_peek(self):
        self.act("amin", "peek", slots=[0, 1])
        self.act("sara", "peek", slots=[0, 1])
        self.drop("omar")                                            # omar never peeked
        s = self.next_state("amin", lambda s: "omar" in s["left"])
        self.assertEqual(s["phase"], "turn")                         # nobody waits for omar

    def test_room_keeps_the_seat(self):
        self.drop("sara")
        room = self.players["amin"].expect("room_update")["room"]
        while "sara" not in room["disconnected"]:
            room = self.players["amin"].expect("room_update")["room"]
        self.assertIn("sara", room["players"])

    # ------------------------------------------------------------------ coming back
    def test_rejoin_as_watcher_then_play_next_round(self):
        self.peek_all()
        self.drop("sara")
        self.next_state("amin", lambda s: "sara" in s["left"])
        sara = self.relogin("sara")
        self.assertEqual(sara.expect("room_joined")["room"]["name"], "Game")
        start = sara.expect("game_start")
        self.assertTrue(start["watching"])
        s = sara.expect("state")["state"]
        self.assertTrue(s["watching"])
        self.assertEqual(s["players"], ["amin", "sara", "omar"])
        self.assertEqual(s["left"], ["sara"])
        self.assertIn("not playing this round", sara.request("draw")["message"])
        # watchers see the game go on
        self.act("amin", "draw")
        self.assertEqual(sara.expect("event")["event"]["action"], "draw")
        # the round ends; sara scores nothing for it
        self.act("amin", "throw_drawn")
        if self.state["amin"]["phase"] == "power":
            self.act("amin", "skip_power")
        self.wait_event("amin", "cut_window_closed")
        cur = self.players["amin"].expect("state")["state"]["current"]
        self.players[cur].conn.send({"type": "pablo"})
        over = sara.expect("round_over")
        self.assertNotIn("sara", over["result"]["scores"])
        self.assertEqual(over["scores"]["sara"], 0)
        # next round: sara is dealt in again
        self.players["amin"].conn.send({"type": "next_round"})
        start = sara.expect("game_start")
        self.assertFalse(start["watching"])
        self.assertEqual(start["players"], ["amin", "sara", "omar"])

    def test_still_away_at_next_round_sits_out(self):
        self.peek_all()
        self.drop("sara")
        self.next_state("amin", lambda s: "sara" in s["left"])
        self.act("amin", "pablo")
        self.players["amin"].expect("round_over")
        self.players["amin"].conn.send({"type": "next_round"})
        start = self.players["omar"].expect("game_start")
        self.assertEqual(start["players"], ["amin", "omar"])

    def test_down_to_one_player_ends_the_match(self):
        self.peek_all()
        self.drop("sara")
        self.drop("omar")
        over = self.players["amin"].expect("round_over")
        self.assertEqual((over["result"]["reason"], over["result"]["winner"]), ("players_left", "amin"))
        self.assertTrue(over["match_over"])
        self.assertEqual(self.players["amin"].expect("match_over")["winners"], ["amin"])

    def test_leave_button_mid_game_frees_the_seat(self):
        self.peek_all()
        self.players["omar"].request("leave_room")
        notice = self.players["amin"].expect("player_left")
        self.assertEqual((notice["username"], notice["reason"]), ("omar", "left"))
        room = self.players["amin"].expect("room_update")["room"]
        while "omar" in room["players"]:
            room = self.players["amin"].expect("room_update")["room"]
        self.assertEqual(room["disconnected"], [])
        del self.players["omar"]                                     # omar is back in the lobby
        self.players["amin"].conn.send({"type": "draw"})
        self.assertEqual(self.wait_event("amin", "draw")["player"], "amin")   # the game goes on

    def test_host_drop_hands_over_host(self):
        self.drop("amin")
        room = self.players["sara"].expect("room_update")["room"]
        while room["host"] == "amin":
            room = self.players["sara"].expect("room_update")["room"]
        self.assertEqual(room["host"], "sara")

    # ------------------------------------------------------------------ timeouts
    @mock.patch.object(config, "TURN_TIMEOUT_SECONDS", 0.6)
    def test_idle_player_is_skipped(self):
        self.peek_all()
        event = self.wait_event("sara", "timeout")
        self.assertEqual((event["player"], event["phase"]), ("amin", "turn"))
        s = self.next_state("sara", lambda s: s["current"] != "amin")
        self.assertEqual(s["current"], "sara")

    @mock.patch.object(config, "TURN_TIMEOUT_SECONDS", 0.6)
    def test_idle_with_a_drawn_card(self):
        self.peek_all()
        self.act("amin", "draw")
        event = self.wait_event("sara", "timeout")
        self.assertEqual(event["phase"], "drawn")
        s = self.next_state("sara", lambda s: s["current"] == "sara")
        self.assertEqual(s["pile_top"], event["card"])              # the drawn card was thrown

    def test_nobody_peeks(self):
        game = next(iter(self.server.games.values()))
        with self.server.lock:
            game.turn_deadline = time.monotonic() + 0.5      # (the clock started before this test)
        event = self.wait_event("amin", "timeout")
        self.assertEqual((event["phase"], sorted(event["players"])), ("peek", ["amin", "omar", "sara"]))

    @mock.patch.object(config, "HEARTBEAT_TIMEOUT", 0.6)
    def test_silent_udp_client_is_disconnected(self):
        """sara's UDP heartbeats stop (cable pulled): the server drops her even though TCP looks open."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        token = self.server.sessions["sara"].token
        sock.sendto(p.encode_datagram(p.make("hello", token=token, seq=1)), ("127.0.0.1", self.server.udp.port))
        sock.close()
        notice = self.players["amin"].expect("player_left")
        self.assertEqual(notice["username"], "sara")
        sara = self.players["sara"]
        while sara.conn.recv() is not None:
            pass                                                     # the server closed her connection
        self.assertNotIn("sara", self.server.sessions)

    def test_no_udp_at_all_is_not_a_disconnection(self):
        """A client whose UDP is blocked by a firewall never sent a heartbeat: TCP alone decides."""
        with mock.patch.object(config, "HEARTBEAT_TIMEOUT", 0.3):
            time.sleep(1.2)
        self.assertIn("sara", self.server.sessions)

    # ------------------------------------------------------------------ helpers
    def rig_top(self, code):
        from common.cards import Card
        game = next(iter(self.server.games.values()))
        with self.server.lock:
            game.round.deck.append(Card.from_code(code))


for _name in dir(test_game.GameTests):
    if _name.startswith("test_") and _name not in DisconnectTests.__dict__:
        setattr(DisconnectTests, _name, None)
