"""The Online back-dated check waits no longer than its budget, even on a hung link.

core.sale_availability gives the whole question 15 seconds and each page at most what is
left of that. But server_api._request tried a timed-out request a second time with the SAME
timeout, so a link that hung on every read held the save-time check for 24 seconds (12 + 12)
-- on the Tk thread in Classic, where the save window froze for all of it. The retry now gets
only what is left of the budget, and none at all when nothing is left.

The HTTP connection is faked and the clock is faked; nothing reaches a server and nothing
really sleeps.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from unittest import mock

from core import sale_availability, server_api  # noqa: E402

PAST = (date.today() - timedelta(days=10)).isoformat()


class _HungLink(unittest.TestCase):
    def setUp(self):
        sale_availability.invalidate()
        self.addCleanup(sale_availability.invalidate)
        self.clock = [1000.0]
        self.attempts: list[tuple[float, float]] = []
        clock, attempts = self.clock, self.attempts

        class Hanging:
            """Every read waits its whole socket timeout, then times out."""

            timeout = 120.0

            def request(self, *a, **k):
                pass

            def getresponse(self):
                attempts.append((clock[0], float(self.timeout)))
                clock[0] += float(self.timeout)
                raise TimeoutError("timed out")

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_catalog._token", return_value="t"),
            mock.patch.object(server_api, "_acquire_conn", side_effect=lambda _key: Hanging()),
            mock.patch.object(server_api, "_shutdown_conn", return_value=None),
            mock.patch.object(server_api, "api_base", return_value="https://example.invalid"),
            mock.patch("time.monotonic", side_effect=lambda: clock[0]),
        ):
            stack.enter_context(patch)


class AHungLinkIsGivenOnlyTheBudget(_HungLink):
    def test_the_save_time_check_gives_up_inside_its_budget(self):
        start = self.clock[0]
        got = sale_availability.batches_missing_on_online(PAST)  # the save-time check waits
        self.assertEqual(got, frozenset(), "a store that cannot answer must hide nothing")
        waited = self.clock[0] - start
        self.assertLessEqual(
            waited,
            sale_availability._CHECK_BUDGET_SECONDS + 0.01,
            f"the check waited {waited:.1f} s; attempts (start, timeout) = {self.attempts}",
        )

    def test_a_retry_with_nothing_left_is_not_sent(self):
        deadline = self.clock[0] + 5.0
        with self.assertRaises(RuntimeError):
            server_api._request("GET", "/api/sync/medicines", token="t", timeout=12.0,
                                deadline=deadline)
        self.assertEqual([t for _, t in self.attempts], [5.0])

    def test_without_a_deadline_the_request_is_unchanged(self):
        with self.assertRaises(RuntimeError):
            server_api._request("GET", "/api/sync/medicines", token="t", timeout=12.0)
        self.assertEqual([t for _, t in self.attempts], [12.0, 12.0])


if __name__ == "__main__":
    unittest.main()
