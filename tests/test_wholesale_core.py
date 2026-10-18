"""Wholesale mode, the core part (D:\\projects\\WHOLESALE_DESIGN.md).

* the server's word about each store (licence / pairing answer -> wholesale_stores.dat, per
  store key): store_kind, wholesale_store / retail_store; missing = retail, no companion;
* the mode is the OPEN store's kind (wholesale is a separate store); the old business_mode
  row is not read; the rate margins travel like the other shop-wide settings;
* a customer as a wholesale party (party type, GSTIN, DL, state, tier, credit terms);
* a wholesale purchase's Rate A / B / C on its lines and on the medicine batch;
* a payment's paper receipt number, both modes: history, View box, ledger, report;
* every new column survives a push document -> pull on another device.

Everything runs on throw-away SQLite files with the sync mode pinned to Offline and AppData
pointed at a temp folder. Nothing reaches a server.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup  # noqa: E402
from core import desktop_settings_service as settings  # noqa: E402
from core import license_manager as lm  # noqa: E402
from core import wholesale_mode as wm  # noqa: E402

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin(first14: str) -> str:
    """first14 + the check character, so customer_gst accepts it."""
    total = 0
    for i, ch in enumerate(first14):
        v = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return first14 + _CHARS[(36 - total % 36) % 36]


GSTIN_MH = gstin("27ABCDE1234F1Z")
GSTIN_GJ = gstin("24ABCDE1234F1Z")


def _store() -> sqlite3.Connection:
    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"), check_same_thread=False)
    db_setup.initialise(conn)
    return conn


class _Base(unittest.TestCase):
    """Offline mode, AppData in a temp folder, the server's answer set by the test."""

    allowed = True

    def setUp(self):
        self.appdata = tempfile.mkdtemp()
        for p in (
            mock.patch("core.sync_prefs.get_sync_mode", lambda: "offline"),
            mock.patch("core.license_manager._appdata_dir", lambda: self.appdata),
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.server_crud.get_doc", return_value=None),
            mock.patch.dict(os.environ, {wm.OVERRIDE_ENV: "1" if self.allowed else "0"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.conn = _store()
        self.addCleanup(self.conn.close)


# ── 1. the server's word: kept per store ─────────────────────────────────────

COMPANION = {"store_id": "9_wholesale", "store_key": "TEST_STORE_Wholesale",
             "store_name": "TEST STORE WHOLESALE", "android_key": "SC-W0000001"}
RETAIL_LINK = {"store_id": "9", "store_key": "TEST_STORE", "store_name": "TEST STORE",
               "android_key": "SC-R0000001"}


class _Stores(unittest.TestCase):
    """AppData in a temp folder; the open store is whatever ``self.active['key']`` says."""

    def setUp(self):
        self.appdata = tempfile.mkdtemp()
        self.active = {"key": "Store_Retail"}
        for p in (
            mock.patch("core.license_manager._appdata_dir", lambda: self.appdata),
            mock.patch("core.sync_prefs.get_sync_mode", lambda: "offline"),
            mock.patch("core.store_manager.get_active_store_key", lambda: self.active["key"]),
            mock.patch.dict(os.environ, {wm.OVERRIDE_ENV: ""}),
        ):
            p.start()
            self.addCleanup(p.stop)
        lm._WHOLESALE_LIVE.clear()
        self.addCleanup(lm._WHOLESALE_LIVE.clear)


class TheServerSwitch(_Stores):
    """What the licence / pairing answer says about wholesale is kept per local store key."""

    def test_missing_is_off(self):
        self.assertFalse(lm.wholesale_from_license({}))
        self.assertFalse(lm.wholesale_from_license({"voice_enabled": True}))
        self.assertFalse(lm.wholesale_from_license({"wholesale_enabled": False}))
        self.assertFalse(lm.wholesale_from_license({"wholesale_enabled": "no"}))
        self.assertTrue(lm.wholesale_from_license({"wholesale_enabled": True}))
        self.assertTrue(lm.wholesale_from_license({"store": {"wholesale_enabled": True}}))
        st = wm.allowed_status(refresh=False)
        self.assertEqual(st, {"allowed": False, "source": "default"})
        self.assertEqual(wm.current_mode(), "retail")

    def test_the_licence_answer_is_kept_on_disk(self):
        lm._cache_server_license_locally({"wholesale_enabled": True, "store_kind": "retail",
                                          "wholesale_store": COMPANION})
        self.assertTrue(lm.read_cached_wholesale())
        self.assertEqual(wm.allowed_status(refresh=False), {"allowed": True, "source": "server"})
        lm._WHOLESALE_LIVE.clear()  # a new process, offline
        self.assertEqual(wm.allowed_status(refresh=False), {"allowed": True, "source": "cache"})
        self.assertEqual(wm.store_info()["wholesale_store"], COMPANION)
        # an answer that does not speak about wholesale changes nothing
        lm._cache_server_license_locally({"expiry_enabled": True})
        self.assertTrue(lm.read_cached_wholesale())
        # wholesale switched off: the server stops offering the companion
        lm._cache_server_license_locally({"wholesale_enabled": False})
        self.assertFalse(lm.read_cached_wholesale())
        self.assertFalse(wm.is_allowed(refresh=False))
        self.assertIsNone(wm.store_info()["wholesale_store"])

    def test_a_pairing_answer_that_carries_it_is_kept_for_that_store(self):
        from core import server_api as api

        with mock.patch.object(api, "pair_store", return_value={
                "token": "t", "store": {"store_id": 9, "store_name": "TEST STORE WHOLESALE",
                                        "wholesale_enabled": True, "store_kind": "wholesale",
                                        "retail_store": RETAIL_LINK}}), \
                mock.patch.object(api, "load_session", return_value={}), \
                mock.patch.object(api, "save_session"), \
                mock.patch("core.store_manager.adopt_server_display_name"):
            api.ensure_store_session(store_key="Store_Wholesale", android_key="SC-W", store_name="X",
                                     device_id="dev", force_pair=True)
        # kept for the store that paired -- not for the open one
        self.assertIsNone(lm.read_cached_wholesale_info("Store_Retail"))
        info = lm.read_cached_wholesale_info("Store_Wholesale")
        self.assertEqual((info["store_kind"], info["retail_store"]), ("wholesale", RETAIL_LINK))
        self.assertEqual(wm.current_mode(), "retail")
        self.active["key"] = "Store_Wholesale"
        self.assertEqual(wm.current_mode(), "wholesale")

    def test_two_stores_keep_their_own_answer(self):
        lm.remember_server_wholesale({"wholesale_enabled": True, "store_kind": "retail",
                                      "wholesale_store": COMPANION}, store_key="Store_Retail")
        lm.remember_server_wholesale({"wholesale_enabled": True, "store_kind": "wholesale",
                                      "retail_store": RETAIL_LINK}, store_key="Store_Wholesale")
        lm._WHOLESALE_LIVE.clear()
        self.assertEqual(lm.read_cached_wholesale_info("Store_Retail")["wholesale_store"], COMPANION)
        self.assertEqual(lm.read_cached_wholesale_info("Store_Wholesale")["retail_store"], RETAIL_LINK)

    def test_missing_fields_mean_retail_with_no_companion(self):
        info = lm.wholesale_info_from_license({"wholesale_enabled": True})
        self.assertEqual(info, {"enabled": True, "store_kind": "retail", "wholesale_store": None,
                                "retail_store": None})
        # a companion this PC could not open (no SC- key) is no companion
        info = lm.wholesale_info_from_license({"wholesale_store": {"store_name": "X"}})
        self.assertIsNone(info["wholesale_store"])
        # a retail store never links "back"; a wholesale store never offers a wholesale store
        info = lm.wholesale_info_from_license({"store_kind": "retail", "retail_store": RETAIL_LINK})
        self.assertIsNone(info["retail_store"])
        info = lm.wholesale_info_from_license({"store_kind": "WHOLESALE", "wholesale_store": COMPANION})
        self.assertEqual((info["store_kind"], info["wholesale_store"]), ("wholesale", None))
        self.assertTrue(lm.speaks_about_wholesale({"store": {"store_kind": "retail"}}))
        self.assertFalse(lm.speaks_about_wholesale({"expiry_enabled": True}))


# ── 2. the mode is the open store's kind ─────────────────────────────────────

class TheMode(_Stores):
    def test_mode_and_state_follow_the_open_store(self):
        from core import wholesale_api

        lm.remember_server_wholesale({"wholesale_enabled": True, "store_kind": "retail",
                                      "wholesale_store": COMPANION}, store_key="Store_Retail")
        lm.remember_server_wholesale({"wholesale_enabled": True, "store_kind": "wholesale",
                                      "retail_store": RETAIL_LINK}, store_key="Store_Wholesale")
        with mock.patch.object(wm, "_store_name", return_value="TEST STORE"):
            st = wm.state(None)
            self.assertEqual((st["allowed"], st["mode"], st["companion_name"], st["store_name"]),
                             (True, "retail", "TEST STORE WHOLESALE", "TEST STORE"))
            self.active["key"] = "Store_Wholesale"
            code, st = wholesale_api.handle_get("/api/wholesale/state", None)
            self.assertEqual((code, st["allowed"], st["mode"], st["companion_name"]),
                             (200, True, "wholesale", "TEST STORE"))
        self.assertTrue(wm.is_wholesale())
        from core.wholesale_schema import get_business_mode

        self.assertEqual(get_business_mode(None), "wholesale")

    def test_the_old_business_mode_row_is_not_read(self):
        conn = _store()
        conn.execute("INSERT INTO settings (name, value) VALUES ('business_mode', 'wholesale')")
        from core.wholesale_schema import get_business_mode

        self.assertEqual(get_business_mode(conn), "retail")
        self.assertFalse(wm.is_wholesale(conn))

    def test_the_override_marks_the_open_store(self):
        with mock.patch.dict(os.environ, {wm.OVERRIDE_ENV: "1"}):
            self.assertEqual(wm.current_mode(), "wholesale")
            self.assertTrue(wm.state(None)["allowed"])
        with mock.patch.dict(os.environ, {wm.OVERRIDE_ENV: "0"}):
            lm.remember_server_wholesale({"store_kind": "wholesale"}, store_key="Store_Retail")
            self.assertEqual(wm.current_mode(), "retail")
            self.assertFalse(wm.state(None)["allowed"])

    def test_no_companion_is_refused_clearly(self):
        from core import wholesale_api

        code, body = wholesale_api.handle_post("/api/wholesale/mode", {"mode": "wholesale"}, None)
        self.assertEqual((code, body["code"]), (403, "no_companion"))
        self.assertIn("no wholesale store", body["error"])
        self.assertEqual(wholesale_api.handle_post("/api/wholesale/mode", {"mode": "x"}, None)[0], 400)


class TheMargins(_Base):
    def test_margins(self):
        self.assertEqual(wm.margins(self.conn), {"A": 8.0, "B": 10.0, "C": 12.0})
        wm.set_margins(self.conn, {"A": 5, "C": "15"})
        self.assertEqual(wm.margins(self.conn), {"A": 5.0, "B": 10.0, "C": 15.0})
        self.assertEqual(wm.suggested_rates(100, wm.margins(self.conn)), {"A": 105.0, "B": 110.0, "C": 115.0})
        with self.assertRaises(ValueError):
            wm.set_margins(self.conn, {"B": -1})

    def test_the_margins_go_up_and_come_down_like_other_shop_settings(self):
        from core import server_live, store_kv_carry
        from core.offline_first import outbox, schema

        schema.ensure_schema(self.conn)
        wm.set_margins(self.conn, {"B": 11})
        noted = self.conn.execute("SELECT local_id FROM of_dirty WHERE collection='settings'").fetchall()
        docs = [outbox.build_doc(self.conn, "settings", i) for (i,) in noted]
        self.assertIn({"name": "ws_margin_b", "value": "11"}, docs)
        # a PC-only row is still not sent
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('startup_migration_x', '1')")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)
        # the other device of the same store
        other = _store()
        self.assertEqual(server_live.apply_server_doc(other, "settings", {"name": "ws_margin_b",
                                                                          "value": "11"}), "applied")
        self.assertEqual(wm.margins(other)["B"], 11.0)
        self.assertTrue(store_kv_carry._carried("ws_margin_a"))

    def test_an_old_medicine_trigger_is_made_again_with_the_new_columns(self):
        from core.offline_first import schema

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT, stock_qty INTEGER)")
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO medicines (id, name, stock_qty) VALUES (1, 'A', 0)")
        conn.execute("DELETE FROM of_dirty")
        conn.execute("ALTER TABLE medicines ADD COLUMN later_col REAL")  # a column a newer build adds
        conn.execute("UPDATE medicines SET later_col=5 WHERE id=1")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)  # the old trigger
        schema.ensure_schema(conn)
        conn.execute("UPDATE medicines SET later_col=6 WHERE id=1")
        self.assertEqual(conn.execute("SELECT collection FROM of_dirty").fetchall(), [("medicines",)])


# ── 3. the customer as a party ────────────────────────────────────────────────

class TheParty(_Base):
    def add(self, **fields):
        body = {"kind": "customer", "action": "add", "name": "TEST MEDICAL", "phone": "", "address": "", **fields}
        return settings.mutate_contact(self.conn, body)

    def test_add_with_party_fields_and_list(self):
        out = self.add(party_type="medical", gstin=GSTIN_GJ, dl_numbers="20B-1, 21B-2",
                       rate_tier="b", credit_limit="50000", credit_days="30", state_code="27")
        c = [x for x in out["customers"] if x["name"] == "TEST MEDICAL"][0]
        self.assertEqual((c["party_type"], c["rate_tier"], c["credit_limit"], c["credit_days"]),
                         ("medical", "B", 50000.0, 30))
        self.assertEqual((c["gstin"], c["state_code"], c["state_name"]), (GSTIN_GJ, "24", "Gujarat"))
        self.assertEqual(c["dl_numbers"], "20B-1, 21B-2")
        from core import customer_gst

        self.assertEqual(customer_gst.get_customer_gst(self.conn, c["id"])["gstin"], GSTIN_GJ)
        # the retail form (no party fields) keeps them
        settings.mutate_contact(self.conn, {"kind": "customer", "action": "update", "id": c["id"],
                                            "name": "TEST MEDICAL", "phone": "99", "address": ""})
        row = self.conn.execute("SELECT party_type, rate_tier, credit_days FROM customers WHERE id=?",
                                (c["id"],)).fetchone()
        self.assertEqual(row, ("medical", "B", 30))

    def test_state_without_gstin_and_bad_values(self):
        out = self.add(party_type="medical", state_code="7", gstin="")
        c = [x for x in out["customers"] if x["name"] == "TEST MEDICAL"][0]
        self.assertEqual((c["state_code"], c["state_name"]), ("07", "Delhi"))
        for bad in ({"rate_tier": "D"}, {"party_type": "x"}, {"credit_limit": "-1"}, {"gstin": "27ABC"},
                    {"state_code": "99"}):
            with self.assertRaises(ValueError):
                self.add(name="TEST OTHER", **bad)
        self.assertFalse(self.conn.execute("SELECT 1 FROM customers WHERE name='TEST OTHER'").fetchone())


# ── 4. the purchase's Rate A / B / C ──────────────────────────────────────────

class ThePurchaseRates(_Base):
    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'TEST AGENCY')")
        self.conn.commit()

    def save(self, items, **body):
        from core import desktop_purchase_service as svc

        payload = {"supplier_name": "TEST AGENCY", "bill_number": body.pop("bill", "W-1"),
                   "purchase_date": "2026-10-18", "items": items, "cash_paid": 0, **body}
        return svc.save_purchase_bill(self.conn, payload)

    def line(self, name="TEST TAB", **kw):
        item = {"name": name, "type": "Tablet", "batch": "B1", "expiry": "12/28", "qty": 2,
                "rate": 100.0, "mrp": 0, "unit": "10", "gst_pct": 12}
        item.update(kw)
        return item

    def test_rates_on_the_line_and_the_batch(self):
        from core import desktop_purchase_service as svc
        from core.wholesale_rates import last_rates

        res = self.save([self.line(rate_a=108, rate_b=110, rate_c=112.5), self.line("TEST SYRUP", batch="S1")])
        self.assertTrue(res["ok"], res)
        pid = res["purchase_id"]
        rows = self.conn.execute(
            "SELECT m.name, pi.rate_a, pi.rate_b, pi.rate_c, m.purchase_rate, m.rate_a, m.rate_c "
            "FROM purchase_items pi JOIN medicines m ON m.id = pi.medicine_id WHERE pi.purchase_id=? ORDER BY pi.id",
            (pid,)).fetchall()
        self.assertEqual(rows[0], ("TEST TAB", 108.0, 110.0, 112.5, 100.0, 108.0, 112.5))
        self.assertEqual(rows[1][1:4], (None, None, None))  # a line without rates: retail as before
        loaded = svc.load_purchase(self.conn, pid)
        self.assertEqual([(i["rate_a"], i["rate_b"], i["rate_c"]) for i in loaded["form"]["items"]],
                         [(108.0, 110.0, 112.5), (None, None, None)])
        got = last_rates(self.conn, name="test tab")
        self.assertEqual((got["found"], got["rate_b"], got["purchase_rate"]), (True, 110.0, 100.0))
        self.assertFalse(last_rates(self.conn, name="TEST SYRUP")["found"])

    def test_autosave_keeps_the_rates_on_the_lines_only(self):
        from core import desktop_purchase_service as svc

        with mock.patch("core.autosave_prefs.load_autosave_enabled", return_value=True):
            res = svc.autosave_purchase(self.conn, {"supplier_name": "TEST AGENCY", "bill_number": "W-2",
                                                    "purchase_date": "2026-10-18",
                                                    "items": [self.line(rate_a=120)]})
        aid = res["autosave_purchase_id"]
        self.assertGreater(aid, 0)
        self.assertEqual(self.conn.execute("SELECT rate_a FROM purchase_items WHERE purchase_id=?",
                                           (aid,)).fetchone()[0], 120.0)


