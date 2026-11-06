"""Pablo rules engine — pure game logic, no networking and no clocks.

The server owns one `Match` per room. Every player action is a method call that
either changes the state and returns a result dict, or raises `GameError` and
changes nothing. Timing (the 3-second cut window, turn timeouts) is handled by
the server, which calls `Round.close_cut_window()` when the window expires.

Hands are lists of *slots*. A slot holds a Card, or None once that card has
left the hand. Slots never shift, so "my card #2" stays card #2 for the whole
round, which matters in a memory game. Penalty cards are added as new slots.
"""
import random

from common import config
from common.cards import Card, new_deck

# Round phases
PEEK = "peek"              # everyone chooses the 2 cards they look at
TURN = "turn"              # current player chooses: draw, throw a pair or call Pablo
DRAWN = "drawn"            # current player holds a drawn card: throw it or swap it
POWER = "power"            # current player must use (or skip) a Jack / Queen power
CUT_WINDOW = "cut_window"  # anyone may cut; the server closes it after 3 seconds
ROUND_OVER = "round_over"


class GameError(Exception):
    """An illegal action. The state is left unchanged."""


class Round:
    def __init__(self, players, starter=0, rng=None, deck=None):
        if len(players) < 2:
            raise ValueError("Pablo needs at least 2 players")
        self.players = list(players)
        self.rng = rng or random.Random()
        self.deck = list(deck) if deck is not None else new_deck(self.rng)
        self.pile = []                       # middle pile, top = last element
        self.hands = {p: [] for p in self.players}
        for _ in range(config.CARDS_PER_PLAYER):
            for p in self.players:
                self.hands[p].append(self.deck.pop())

        self.phase = PEEK
        self.peeked = set()
        self.current = starter % len(self.players)  # seat index of the player to act
        self.drawn = None                    # card the current player is holding
        self.pending_power = None            # 'J' or 'Q'
        self.power_uses = 0                  # 1 for a single card, 2 for a pair
        self.out = []                        # players disqualified this round (more than 12 cards)
        self.cut_rank = None                 # rank that may be cut in the open window
        self.last_thrower = None             # whoever threw the last card that stayed in the pile
        self.result = None                   # filled when the round ends

    # ------------------------------------------------------------------ helpers
    @property
    def current_player(self):
        return self.players[self.current]

    @property
    def active(self):
        """Players still in the round (not disqualified), in seat order."""
        return [p for p in self.players if p not in self.out]

    def _require(self, pid, *phases, own_turn=True):
        if pid not in self.hands:
            raise GameError("unknown player")
        if pid in self.out:
            raise GameError("you are disqualified")
        if self.phase not in phases:
            raise GameError(f"not allowed during phase '{self.phase}'")
        if own_turn and pid != self.current_player:
            raise GameError("not your turn")

    def _card_at(self, pid, slot):
        if pid not in self.hands:
            raise GameError("unknown player")
        hand = self.hands[pid]
        if not isinstance(slot, int) or not 0 <= slot < len(hand) or hand[slot] is None:
            raise GameError(f"no card in slot {slot}")
        return hand[slot]

    def _take_from_deck(self):
        """Top card of the deck; reshuffles the pile (minus its top card) if the deck is empty."""
        if not self.deck and len(self.pile) > 1:
            top = self.pile.pop()
            self.deck = self.pile
            self.rng.shuffle(self.deck)
            self.pile = [top]
        return self.deck.pop() if self.deck else None

    def _give_penalty(self, pid):
        """Adds one unseen card as a new slot. Returns the slot index (None if no card was left)."""
        card = self._take_from_deck()
        if card is None:
            return None
        self.hands[pid].append(card)
        slot = len(self.hands[pid]) - 1
        if self.hand_size(pid) > config.MAX_HAND_SIZE:
            self._disqualify(pid)
        return slot

    def _disqualify(self, pid):
        """More than 12 cards: the player is out for this round and their cards are shuffled into the deck."""
        self.deck += [c for c in self.hands[pid] if c is not None]
        self.rng.shuffle(self.deck)
        self.hands[pid] = [None] * len(self.hands[pid])
        self.out.append(pid)
        if len(self.active) < 2:
            self._end_round("disqualification", winner=self.active[0])

    def can_draw(self):
        """False only if every card is in someone's hand (deck empty, pile has <= 1 card)."""
        return bool(self.deck) or len(self.pile) > 1

    def hand_size(self, pid):
        return sum(1 for c in self.hands[pid] if c is not None)

    def hand_total(self, pid):
        return sum(c.points for c in self.hands[pid] if c is not None)

    def _discarded(self, pid, rank, power, uses=1):
        """Cards of `rank` just went into the pile on pid's turn. Decide what comes next."""
        self.last_thrower = pid
        if self.hand_size(pid) == 0:
            self._end_round("empty_hand", winner=pid)
        elif power:
            self.phase, self.pending_power, self.cut_rank = POWER, power, rank
            self.power_uses = uses
        else:
            self._open_cut_window(rank)

    def _open_cut_window(self, rank):
        self.phase, self.cut_rank, self.pending_power, self.power_uses = CUT_WINDOW, rank, None, 0

    def _next_turn(self, after_pid):
        """The next non-disqualified player after `after_pid` (who may be disqualified)."""
        seat = self.players.index(after_pid)
        while True:
            seat = (seat + 1) % len(self.players)
            if self.players[seat] not in self.out:
                break
        self.current = seat
        self.phase, self.drawn, self.pending_power, self.cut_rank = TURN, None, None, None

    # ------------------------------------------------------------------ peek
    def peek(self, pid, slots):
        """Look at 2 of your own cards at the start of the round (private result)."""
        self._require(pid, PEEK, own_turn=False)
        if pid in self.peeked:
            raise GameError("you already peeked")
        if len(set(slots)) != config.INITIAL_PEEK:
            raise GameError(f"choose exactly {config.INITIAL_PEEK} different cards")
        cards = {s: self._card_at(pid, s).code() for s in slots}
        self.peeked.add(pid)
        if self.peeked == set(self.players):
            self.phase = TURN
        return {"action": "peek", "player": pid, "private": {"cards": cards}}

    # ------------------------------------------------------------------ turn actions
    def call_pablo(self, pid):
        self._require(pid, TURN)
        self._end_round("pablo", caller=pid)
        return {"action": "pablo", "player": pid}

    def throw_pair(self, pid, slot_a, slot_b):
        self._require(pid, TURN)
        if slot_a == slot_b:
            raise GameError("choose two different cards")
        a, b = self._card_at(pid, slot_a), self._card_at(pid, slot_b)
        result = {"action": "pair", "player": pid, "slots": [slot_a, slot_b],
                  "cards": [a.code(), b.code()], "match": a.matches(b)}
        if a.matches(b):
            self.hands[pid][slot_a] = self.hands[pid][slot_b] = None
            self.pile += [a, b]
            self._discarded(pid, a.rank, a.power, uses=2)   # a pair uses the power twice
        else:
            # Both cards stay where they were (everyone saw them) + one penalty card.
            result["penalty_slot"] = self._give_penalty(pid)
            result["disqualified"] = pid in self.out
            if self.phase != ROUND_OVER:
                self._next_turn(pid)
        return result

    def draw(self, pid):
        self._require(pid, TURN)
        card = self._take_from_deck()
        if card is None:
            raise GameError("no cards left to draw")
        self.drawn = card
        self.phase = DRAWN
        return {"action": "draw", "player": pid, "private": {"card": card.code()}}

    def throw_drawn(self, pid):
        self._require(pid, DRAWN)
        card, self.drawn = self.drawn, None
        self.pile.append(card)
        self._discarded(pid, card.rank, card.power)
        return {"action": "throw_drawn", "player": pid, "card": card.code()}

    def swap_drawn(self, pid, slot):
        self._require(pid, DRAWN)
        old = self._card_at(pid, slot)
        self.hands[pid][slot], self.drawn = self.drawn, None
        self.pile.append(old)
        self._discarded(pid, old.rank, old.power)
        return {"action": "swap", "player": pid, "slot": slot, "card": old.code()}

    # ------------------------------------------------------------------ powers
    def use_jack(self, pid, my_slot, target, target_slot):
        """Blind swap of one of your cards with another player's card."""
        self._require(pid, POWER)
        if self.pending_power != "J":
            raise GameError("no Jack power to use")
        if target == pid:
            raise GameError("swap with another player")
        self._card_at(pid, my_slot), self._card_at(target, target_slot)
        mine, theirs = self.hands[pid], self.hands[target]
        mine[my_slot], theirs[target_slot] = theirs[target_slot], mine[my_slot]
        self._power_used()
        return {"action": "jack", "player": pid, "slot": my_slot,
                "target": target, "target_slot": target_slot, "uses_left": self.power_uses}

    def use_queen(self, pid, target, target_slot):
        """Look at any one card on the table (private result)."""
        self._require(pid, POWER)
        if self.pending_power != "Q":
            raise GameError("no Queen power to use")
        card = self._card_at(target, target_slot)
        self._power_used()
        return {"action": "queen", "player": pid, "target": target, "target_slot": target_slot,
                "uses_left": self.power_uses, "private": {"card": card.code()}}

    def _power_used(self):
        self.power_uses -= 1
        if self.power_uses == 0:
            self._open_cut_window(self.cut_rank)

    def skip_power(self, pid):
        """Gives up the power (both uses, for a pair)."""
        self._require(pid, POWER)
        self._open_cut_window(self.cut_rank)
        return {"action": "skip_power", "player": pid}

    # ------------------------------------------------------------------ cutting
    def cut(self, pid, slot):
        """Out-of-turn throw of one of your own cards matching the cut rank.

        The server calls this in the order the cut requests arrive, so the
        network decides who was first.
        """
        self._require(pid, CUT_WINDOW, own_turn=False)
        card = self._card_at(pid, slot)
        result = {"action": "cut", "player": pid, "slot": slot, "card": card.code()}
        if card.rank == self.cut_rank:
            self.hands[pid][slot] = None
            self.pile.append(card)
            self.last_thrower = pid
            result["correct"] = True
            if self.hand_size(pid) == 0:
                self._end_round("empty_hand", winner=pid)
        else:
            result["correct"] = False
            result["penalty_slot"] = self._give_penalty(pid)
            result["disqualified"] = pid in self.out
        return result

    def close_cut_window(self):
        """Called by the server when the 3 seconds are up."""
        if self.phase != CUT_WINDOW:
            raise GameError("no cut window is open")
        self._next_turn(self.last_thrower)
        return {"action": "cut_window_closed", "next": self.current_player}

    # ------------------------------------------------------------------ end of round
    def _end_round(self, reason, winner=None, caller=None):
        totals = {p: self.hand_total(p) for p in self.active}
        scores = dict(totals)
        pablo_correct = None
        if reason == "pablo":
            lowest = min(totals.values())
            at_lowest = [p for p in self.active if totals[p] == lowest]
            if len(at_lowest) == 1:                  # a tie means nothing happens
                winner = at_lowest[0]
                pablo_correct = winner == caller
                if not pablo_correct:
                    scores[caller] += config.WRONG_PABLO_PENALTY
        if winner is not None:
            scores[winner] = config.ROUND_WINNER_SCORE
        for p in self.out:
            scores[p] = config.DISQUALIFICATION_PENALTY
        self.phase = ROUND_OVER
        self.drawn = self.pending_power = self.cut_rank = None
        self.power_uses = 0
        self.result = {
            "reason": reason, "winner": winner, "caller": caller,
            "pablo_correct": pablo_correct, "totals": totals, "scores": scores,
            "hands": {p: [c.code() if c else None for c in self.hands[p]] for p in self.active},
            "disqualified": list(self.out),
        }

    # ------------------------------------------------------------------ views
    def public_state(self):
        """What every player may know. Card faces are hidden until the round ends."""
        return {
            "phase": self.phase,
            "players": self.players,
            "current": self.current_player,
            "slots": {p: [c is not None for c in self.hands[p]] for p in self.players},
            "deck_count": len(self.deck),
            "can_draw": self.can_draw(),
            "pile_top": self.pile[-1].code() if self.pile else None,
            "pile_count": len(self.pile),
            "holding_drawn": self.drawn is not None,
            "pending_power": self.pending_power,
            "power_uses": self.power_uses,
            "disqualified": list(self.out),
            "cut_rank": self.cut_rank,
            "waiting_peek": [p for p in self.players if p not in self.peeked],
            "result": self.result,
        }

    def state_for(self, pid):
        """Public state plus the drawn card if this player is holding it."""
        state = self.public_state()
        state["you"] = pid
        state["drawn"] = self.drawn.code() if self.drawn and pid == self.current_player else None
        return state


