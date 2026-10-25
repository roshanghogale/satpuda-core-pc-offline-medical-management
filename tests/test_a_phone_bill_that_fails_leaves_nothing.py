"""Mobile Import (phone purchase export) on Offline / offline-first: a bill is all or nothing.

A bill that failed partway -- a bad figure on its third line, a line with no name -- kept the
supplier and the medicines it had made (or rewritten) before the failure: the loop's final
commit saved them and offline-first sent them to every device. And a name-only supplier in the
export wiped the address / phone / GSTIN / DL the PC had saved for that supplier.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, mobile_import_apply


def _line(name, qty=10, batch="B1"):
    return {"name": name, "type": "Syrup", "qty": qty, "rate": 100, "mrp": 120,
            "batch_no": batch, "expiry_date": "04/28"}


def _bill(supplier_name, bill_no, items, **extra):
    return {"supplier_name": supplier_name, "bill_number": bill_no, "purchase_date": "2026-10-20",
            "items": items, **extra}


class _Offline(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        self.conn.execute("INSERT INTO suppliers (name, address, phone, gstin, dl_numbers) "
                          "VALUES ('OLD AGENCY', 'MAIN ROAD', '98220', '27ABCDE1234F1Z5', 'DL-1')")
        self.conn.execute("INSERT INTO medicines (name, type, batch_no, mrp, rate, manufacturer, stock_qty) "
                          "VALUES ('COREX', 'Syrup', 'B1', 110, 90, 'PFIZER', 0)")
        self.conn.commit()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for p in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch.object(mobile_import_apply, "_pc_is_wholesale", return_value=False),
            mock.patch("core.sync_coordinator.after_purchase_saved", return_value=None),
            mock.patch("core.purchase_service.upsert_master_medicine", return_value=None, create=True),
            mock.patch("core.app_prefs.load_app_mode", return_value="veterinary"),
        ):
            stack.enter_context(p)

    def run_import(self, *bills):
        return mobile_import_apply._apply_purchases(self.conn, {"export_type": "purchases",
                                                                "purchases": list(bills)})

    def names(self, table):
        return sorted(r[0] for r in self.conn.execute(f"SELECT name FROM {table}"))


class ABillThatFailsPartway(_Offline):
    def test_leaves_no_supplier_and_no_medicine_behind(self):
        out = self.run_import(
            _bill("NEW AGENCY", "INV-1", [_line("AZEE"), _line("COREX"), _line("CROCIN", qty="ten")]),
        )
        self.assertEqual(out["saved"], 0)
        self.assertEqual(len(out["errors"]), 1)
        self.assertEqual(self.names("suppliers"), ["OLD AGENCY"])
        self.assertEqual(self.names("medicines"), ["COREX"])
        # the medicine that exists was not rewritten by the failed bill either
        self.assertEqual(self.conn.execute("SELECT mrp, rate FROM medicines").fetchone(), (110, 90))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0], 0)

    def test_a_line_with_no_name_refuses_the_bill_before_anything_is_written(self):
        out = self.run_import(_bill("NEW AGENCY", "INV-2", [_line("AZEE"), _line("")]))
        self.assertEqual(out["saved"], 0)
        self.assertIn("missing medicine name", out["errors"][0])
        self.assertEqual(self.names("suppliers"), ["OLD AGENCY"])
        self.assertEqual(self.names("medicines"), ["COREX"])

    def test_the_other_bills_of_the_export_are_still_saved(self):
        out = self.run_import(
            _bill("NEW AGENCY", "INV-3", [_line("AZEE"), _line("CROCIN", qty="ten")]),
            _bill("GOOD AGENCY", "INV-4", [_line("DOLO", batch="D1")]),
        )
        self.assertEqual(out["saved"], 1, out)
        self.assertEqual(self.names("suppliers"), ["GOOD AGENCY", "OLD AGENCY"])
        self.assertEqual(self.names("medicines"), ["COREX", "DOLO"])
        self.assertEqual(self.conn.execute("SELECT bill_number FROM purchases").fetchall(), [("INV-4",)])
        self.assertFalse(self.conn.in_transaction)


class ANameOnlySupplier(_Offline):
    def test_keeps_what_the_pc_has_saved(self):
        out = self.run_import(_bill("OLD AGENCY", "INV-5", [_line("COREX")]))
        self.assertEqual(out["saved"], 1, out)
        self.assertEqual(
            self.conn.execute("SELECT address, phone, gstin, dl_numbers FROM suppliers "
                              "WHERE name='OLD AGENCY'").fetchone(),
            ("MAIN ROAD", "98220", "27ABCDE1234F1Z5", "DL-1"),
        )

    def test_a_field_the_export_does_carry_still_changes(self):
        out = self.run_import(_bill("OLD AGENCY", "INV-6", [_line("COREX")],
                                    supplier={"name": "OLD AGENCY", "phone": "70000"}))
        self.assertEqual(out["saved"], 1, out)
        self.assertEqual(
            self.conn.execute("SELECT address, phone FROM suppliers WHERE name='OLD AGENCY'").fetchone(),
            ("MAIN ROAD", "70000"),
        )


class OnOfflineFirst(unittest.TestCase):
    """The same on an offline-first store: nothing of the failed bill is noted for sending."""

    def test_a_failed_bill_sends_nothing(self):
        from tests.test_offline_first_pc_local import make_store, till
        import os

        path = make_store()
        conn = till(path)
        try:
            with mock.patch("core.sync_prefs.is_online_mode", return_value=False),                     mock.patch.object(mobile_import_apply, "_pc_is_wholesale", return_value=False),                     mock.patch("core.app_prefs.load_app_mode", return_value="veterinary"):
                out = mobile_import_apply._apply_purchases(conn, {"purchases": [
                    _bill("NEW AGENCY", "INV-7", [_line("AZEE"), _line("CROCIN", qty="ten")])]})
            self.assertEqual(out["saved"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)
        finally:
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
