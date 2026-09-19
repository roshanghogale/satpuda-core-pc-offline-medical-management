"""An Online read that failed must never be drawn as an empty shop.

In Online mode the desktop keeps no business data of its own: the engine's
database is an empty in-memory SQLite and every list on Inventory, Sales History
and Purchase History is a live call to the server. Each of those calls was
wrapped in `except Exception: return {}`, so any break in the link between this
PC and its store -- a renamed store, a rebuilt registry, a rejected re-pair --
rendered as a clean, working, completely EMPTY app with no reason given. That is
the "store cleared and there is no data" report; the ledger was on the server the
whole time.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desktop_pages_service import _online_read, _server_error_text  # noqa: E402


class OnlineReadRemembersFailure(unittest.TestCase):

    def test_a_good_read_is_passed_straight_through(self):
        failures = []
        self.assertEqual(_online_read(lambda: {"rows": [1, 2]}, failures), {"rows": [1, 2]})
        self.assertEqual(failures, [])

    def test_a_genuinely_empty_answer_is_not_an_error(self):
        failures = []
        self.assertEqual(_online_read(lambda: {"rows": []}, failures), {"rows": []})
        self.assertEqual(failures, [], "zero rows from a healthy server is not a failure")

    def test_a_none_answer_is_not_an_error_either(self):
        failures = []
        self.assertEqual(_online_read(lambda: None, failures), {})
        self.assertEqual(failures, [])

    def test_a_thrown_read_is_remembered_and_still_returns_empty(self):
        failures = []
        def boom():
            raise RuntimeError("connection refused")
        self.assertEqual(_online_read(boom, failures), {})
        self.assertEqual(failures, ["connection refused"])

    def test_an_exception_with_no_message_still_records_something(self):
        failures = []
        def boom():
            raise ValueError()
        _online_read(boom, failures)
        self.assertEqual(failures, ["ValueError"])

    def test_every_failing_read_on_the_page_is_recorded(self):
        failures = []
        for msg in ("first broke", "second broke"):
            def boom(m=msg):
                raise RuntimeError(m)
            _online_read(boom, failures)
        self.assertEqual(failures, ["first broke", "second broke"])


class TheBannerSaysSomethingUseful(unittest.TestCase):

    def test_no_failures_means_no_banner(self):
        self.assertEqual(_server_error_text([]), "")

    def test_an_unlinked_store_is_reported_verbatim(self):
        msg = ('This PC is not linked to any store on the server '
               '(local store "Shivkrupa Medical", key Store_Shivkrupa). '
               'Open Settings and connect it to the existing store.')
        self.assertEqual(_server_error_text([msg]), msg)

    def test_a_network_failure_says_it_cannot_reach_the_server(self):
        for msg in ("connection refused", "Read timed out", "Name or service not known: could not resolve"):
            out = _server_error_text([msg])
            self.assertIn("Cannot reach the server", out, msg)
            # It must not imply the shop has no data.
            self.assertNotIn("no data", out.lower())

    def test_a_rejected_pairing_tells_the_shop_to_reconnect(self):
        for msg in ("HTTP 403 Forbidden", "401 Unauthorized", "Store name does not match key"):
            out = _server_error_text([msg])
            self.assertIn("reconnect", out.lower(), msg)

    def test_an_unrecognised_failure_is_still_shown_not_hidden(self):
        out = _server_error_text(["something odd happened"])
        self.assertIn("something odd happened", out)

    def test_only_the_first_failure_is_reported(self):
        out = _server_error_text(["connection refused", "and another thing"])
        self.assertNotIn("and another thing", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
