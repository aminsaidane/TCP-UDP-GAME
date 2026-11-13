"""A real game over the network: 3 clients play against a running server."""
import unittest
from unittest import mock

from common import config
from tests.test_server import ServerTestCase


class GameTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.players = {n: self.logged_in(n) for n in ("amin", "sara", "omar")}
        amin = self.players["amin"]
        room_id = amin.request("create_room", name="Game", size=3)["room"]["id"]
        for n in ("sara", "omar"):
            self.players[n].request("join_room", room_id=room_id)
        amin.conn.send({"type": "start_game"})
        for c in self.players.values():
            c.expect("game_start")
        self.state = {n: c.expect("state")["state"] for n, c in self.players.items()}

    def act(self, name, msg_type, **fields):
        """Sends an action; returns the actor's event (or error) and refreshes everyone's state."""
        c = self.players[name]
        c.conn.send({"type": msg_type, **fields})
        while True:
            msg = c.conn.recv()
            if msg["type"] == "error":
                return msg
            if msg["type"] == "event":
                event = msg["event"]
                break
        c.inbox.append(c.expect("state"))   # keep the matching state for refresh()
        self.refresh()
        return event

    def refresh(self):
        for n, c in self.players.items():
            self.state[n] = c.expect("state")["state"]

    def wait_event(self, name, action):
        """Skips older events until one with this action arrives."""
        while True:
            event = self.players[name].expect("event")["event"]
            if event["action"] == action:
                return event

    def peek_all(self):
        for n in self.players:
            self.act(n, "peek", slots=[0, 1])

    def current(self):
        return self.state["amin"]["current"]

    def test_game_starts_with_4_hidden_cards_each(self):
        s = self.state["sara"]
        self.assertEqual(s["phase"], "peek")
        self.assertEqual(s["players"], ["amin", "sara", "omar"])
        self.assertEqual(s["slots"], {n: [True] * 4 for n in ("amin", "sara", "omar")})
        self.assertEqual(s["deck_count"], 40 - 12)
        self.assertNotIn("hands", str(s))         # no card faces anywhere in the state

    def test_peek_is_private(self):
        event = self.act("sara", "peek", slots=[1, 3])
        self.assertEqual(sorted(event["private"]["cards"]), ["1", "3"])   # JSON keys are text
        other = self.players["amin"].expect("event")["event"]
        self.assertEqual(other, {"action": "peek", "player": "sara"})     # no cards for others

    def test_draw_is_private_and_throw_is_public(self):
        self.peek_all()
        me = self.current()
        drawn = self.act(me, "draw")["private"]["card"]
        self.assertEqual(self.state[me]["drawn"], drawn)
        others = [n for n in self.players if n != me]
        for n in others:
            self.assertIsNone(self.state[n]["drawn"])
            self.assertTrue(self.state[n]["holding_drawn"])
        event = self.act(me, "throw_drawn")
        self.assertEqual(event["card"], drawn)
        self.assertEqual(self.state[others[0]]["pile_top"], drawn)

    def test_illegal_moves_are_refused(self):
        self.assertEqual(self.act("amin", "draw")["message"], "not allowed during phase 'peek'")
        self.peek_all()
        not_me = next(n for n in self.players if n != self.current())
        self.assertEqual(self.act(not_me, "draw")["message"], "not your turn")
        self.assertEqual(self.act(self.current(), "swap", slot=0)["type"], "error")

    def test_cut_window_closes_and_turn_passes(self):
        self.peek_all()
        first = self.current()
        self.act(first, "draw")
        event = self.act(first, "throw_drawn")
        if self.state[first]["phase"] == "power":   # drew a Jack or Queen
            self.act(first, "skip_power")
        self.assertEqual(self.state[first]["phase"], "cut_window")
        self.assertAlmostEqual(self.state[first]["cut_remaining"], config.CUT_WINDOW_SECONDS, delta=0.5)
        closed = self.wait_event(first, "cut_window_closed")         # sent by the server's timer
        self.assertIn(closed["next"], ["amin", "sara", "omar"])
        self.refresh()
        players = ["amin", "sara", "omar"]
        self.assertEqual(self.current(), players[(players.index(first) + 1) % 3])
        self.assertEqual(self.state["omar"]["phase"], "turn")
        del event

    @mock.patch.object(config, "CUT_WINDOW_SECONDS", 0.05)
    def test_several_turns(self):
        self.peek_all()
        for _ in range(9):
            me = self.current()
            self.act(me, "draw")
            self.act(me, "swap", slot=2)
            if self.state[me]["phase"] == "power":
                self.act(me, "skip_power")
            self.wait_event(me, "cut_window_closed")
            self.refresh()
            self.assertNotEqual(self.current(), me)
        self.assertEqual(self.state["amin"]["deck_count"], 40 - 12 - 9)

    def test_not_in_a_game(self):
        lina = self.logged_in("lina")
        self.assertEqual(lina.request("draw")["message"], "you are not in a game")


