"""The Pablo server (TCP part): accounts, login, lobby, rooms and games.

Run it with:   python -m server.main            (listens on every interface, port 5000)
               python -m server.main --port 6000 --users-file accounts.json

One thread accepts connections; every client then gets its own thread that
loops recv -> validate -> handle -> reply. A slow or crashed client only
affects its own thread.
"""
import argparse
import logging
import queue
import secrets
import socket
import threading

from common import config
from common import protocol as proto
from server.accounts import AccountError, AccountStore
from server.game import ACTIONS, GameRoom
from server.lobby import Lobby, LobbyError

log = logging.getLogger("pablo")

MAX_BAD_MESSAGES = 5          # invalid messages in a row before a client is dropped
PUBLIC_TYPES = {"register", "login"}   # the only messages allowed before logging in


class Session:
    """One connected client.

    Every client has two threads: a *reader* (PabloServer._client_loop) and a
    *writer* that sends whatever is put in its outgoing queue. send() only adds
    to the queue, so it never blocks: a slow client delays only its own writer,
    and messages leave in exactly the order they were queued.
    """

    def __init__(self, conn: proto.Connection, addr):
        self.conn = conn
        self.addr = f"{addr[0]}:{addr[1]}"
        self.username = None
        self.token = None
        self.bad_messages = 0
        self.outgoing = queue.Queue()
        self.writer = threading.Thread(target=self._write_loop, name=f"writer-{self.addr}", daemon=True)
        self.writer.start()

    def send(self, msg):
        self.outgoing.put(msg)

    def _write_loop(self):
        while True:
            msg = self.outgoing.get()
            if msg is None:          # end of session
                return
            try:
                self.conn.send(msg)
            except OSError:
                return               # the client is gone; the reader thread cleans up

    def close(self):
        """Sends what is still queued (for up to 2 s), then closes the socket."""
        self.outgoing.put(None)
        self.writer.join(timeout=2)
        self.conn.close()

    def __str__(self):
        return f"{self.username or '?'}@{self.addr}"


