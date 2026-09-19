"""An Online purchase delete takes back what the purchase added, even when its arithmetic fails.

The queue's purchase delete works out each line's stock with the same arithmetic the save
used (strips times tablets per strip for a tablet, capsule or bolus line). When that
arithmetic raised -- a catalogue that could not be read, say -- its fallback took back
qty + free_qty, in strips. A delete of 5 strips (+1 free) of 10 tablets therefore removed 6
tablets while the save had added 60, leaving 54 tablets of phantom stock and a purchase_delete
op that undoes less than the purchase op (store 4: medicines 17500-17508).

The server client is patched; nothing reaches a server.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

from core import online_mutation_queue  # noqa: E402


class _Delete(unittest.TestCase):
    def delete(self, line: dict, stock: float = 100.0) -> dict:
        doc = {"id": 42, "client_uuid": "p-42", "supplier_id": 3, "items": [dict(line)]}
        med = {"id": int(line["medicine_id"]), "name": line.get("name"), "stock_qty": stock,
               "version": 4}
        pushed: list[dict] = []
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.server_crud.get_doc",
                           side_effect=lambda col, _id: dict(doc) if col == "purchases" else dict(med)),
                mock.patch("core.online_catalog.medicine_by_id", return_value=dict(med)),
                mock.patch("core.online_catalog.patch_docs", return_value=None),
                mock.patch("core.server_crud.delete_purchase_online", return_value=None),
                mock.patch("core.server_crud.upsert_docs",
                           side_effect=lambda col, docs: pushed.extend(docs) or {}),
                mock.patch("core.server_crud._device_id", return_value="dev"),
                mock.patch("core.purchase_service._normalize_purchase_item_stock_fields",
                           side_effect=RuntimeError("catalogue unreadable")),
                mock.patch.object(online_mutation_queue, "_recompute_supplier_balance_online",
                                  return_value=None),
            ):
                stack.enter_context(patch)
            online_mutation_queue._flush_purchase_delete({"purchase_id": 42}, 42)
        [medicine] = pushed
        return medicine


class WhenTheStockArithmeticFails(_Delete):
    def test_a_strip_line_is_taken_back_in_tablets(self):
        med = self.delete({"medicine_id": 9, "name": "PARA TAB", "type": "Tablet", "qty": 5,
                           "free_qty": 1, "unit": "10", "tablets_per_stripe": 10})
        self.assertEqual(med["stock_ops"][0]["qty_delta"], -60,
                         "the delete took back strips while the purchase added tablets")
        self.assertEqual(med["stock_qty"], 40.0)

    def test_a_strip_line_with_only_its_pack_text_is_taken_back_in_tablets(self):
        med = self.delete({"medicine_id": 9, "name": "PARA TAB", "type": "Capsule", "qty": 2,
                           "free_qty": 0, "unit": "15"})
        self.assertEqual(med["stock_ops"][0]["qty_delta"], -30)

    def test_a_bottle_line_is_still_taken_back_in_bottles(self):
        med = self.delete({"medicine_id": 5, "name": "COUGH SYRUP", "type": "Syrup", "qty": 3,
                           "free_qty": 1, "unit": "100ML"})
        self.assertEqual(med["stock_ops"][0]["qty_delta"], -4)
        self.assertEqual(med["stock_qty"], 96.0)


if __name__ == "__main__":
    unittest.main()
