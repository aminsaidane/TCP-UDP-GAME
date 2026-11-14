"""Client tests: the network layer against a real server, and the screen flow.

Skipped automatically on a PC without pygame (e.g. the server PC).
"""
import logging
import os
import tempfile
import time
import unittest

from common import config
from common import protocol as p
from client.network import NetworkClient, parse_address
from server.main import PabloServer

logging.disable(logging.CRITICAL)

try:
    import pygame
    HAVE_PYGAME = True
except ImportError:
    HAVE_PYGAME = False


def wait_for(get, timeout=3.0):
    """Polls `get()` until it returns something truthy."""
    end = time.time() + timeout
    while time.time() < end:
        value = get()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out")


class ParseAddressTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_address("192.168.1.12"), ("192.168.1.12", config.TCP_PORT))
        self.assertEqual(parse_address("192.168.1.12:6000"), ("192.168.1.12", 6000))
        self.assertEqual(parse_address("  "), (config.SERVER_HOST, config.TCP_PORT))


class LiveServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = PabloServer("127.0.0.1", 0, os.path.join(self.tmp.name, "users.json"),
                                  udp_port=0, discovery_port=0)
        self.server.start()
        self.address = f"127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()


class NetworkClientTests(LiveServerCase):
    def collect(self, net, msg_type):
        seen = []

        def get():
            seen.extend(net.messages())
            return next((m for m in seen if m["type"] == msg_type), None)
        return wait_for(get)

    def test_connect_send_receive(self):
        net = NetworkClient()
        net.connect("127.0.0.1", self.server.port,
                    first_message=p.make("register", username="amin", password="pass1234"))
        self.assertEqual(self.collect(net, "register_ok")["username"], "amin")
        net.close()

    def test_unreachable_server(self):
        net = NetworkClient()
        net.connect("127.0.0.1", 1)                       # nothing listens on port 1
        self.assertIn("cannot reach", self.collect(net, "_connect_failed")["message"])

    def test_server_closing_the_connection_is_reported(self):
        net = NetworkClient()
        net.connect("127.0.0.1", self.server.port,
                    first_message=p.make("register", username="amin", password="pass1234"))
        self.collect(net, "register_ok")
        net.send(p.make("login", username="amin", password="pass1234"))
        self.collect(net, "login_ok")
        self.server.sessions["amin"].conn.close()        # the server side hangs up
        self.collect(net, "_disconnected")

    def test_messages_never_block(self):
        net = NetworkClient()
        start = time.time()
        self.assertEqual(net.messages(), [])
        self.assertLess(time.time() - start, 0.05)


