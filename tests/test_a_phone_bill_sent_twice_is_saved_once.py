"""The same phone export sent twice must not put its stock on the shelf twice.

The Purchase page refuses a supplier bill number that is already saved; the Mobile Import
saved it again, so a resend (or a second scan of the QR) doubled the stock and the dues.
"""
from __future__ import annotations

import unittest
from unittest import mock

from core import mobile_import_apply, purchase_service

DATA = {
    "export_type": "purchases",
    "suppliers": [{"name": "S PHARMA"}],
    "purchases": [{
        "supplier_name": "S PHARMA", "bill_number": "INV-9", "purchase_date": "2026-09-14",
        "items": [{"name": "COREX", "type": "Syrup", "qty": 10, "rate": 100, "mrp": 120,
                   "batch_no": "B1", "expiry_date": "04/28"}],
    }],
}


class APhoneBillSentTwice(unittest.TestCase):
    def test_saved_bill_is_refused_not_saved_again(self):
        conn = mock.MagicMock()
        with mock.patch("core.save_warnings.find_saved_supplier_bill",
                        return_value={"purchase_no": "12/FY2026-27"}) as found, \
                mock.patch.object(mobile_import_apply, "_pc_is_wholesale", return_value=False), \
                mock.patch.object(purchase_service, "get_or_create_supplier") as sup, \
                mock.patch.object(purchase_service, "save_purchase") as save:
            out = mobile_import_apply._apply_purchases(conn, DATA)
        found.assert_called_once()
        save.assert_not_called()
        sup.assert_not_called()
        self.assertEqual(out["saved"], 0)
        self.assertIn("already saved", out["errors"][0])

    def test_new_bill_still_saves(self):
        conn = mock.MagicMock()
        with mock.patch("core.save_warnings.find_saved_supplier_bill", return_value=None), \
                mock.patch.object(mobile_import_apply, "_pc_is_wholesale", return_value=False), \
                mock.patch.object(purchase_service, "get_or_create_supplier", return_value=1), \
                mock.patch.object(purchase_service, "get_or_create_medicine", return_value=2), \
                mock.patch.object(mobile_import_apply, "import_line_pack", return_value={}), \
                mock.patch.object(purchase_service, "get_supplier_due", return_value=(0.0, 0.0)), \
                mock.patch.object(purchase_service, "save_purchase", return_value="1") as save:
            out = mobile_import_apply._apply_purchases(conn, DATA)
        save.assert_called_once()
        self.assertEqual(out["saved"], 1)


if __name__ == "__main__":
    unittest.main()
