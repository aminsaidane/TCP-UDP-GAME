"""The Pablo client (pygame).

Run it with:   python -m client.main
               python -m client.main --server 192.168.1.12

Screens: Login -> Lobby -> Room -> Table (the game).
The window loop runs at 30 frames per second: handle mouse/keyboard,
handle the messages the network thread received, draw.
"""
import argparse

import pygame

from common import config
from common import protocol as proto
from client import ui
from client.network import NetworkClient, parse_address
from client.table import TableScreen

WIDTH, HEIGHT = 1000, 680
FPS = 30


# ====================================================================== screens
class Screen:
    def __init__(self, app):
        self.app = app
        self.buttons = []
        self.inputs = []
        self.message = ""          # status line (errors in red, info in green)
        self.message_ok = False

    def show(self, message, ok=False):
        self.message, self.message_ok = message, ok

    def enter(self):
        """Called when the screen becomes active."""

    def handle_event(self, event):
        for widget in self.inputs + self.buttons:
            if widget.handle(event):
                return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB and self.inputs:
            ui.focus_next(self.inputs)
        elif event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.submit()

    def submit(self):
        """Enter key."""

    def draw_message(self, surface, y):
        if self.message:
            ui.text(surface, self.message, (WIDTH // 2, y), size=26,
                    color=ui.GREEN_OK if self.message_ok else ui.RED, bold=True, center=True)


class LoginScreen(Screen):
    def __init__(self, app):
        super().__init__(app)
        x = WIDTH // 2 - 170
        self.server = ui.TextInput((x, 250, 340, 44), "server IP", max_len=40, value=app.server_address)
        self.username = ui.TextInput((x, 330, 340, 44), "username", max_len=16)
        self.password = ui.TextInput((x, 410, 340, 44), "password", password=True, max_len=32)
        self.username.focused = True
        self.inputs = [self.server, self.username, self.password]
        self.login_btn = ui.Button((x, 482, 165, 48), "Log in", self.login)
        self.register_btn = ui.Button((x + 175, 482, 165, 48), "Register", self.register, kind="secondary")
        self.buttons = [self.login_btn, self.register_btn]

    def submit(self):
        self.login()

    def _credentials(self):
        user, pw = self.username.value.strip(), self.password.value
        if not user or not pw:
            self.show("Enter a username and a password")
            return None
        return user, pw

    def login(self):
        creds = self._credentials()
        if creds:
            self.app.request(self.server.value, proto.make("login", username=creds[0], password=creds[1]))
            self.show("Connecting...", ok=True)

    def register(self):
        creds = self._credentials()
        if creds:
            # After register_ok the app logs in automatically with the same credentials.
            self.app.pending_login = creds
            self.app.request(self.server.value, proto.make("register", username=creds[0], password=creds[1]))
            self.show("Creating account...", ok=True)

    def draw(self, surface):
        ui.felt_background(surface)
        ui.text(surface, "PABLO", (WIDTH // 2, 105), size=120, color=ui.BRASS, bold=True, center=True)
        ui.text(surface, "the memory card game  ·  TCP & UDP edition", (WIDTH // 2, 165),
                size=28, color=ui.PAPER, center=True)
        ui.panel(surface, (WIDTH // 2 - 200, 205, 400, 350))
        self.server.draw(surface, "SERVER")
        self.username.draw(surface, "USERNAME")
        self.password.draw(surface, "PASSWORD")
        for b in self.buttons:
            b.draw(surface)
        self.draw_message(surface, 590)
        ui.text(surface, "Tab: next field   ·   Enter: log in", (WIDTH // 2, 635),
                size=22, color=ui.PAPER_DARK, center=True)


class LobbyScreen(Screen):
    ROW_H = 64
    LIST_X, LIST_Y, LIST_W = 40, 140, 560

    def __init__(self, app):
        super().__init__(app)
        self.room_name = ui.TextInput((660, 200, 300, 44), "room name", max_len=24)
        self.inputs = [self.room_name]
        self.size = config.DEFAULT_ROOM_SIZE
        self.size_buttons = {n: ui.Button((660 + i * 155, 290, 145, 44), f"{n} players",
                                          lambda n=n: self.set_size(n), kind="secondary")
                             for i, n in enumerate(config.ALLOWED_ROOM_SIZES)}
        self.create_btn = ui.Button((660, 360, 300, 48), "Create room", self.create)
        self.refresh_btn = ui.Button((self.LIST_X + self.LIST_W - 120, 92, 120, 36), "Refresh",
                                     lambda: app.send(proto.make("list_rooms")), kind="secondary")
        self.logout_btn = ui.Button((WIDTH - 150, 20, 120, 40), "Log out", app.logout, kind="danger")
        self.join_buttons = []

    def enter(self):
        self.show("")
        self.app.send(proto.make("list_rooms"))

    def set_size(self, n):
        self.size = n

    def create(self):
        name = self.room_name.value.strip() or f"{self.app.username}'s room"
        self.app.send(proto.make("create_room", name=name, size=self.size))

    def submit(self):
        self.create()

    def _rebuild_join_buttons(self):
        self.join_buttons = []
        for i, room in enumerate(self.app.rooms[:7]):
            y = self.LIST_Y + i * (self.ROW_H + 8)
            open_seat = room["status"] == "waiting" and len(room["players"]) < room["size"]
            self.join_buttons.append(ui.Button(
                (self.LIST_X + self.LIST_W - 110, y + 12, 95, 40), "Join",
                lambda rid=room["id"]: self.app.send(proto.make("join_room", room_id=rid)),
                enabled=open_seat))

    @property
    def buttons(self):
        return [self.create_btn, self.refresh_btn, self.logout_btn,
                *self.size_buttons.values(), *self.join_buttons]

    @buttons.setter
    def buttons(self, value):
        pass   # built dynamically

    def draw(self, surface):
        self._rebuild_join_buttons()
        ui.felt_background(surface)
        ui.text(surface, "Lobby", (40, 22), size=56, color=ui.BRASS, bold=True)
        ui.text(surface, f"Logged in as {self.app.username}", (WIDTH - 170, 32),
                size=26, color=ui.PAPER, right=True)

        # room list
        ui.text(surface, f"Rooms ({len(self.app.rooms)})", (self.LIST_X, 100), size=32, color=ui.PAPER, bold=True)
        if not self.app.rooms:
            ui.text(surface, "No rooms yet — create one!", (self.LIST_X + self.LIST_W // 2, 260),
                    size=30, color=ui.PAPER_DARK, center=True)
        for i, room in enumerate(self.app.rooms[:7]):
            y = self.LIST_Y + i * (self.ROW_H + 8)
            row = ui.panel(surface, (self.LIST_X, y, self.LIST_W, self.ROW_H), radius=10, shadow=False)
            ui.text(surface, room["name"], (row.x + 16, row.y + 10), size=30, bold=True)
            ui.text(surface, "host: " + room["host"], (row.x + 16, row.y + 38), size=22, color=ui.INK_SOFT)
            count = f"{len(room['players'])}/{room['size']}"
            ui.text(surface, count, (row.x + 330, row.y + 20), size=32, bold=True)
            playing = room["status"] == "playing"
            ui.text(surface, "playing" if playing else "waiting", (row.x + 385, row.y + 24), size=24,
                    color=ui.RED if playing else ui.GREEN_OK, bold=True)
        for b in self.join_buttons:
            b.draw(surface)
        self.refresh_btn.draw(surface)

        # create panel
        ui.panel(surface, (635, 140, 340, 300))
        ui.text(surface, "New room", (660, 152), size=32, bold=True)
        self.room_name.draw(surface, "")
        ui.text(surface, "MAX PLAYERS", (660, 264), size=22, color=ui.INK_SOFT, bold=True)
        for n, b in self.size_buttons.items():
            b.kind = "primary" if n == self.size else "secondary"
            b.draw(surface)
        self.create_btn.draw(surface)
        ui.text(surface, f"The game can start with {config.MIN_PLAYERS_TO_START}+ players.",
                (805, 465), size=22, color=ui.PAPER_DARK, center=True)
        self.logout_btn.draw(surface)
        self.draw_message(surface, 640)


class RoomScreen(Screen):
    def __init__(self, app):
        super().__init__(app)
        self.start_btn = ui.Button((WIDTH // 2 - 230, 560, 220, 54), "Start game", self.start)
        self.leave_btn = ui.Button((WIDTH // 2 + 10, 560, 220, 54), "Leave room", self.leave, kind="danger")
        self.buttons = [self.start_btn, self.leave_btn]

    def enter(self):
        self.show("")

    def start(self):
        self.app.send(proto.make("start_game"))

    def leave(self):
        self.app.send(proto.make("leave_room"))

    def draw(self, surface):
        room = self.app.room
        ui.felt_background(surface)
        ui.text(surface, room["name"], (WIDTH // 2, 70), size=64, color=ui.BRASS, bold=True, center=True)
        ui.text(surface, f"{len(room['players'])} / {room['size']} players", (WIDTH // 2, 120),
                size=30, color=ui.PAPER, center=True)

        # one seat per possible player, drawn as a card
        size = room["size"]
        card_w, card_h, gap = 170, 230, 30
        x0 = WIDTH // 2 - (size * card_w + (size - 1) * gap) // 2
        for i in range(size):
            rect = pygame.Rect(x0 + i * (card_w + gap), 175, card_w, card_h)
            if i < len(room["players"]):
                name = room["players"][i]
                ui.panel(surface, rect, radius=14)
                pygame.draw.rect(surface, ui.BRASS, rect.inflate(-14, -14), width=2, border_radius=10)
                ui.text(surface, name, (rect.centerx, rect.centery - 10), size=32, bold=True, center=True)
                if name == room["host"]:
                    ui.text(surface, "HOST", (rect.centerx, rect.y + 30), size=24,
                            color=ui.BRASS_DARK, bold=True, center=True)
                if name == self.app.username:
                    ui.text(surface, "(you)", (rect.centerx, rect.centery + 22), size=24,
                            color=ui.INK_SOFT, center=True)
            else:
                pygame.draw.rect(surface, ui.FELT_LIGHT, rect, width=3, border_radius=14)
                ui.text(surface, "empty seat", rect.center, size=26, color=ui.SEAT_TEXT, center=True)

        is_host = room["host"] == self.app.username
        self.start_btn.visible = is_host and room["status"] == "waiting"
        self.start_btn.enabled = room["can_start"]
        if room["status"] == "playing":
            status = "The game is starting..."
        elif is_host:
            if not room["can_start"]:
                status = f"Waiting for at least {config.MIN_PLAYERS_TO_START} players..."
            elif len(room["players"]) < room["size"]:
                status = "You can start now, or wait for one more player"
            else:
                status = "The room is full: press Start!"
        else:
            status = f"Waiting for {room['host']} to start the game..."
        ui.text(surface, status, (WIDTH // 2, 470), size=30, color=ui.PAPER, center=True)
        if not self.start_btn.visible:
            self.leave_btn.rect.centerx = WIDTH // 2
        else:
            self.leave_btn.rect.x = WIDTH // 2 + 10
        for b in self.buttons:
            b.draw(surface)
        self.draw_message(surface, 640)


# ====================================================================== app
class App:
    def __init__(self, server_address=""):
        self.net = NetworkClient()
        self.server_address = server_address or config.SERVER_HOST
        self.username = None
        self.token = None
        self.rooms = []
        self.room = None
        self.pending_login = None       # credentials to log in with right after registering
        self.screens = {"login": LoginScreen(self), "lobby": LobbyScreen(self), "room": RoomScreen(self),
                        "table": TableScreen(self)}
        self.screen = self.screens["login"]

    def go(self, name):
        self.screen = self.screens[name]
        self.screen.enter()

    # ---------------------------------------------------------------- network
    def request(self, address, msg):
        """Sends a message, connecting first if needed (used by the login screen)."""
        if self.net.connected:
            self.net.send(msg)
        else:
            self.server_address = address
            host, port = parse_address(address)
            self.net.connect(host, port, first_message=msg)

    def send(self, msg):
        if not self.net.send(msg):
            self.screen.show("Not connected to the server")

    def logout(self):
        self.net.send(proto.make("logout"))
        self.net.close()
        self.username = self.room = None
        self.rooms = []
        self.go("login")
        self.screen.show("Logged out", ok=True)

    def handle_message(self, msg):
        kind = msg["type"]
        if kind == "_connect_failed":
            self.screens["login"].show(msg["message"])
        elif kind == "_disconnected":
            self.net.close()
            self.username = self.room = None
            self.go("login")
            self.screen.show("Lost the connection to the server")
        elif kind == "register_ok":
            if self.pending_login:
                user, pw = self.pending_login
                self.pending_login = None
                self.net.send(proto.make("login", username=user, password=pw))
        elif kind == "login_ok":
            self.username, self.token = msg["username"], msg["token"]
            self.go("lobby")
        elif kind == "rooms":
            self.rooms = msg["rooms"]
        elif kind == "room_joined":
            self.room = msg["room"]
            self.go("room")
        elif kind == "room_update":
            self.room = msg["room"]
            table = self.screens["table"]
            if self.screen is table and table.state and len(msg["room"]["players"]) < len(
                    table.state["players"]):
                self.screen.show("A player left the room (handled in Step 10)")
        elif kind == "room_left":
            self.room = None
            self.screens["table"].state = None
            self.go("lobby")
        elif kind == "game_start":
            self.screens["table"].on_game_start(msg)
            self.go("table")
        elif kind == "state":
            self.screens["table"].on_state(msg["state"])
        elif kind == "event":
            self.screens["table"].on_event(msg["event"])
        elif kind == "round_over":
            self.screens["table"].on_round_over(msg)
        elif kind == "match_over":
            self.screens["table"].on_match_over(msg)
        elif kind == "error":
            self.pending_login = None
            self.screen.show(msg["message"])

    # ---------------------------------------------------------------- frame
    def update(self):
        for msg in self.net.messages():
            self.handle_message(msg)

    def handle_event(self, event):
        self.screen.handle_event(event)

    def draw(self, surface):
        self.screen.draw(surface)


def main():
    parser = argparse.ArgumentParser(description="Pablo client")
    parser.add_argument("--server", default="", help="server IP (optionally ip:port)")
    args = parser.parse_args()

    pygame.init()
    pygame.display.set_caption("Pablo")
    window = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    app = App(args.server)
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            else:
                app.handle_event(event)
        app.update()
        app.draw(window)
        pygame.display.flip()
        clock.tick(FPS)
    app.net.send(proto.make("logout"))
    app.net.close()
    pygame.quit()


if __name__ == "__main__":
    main()