# ── 5. the receipt number ─────────────────────────────────────────────────────

class TheReceiptNumber(_Base):
    def setUp(self):
        super().setUp()
        c = self.conn
        self.cid = c.execute("INSERT INTO customers (name) VALUES ('TEST CUSTOMER')").lastrowid
        self.sid = c.execute("INSERT INTO suppliers (name) VALUES ('TEST SUPPLIER')").lastrowid
        c.execute("INSERT INTO sales (bill_no, bill_date, customer_id, total_amount, amount_paid, due_amount, "
                  "total_due) VALUES ('T1', '2026-10-01', ?, 500, 0, 500, 500)", (self.cid,))
        c.commit()

    def test_customer_and_supplier_receipts(self):
        from core.desktop_export_service import payment_report
        from core.payment_details import payment_details

        out = settings.save_payment(self.conn, {"kind": "customer", "party": "TEST CUSTOMER", "cash": 100,
                                                "online": 0, "date": "2026-10-10", "receipt_no": " R-55 "})
        pid = out["payment_id"]
        self.assertEqual(out["history"][0]["receipt_no"], "R-55")
        self.assertEqual(payment_details(self.conn, "customer", pid)["payment"]["receipt_no"], "R-55")
        led = settings.get_ledger(self.conn, "customer", "TEST CUSTOMER", "2026-04-01", "2026-10-31")
        pay = [r for r in led["rows"] if r.get("ref_kind") == "payment"][0]
        self.assertEqual(pay["receipt_no"], "R-55")
        self.assertIn("Rcpt R-55", pay["particulars"])
        rep = payment_report(self.conn, "customer")
        self.assertEqual(rep["payments"][0]["receipt_no"], "R-55")
        self.assertIn("Rcpt R-55", rep["payments"][0]["number"])
        # an edit that does not mention it (voice, an older screen) keeps it
        settings.save_payment(self.conn, {"kind": "customer", "party": "TEST CUSTOMER", "cash": 90,
                                          "online": 0, "date": "2026-10-10", "id": pid})
        self.assertEqual(self.conn.execute("SELECT amount, receipt_no FROM customer_payments WHERE id=?",
                                           (pid,)).fetchone(), (90.0, "R-55"))
        settings.save_payment(self.conn, {"kind": "customer", "party": "TEST CUSTOMER", "cash": 90,
                                          "online": 0, "date": "2026-10-10", "id": pid, "receipt_no": ""})
        self.assertIsNone(self.conn.execute("SELECT receipt_no FROM customer_payments WHERE id=?",
                                            (pid,)).fetchone()[0])

        sp = settings.save_payment(self.conn, {"kind": "supplier", "party": "TEST SUPPLIER", "amount": 40,
                                               "mode": "Cash", "receipt_no": "S-9"})
        self.assertEqual(sp["history"][0]["receipt_no"], "S-9")
        self.assertEqual(payment_details(self.conn, "supplier", sp["payment_id"])["payment"]["receipt_no"], "S-9")


