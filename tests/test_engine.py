"""Tests for the Pablo rules engine. Run with:  python -m unittest -v"""
import random
import unittest

from common import config
from common.cards import Card, new_deck
from server.engine import (CUT_WINDOW, DRAWN, PEEK, POWER, ROUND_OVER, TURN,
                           GameError, Match, Round)

A, B, C = "alice", "bob", "carol"


def cards(*codes):
    return [Card.from_code(c) for c in codes]


def make_round(hands, deck=("1S", "2S", "3S", "4S", "5S"), pile=(), current=0):
    """A round already past the peek phase, with chosen hands / deck (top = last)."""
    r = Round(list(hands), rng=random.Random(0))
    r.hands = {p: cards(*h) for p, h in hands.items()}
    r.deck = cards(*deck)
    r.pile = cards(*pile)
    r.peeked = set(hands)
    r.phase = TURN
    r.current = current
    return r


class CardTests(unittest.TestCase):
    def test_deck_has_40_cards(self):
        deck = new_deck()
        self.assertEqual(len(deck), 40)
        self.assertEqual(len(set(deck)), 40)

    def test_points(self):
        self.assertEqual(Card("1", "S").points, 1)
        self.assertEqual(Card("7", "D").points, 7)
        for rank in "JQK":
            self.assertEqual(Card(rank, "S").points, 10)
        self.assertEqual(Card("K", "H").points, 0)  # King of Hearts

    def test_powers(self):
        self.assertEqual(Card("J", "C").power, "J")
        self.assertEqual(Card("Q", "C").power, "Q")
        self.assertIsNone(Card("K", "C").power)
        self.assertIsNone(Card("K", "H").power)

    def test_code_round_trip(self):
        self.assertEqual(Card.from_code("QH"), Card("Q", "H"))
        self.assertEqual(Card("7", "S").code(), "7S")


class SetupTests(unittest.TestCase):
    def test_deal(self):
        r = Round([A, B, C], rng=random.Random(1))
        for p in (A, B, C):
            self.assertEqual(r.hand_size(p), 4)
        self.assertEqual(len(r.deck), 40 - 12)
        self.assertEqual(r.phase, PEEK)

    def test_four_players(self):
        r = Round([A, B, C, "dave"], rng=random.Random(1))
        self.assertEqual(len(r.deck), 40 - 16)

    def test_peek_any_two_cards_once(self):
        r = Round([A, B, C], rng=random.Random(1))
        res = r.peek(A, [0, 3])
        self.assertEqual(res["private"]["cards"], {0: r.hands[A][0].code(), 3: r.hands[A][3].code()})
        with self.assertRaises(GameError):
            r.peek(A, [1, 2])                      # only once
        with self.assertRaises(GameError):
            r.peek(B, [1])                         # exactly two
        with self.assertRaises(GameError):
            r.draw(A)                              # no playing before everyone peeked
        r.peek(B, [1, 2])
        r.peek(C, [0, 1])
        self.assertEqual(r.phase, TURN)


