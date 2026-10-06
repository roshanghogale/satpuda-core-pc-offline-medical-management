"""GST made easy (6 Oct 2026): one-page summary, the month's GST to pay with ITC carried
forward, medicines without HSN in one list, Tally Prime vouchers, one zip for the CA.

The store is the one test_gst_reports builds (bills, a return, supplier bills, a supplier
return); every figure here must agree with that module's GSTR-3B.
"""
import base64
import io
import os
import re
import sys
import unittest
import zipfile
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import gst_extras as gx  # noqa: E402
from core import gst_reports as gr  # noqa: E402
from tests.test_gst_reports import _Store  # noqa: E402


class TheSetOffOrder(unittest.TestCase):
    """Sections 49 / 49A and rule 88A."""

    def test_cgst_credit_never_pays_sgst(self):
        so = gx.set_off({"igst": 0, "cgst": 0, "sgst": 100}, {"igst": 0, "cgst": 100, "sgst": 0})
        self.assertEqual(100.0, so["cash"]["sgst"])
        self.assertEqual(100.0, so["carried"]["cgst"])

    def test_igst_credit_goes_to_igst_first_then_cgst_and_sgst(self):
        so = gx.set_off({"igst": 30, "cgst": 50, "sgst": 50}, {"igst": 100, "cgst": 0, "sgst": 0})
        self.assertEqual({"igst": 0.0, "cgst": 0.0, "sgst": 30.0}, so["cash"])
        self.assertEqual(0.0, so["carried_total"])

    def test_cgst_and_sgst_credit_pay_igst_after_their_own(self):
        so = gx.set_off({"igst": 40, "cgst": 10, "sgst": 10}, {"igst": 0, "cgst": 30, "sgst": 30})
        self.assertEqual(0.0, so["cash_total"])
        self.assertEqual(0.0, so["carried_total"])

    def test_more_credit_than_tax_is_carried(self):
        so = gx.set_off({"cgst": 10, "sgst": 10}, {"cgst": 25, "sgst": 25})
        self.assertEqual(0.0, so["cash_total"])
        self.assertEqual(30.0, so["carried_total"])


class TheSummaryAndTheMonths(_Store):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(gx.gr, "_online", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_the_summary_agrees_with_gstr_3b(self):
        s = gx.one_page(self.conn, "2026-09-01", "2026-09-30")
        t3b = self.report["gstr3b"]
        out = t3b["3.1(a) Outward taxable supplies"]
        self.assertEqual(round(out["cgst"] + out["sgst"] + out["igst"], 2), s["tax_on_sales_total"])
        net = t3b["4(C) Net ITC available"]
        self.assertEqual(round(net["cgst"] + net["sgst"] + net["igst"], 2), s["itc_total"])
        self.assertEqual(4, s["bills"])

    def test_the_credit_left_over_is_carried_to_the_next_month(self):
        months = gx.monthly_ledger(self.conn, "2026-10-31")
        self.assertEqual("2026-04", months[0]["month"])
        sep, octo = months[-2], months[-1]
        self.assertEqual(sep["carried_forward"], octo["brought_forward"])
        self.assertEqual(0.0, octo["tax_on_sales"])
        # September: more ITC than tax on sales, so nothing to pay and credit carried
        self.assertEqual(0.0, sep["cash_to_pay"])
        self.assertGreater(sep["carried_forward"], 0)


class OneListOfMedicinesWithoutHsn(_Store):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(gx.gr, "_online", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_a_medicine_is_listed_once_and_fixed_once(self):
        rows = gx.missing_hsn(self.conn, "2026-09-01", "2026-09-30")
        self.assertEqual(["COTTON ROLL"], [r["name"] for r in rows])
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            res = gx.save_hsn(self.conn, [{"medicine_id": rows[0]["medicine_id"], "hsn": "3005"}])
        self.assertTrue(res["ok"], res)
        self.assertEqual([], gx.missing_hsn(self.conn, "2026-09-01", "2026-09-30"))

    def test_a_wrong_length_is_refused(self):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            res = gx.save_hsn(self.conn, [{"medicine_id": 3, "hsn": "30"}])
        self.assertFalse(res["ok"])
        self.assertEqual(0, res["saved"])


class TallyPrime(_Store):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(gx.gr, "_online", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.t = gx.tally_files(self.conn, "2026-09-01", "2026-09-30")

    def _vouchers(self):
        return re.findall(r"<VOUCHER VCHTYPE=\"([^\"]+)\".*?</VOUCHER>", self.t["vouchers"], re.S), \
            re.findall(r"<VOUCHER .*?</VOUCHER>", self.t["vouchers"], re.S)

    def test_every_voucher_balances_to_the_paisa(self):
        _, bodies = self._vouchers()
        self.assertTrue(bodies)
        for v in bodies:
            total = sum(Decimal(a) for a in re.findall(r"<AMOUNT>(-?[0-9.]+)</AMOUNT>", v))
            self.assertEqual(Decimal("0"), total, v[:200])

    def test_sales_returns_purchases_and_supplier_returns_are_all_there(self):
        kinds, _ = self._vouchers()
        self.assertEqual(4, kinds.count("Sales"))
        self.assertEqual(2, kinds.count("Credit Note"))
        self.assertEqual(3, kinds.count("Purchase"))
        self.assertEqual(1, kinds.count("Debit Note"))

    def test_the_tax_in_tally_is_the_tax_in_gstr_3b(self):
        out = Decimal("0")
        for led in ("Output CGST", "Output SGST", "Output IGST"):
            for v in re.findall(r"<VOUCHER .*?</VOUCHER>", self.t["vouchers"], re.S):
                for amt in re.findall(rf"<LEDGERNAME>{led}</LEDGERNAME><ISDEEMEDPOSITIVE>\w+</ISDEEMEDPOSITIVE>"
                                      r"<AMOUNT>(-?[0-9.]+)</AMOUNT>", v):
                    out += Decimal(amt)   # credits positive, credit-note debits negative
        a = self.report["gstr3b"]["3.1(a) Outward taxable supplies"]
        self.assertEqual(Decimal(str(round(a["cgst"] + a["sgst"] + a["igst"], 2))), out)

    def test_the_b2b_customer_gets_a_ledger_with_the_gstin(self):
        self.assertIn(f"<PARTYGSTIN>{self.b2b_gstin}</PARTYGSTIN>", self.t["masters"])
        self.assertIn("<GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>", self.t["masters"])


class OneZipForTheCa(_Store):
    def test_everything_is_in_it(self):
        with mock.patch.object(gx.gr, "_online", return_value=False):
            res = gr.export(self.conn, {"from": "2026-09-01", "to": "2026-09-30", "format": "ca_zip"})
        names = zipfile.ZipFile(io.BytesIO(base64.b64decode(res["content_base64"]))).namelist()
        for part in (".xlsx", ".pdf", ".json", "_saransh.txt", "Tally_1_ledgers", "Tally_2_vouchers",
                     "Tally_import_kase_karayche.txt"):
            self.assertTrue(any(part in n for n in names), (part, names))


if __name__ == "__main__":
    unittest.main()
