"""Rooms and the lobby — pure logic, no sockets.

The server calls these methods while holding its lock, then sends the
resulting updates to the players concerned.
"""
from common import config

WAITING = "waiting"
PLAYING = "playing"
MAX_ROOM_NAME = 24


class LobbyError(Exception):
    """A refused lobby action (room full, not the host...)."""


class Room:
    def __init__(self, room_id, name, size, host):
        self.id = room_id
        self.name = name
        self.size = size              # maximum number of players (3 or 4)
        self.host = host
        self.players = [host]         # in seat order
        self.status = WAITING
        self.disconnected = []        # players whose connection dropped mid-game (seat kept)

    @property
    def connected(self):
        return [p for p in self.players if p not in self.disconnected]

    @property
    def can_start(self):
        return self.status == WAITING and len(self.connected) >= config.MIN_PLAYERS_TO_START

    def to_dict(self):
        return {"id": self.id, "name": self.name, "size": self.size, "host": self.host,
                "players": list(self.players), "status": self.status, "can_start": self.can_start,
                "disconnected": list(self.disconnected)}

    def _pick_host(self):
        if self.host not in self.connected and self.connected:
            self.host = self.connected[0]


class Lobby:
    def __init__(self):
        self.rooms = {}               # room id -> Room
        self.player_room = {}         # username -> room id
        self._next_id = 1

    def room_of(self, username):
        room_id = self.player_room.get(username)
        return self.rooms.get(room_id) if room_id is not None else None

    def list(self):
        return [room.to_dict() for room in self.rooms.values()]

    def create(self, username, name, size):
        if username in self.player_room:
            raise LobbyError("you are already in a room")
        name = name.strip()
        if not 1 <= len(name) <= MAX_ROOM_NAME:
            raise LobbyError(f"room name must be 1-{MAX_ROOM_NAME} characters")
        if size not in config.ALLOWED_ROOM_SIZES:
            raise LobbyError(f"room size must be one of {list(config.ALLOWED_ROOM_SIZES)}")
        room = Room(self._next_id, name, size, username)
        self._next_id += 1
        self.rooms[room.id] = room
        self.player_room[username] = room.id
        return room

    def join(self, username, room_id):
        if username in self.player_room:
            raise LobbyError("you are already in a room")
        room = self.rooms.get(room_id)
        if room is None:
            raise LobbyError("this room does not exist")
        if room.status != WAITING:
            raise LobbyError("this game has already started")
        if len(room.players) >= room.size:
            raise LobbyError("room is full")
        room.players.append(username)
        self.player_room[username] = room.id
        return room

    def leave(self, username):
        """Removes the player from their room. Returns (room, deleted).

        The next player becomes host if the host leaves; an empty room is deleted.
        """
        room = self.room_of(username)
        if room is None:
            raise LobbyError("you are not in a room")
        room.players.remove(username)
        del self.player_room[username]
        if not room.players:
            del self.rooms[room.id]
            return room, True
        if username in room.disconnected:
            room.disconnected.remove(username)
        if not room.connected:                 # only disconnected players left: close the room
            for p in room.players:
                self.player_room.pop(p, None)
            del self.rooms[room.id]
            return room, True
        room._pick_host()
        return room, False

    def disconnect(self, username):
        """A player's connection dropped. Returns (room, deleted).

        During a game the seat is kept so the player can log back in and come back;
        otherwise it is the same as leaving.
        """
        room = self.room_of(username)
        if room is None:
            raise LobbyError("you are not in a room")
        if room.status != PLAYING:
            return self.leave(username)
        room.disconnected.append(username)
        if not room.connected:
            for p in room.players:
                self.player_room.pop(p, None)
            del self.rooms[room.id]
            return room, True
        room._pick_host()
        return room, False

    def reconnect(self, username):
        """The player logged back in: returns their room if a seat is waiting for them."""
        room = self.room_of(username)
        if room is not None and username in room.disconnected:
            room.disconnected.remove(username)
            return room
        return None

    def finish(self, room_id):
        """The match is over: the room waits for the host to start a new one.

        Players who are still disconnected lose their seat now.
        """
        room = self.rooms.get(room_id)
        if room:
            room.status = WAITING
            for p in list(room.disconnected):
                room.players.remove(p)
                self.player_room.pop(p, None)
            room.disconnected = []
        return room

    def start(self, username):
        room = self.room_of(username)
        if room is None:
            raise LobbyError("you are not in a room")
        if room.host != username:
            raise LobbyError("only the host can start the game")
        if room.status != WAITING:
            raise LobbyError("the game has already started")
        if len(room.connected) < config.MIN_PLAYERS_TO_START:
            raise LobbyError(f"at least {config.MIN_PLAYERS_TO_START} players are needed")
        room.status = PLAYING
        return room
