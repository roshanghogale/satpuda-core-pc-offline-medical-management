"""A bill loaded from the phone must arrive whole.

Two things went missing on the way in, and neither raised a word:

  * the expiry. The loader app's box asked for MM/YYYY, nothing typed the
    slash, so a register went out as rows of "0428" -- and every converter on
    the PC tested for a "/" and returned "". The medicine landed on the shelf
    with an empty expiry column.

  * the money. The import rebuilt each purchase with rounding 0 and no
    delivery charge, and the phone's own export sent neither those nor the
    line discount nor what had been paid. A bill the shop had already settled
    arrived fully due, at the wrong total, and the supplier's account was out
    by the difference.

The mocks stand in for the server; nothing here reaches one.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import mobile_import_apply, purchase_service  # noqa: E402

SYRUP = {"id": 103, "local_id": 103, "name": "COREX SYRUP", "type": "Syrup",
         "unit": "100ML", "stock_qty": 0, "version": 2}


def phone_line(**over):
    """One line as the loader/phone export writes it."""
    line = {
        "name": "COREX SYRUP", "medicine_name": "COREX SYRUP", "type": "Syrup",
        "qty": 10.0, "free_qty": 0.0, "batch_no": "B1",
        "expiry_date": "04/28",
        "mrp": 120.0, "rate": 100.0, "gst_pct": 12.0, "gst_percent": 12.0,
        "hsn_code": "3004", "schedule": "", "manufacturer": "PFIZER",
    }
    line.update(over)
    return line


def phone_bill(lines, **over):
    bill = {
        "supplier_name": "S PHARMA", "bill_number": "INV-9",
        "purchase_date": "2026-09-14", "purchase_no": "7",
        "gst_calc_method": "discount_before_gst",
        "items": list(lines),
    }
    bill.update(over)
    return {
        "export_type": "purchases",
        "suppliers": [{"name": "S PHARMA", "address": "", "phone": "",
                       "gstin": "", "dl_numbers": ""}],
        "purchases": [bill],
    }


class APhoneBill(unittest.TestCase):

    def setUp(self):
        self.sent: list[dict] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch.object(purchase_service, "get_or_create_supplier", return_value=279),
            mock.patch.object(purchase_service, "get_supplier_due", return_value=(0.0, 0.0)),
            mock.patch.object(purchase_service, "get_or_create_medicine", return_value=103),
            mock.patch.object(purchase_service, "_ensure_purchase_line_medicine_ids",
                              return_value=None),
            mock.patch.object(purchase_service, "hide_online_zero_stock_duplicates",
                              return_value=0),
            mock.patch("core.server_api.store_token_for_active", return_value="t"),
            mock.patch("core.server_api.allocate_fy",
                       return_value={"fy_start_year": 2026, "fy_serial": 12,
                                     "purchase_no": "12/FY2026-27"}),
            mock.patch("core.server_crud.allocate_id", return_value=5001),
            mock.patch("core.server_crud.get_doc", return_value=None),
            mock.patch("core.server_crud._device_id", return_value="dev"),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda mid: dict(SYRUP) if int(mid) == 103 else None),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       return_value={"id": 279, "local_id": 279, "name": "S PHARMA"}),
            mock.patch("core.server_crud.save_new_purchase_online",
                       side_effect=lambda doc: self.sent.append(doc) or 5001),
        ):
            stack.enter_context(patch)

    def imported(self, export):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        out = mobile_import_apply.apply_mobile_data(conn, export)
        self.assertEqual(out.get("errors"), [], out)
        self.assertEqual(out.get("saved"), 1, out)
        return self.sent[-1]

    # ── the expiry ──────────────────────────────────────────────────────────

    def test_keeps_an_expiry_that_nobody_typed_a_slash_into(self):
        doc = self.imported(phone_bill([phone_line(expiry_date="0428")]))
        self.assertEqual(doc["items"][0]["expiry_date"], "2028-04-01")

    def test_reads_every_shape_the_phone_can_send_the_same_way(self):
        for raw in ("04/28", "0428", "04-28", "4/28", "04/2028", "2028-04-01"):
            with self.subTest(raw=raw):
                self.sent.clear()
                doc = self.imported(phone_bill([phone_line(expiry_date=raw)]))
                self.assertEqual(doc["items"][0]["expiry_date"], "2028-04-01")

    def test_puts_that_expiry_on_the_medicine_too(self):
        doc = self.imported(phone_bill([phone_line(expiry_date="0428")]))
        [med] = [m for m in doc["_medicines"] if int(m["id"]) == 103]
        self.assertEqual(str(med["expiry_date"])[:10], "2028-04-01")

    def test_a_row_with_no_expiry_is_left_empty_not_guessed(self):
        doc = self.imported(phone_bill([phone_line(expiry_date="")]))
        self.assertEqual(doc["items"][0]["expiry_date"] or "", "")

    # ── the money ───────────────────────────────────────────────────────────

    def test_keeps_the_line_discount_the_bill_carried(self):
        doc = self.imported(phone_bill([phone_line(item_discount=10.0)]))
        self.assertEqual(doc["items"][0]["discount_pct"], 10.0)
        # 10 x 100 less 10% = 900 taxable, not the full 1000.
        self.assertEqual(doc["subtotal"], 900.0)

    def test_adds_the_delivery_charge_to_what_the_shop_owes(self):
        plain = self.imported(phone_bill([phone_line()]))
        self.sent.clear()
        with_delivery = self.imported(phone_bill([phone_line()], expenditure=120.0))
        self.assertEqual(
            round(with_delivery["final_amount"] - plain["final_amount"], 2), 120.0,
            "the delivery charge on the bill was dropped",
        )

    def test_keeps_the_rounding_the_supplier_wrote_on_the_bill(self):
        doc = self.imported(phone_bill([phone_line()], rounding=-0.4))
        self.assertEqual(doc["rounding"], -0.4)

    def test_a_bill_already_paid_does_not_arrive_as_due(self):
        doc = self.imported(
            phone_bill([phone_line()], cash_paid=400.0, online_paid=100.0),
        )
        self.assertEqual(doc["cash_paid_at_entry"], 400.0)
        self.assertEqual(doc["online_paid_at_entry"], 100.0)
        self.assertEqual(doc["amount_paid"], 500.0)
        self.assertEqual(round(doc["due"], 2), round(doc["final_amount"] - 500.0, 2))

    def test_the_bill_discount_still_comes_through(self):
        doc = self.imported(phone_bill([phone_line()], overall_discount=50.0))
        self.assertEqual(doc["overall_discount"], 50.0)
        self.assertEqual(doc["subtotal"], 950.0)


if __name__ == "__main__":
    unittest.main()