class TurnTests(unittest.TestCase):
    def test_only_current_player_acts(self):
        r = make_round({A: ["5H"], B: ["6H"], C: ["7H"]})
        with self.assertRaises(GameError):
            r.draw(B)

    def test_draw_then_throw(self):
        r = make_round({A: ["5H", "6H"], B: ["6D"], C: ["7H"]}, deck=["3C"])
        res = r.draw(A)
        self.assertEqual(res["private"]["card"], "3C")
        self.assertEqual(r.phase, DRAWN)
        self.assertEqual(r.state_for(A)["drawn"], "3C")
        self.assertIsNone(r.state_for(B)["drawn"])   # others don't see it
        r.throw_drawn(A)
        self.assertEqual(r.pile[-1], Card("3", "C"))
        self.assertEqual(r.phase, CUT_WINDOW)
        self.assertEqual(r.hand_size(A), 2)

    def test_draw_then_swap(self):
        r = make_round({A: ["5H", "6H"], B: ["6D"], C: ["7H"]}, deck=["3C"])
        r.draw(A)
        res = r.swap_drawn(A, 1)
        self.assertEqual(res["card"], "6H")              # old card is shown to everyone
        self.assertEqual(r.hands[A], cards("5H", "3C"))
        self.assertEqual(r.pile[-1], Card("6", "H"))
        self.assertEqual(r.phase, CUT_WINDOW)

    def test_turn_passes_after_cut_window(self):
        r = make_round({A: ["5H", "6H"], B: ["6D"], C: ["7H"]}, deck=["3C"])
        r.draw(A)
        r.throw_drawn(A)
        res = r.close_cut_window()
        self.assertEqual(res["next"], B)
        self.assertEqual(r.phase, TURN)

    def test_correct_pair(self):
        r = make_round({A: ["5H", "2C", "5S"], B: ["6D"], C: ["7H"]})
        res = r.throw_pair(A, 0, 2)
        self.assertTrue(res["match"])
        self.assertEqual(r.hands[A], [None, Card("2", "C"), None])  # slots don't shift
        self.assertEqual(r.phase, CUT_WINDOW)
        self.assertEqual(r.cut_rank, "5")

    def test_wrong_pair_gives_penalty_and_ends_turn(self):
        r = make_round({A: ["5H", "2C"], B: ["6D"], C: ["7H"]}, deck=["4D"])
        res = r.throw_pair(A, 0, 1)
        self.assertFalse(res["match"])
        self.assertEqual(r.hands[A], cards("5H", "2C", "4D"))   # cards stay + penalty
        self.assertEqual(res["penalty_slot"], 2)
        self.assertEqual(r.phase, TURN)
        self.assertEqual(r.current_player, B)

    def test_deck_reshuffles_when_empty(self):
        r = make_round({A: ["5H"], B: ["6D"], C: ["7H"]}, deck=[], pile=["1C", "2C", "3C"])
        res = r.draw(A)
        self.assertIn(res["private"]["card"], ("1C", "2C"))
        self.assertEqual(r.pile, cards("3C"))                    # top card stays


class PowerTests(unittest.TestCase):
    def test_jack_thrown_after_draw(self):
        r = make_round({A: ["5H", "6H"], B: ["2D", "3D"], C: ["7H"]}, deck=["JC"])
        r.draw(A)
        r.throw_drawn(A)
        self.assertEqual((r.phase, r.pending_power), (POWER, "J"))
        with self.assertRaises(GameError):
            r.cut(B, 0)                                          # no cutting during the power
        r.use_jack(A, 0, B, 1)
        self.assertEqual(r.hands[A], cards("3D", "6H"))
        self.assertEqual(r.hands[B], cards("2D", "5H"))
        self.assertEqual((r.phase, r.cut_rank), (CUT_WINDOW, "J"))

    def test_jack_swapped_out_also_triggers(self):
        r = make_round({A: ["JH", "6H"], B: ["2D"], C: ["7H"]}, deck=["1C"])
        r.draw(A)
        r.swap_drawn(A, 0)
        self.assertEqual((r.phase, r.pending_power), (POWER, "J"))

    def test_queen_peek_any_card(self):
        r = make_round({A: ["5H"], B: ["2D", "3D"], C: ["7H"]}, deck=["QS"])
        r.draw(A)
        r.throw_drawn(A)
        res = r.use_queen(A, B, 1)
        self.assertEqual(res["private"]["card"], "3D")
        self.assertEqual(r.phase, CUT_WINDOW)

    def test_queen_can_look_at_own_card(self):
        r = make_round({A: ["5H"], B: ["2D"], C: ["7H"]}, deck=["QS"])
        r.draw(A)
        r.throw_drawn(A)
        self.assertEqual(r.use_queen(A, A, 0)["private"]["card"], "5H")

    def test_pair_of_queens_triggers_power_twice(self):
        r = make_round({A: ["QH", "QS", "1C"], B: ["2D"], C: ["7H"]})
        r.throw_pair(A, 0, 1)
        self.assertEqual((r.phase, r.pending_power, r.power_uses), (POWER, "Q", 2))
        self.assertEqual(r.use_queen(A, B, 0)["uses_left"], 1)
        self.assertEqual(r.phase, POWER)                       # second peek still to use
        self.assertEqual(r.use_queen(A, C, 0)["private"]["card"], "7H")
        self.assertEqual(r.phase, CUT_WINDOW)

    def test_pair_of_jacks_two_swaps(self):
        r = make_round({A: ["JH", "JS", "1C", "2C"], B: ["5D", "6D"], C: ["7H"]})
        r.throw_pair(A, 0, 1)
        r.use_jack(A, 2, B, 0)
        r.use_jack(A, 3, C, 0)
        self.assertEqual(r.hands[A], [None, None, Card("5", "D"), Card("7", "H")])
        self.assertEqual(r.hands[B], cards("1C", "6D"))
        self.assertEqual(r.hands[C], cards("2C"))
        self.assertEqual(r.phase, CUT_WINDOW)

    def test_skipping_a_pair_power_skips_both(self):
        r = make_round({A: ["JH", "JS", "1C"], B: ["5D"], C: ["7H"]})
        r.throw_pair(A, 0, 1)
        r.skip_power(A)
        self.assertEqual(r.phase, CUT_WINDOW)

    def test_skip_power(self):
        r = make_round({A: ["5H"], B: ["2D"], C: ["7H"]}, deck=["JS"])
        r.draw(A)
        r.throw_drawn(A)
        r.skip_power(A)
        self.assertEqual(r.phase, CUT_WINDOW)

    def test_king_has_no_power(self):
        r = make_round({A: ["5H"], B: ["2D"], C: ["7H"]}, deck=["KS"])
        r.draw(A)
        r.throw_drawn(A)
        self.assertEqual(r.phase, CUT_WINDOW)


