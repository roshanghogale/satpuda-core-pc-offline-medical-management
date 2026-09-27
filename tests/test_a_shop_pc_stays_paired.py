"""A shop PC keeps its server link: 2026-09-27, store #127.

Its PC sent its folder key "Store_Shree_Gajanan_Medical_General_Stores" as the store's
name when its 7-day token ran out; the server refused (403) and the shop went offline.
The PC then re-paired about fifteen times in two seconds, 711 times in half an hour.
And another PC asked the server for bill numbers of the year 0020 while a year was
being typed into the Bill Date box.

    python -m unittest tests.test_a_shop_pc_stays_paired
"""
import threading
import time
import unittest
from datetime import date
from unittest import mock

from core import server_api as api
from core import store_manager as sm
from core.desktop_pages_service import _sane_bill_date


class _Sessions:
    def __init__(self):
        self.data = {}

    def load(self, key):
        return dict(self.data.get(key, {}))

    def save(self, key, value):
        self.data[key] = dict(value)


class PairingTest(unittest.TestCase):
    def setUp(self):
        api._PAIR_REFUSED.clear()
        self.sessions = _Sessions()
        self.patches = [mock.patch.object(api, "load_session", self.sessions.load),
                        mock.patch.object(api, "save_session", self.sessions.save),
                        mock.patch("core.store_manager.adopt_server_display_name", lambda *a: False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        api._PAIR_REFUSED.clear()

    def _ask(self):
        return api.ensure_store_session(store_key="Store_X", android_key="SC-1", store_name="X",
                                        device_id="pc-1", force_pair=True)

    def test_a_page_full_of_calls_pairs_once(self):
        calls = []

        def pair(**kw):
            calls.append(kw)
            time.sleep(0.2)
            return {"token": "t", "store": {"store_id": "store_x", "store_name": "X Medical"}}

        with mock.patch.object(api, "pair_store", pair):
            threads = [threading.Thread(target=self._ask) for _ in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)

    def test_a_refused_pairing_waits_before_asking_again(self):
        calls = []

        def refuse(**kw):
            calls.append(kw)
            raise api.ServerHttpError(403, "Store name does not match key")

        with mock.patch.object(api, "pair_store", refuse):
            for _ in range(15):
                with self.assertRaises(api.ServerHttpError):
                    self._ask()
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(api._PAIR_REFUSED["Store_X"][0] - time.time(), 25)

    def test_the_wait_grows_and_success_clears_it(self):
        with mock.patch.object(api, "pair_store", side_effect=api.ServerHttpError(403, "no")):
            with self.assertRaises(api.ServerHttpError):
                self._ask()
            api._PAIR_REFUSED["Store_X"] = (0.0,) + api._PAIR_REFUSED["Store_X"][1:]   # the wait is over
            with self.assertRaises(api.ServerHttpError):
                self._ask()
        self.assertEqual(api._PAIR_REFUSED["Store_X"][1], 2)
        self.assertGreater(api._PAIR_REFUSED["Store_X"][0] - time.time(), 55)
        api._PAIR_REFUSED["Store_X"] = (0.0,) + api._PAIR_REFUSED["Store_X"][1:]
        with mock.patch.object(api, "pair_store", return_value={"token": "t", "store": {"store_id": "s"}}):
            self._ask()
        self.assertNotIn("Store_X", api._PAIR_REFUSED)


class ServerNameTest(unittest.TestCase):
    def test_the_label_becomes_the_servers_name_and_the_folder_key_stays(self):
        reg = {"stores": [{"store_key": "Store_Shree_Gajanan_Medical_General_Stores",
                           "display_name": "Store_Shree_Gajanan_Medical_General_Stores"},
                          {"store_key": "Store_Roshan", "display_name": "Roshan"}]}
        saved = []
        with mock.patch.object(sm, "load_registry", return_value=reg), \
                mock.patch.object(sm, "save_registry", side_effect=lambda d: saved.append(d)):
            self.assertTrue(sm.adopt_server_display_name("Store_Shree_Gajanan_Medical_General_Stores",
                                                         "Shree Gajanan Medical & General Stores"))
            self.assertFalse(sm.adopt_server_display_name("Store_Roshan", "Roshan"))
        s = saved[0]["stores"][0]
        self.assertEqual(s["display_name"], "Shree Gajanan Medical & General Stores")
        self.assertEqual(s["store_key"], "Store_Shree_Gajanan_Medical_General_Stores")
        self.assertEqual(len(saved), 1)


class BillDateTest(unittest.TestCase):
    def test_a_year_still_being_typed_is_not_a_date(self):
        for typed in ("0020-07-21", "0002-07-21", "0202-07-21", "20-07-21", date(20, 7, 21), "", None):
            with self.subTest(typed=typed):
                self.assertIsNone(_sane_bill_date(typed))
        self.assertEqual(_sane_bill_date("2026-07-21"), "2026-07-21")
        self.assertEqual(_sane_bill_date(date(2026, 3, 20)), date(2026, 3, 20))


if __name__ == "__main__":
    unittest.main()
