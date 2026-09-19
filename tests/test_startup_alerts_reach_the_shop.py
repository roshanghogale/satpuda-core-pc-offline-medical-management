"""The startup alert popup, in the mode the shops actually run.

1. ONLINE the engine's connection is sqlite3.connect(":memory:") -- an empty schema
   shell. collect_startup_alerts ran its SQL against it, found no medicines and no
   customers, returned zero tabs, and /api/startup/alerts answered show=False. The
   Tauri dialog is `open={tabs.length}`, so on every Online shop it never opened:
   no expiry warning, no low stock, no dues. Silence looked like a clean shelf.
2. A failed store read came back as the same silence. Now it is an error the shell
   can show.
3. Offline, "Customer Due" listed EVERY customer: `total_due >= 0` with the default
   minimum of 0 let zero balances through. Android filters `> 0`.
4. Offline, the low-stock popup rows re-read batches without the hidden filter, so
   a batch the shop had removed from lists came back in the popup.
5. A month-only expiry (stored as YYYY-MM-01, which is how the phone writes MM/YY)
   was "Expired" in the alert lists from the 2nd of its month, while billing, Remove
   Expired and Inventory's intent (batch_visibility.expiry_cutoff) sell it until the
   month end. "Remove All Expired" then left those rows sitting in the Expired list.
6. Near-expiry ignored the medicine-type aliases low stock already honours, so a
   "Drop" batch used the 3-month default instead of the shop's Drops months.
7. Popup preferences (categories, re-check while running, test now) are real prefs
   that the popup logic reads.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import alert_monitoring_service as ams  # noqa: E402
from core import db_setup, sync_prefs  # noqa: E402
from core import desktop_startup_service as dss  # noqa: E402
from core import online_catalog as oc  # noqa: E402
from core import startup_alerts_data as sad  # noqa: E402
from core import startup_alerts_prefs as prefs  # noqa: E402
from core import store_query_client as sq  # noqa: E402

TODAY = date.today()


def _full_date(days: int) -> str:
    """An ISO date `days` from today that is NOT the 1st (the 1st means month-only)."""
    d = TODAY + timedelta(days=days)
    if d.day == 1:
        d += timedelta(days=1)
    return d.isoformat()


# One shelf, used for both modes.
MEDS = [
    # low stock: 3 < 10 (Tablet default)
    dict(id=1, name="ALPHA", type="Tablet", batch_no="A1", expiry_date=_full_date(400),
         stock_qty=3, unit="10", mrp=10, rate=8, is_hidden=0),
    # hidden batch of the same low name -- must not appear in the popup
    dict(id=2, name="ALPHA", type="Tablet", batch_no="A2", expiry_date=_full_date(400),
         stock_qty=2, unit="10", mrp=10, rate=8, is_hidden=1),
    # expired, in stock
    dict(id=3, name="BRAVO", type="Tablet", batch_no="B1", expiry_date=_full_date(-40),
         stock_qty=50, unit="10", mrp=10, rate=8, is_hidden=0),
    # expired but sold out -- not an alert
    dict(id=4, name="BRAVO", type="Tablet", batch_no="B0", expiry_date=_full_date(-40),
         stock_qty=0, unit="10", mrp=10, rate=8, is_hidden=0),
    # near expiry: 20 days, window 90
    dict(id=5, name="CHARLIE", type="Tablet", batch_no="C1", expiry_date=_full_date(20),
         stock_qty=50, unit="10", mrp=10, rate=8, is_hidden=0),
    # out of stock
    dict(id=6, name="DELTA", type="Tablet", batch_no="D1", expiry_date=_full_date(400),
         stock_qty=0, unit="15", mrp=12, rate=9, is_hidden=0),
    # month-only expiry of THIS month: good until the month end
    dict(id=7, name="ECHO", type="Tablet", batch_no="E1",
         expiry_date=TODAY.replace(day=1).isoformat(),
         stock_qty=50, unit="10", mrp=10, rate=8, is_hidden=0),
    # alias type "Drop" -> "Drops"; shop set Drops to 1 month; 60 days out
    dict(id=8, name="FOXTROT", type="Drop", batch_no="F1", expiry_date=_full_date(60),
         stock_qty=50, unit="5", mrp=10, rate=8, is_hidden=0),
]
CUSTOMERS = [
    dict(id=1, name="RAMESH", phone="9000000001", total_due=500.0),
    dict(id=2, name="SURESH", phone="9000000002", total_due=0.0),
]
SETTINGS = {"near_expiry_drops": "1"}


def _ins(conn, table, **vals):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    vals = {k: v for k, v in vals.items() if k in cols}
    conn.execute(
        f"INSERT INTO {table} ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
        tuple(vals.values()),
    )


def _shell():
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    for k, v in SETTINGS.items():
        conn.execute("INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)", (k, v))
    conn.commit()
    return conn


def _offline_store():
    conn = _shell()
    for m in MEDS:
        _ins(conn, "medicines", **m)
    for c in CUSTOMERS:
        _ins(conn, "customers", **c)
    conn.commit()
    return conn


def _tab(tabs, title):
    for t in tabs:
        if t["title"] == title:
            return t
    return None


class _PrefsIsolated(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._cfg = mock.patch.object(prefs, "_config_dir", lambda: self._dir)
        self._cfg.start()

    def tearDown(self):
        self._cfg.stop()
        shutil.rmtree(self._dir, ignore_errors=True)


class _Online(_PrefsIsolated):
    meds = MEDS
    medicines_error = ""

    def setUp(self):
        super().setUp()
        self._patches = [
            mock.patch.object(sync_prefs, "is_online_mode", lambda *a, **k: True),
            mock.patch.object(oc, "medicines", lambda *a, **k: [dict(m) for m in self.meds]),
            mock.patch.object(oc, "customers", lambda *a, **k: [dict(c) for c in CUSTOMERS]),
            mock.patch.object(
                oc, "last_error",
                lambda key="": self.medicines_error if key == "medicines_inventory" else "",
            ),
            mock.patch.object(sq, "list_sales", lambda *a, **k: {"rows": []}),
            # db_setup.initialise -> load_pharmacy_profile asks the SERVER in
            # Online; the suite guard blocked it, but a test must not try at all.
            mock.patch(
                "core.pharmacy_profile_io.fetch_profile_from_server",
                lambda *a, **k: None,
            ),
        ]
        for p in self._patches:
            p.start()
        self.conn = _shell()  # what Online really runs on: an EMPTY shell

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        super().tearDown()


class OnlinePopupReadsTheStore(_Online):
    def test_online_popup_is_not_empty(self):
        res = dss.get_startup_alerts(self.conn)
        self.assertTrue(res["ok"])
        self.assertTrue(res["show"], "Online popup came back empty from the :memory: shell")
        titles = {t["title"] for t in res["tabs"]}
        self.assertEqual(
            titles,
            {"Low Stock Alerts", "Out of Stock", "Near Expiry Alerts",
             "Expired Medicines", "Customer Due Alerts"},
        )

    def test_online_and_offline_popups_agree(self):
        online = sad.collect_startup_alerts(self.conn)
        with mock.patch.object(sync_prefs, "is_online_mode", lambda *a, **k: False):
            offline = sad.collect_startup_alerts(_offline_store())
        self.assertEqual(
            {t["title"]: sorted(map(tuple, t["rows"])) for t in online},
            {t["title"]: sorted(map(tuple, t["rows"])) for t in offline},
        )

    def test_online_rows(self):
        tabs = sad.collect_startup_alerts(self.conn)
        low = _tab(tabs, "Low Stock Alerts")
        self.assertEqual([r[2] for r in low["rows"]], ["A1"])  # hidden A2 excluded
        self.assertEqual([r[0] for r in _tab(tabs, "Expired Medicines")["rows"]], ["BRAVO"])
        self.assertEqual([r[0] for r in _tab(tabs, "Out of Stock")["rows"]], ["DELTA"])
        self.assertEqual([r[0] for r in _tab(tabs, "Customer Due Alerts")["rows"]], ["RAMESH"])


class OnlineReadFailureIsNotSilence(_Online):
    meds = []
    medicines_error = "store server unreachable"

    def test_failed_store_read_is_reported(self):
        res = dss.get_startup_alerts(self.conn)
        self.assertFalse(res["ok"])
        self.assertIn("unreachable", res.get("error", ""))


class OfflineCorrectness(_PrefsIsolated):
    def setUp(self):
        super().setUp()
        self.conn = _offline_store()

    def test_customer_due_excludes_zero_balances(self):
        rows = ams.fetch_customer_due_summary(self.conn)
        self.assertEqual([r[0] for r in rows], ["RAMESH"])

    def test_low_stock_popup_skips_hidden_batches(self):
        low = _tab(sad.collect_startup_alerts(self.conn), "Low Stock Alerts")
        self.assertEqual([(r[0], r[2]) for r in low["rows"]], [("ALPHA", "A1")])

    def test_month_only_expiry_is_good_until_month_end(self):
        from core.batch_visibility import is_expired_as_of

        raw = TODAY.replace(day=1).isoformat()
        self.assertFalse(is_expired_as_of(raw, TODAY))  # billing sells it
        sections = ams.fetch_all_monitoring_sections(self.conn)
        self.assertNotIn("ECHO", [r[0] for r in sections["expired"]])
        self.assertIn("ECHO", [r[0] for r in sections["near_expiry"]])
        tabs = sad.collect_startup_alerts(self.conn)
        self.assertNotIn("ECHO", [r[0] for r in _tab(tabs, "Expired Medicines")["rows"]])
        self.assertIn("ECHO", [r[0] for r in _tab(tabs, "Near Expiry Alerts")["rows"]])

    def test_inventory_agrees_on_month_only_expiry(self):
        from core import desktop_pages_service as dps

        raw = TODAY.replace(day=1).isoformat()
        self.assertGreaterEqual(dps._days_left(raw), 0)
        self.assertEqual(
            dps._inventory_row_status(50, raw, "ECHO", "Tablet", "10", self.conn),
            "near_expiry",
        )

    def test_near_expiry_uses_type_alias(self):
        near = [r[0] for r in ams.fetch_near_expiry_medicines(self.conn)]
        self.assertNotIn("FOXTROT", near)  # Drops = 1 month, 60 days out
        self.assertIn("CHARLIE", near)


class PopupPrefsAreReal(_PrefsIsolated):
    def setUp(self):
        super().setUp()
        self.conn = _offline_store()

    def test_category_switch_removes_tab(self):
        prefs.save_alert_popup_prefs({"categories": {"out_of_stock": False}})
        res = dss.get_startup_alerts(self.conn)
        self.assertNotIn("Out of Stock", [t["title"] for t in res["tabs"]])

    def test_skip_today_and_test_now(self):
        prefs.snooze_startup_alerts_for_today()
        self.assertFalse(dss.get_startup_alerts(self.conn)["show"])
        self.assertTrue(dss.get_startup_alerts(self.conn, force=True)["show"])
        prefs.save_alert_popup_prefs({"clear_snooze": True})
        self.assertTrue(dss.get_startup_alerts(self.conn)["show"])

    def test_recheck_only_shows_what_is_new(self):
        self.assertTrue(dss.get_startup_alerts(self.conn)["show"])
        self.assertFalse(dss.get_startup_alerts(self.conn, recheck=True)["show"])
        _ins(self.conn, "medicines", id=99, name="GOLF", type="Tablet", batch_no="G1",
             expiry_date=_full_date(400), stock_qty=2, unit="10", is_hidden=0)
        self.conn.commit()
        res = dss.get_startup_alerts(self.conn, recheck=True)
        self.assertTrue(res["show"])
        self.assertEqual([[r[0] for r in t["rows"]] for t in res["tabs"]], [["GOLF"]])

    def test_recheck_off_by_interval_zero(self):
        prefs.save_alert_popup_prefs({"recheck_minutes": 0})
        self.assertEqual(prefs.load_alert_popup_prefs()["recheck_minutes"], 0)
        self.assertFalse(dss.get_startup_alerts(self.conn, recheck=True)["show"])


if __name__ == "__main__":
    unittest.main()