class CutTests(unittest.TestCase):
    def setUp(self):
        self.r = make_round({A: ["5H", "6H"], B: ["5D", "3D"], C: ["5C", "7H"]}, deck=["2S", "5S"])
        self.r.draw(A)          # draws 5S
        self.r.throw_drawn(A)   # cut window for 5s

    def test_correct_cut(self):
        res = self.r.cut(B, 0)
        self.assertTrue(res["correct"])
        self.assertEqual(self.r.hands[B], [None, Card("3", "D")])
        self.assertEqual(self.r.pile[-1], Card("5", "D"))

    def test_wrong_cut_penalty(self):
        res = self.r.cut(B, 1)
        self.assertFalse(res["correct"])
        self.assertEqual(self.r.hands[B], cards("5D", "3D", "2S"))

    def test_several_players_cut_last_one_decides_next_turn(self):
        self.r.cut(C, 0)        # arrives first
        self.r.cut(B, 0)        # arrives last -> the player after B plays next
        self.assertEqual(self.r.close_cut_window()["next"], C)

    def test_wrong_cut_does_not_change_turn_order(self):
        self.r.cut(B, 1)
        self.assertEqual(self.r.close_cut_window()["next"], B)   # still the player after A

    def test_thrower_may_cut_too(self):
        self.assertTrue(self.r.cut(A, 0)["correct"])

    def test_cut_on_a_pair(self):
        r = make_round({A: ["4H", "4S", "1C"], B: ["4D"], C: ["7H"]})
        r.throw_pair(A, 0, 1)
        self.assertTrue(r.cut(B, 0)["correct"])

    def test_no_cut_after_window_closes(self):
        self.r.close_cut_window()
        with self.assertRaises(GameError):
            self.r.cut(B, 0)

    def test_cutting_last_card_wins_round(self):
        r = make_round({A: ["5H", "6H"], B: ["5D"], C: ["7H"]}, deck=["5S"])
        r.draw(A)
        r.throw_drawn(A)
        r.cut(B, 0)
        self.assertEqual(r.phase, ROUND_OVER)
        self.assertEqual(r.result["winner"], B)
        self.assertEqual(r.result["scores"], {A: 11, B: -5, C: 7})


