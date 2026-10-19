"""Expired / Near Expiry grouped by supplier (owner, 19 Oct 2026).

"I want expired and near-expiry exports supplier-wise": the shop sends its expiries back to
whoever sold them. The alerts window groups those two lists by the supplier of the purchase
the batch came from, with each supplier's count and value at the purchase rate and at MRP,
and the export writes one block (or one PDF) per supplier with its own total.

    python -m pytest tests/test_expiry_alerts_by_supplier.py
"""
import base64
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import alert_supplier_groups as asg  # noqa: E402
from core import db_setup, sync_prefs  # noqa: E402
from core import startup_alerts_data as sad  # noqa: E402
from core.desktop_export_service import export_alert_sections  # noqa: E402

TODAY = date.today()


def _day(days: int) -> str:
    """An ISO date `days` from today that is not the 1st (the 1st means month-only)."""
    d = TODAY + timedelta(days=days)
    if d.day == 1:
        d += timedelta(days=1)
    return d.isoformat()


def _ins(conn, table, **vals):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    vals = {k: v for k, v in vals.items() if k in cols}
    cur = conn.execute(
        f"INSERT INTO {table} ({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
        tuple(vals.values()),
    )
    return cur.lastrowid


def _store():
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    cipla = _ins(conn, "suppliers", name="CIPLA AGENCIES")
    balaji = _ins(conn, "suppliers", name="SHREE BALAJI PHARMA")
    # expired, bought from CIPLA on bill C-1 at 40 a strip (the shelf row says 45 now)
    _ins(conn, "medicines", id=1, name="ALPHA 500", type="Tablet", batch_no="A1",
         expiry_date=_day(-30), stock_qty=20, unit="10", mrp=60, rate=45)
    # expired, the SAME medicine row bought later from BALAJI under another batch: the
    # batch's own bill decides, not the newest one
    _ins(conn, "medicines", id=2, name="BRAVO SYRUP", type="Syrup", batch_no="B1",
         expiry_date=_day(-10), stock_qty=3, unit="100ml", mrp=90, rate=70)
    # near expiry, from BALAJI
    _ins(conn, "medicines", id=3, name="CHARLIE CAP", type="Capsule", batch_no="C1",
         expiry_date=_day(20), stock_qty=15, unit="15", mrp=150, rate=100)
    # expired, no purchase and nothing written on the shelf row: Unknown supplier
    _ins(conn, "medicines", id=4, name="DELTA GEL", type="Others", batch_no="D1",
         expiry_date=_day(-5), stock_qty=2, unit="1", mrp=50, rate=30)
    # expired, opening stock: no purchase, but the shelf row names where it came from
    _ins(conn, "medicines", id=5, name="ECHO DROPS", type="Others", batch_no="E1",
         expiry_date=_day(-5), stock_qty=4, unit="1", mrp=20, rate=12, supplier_name="MAHALAXMI MEDICAL")
    p1 = _ins(conn, "purchases", supplier_id=cipla, bill_number="C-1", purchase_date=_day(-400))
    _ins(conn, "purchase_items", purchase_id=p1, medicine_id=1, batch_no="A1", qty=2, rate=40, mrp=60)
    _ins(conn, "purchase_items", purchase_id=p1, medicine_id=2, batch_no="B1", qty=3, rate=70, mrp=90)
    p2 = _ins(conn, "purchases", supplier_id=balaji, bill_number="SB-9", purchase_date=_day(-100))
    _ins(conn, "purchase_items", purchase_id=p2, medicine_id=3, batch_no="C1", qty=1, rate=100, mrp=150)
    _ins(conn, "purchase_items", purchase_id=p2, medicine_id=2, batch_no="B9", qty=1, rate=75, mrp=90)
    conn.commit()
    return conn


def _tab(tabs, title):
    return next(t for t in tabs if t["title"] == title)


def _as_dicts(tab):
    return [dict(zip(tab["columns"], r)) for r in tab["rows"]]