class CutTests(GameTests):
    test_game_starts_with_4_hidden_cards_each = test_peek_is_private = None
    test_draw_is_private_and_throw_is_public = test_illegal_moves_are_refused = None
    test_cut_window_closes_and_turn_passes = test_several_turns = test_not_in_a_game = None

    """Cutting over the network. Hands are rigged so the outcome is known."""

    def setUp(self):
        super().setUp()
        self.peek_all()
        self.first = self.current()
        order = ["amin", "sara", "omar"]
        i = order.index(self.first)
        self.second, self.third = order[(i + 1) % 3], order[(i + 2) % 3]

    def rig(self, deck_top, **hands):
        from common.cards import Card
        game = next(iter(self.server.games.values()))
        with self.server.lock:
            for name, codes in hands.items():
                game.round.hands[name] = [Card.from_code(c) for c in codes]
            game.round.deck.append(Card.from_code(deck_top))

    def throw_five(self):
        """The first player draws a 5 and throws it: the cut window for 5s opens."""
        self.act(self.first, "draw")
        self.act(self.first, "throw_drawn")
        self.assertEqual(self.state[self.second]["cut_rank"], "5")

    def test_correct_cut(self):
        self.rig("5S", **{self.second: ["5D", "1C", "2C", "3C"]})
        self.throw_five()
        event = self.act(self.second, "cut", slot=0)
        self.assertEqual((event["card"], event["correct"]), ("5D", True))
        self.assertEqual(self.state[self.third]["slots"][self.second], [False, True, True, True])
        self.assertEqual(self.state[self.third]["pile_top"], "5D")
        # the window closes; play continues with the player after the cutter
        self.wait_event(self.first, "cut_window_closed")
        self.refresh()
        self.assertEqual(self.current(), self.third)

    def test_wrong_cut_gives_penalty(self):
        self.rig("5S", **{self.second: ["7D", "1C", "2C", "3C"]})
        self.throw_five()
        event = self.act(self.second, "cut", slot=0)
        self.assertEqual((event["card"], event["correct"]), ("7D", False))
        self.assertEqual(self.state[self.first]["slots"][self.second], [True] * 5)
        self.wait_event(self.first, "cut_window_closed")
        self.refresh()
        self.assertEqual(self.current(), self.second)    # a wrong cut does not change the turn order

    def test_two_cuts_last_one_decides(self):
        self.rig("5S", **{self.second: ["5D", "1C"], self.third: ["5C", "1D"]})
        self.throw_five()
        self.act(self.third, "cut", slot=0)
        self.act(self.second, "cut", slot=0)             # arrives last
        closed = self.wait_event(self.first, "cut_window_closed")
        self.assertEqual(closed["next"], self.third)     # the player after the LAST cutter

    def test_simultaneous_cuts_are_seen_in_the_same_order_by_everyone(self):
        """Both cut at the same instant: the server picks an order, and every client sees that same order."""
        import threading
        self.rig("5S", **{self.second: ["5D", "1C"], self.third: ["5C", "1D"]})
        self.throw_five()
        go = threading.Event()

        def cut(name):
            go.wait()
            self.players[name].conn.send({"type": "cut", "slot": 0})

        threads = [threading.Thread(target=cut, args=(n,)) for n in (self.second, self.third)]
        for t in threads:
            t.start()
        go.set()                                         # release both at once
        for t in threads:
            t.join()
        orders = {}
        for name in self.players:
            cuts = []
            while len(cuts) < 2:
                event = self.players[name].expect("event")["event"]
                if event["action"] == "cut":
                    cuts.append(event["player"])
            orders[name] = cuts
        self.assertEqual(len({tuple(o) for o in orders.values()}), 1, orders)   # same order everywhere
        closed = self.wait_event(self.first, "cut_window_closed")
        order = ["amin", "sara", "omar"]
        last = orders[self.first][-1]
        self.assertEqual(closed["next"], order[(order.index(last) + 1) % 3])

    def test_cut_on_a_pair(self):
        self.rig("1H", **{self.first: ["4H", "4S", "1C", "2C"], self.second: ["4D", "1S"]})
        self.act(self.first, "throw_pair", slots=[0, 1])
        self.assertTrue(self.act(self.second, "cut", slot=0)["correct"])

    def test_cutting_your_last_card_wins_the_round(self):
        self.rig("5S", **{self.second: ["5D"]})
        self.throw_five()
        self.act(self.second, "cut", slot=0)
        self.assertEqual(self.state[self.first]["phase"], "round_over")
        self.assertEqual(self.state[self.first]["result"]["winner"], self.second)

    def test_no_cut_outside_the_window(self):
        self.rig("5S", **{self.second: ["5D", "1C"]})
        self.assertEqual(self.act(self.second, "cut", slot=0)["message"], "not allowed during phase 'turn'")
        self.throw_five()
        self.wait_event(self.second, "cut_window_closed")
        self.refresh()
        self.assertEqual(self.act(self.second, "cut", slot=0)["type"], "error")

    def test_no_cut_while_a_power_is_used(self):
        self.rig("QS", **{self.second: ["QD", "1C"]})
        self.act(self.first, "draw")
        self.act(self.first, "throw_drawn")              # Queen: power first, then the cut window
        self.assertEqual(self.act(self.second, "cut", slot=0)["message"], "not allowed during phase 'power'")
        self.act(self.first, "skip_power")
        self.assertTrue(self.act(self.second, "cut", slot=0)["correct"])