class DisqualificationTests(unittest.TestCase):
    TWELVE = ["1H", "2H", "3H", "4H", "5H", "6H", "7H", "1D", "2D", "3D", "4D", "5D"]

    def test_twelve_cards_is_allowed(self):
        r = make_round({A: self.TWELVE[:11] + ["7C"], B: ["6D"], C: ["7D"]}, deck=["6C"])
        r.throw_pair(A, 0, 1)                                  # wrong pair -> 13th card
        self.assertIn(A, r.out)
        r = make_round({A: self.TWELVE[:11], B: ["6D"], C: ["7D"]}, deck=["6C"])
        r.throw_pair(A, 0, 1)                                  # 12 cards: still playing
        self.assertNotIn(A, r.out)

    def test_disqualified_player_is_skipped(self):
        r = make_round({A: self.TWELVE, B: ["6D"], C: ["7D"]}, deck=["6C"])
        res = r.throw_pair(A, 0, 1)
        self.assertTrue(res["disqualified"])
        self.assertEqual(r.current_player, B)
        self.assertEqual(r.hand_size(A), 0)
        self.assertEqual(len(r.deck), 13)                      # all 13 of A's cards went back to the deck
        r.draw(B)
        r.throw_drawn(B)
        r.close_cut_window()
        self.assertEqual(r.current_player, C)
        r.call_pablo(C)
        self.assertEqual(r.result["scores"][A], 50)            # disqualified: +50 this round
        with self.assertRaises(GameError):
            r.cut(A, 0)

    def test_wrong_cut_can_disqualify(self):
        r = make_round({A: ["5H", "6H"], B: self.TWELVE, C: ["7D"]}, deck=["1S", "5S"])
        r.draw(A)
        r.throw_drawn(A)
        res = r.cut(B, 1)                                      # 2H is not a 5
        self.assertTrue(res["disqualified"])
        self.assertEqual(r.close_cut_window()["next"], C)      # B is skipped

    def test_last_player_standing_wins(self):
        r = Round([A, B], rng=random.Random(0))
        r.hands = {A: cards(*self.TWELVE), B: cards("6D")}
        r.deck, r.peeked, r.phase = cards("6C"), {A, B}, TURN
        r.throw_pair(A, 0, 1)
        self.assertEqual(r.phase, ROUND_OVER)
        self.assertEqual(r.result["winner"], B)
        self.assertEqual(r.result["scores"], {A: 50, B: -5})

    def test_disqualified_only_for_that_round(self):
        m = Match([A, B, C], rng=random.Random(0))
        r = m.start_round()
        r.peeked, r.phase = {A, B, C}, TURN
        r.hands[A] = cards(*self.TWELVE)
        r.deck.append(Card("6", "C"))
        r.throw_pair(A, 0, 1)
        r.call_pablo(B)
        self.assertEqual(m.record_round()[A], 50)
        self.assertEqual(m.start_round().players, [A, B, C])   # A plays again


class EndOfRoundTests(unittest.TestCase):
    def test_empty_hand_by_pair_wins(self):
        r = make_round({A: ["3H", "3S"], B: ["2D", "JD"], C: ["KH"]})
        r.throw_pair(A, 0, 1)
        self.assertEqual(r.phase, ROUND_OVER)
        self.assertEqual(r.result["reason"], "empty_hand")
        self.assertEqual(r.result["scores"], {A: -5, B: 12, C: 0})

    def test_correct_pablo(self):
        r = make_round({A: ["1H", "KH"], B: ["2D", "3D"], C: ["7H"]})
        r.call_pablo(A)
        res = r.result
        self.assertTrue(res["pablo_correct"])
        self.assertEqual(res["scores"], {A: -5, B: 5, C: 7})
        self.assertEqual(res["hands"][B], ["2D", "3D"])          # everyone is revealed

    def test_wrong_pablo(self):
        r = make_round({A: ["4H", "2H"], B: ["1D", "2D"], C: ["7H"]})
        r.call_pablo(A)
        self.assertFalse(r.result["pablo_correct"])
        self.assertEqual(r.result["winner"], B)
        self.assertEqual(r.result["scores"], {A: 6 + 50, B: -5, C: 7})

    def test_pablo_tie_nothing_happens(self):
        r = make_round({A: ["1H", "2H"], B: ["3D"], C: ["7H"]})
        r.call_pablo(A)
        self.assertIsNone(r.result["winner"])
        self.assertEqual(r.result["scores"], {A: 3, B: 3, C: 7})

    def test_pablo_only_on_own_turn_before_drawing(self):
        r = make_round({A: ["1H"], B: ["3D"], C: ["7H"]}, deck=["2C"])
        with self.assertRaises(GameError):
            r.call_pablo(B)
        r.draw(A)
        with self.assertRaises(GameError):
            r.call_pablo(A)

    def test_no_actions_after_round_over(self):
        r = make_round({A: ["1H"], B: ["3D"], C: ["7H"]})
        r.call_pablo(A)
        with self.assertRaises(GameError):
            r.draw(B)


