"""A store file the phone worked on comes back to the PC whole, and the PC works on it (3 Oct 2026).

One device at a time, the WHOLE SQLite file passed over Google Drive: PC Backup Now -> phone
Restore -> phone bills -> phone Backup Now -> PC Sync from Drive. Nothing is merged, so a record a
hop drops, or a schema one app refuses, is a shop's bill gone.

The PC leg of the round trip PC -> phone -> PC -> phone (Android DriveRoundTripTest):

  tests/fixtures/desktop_made_store.db   the PC's file (tests/fixtures/make_desktop_made_store.py),
                                         also the PC's own copy still on disk when the phone's
                                         file arrives;
  tests/fixtures/android_made_store.db   that file after the phone restored it (LegacyDbMigrator +
                                         Room) and billed SCB4 + took a payment through its own
                                         services -- written by the Android test;
  tests/fixtures/pc_after_phone_store.db what this test leaves: the phone's file restored through
                                         the desktop's own path, opened, billed on (SCB5). The
                                         Android test reads a gzipped copy of it for the last leg.
"""
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.backup_manager as bm  # noqa: E402
from core import db_setup  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PC_FILE = os.path.join(FIXTURES, "desktop_made_store.db")
PHONE_FILE = os.path.join(FIXTURES, "android_made_store.db")
OUT_FILE = os.path.join(FIXTURES, "pc_after_phone_store.db")

TABLES = ("customers", "suppliers", "medicines", "sales", "sales_items", "purchases", "purchase_items",
          "sales_returns", "sales_return_items", "purchase_returns", "purchase_return_items",
          "customer_payments", "supplier_payments", "pharmacy_profile")

# Online-sync bookkeeping each app stamps as it opens the file -- not the shop's data.
SYNC_META = {"updated_at", "device_id", "version", "sync_status", "sync_state", "client_uuid", "synced_at",
             "last_updated"}

# What the phone's own work (bill SCB4 to RAMESH PATIL, 5 x DEMOCETAMOL; RS 20 from SUNITA JADHAV)
# rightly changed in the PC's rows.
CHANGED_BY_THE_PHONE = {
    ("medicines", 1, "stock_qty"): 85,          # 90 - 5
    ("customers", 2, "total_due"): 50.0,        # 70 - 20
    ("sales", 2, "total_due"): 50.0,            # her running due on her last bill
}

# Desktop-only columns the phone's Room tables do not have: a PC file restored on the phone
# for the first time loses them (LegacyDbMigrator rebuilds the table to Room's columns, and
# Room refuses any other after a migration). The desktop adds them back empty. The first
# three are older names for item_amount / gst_pct / discount_pct, which the desktop reads
# first; stock_units is recomputed from the pack when missing. Reported 3 Oct 2026.
NOT_KEPT_BY_THE_PHONE = {
    ("purchase_items", "amount"), ("purchase_items", "gst_value"), ("purchase_items", "discount_percent"),
    ("purchase_items", "stock_units"), ("purchase_return_items", "stock_units"),
    ("pharmacy_profile", "fssai_number"), ("pharmacy_profile", "show_fssai_on_bill"),
}


