"""A purchase imported from the phone is stocked by the catalogue pack unless it carries its own.

The phone's purchase export (MobileExportService.buildPurchasesPayload) writes type, qty, free_qty,
batch, rate and so on for each line, but no tablets_per_stripe, unit or quantity_value. Desktop
"Import from mobile" (core/mobile_import_apply.py and the Classic ui/shared/import_from_mobile.py)
filled the gap with tablets_per_stripe 1 and quantity_value "1". Since a purchase line is stocked by
its own pack (X3, _line_has_own_pack), that invented 1 became the line's pack: 3 strips of a
strip-of-10 medicine imported from the phone went on the shelf as 3 tablets, where the catalogue
pack used to give 30. Offline it was never looked up at all, and a syrup's "100ML" pack was written
over with "1".

Now a line with no pack of its own takes the catalogue's (the store's medicine row), and a pack
the export does carry is honoured as typed -- the owner's rule, stock per line pack.

The server client and the store query client are patched; nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from core import db_setup, mobile_import_apply, purchase_service  # noqa: E402

CATALOGUE = {
    101: {"id": 101, "local_id": 101, "name": "DOLO 650", "type": "Tablet", "unit": "10",
          "stock_qty": 0, "version": 2},
    102: {"id": 102, "local_id": 102, "name": "PAN 40", "type": "Tablet", "unit": "10",
          "stock_qty": 0, "version": 2},
    103: {"id": 103, "local_id": 103, "name": "COREX SYRUP", "type": "Syrup", "unit": "100ML",
          "stock_qty": 0, "version": 2},
}
IDS = {"DOLO 650": 101, "PAN 40": 102, "COREX SYRUP": 103, "NEWMED 5": 104}


def _phone_line(name, kind, qty, **pack):
    """One line exactly as MobileExportService writes it, plus any pack fields given."""
    line = {"name": name, "medicine_name": name, "type": kind, "qty": float(qty), "free_qty": 0.0,
            "batch_no": "B1", "expiry_date": "12/27", "mrp": 35.0, "rate": 25.0, "gst_pct": 5.0,
            "gst_percent": 5.0, "hsn_code": "3004", "schedule": "", "manufacturer": "MICRO LABS"}
    line.update(pack)
    return line


def _export(*lines):
    return {"export_type": "purchases",
            "suppliers": [{"name": "S PHARMA", "address": "", "phone": "", "gstin": "",
                           "dl_numbers": ""}],
            "purchases": [{"supplier_name": "S PHARMA", "bill_number": "INV-9",
                           "purchase_date": "2026-09-14", "purchase_no": "7", "items": list(lines)}]}


class OnlineImport(unittest.TestCase):
    def setUp(self):
        self.sent: list[dict] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch.object(purchase_service, "get_or_create_supplier", return_value=279),
            mock.patch.object(purchase_service, "get_supplier_due", return_value=(0.0, 0.0)),
            mock.patch.object(purchase_service, "get_or_create_medicine",
                              side_effect=lambda conn, name, *a: IDS[name]),
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
                       side_effect=lambda mid: dict(CATALOGUE[int(mid)]) if int(mid) in CATALOGUE else None),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       return_value={"id": 279, "local_id": 279, "name": "S PHARMA"}),
            mock.patch("core.server_crud.save_new_purchase_online",
                       side_effect=lambda doc: self.sent.append(doc) or 5001),
        ):
            stack.enter_context(patch)

    def imported(self, *lines):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        out = mobile_import_apply.apply_mobile_data(conn, _export(*lines))
        self.assertEqual(out.get("errors"), [], out)
        self.assertEqual(out.get("saved"), 1, out)
        [doc] = self.sent
        meds = {int(m["id"]): m for m in doc["_medicines"]}
        moved = {mid: sum(int(op["qty_delta"]) for op in m.get("stock_ops") or [])
                 for mid, m in meds.items()}
        return doc, meds, moved

    def test_a_strip_line_with_no_pack_is_stocked_by_the_catalogue_pack(self):
        doc, _meds, moved = self.imported(_phone_line("DOLO 650", "Tablet", 3))
        self.assertEqual(moved[101], 30, "3 strips of 10 went on the shelf as 3 tablets")
        self.assertEqual(str(doc["items"][0]["unit"]), "10")

    def test_a_pack_the_export_carries_is_honoured(self):
        _doc, _meds, moved = self.imported(
            _phone_line("PAN 40", "Tablet", 3, tablets_per_stripe=15, quantity_value="15"))
        self.assertEqual(moved[102], 45)

    def test_a_loose_pack_the_export_carries_is_honoured(self):
        _doc, _meds, moved = self.imported(
            _phone_line("DOLO 650", "Tablet", 3, tablets_per_stripe=1, quantity_value="1"))
        self.assertEqual(moved[101], 3)

    def test_a_syrup_keeps_its_catalogue_pack(self):
        _doc, meds, moved = self.imported(_phone_line("COREX SYRUP", "Syrup", 2))
        self.assertEqual(moved[103], 2)
        self.assertEqual(meds[103]["unit"], "100ML", "the syrup's pack was written over with 1")

    def test_a_medicine_the_catalogue_has_no_pack_for_is_counted_as_it_comes(self):
        _doc, _meds, moved = self.imported(_phone_line("NEWMED 5", "Tablet", 3))
        self.assertEqual(moved[104], 3)


class OfflineImport(unittest.TestCase):
    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        cur = conn.execute(
            "INSERT INTO medicines (name, type, batch_no, expiry_date, unit, stock_qty, mrp, rate) "
            "VALUES ('DOLO 650', 'Tablet', 'B1', '2027-12-01', '10', 0, 35, 25)")
        self.mid = int(cur.lastrowid)
        conn.commit()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            # Offline: the store's catalogue is this machine's medicines table, not the server.
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.server_crud.get_doc", return_value=None),
        ):
            stack.enter_context(patch)

    def stock(self):
        return float(self.conn.execute(
            "SELECT stock_qty FROM medicines WHERE id=?", (self.mid,)).fetchone()[0])

    def test_the_engine_import_takes_the_medicines_pack(self):
        out = mobile_import_apply.apply_mobile_data(self.conn, _export(_phone_line("DOLO 650", "Tablet", 3)))
        self.assertEqual(out.get("errors"), [], out)
        self.assertEqual(self.stock(), 30.0)

    def test_the_engine_import_honours_a_pack_the_export_carries(self):
        out = mobile_import_apply.apply_mobile_data(
            self.conn,
            _export(_phone_line("DOLO 650", "Tablet", 3, tablets_per_stripe=1, quantity_value="1")))
        self.assertEqual(out.get("errors"), [], out)
        self.assertEqual(self.stock(), 3.0)

    def test_the_classic_import_takes_the_medicines_pack(self):
        try:
            from ui.shared.import_from_mobile import ImportFromMobilePage
        except Exception as exc:  # pragma: no cover - no Tk on this machine
            self.skipTest(f"Classic UI not importable: {exc}")
        page = SimpleNamespace(conn=self.conn)
        purchase = _export(_phone_line("DOLO 650", "Tablet", 3))["purchases"][0]
        ImportFromMobilePage._save_purchase(page, None, purchase, {})
        self.assertEqual(self.stock(), 30.0)


if __name__ == "__main__":
    unittest.main()