class MatchTests(unittest.TestCase):
    def test_scores_accumulate_and_match_ends_at_100(self):
        m = Match([A, B, C], rng=random.Random(3))
        m.start_round()
        m.round.result = {"scores": {A: 60, B: -5, C: 20}}
        m.round.phase = ROUND_OVER
        m.record_round()
        self.assertFalse(m.is_over)
        m.start_round()
        m.round.result = {"scores": {A: 45, B: 8, C: -5}}
        m.round.phase = ROUND_OVER
        self.assertEqual(m.record_round(), {A: 105, B: 3, C: 15})
        self.assertTrue(m.is_over)
        self.assertEqual(m.winners(), [B])
        with self.assertRaises(GameError):
            m.start_round()

    def test_starting_player_rotates(self):
        m = Match([A, B, C])
        self.assertEqual(m.start_round().current_player, A)
        m.round.phase = ROUND_OVER
        m.round.result = {"scores": {A: 0, B: 0, C: 0}}
        m.record_round()
        self.assertEqual(m.start_round().current_player, B)


class RandomPlayTests(unittest.TestCase):
    """Bots play hundreds of random games: the engine must never break its own invariants."""

    def test_random_matches(self):
        for seed in range(150):
            rng = random.Random(seed)
            players = [A, B, C] if seed % 2 else [A, B, C, "dave"]
            m = Match(players, rng=random.Random(seed))
            while not m.is_over:
                r = m.start_round()
                for p in r.players:
                    r.peek(p, rng.sample(range(4), 2))
                for _ in range(500):
                    if r.phase == ROUND_OVER:
                        break
                    self._random_action(r, rng)
                    total = len(r.deck) + len(r.pile) + sum(r.hand_size(p) for p in r.players) + (r.drawn is not None)
                    self.assertEqual(total, 40, f"cards lost (seed {seed})")
                while r.phase != ROUND_OVER:   # long round: make the next player call Pablo
                    self._random_action(r, rng, pablo=True)
                m.record_round()
            self.assertTrue(m.winners())

    def _random_action(self, r, rng, pablo=False):
        p = r.current_player
        slots = [i for i, c in enumerate(r.hands[p]) if c is not None]
        if r.phase == TURN:
            choice = "pablo" if pablo else rng.choice(["draw"] * 6 + ["pair"] * 2 + ["pablo"])
            if choice == "pair" and len(slots) >= 2:
                r.throw_pair(p, *rng.sample(slots, 2))
            elif choice == "pablo":
                r.call_pablo(p)
            elif r.can_draw():
                r.draw(p)
            else:
                r.call_pablo(p)                    # nothing left to draw
        elif r.phase == DRAWN:
            if rng.random() < 0.5:
                r.throw_drawn(p)
            else:
                r.swap_drawn(p, rng.choice(slots))
        elif r.phase == POWER:
            others = [q for q in r.active if q != p and r.hand_size(q)]
            if r.pending_power == "J" and others:
                q = rng.choice(others)
                theirs = [i for i, c in enumerate(r.hands[q]) if c is not None]
                r.use_jack(p, rng.choice(slots), q, rng.choice(theirs))
            else:
                r.skip_power(p)
        elif r.phase == CUT_WINDOW:
            for q in rng.sample(r.active, len(r.active)):
                own = [i for i, c in enumerate(r.hands[q]) if c is not None]
                if own and rng.random() < 0.4 and r.phase == CUT_WINDOW:
                    r.cut(q, rng.choice(own))
            if r.phase == CUT_WINDOW:
                r.close_cut_window()


if __name__ == "__main__":
    unittest.main()