class Match:
    """A series of rounds until someone reaches the target score (100).

    A player disqualified in one round plays again in the next one.
    """

    def __init__(self, players, rng=None):
        self.players = list(players)
        self.rng = rng or random.Random()
        self.scores = {p: 0 for p in self.players}
        self.round_number = 0
        self.round = None
        self.history = []

    def start_round(self, deck=None):
        if self.is_over:
            raise GameError("the match is over")
        if self.round and self.round.phase != ROUND_OVER:
            raise GameError("the current round is not finished")
        # The first player rotates every round.
        self.round = Round(self.players, starter=self.round_number, rng=self.rng, deck=deck)
        self.round_number += 1
        return self.round

    def record_round(self):
        """Adds the finished round's scores to the totals. Call once per round."""
        if not self.round or self.round.phase != ROUND_OVER:
            raise GameError("the round is not finished")
        if len(self.history) == self.round_number:
            raise GameError("round already recorded")
        for p, s in self.round.result["scores"].items():
            self.scores[p] += s
        self.history.append(self.round.result)
        return dict(self.scores)

    @property
    def is_over(self):
        return any(s >= config.TARGET_SCORE for s in self.scores.values())

    def winners(self):
        """Lowest total score wins (a list, in case of a tie)."""
        lowest = min(self.scores.values())
        return [p for p in self.players if self.scores[p] == lowest]
