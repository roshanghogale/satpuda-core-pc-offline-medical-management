"""Offline-first audit (23 Oct 2026), the parts that need no server.

The two-PC matrix against the rehearsal server is tests/test_offline_first_audit_server.py;
these pin the fixes it led to on a temp store file:

* a receipt / return / another bill re-clearing a bill (or a party's due) is not an edit of
  it: the bill is not sent again -- the whole stale copy replaced another PC's edit;
* an event names THIS PC as the writer -- the row's old device_id made a one-PC shop's second
  edit read as another device's clash;
* a record made and deleted before a sync sends nothing (it came back "delete_missing");
* the server's settings change is one document of every row -- each shop-wide row is applied;
* walk-in lines go into this PC's own day counter bill, never another PC's;
* a purchase line carries its discount, taxable value and GST to the server.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.offline_first import outbox, pull, schema  # noqa: E402
from core.offline_first.schema import meta_set  # noqa: E402
from tests.test_offline_first_pc_local import BASE, Base  # noqa: E402


class BillsAndPartiesAreSentOnlyWhenEdited(Base):
    def setUp(self):
        super().setUp()
        self.cid = self.conn.execute("INSERT INTO customers (name) VALUES ('RAM')").lastrowid
        self.sid = self.conn.execute(
            "INSERT INTO sales (bill_no, customer_id, bill_date, total_amount) VALUES ('SCB1','%d','2026-10-23',50)"
            % self.cid).lastrowid
        self.pid = self.conn.execute(
            "INSERT INTO purchases (purchase_no, supplier_id, purchase_date, total_amount) VALUES ('1',1,'2026-10-23',90)"
        ).lastrowid
        self.conn.commit()
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.commit()

    def test_the_dues_cascade_fields_do_not_send_the_bill(self):
        self.conn.execute("UPDATE sales SET total_due=20, due_amount=20, bill_cleared=0, account_cleared=0, "
                          "version=COALESCE(version,0)+1, updated_at='x' WHERE id=?", (self.sid,))
        self.conn.execute("UPDATE purchases SET due_amount=5, due=5, total_due=5, bill_cleared=0 WHERE id=?",
                          (self.pid,))
        self.conn.execute("UPDATE customers SET total_due=20, total_credit=0, last_updated='now' WHERE id=?",
                          (self.cid,))
        self.conn.commit()
        self.assertEqual(self.dirty(), set())

    def test_an_edit_still_sends_it(self):
        self.conn.execute("UPDATE sales SET amount_paid=30, cash_paid=30 WHERE id=?", (self.sid,))
        self.conn.execute("UPDATE purchases SET bill_number='B9' WHERE id=?", (self.pid,))
        self.conn.execute("UPDATE customers SET phone='9000000001' WHERE id=?", (self.cid,))
        self.conn.commit()
        self.assertEqual(self.dirty(), {("sales", self.sid, "upsert"), ("purchases", self.pid, "upsert"),
                                        ("customers", self.cid, "upsert")})

    def test_an_old_unconditional_trigger_is_replaced(self):
        self.conn.execute("DROP TRIGGER of_upd_sales")
        self.conn.execute(
            "CREATE TRIGGER of_upd_sales AFTER UPDATE ON sales BEGIN "
            "INSERT OR REPLACE INTO of_dirty (collection, local_id, op, at) VALUES ('sales', NEW.id, 'upsert', 'x'); END;")
        self.conn.commit()
        schema.ensure_schema(self.conn)
        self.conn.execute("UPDATE sales SET bill_cleared=1 WHERE id=?", (self.sid,))
        self.conn.commit()
        self.assertEqual(self.dirty(), set())


class WhatAnEventSays(Base):
    def test_the_event_names_this_pc_as_the_writer(self):
        cid = self.conn.execute("INSERT INTO customers (name) VALUES ('SHAM')").lastrowid
        self.conn.execute("UPDATE customers SET device_id='MEDIVISION-IMPORT' WHERE id=?", (cid,))
        self.conn.commit()
        outbox.build_events(self.conn, "pc-this-one")
        docs = [json.loads(p)["doc"] for (p,) in self.conn.execute(
            "SELECT payload FROM of_events WHERE collection='customers'")]
        self.assertEqual([d.get("device_id") for d in docs], ["pc-this-one"])

    def test_made_and_deleted_before_a_sync_sends_nothing(self):
        sid = self.conn.execute(
            "INSERT INTO sales (bill_no, bill_date, total_amount) VALUES ('SCB2','2026-10-23',10)").lastrowid
        self.conn.execute("DELETE FROM sales WHERE id=?", (sid,))
        self.conn.commit()
        self.assertTrue(BASE < sid)
        outbox.build_events(self.conn, "pc-this-one")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM of_events WHERE collection='sales'").fetchone()[0], 0)
        self.assertEqual(self.dirty(), set())

    def test_a_delete_of_something_sent_before_is_sent(self):
        sid = self.conn.execute(
            "INSERT INTO sales (bill_no, bill_date, total_amount) VALUES ('SCB3','2026-10-23',10)").lastrowid
        self.conn.commit()
        outbox.build_events(self.conn, "pc-this-one")
        self.conn.execute("DELETE FROM sales WHERE id=?", (sid,))
        self.conn.commit()
        outbox.build_events(self.conn, "pc-this-one")
        ops = [r[0] for r in self.conn.execute("SELECT op FROM of_events WHERE collection='sales' ORDER BY seq")]
        self.assertEqual(ops, ["upsert", "delete"])

    def test_a_clash_on_a_party_due_is_not_counted_for_the_shop(self):
        for seq, col, code in ((1, "customers", "concurrent_edit"), (2, "sales", "concurrent_edit"),
                               (3, "sales", "delete_missing")):
            self.conn.execute(
                "INSERT INTO of_events (seq, event_uuid, collection, op, payload, created_at, status, outcome, flag_code) "
                "VALUES (?,?,?,'upsert','{}','x','sent','flagged',?)", (seq, f"u{seq}", col, code))
        self.conn.commit()
        self.assertEqual(outbox.counts(self.conn)["flagged"], 1)


class TheSettingsChange(Base):
    def test_every_shop_wide_row_of_the_bundle_is_applied_and_a_pending_one_kept(self):
        from unittest import mock

        self.conn.execute("INSERT INTO settings (name, value) VALUES ('customer_gst:5', 'MINE')")
        self.conn.commit()      # noted, not sent yet
        page = [{"collection": "settings", "local_id": 0, "operation": "upsert",
                 "doc": {"id": 0, "local_id": 0, "settings": [
                     {"name": "regular_meds:12", "value": '{"items": [{"name": "DOLO"}]}'},
                     {"name": "customer_gst:5", "value": "THEIRS"},
                     {"name": "fy_serial_migrated_v1", "value": "9"},
                 ]}}]
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            self.conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
            pull.apply_page(self.conn, page, to_revision=5)
            self.conn.execute("UPDATE of_flags SET v='1' WHERE k='capture'")
        got = dict(self.conn.execute("SELECT name, value FROM settings WHERE name IN "
                                     "('regular_meds:12','customer_gst:5','0')").fetchall())
        self.assertEqual(got, {"regular_meds:12": '{"items": [{"name": "DOLO"}]}', "customer_gst:5": "MINE"})
        # a PC's own marker never comes from another device
        self.assertNotEqual(self.conn.execute(
            "SELECT value FROM settings WHERE name='fy_serial_migrated_v1'").fetchone()[0], "9")


class WalkInsGoIntoThisPcsOwnDayBill(Base):
    def test_another_pcs_counter_bill_is_not_added_to(self):
        from core.billing_service import find_todays_counter_sale_id

        cid = self.conn.execute("INSERT INTO customers (name) VALUES ('COUNTER SALE')").lastrowid
        other = 7_000_000_001
        self.conn.execute("INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount) "
                          "VALUES (?, 'SCB70', ?, '2026-10-23', 10)", (other, cid))
        self.conn.commit()
        self.assertIsNone(find_todays_counter_sale_id(self.conn, cid, "2026-10-23"))
        mine = self.conn.execute("INSERT INTO sales (bill_no, customer_id, bill_date, total_amount) "
                                 "VALUES ('SCB71', ?, '2026-10-23', 10)", (cid,)).lastrowid
        self.conn.commit()
        self.assertEqual(find_todays_counter_sale_id(self.conn, cid, "2026-10-23"), mine)


class APurchaseLineCarriesItsTax(Base):
    def test_discount_taxable_and_gst_go_up(self):
        from core import server_sync as ss

        mid = self.add_medicine()
        pid = self.conn.execute(
            "INSERT INTO purchases (purchase_no, supplier_id, purchase_date, total_amount, gst_calc_method) "
            "VALUES ('2',1,'2026-10-23',54.01,'discount_after_gst')").lastrowid
        self.conn.execute(
            "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, gst_pct, discount_pct, taxable, "
            "gst_amt, item_amount, gst_value) VALUES (?,?,10,6,12,10,48.21,5.8,54.01,12)", (pid, mid))
        self.conn.commit()
        doc = ss._build_docs(self.conn, "purchases", ids=[pid])[0]
        it = doc["items"][0]
        self.assertEqual((it["discount_pct"], it["taxable"], it["gst_amt"]), (10.0, 48.21, 5.8))
        self.assertEqual(doc["gst_calc_method"], "discount_after_gst")


if __name__ == "__main__":
    unittest.main()
