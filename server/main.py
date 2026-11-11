"""The Pablo server (TCP part): accounts and login.

Run it with:   python -m server.main            (listens on every interface, port 5000)
               python -m server.main --port 6000 --users-file accounts.json

One thread accepts connections; every client then gets its own thread that
loops recv -> validate -> handle -> reply. A slow or crashed client only
affects its own thread.
"""
import argparse
import logging
import secrets
import socket
import threading

from common import config
from common import protocol as proto
from server.accounts import AccountError, AccountStore

log = logging.getLogger("pablo")

MAX_BAD_MESSAGES = 5          # invalid messages in a row before a client is dropped
PUBLIC_TYPES = {"register", "login"}   # the only messages allowed before logging in


class Session:
    """One connected client."""

    def __init__(self, conn: proto.Connection, addr):
        self.conn = conn
        self.addr = f"{addr[0]}:{addr[1]}"
        self.username = None
        self.token = None
        self.bad_messages = 0

    def send(self, msg):
        try:
            self.conn.send(msg)
        except OSError:
            pass   # the client is gone; its own thread will notice and clean up

    def __str__(self):
        return f"{self.username or '?'}@{self.addr}"


class PabloServer:
    def __init__(self, host=config.SERVER_BIND, port=config.TCP_PORT, users_file="users.json"):
        self.host, self.port = host, port
        self.accounts = AccountStore(users_file)
        self.sessions = {}                  # username -> Session (logged-in players only)
        self.lock = threading.Lock()        # protects self.sessions
        self.listener = None
        self.running = False
        self.handlers = {
            "register": self.on_register,
            "login": self.on_login,
            "logout": self.on_logout,
        }

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
            s.conn.close()
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
                if self.sessions.get(session.username) is session:
                    del self.sessions[session.username]
            log.info("%s disconnected", session)
        else:
            log.info("%s disconnected (not logged in)", session.addr)
        session.conn.close()

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