# ── 6. every new column, push -> pull ─────────────────────────────────────────

class TheRoundTrip(_Base):
    def test_push_documents_carry_and_pull_applies_every_new_column(self):
        from core import server_entity_sync as fb
        from core import server_sync
        from core.offline_first import outbox

        c = self.conn
        cid = c.execute(
            "INSERT INTO customers (name, party_type, dl_numbers, state_code, rate_tier, credit_limit, credit_days) "
            "VALUES ('TEST MEDICAL', 'medical', '20B-7', '24', 'C', 25000, 45)").lastrowid
        sid = c.execute("INSERT INTO suppliers (name) VALUES ('TEST SUPPLIER')").lastrowid
        mid = c.execute("INSERT INTO medicines (name, batch_no, stock_qty, purchase_rate, rate_a, rate_b, rate_c) "
                        "VALUES ('TEST TAB', 'B1', 0, 90, 97.2, 99, 100.8)").lastrowid
        pid = c.execute("INSERT INTO purchases (purchase_no, purchase_date, supplier_id, total_amount, final_amount) "
                        "VALUES ('1', '2026-10-18', ?, 180, 180)", (sid,)).lastrowid
        c.execute("INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, batch_no, rate_a, rate_b, rate_c) "
                  "VALUES (?, ?, 2, 90, 'B1', 97.2, 99, 100.8)", (pid, mid))
        cp = c.execute("INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, receipt_no) "
                       "VALUES (?, '2026-10-18', 50, 'cash', 'R-1')", (cid,)).lastrowid
        sp = c.execute("INSERT INTO supplier_payments (supplier_id, payment_date, amount, mode, receipt_no) "
                       "VALUES (?, '2026-10-18', 70, 'Cash', 'S-1')", (sid,)).lastrowid
        c.commit()

        docs = {col: server_sync._build_docs(c, col, ids=[i])[0] for col, i in (
            ("customers", cid), ("medicines", mid), ("purchases", pid),
            ("customer_payments", cp), ("supplier_payments", sp))}
        self.assertEqual({k: docs["customers"][k] for k in ("party_type", "dl_numbers", "state_code", "rate_tier",
                                                            "credit_limit", "credit_days")},
                         {"party_type": "medical", "dl_numbers": "20B-7", "state_code": "24", "rate_tier": "C",
                          "credit_limit": 25000.0, "credit_days": 45})
        self.assertEqual([docs["medicines"][k] for k in ("purchase_rate", "rate_a", "rate_b", "rate_c")],
                         [90.0, 97.2, 99.0, 100.8])
        self.assertEqual([docs["purchases"]["items"][0][k] for k in ("rate_a", "rate_b", "rate_c")],
                         [97.2, 99.0, 100.8])
        self.assertEqual(docs["customer_payments"]["receipt_no"], "R-1")
        self.assertEqual(docs["supplier_payments"]["receipt_no"], "S-1")
        # the one-by-one builders (Online pushes, offline-first events) say the same
        self.assertEqual(fb.build_customer_payload(c, cid)["rate_tier"], "C")
        self.assertEqual(fb.build_medicine_payload(c, mid)["rate_c"], 100.8)
        self.assertEqual(fb.build_purchase_payload(c, pid)["items"][0]["rate_b"], 99.0)
        self.assertEqual(fb.build_customer_payment_payload(c, cp)["receipt_no"], "R-1")
        self.assertEqual(fb.build_supplier_payment_payload(c, sp)["receipt_no"], "S-1")
        self.assertEqual(outbox.build_doc(c, "customers", cid)["credit_days"], 45)

        other = _store()
        with fb.offline_first_apply():
            for col, i in (("customers", cid), ("suppliers", sid), ("medicines", mid), ("purchases", pid),
                           ("customer_payments", cp), ("supplier_payments", sp)):
                doc = docs.get(col) or server_sync._build_docs(c, col, ids=[i])[0]
                self.assertEqual(fb.sync_down_doc(other, col, str(i), json.loads(json.dumps(doc))), "applied", col)
        q = other.execute
        self.assertEqual(q("SELECT party_type, dl_numbers, state_code, rate_tier, credit_limit, credit_days "
                           "FROM customers WHERE id=?", (cid,)).fetchone(),
                         ("medical", "20B-7", "24", "C", 25000.0, 45))
        self.assertEqual(q("SELECT purchase_rate, rate_a, rate_b, rate_c FROM medicines WHERE id=?", (mid,)).fetchone(),
                         (90.0, 97.2, 99.0, 100.8))
        self.assertEqual(q("SELECT rate_a, rate_b, rate_c FROM purchase_items WHERE purchase_id=?", (pid,)).fetchone(),
                         (97.2, 99.0, 100.8))
        self.assertEqual(q("SELECT receipt_no FROM customer_payments WHERE id=?", (cp,)).fetchone()[0], "R-1")
        self.assertEqual(q("SELECT receipt_no FROM supplier_payments WHERE id=?", (sp,)).fetchone()[0], "S-1")

        # a document from a device that never heard of these fields keeps them; null clears
        old = {k: v for k, v in docs["customers"].items() if k not in ("party_type", "rate_tier", "credit_days")}
        old["version"] = int(old.get("version") or 1) + 5
        old["phone"] = "123"
        pay = {k: v for k, v in docs["customer_payments"].items() if k != "receipt_no"}
        pay["version"] = int(pay.get("version") or 1) + 5
        with fb.offline_first_apply():
            fb.sync_down_doc(other, "customers", str(cid), old)
            fb.sync_down_doc(other, "customer_payments", str(cp), pay)
        self.assertEqual(q("SELECT phone, party_type, rate_tier, credit_days FROM customers WHERE id=?",
                           (cid,)).fetchone(), ("123", "medical", "C", 45))
        self.assertEqual(q("SELECT receipt_no FROM customer_payments WHERE id=?", (cp,)).fetchone()[0], "R-1")
        cleared = dict(pay, receipt_no=None, version=pay["version"] + 1)
        with fb.offline_first_apply():
            fb.sync_down_doc(other, "customer_payments", str(cp), cleared)
        self.assertIsNone(q("SELECT receipt_no FROM customer_payments WHERE id=?", (cp,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
