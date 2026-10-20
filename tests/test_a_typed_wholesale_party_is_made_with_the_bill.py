"""A wholesale bill takes a typed party name, as a retail bill takes a new customer.

Owner, 20 Oct 2026: "if all the boxes are already in the sales input for wholesale too then the
party can be created same as the retail saves the customer". So a bill whose Party box was typed
(not picked) is saved: a name already saved is that party -- never a second one of the same
name -- and a new name becomes a party made from the bill's own boxes (phone, address, GSTIN,
DL numbers; a medical at Rate A, no credit terms, the state from the GSTIN or none, the GSTIN
kept where the PC keeps one, from the bill's date) -- the same party the phone app and the web
make. A bill refused for anything else makes no party, and no name at all still refuses.

The Sales page sends the typed name (customer_name) and asks for no picked party before saving.
Everything runs on in-memory or temporary stores; no server is touched.
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

from core import desktop_sales_service as sales_svc  # noqa: E402
from core import wholesale_sale as ws  # noqa: E402
from tests.test_wholesale_tax_invoice import _offline, _store, gstin  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = "2026-10-20"
NEW_GSTIN = gstin("24", "CCCPC3333C")  # Gujarat: another state


def _line(qty=2):
    return {"id": 1, "qty": qty, "free": 0, "rate": 90, "disc_pct": 0, "scheme_pct": 0,
            "gst_percent": 12, "rate_tier": "A"}


def _body(name, **over):
    body = {"invoice_mode": "wholesale", "customer_id": 0, "customer_name": name,
            "bill_date": DAY, "items": [_line()], "payment_mode": "Due",
            "customer_phone": "9222222222", "customer_address": "Station Road, Newtown",
            "buyer_gstin": NEW_GSTIN, "buyer_dl": "20B-77, 21B-78"}
    body.update(over)
    return body


def _named(conn, name):
    return conn.execute(
        "SELECT id FROM customers WHERE UPPER(TRIM(name))=?", (name.upper(),)).fetchall()


class _Store(unittest.TestCase):
    def setUp(self):
        for p in _offline():
            p.start()
            self.addCleanup(p.stop)
        self.conn = _store()
        self.addCleanup(self.conn.close)

    def save(self, body):
        return sales_svc.save_sale(self.conn, body)


class ANewName(_Store):
    def test_becomes_a_party_from_the_bills_boxes(self):
        res = self.save(_body("  new   medical  agency "))
        self.assertTrue(res["ok"], res)
        rows = _named(self.conn, "NEW MEDICAL AGENCY")
        self.assertEqual(len(rows), 1)
        cid = rows[0][0]
        self.assertEqual(res["customer_id"], cid)
        p = ws.party(self.conn, cid)
        self.assertEqual(p["name"], "NEW MEDICAL AGENCY")
        self.assertEqual(p["phone"], "9222222222")
        self.assertEqual(p["address"], "Station Road, Newtown")
        self.assertEqual(p["gstin"], NEW_GSTIN)
        self.assertEqual(p["dl_numbers"], "20B-77, 21B-78")
        self.assertEqual(p["state_code"], "24")
        self.assertEqual(p["rate_tier"], "A")
        self.assertEqual(p["party_type"], "medical")
        self.assertEqual((p["credit_limit"], p["credit_days"]), (0.0, 0))
        kept = json.loads(self.conn.execute(
            "SELECT value FROM settings WHERE name=?", (f"customer_gst:{cid}",)).fetchone()[0])
        self.assertEqual((kept["gstin"], kept["legal_name"], kept["state"], kept["since"]),
                         (NEW_GSTIN, "", "24", DAY))
        sale = self.conn.execute(
            "SELECT customer_id, buyer_gstin, place_of_supply FROM sales WHERE id=?",
            (res["sale_id"],)).fetchone()
        self.assertEqual(sale, (cid, NEW_GSTIN, "24"))

    def test_typed_again_is_the_same_party(self):
        first = self.save(_body("NEW MEDICAL AGENCY"))
        self.assertTrue(first["ok"], first)
        again = self.save(_body("new medical agency", customer_phone="", buyer_gstin="",
                                buyer_dl=""))
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["customer_id"], first["customer_id"])
        self.assertEqual(len(_named(self.conn, "NEW MEDICAL AGENCY")), 1)
        # an empty box on the second bill never wipes what the party has
        self.assertEqual(ws.party(self.conn, first["customer_id"])["gstin"], NEW_GSTIN)

    def test_a_back_dated_bill_has_the_gstin_from_its_own_date(self):
        res = self.save(_body("OLD DATE MEDICAL", bill_date="2026-10-15"))
        self.assertTrue(res["ok"], res)
        kept = json.loads(self.conn.execute(
            "SELECT value FROM settings WHERE name=?",
            (f"customer_gst:{res['customer_id']}",)).fetchone()[0])
        self.assertEqual(kept["since"], "2026-10-15")

    def test_without_a_gstin_or_dl_is_still_a_party(self):
        res = self.save(_body("SMALL CHEMIST", buyer_gstin="", buyer_dl="", customer_phone=""))
        self.assertTrue(res["ok"], res)
        p = ws.party(self.conn, res["customer_id"])
        self.assertEqual((p["gstin"], p["dl_numbers"], p["rate_tier"]), ("", "", "A"))
        # no GSTIN, no state: the bill falls back to the shop's own
        self.assertEqual(self.conn.execute(
            "SELECT COALESCE(state_code,'') FROM customers WHERE id=?",
            (res["customer_id"],)).fetchone()[0], "")
        self.assertIsNone(self.conn.execute(
            "SELECT value FROM settings WHERE name=?",
            (f"customer_gst:{res['customer_id']}",)).fetchone())


class AnExistingName(_Store):
    def test_typed_not_picked_is_that_party(self):
        before = self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        res = self.save(_body("test medical one", buyer_gstin="", buyer_dl=""))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["customer_id"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], before)
        # its own terms stay: Rate B, its credit days
        p = ws.party(self.conn, 1)
        self.assertEqual((p["rate_tier"], p["credit_days"]), ("B", 30))


class NoParty(_Store):
    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]

    def test_no_name_still_refuses(self):
        before = self.count()
        res = self.save(_body("   "))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "Type the party name.")
        self.assertEqual(self.count(), before)

    def test_counter_sale_is_not_a_party(self):
        res = self.save(_body("Counter Sale"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "party_required")

    def test_a_wrong_gstin_refuses_and_makes_nothing(self):
        before = self.count()
        bad = NEW_GSTIN[:14] + ("A" if NEW_GSTIN[14] != "A" else "B")  # check character wrong
        res = self.save(_body("BAD GST MEDICAL", buyer_gstin=bad))
        self.assertFalse(res["ok"])
        self.assertEqual(self.count(), before)

    def test_a_refused_bill_makes_no_party(self):
        before = self.count()
        res = self.save(_body("NO STOCK MEDICAL", items=[_line(qty=999)]))
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "insufficient_stock")
        self.assertEqual(self.count(), before)


class OfflineFirst(unittest.TestCase):
    """The same save on an offline-first PC: the party gets this device's id and goes up."""

    def setUp(self):
        from core.offline_first import ids, schema
        from core.offline_first.schema import meta_set

        fd, self.path = tempfile.mkstemp(suffix=".db", prefix="ws_party_")
        os.close(fd)
        self.addCleanup(self._cleanup)
        seed = _store()
        try:
            disk = sqlite3.connect(self.path)
            seed.backup(disk)
            disk.close()
        finally:
            seed.close()
        conn = sqlite3.connect(self.path)
        schema.ensure_schema(conn)
        meta_set(conn, "device_no", 3)
        meta_set(conn, "id_base", 3_000_000_000)
        meta_set(conn, "id_max", 3_999_999_999)
        meta_set(conn, "install_id", "pc-test-install")
        conn.commit()
        conn.close()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, factory=ids.OfConnection)
        patches = [
            mock.patch("core.sync_prefs.get_sync_mode", return_value="offline_first"),
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch.dict(os.environ, {"SATPUDA_WHOLESALE_OVERRIDE": "1"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _cleanup(self):
        try:
            self.conn.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_a_new_party_is_made_once_and_noted_for_the_server(self):
        res = sales_svc.save_sale(self.conn, _body("NEW MEDICAL AGENCY"))
        self.assertTrue(res["ok"], res)
        rows = _named(self.conn, "NEW MEDICAL AGENCY")
        self.assertEqual(len(rows), 1)
        cid = rows[0][0]
        self.assertTrue(3_000_000_000 < cid < 4_000_000_000, cid)
        dirty = {(c, i) for c, i, _ in self.conn.execute(
            "SELECT collection, local_id, op FROM of_dirty")}
        self.assertIn(("customers", cid), dirty)
        p = ws.party(self.conn, cid)
        self.assertEqual((p["gstin"], p["dl_numbers"]), (NEW_GSTIN, "20B-77, 21B-78"))
        again = sales_svc.save_sale(self.conn, _body("New Medical Agency"))
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["customer_id"], cid)
        self.assertEqual(len(_named(self.conn, "NEW MEDICAL AGENCY")), 1)


class TheSalesPage(unittest.TestCase):
    """The wholesale Sales page sends the typed name and saves without a picked party."""

    def setUp(self):
        with open(os.path.join(ROOT, "desktop", "src", "wholesale", "WholesaleSalePage.tsx"),
                  encoding="utf-8") as fh:
            self.src = fh.read()

    def test_sends_the_typed_name(self):
        self.assertIn("customer_name: form.partyText.trim()", self.src)

    def test_no_longer_asks_for_a_picked_party(self):
        self.assertNotIn("Pick the party first.", self.src)
        self.assertIn("Type the party name.", self.src)


if __name__ == "__main__":
    unittest.main()
