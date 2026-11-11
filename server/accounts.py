"""Player accounts, saved in a JSON file on the server.

Passwords are never stored. Each account keeps a random *salt* and the
PBKDF2 hash of salt + password; logging in recomputes the hash and compares.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
MIN_PASSWORD = 4
HASH_ITERATIONS = 100_000


class AccountError(Exception):
    """Registration refused (bad name, weak password, name taken)."""


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS).hex()


class AccountStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()   # several client threads may register at once
        self._users = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self._users = json.load(f)

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._users, f, indent=2)
        os.replace(tmp, self.path)       # atomic: a crash never leaves a half-written file

    def register(self, username: str, password: str):
        if not USERNAME_RE.match(username):
            raise AccountError("username must be 3-16 letters, digits or _")
        if len(password) < MIN_PASSWORD:
            raise AccountError(f"password must be at least {MIN_PASSWORD} characters")
        salt = secrets.token_bytes(16)
        record = {"salt": salt.hex(), "hash": _hash(password, salt)}
        with self._lock:
            if username.lower() in (u.lower() for u in self._users):
                raise AccountError("username already taken")
            self._users[username] = record
            self._save()

    def check_login(self, username: str, password: str) -> bool:
        with self._lock:
            record = self._users.get(username)
        if record is None:
            return False
        expected = record["hash"]
        actual = _hash(password, bytes.fromhex(record["salt"]))
        return hmac.compare_digest(expected, actual)   # constant-time comparison
