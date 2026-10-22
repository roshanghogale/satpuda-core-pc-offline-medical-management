"""A saved bill's payment changed by an edit reaches the server and the customer's due (owner, 22 Oct 2026).

"I reduced the cash of a bill so it is partially paid instead of cleared, then checked the server
but it is still the full amount, and Payments does not show the remaining amount as a due."

Runs only against a REHEARSAL server (never live), like test_offline_first_pc_server.py:
    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 python -m pytest -q tests/test_edit_payment_rehearsal.py

PC A edits with the app's own edit (billing_service.update_existing_bill) and syncs; PC B pulls
afterwards, so B's copy is what the server holds. Both must show the bill's new payment, and the
customer's due must be the bill's remaining amount on both -- the due the Payments screen lists.
"""
from __future__ import annotations

import unittest
import uuid
from unittest import mock

from tests.test_offline_first_pc_server import BASE, PC


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class EditedPaymentReachesTheServer(unittest.TestCase):
    def setUp(self):
        run = uuid.uuid4().hex[:8]
        self.run = run
        self.a = PC("A", f"pc-A-payedit-{run}")
        self.b = PC("B", f"pc-B-payedit-{run}")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.a.prepare()
        self.b.prepare()
        self.mid = self.a.till.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) "
            "VALUES ('PAYEDIT TAB','Tablet',100,'10',50,30,'P1')"
        ).lastrowid
        self.a.till.commit()

    def _bill(self, label: str, cash: float):
        """A bill of Rs 100 (2 x 50) for a new customer, paid ``cash`` at the counter."""
        a = self.a
        name = f"PAYEDIT {label} {self.run}".upper()
        cid = a.till.execute("INSERT INTO customers (name) VALUES (?)", (name,)).lastrowid
        a.till.commit()
        sid, _ = a.sell(cid, self.mid, 2, rate=50.0)
        a.till.execute(
            "UPDATE sales SET cash_paid=?, amount_paid=?, due_amount=?, bill_cleared=? WHERE id=?",
            (cash, cash, 100 - cash, 1 if cash >= 100 else 0, sid),
        )
        a.till.execute("UPDATE customers SET total_due=? WHERE id=?", (100 - cash, cid))
        a.till.commit()
        a.sync()
        self.b.sync()
        return sid, cid, name

    def _edit_cash(self, sid: int, name: str, cash: float):
        from core import billing_service, sync_prefs

        meds = [{"id": self.mid, "name": "PAYEDIT TAB", "qty": 2, "rate": 50, "amount": 100,
                 "medicine_discount": 0}]
        with mock.patch.object(sync_prefs, "get_sync_mode", lambda: sync_prefs.MODE_OFFLINE_FIRST), \
                self.a.acting():
            billing_service.update_existing_bill(
                self.a.till, sid, meds, 0, 0, cash, 0, name, "", "", 0, bill_date="2026-10-07",
            )
        self.a.sync()
        self.b.sync()
        self.a.sync()

    def _check(self, sid: int, cid: int, paid: float):
        due = round(100 - paid, 2)
        for pc in (self.a, self.b):
            row = pc.q("SELECT cash_paid, amount_paid, due_amount, bill_cleared FROM sales WHERE id=?", sid)
            self.assertTrue(row, f"{pc.name} does not hold the bill")
            cash_paid, amount_paid, due_amount, cleared = row[0]
            self.assertAlmostEqual(float(cash_paid), paid, 2, f"{pc.name}: cash on the bill")
            self.assertAlmostEqual(float(amount_paid), paid, 2, f"{pc.name}: paid on the bill")
            self.assertAlmostEqual(float(due_amount), due, 2, f"{pc.name}: the bill's due")
            self.assertEqual(bool(cleared), due == 0, f"{pc.name}: cleared flag")
            cust_due = pc.q("SELECT total_due FROM customers WHERE id=?", cid)[0][0]
            self.assertAlmostEqual(float(cust_due), due, 2, f"{pc.name}: the customer's due (Payments)")
        flagged = self.a.q(
            "SELECT collection, outcome, flag_code, flag_detail FROM of_events WHERE outcome <> 'applied'"
        )
        self.assertEqual(flagged, [], "an event of the edit did not land on the server")

    def test_cash_lowered_leaves_the_rest_due(self):
        sid, cid, name = self._bill("LOWER", 100)
        self._edit_cash(sid, name, 40)
        self._check(sid, cid, 40)

    def test_cash_raised_clears_the_bill(self):
        sid, cid, name = self._bill("RAISE", 30)
        self._edit_cash(sid, name, 100)
        self._check(sid, cid, 100)

    def test_cash_bill_turned_into_due(self):
        sid, cid, name = self._bill("TODUE", 100)
        self._edit_cash(sid, name, 0)
        self._check(sid, cid, 0)


if __name__ == "__main__":
    unittest.main()
