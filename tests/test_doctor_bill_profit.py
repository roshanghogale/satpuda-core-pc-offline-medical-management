"""Doctor bill-wise profit (owner, 30 Oct 2026): Sales History -> Export -> Doctor Margin Report
-> Bill-wise profit, like the other software's "Selected doctors: bill-wise profit report".

One row per bill of the doctors picked: date, bill no, customer, short address, sale less
returns, cost, profit, profit %; totals; one doctor, several, or all; any period. The books
are test_doctor_margin_report's small store (made-up names only).
"""
import base64
import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import doctor_bill_profit as dbp  # noqa: E402
from core import doctor_margin_report as rep  # noqa: E402
from tests.test_doctor_margin_report import FROM, TO, Books  # noqa: E402


class BillWise(Books):
    def setUp(self):
        super().setUp()
        self.conn.execute("UPDATE customers SET address='DEMO LANE, DEMO TOWN' WHERE id=?", (self.cust2,))
        self.conn.commit()

    def bills(self, **kw):
        body = {"from": FROM, "to": TO, "mode": "bills"}
        body.update(kw)
        return rep.build_report(self.conn, body)

    def by_no(self, r):
        return {x["bill_no"]: x for x in r["rows"]}


class TheRows(BillWise):
    def test_one_row_per_bill_of_the_doctor(self):
        r = self.bills(doctors=["dr a"])
        rows = self.by_no(r)
        # SB6 deleted, SB7 a draft, SB8 before the period
        self.assertEqual(list(rows), ["SB1", "SB2", "SB5"])
        self.assertEqual((rows["SB1"]["sale"], rows["SB1"]["cost"], rows["SB1"]["profit"], rows["SB1"]["pct"]),
                         (40.0, 10.0, 30.0, 75.0))
        # 2 batches on one bill: one row
        self.assertEqual((rows["SB2"]["sale"], rows["SB2"]["cost"]), (100.0, 30.0))
        # 4 of the 10 tablets came back: the sale less the return
        self.assertEqual((rows["SB5"]["sale"], rows["SB5"]["cost"], rows["SB5"]["profit"]), (48.0, 30.0, 18.0))
        self.assertEqual(r["totals"], {"bills": 3, "sale": 188.0, "cost": 70.0, "profit": 118.0, "pct": 62.77})

    def test_injections_count_too(self):
        row = self.by_no(self.bills(doctors=["DR B"]))["SB3"]
        self.assertEqual((row["sale"], row["cost"], row["profit"], row["pct"]), (240.0, 140.0, 100.0, 41.67))

    def test_several_doctors_and_all(self):
        both = self.bills(doctors=["DR A", "dr b"])
        self.assertEqual([x["bill_no"] for x in both["rows"]], ["SB1", "SB2", "SB3", "SB5"])
        self.assertEqual([d["doctor"] for d in both["doctors"]], ["DR A", "DR B"])
        everyone = self.bills()
        self.assertEqual(len(everyone["rows"]), 7)             # SB1-5, SB9, SB10
        self.assertIn("(No doctor)", [d["doctor"] for d in everyone["doctors"]])

    def test_the_customer_and_short_address(self):
        rows = self.by_no(self.bills(doctors=["DR A"]))
        self.assertEqual((rows["SB2"]["patient"], rows["SB2"]["address"]), ("PATIENT TWO", "DEMO LANE, DEMO TOWN"))
        self.assertEqual(rows["SB1"]["address"], "")

    def test_a_period(self):
        r = self.bills(doctors=["DR A"], **{"from": "2026-10-05", "to": "2026-10-05"})
        self.assertEqual([x["bill_no"] for x in r["rows"]], ["SB5"])
        with self.assertRaises(rep.ReportError):
            self.bills(**{"from": "2026-10-06", "to": "2026-10-01"})


class TheFiles(BillWise):
    def test_csv_like_the_other_software(self):
        out = rep.export_report(self.conn, {"from": FROM, "to": TO, "mode": "bills", "doctors": ["DR A"], "format": "csv"})
        self.assertTrue(out["ok"], out)
        text = base64.b64decode(out["content_base64"]).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], [dbp.TITLE])
        self.assertIn(dbp.COLUMNS, rows)
        self.assertIn(["02/10/2026", "SB2", "PATIENT TWO", "DEMO LANE, DEMO TOWN", "100.0", "30.0", "70.0", "70.0"], rows)
        self.assertIn(["Totals", "", "", "", "188.0", "70.0", "118.0", "62.77"], rows)
        self.assertIn(["Doctors", "DR A"], rows)

    def test_excel_and_nothing_found(self):
        out = rep.export_report(self.conn, {"from": FROM, "to": TO, "mode": "bills", "format": "xlsx"})
        self.assertTrue(out["ok"])
        self.assertTrue(out["filename"].endswith(".xlsx"))
        none = rep.export_report(self.conn, {"from": FROM, "to": TO, "mode": "bills", "doctors": ["DR NOBODY"],
                                             "format": "csv"})
        self.assertFalse(none["ok"])


if __name__ == "__main__":
    unittest.main()
