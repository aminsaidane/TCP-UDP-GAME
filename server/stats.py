"""Counts the server's network traffic by transport and message type.

Started with `python -m server.main --stats`, the server prints a summary every
10 seconds. Handy during the demo: it shows live, in numbers, that game moves
travel over TCP while heartbeats, hover and timer ticks travel over UDP, and
it gives something to compare with the Wireshark capture.
"""
import threading
from collections import Counter


class TrafficStats:
    def __init__(self):
        self._lock = threading.Lock()       # updated from many threads
        self.messages = Counter()           # (channel, direction, type) -> count
        self.bytes = Counter()              # (channel, direction) -> bytes

    def count(self, channel, direction, msg_type, size):
        """channel: 'tcp', 'udp' or 'discovery'; direction: 'in' or 'out'."""
        with self._lock:
            self.messages[(channel, direction, msg_type)] += 1
            self.bytes[(channel, direction)] += size

    def snapshot(self):
        with self._lock:
            return Counter(self.messages), Counter(self.bytes)

    def summary(self):
        messages, sizes = self.snapshot()
        parts = []
        for channel in ("tcp", "udp", "discovery"):
            for direction in ("in", "out"):
                types = {t: n for (c, d, t), n in messages.items() if c == channel and d == direction}
                if not types:
                    continue
                total = sum(types.values())
                top = ", ".join(f"{t} {n}" for t, n in sorted(types.items(), key=lambda x: -x[1])[:4])
                parts.append(f"{channel.upper()} {direction}: {total} msgs / {sizes[(channel, direction)]} B ({top})")
        return " | ".join(parts) if parts else "no traffic yet"
