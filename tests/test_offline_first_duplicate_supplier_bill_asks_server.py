"""A NEW purchase for a supplier bill already saved is refused (save_warnings.find_saved_supplier_bill).

Offline-first looked only at this PC's copy: the same bill saved a moment ago on the other PC or
the phone -- on the server, not pulled here yet -- was saved a second time and its goods went on
the shelf twice. The server is asked when the sync is working; no internet: the local answer
stands (two PCs both offline can still both save it -- see the audit report). No server is
contacted here.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

from core.offline_first import worker
from core.save_warnings import duplicate_supplier_bill_warnings, find_saved_supplier_bill

from tests.test_offline_first_pc_local import Base as OfBase

OTHER_DEVICE_ID = 5_000_000_123


def _server_rows(*rows):
    def answer(method, path, **kw):
        assert method == "GET" and path.startswith("/api/store/purchases?"), path
        assert kw.get("timeout", 99) <= 5.0
        return {"ok": True, "data": {"rows": list(rows)}}
    return answer


ROW = {"id": OTHER_DEVICE_ID, "purchase_no": "41/FY2026-27", "purchase_date": "2026-10-25 10:00:00",
       "bill_number": "INV-77", "supplier_id": 900, "supplier_name": "SHREE PHARMA",
       "total_amount": 1200, "items": [{}, {}]}


class TheOtherDevicesBill(OfBase):
    def setUp(self):
        super().setUp()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for p in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch("core.offline_first.runtime.is_active", return_value=True),
            mock.patch.object(worker, "current", return_value=None),
            mock.patch.object(worker, "kick", return_value=None),
            mock.patch("core.offline_first.client._token", return_value="t"),
        ):
            self.stack.enter_context(p)

    def find(self, *rows):
        with mock.patch("core.server_api._request", side_effect=_server_rows(*rows)) as asked:
            got = find_saved_supplier_bill(self.conn, supplier_name="Shree Pharma", bill_number="inv-77")
        return got, asked

    def test_saved_on_another_device_and_not_pulled_yet_is_found(self):
        got, asked = self.find(ROW)
        self.assertTrue(asked.called)
        self.assertEqual(got["purchase_id"], OTHER_DEVICE_ID)
        self.assertEqual(got["purchase_no"], "41/FY2026-27")
        self.assertEqual(got["item_count"], 2)
        self.assertTrue(got["on_another_device"])

    def test_another_suppliers_bill_with_that_number_is_not(self):
        got, _ = self.find(dict(ROW, supplier_id=901, supplier_name="OTHER AGENCY"))
        self.assertIsNone(got)

    def test_a_purchase_this_pc_deleted_and_has_not_sent_is_not(self):
        self.conn.execute("INSERT INTO of_dirty (collection, local_id, op, at) "
                          "VALUES ('purchases', ?, 'delete', 'now')", (OTHER_DEVICE_ID,))
        self.conn.commit()
        got, _ = self.find(ROW)
        self.assertIsNone(got)

    def test_a_purchase_this_pc_holds_is_judged_on_this_pcs_copy(self):
        # Held here with another bill number (edited here): the server's older copy is not used.
        self.conn.execute("INSERT INTO purchases (id, purchase_no, bill_number, supplier_id) "
                          "VALUES (?, '41', 'INV-78', 900)", (OTHER_DEVICE_ID,))
        self.conn.commit()
        got, _ = self.find(ROW)
        self.assertIsNone(got)

    def test_found_here_the_server_is_not_asked(self):
        self.conn.execute("INSERT INTO suppliers (id, name) VALUES (900, 'SHREE PHARMA')")
        self.conn.execute("INSERT INTO purchases (purchase_no, bill_number, supplier_id) "
                          "VALUES ('12', 'INV-77', 900)")
        self.conn.commit()
        got, asked = self.find(ROW)
        self.assertFalse(asked.called)
        self.assertNotIn("on_another_device", got)

    def test_no_internet_the_save_goes_ahead(self):
        with mock.patch("core.server_api._request", side_effect=OSError("down")):
            got = find_saved_supplier_bill(self.conn, supplier_name="SHREE PHARMA", bill_number="INV-77")
        self.assertIsNone(got)

    def test_the_edit_warning_does_not_wait_on_the_server(self):
        with mock.patch("core.server_api._request", side_effect=_server_rows(ROW)) as asked:
            self.assertEqual(duplicate_supplier_bill_warnings(
                self.conn, supplier_name="SHREE PHARMA", bill_number="INV-77"), [])
        self.assertFalse(asked.called)


class OfflineModeNeverAsks(OfBase):
    def test_offline_mode(self):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False), \
                mock.patch("core.offline_first.runtime.is_active", return_value=False), \
                mock.patch("core.server_api._request") as asked:
            self.assertIsNone(find_saved_supplier_bill(self.conn, supplier_name="X", bill_number="1"))
        self.assertFalse(asked.called)
