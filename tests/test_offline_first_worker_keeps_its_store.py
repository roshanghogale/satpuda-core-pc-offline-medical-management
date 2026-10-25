"""The offline-first worker syncs the store whose file it was started for, whatever the registry
says afterwards.

Settings > Stores > Switch points the registry at the other store and asks for a restart. Until
the app closed, the worker kept cycling on the OLD store file -- but every "active store" lookup
(the token, the store's key file) followed the registry: the old store's unsent events went up
with the NEW store's token, and the new store's changes were pulled into the old file with
capture off. No server is contacted here.
"""
from __future__ import annotations

import threading
import unittest
from unittest import mock

from core import store_manager
from core.offline_first import worker


class _Cycle:
    """Stands in for SyncCycle: records which store each cycle ran for."""

    def __init__(self, on_cycle):
        self.path = "store-a.db"
        self.seen: list[str] = []
        self.tokens: list[str] = []
        self.on_cycle = on_cycle
        self.closed = False

    def run_once(self):
        from core.offline_first import client

        self.seen.append(store_manager.get_active_store_key())
        self.tokens.append(client._token())
        self.on_cycle(len(self.seen))
        return {}

    def _c(self):
        raise RuntimeError("not used")

    def close(self):
        self.closed = True


class TheWorkerKeepsItsStore(unittest.TestCase):
    def setUp(self):
        self.registry = {"active_store_key": "STORE_A"}
        stores = {"STORE_A": {"store_key": "STORE_A"}, "STORE_B": {"store_key": "STORE_B"}}
        for target, fake in (
            ("load_registry", lambda *a, **k: dict(self.registry)),
            ("_find_store_by_key", lambda key: stores.get(key)),
        ):
            p = mock.patch.object(store_manager, target, fake)
            p.start()
            self.addCleanup(p.stop)
        # The token is the active store's: answer with the store's key so the test sees it.
        p = mock.patch("core.offline_first.client._token",
                       lambda: f"token-of-{store_manager.get_active_store_key()}")
        p.start()
        self.addCleanup(p.stop)

    def _run(self, w: worker.Worker, cycle: _Cycle, cycles: int) -> None:
        done = threading.Event()

        def on_cycle(n):
            if n == 1:
                # The shop switches store; the app has not restarted yet.
                self.registry["active_store_key"] = "STORE_B"
            if n >= cycles:
                w._stop.set()
                done.set()
            w._kick.set()

        cycle.on_cycle = on_cycle
        w.cycle = cycle
        with mock.patch.object(worker.Worker, "_start_hints", lambda self: None):
            w.start()
            self.assertTrue(done.wait(10))
            w.thread.join(10)

    def test_a_switch_of_the_registry_does_not_move_the_worker(self):
        w = worker.Worker("store-a.db")
        self.assertEqual(w.store_key, "STORE_A")
        cycle = _Cycle(lambda n: None)
        self._run(w, cycle, 3)
        self.assertEqual(cycle.seen, ["STORE_A", "STORE_A", "STORE_A"])
        self.assertEqual(set(cycle.tokens), {"token-of-STORE_A"})
        # the registry itself did move (the UI's thread sees the new store)
        self.assertEqual(store_manager.get_active_store_key(), "STORE_B")
        self.assertTrue(cycle.closed)

    def test_the_hint_socket_asks_for_its_own_stores_token(self):
        w = worker.Worker("store-a.db")
        self.registry["active_store_key"] = "STORE_B"
        captured = {}

        class _Ws:
            connected = False

            def start(self, token_fn, on_hint, on_connect):
                captured["token"] = token_fn()

            def stop(self):
                pass

        with mock.patch("core.sync_ws.SyncWebSocket", _Ws):
            w._start_hints()
        self.assertEqual(captured["token"], "token-of-STORE_A")

    def test_a_store_no_longer_on_this_pc_falls_back_to_the_registry(self):
        w = worker.Worker("store-x.db", store_key="GONE")
        with w._acting():
            self.assertEqual(store_manager.get_active_store_key(), "STORE_A")


if __name__ == "__main__":
    unittest.main()
