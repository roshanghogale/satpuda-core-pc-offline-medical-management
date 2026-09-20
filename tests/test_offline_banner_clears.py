"""The "cannot reach server" bar must go when the server is back.

The bar shows the last catalog read failure, and the lists are cached, so after
a blip nothing re-read them and a shop kept reading the warning long after the
connection returned - which is how a bill that was already saved gets entered
twice. The connectivity monitor clears it on reconnect.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import online_catalog  # noqa: E402


class TheWarningBar(unittest.TestCase):

    def setUp(self):
        online_catalog.clear_errors()

    def tearDown(self):
        online_catalog.clear_errors()

    def test_a_failed_read_is_reported(self):
        with online_catalog._lock:
            online_catalog._last_error["customers"] = "Cannot reach server (...): getaddrinfo failed"
        self.assertIn("Cannot reach server", online_catalog.last_error())

    def test_reconnecting_clears_it(self):
        with online_catalog._lock:
            online_catalog._last_error["customers"] = "Cannot reach server"
            online_catalog._last_error["medicines_inventory"] = "Cannot reach server"
        online_catalog.clear_errors()
        self.assertEqual(online_catalog.last_error(), "")
        self.assertEqual(online_catalog.last_error("customers"), "")

    def test_the_lists_are_read_again_after_a_reconnect(self):
        with online_catalog._lock:
            online_catalog._cache["customers"] = (1.0, [{"id": 1, "name": "OLD COPY"}])
        online_catalog.invalidate()
        with online_catalog._lock:
            self.assertNotIn("customers", online_catalog._cache)

    def test_the_monitor_clears_the_bar_when_the_server_answers(self):
        """The reconnect branch of the monitor loop does both things."""
        import inspect
        from core import online_guard
        src = inspect.getsource(online_guard.start_connectivity_monitor)
        after_reconnect = src.split("_was_unreachable = False", 1)[1]
        self.assertIn("kick_flush()", after_reconnect)
        self.assertIn("clear_errors()", after_reconnect)
        self.assertIn("invalidate()", after_reconnect)


if __name__ == "__main__":
    unittest.main()