class PabloServer:
    def __init__(self, host=config.SERVER_BIND, port=config.TCP_PORT, users_file="users.json"):
        self.host, self.port = host, port
        self.accounts = AccountStore(users_file)
        self.sessions = {}                  # username -> Session (logged-in players only)
        self.lobby = Lobby()
        self.games = {}                     # room id -> GameRoom
        self.lock = threading.Lock()        # protects self.sessions, self.lobby and self.games
        self.listener = None
        self.running = False
        self.handlers = {
            "register": self.on_register,
            "login": self.on_login,
            "logout": self.on_logout,
            "list_rooms": self.on_list_rooms,
            "create_room": self.on_create_room,
            "join_room": self.on_join_room,
            "leave_room": self.on_leave_room,
            "start_game": self.on_start_game,
        }
        for action in ACTIONS:              # peek, draw, swap... all go to the game
            self.handlers[action] = self.on_game_action

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        """Opens the listening socket and accepts clients in a background thread."""
        self.listener = socket.create_server((self.host, self.port), reuse_port=False)
        self.port = self.listener.getsockname()[1]   # real port if 0 was asked (tests)
        self.running = True
        threading.Thread(target=self._accept_loop, name="accept", daemon=True).start()
        log.info("TCP server listening on %s:%d", self.host, self.port)

    def stop(self):
        self.running = False
        if self.listener:
            self.listener.close()
        with self.lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            s.close()
        log.info("server stopped")

    def _accept_loop(self):
        while self.running:
            try:
                sock, addr = self.listener.accept()
            except OSError:
                break   # listener closed by stop()
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # send small messages at once
            session = Session(proto.Connection(sock), addr)
            log.info("connection from %s", session.addr)
            threading.Thread(target=self._client_loop, args=(session,),
                             name=f"client-{session.addr}", daemon=True).start()

    # ------------------------------------------------------------------ per-client thread
    def _client_loop(self, session: Session):
        try:
            while True:
                try:
                    msg = session.conn.recv()
                except proto.ProtocolError as exc:
                    if self._bad_message(session, "?", str(exc)):
                        break
                    continue
                if msg is None:
                    break                   # the client closed the connection
                if not self._handle(session, msg):
                    break
        except OSError as exc:              # connection reset, network cable pulled...
            log.info("%s connection error: %s", session, exc)
        finally:
            self._disconnect(session)

    def _bad_message(self, session, for_type, reason) -> bool:
        """Answers with an error. Returns True if the client should be dropped."""
        session.bad_messages += 1
        session.send(proto.error(for_type, reason))
        if session.bad_messages >= MAX_BAD_MESSAGES:
            log.warning("%s sent too many invalid messages, disconnecting", session)
            return True
        return False

    def _handle(self, session: Session, msg) -> bool:
        """Dispatches one message. Returns False to close the connection."""
        msg_type = msg.get("type", "?")
        try:
            proto.validate(msg, allowed=proto.TCP_TYPES)
        except proto.ProtocolError as exc:
            return not self._bad_message(session, msg_type, str(exc))
        session.bad_messages = 0
        if session.username is None and msg_type not in PUBLIC_TYPES:
            session.send(proto.error(msg_type, "log in first"))
            return True
        handler = self.handlers.get(msg_type)
        if handler is None:
            session.send(proto.error(msg_type, "not available yet"))
            return True
        return handler(session, msg) is not False

    def _disconnect(self, session: Session):
        if session.username:
            with self.lock:
                outbox = []
                if self.sessions.get(session.username) is session:
                    del self.sessions[session.username]
                    if self.lobby.room_of(session.username):
                        outbox = self._leave_room(session.username)
                    self._deliver(outbox)
            log.info("%s disconnected", session)
        else:
            log.info("%s disconnected (not logged in)", session.addr)
        session.close()

    # ------------------------------------------------------------------ account handlers
    def on_register(self, session, msg):
        try:
            self.accounts.register(msg["username"], msg["password"])
        except AccountError as exc:
            session.send(proto.error("register", str(exc)))
            return
        log.info("%s registered account '%s'", session.addr, msg["username"])
        session.send(proto.make("register_ok", username=msg["username"]))

    def on_login(self, session, msg):
        username = msg["username"]
        if session.username:
            session.send(proto.error("login", "already logged in"))
            return
        if not self.accounts.check_login(username, msg["password"]):
            log.info("%s failed login as '%s'", session.addr, username)
            session.send(proto.error("login", "wrong username or password"))
            return
        with self.lock:
            if username in self.sessions:
                session.send(proto.error("login", "this account is already logged in"))
                return
            session.username = username
            session.token = secrets.token_hex(16)   # identifies this player's UDP packets later
            self.sessions[username] = session
        log.info("%s logged in as '%s'", session.addr, username)
        session.send(proto.make("login_ok", username=username, token=session.token,
                                udp_port=config.UDP_PORT))

    def on_logout(self, session, msg):
        log.info("%s logged out", session)
        return False   # closes the connection; _disconnect does the cleanup


    # ------------------------------------------------------------------ lobby helpers
    # Lobby changes happen while holding self.lock, and the messages they produce
    # are queued *before* the lock is released. So if two players join at the
    # same moment, everyone receives the two room updates in the order the
    # changes really happened. (Queueing never blocks: see Session.)

    def _deliver(self, outbox):
        for session, msg in outbox:
            session.send(msg)

    def _deliver_named(self, outbox):
        """Same as _deliver, for (username, message) pairs produced by a GameRoom."""
        for name, msg in outbox:
            session = self.sessions.get(name)
            if session:
                session.send(msg)

    def _lobby_sessions(self):
        """Logged-in players who are not in a room: they see the room list."""
        return [s for name, s in self.sessions.items() if self.lobby.room_of(name) is None]

    def _room_sessions(self, room, exclude=None):
        return [self.sessions[p] for p in room.players if p in self.sessions and p != exclude]

    def _rooms_broadcast(self):
        msg = proto.make("rooms", rooms=self.lobby.list())
        return [(s, msg) for s in self._lobby_sessions()]

    def _room_update(self, room, exclude=None):
        msg = proto.make("room_update", room=room.to_dict())
        return [(s, msg) for s in self._room_sessions(room, exclude)]

    def _leave_room(self, username):
        room, deleted = self.lobby.leave(username)
        log.info("'%s' left room %d '%s'%s", username, room.id, room.name,
                 " (room closed)" if deleted else "")
        if deleted and room.id in self.games:
            self.games.pop(room.id).stop()
        outbox = [] if deleted else self._room_update(room)
        return outbox + self._rooms_broadcast()

    # ------------------------------------------------------------------ lobby handlers
    def on_list_rooms(self, session, msg):
        with self.lock:
            session.send(proto.make("rooms", rooms=self.lobby.list()))

    def on_create_room(self, session, msg):
        with self.lock:
            try:
                room = self.lobby.create(session.username, msg["name"], msg["size"])
            except LobbyError as exc:
                outbox = [(session, proto.error("create_room", str(exc)))]
            else:
                log.info("'%s' created room %d '%s' (max %d players)",
                         session.username, room.id, room.name, room.size)
                outbox = [(session, proto.make("room_joined", room=room.to_dict()))]
                outbox += self._rooms_broadcast()
            self._deliver(outbox)

    def on_join_room(self, session, msg):
        with self.lock:
            try:
                room = self.lobby.join(session.username, msg["room_id"])
            except LobbyError as exc:
                outbox = [(session, proto.error("join_room", str(exc)))]
            else:
                log.info("'%s' joined room %d '%s' (%d/%d)", session.username, room.id,
                         room.name, len(room.players), room.size)
                outbox = [(session, proto.make("room_joined", room=room.to_dict()))]
                outbox += self._room_update(room, exclude=session.username)
                outbox += self._rooms_broadcast()
            self._deliver(outbox)

    def on_leave_room(self, session, msg):
        with self.lock:
            try:
                outbox = self._leave_room(session.username)
            except LobbyError as exc:
                outbox = [(session, proto.error("leave_room", str(exc)))]
            else:
                # the player is back in the lobby: confirm first, then the room list
                outbox = [(session, proto.make("room_left"))] + outbox
            self._deliver(outbox)

    def on_start_game(self, session, msg):
        with self.lock:
            try:
                room = self.lobby.start(session.username)
            except LobbyError as exc:
                outbox = [(session, proto.error("start_game", str(exc)))]
            else:
                log.info("'%s' started the game in room %d '%s' with %s",
                         session.username, room.id, room.name, ", ".join(room.players))
                outbox = self._room_update(room) + self._rooms_broadcast()
                game = GameRoom(room.id, room.players, self.lock, self._deliver_named)
                self.games[room.id] = game
                self._deliver(outbox)
                outbox = []
                self._deliver_named(game.start())
            self._deliver(outbox)

    # ------------------------------------------------------------------ game handler
    def on_game_action(self, session, msg):
        with self.lock:
            room = self.lobby.room_of(session.username)
            game = self.games.get(room.id) if room else None
            if game is None:
                session.send(proto.error(msg["type"], "you are not in a game"))
                return
            self._deliver_named(game.act(session.username, msg))


def main():
    parser = argparse.ArgumentParser(description="Pablo game server")
    parser.add_argument("--host", default=config.SERVER_BIND, help="address to listen on")
    parser.add_argument("--port", type=int, default=config.TCP_PORT, help="TCP port")
    parser.add_argument("--users-file", default="users.json", help="where accounts are saved")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    server = PabloServer(args.host, args.port, args.users_file)
    server.start()
    try:
        threading.Event().wait()   # sleep until Ctrl+C
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
