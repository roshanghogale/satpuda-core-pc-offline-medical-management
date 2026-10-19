"""Every Alert & Monitoring list exports grouped the way it is used (owner, 19 Oct 2026:
"these options should be in each Alert & Monitoring list according to their requirements").

* Low Stock / Out of Stock -> by the supplier the medicine was LAST bought from (the newest
  purchase line of that medicine; else the supplier written on the shelf row; else
  "Unknown supplier"), so the shop reorders supplier by supplier.
* Customer Dues -> by the customer's village / area (customers.address, else the address on
  the customer's newest bill), with each area's total due.
* Expired / Near Expiry stay by the supplier the batch came from
  (tests/test_expiry_alerts_by_supplier.py).

The new columns are appended after the old ones, so row actions that read by position keep
working; the export writes a summary by supplier / area and one block (or one PDF) per group.

    python -m pytest tests/test_alert_lists_grouped_for_export.py
"""
import base64
import csv
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

from core import alert_monitoring_service as ams  # noqa: E402
from core import alert_supplier_groups as asg  # noqa: E402
from core import db_setup, sync_prefs  # noqa: E402
from core import startup_alerts_data as sad  # noqa: E402
from core.desktop_export_service import export_alert_sections  # noqa: E402

TODAY = date.today()


def _day(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


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
    conn.execute("INSERT OR REPLACE INTO settings (name, value) VALUES ('low_stock_tablet', '10')")
    cipla = _ins(conn, "suppliers", name="CIPLA AGENCIES")
    balaji = _ins(conn, "suppliers", name="SHREE BALAJI PHARMA")
    # ALPHA: bought from CIPLA long ago, from BALAJI last month -> BALAJI; the shelf row still
    # says the opening-stock supplier, which no longer decides
    _ins(conn, "medicines", id=1, name="ALPHA 500", type="Tablet", batch_no="A1",
         expiry_date=_day(400), stock_qty=3, unit="10", mrp=60, rate=45, supplier_name="OLD OPENING")
    # BRAVO: one purchase from CIPLA, then a NEWER bill with no supplier on it -> CIPLA
    _ins(conn, "medicines", id=2, name="BRAVO 250", type="Tablet", batch_no="B1",
         expiry_date=_day(400), stock_qty=4, unit="10", mrp=30, rate=20)
    # CHARLIE: never bought on a bill; opening stock names its supplier -> that one
    _ins(conn, "medicines", id=3, name="CHARLIE CAP", type="Tablet", batch_no="C1",
         expiry_date=_day(400), stock_qty=2, unit="10", mrp=50, rate=30, supplier_name="MAHALAXMI MEDICAL")
    # DELTA: nothing anywhere -> Unknown supplier
    _ins(conn, "medicines", id=4, name="DELTA GEL", type="Tablet", batch_no="D1",
         expiry_date=_day(400), stock_qty=1, unit="10", mrp=40, rate=25)
    # ECHO: out of stock, last bought from BALAJI (an older CIPLA line on another batch row)
    _ins(conn, "medicines", id=5, name="ECHO DROPS", type="Tablet", batch_no="E1",
         expiry_date=_day(400), stock_qty=0, unit="15", mrp=20, rate=12)
    _ins(conn, "medicines", id=6, name="ECHO DROPS", type="Tablet", batch_no="E0",
         expiry_date=_day(300), stock_qty=0, unit="15", mrp=20, rate=12)
    p_old = _ins(conn, "purchases", supplier_id=cipla, bill_number="C-1", purchase_date=_day(-300))
    _ins(conn, "purchase_items", purchase_id=p_old, medicine_id=1, batch_no="A0", qty=1, rate=40)
    _ins(conn, "purchase_items", purchase_id=p_old, medicine_id=2, batch_no="B1", qty=1, rate=20)
    _ins(conn, "purchase_items", purchase_id=p_old, medicine_id=6, batch_no="E0", qty=1, rate=12)
    p_new = _ins(conn, "purchases", supplier_id=balaji, bill_number="SB-7", purchase_date=_day(-30))
    _ins(conn, "purchase_items", purchase_id=p_new, medicine_id=1, batch_no="A1", qty=1, rate=44)
    _ins(conn, "purchase_items", purchase_id=p_new, medicine_id=5, batch_no="E1", qty=1, rate=12)
    p_nosup = _ins(conn, "purchases", bill_number="X-9", purchase_date=_day(-5))
    _ins(conn, "purchase_items", purchase_id=p_nosup, medicine_id=2, batch_no="B1", qty=1, rate=21)
    # a draft from CIPLA, newest of all: never the source
    p_draft = _ins(conn, "purchases", supplier_id=cipla, bill_number="D-1", purchase_date=_day(-1),
                   is_autosave=1)
    _ins(conn, "purchase_items", purchase_id=p_draft, medicine_id=1, batch_no="A1", qty=1, rate=40)

    # customers: two in Shirpur (one typed in lower case with spaces), one whose area is only on
    # her bill, one with no address anywhere
    ram = _ins(conn, "customers", name="RAMESH", phone="9000000001", address="Shirpur", total_due=700)
    sur = _ins(conn, "customers", name="SURESH", phone="9000000002", address="  shirpur ", total_due=300)
    gee = _ins(conn, "customers", name="GEETA", phone="9000000003", address="", total_due=250)
    raj = _ins(conn, "customers", name="RAJU", phone="9000000004", total_due=100)
    for cid, bill, d, due in ((ram, "B1", -40, 500), (ram, "B2", -10, 200), (sur, "B3", -20, 300),
                              (gee, "B4", -15, 250), (raj, "B5", -5, 100)):
        _ins(conn, "sales", customer_id=cid, bill_no=bill, bill_date=_day(d), total_amount=due,
             amount_paid=0, due_amount=due, total_due=due, account_cleared=0,
             customer_address="Dondaicha" if cid == gee else "")
    conn.commit()
    return conn


def _offline():
    return mock.patch.object(sync_prefs, "is_online_mode", lambda *a, **k: False)


def _tab(tabs, title):
    return next(t for t in tabs if t["title"] == title)


def _csv(res):
    return list(csv.reader(io.StringIO(base64.b64decode(res["content_base64"]).decode("utf-8-sig"))))


class LowStockNamesTheLastSupplier(unittest.TestCase):
    def setUp(self):
        self.conn = _store()
        self.addCleanup(self.conn.close)

    def test_the_latest_purchase_wins_then_the_shelf_then_nobody(self):
        low = {r[0]: r for r in ams.fetch_low_stock_alerts(self.conn)}
        self.assertEqual(low["ALPHA 500"][3], "SHREE BALAJI PHARMA")      # newest bill
        self.assertEqual(low["BRAVO 250"][3], "CIPLA AGENCIES")           # newest naming one
        self.assertEqual(low["CHARLIE CAP"][3], "MAHALAXMI MEDICAL")      # shelf row
        self.assertEqual(low["DELTA GEL"][3], "")                         # nobody
        self.assertEqual(len(low["ALPHA 500"]), 4)                        # shape kept

    def test_out_of_stock_too_across_the_rows_of_the_name(self):
        out = {r[0]: r for r in ams.fetch_out_of_stock_medicines(self.conn)}
        self.assertEqual(out["ECHO DROPS"][5], "SHREE BALAJI PHARMA")

    def test_alert_and_monitoring_shows_the_same_supplier(self):
        with _offline():
            data = ams.fetch_all_monitoring_sections(self.conn, detail=True)
        low = {r[0]: r for r in data["low_stock"]}
        self.assertEqual(low["ALPHA 500"][3], "SHREE BALAJI PHARMA")
        # the month filter still reads the hidden purchase date last
        self.assertEqual(low["ALPHA 500"][-1], _day(-30))

    def test_the_popup_appends_the_supplier_after_the_old_columns(self):
        with _offline():
            tabs = sad.collect_startup_alerts(self.conn)
        low = _tab(tabs, "Low Stock Alerts")
        self.assertEqual(low["columns"], ["Medicine", "Type", "Batch", "Stock", "Unit", "Supplier"])
        self.assertEqual(low["group_by"], "Supplier")
        self.assertFalse(low["grouped"])          # opens as a plain list
        rows = {r[0]: r for r in low["rows"]}
        self.assertEqual(rows["ALPHA 500"][2], "A1")
        self.assertEqual(rows["ALPHA 500"][5], "SHREE BALAJI PHARMA")
        oos = _tab(tabs, "Out of Stock")
        self.assertEqual(oos["group_by"], "Supplier")
        # Expired / Near Expiry open grouped (their rows go back to the supplier)
        empty = sad._tabs([], [], [], [], [])
        self.assertTrue(_tab(empty, "Expired Medicines")["grouped"])
        self.assertTrue(_tab(empty, "Near Expiry Alerts")["grouped"])
        self.assertFalse(_tab(empty, "Customer Due Alerts")["grouped"])

    def test_unknown_supplier_is_its_own_group_last(self):
        with _offline():
            low = _tab(sad.collect_startup_alerts(self.conn), "Low Stock Alerts")
        groups = asg.group_rows(low["columns"], low["rows"], "supplier")
        self.assertEqual([g["name"] for g in groups],
                         ["CIPLA AGENCIES", "MAHALAXMI MEDICAL", "SHREE BALAJI PHARMA", asg.UNKNOWN_SUPPLIER])
        self.assertEqual(groups[0]["names"], 1)


class OnlineStartupStaysLight(unittest.TestCase):
    def test_no_purchase_pull_for_the_popup_but_alert_and_monitoring_reads_them(self):
        meds = [{"id": 1, "name": "ALPHA 500", "batch_no": "A1", "expiry_date": _day(400),
                 "stock_qty": 3, "type": "Tablet", "unit": "10", "supplier_name": "OLD OPENING"}]
        docs = [{"id": 10, "bill_number": "SB-7", "supplier_id": 2, "purchase_date": _day(-30),
                 "items": [{"medicine_id": 1, "batch_no": "A1"}]}]
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        pull = mock.Mock(return_value=docs)
        with mock.patch("core.online_catalog._pull_sync_pages", pull), \
                mock.patch("core.online_catalog.find_supplier_by_id",
                           side_effect=lambda sid: {"name": "SHREE BALAJI PHARMA"}):
            light = ams.online_stock_sections(conn, meds)
            self.assertFalse(pull.called)
            self.assertEqual(light["low_stock"][0][3], "OLD OPENING")
            full = ams.online_stock_sections(conn, meds, detail=True)
        self.assertEqual(full["low_stock"][0][3], "SHREE BALAJI PHARMA")


class DuesKnowTheArea(unittest.TestCase):
    def setUp(self):
        self.conn = _store()
        self.addCleanup(self.conn.close)

    def test_each_bill_carries_its_customers_area_last(self):
        rows = {r[2]: r for r in ams.fetch_customer_due_bills(self.conn)}
        self.assertEqual(rows["B1"][8], "Shirpur")
        self.assertEqual(rows["B3"][8], "shirpur")          # spaces tidied, case kept
        self.assertEqual(rows["B4"][8], "Dondaicha")        # from her bill
        self.assertEqual(rows["B5"][8], "")
        self.assertEqual(rows["B1"][6], 500.0)              # old columns unchanged

    def test_the_popup_appends_the_area(self):
        with _offline():
            due = _tab(sad.collect_startup_alerts(self.conn), "Customer Due Alerts")
        self.assertEqual(due["columns"], ["Customer", "Phone", "Due Amount", "Area"])
        self.assertEqual(due["group_by"], "Area")
        rows = {r[0]: r for r in due["rows"]}
        self.assertEqual(rows["RAMESH"][:3], ("RAMESH", "9000000001", 700.0))
        self.assertEqual(rows["GEETA"][3], "Dondaicha")
        # old callers keep the three-column shape
        self.assertEqual(len(ams.fetch_customer_due_summary(self.conn)[0]), 3)

    def test_online_reads_the_store_customers_address(self):
        custs = [{"id": 1, "name": "RAMESH", "phone": "9", "total_due": 700, "address": "Shirpur"}]
        sales = [{"customer_id": 1, "customer_name": "RAMESH", "bill_no": "B1", "bill_date": _day(-3),
                  "total_amount": 700, "amount_paid": 0, "due_amount": 700, "total_due": 700}]
        with mock.patch.object(sync_prefs, "is_online_mode", lambda *a, **k: True), \
                mock.patch("core.online_catalog.customers", return_value=custs), \
                mock.patch("core.online_catalog.last_error", return_value=""), \
                mock.patch("core.store_query_client.list_sales", return_value={"rows": sales}), \
                mock.patch.object(ams, "online_stock_sections", return_value={}):
            summary = ams.fetch_customer_due_summary_online(self.conn, area=True)
            bills = ams._fetch_all_monitoring_online(self.conn)["customer_due"]
        self.assertEqual(summary[0][3], "Shirpur")
        self.assertEqual(bills[0][8], "Shirpur")

    def test_grouped_by_area_case_blind_unknown_last(self):
        cols = ["Customer Name", "Mobile Number", "Bill Number", "Bill Date", "Total Amount",
                "Paid Amount", "Due Amount", "Due Days", "Area"]
        groups = asg.group_rows(cols, ams.fetch_customer_due_bills(self.conn), "area")
        self.assertEqual([g["name"] for g in groups], ["Dondaicha", "Shirpur", asg.UNKNOWN_AREA])
        shirpur = groups[1]
        self.assertEqual((shirpur["names"], shirpur["bills"], shirpur["due"]), (2, 3, 1000.0))


class TheExportCutsEachListItsWay(unittest.TestCase):
    def setUp(self):
        self.conn = _store()
        self.addCleanup(self.conn.close)
        self._off = _offline()
        self._off.start()
        self.addCleanup(self._off.stop)
        tabs = sad.collect_startup_alerts(self.conn)
        low = _tab(tabs, "Low Stock Alerts")
        self.low = {"title": "Low Stock", "columns": low["columns"], "rows": low["rows"],
                    "group_by": "Supplier"}
        cols = ["Customer Name", "Mobile Number", "Bill Number", "Bill Date", "Total Amount",
                "Paid Amount", "Due Amount", "Due Days", "Area"]
        self.dues = {"title": "Customer Dues", "columns": cols,
                     "rows": [list(r) for r in ams.fetch_customer_due_bills(self.conn)], "group_by": "Area"}

    def test_low_stock_csv_by_supplier(self):
        res = export_alert_sections({"title": "Low Stock", "sections": [self.low], "format": "csv"})
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["filename"].startswith("Low_Stock_by_supplier"))
        lines = _csv(res)
        flat = [ln[0] for ln in lines if ln]
        self.assertIn("Low Stock — by supplier", flat)
        self.assertIn("Low Stock — SHREE BALAJI PHARMA: 1 medicine", flat)
        self.assertEqual(flat[-3], "Low Stock — Unknown supplier: 1 medicine")   # last block
        # stock of different medicines is never added up; the heading counts them
        self.assertEqual(flat.count("TOTAL"), 1)   # the summary's medicine count only
        at = flat.index("Low Stock — by supplier")
        self.assertEqual(lines[[i for i, ln in enumerate(lines) if ln][at] + 1], ["Supplier", "Medicine Count"])
        self.assertEqual(res["row_count"], 4)

    def test_dues_csv_by_area_with_each_areas_total_due(self):
        res = export_alert_sections({"title": "Customer Dues", "sections": [self.dues], "format": "csv"})
        self.assertTrue(res["filename"].startswith("Customer_Dues_by_area"))
        lines = _csv(res)
        heads = [ln[0] for ln in lines if ln]
        self.assertIn("Customer Dues — by area", heads)
        self.assertIn("Customer Dues — Shirpur: 2 customers, 3 bills, ₹1,000.00 due", heads)
        self.assertIn("Customer Dues — Unknown area: 1 customer, 1 bill, ₹100.00 due", heads)
        at = next(i for i, ln in enumerate(lines) if ln and ln[0].startswith("Customer Dues — Shirpur"))
        block = lines[at:at + 6]
        self.assertEqual(block[1][-1], "Due Days")        # the area column is in the heading
        self.assertNotIn("Area", block[1])
        total = block[-1]
        self.assertEqual(total[0], "TOTAL", block)
        self.assertEqual(total[6], "1000.00")            # due
        self.assertEqual(total[7], "")                   # days are never added up
        summary_at = heads.index("Customer Dues — by area")
        rows = [ln for ln in lines if ln]
        self.assertEqual(rows[summary_at + 1], ["Area", "Customer Count", "Bill Count", "Due Amount"])

    def test_excel_one_sheet_per_list_with_area_blocks(self):
        import openpyxl

        res = export_alert_sections({"title": "Alert & Monitoring", "sections": [self.low, self.dues],
                                     "format": "xlsx"})
        self.assertTrue(res["filename"].startswith("Alert_Monitoring_by_supplier_and_area"))
        wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(res["content_base64"])))
        self.assertEqual(wb.sheetnames, ["Low Stock", "Customer Dues"])
        col_a = [c.value for c in wb["Customer Dues"]["A"]]
        self.assertIn("Customer Dues — Dondaicha: 1 customer, 1 bill, ₹250.00 due", col_a)

    def test_one_pdf_per_area(self):
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch("core.saved_files.reports_dir", lambda: tmp), \
                    mock.patch("core.desktop_export_service._alerts_pdf_bytes",
                               lambda title, sections, landscape, kinds=None:
                               (("%PDF " + title + repr(sections)).encode(), "pdf")):
                res = export_alert_sections({"title": "Customer Dues", "sections": [self.dues],
                                             "format": "pdf", "per_supplier_files": True})
            self.assertTrue(res["ok"], res)
            self.assertEqual(len(res["files"]), 3)
            self.assertTrue(os.path.basename(res["folder"]).startswith("Dues by area_"))
            names = sorted(os.listdir(res["folder"]))
            self.assertTrue(names[0].startswith("Customer_Dues_Dondaicha_"), names)
            body = open(os.path.join(res["folder"], [n for n in names if "Shirpur" in n][0]), "rb").read().decode()
            self.assertIn("RAMESH", body)
            self.assertNotIn("GEETA", body)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_one_pdf_per_supplier_for_low_stock(self):
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch("core.saved_files.reports_dir", lambda: tmp), \
                    mock.patch("core.desktop_export_service._alerts_pdf_bytes",
                               lambda title, sections, landscape, kinds=None:
                               (("%PDF " + title).encode(), "pdf")):
                res = export_alert_sections({"title": "Low Stock", "sections": [self.low],
                                             "format": "pdf", "per_supplier_files": True})
            self.assertEqual(len(res["files"]), 4)
            self.assertTrue(os.path.basename(res["folder"]).startswith("Stock by supplier_"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_pdf_heads_each_group_and_totals_it(self):
        from core import desktop_export_service as des

        seen = {}

        def fake_save(html, path):
            seen["html"] = html
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
            return path, None

        with mock.patch("core.document_output.save_html_as_pdf", fake_save):
            res = des.export_alert_sections({"title": "Customer Dues", "sections": [self.dues], "format": "pdf"})
        self.assertTrue(res["ok"], res)
        html = seen["html"]
        self.assertIn("Customer Dues — village / area-wise", html)
        self.assertIn("<h3 class=sup>Customer Dues — Shirpur: 2 customers, 3 bills, ₹1,000.00 due</h3>", html)
        self.assertIn("<td>1000.00</td>", html)

    def test_ungrouped_is_the_plain_list(self):
        res = export_alert_sections({"sections": [dict(self.dues, group_by="")], "format": "csv"})
        lines = _csv(res)
        self.assertEqual(lines[0][-1], "Area")
        self.assertEqual(len(lines), 1 + 5)
        self.assertNotIn("TOTAL", [ln[0] for ln in lines if ln])


if __name__ == "__main__":
    unittest.main()
