"""The phone loader and the Opening Stock page are one form in two places.

A shop starting on Satpuda has shelves no purchase bill ever brought in. It can
type that shelf on the Satpuda Loader app and send it over by QR, or type it
straight into Settings - Opening Stock. Neither creates a supplier, a purchase
or a payment; both hand the same rows to the same engine call.

So whatever one collects, the other must collect, and the engine must read it.
When the two drift, a shelf loaded from the phone quietly arrives missing the
columns only the other form knew about -- which is how HSN, Schedule and
Content / Drug came in blank: the loader's payload carried them and its form
never asked, so they were "" on every row it ever sent.
"""
from __future__ import annotations

import unittest

from core.opening_stock_service import COLUMNS, apply, parse  # noqa: E402

# Exactly what LoaderRules.row() puts on the wire, for one liquid.
LOADER_ROW = {
    "name": "CALGOPHOS",
    "type": "Liquid",
    "unit": "1LTR",
    "batch_no": "GK152",
    "expiry_date": "04/28",
    "stock_qty": 3,
    "extra_medicine": 0,
    "stock_units": 3,
    "mrp": 715.0,
    "rate": 518.0,
    "gst_percent": 12.0,
    "hsn_code": "23099090",
    "manufacturer": "VIR",
    "schedule": "H",
    "content_drug": "Calcium, Phosphorus, Vitamin D3",
    "supplier_name": "SHIWANI AGENCIES",
}

# The same medicine as the Opening Stock page sends it.
PAGE_ROW = dict(LOADER_ROW)
PAGE_ROW.pop("stock_units")


class TheLoaderAndThePage(unittest.TestCase):

    def read(self, row):
        out = parse({"rows": [row]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["count"], 1, out)
        return out["rows"][0]

    def test_read_a_row_to_exactly_the_same_thing(self):
        self.assertEqual(self.read(LOADER_ROW), self.read(PAGE_ROW))

    def test_keep_every_column_the_loader_collects(self):
        row = self.read(LOADER_ROW)
        self.assertEqual(row["name"], "CALGOPHOS")
        self.assertEqual(row["type"], "Liquid")
        self.assertEqual(row["unit"], "1LTR")
        self.assertEqual(row["batch_no"], "GK152")
        self.assertEqual(row["stock_qty"], 3)
        self.assertEqual(row["mrp"], 715.0)
        self.assertEqual(row["rate"], 518.0)
        self.assertEqual(row["gst_percent"], 12.0)
        self.assertEqual(row["hsn_code"], "23099090")
        self.assertEqual(row["manufacturer"], "VIR")
        self.assertEqual(row["schedule"], "H")
        self.assertEqual(row["content_drug"], "Calcium, Phosphorus, Vitamin D3")

    def test_the_three_that_used_to_arrive_blank_are_real_columns(self):
        for field in ("hsn_code", "schedule", "content_drug"):
            self.assertIn(field, COLUMNS, field)

    def test_read_an_expiry_however_the_phone_spelled_it(self):
        # The loader normalises now, but rows sent by an older build still land.
        for raw in ("04/28", "0428", "04-28", "04/2028", "2028-04-01"):
            with self.subTest(raw=raw):
                row = self.read({**LOADER_ROW, "expiry_date": raw})
                # parse keeps the text; the medicine write converts it.
                from core.expiry_text import expiry_to_db
                self.assertEqual(expiry_to_db(row["expiry_date"]), "2028-04-01")

    def test_a_row_with_no_batch_still_goes_on_the_shelf(self):
        row = self.read({**LOADER_ROW, "batch_no": ""})
        self.assertEqual(row["batch_no"], "-")

    def test_a_row_with_no_name_is_refused_and_says_why(self):
        out = parse({"rows": [{**LOADER_ROW, "name": ""}]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 0)
        self.assertTrue(out["problems"], "a nameless row was dropped in silence")

    def test_neither_form_can_ask_for_a_bill_or_a_payment(self):
        """Opening stock is stock, not a purchase. There is nowhere to put one."""
        for word in ("bill_number", "purchase_date", "amount_paid", "cash_paid",
                     "online_paid", "due", "supplier_id"):
            self.assertNotIn(word, COLUMNS, word)

    def test_the_supplier_is_a_note_on_the_medicine(self):
        """Named for reference, so the shop knows where a shelf came from."""
        self.assertIn("supplier_name", COLUMNS)
        row = self.read(LOADER_ROW)
        self.assertEqual(row["supplier_name"], "SHIWANI AGENCIES")

    def test_a_row_with_no_supplier_is_perfectly_fine(self):
        row = self.read({**LOADER_ROW, "supplier_name": ""})
        self.assertEqual(row["supplier_name"], "")
        self.assertEqual(row["name"], "CALGOPHOS")

    def test_the_supplier_never_becomes_a_supplier_record(self):
        """A name here must not reach get_or_create_supplier: no party, no due."""
        from unittest import mock
        import core.purchase_service as ps
        with mock.patch.object(ps, "get_or_create_supplier") as made:
            parse({"rows": [LOADER_ROW]})
        made.assert_not_called()

    def test_nothing_is_written_without_a_database(self):
        out = apply(None, {"rows": []})
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
