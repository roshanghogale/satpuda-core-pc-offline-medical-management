"""Medicines loaded from the phone must land on the PC at the count the phone holds.

The phone keeps ``medicines.stock_qty`` in base units — tablets (Android InventoryService writes
``strips * tabletsPerStrip + extraTablets``). The desktop reads that same field as STRIPS and
multiplies it by the pack: ``core/stock_utils.android_import_stock`` — "Android stock_qty = strips;
unit = tablets per strip; extra_medicine = loose units", and the Classic screen's own help text has
said so all along (ui/shared/import_from_mobile.py: '"stock_qty": 10, "unit": "10",
"extra_medicine": 4'). The phone's export ignored that contract and sent the tablet count raw with
no extra_medicine, so 100 tablets of a strip-of-10 went on the shelf as 1000.

The fix is on the phone (MobileExportService.medicineExport decomposes into strips + loose), so it
works against every desktop already in a shop, including the v1.0.1 builds that will never be
updated. This test pins the desktop half of that contract: fed the shape the fixed phone now sends,
the store ends up holding exactly the number the phone held.

The rows below are written the way the Kotlin now builds them, and the Android side has the mirror
of this test (app/src/test/java/com/selling/satpudacore/export/MobileExportMedicineStockTest.kt).

Offline mode, a throwaway in-memory store, no live store and no network.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup, mobile_import_apply
from core.stock_utils import android_import_stock


def _phone_row(name, kind, held, pack):
    """One medicine exactly as the fixed MobileExportService.medicineExport writes it."""
    strips, loose, unit = held, 0, pack
    if kind.strip().lower() in ("tablet", "bolus", "capsule"):
        size = max(1, int(pack))
        strips, loose, unit = held // size, held % size, str(size)
    return {
        "name": name, "type": kind, "batch_no": "B1", "expiry_date": "2027-12-01",
        "stock_qty": strips, "extra_medicine": loose, "unit": unit, "stock_units": held,
        "mrp": 35.0, "rate": 25.0, "manufacturer": "MICRO LABS", "hsn_code": "3004",
        "schedule": "H", "gst_percent": 12.0, "gst_pct": 12.0, "content_drug": "",
        "is_hidden": False,
    }


def _export(*rows):
    return {"export_type": "medicines", "device_name": "Satpuda Core Android",
            "export_date": "2026-09-16", "medicines": list(rows)}


class PhoneMedicineLoad(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        patch = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    def stock(self, name):
        row = self.conn.execute(
            "SELECT stock_qty FROM medicines WHERE name=? ORDER BY id DESC LIMIT 1", (name,)
        ).fetchone()
        return None if row is None else int(row[0])

    def test_the_shelf_holds_what_the_phone_held(self):
        cases = [
            ("DOLO 650", "Tablet", 100, 10),
            ("PAN 40", "Tablet", 23, 10),
            ("AMOXY 500", "Capsule", 45, 15),
            ("NILVERM", "Bolus", 7, 4),
            ("SINGLE TAB", "Tablet", 9, 10),
            ("COREX SYRUP", "Syrup", 6, "100 ML"),
            ("PACK OF THIRTY", "Tablet Pack", 12, "30"),
        ]
        out = mobile_import_apply.apply_mobile_data(
            self.conn, _export(*[_phone_row(*c) for c in cases]))
        self.assertEqual(out.get("errors"), [], out)
        for name, _kind, held, _pack in cases:
            self.assertEqual(self.stock(name), held, f"{name} did not land at {held}")

    def test_loading_the_same_list_twice_does_not_double_the_shelf(self):
        rows = _export(_phone_row("DOLO 650", "Tablet", 100, 10))
        mobile_import_apply.apply_mobile_data(self.conn, rows)
        mobile_import_apply.apply_mobile_data(self.conn, rows)
        self.assertEqual(self.stock("DOLO 650"), 100)

    def test_the_desktop_contract_is_strips_plus_loose(self):
        # What the phone is now written against. If this ever changes, the APK must change with it.
        self.assertEqual(android_import_stock("Tablet", 10, "10", 0), 100)
        self.assertEqual(android_import_stock("Tablet", 2, "10", 3), 23)
        self.assertEqual(android_import_stock("Syrup", 6, "100 ML", 0), 6)
        self.assertEqual(android_import_stock("Tablet Pack", 12, "30", 0), 12)


if __name__ == "__main__":
    unittest.main()
