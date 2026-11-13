"""One running game: connects a room's players to the rules engine.

Every method is called while the server lock is held, and returns an
"outbox": a list of (username, message) for the server to queue. So the
engine is never touched by two threads at once, and every player receives the
messages in the order the moves really happened.

The only clock in the game lives here: when the engine opens a cut window,
a timer closes it CUT_WINDOW_SECONDS later (from the timer's own thread,
which takes the server lock like everyone else).
"""
import logging
import threading
import time
from datetime import datetime

from common import config
from common import protocol as proto
from server.engine import CUT_WINDOW, ROUND_OVER, GameError, Match

log = logging.getLogger("pablo")

# protocol message type -> how to call the engine
ACTIONS = {
    "peek":        lambda r, p, m: r.peek(p, m["slots"]),
    "draw":        lambda r, p, m: r.draw(p),
    "throw_drawn": lambda r, p, m: r.throw_drawn(p),
    "swap":        lambda r, p, m: r.swap_drawn(p, m["slot"]),
    "throw_pair":  lambda r, p, m: r.throw_pair(p, *m["slots"]),
    "jack":        lambda r, p, m: r.use_jack(p, m["slot"], m["target"], m["target_slot"]),
    "queen":       lambda r, p, m: r.use_queen(p, m["target"], m["target_slot"]),
    "skip_power":  lambda r, p, m: r.skip_power(p),
    "cut":         lambda r, p, m: r.cut(p, m["slot"]),
    "pablo":       lambda r, p, m: r.call_pablo(p),
}


class GameRoom:
    def __init__(self, room_id, players, lock, deliver, rng=None, on_finished=None):
        """`lock` is the server lock; `deliver(outbox)` queues messages (called with the lock held).

        `on_finished()` is called (lock held) if the match ends from the timer thread.
        """
        self.on_finished = on_finished
        self.room_id = room_id
        self.match = Match(players, rng=rng)
        self.lock = lock
        self.deliver = deliver
        self.cut_timer = None
        self.cut_deadline = None      # time.monotonic() when the open cut window closes
        self.finished = False         # True once someone reached the target score

    @property
    def round(self):
        return self.match.round

    @property
    def players(self):
        return self.match.players

    # ------------------------------------------------------------------ messages
    def _state_for(self, player):
        state = self.round.state_for(player)
        state["round"] = self.match.round_number
        state["scores"] = dict(self.match.scores)
        state["cut_remaining"] = (max(0.0, self.cut_deadline - time.monotonic())
                                  if self.cut_deadline else None)
        return proto.make("state", state=state)

    def states(self):
        return [(p, self._state_for(p)) for p in self.players]

    def events(self, result, actor):
        """Everyone gets the public part of `result`; only `actor` also gets the private part."""
        public = {k: v for k, v in result.items() if k != "private"}
        out = []
        for p in self.players:
            event = result if p == actor and "private" in result else public
            out.append((p, proto.make("event", event=event)))
        return out

    # ------------------------------------------------------------------ flow
    def start(self):
        self.match.start_round()
        start = proto.make("game_start", players=self.players, scores=dict(self.match.scores),
                           round=self.match.round_number)
        return [(p, start) for p in self.players] + self.states()

    def act(self, player, msg):
        """Applies one player action. Illegal moves get an error and change nothing."""
        try:
            result = ACTIONS[msg["type"]](self.round, player, msg)
        except GameError as exc:
            return [(player, proto.error(msg["type"], str(exc)))]
        if msg["type"] == "cut":
            # Cuts are applied in the order their messages reach the server. The
            # millisecond timestamp in the log shows who really was first.
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            left = self.cut_deadline - time.monotonic() if self.cut_deadline else 0
            log.info("CUT %s  room %d  %-10s %s %s (%.2f s left in the window)", stamp, self.room_id,
                     player, result["card"], "correct" if result["correct"] else "WRONG -> penalty", left)
        if msg["type"] == "pablo":
            log.info("room %d: %s called PABLO", self.room_id, player)
        outbox = self.events(result, player)
        self._check_cut_window()
        return outbox + self.states() + self._check_round_over()

    def _check_round_over(self):
        """When the engine has ended the round: record the scores and announce the result."""
        if self.round.phase != ROUND_OVER or len(self.match.history) == self.match.round_number:
            return []
        self.stop()                                   # no cut window can still be running
        self.match.record_round()
        result = self.round.result
        log.info("room %d: round %d over (%s), winner %s, round scores %s, totals %s",
                 self.room_id, self.match.round_number, result["reason"], result["winner"],
                 result["scores"], self.match.scores)
        msg = proto.make("round_over", round=self.match.round_number, result=result,
                         scores=dict(self.match.scores), match_over=self.match.is_over)
        outbox = [(p, msg) for p in self.players]
        if self.match.is_over:
            self.finished = True
            winners = self.match.winners()
            log.info("room %d: MATCH OVER, winner(s) %s", self.room_id, ", ".join(winners))
            over = proto.make("match_over", scores=dict(self.match.scores), winners=winners,
                              rounds=self.match.round_number)
            outbox += [(p, over) for p in self.players]
        return outbox

    def next_round(self):
        """Deals the next round (the server checks that the host asked and the round is over)."""
        if self.finished:
            raise GameError("the match is over")
        if self.round.phase != ROUND_OVER:
            raise GameError("the round is not finished")
        return self.start()

    def _check_cut_window(self):
        """Starts the 3-second timer when the engine has just opened a cut window."""
        if self.round.phase == CUT_WINDOW and self.cut_timer is None:
            self.cut_deadline = time.monotonic() + config.CUT_WINDOW_SECONDS
            self.cut_timer = threading.Timer(config.CUT_WINDOW_SECONDS, self._close_cut_window)
            self.cut_timer.daemon = True
            self.cut_timer.start()

    def _close_cut_window(self):
        """Runs in the timer thread when the 3 seconds are up."""
        with self.lock:
            self.cut_timer = self.cut_deadline = None
            if self.round.phase != CUT_WINDOW:
                return                     # the round ended meanwhile
            result = self.round.close_cut_window()
            self.deliver(self.events(result, None) + self.states() + self._check_round_over())
            if self.finished and self.on_finished:
                self.on_finished()

    def stop(self):
        if self.cut_timer:
            self.cut_timer.cancel()
            self.cut_timer = None