@unittest.skipUnless(HAVE_PYGAME, "pygame is not installed")
class ScreenFlowTests(LiveServerCase):
    """Drives the real App (no window needed) through login -> lobby -> room."""

    def app(self, name):
        from client.main import App
        app = App(self.address)
        login = app.screen
        login.username.value, login.password.value = name, "pass1234"
        login.register()
        wait_for(lambda: (app.update(), app.username)[1])
        return app

    def pump(self, *apps, until):
        def get():
            for a in apps:
                a.update()
            return until()
        wait_for(get)

    def test_full_flow(self):
        amin, sara, omar = self.app("amin"), self.app("sara"), self.app("omar")
        self.assertEqual(type(amin.screen).__name__, "LobbyScreen")

        amin.screen.room_name.value = "Friday"
        amin.screen.create()
        self.pump(amin, until=lambda: amin.room)
        self.assertEqual(type(amin.screen).__name__, "RoomScreen")
        self.pump(sara, until=lambda: sara.rooms)          # pushed room list

        sara.send(p.make("join_room", room_id=sara.rooms[0]["id"]))
        omar.send(p.make("join_room", room_id=sara.rooms[0]["id"]))
        self.pump(amin, sara, omar, until=lambda: amin.room["can_start"])

        amin.screen.start()
        self.pump(amin, sara, omar, until=lambda: omar.room["status"] == "playing")

    def test_game_table(self):
        amin, sara, omar = self.app("amin"), self.app("sara"), self.app("omar")
        amin.screen.create()
        self.pump(amin, until=lambda: amin.room)
        for other in (sara, omar):
            other.send(p.make("join_room", room_id=amin.room["id"]))
            self.pump(amin, sara, omar, until=lambda o=other: o.room)
        amin.screen.start()
        self.pump(amin, sara, omar, until=lambda: all(type(a.screen).__name__ == "TableScreen"
                                                      and a.screen.state for a in (amin, sara, omar)))
        table = amin.screen
        self.assertEqual(table.state["phase"], "peek")
        # click 2 of my own cards -> peek -> they are shown face-up for a few seconds
        table.click_card("amin", 0)
        table.click_card("amin", 3)
        self.pump(amin, until=lambda: table._card_shown("amin", 0) and table._card_shown("amin", 3))
        self.assertIsNone(table._card_shown("amin", 1))
        self.assertIsNone(sara.screen._card_shown("amin", 0))      # sara never sees amin's cards
        for a in (sara, omar):
            a.screen.click_card(a.username, 1)
            a.screen.click_card(a.username, 2)
        self.pump(amin, sara, omar, until=lambda: table.state["phase"] == "turn")
        # amin's turn: draw shows the card to amin only
        amin.send(p.make("draw"))
        self.pump(amin, sara, omar, until=lambda: sara.screen.state["holding_drawn"])
        self.pump(amin, until=lambda: table.state["drawn"])
        self.assertIsNone(sara.screen.state["drawn"])
        self.assertIn("You drew a card:", " ".join(table.log))
        self.assertIn("amin drew a card", " ".join(sara.screen.log))
        # amin throws it: during the cut window sara can click one of her cards to cut
        amin.send(p.make("throw_drawn"))
        self.pump(amin, sara, omar, until=lambda: sara.screen.state["phase"] in ("cut_window", "power"))
        if sara.screen.state["phase"] == "power":           # amin drew a Jack/Queen
            amin.send(p.make("skip_power"))
            self.pump(amin, sara, omar, until=lambda: sara.screen.state["phase"] == "cut_window")
        self.assertTrue(sara.screen._clickable("sara"))
        self.assertFalse(sara.screen._clickable("amin"))     # only your own cards
        sara.screen.click_card("sara", 0)
        self.pump(amin, sara, omar, until=lambda: any(" cut with the " in line or " CUT with the " in line
                                                     for line in amin.screen.log))

    def test_pablo_results_and_next_round(self):
        amin, sara, omar = self.app("amin"), self.app("sara"), self.app("omar")
        amin.screen.create()
        self.pump(amin, until=lambda: amin.room)
        for other in (sara, omar):
            other.send(p.make("join_room", room_id=amin.room["id"]))
            self.pump(amin, sara, omar, until=lambda o=other: o.room)
        amin.screen.start()
        everyone = (amin, sara, omar)
        self.pump(*everyone, until=lambda: all(type(a.screen).__name__ == "TableScreen" and a.screen.state
                                               for a in everyone))
        for a in everyone:
            a.screen.click_card(a.username, 0)
            a.screen.click_card(a.username, 1)
        self.pump(*everyone, until=lambda: amin.screen.state["phase"] == "turn")
        table = amin.screen
        table._pablo()                                   # 1st click only asks for confirmation
        self.assertIn("Click again", table.message)
        table._pablo()                                   # 2nd click calls Pablo
        self.pump(*everyone, until=lambda: all(a.screen.round_result for a in everyone))
        result = sara.screen.round_result["result"]
        self.assertEqual((result["reason"], result["caller"]), ("pablo", "amin"))
        self.assertEqual(len(result["hands"]["omar"]), 4)            # everyone's cards are revealed
        amin.draw(pygame.Surface((1000, 680)))                       # host sees "Next round"
        self.assertTrue(table.next_btn.visible)
        sara.draw(pygame.Surface((1000, 680)))
        self.assertFalse(sara.screen.next_btn.visible)
        table.next_btn.on_click()
        self.pump(*everyone, until=lambda: sara.screen.state and sara.screen.state["round"] == 2)
        self.assertIsNone(sara.screen.round_result)

    def test_wrong_password_shows_error(self):
        from client.main import App
        self.app("amin").logout()
        app = App(self.address)
        app.screen.username.value, app.screen.password.value = "amin", "nope"
        app.screen.login()
        self.pump(app, until=lambda: "wrong" in app.screen.message)
        self.assertEqual(type(app.screen).__name__, "LoginScreen")

    def test_leave_room_returns_to_lobby(self):
        amin = self.app("amin")
        amin.screen.create()
        self.pump(amin, until=lambda: amin.room)
        amin.screen.leave()
        self.pump(amin, until=lambda: type(amin.screen).__name__ == "LobbyScreen")

    def test_server_crash_returns_to_login(self):
        amin = self.app("amin")
        self.server.sessions["amin"].conn.close()   # the connection dies on the server side
        self.pump(amin, until=lambda: type(amin.screen).__name__ == "LoginScreen")
        self.assertIn("Lost the connection", amin.screen.message)


if __name__ == "__main__":
    unittest.main()
