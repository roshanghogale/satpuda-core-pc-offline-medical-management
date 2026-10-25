"""Offline-first: a change travels at once, not at the next tick.

The worker used to wake every 6 s and nothing woke it sooner: this PC's own bill waited for
the tick before it was sent, and another device's bill waited for the tick before it was
pulled. Now a POST that wrote to the store kicks the worker (core/desktop_api.py) and the
server's sync_hint over /ws/sync kicks it when another device's change landed.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.offline_first import worker  # noqa: E402
from tests.test_offline_first_pc_local import make_store  # noqa: E402


class FakeSocket:
    """Stands in for core.sync_ws.SyncWebSocket: remembers the callbacks, delivers hints."""

    made: list = []

    def __init__(self):
        self.started = False
        self.stopped = False
        self.on_hint = None
        self.on_connect = None
        self.connected = False
        FakeSocket.made.append(self)

    def start(self, token_fn, on_hint, on_connect=None):
        self.started = True
        self.token_fn = token_fn
        self.on_hint = on_hint
        self.on_connect = on_connect
        self.connected = True

    def stop(self):
        self.stopped = True
        self.connected = False


class WorkerHints(unittest.TestCase):
    def setUp(self):
        self.path = make_store()
        self.addCleanup(self._cleanup)
        FakeSocket.made.clear()
        self.cycles = []
        self.woke = threading.Event()

        def run_once(_self):
            self.cycles.append(time.monotonic())
            self.woke.set()
            return {"sent": 0, "pulled": 0}

        for p in (
            mock.patch("core.sync_ws.SyncWebSocket", FakeSocket),
            mock.patch.object(worker.SyncCycle, "run_once", run_once),
            mock.patch.object(worker, "INTERVAL_IDLE", 60.0),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _cleanup(self):
        worker.stop()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_the_worker_listens_to_the_server_and_a_hint_runs_a_cycle_now(self):
        w = worker.start_for(self.path)
        self.assertTrue(self.woke.wait(5), "the first cycle did not run")
        self.woke.clear()
        sock = FakeSocket.made[-1]
        self.assertTrue(sock.started, "the hint socket was not started with the worker")
        self.assertIs(w.ws, sock)
        self.assertTrue(worker.hints_connected())
        n = len(self.cycles)
        t = time.monotonic()
        sock.on_hint(123, "android-other-phone", [{"collection": "sales", "local_id": 5}], False)
        self.assertTrue(self.woke.wait(5), "a hint did not wake the worker")
        self.assertGreater(len(self.cycles), n)
        self.assertLess(time.monotonic() - t, 2.0, "the hint took the slow tick instead of waking the loop")
        self.assertEqual(w.hints, 1)
        # Back-compat: the socket may call with two arguments only.
        self.woke.clear()
        sock.on_hint(124, None)
        self.assertTrue(self.woke.wait(5))
        # A reconnect pulls what happened meanwhile.
        self.woke.clear()
        sock.on_connect()
        self.assertTrue(self.woke.wait(5))
        worker.stop()
        self.assertTrue(sock.stopped, "the socket outlived the worker")
        self.assertFalse(worker.hints_connected())

    def test_no_websocket_module_means_polling_alone(self):
        import builtins

        real_import = builtins.__import__

        def no_ws(name, *a, **k):
            if name == "core.sync_ws":
                raise ImportError("no websocket here")
            return real_import(name, *a, **k)

        with mock.patch("builtins.__import__", side_effect=no_ws):
            w = worker.start_for(self.path)
        self.assertTrue(self.woke.wait(5), "the loop must run without the socket")
        self.assertIsNone(w.ws)
        self.assertFalse(worker.hints_connected())


class KickAfterWrite(unittest.TestCase):
    """core/desktop_api._of_kick_after_write: a POST that wrote wakes the worker, one that
    did not leaves it asleep."""

    def setUp(self):
        from core import desktop_api

        self.api = desktop_api
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE t (x)")
        self.kicks = 0

        def kick():
            self.kicks += 1

        for p in (
            mock.patch.dict(desktop_api._db, {"conn": self.conn}),
            mock.patch.object(desktop_api, "_of_worker_running", lambda: True),
            mock.patch("core.offline_first.worker.kick", kick),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.conn.close)

    def test_a_write_kicks_and_a_read_does_not(self):
        before = self.api._of_changes()
        self.assertGreaterEqual(before, 0)
        self.api._of_kick_after_write("/api/sales/search", before)
        self.assertEqual(self.kicks, 0, "a POST that changed nothing must not wake the worker")
        self.conn.execute("INSERT INTO t VALUES (1)")
        self.api._of_kick_after_write("/api/sales/save?x=1", before)
        self.assertEqual(self.kicks, 1)

    def test_the_sync_calls_themselves_do_not_kick(self):
        before = self.api._of_changes()
        self.conn.execute("INSERT INTO t VALUES (1)")
        self.api._of_kick_after_write("/api/offline-first/sync-now", before)
        self.api._of_kick_after_write("/api/sync/status", before)
        self.assertEqual(self.kicks, 0)

    def test_not_offline_first_means_no_bookkeeping(self):
        with mock.patch.object(self.api, "_of_worker_running", lambda: False):
            self.assertEqual(self.api._of_changes(), -1)
            self.conn.execute("INSERT INTO t VALUES (1)")
            self.api._of_kick_after_write("/api/sales/save", -1)
        self.assertEqual(self.kicks, 0)


if __name__ == "__main__":
    unittest.main()
