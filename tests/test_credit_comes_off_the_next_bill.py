"""Money a customer has with the shop must come off their next bill.

A sales return settled as credit leaves the amount on the customer's account.
The LEDGER has always handled that correctly -- core.customer_service
recalculate_customer_due, and the store server's own partyDueCascade, derive the
balance from the raw transactions, so credit is consumed by the ABSENCE of a
payment and nothing has to be written to reduce it. Writing a reduction as well
would spend the same money twice.

What was wrong was the figure the counter was shown. calc_payment_result clamps
prev_net at max(0, due - credit), so credit is only ever allowed to cancel a
previous DUE: a customer with nothing owing and Rs 500 standing credit was still
asked for the whole new bill, and the credit sat there for ever.

The fix adds two figures and changes none of the stored ones. calc_payment_result
is a WRITE path as well as a preview -- billing_service saves and edits bills
through it, core/server_crud.py does too -- and sales.due_amount is read back by
the daily and monthly exports, the due-reminder list and the home dashboard's
cleared/pending counts. So the bookkeeping keeps meaning "this bill alone", and
the screen gets the figure the shop needs.
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.calc_engine import calc_payment_result  # noqa: E402
from core import bill_render_utils, calc_engine  # noqa: E402


def pay(bill, cash=0.0, online=0.0, due=0.0, credit=0.0):
    return calc_payment_result(bill, cash, online, due, credit)


class CreditComesOffWhatTheCustomerPays(unittest.TestCase):
    def test_credit_with_nothing_owing_covers_the_new_bill(self):
        """Rs 500 credit, Rs 400 bill: take nothing, Rs 100 credit remains."""
        r = pay(400, due=0, credit=500)
        self.assertEqual(r["credit_applied"], 400.0)
        self.assertEqual(r["net_total_due"], 0.0)

    def test_credit_larger_than_the_due_covers_the_rest_of_the_bill(self):
        """Owed 200, holds 500: 300 spare goes against a 400 bill, 100 left."""
        r = pay(400, due=200, credit=500)
        self.assertEqual(r["credit_applied"], 300.0)
        self.assertEqual(r["net_total_due"], 100.0)

    def test_a_due_with_no_credit_is_untouched(self):
        r = pay(400, due=500, credit=0)
        self.assertEqual(r["credit_applied"], 0.0)
        self.assertEqual(r["net_total_due"], 900.0)

    def test_an_ordinary_bill_is_untouched(self):
        r = pay(400, due=0, credit=0)
        self.assertEqual(r["credit_applied"], 0.0)
        self.assertEqual(r["net_total_due"], 400.0)

    def test_credit_is_not_spent_when_the_customer_pays_anyway(self):
        """The obvious way to get this wrong: bank the cash AND the credit."""
        r = pay(400, cash=400, due=0, credit=500)
        self.assertEqual(
            r["credit_applied"], 0.0,
            "the customer paid in full — their credit must still be there "
            "tomorrow, or the same Rs 500 gets spent on every bill",
        )
        self.assertEqual(r["net_total_due"], 0.0)

    def test_a_part_payment_uses_only_the_rest(self):
        r = pay(400, cash=100, due=0, credit=500)
        self.assertEqual(r["credit_applied"], 300.0)
        self.assertEqual(r["net_total_due"], 0.0)

    def test_credit_smaller_than_the_bill_leaves_the_remainder_to_collect(self):
        r = pay(400, due=0, credit=300)
        self.assertEqual(r["credit_applied"], 300.0)
        self.assertEqual(r["net_total_due"], 100.0)


class NoStoredFigureChanged(unittest.TestCase):
    """The change must be invisible to every writer and reader of a saved bill.

    calc_payment_result is called on the save path (core/billing_service.py:318),
    the edit path (:941) and the online write path (core/server_crud.py:281), and
    sales.due_amount feeds core/desktop_export_service.py, the due reminders in
    core/alert_monitoring_service.py and the counters in core/home_dashboard.py.
    """

    CASES = [
        # (bill, cash, online, due, credit, expected due_amount, total_due, need_to_pay)
        (400, 0, 0, 0, 500, 400.0, 400.0, 400.0),
        (400, 0, 0, 200, 500, 400.0, 400.0, 400.0),
        (400, 0, 0, 500, 0, 400.0, 900.0, 900.0),
        (400, 0, 0, 0, 0, 400.0, 400.0, 400.0),
        (400, 400, 0, 0, 500, 0.0, 0.0, 400.0),
        (400, 100, 0, 0, 500, 300.0, 300.0, 400.0),
        (400, 0, 0, 0, 300, 400.0, 400.0, 400.0),
    ]

    def test_the_bookkeeping_fields_are_exactly_what_they_always_were(self):
        for bill, cash, online, due, credit, exp_due, exp_total, exp_need in self.CASES:
            with self.subTest(bill=bill, cash=cash, due=due, credit=credit):
                r = calc_payment_result(bill, cash, online, due, credit)
                self.assertEqual(r["due_amount"], exp_due)
                self.assertEqual(r["current_bill_due"], exp_due)
                self.assertEqual(r["total_due"], exp_total)
                self.assertEqual(r["need_to_pay"], exp_need)

    def test_the_new_keys_are_additions_and_nothing_was_removed(self):
        r = calc_payment_result(400, 0, 0, 0, 500)
        for key in (
            "amount_paid", "due_amount", "credit_amount", "need_to_pay",
            "current_bill_due", "remaining_previous_due", "total_due",
        ):
            self.assertIn(key, r, f"{key} disappeared from the payment result")
        self.assertIn("credit_applied", r)
        self.assertIn("net_total_due", r)


class TheLedgerStillSpendsTheCreditByItself(unittest.TestCase):
    """The invariant that makes writing a credit reduction unnecessary.

    recalculate_customer_due derives the balance from raw transactions, so a
    bill saved with no payment against Rs 500 of credit leaves Rs 100 -- and a
    bill the customer paid in full leaves the Rs 500 intact.
    """

    def _net(self, sales_total, sales_paid, returns_refund, payments=0.0):
        # The expression at core/customer_service.py:271, in the same order.
        return round(sales_total - sales_paid - payments - returns_refund, 2)

    def test_an_unpaid_bill_eats_the_credit(self):
        net = self._net(sales_total=400, sales_paid=0, returns_refund=500)
        self.assertEqual(net, -100.0, "credit should have dropped to 100")

    def test_a_paid_bill_leaves_the_credit_standing(self):
        net = self._net(sales_total=400, sales_paid=400, returns_refund=500)
        self.assertEqual(net, -500.0, "the credit was spent as well as the cash")


class ThePrintedBillAgreesWithTheScreen(unittest.TestCase):
    """R5 -- the paper the customer carries out and the counter screen.

    core.bill_render_utils.total_due_display used to clamp the previous
    balance at max(0, due - credit), the same clamp calc_payment_result had.
    After net_total_due was added the screen said Rs 0 and the printed bill
    still said Rs 400, in front of the customer, about money.
    """

    GRID = [
        # previous_due, previous_credit, bill, paid
        (0, 500, 400, 0),
        (200, 500, 400, 0),
        (500, 0, 400, 0),
        (0, 0, 400, 0),
        (0, 500, 400, 400),
        (0, 500, 400, 150),
        (0, 300, 400, 0),
        (1000, 250, 400, 100),
        (0, 0, 0, 0),
        (0, 450.55, 400.40, 0),
        (75.25, 120.10, 33.90, 10),
    ]

    def _paper(self, due, credit, bill, paid):
        ctx = SimpleNamespace(previous_due=due, previous_credit=credit,
                              grand_total=bill, amount_paid=paid)
        return bill_render_utils.total_due_display(ctx)

    def _screen(self, due, credit, bill, paid):
        return calc_engine.calc_payment_result(
            total_amount=bill, cash_paid=paid, online_paid=0,
            previous_due=due, previous_credit=credit,
        )["net_total_due"]

    def test_paper_and_screen_never_disagree(self):
        for due, credit, bill, paid in self.GRID:
            with self.subTest(due=due, credit=credit, bill=bill, paid=paid):
                self.assertAlmostEqual(
                    self._paper(due, credit, bill, paid),
                    self._screen(due, credit, bill, paid),
                    places=2,
                    msg="the printed bill and the counter screen disagree",
                )

    def test_standing_credit_comes_off_the_printed_total(self):
        # Rs 500 credit, nothing owing, a Rs 400 bill taken on due.
        self.assertEqual(self._paper(0, 500, 400, 0), 0.0)

    def test_a_bill_bigger_than_the_credit_still_prints_the_rest(self):
        self.assertEqual(self._paper(0, 300, 400, 0), 100.0)

    def test_the_printed_total_never_goes_negative(self):
        # Leftover credit is not a refund the bill should advertise.
        self.assertEqual(self._paper(0, 900, 400, 0), 0.0)

    def test_this_bill_alone_is_untouched_by_credit(self):
        # bill_due_display is "this bill only" and must not net off credit.
        ctx = SimpleNamespace(previous_due=0, previous_credit=500,
                              grand_total=400, amount_paid=0)
        self.assertEqual(bill_render_utils.bill_due_display(ctx), 400.0)


if __name__ == "__main__":
    unittest.main()
