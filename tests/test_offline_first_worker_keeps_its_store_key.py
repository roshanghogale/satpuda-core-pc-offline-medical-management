"""A store switch while the offline-first worker is in the middle of a cycle (audit, 25 Oct 2026).

Every server call read "the active store's key" when it was made. A cycle still running when the
shop switched stores (a long catch-up pull, a big push) went on with the NEW store's key: the old
store file's events landed in the other store on the server and the other store's changes were
written into the old file. The worker now takes its store's key when it starts and every call of
its cycle uses that one.
"""
from __future__ import annotations

import threading
import unittest
from unittest import mock

from core.offline_first import client, worker


class KeyHeldForTheCycle(unittest.TestCase):
    def test_a_cycle_running_across_a_switch_keeps_the_old_stores_key(self):
        active = {"token": "KEY-OLD-STORE"}
        seen = []
        started, release, done = threading.Event(), threading.Event(), threading.Event()

        def run_once():
            seen.append(client._token())          # first server call of the cycle
            started.set()
            release.wait(5)                      # ... the shop switches stores meanwhile ...
            seen.append(client._token())          # a later call of the SAME cycle
            done.set()
            return {}

        with mock.patch("core.server_api.store_token_for_active", lambda: active["token"]):
            w = worker.Worker(":memory:")
            self.assertEqual(w.token, "KEY-OLD-STORE")
            w.cycle.run_once = run_once
            w._start_hints = lambda: None
            w.start()
            self.assertTrue(started.wait(5))
            active["token"] = "KEY-NEW-STORE"    # the switch
            self.assertEqual(client._token(), "KEY-NEW-STORE")   # other threads: the active store
            release.set()
            self.assertTrue(done.wait(5))
            w._stop.set()
            w._kick.set()
            w.thread.join(5)
        self.assertEqual(seen, ["KEY-OLD-STORE", "KEY-OLD-STORE"])

    def test_without_a_bound_key_calls_follow_the_active_store(self):
        with mock.patch("core.server_api.store_token_for_active", lambda: "KEY-ACTIVE"):
            self.assertEqual(client._token(), "KEY-ACTIVE")
            with client.bound_token("KEY-BOUND"):
                self.assertEqual(client._token(), "KEY-BOUND")
            self.assertEqual(client._token(), "KEY-ACTIVE")


if __name__ == "__main__":
    unittest.main()