class TheWindowKnowsEachBatchsSupplier(unittest.TestCase):
    def setUp(self):
        self._online = mock.patch.object(sync_prefs, "is_online_mode", lambda *a, **k: False)
        self._online.start()
        self.tabs = sad.collect_startup_alerts(_store())

    def tearDown(self):
        self._online.stop()

    def test_the_first_five_columns_stay_where_the_row_actions_read_them(self):
        exp = _tab(self.tabs, "Expired Medicines")
        self.assertEqual(exp["columns"][:5], ["Medicine", "Type", "Batch", "Expiry", "Days Expired"])
        self.assertEqual(exp["group_by"], "Supplier")
        near = _tab(self.tabs, "Near Expiry Alerts")
        self.assertEqual(near["columns"][:5], ["Medicine", "Type", "Batch", "Expiry", "Days Left"])
        self.assertEqual(near["group_by"], "Supplier")

    def test_the_supplier_is_the_one_on_the_bill_that_brought_the_batch(self):
        rows = {r["Medicine"]: r for r in _as_dicts(_tab(self.tabs, "Expired Medicines"))}
        self.assertEqual(rows["ALPHA 500"]["Supplier"], "CIPLA AGENCIES")
        self.assertEqual(rows["ALPHA 500"]["Bill No"], "C-1")
        # B1 came on CIPLA's bill; BALAJI's newer bill brought another batch
        self.assertEqual(rows["BRAVO SYRUP"]["Supplier"], "CIPLA AGENCIES")
        self.assertEqual(rows["ECHO DROPS"]["Supplier"], "MAHALAXMI MEDICAL")
        self.assertEqual(rows["DELTA GEL"]["Supplier"], "")
        near = {r["Medicine"]: r for r in _as_dicts(_tab(self.tabs, "Near Expiry Alerts"))}
        self.assertEqual(near["CHARLIE CAP"]["Supplier"], "SHREE BALAJI PHARMA")

    def test_value_is_at_the_rate_paid_on_that_bill_and_at_mrp(self):
        rows = {r["Medicine"]: r for r in _as_dicts(_tab(self.tabs, "Expired Medicines"))}
        a = rows["ALPHA 500"]
        # 20 tablets of a 10-tablet strip = 2 strips; paid 40 a strip on C-1 (not today's 45)
        self.assertEqual((a["Qty"], a["P.Rate"], a["MRP"]), (20, 40.0, 60.0))
        self.assertEqual((a["Value (P.Rate)"], a["Value (MRP)"]), (80.0, 120.0))
        # no purchase: the shelf row's own purchase rate
        e = rows["ECHO DROPS"]
        self.assertEqual((e["P.Rate"], e["Value (P.Rate)"], e["Value (MRP)"]), (12.0, 48.0, 80.0))

    def test_grouped_by_supplier_unknown_last(self):
        exp = _tab(self.tabs, "Expired Medicines")
        groups = asg.group_by_supplier(exp["columns"], exp["rows"])
        self.assertEqual([g["supplier"] for g in groups],
                         ["CIPLA AGENCIES", "MAHALAXMI MEDICAL", asg.UNKNOWN_SUPPLIER])
        cipla = groups[0]
        self.assertEqual(cipla["count"], 2)
        self.assertEqual(cipla["value_rate"], 80.0 + 210.0)
        self.assertEqual(cipla["value_mrp"], 120.0 + 270.0)
        self.assertEqual([r[0] for r in groups[-1]["rows"]], ["DELTA GEL"])


