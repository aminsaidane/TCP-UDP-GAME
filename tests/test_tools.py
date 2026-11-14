"""The demo tools: traffic statistics and the network check."""
import contextlib
import io
import sys
import time
from unittest import mock

from common import config
from client import check
from server.stats import TrafficStats
from tests.test_server import ServerTestCase


class StatsTests(ServerTestCase):
    def test_summary_format(self):
        stats = TrafficStats()
        self.assertEqual(stats.summary(), "no traffic yet")
        stats.count("tcp", "in", "draw", 16)
        stats.count("tcp", "in", "draw", 16)
        stats.count("udp", "in", "heartbeat", 60)
        text = stats.summary()
        self.assertIn("TCP in: 2 msgs / 32 B (draw 2)", text)
        self.assertIn("UDP in: 1 msgs / 60 B (heartbeat 1)", text)

    def test_server_counts_its_traffic(self):
        self.logged_in("amin")
        time.sleep(0.2)                       # replies are sent by the writer thread
        messages, sizes = self.server.stats.snapshot()
        self.assertEqual(messages[("tcp", "in", "login")], 1)
        self.assertEqual(messages[("tcp", "out", "login_ok")], 1)
        self.assertGreater(sizes[("tcp", "out")], 0)


class NetworkCheckTests(ServerTestCase):
    def run_check(self, *args):
        out = io.StringIO()
        with mock.patch.object(config, "DISCOVERY_PORT", self.server.udp.discovery_port), \
                mock.patch.object(sys, "argv", ["check", *args]), contextlib.redirect_stdout(out):
            code = check.main()
        return code, out.getvalue()

    def test_everything_ok(self):
        code, text = self.run_check(f"127.0.0.1:{self.server.port}")
        self.assertEqual(code, 0, text)
        self.assertIn("connected (3-way handshake", text)
        self.assertIn("the Pablo server answered over TCP", text)
        self.assertIn("5/5 UDP packets answered", text)
        self.assertIn("All good", text)

    def test_server_not_running(self):
        code, text = self.run_check("127.0.0.1:1")
        self.assertEqual(code, 1)
        self.assertIn("server is not running on that port", text)