class RoundEndTests(CutTests):
    """Pablo, round results, next round, match end. amin is the host and plays first in round 1."""
    test_correct_cut = test_wrong_cut_gives_penalty = test_two_cuts_last_one_decides = None
    test_simultaneous_cuts_are_seen_in_the_same_order_by_everyone = test_cut_on_a_pair = None
    test_cutting_your_last_card_wins_the_round = test_no_cut_outside_the_window = None
    test_no_cut_while_a_power_is_used = None

    def call_pablo(self, name):
        self.act(name, "pablo")
        return {n: c.expect("round_over") for n, c in self.players.items()}

    def test_correct_pablo(self):
        self.rig("1S", amin=["1H", "KH"], sara=["2D", "3D"], omar=["7H", "JC"])
        over = self.call_pablo("amin")
        result = over["omar"]["result"]
        self.assertEqual((result["reason"], result["caller"], result["pablo_correct"]), ("pablo", "amin", True))
        self.assertEqual(result["scores"], {"amin": -5, "sara": 5, "omar": 17})
        self.assertEqual(result["hands"]["sara"], ["2D", "3D"])          # every hand revealed
        self.assertEqual(over["sara"]["scores"], {"amin": -5, "sara": 5, "omar": 17})
        self.assertFalse(over["amin"]["match_over"])
        self.assertEqual(self.state["sara"]["phase"], "round_over")

    def test_wrong_pablo_costs_50(self):
        self.rig("1S", amin=["4H", "3H"], sara=["1D", "2D"], omar=["7H"])
        result = self.call_pablo("amin")["amin"]["result"]
        self.assertFalse(result["pablo_correct"])
        self.assertEqual(result["winner"], "sara")
        self.assertEqual(result["scores"], {"amin": 57, "sara": -5, "omar": 7})

    def test_tie_nothing_happens(self):
        self.rig("1S", amin=["1H", "2H"], sara=["3D"], omar=["7H"])
        result = self.call_pablo("amin")["amin"]["result"]
        self.assertIsNone(result["winner"])
        self.assertEqual(result["scores"], {"amin": 3, "sara": 3, "omar": 7})

    def test_next_round_host_only_and_scores_carry_over(self):
        self.assertEqual(self.players["amin"].request("next_round")["message"], "the round is not finished")
        self.rig("1S", amin=["1H", "KH"], sara=["2D", "3D"], omar=["7H", "JC"])
        self.call_pablo("amin")
        self.assertEqual(self.players["sara"].request("next_round")["message"],
                         "only the host can start the next round")
        self.players["amin"].conn.send({"type": "next_round"})
        starts = {n: c.expect("game_start") for n, c in self.players.items()}
        self.assertEqual(starts["omar"]["round"], 2)
        self.assertEqual(starts["omar"]["scores"], {"amin": -5, "sara": 5, "omar": 17})
        self.refresh()
        self.assertEqual(self.state["omar"]["phase"], "peek")
        self.assertEqual(self.state["omar"]["slots"]["sara"], [True] * 4)   # fresh hands
        self.assertEqual(self.state["omar"]["current"], "sara")             # the first player rotates

    def test_match_ends_at_100_and_room_can_play_again(self):
        game = next(iter(self.server.games.values()))
        with self.server.lock:
            game.match.scores.update({"amin": 60, "sara": 10, "omar": 20})
        self.rig("1S", amin=["7H", "7C"], sara=["1D"], omar=["2C"])
        over = self.call_pablo("amin")                                     # wrong: 60 + 14 + 50
        self.assertTrue(over["omar"]["match_over"])
        final = {n: c.expect("match_over") for n, c in self.players.items()}
        self.assertEqual(final["sara"]["scores"], {"amin": 124, "sara": 5, "omar": 22})
        self.assertEqual(final["sara"]["winners"], ["sara"])
        room = self.players["omar"].expect("room_update")["room"]
        while room["status"] == "playing":                                 # skip the older update
            room = self.players["omar"].expect("room_update")["room"]
        self.assertEqual((room["status"], room["can_start"]), ("waiting", True))
        self.assertEqual(self.players["amin"].request("next_round")["message"], "you are not in a game")
        # same room, new match: scores start from 0 again
        self.players["amin"].conn.send({"type": "start_game"})
        start = self.players["sara"].expect("game_start")
        self.assertEqual((start["round"], start["scores"]), (1, {"amin": 0, "sara": 0, "omar": 0}))


if __name__ == "__main__":
    unittest.main()
