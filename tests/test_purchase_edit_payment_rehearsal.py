"""A saved purchase's payment changed by an edit reaches the server and the supplier's due (owner, 22 Oct 2026).

"Make sure the same error of the sales edit payment should not be in the purchase": a saved sale
whose cash was lowered stayed fully paid on the server and Payments showed no due. The same for a
purchase: PC A saves a supplier bill and edits its Cash paid with the app's own Purchase screen
save (desktop_purchase_service.save_purchase_bill with editing_purchase_id), and syncs; PC B pulls
afterwards, so B's copy is what the server holds. On both, the purchase's paid / due / cleared
fields, the supplier's due and the open purchase bills Payments lists must follow the edit.

Runs only against a REHEARSAL server (never live), like test_offline_first_pc_server.py:
    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 python -m pytest -q tests/test_purchase_edit_payment_rehearsal.py
"""
from __future__ import annotations

import unittest
import uuid
from unittest import mock

from tests.test_offline_first_pc_server import BASE, PC

TOTAL = 100.0  # 10 strips x Rs 10, no GST, no discount


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class EditedPurchasePaymentReachesTheServer(unittest.TestCase):
    def setUp(self):
        run = uuid.uuid4().hex[:8]
        self.run = run
        self.a = PC("A", f"pc-A-purpay-{run}")
        self.b = PC("B", f"pc-B-purpay-{run}")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.a.prepare()
        self.b.prepare()

    def _save(self, body: dict) -> dict:
        from core import desktop_purchase_service, sync_prefs

        with mock.patch.object(sync_prefs, "get_sync_mode", lambda: sync_prefs.MODE_OFFLINE_FIRST), \
                self.a.acting():
            res = desktop_purchase_service.save_purchase_bill(self.a.till, body)
        self.assertTrue(res.get("ok"), res)
        return res

    def _body(self, supplier: str, bill: str, cash: float, online: float = 0.0, **extra) -> dict:
        return {
            "supplier_name": supplier,
            "bill_number": bill,
            "purchase_date": "2026-10-07",
            "gst_calc_method": "discount_before_gst",
            "cash_paid": cash,
            "online_paid": online,
            "items": [{
                "name": f"PURPAY TAB {self.run}", "type": "Tablet", "batch": "PP1", "expiry": "12/28",
                "qty": 10, "free_qty": 0, "rate": 10, "mrp": 20, "gst_pct": 0, "discount_pct": 0,
                "unit": "10",
            }],
            **extra,
        }

    def _bill(self, label: str, cash: float, online: float = 0.0):
        supplier = f"PURPAY {label} {self.run}".upper()
        bill = f"B{self.run[:5]}{label[:3]}"
        res = self._save(self._body(supplier, bill, cash, online))
        self.a.sync()
        self.b.sync()
        return int(res["purchase_id"]), int(res["supplier_id"]), supplier, bill

    def _edit(self, pid: int, supplier: str, bill: str, cash: float, online: float = 0.0):
        row = self.a.q("SELECT previous_due, previous_credit FROM purchases WHERE id=?", pid)[0]
        self._save(self._body(supplier, bill, cash, online, editing_purchase_id=pid,
                              edit_previous_due=row[0], edit_previous_credit=row[1]))
        self.a.sync()
        self.b.sync()
        self.a.sync()

    def _check(self, pid: int, sid: int, cash: float, online: float = 0.0):
        from core.bill_clearing import open_bills

        paid = round(cash + online, 2)
        due = round(max(0.0, TOTAL - paid), 2)
        for pc in (self.a, self.b):
            row = pc.q(
                "SELECT amount_paid, amount_paid_at_entry, cash_paid_at_entry, online_paid_at_entry, "
                "due_amount, bill_cleared, account_cleared FROM purchases WHERE id=?", pid,
            )
            self.assertTrue(row, f"{pc.name} does not hold the purchase")
            amount_paid, at_entry, cash_at, online_at, due_amount, cleared, acc_cleared = row[0]
            self.assertAlmostEqual(float(amount_paid), paid, 2, f"{pc.name}: paid on the purchase")
            self.assertAlmostEqual(float(at_entry), paid, 2, f"{pc.name}: paid at entry")
            self.assertAlmostEqual(float(cash_at or 0), cash, 2, f"{pc.name}: cash at entry")
            self.assertAlmostEqual(float(online_at or 0), online, 2, f"{pc.name}: online at entry")
            self.assertAlmostEqual(float(due_amount), due, 2, f"{pc.name}: the purchase's due")
            self.assertEqual(bool(cleared), due == 0, f"{pc.name}: bill_cleared")
            self.assertEqual(bool(acc_cleared), due == 0, f"{pc.name}: account_cleared")
            sup_due = pc.q("SELECT total_due FROM suppliers WHERE id=?", sid)[0][0]
            self.assertAlmostEqual(float(sup_due or 0), due, 2, f"{pc.name}: the supplier's due (Payments)")
            owes = {b["bill_id"]: b["owes"] for b in open_bills(pc.till, "supplier", sid)}
            self.assertEqual(owes, {pid: due} if due else {}, f"{pc.name}: open purchase bills (Payments)")
        flagged = self.a.q(
            "SELECT collection, outcome, flag_code, flag_detail FROM of_events WHERE outcome <> 'applied'"
        )
        self.assertEqual(flagged, [], "an event of the edit did not land on the server")

    def test_cash_lowered_leaves_the_rest_due(self):
        pid, sid, supplier, bill = self._bill("LOWER", 100)
        self._edit(pid, supplier, bill, 40)
        self._check(pid, sid, 40)

    def test_cash_raised_clears_the_purchase(self):
        pid, sid, supplier, bill = self._bill("RAISE", 30)
        self._edit(pid, supplier, bill, 100)
        self._check(pid, sid, 100)

    def test_cash_purchase_turned_into_due(self):
        pid, sid, supplier, bill = self._bill("TODUE", 100)
        self._edit(pid, supplier, bill, 0)
        self._check(pid, sid, 0)

    def test_due_purchase_paid_in_cash(self):
        pid, sid, supplier, bill = self._bill("FROMDUE", 0)
        self._edit(pid, supplier, bill, 100)
        self._check(pid, sid, 100)

    def test_cash_and_online_split_lowered(self):
        pid, sid, supplier, bill = self._bill("SPLIT", 50, 50)
        self._edit(pid, supplier, bill, 20, 0)
        self._check(pid, sid, 20, 0)


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class OnlineEditedPurchasePaymentReachesTheServer(unittest.TestCase):
    """The same, Online: the PC holds nothing and saves straight to the store.

    The edit is the Purchase screen's save with editing_purchase_id (update_purchase ->
    update_purchase_online_now); the store's own row is then read back through the API.
    """

    def setUp(self):
        import contextlib
        import shutil
        import tempfile

        from core import license_manager, online_catalog, online_guard, server_api, sync_prefs
        from core import server_crud
        from tests.test_offline_first_pc_server import KEY, _post

        self.run = uuid.uuid4().hex[:8]
        install = f"pc-online-purpay-{self.run}"
        token = _post("/api/auth/pair", {"android_key": KEY, "device_id": install, "device_type": "pc"})["data"]["token"]
        self.token = token
        folder = tempfile.mkdtemp(prefix="purpay_online_")
        self.addCleanup(shutil.rmtree, folder, True)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for target, attr, value in (
            # tests/__init__.py pins the suite Offline by replacing these functions.
            (sync_prefs, "get_sync_mode", lambda: sync_prefs.MODE_ONLINE),
            (sync_prefs, "is_online_mode", lambda: True),
            (sync_prefs, "is_offline_mode", lambda: False),
            (sync_prefs, "is_offline_first", lambda: False),
            (server_api, "api_base", lambda: BASE),
            (server_api, "store_token_for_active", lambda *a, **k: token),
            (license_manager, "_appdata_dir", lambda: folder),
            (online_guard, "is_cloud_reachable", lambda **k: True),
            (server_crud, "_device_id", lambda: install),
        ):
            stack.enter_context(mock.patch.object(target, attr, value))
        # Online a new purchase's answer carries no row id (the engine's SQLite is empty): the id
        # the store filed it under is the one the push used.
        self.pushed: list[int] = []
        real_push = server_crud.save_new_purchase_online

        def spy(payload):
            out = real_push(payload)
            self.pushed.append(int(out))
            return out

        stack.enter_context(mock.patch.object(server_crud, "save_new_purchase_online", spy))
        online_catalog.invalidate()
        self.addCleanup(online_catalog.invalidate)
        import sqlite3

        from core import db_setup

        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        self.addCleanup(self.conn.close)

    _body = EditedPurchasePaymentReachesTheServer._body

    def _save(self, body: dict) -> dict:
        from core import desktop_purchase_service

        res = desktop_purchase_service.save_purchase_bill(self.conn, body)
        self.assertTrue(res.get("ok"), res)
        return res

    def _doc(self, collection: str, local_id: int) -> dict:
        from core import server_crud

        server_crud.forget_missing(collection)
        from core.online_catalog import invalidate

        invalidate()
        return server_crud.get_doc(collection, int(local_id)) or {}

    def _bill(self, label: str, cash: float, online: float = 0.0):
        supplier = f"PURPAY ON {label} {self.run}".upper()
        bill = f"O{self.run[:5]}{label[:3]}"
        res = self._save(self._body(supplier, bill, cash, online))
        return int(res["purchase_id"]) or self.pushed[-1], int(res["supplier_id"]), supplier, bill

    def _edit(self, pid: int, supplier: str, bill: str, cash: float, online: float = 0.0):
        row = self._doc("purchases", pid)
        self.assertTrue(row, f"the store does not hold purchase {pid}")
        return self._save(self._body(supplier, bill, cash, online, editing_purchase_id=pid,
                                     edit_previous_due=row.get("previous_due"),
                                     edit_previous_credit=row.get("previous_credit")))

    def _check(self, pid: int, sid: int, cash: float, online: float = 0.0, answer: dict | None = None):
        paid = round(cash + online, 2)
        due = round(max(0.0, TOTAL - paid), 2)
        row = self._doc("purchases", pid)
        self.assertAlmostEqual(float(row.get("amount_paid") or 0), paid, 2, "store: paid on the purchase")
        self.assertAlmostEqual(float(row.get("cash_paid_at_entry") or 0), cash, 2, "store: cash at entry")
        self.assertAlmostEqual(float(row.get("online_paid_at_entry") or 0), online, 2, "store: online at entry")
        self.assertAlmostEqual(float(row.get("due_amount") or 0), due, 2, "store: the purchase's due")
        self.assertEqual(bool(row.get("bill_cleared")), due == 0, "store: bill_cleared")
        self.assertEqual(bool(row.get("account_cleared")), due == 0, "store: account_cleared")
        sup = self._doc("suppliers", sid)
        self.assertAlmostEqual(float(sup.get("total_due") or 0), due, 2, "store: the supplier's due")
        if answer is not None:
            self.assertAlmostEqual(float(answer.get("supplier_due") or 0), due, 2, "the save's answer: supplier due")

    def test_cash_lowered_leaves_the_rest_due(self):
        pid, sid, supplier, bill = self._bill("LOWER", 100)
        res = self._edit(pid, supplier, bill, 40)
        self._check(pid, sid, 40, answer=res)

    def test_cash_raised_clears_the_purchase(self):
        pid, sid, supplier, bill = self._bill("RAISE", 30)
        res = self._edit(pid, supplier, bill, 100)
        self._check(pid, sid, 100, answer=res)

    def test_cash_purchase_turned_into_due(self):
        pid, sid, supplier, bill = self._bill("TODUE", 100)
        res = self._edit(pid, supplier, bill, 0)
        self._check(pid, sid, 0, answer=res)

    def test_cash_and_online_split_lowered(self):
        pid, sid, supplier, bill = self._bill("SPLIT", 50, 50)
        res = self._edit(pid, supplier, bill, 20, 0)
        self._check(pid, sid, 20, 0, answer=res)


if __name__ == "__main__":
    unittest.main()