class AlertAndMonitoringGetsTheValues(unittest.TestCase):
    """Settings -> Alert & Monitoring: the lists stay as they were; the batch values come
    beside them (batch_money), matched by name and batch."""

    def test_batch_money_and_counts(self):
        from core.desktop_settings_service import get_alerts

        with mock.patch.object(sync_prefs, "is_online_mode", lambda *a, **k: False):
            data = get_alerts(_store())
        self.assertNotIn("batch_money", data["counts"])
        money = {(r[0], r[1]): r[2:] for r in data["batch_money"]}
        self.assertEqual(money[("ALPHA 500", "A1")], [40.0, 60.0, 80.0, 120.0])
        # the Expired rows keep their shape: name, batch, expiry, qty, supplier, bill, ...
        exp = {r[0]: r for r in data["expired"]}
        self.assertEqual(exp["ALPHA 500"][4:6], ["CIPLA AGENCIES", "C-1"])
        self.assertEqual(exp["DELTA GEL"][4], "")


class TheExportIsSupplierWise(unittest.TestCase):
    def setUp(self):
        self._online = mock.patch.object(sync_prefs, "is_online_mode", lambda *a, **k: False)
        self._online.start()
        exp = _tab(sad.collect_startup_alerts(_store()), "Expired Medicines")
        self.section = {"title": "Expired", "columns": exp["columns"], "rows": exp["rows"],
                        "group_by": "Supplier"}

    def tearDown(self):
        self._online.stop()

    def test_csv_has_a_block_and_a_total_per_supplier(self):
        res = export_alert_sections({"title": "Expired", "sections": [self.section], "format": "csv"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["row_count"], 4)
        self.assertTrue(res["filename"].startswith("Expired_by_supplier"))
        text = base64.b64decode(res["content_base64"]).decode("utf-8-sig")
        self.assertIn("Expired — by supplier", text)
        self.assertIn("Expired — CIPLA AGENCIES: 2 batches", text)
        self.assertIn("Expired — Unknown supplier: 1 batch", text)
        import csv

        lines = list(csv.reader(io.StringIO(text)))
        at = next(i for i, ln in enumerate(lines) if ln and ln[0].startswith("Expired — CIPLA AGENCIES"))
        block = lines[at:at + 5]
        self.assertEqual(block[-1][0], "TOTAL", block)
        self.assertIn("290.00", block[-1])   # value at the purchase rate
        self.assertNotIn("Supplier", block[1])   # the heading names the supplier

    def test_excel_one_sheet_with_supplier_blocks(self):
        import openpyxl

        res = export_alert_sections({"title": "Expired", "sections": [self.section], "format": "xlsx"})
        wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(res["content_base64"])))
        self.assertEqual(wb.sheetnames, ["Expired"])
        col_a = [c.value for c in wb.worksheets[0]["A"]]
        self.assertIn("Expired — by supplier", col_a)
        self.assertEqual(sum(1 for v in col_a if v == "TOTAL"), 4)  # summary + 3 suppliers

    def test_without_grouping_the_list_is_as_before(self):
        res = export_alert_sections({"sections": [dict(self.section, group_by="")], "format": "csv"})
        text = base64.b64decode(res["content_base64"]).decode("utf-8-sig")
        self.assertNotIn("by supplier", text)
        self.assertNotIn("TOTAL", text)

    def test_one_pdf_per_supplier_in_a_dated_folder(self):
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch("core.saved_files.reports_dir", lambda: tmp), \
                    mock.patch("core.desktop_export_service._alerts_pdf_bytes",
                               lambda title, sections, landscape, kinds=None:
                               (("%PDF " + title + repr(sections)).encode(), "pdf")):
                res = export_alert_sections({"title": "Expired", "sections": [self.section],
                                             "format": "pdf", "per_supplier_files": True})
            self.assertTrue(res["ok"], res)
            self.assertEqual(len(res["files"]), 3)
            folder = res["folder"]
            self.assertTrue(os.path.basename(folder).startswith("Expiry by supplier_"))
            names = sorted(os.listdir(folder))
            self.assertTrue(names[0].startswith("Expired_CIPLA_AGENCIES_"), names)
            self.assertTrue(any(n.startswith("Expired_Unknown_supplier_") for n in names), names)
            body = open(os.path.join(folder, names[0]), "rb").read().decode()
            self.assertIn("ALPHA 500", body)
            self.assertNotIn("DELTA GEL", body)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