def _rows(path_or_conn, table):
    conn = sqlite3.connect(path_or_conn) if isinstance(path_or_conn, str) else path_or_conn
    try:
        conn.row_factory = sqlite3.Row
        return {r["id"]: dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")}
    finally:
        conn.row_factory = None
        if isinstance(path_or_conn, str):
            conn.close()


def _same(a, b):
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def _changed(before: dict, after: dict, table: str) -> list:
    out = []
    for rid, row in before.items():
        got = after.get(rid)
        if got is None:
            out.append(f"{table} id={rid} missing")
            continue
        for col, val in row.items():
            if (table, col) in NOT_KEPT_BY_THE_PHONE or col in SYNC_META:
                continue
            if val is None and col == "created_at":
                continue
            if (table, rid, col) in CHANGED_BY_THE_PHONE:
                val = CHANGED_BY_THE_PHONE[(table, rid, col)]
            if col not in got:
                out.append(f"{table}.{col} gone")
            elif not _same(val, got[col]):
                out.append(f"{table} id={rid} {col}: {val!r} -> {got[col]!r}")
    return out


@unittest.skipUnless(os.path.isfile(PHONE_FILE), "android_made_store.db not made yet (Android DriveRoundTripTest)")
class APhoneFileWorksOnThePc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Offline: the baton pass is an Offline-mode flow, and Online the desktop would read the
        # pharmacy header (and more) from the server instead of the file.
        cls.offline = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        cls.offline.start()
        cls.d = tempfile.mkdtemp()
        # The PC still has its own file from before it handed the store to the phone.
        cls.store_dir = os.path.join(cls.d, "stores", "Store_Demo_Medical")
        os.makedirs(cls.store_dir)
        cls.dest = os.path.join(cls.store_dir, "veterinary.db")
        shutil.copy2(PC_FILE, cls.dest)

        # What the phone uploaded (a gzip, as Backup Now writes it), through the desktop's own
        # download path: materialize, schema upgrade (initialise), then put in place.
        tmp_dir = tempfile.mkdtemp(dir=cls.d)
        src = os.path.join(tmp_dir, "SatpudaCore_2026-10-04_18-00.db.gz")
        with open(PHONE_FILE, "rb") as f_in, gzip.open(src, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        db_path = os.path.join(tmp_dir, "veterinary.db")
        assert bm._materialize_backup_file(src, db_path)
        bm._upgrade_restored_db(db_path)
        cls.lost = bm.would_lose(cls.dest, db_path)
        with mock.patch("core.store_manager.get_store_db_path", return_value=cls.dest), \
                mock.patch("core.store_manager.get_store_dir", return_value=cls.store_dir), \
                mock.patch.object(bm, "_close_all_db_users"), \
                mock.patch.object(bm, "_after_restore_sync_policy"):
            cls.ok, cls.info = bm._apply_restored_db(
                {"db_path": db_path, "tmp_dir": tmp_dir, "backup_file": os.path.basename(src),
                 "sale_count": bm._sale_stats(db_path)["count"]},
                "Demo Medical", "Store_Demo_Medical",
            )

        # The app opening the store.
        cls.conn = sqlite3.connect(cls.dest)
        db_setup.initialise(cls.conn)
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.offline.stop()
        cls.conn.close()
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_1_the_phones_file_is_taken_without_a_loss_warning(self):
        self.assertEqual({}, self.lost, "every PC record is in the phone's file: nothing to warn about")
        self.assertTrue(self.ok, self.info)
        self.assertEqual(4, self.info["sale_count"])

    def test_2_every_pc_record_and_the_phones_new_work_are_there(self):
        changed = []
        for table in TABLES:
            changed += _changed(_rows(PC_FILE, table), _rows(self.conn, table), table)
        self.assertEqual([], changed)
        c = self.conn
        bills = [r[0] for r in c.execute("SELECT bill_no FROM sales WHERE COALESCE(deleted,0)=0 "
                                          "AND COALESCE(is_autosave,0)=0 AND bill_no LIKE 'SCB%' ORDER BY id")]
        self.assertEqual(["SCB1/FY2026-27", "SCB2/FY2026-27", "SCB3/FY2026-27", "SCB4/FY2026-27"], bills[:4])
        phone_sale = c.execute("SELECT id, total_amount, amount_paid, customer_id, bill_date FROM sales "
                               "WHERE bill_no='SCB4/FY2026-27'").fetchone()
        self.assertEqual((15.0, 15.0, 1, "2026-10-04"), phone_sale[1:])
        self.assertEqual(1, c.execute("SELECT COUNT(*) FROM sales_items WHERE sale_id=?", (phone_sale[0],)).fetchone()[0])
        self.assertAlmostEqual(15.0, c.execute("SELECT SUM(amount) FROM sales_items WHERE sale_id=?",
                                               (phone_sale[0],)).fetchone()[0])
        pays = c.execute("SELECT customer_id, amount, payment_date FROM customer_payments ORDER BY id").fetchall()
        self.assertEqual([(2, 50.0, "2026-10-03"), (2, 20.0, "2026-10-04")], pays[:2])
        self.assertEqual(2, c.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
        self.assertEqual(3, c.execute("SELECT COUNT(*) FROM medicines").fetchone()[0])
        self.assertEqual(1, c.execute("SELECT COUNT(*) FROM purchases").fetchone()[0])
        self.assertEqual(3, c.execute("SELECT COUNT(*) FROM purchase_items").fetchone()[0])
        self.assertEqual(1, c.execute("SELECT COUNT(*) FROM sales_returns").fetchone()[0])
        self.assertEqual(1, c.execute("SELECT COUNT(*) FROM purchase_returns").fetchone()[0])
        self.assertEqual(1, c.execute("SELECT COUNT(*) FROM supplier_payments").fetchone()[0])

    def test_3_the_pc_pages_reports_and_exports_run_on_it(self):
        from core import desktop_pages_service as pages
        from core import gst_reports
        from core.desktop_export_service import run_export

        sales = pages.list_sales_history(self.conn, from_date="2026-10-01", to_date="2026-10-31")
        shown = {r.get("bill_no") or r.get("bill") for r in sales["rows"]} if sales["rows"] and isinstance(
            sales["rows"][0], dict) else None
        self.assertGreaterEqual(len(sales["rows"]), 4, sales.get("error"))
        if shown is not None:
            self.assertTrue({"SCB1", "SCB4"} <= {str(s).split("/")[0] for s in shown}, shown)
        purchases = pages.list_purchase_history(self.conn, from_date="2026-10-01", to_date="2026-10-31")
        self.assertEqual(1, len(purchases["rows"]), purchases.get("error"))
        inventory = pages.list_inventory(self.conn)
        self.assertEqual(3, len(inventory["rows"]), inventory.get("error"))
        report = gst_reports.build(self.conn, "2026-10-01", "2026-10-31")
        self.assertEqual(4, report["counts"]["bills"])
        self.assertEqual(1, report["counts"]["credit_notes"])
        self.assertEqual(1, report["counts"]["purchases"])
        self.assertEqual(1, report["counts"]["purchase_returns"])
        for page, rep in (("sales_history", "sales_register"), ("sales_history", "monthly_summary"),
                          ("purchase_history", "purchase_register")):
            out = run_export(self.conn, page, rep, from_date="2026-10-01", to_date="2026-10-31")
            self.assertFalse(out.get("error"), (page, rep, out.get("error")))
            self.assertTrue(out.get("rows"), (page, rep))

    def test_4_the_pc_bills_on_it_and_hands_it_back(self):
        """Room declares sales.paid_due, customers.total_due ... NOT NULL with no default; the
        desktop's INSERTs leave them out. On the phone's file as it used to be uploaded both of
        these failed ("NOT NULL constraint failed: sales.paid_due"). The phone now uploads a copy
        with the desktop's DEFAULT 0 on them (Android PcWritableBackup)."""
        from core import desktop_sales_service, desktop_settings_service

        body = {"items": [{"id": 3, "name": "DEMO COUGH SYRUP 100ML", "batch": "S303", "expiry": "2027-06-30",
                           "qty": 1, "rate": 110.0, "amount": 110.0}],
                "bill_date": "2026-10-05", "customer_name": "GANESH MORE", "payment_mode": "Cash",
                "cash_paid": 110.0}
        res = desktop_sales_service.save_sale(self.conn, body)
        self.assertTrue(res.get("ok", True) and not res.get("error"), res)
        row = self.conn.execute("SELECT bill_no, total_amount, paid_due FROM sales "
                                "WHERE bill_date='2026-10-05'").fetchone()
        self.assertEqual(("SCB5/FY2026-27", 110.0, 0.0), row)
        self.assertEqual((0.0, 0.0), self.conn.execute(
            "SELECT total_due, total_credit FROM customers WHERE name='GANESH MORE'").fetchone())
        pay = desktop_settings_service.save_payment(
            self.conn, {"kind": "customer", "party": "SUNITA JADHAV", "cash": 10, "date": "2026-10-05"})
        self.assertTrue(pay.get("payment_id"), pay)
        self.conn.commit()
        # The PC's Backup Now: what the phone restores next (Android DriveRoundTripTest, leg d).
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        bm._snapshot_db_for_backup(self.dest, OUT_FILE)
        c = sqlite3.connect(OUT_FILE)
        try:
            self.assertEqual(5, c.execute("SELECT COUNT(*) FROM sales").fetchone()[0])
            self.assertEqual(3, c.execute("SELECT COUNT(*) FROM customer_payments").fetchone()[0])
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
