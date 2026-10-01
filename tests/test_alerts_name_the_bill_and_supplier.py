"""Alert & Monitoring names where every batch came from, and can be read by month or year.

1 Oct 2026, the owner: Bill Number and Supplier were empty, and there was no way to list the
expired / near-expiry / out-of-stock / low-stock batches of one month or one year -- "then
what is Alert & Monitoring for". The causes:
  * the bill was looked up by the batch exactly as typed on the bill, while the shelf keeps
    it the way purchase save matches it ("ab 12" on the bill, "AB12" on the shelf);
  * the supplier was read from purchase bills only, so opening stock and imported stock,
    whose rows carry their own supplier_name, were blank;
  * an unsaved draft (autosave) bill could be named as a batch's source;
  * Online wrote "" in both columns.

    python -m pytest tests/test_alerts_name_the_bill_and_supplier.py
"""
import sqlite3
import unittest
from datetime import date, timedelta
from unittest import mock

from core import alert_monitoring_service as am
from core import db_setup


def _day(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _shop():
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE BALAJI PHARMA')")
    conn.execute("INSERT INTO suppliers (id, name) VALUES (2, 'DRAFT DISTRIBUTOR')")
    meds = (
        # id, name, batch, expiry, stock, supplier written on the row
        (1, "DOLO 650", "AB12", _day(-40), 20, ""),            # expired, bought on a bill as "ab 12"
        (2, "SHELCAL 500", "SC9", _day(20), 15, ""),           # near expiry, bought on a bill
        (3, "ZANDU COUGH", "Z1", _day(-10), 4, "OPENING SUPPLIER"),   # opening stock: no bill
        (4, "PAN 40", "P1", _day(400), 0, ""),                 # out of stock
        (5, "CIPCAL 500", "C1", _day(500), 2, ""),             # low stock
    )
    for mid, name, batch, exp, qty, sup in meds:
        conn.execute("INSERT INTO medicines (id, name, batch_no, expiry_date, stock_qty, type, unit, supplier_name) "
                     "VALUES (?, ?, ?, ?, ?, 'Tablet', '10', ?)", (mid, name, batch, exp, qty, sup))
    bills = (
        # purchase id, bill no, supplier, date, autosave, [(medicine id, batch as typed)]
        (10, "INV-501", 1, "2026-08-14", 0, [(1, "ab 12"), (2, "SC9"), (4, "P1"), (5, "C1")]),
        (11, "DRAFT-9", 2, "2026-09-30", 1, [(1, "AB12"), (2, "SC9")]),      # a draft: not a source
    )
    for pid, no, sid, d, auto, lines in bills:
        conn.execute("INSERT INTO purchases (id, bill_number, supplier_id, purchase_date, is_autosave) "
                     "VALUES (?, ?, ?, ?, ?)", (pid, no, sid, d, auto))
        for mid, batch in lines:
            conn.execute("INSERT INTO purchase_items (purchase_id, medicine_id, batch_no, qty) VALUES (?, ?, ?, 10)",
                         (pid, mid, batch))
    conn.execute("INSERT OR REPLACE INTO settings (name, value) VALUES ('near_expiry_tablet', '60')")
    conn.execute("INSERT OR REPLACE INTO settings (name, value) VALUES ('low_stock_tablet', '5')")
    conn.commit()
    return conn


class TheColumnsAreFilled(unittest.TestCase):
    def setUp(self):
        self.conn = _shop()
        self.addCleanup(self.conn.close)

    def test_expired_names_the_bill_whatever_the_batch_spacing(self):
        rows = {r[0]: r for r in am.fetch_expired_medicines(self.conn)}
        self.assertEqual(rows["DOLO 650"][4:6], ("SHREE BALAJI PHARMA", "INV-501"))

    def test_opening_stock_names_its_own_supplier(self):
        rows = {r[0]: r for r in am.fetch_expired_medicines(self.conn)}
        self.assertEqual(rows["ZANDU COUGH"][4], "OPENING SUPPLIER")
        self.assertEqual(rows["ZANDU COUGH"][5], "")          # no bill brought it

    def test_a_draft_bill_is_never_the_source(self):
        rows = {r[0]: r for r in am.fetch_near_expiry_medicines(self.conn)}
        self.assertEqual(rows["SHELCAL 500"][5:7], ("SHREE BALAJI PHARMA", "INV-501"))

    def test_the_old_shapes_are_kept_for_the_popup_and_reorder(self):
        self.assertEqual(len(am.fetch_low_stock_alerts(self.conn)[0]), 4)
        self.assertEqual(len(am.fetch_out_of_stock_medicines(self.conn)[0]), 6)
        self.assertEqual(len(am.fetch_expired_medicines(self.conn)[0]), 6)
        self.assertEqual(len(am.fetch_near_expiry_medicines(self.conn)[0]), 7)


class TheScreenGetsBatchBillAndDate(unittest.TestCase):
    def setUp(self):
        self.conn = _shop()
        self.addCleanup(self.conn.close)
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            self.data = am.fetch_all_monitoring_sections(self.conn, detail=True)

    def test_low_and_out_carry_the_last_bill(self):
        low = {r[0]: r for r in self.data["low_stock"]}
        self.assertEqual(low["CIPCAL 500"][3:], ("SHREE BALAJI PHARMA", "C1", low["CIPCAL 500"][5],
                                                  "INV-501", "14-08-2026", "2026-08-14"))
        out = {r[0]: r for r in self.data["out_of_stock"]}
        self.assertEqual(out["PAN 40"][5:], ("SHREE BALAJI PHARMA", "P1", "INV-501", "14-08-2026", "2026-08-14"))

    def test_expiry_rows_end_with_the_expiry_date_for_the_month_filter(self):
        exp = {r[0]: r for r in self.data["expired"]}
        self.assertEqual(exp["DOLO 650"][-2:], ("14-08-2026", _day(-40)))

    def test_every_batch_with_an_expiry_is_there_for_month_and_year(self):
        names = {r[0] for r in self.data["expiry_by_batch"]}
        # CIPCAL expires in 500 days: not "near", but a month / year filter must find it
        self.assertEqual(names, {"DOLO 650", "SHELCAL 500", "ZANDU COUGH", "CIPCAL 500"})
        cip = next(r for r in self.data["expiry_by_batch"] if r[0] == "CIPCAL 500")
        self.assertEqual(len(cip), 9)
        self.assertEqual(cip[-1], _day(500))


class OnlineFillsThemToo(unittest.TestCase):
    def test_supplier_and_bill_are_not_blank_online(self):
        meds = [
            {"id": 1, "name": "DOLO 650", "batch_no": "AB12", "expiry_date": _day(-40), "stock_qty": 20,
             "type": "Tablet", "unit": "10", "supplier_name": ""},
            {"id": 3, "name": "ZANDU COUGH", "batch_no": "Z1", "expiry_date": _day(-10), "stock_qty": 4,
             "type": "Tablet", "unit": "10", "supplier_name": "OPENING SUPPLIER"},
        ]
        docs = [{"id": 10, "bill_number": "INV-501", "supplier_id": 1, "purchase_date": "2026-08-14",
                 "items": [{"medicine_id": 1, "batch_no": "ab 12"}]},
                {"id": 11, "bill_number": "DRAFT-9", "supplier_id": 2, "purchase_date": "2026-09-30",
                 "is_autosave": 1, "items": [{"medicine_id": 1, "batch_no": "AB12"}]}]
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        with mock.patch("core.online_catalog._pull_sync_pages", return_value=docs), \
                mock.patch("core.online_catalog.find_supplier_by_id",
                           side_effect=lambda sid: {"name": {1: "SHREE BALAJI PHARMA", 2: "DRAFT"}[int(sid)]}):
            got = am.online_stock_sections(conn, meds, detail=True)
        exp = {r[0]: r for r in got["expired"]}
        self.assertEqual(exp["DOLO 650"][4:6], ("SHREE BALAJI PHARMA", "INV-501"))
        self.assertEqual(exp["ZANDU COUGH"][4], "OPENING SUPPLIER")
        # without detail the popup's shape is unchanged, and the shelf supplier still shows
        plain = am.online_stock_sections(conn, meds)
        self.assertEqual(len(plain["expired"][0]), 6)


if __name__ == "__main__":
    unittest.main()
