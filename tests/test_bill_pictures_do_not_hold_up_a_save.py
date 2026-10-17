"""A slow or unreachable server must not hold every bill save (owner, 17 Oct 2026: "saving
bills or editing them are very slow in the new version, online").

Each bill print asks the store's settings for the logo and, since the signature picture,
for the signature too. Each asked on its own with a 30 s timeout and a failure was not
remembered, so with the server down every bill waited for two timeouts. Now one request
serves both, it gives up after 8 s, and a failure is remembered for a minute.
"""
from __future__ import annotations

import unittest
from unittest import mock

import core.store_images as si


class OneAskForBothPictures(unittest.TestCase):
    def setUp(self):
        si._reset_server_cache()
        for p in (mock.patch.object(si, "_is_online", return_value=True),
                  mock.patch.object(si, "_store_key", return_value="Store_A"),
                  mock.patch("core.server_live._token", return_value="t")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(si._reset_server_cache)

    def test_logo_and_signature_share_one_request(self):
        rows = [{"name": "bill_logo_image", "value": "data:image/png;base64,QQ=="}]
        with mock.patch("core.server_api._request", return_value={"ok": True, "data": rows}) as req:
            self.assertTrue(si.fetch_from_server(si.BILL_LOGO).startswith("data:image/png"))
            self.assertEqual(si.fetch_from_server(si.BILL_SIGNATURE), "")
        self.assertEqual(req.call_count, 1)
        self.assertEqual(req.call_args.kwargs["timeout"], 8)

    def test_a_server_that_does_not_answer_is_asked_once_a_minute(self):
        with mock.patch("core.server_api._request", side_effect=TimeoutError("timed out")) as req:
            for _ in range(5):
                self.assertEqual(si.fetch_from_server(si.BILL_SIGNATURE), "")
                self.assertEqual(si.fetch_from_server(si.BILL_LOGO), "")
        self.assertEqual(req.call_count, 1)

    def test_after_the_minute_it_asks_again(self):
        clock = [1000.0]
        with mock.patch("core.server_api._request", side_effect=TimeoutError("x")) as req, \
             mock.patch.object(si.time, "time", side_effect=lambda: clock[0]):
            si.fetch_from_server(si.BILL_LOGO)
            clock[0] += 61
            si.fetch_from_server(si.BILL_LOGO)
        self.assertEqual(req.call_count, 2)

    def test_each_store_has_its_own_answer(self):
        with mock.patch("core.server_api._request", return_value={"ok": True, "data": []}) as req:
            si.fetch_from_server(si.BILL_LOGO)
            with mock.patch.object(si, "_store_key", return_value="Store_B"):
                si.fetch_from_server(si.BILL_LOGO)
        self.assertEqual(req.call_count, 2)


if __name__ == "__main__":
    unittest.main()
