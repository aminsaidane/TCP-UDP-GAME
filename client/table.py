"""The game table screen.

Everything shown comes from the last `state` the server sent (what this player
is allowed to see) plus `event` messages for the log and for the cards this
player was shown privately (peek, Queen), which stay face-up for a few
seconds only: it is a memory game.
"""
import time

import pygame

from common import config
from common import protocol as proto
from client import ui

WIDTH, HEIGHT = 1000, 680
PEEK_SECONDS = 5.0       # how long the 2 starting cards stay visible
QUEEN_SECONDS = 3.0      # how long a card seen with a Queen stays visible
MY_CARD = (84, 118)
OPP_CARD = (46, 64)
POWER_NAMES = {"J": "Jack", "Q": "Queen"}
LOG_FONT, LOG_WIDTH, LOG_ROWS = 20, 266, 7


class TableScreen:
    def __init__(self, app):
        self.app = app
        self.state = None
        self.state_time = 0.0           # when the last state arrived (for the cut countdown)
        self.log = []
        self.reveals = {}               # (owner, slot) -> (card code, visible until)
        self.selection = []             # cards clicked so far for a 2-card action
        self.pair_mode = False
        self.card_rects = []            # (rect, owner, slot), rebuilt every frame
        self.pile_flash = (0.0, None)   # (until, colour): the pile glows after each cut
        self.message = ""
        self.message_ok = False
        y = 600
        self.draw_btn = ui.Button((195, y, 190, 50), "Draw", lambda: self._send("draw"))
        self.pair_btn = ui.Button((405, y, 190, 50), "Throw a pair", self._toggle_pair, kind="secondary")
        self.pablo_btn = ui.Button((615, y, 190, 50), "Pablo!", self._pablo, kind="secondary")
        self.pablo_armed_until = 0.0    # a wrong Pablo costs +50: it takes 2 clicks
        self.round_result = None        # the last round_over message, while the results are shown
        self.final = None               # the match_over message
        self.next_btn = ui.Button((WIDTH // 2 - 120, 560, 240, 54), "Next round",
                                  lambda: self.app.send(proto.make("next_round")))
        self.back_btn = ui.Button((WIDTH // 2 - 120, 560, 240, 54), "Back to the room", self._back_to_room)
        self.throw_btn = ui.Button((300, y, 190, 50), "Throw it", lambda: self._send("throw_drawn"))
        self.skip_btn = ui.Button((510, y, 190, 50), "Skip power", lambda: self._send("skip_power"),
                                  kind="secondary")
        self.buttons = [self.draw_btn, self.pair_btn, self.pablo_btn, self.throw_btn, self.skip_btn,
                        self.next_btn, self.back_btn]

    # ------------------------------------------------------------------ helpers
    @property
    def me(self):
        return self.app.username

    @property
    def my_turn(self):
        return self.state and self.state["current"] == self.me

    def show(self, message, ok=False):
        self.message, self.message_ok = message, ok

    def enter(self):
        self.show("")

    def _send(self, msg_type, **fields):
        self.show("")
        self.app.send(proto.make(msg_type, **fields))

    def _pablo(self):
        now = time.monotonic()
        if now < self.pablo_armed_until:
            self.pablo_armed_until = 0.0
            self._send("pablo")
        else:
            self.pablo_armed_until = now + 3.0
            self.show("Sure? A wrong Pablo costs +50. Click again to confirm.")

    def _back_to_room(self):
        self.state = self.round_result = self.final = None
        self.app.go("room")

    def _toggle_pair(self):
        self.pair_mode = not self.pair_mode
        self.selection = []

    def _say(self, line):
        """Adds a line to the game log, wrapped to the log panel's width."""
        words, row = line.split(), ""
        for word in words:
            candidate = f"{row} {word}".strip()
            if ui.font(LOG_FONT).size(candidate)[0] > LOG_WIDTH and row:
                self.log.append(row)
                row = "   " + word
            else:
                row = candidate
        self.log.append(row)
        self.log = self.log[-LOG_ROWS:]

    # ------------------------------------------------------------------ server messages
    def on_game_start(self, msg):
        self.log, self.reveals, self.selection = [], {}, []
        self.round_result = self.final = None
        self.state = None
        self.pair_mode = False
        self._say(f"Round {msg['round']} starts: pick 2 cards to peek at")

    def on_round_over(self, msg):
        self.round_result = msg
        self.show("")

    def on_match_over(self, msg):
        self.final = msg

    def on_state(self, state):
        if self.state and state["phase"] != self.state["phase"]:
            self.selection, self.pair_mode = [], False
        self.state = state
        self.state_time = time.monotonic()

    def on_event(self, ev):
        who = "You" if ev.get("player") == self.me else ev.get("player")
        private = ev.get("private", {})
        action = ev["action"]
        now = time.monotonic()
        if action == "peek":
            self._say(f"{who} peeked at 2 cards")
            for slot, code in private.get("cards", {}).items():
                self.reveals[(self.me, int(slot))] = (code, now + PEEK_SECONDS)
        elif action == "draw":
            self._say(f"{who} drew a card" + (f": {ui.card_name(private['card'])}" if private else ""))
        elif action == "throw_drawn":
            self._say(f"{who} threw the {ui.card_name(ev['card'])}")
        elif action == "swap":
            self._say(f"{who} swapped card {ev['slot'] + 1}, out goes the {ui.card_name(ev['card'])}")
            self.reveals.pop((ev["player"], ev["slot"]), None)
        elif action == "pair":
            names = " + ".join(ui.card_name(c) for c in ev["cards"])
            if ev["match"]:
                self._say(f"{who} threw a pair: {names}")
            else:
                self._say(f"{who} threw a WRONG pair ({names}): penalty card")
                if ev.get("disqualified"):
                    self._say(f"{who} has more than 12 cards: disqualified this round")
        elif action == "jack":
            target = "your" if ev["target"] == self.me else f"{ev['target']}'s"
            self._say(f"{who} swapped card {ev['slot'] + 1} with {target} card {ev['target_slot'] + 1}")
            self.reveals.pop((ev["player"], ev["slot"]), None)
            self.reveals.pop((ev["target"], ev["target_slot"]), None)
        elif action == "queen":
            target = "your" if ev["target"] == self.me else f"{ev['target']}'s"
            self._say(f"{who} looked at {target} card {ev['target_slot'] + 1}")
            if private:
                self.reveals[(ev["target"], ev["target_slot"])] = (private["card"], now + QUEEN_SECONDS)
        elif action == "pablo":
            self._say(f"{who} called PABLO!")
        elif action == "skip_power":
            self._say(f"{who} skipped the power")
        elif action == "cut":
            card = ui.card_name(ev["card"])
            if ev["correct"]:
                self._say(f"{who} CUT with the {card}!")
                self.pile_flash = (now + 0.6, ui.GREEN_OK)
                self.reveals.pop((ev["player"], ev["slot"]), None)
            else:
                self._say(f"{who} cut with the {card}: WRONG, penalty card")
                self.pile_flash = (now + 0.6, ui.RED)
                if ev.get("disqualified"):
                    self._say(f"{who} has more than 12 cards: disqualified this round")
        elif action == "cut_window_closed":
            self._say("Your turn!" if ev["next"] == self.me else f"{ev['next']}'s turn")

    # ------------------------------------------------------------------ input
    def handle_event(self, event):
        for b in self.buttons:
            if b.handle(event):
                return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.state:
            for rect, owner, slot in self.card_rects:
                if rect.collidepoint(event.pos):
                    self.click_card(owner, slot)
                    return

    def click_card(self, owner, slot):
        s = self.state
        phase, mine = s["phase"], owner == self.me
        if phase == "peek":
            if mine and self.me in s["waiting_peek"]:
                self._select((owner, slot))
                if len(self.selection) == 2:
                    self._send("peek", slots=[x[1] for x in self.selection])
                    self.selection = []
        elif phase == "cut_window":
            if mine:                  # anyone can cut, whoever's turn it is
                self._send("cut", slot=slot)
        elif not self.my_turn:
            return
        elif phase == "drawn" and mine:
            self._send("swap", slot=slot)
        elif phase == "turn" and self.pair_mode and mine:
            self._select((owner, slot))
            if len(self.selection) == 2:
                self._send("throw_pair", slots=[x[1] for x in self.selection])
                self.selection, self.pair_mode = [], False
        elif phase == "power" and s["pending_power"] == "Q":
            self._send("queen", target=owner, target_slot=slot)
        elif phase == "power" and s["pending_power"] == "J":
            # one of mine + one of an opponent's, in any order
            self.selection = [x for x in self.selection if (x[0] == self.me) != mine]
            self.selection.append((owner, slot))
            if len(self.selection) == 2:
                my_pick = next(x for x in self.selection if x[0] == self.me)
                their_pick = next(x for x in self.selection if x[0] != self.me)
                self._send("jack", slot=my_pick[1], target=their_pick[0], target_slot=their_pick[1])
                self.selection = []

    def _select(self, card):
        if card in self.selection:
            self.selection.remove(card)
        else:
            self.selection.append(card)

    # ------------------------------------------------------------------ drawing
    def status(self):
        s = self.state
        cur, phase = s["current"], s["phase"]
        if phase == "peek":
            if self.me in s["waiting_peek"]:
                return "Click 2 of your cards to look at them, and memorise them!"
            return "Waiting for " + ", ".join(s["waiting_peek"]) + " to peek..."
        if phase == "round_over":
            return "Round over"
        if phase == "cut_window":
            if self.me in s.get("disqualified", []):
                return "Cut window..."
            return f"CUT? Click one of your cards if it is a {s['cut_rank']}  ({self.cut_left():.1f} s)"
        if not self.my_turn:
            what = {"turn": "is playing", "drawn": "is deciding what to do with the drawn card",
                    "power": f"is using a {POWER_NAMES.get(s['pending_power'], '')}"}
            return f"{cur} {what.get(phase, '')}..."
        if phase == "turn":
            if self.pair_mode:
                return "Click the 2 cards you think are a pair"
            return "Your turn: draw a card, or throw a pair" if s["can_draw"] else "Nothing to draw: throw a pair"
        if phase == "drawn":
            return "Throw the drawn card, or click one of your cards to swap it"
        if phase == "power":
            uses = f" ({s['power_uses']} uses left)" if s["power_uses"] > 1 else ""
            if s["pending_power"] == "J":
                return "Jack: click one of your cards and one opponent card to swap them" + uses
            return "Queen: click any card to look at it" + uses
        return ""

    def cut_left(self):
        """Seconds left in the cut window, counted down locally from the last state."""
        s = self.state
        return max(0.0, (s["cut_remaining"] or 0) - (time.monotonic() - self.state_time))

    def _card_shown(self, owner, slot):
        hit = self.reveals.get((owner, slot))
        if hit and hit[1] > time.monotonic():
            return hit[0]
        return None

    def _hand(self, surface, owner, slots, x, y, max_w, size, glow_ok):
        """Draws one hand of face-down cards; returns nothing, records clickable rects."""
        w, h = size
        step = min(w + 10, (max_w - w) / max(1, len(slots) - 1)) if len(slots) > 1 else 0
        total = w + step * (len(slots) - 1)
        x0 = x + (max_w - total) / 2
        for i, present in enumerate(slots):
            rect = pygame.Rect(int(x0 + i * step), y, w, h)
            if not present:
                ui.empty_slot(surface, rect)
                continue
            selected = (owner, i) in self.selection
            hover = glow_ok and rect.collidepoint(ui.mouse_pos())
            ui.draw_card(surface, rect, self._card_shown(owner, i), selected=selected,
                         glow=ui.WHITE if hover else None)
            self.card_rects.append((rect, owner, i))

    def _clickable(self, owner):
        """Whether clicking this player's cards does something right now."""
        s = self.state
        mine = owner == self.me
        if s["phase"] == "peek":
            return mine and self.me in s["waiting_peek"]
        if s["phase"] == "cut_window":
            return mine and self.me not in s.get("disqualified", [])
        if not self.my_turn:
            return False
        return ((s["phase"] in ("drawn",) and mine) or (s["phase"] == "turn" and self.pair_mode and mine)
                or s["phase"] == "power")

    def draw(self, surface):
        ui.felt_background(surface)
        for b in self.buttons:
            b.visible = False
        if self.round_result:
            self.draw_results(surface)
            return
        if not self.state:
            ui.text(surface, "Dealing...", (WIDTH // 2, HEIGHT // 2), size=48, color=ui.PAPER, center=True)
            return
        s = self.state
        self.card_rects = []
        out = s.get("disqualified", [])

        # ---- opponents, in seat order starting after me
        players = s["players"]
        me_i = players.index(self.me)
        opponents = players[me_i + 1:] + players[:me_i]
        gap = 16
        pw = (WIDTH - 40 - gap * (len(opponents) - 1)) // len(opponents)
        for i, name in enumerate(opponents):
            rect = pygame.Rect(20 + i * (pw + gap), 20, pw, 150)
            current = name == s["current"] and s["phase"] not in ("peek", "round_over")
            ui.panel(surface, rect, color=ui.FELT_LIGHT, radius=12, shadow=False)
            if current:
                pygame.draw.rect(surface, ui.BRASS, rect, width=3, border_radius=12)
            label = name + ("  (out)" if name in out else "")
            ui.text(surface, label, (rect.x + 14, rect.y + 10), size=28, color=ui.PAPER, bold=True)
            ui.text(surface, f"{sum(s['slots'][name])} cards · {s['scores'][name]} pts",
                    (rect.right - 14, rect.y + 13), size=22, color=ui.PAPER_DARK, right=True)
            self._hand(surface, name, s["slots"][name], rect.x + 10, rect.y + 54, rect.w - 20, OPP_CARD,
                       self._clickable(name))

        # ---- log (left)
        log = ui.panel(surface, (20, 186, 290, 186), color=ui.FELT_DARK, radius=12, shadow=False)
        ui.text(surface, f"ROUND {s['round']}", (log.x + 12, log.y + 9), size=22, color=ui.BRASS, bold=True)
        for i, line in enumerate(self.log):
            ui.text(surface, line, (log.x + 12, log.y + 31 + i * 21), size=LOG_FONT, color=ui.PAPER)

        # ---- deck, pile, drawn card (centre)
        deck = pygame.Rect(360, 210, 90, 126)
        if s["deck_count"]:
            ui.draw_card(surface, deck)
        else:
            ui.empty_slot(surface, deck)
        ui.text(surface, f"deck: {s['deck_count']}", (deck.centerx, deck.bottom + 16), size=22,
                color=ui.PAPER_DARK, center=True)
        pile = deck.move(120, 0)
        flash_until, flash_colour = self.pile_flash
        if s["pile_top"]:
            ui.draw_card(surface, pile, s["pile_top"],
                         glow=flash_colour if time.monotonic() < flash_until else None)
        else:
            ui.empty_slot(surface, pile)
        ui.text(surface, f"pile: {s['pile_count']}", (pile.centerx, pile.bottom + 16), size=22,
                color=ui.PAPER_DARK, center=True)
        if s["holding_drawn"]:
            drawn = deck.move(250, 0)
            ui.draw_card(surface, drawn, s["drawn"], glow=ui.BRASS)   # face-down for the others
            who = "you drew" if s["drawn"] else f"{s['current']} drew"
            ui.text(surface, who, (drawn.centerx, drawn.bottom + 16), size=22, color=ui.BRASS, center=True)
        if s["phase"] == "cut_window":
            ui.text(surface, f"cut: {s['cut_rank']}", (pile.centerx, pile.y - 14), size=24,
                    color=ui.BRASS, bold=True, center=True)

        # ---- cut countdown bar
        if s["phase"] == "cut_window" and s["cut_remaining"] is not None:
            bar = pygame.Rect(330, 372, 340, 8)
            pygame.draw.rect(surface, ui.FELT_DARK, bar, border_radius=4)
            part = bar.copy()
            part.w = int(bar.w * min(1.0, self.cut_left() / config.CUT_WINDOW_SECONDS))
            pygame.draw.rect(surface, ui.BRASS, part, border_radius=4)

        # ---- status banner
        ui.text(surface, self.status(), (WIDTH // 2, 394), size=28, color=ui.WHITE, bold=True, center=True)
        if self.message:
            ui.text(surface, self.message, (WIDTH // 2, 420), size=24,
                    color=ui.GREEN_OK if self.message_ok else (255, 120, 100), center=True)

        # ---- my hand
        mine = pygame.Rect(150, 438, 700, 150)
        if self.my_turn and s["phase"] not in ("peek", "round_over"):
            pygame.draw.rect(surface, ui.BRASS, mine.inflate(16, 4), width=3, border_radius=14)
        me_label = f"{self.me} (you) · {s['scores'][self.me]} pts" + ("  · OUT this round" if self.me in out else "")
        ui.text(surface, me_label, (mine.x + 12, mine.y + 4), size=24, color=ui.PAPER, bold=True)
        self._hand(surface, self.me, s["slots"][self.me], mine.x, mine.y + 24, mine.w, MY_CARD,
                   self._clickable(self.me))

        # ---- buttons
        phase, turn = s["phase"], self.my_turn
        self.draw_btn.visible = turn and phase == "turn"
        self.draw_btn.enabled = bool(s["can_draw"])
        self.pair_btn.visible = turn and phase == "turn"
        self.pair_btn.label = "Cancel" if self.pair_mode else "Throw a pair"
        self.pablo_btn.visible = turn and phase == "turn" and not self.pair_mode
        armed = time.monotonic() < self.pablo_armed_until
        self.pablo_btn.kind = "danger" if armed else "secondary"
        self.pablo_btn.label = "Confirm Pablo" if armed else "Pablo!"
        if not armed and self.message.startswith("Sure? A wrong Pablo"):
            self.show("")
        self.throw_btn.visible = turn and phase == "drawn"
        self.skip_btn.visible = turn and phase == "power"
        for b in self.buttons:
            b.draw(surface)

    # ------------------------------------------------------------------ results
    def headline(self, result):
        caller, winner = result["caller"], result["winner"]
        reason = result["reason"]
        if reason == "empty_hand":
            return f"{winner} got rid of every card!"
        if reason == "disqualification":
            return f"{winner} is the last player standing"
        if winner is None:
            return f"{caller} called Pablo: tie for the lowest hand, nothing happens"
        if result["pablo_correct"]:
            return f"{caller} called Pablo and had the lowest hand!"
        return f"{caller} called Pablo... but {winner} was lower! (+50)"

    def draw_results(self, surface):
        msg = self.round_result
        result, totals = msg["result"], msg["scores"]
        title = "MATCH OVER" if self.final else f"ROUND {msg['round']} RESULTS"
        ui.text(surface, title, (WIDTH // 2, 48), size=54, color=ui.BRASS, bold=True, center=True)
        ui.text(surface, self.headline(result), (WIDTH // 2, 92), size=30, color=ui.WHITE, bold=True, center=True)

        board = ui.panel(surface, (40, 120, WIDTH - 80, 410), radius=14)
        cols = {"name": board.x + 20, "cards": board.x + 175, "hand": board.x + 600,
                "round": board.x + 720, "total": board.x + 840}
        for key, label in (("hand", "HAND"), ("round", "ROUND"), ("total", "TOTAL")):
            ui.text(surface, label, (cols[key], board.y + 14), size=22, color=ui.INK_SOFT, bold=True, center=True)
        players = list(totals)
        ranking = sorted(players, key=lambda p: totals[p])
        row_h = min(90, (board.h - 50) // len(players))
        for i, name in enumerate(players):
            y = board.y + 42 + i * row_h
            if i:
                pygame.draw.line(surface, ui.PAPER_DARK, (board.x + 14, y - 6), (board.right - 14, y - 6), 1)
            label = name + (" (you)" if name == self.me else "")
            ui.text(surface, label, (cols["name"], y + 10), size=28, bold=True)
            if name == result["winner"]:
                ui.text(surface, "round winner", (cols["name"], y + 38), size=20, color=ui.GREEN_OK, bold=True)
            elif name == result["caller"]:
                ui.text(surface, "called Pablo", (cols["name"], y + 38), size=20, color=ui.RED, bold=True)
            out = name in result.get("disqualified", [])
            cards = [c for c in result["hands"].get(name, []) if c]
            if out:
                ui.text(surface, "disqualified (more than 12 cards)", (cols["cards"], y + 22), size=24, color=ui.RED)
            else:
                w = 42
                step = min(w + 6, (400 - w) / max(1, len(cards) - 1)) if len(cards) > 1 else 0
                for j, code in enumerate(cards):
                    ui.draw_card(surface, (int(cols["cards"] + j * step), y, w, 58), code)
                if not cards:
                    ui.text(surface, "no cards left", (cols["cards"], y + 20), size=24, color=ui.INK_SOFT)
            hand = result["totals"].get(name)
            ui.text(surface, "-" if hand is None else hand, (cols["hand"], y + 28), size=30, center=True)
            score = result["scores"][name]
            colour = ui.GREEN_OK if score < 0 else (ui.RED if score >= 50 else ui.INK)
            ui.text(surface, f"{score:+d}", (cols["round"], y + 28), size=32, color=colour, bold=True, center=True)
            ui.text(surface, totals[name], (cols["total"], y + 28), size=34, bold=True, center=True)

        if self.final:
            winners = self.final["winners"]
            text = (" and ".join(winners) + (" win" if len(winners) > 1 else " wins")
                    + f" the match after {self.final['rounds']} rounds!")
            ui.text(surface, text, (WIDTH // 2, 553), size=32, color=ui.BRASS, bold=True, center=True)
            ui.text(surface, "Ranking: " + "  ".join(f"{i + 1}. {p} ({totals[p]})" for i, p in enumerate(ranking)),
                    (WIDTH // 2, 652), size=24, color=ui.PAPER, center=True)
            self.back_btn.rect.y = 578
            self.back_btn.visible = True
        elif msg.get("match_over"):
            ui.text(surface, "...", (WIDTH // 2, 580), size=30, color=ui.PAPER, center=True)
        else:
            host = self.app.room["host"] if self.app.room else None
            if host == self.me:
                self.next_btn.visible = True
            else:
                ui.text(surface, f"Waiting for {host} to deal the next round...", (WIDTH // 2, 585),
                        size=28, color=ui.PAPER, center=True)
            ui.text(surface, "First to reach 100 points ends the match. Lowest total wins.",
                    (WIDTH // 2, 640), size=22, color=ui.PAPER_DARK, center=True)
        for b in (self.next_btn, self.back_btn):
            b.draw(surface)
