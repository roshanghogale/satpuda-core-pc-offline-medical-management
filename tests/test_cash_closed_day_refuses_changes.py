"""Cash & Expenses: a closed day (cash counted) takes no new expense, no edit or delete of a line,
and no change of an opening already typed, until it is opened again (audit, 25 Oct 2026).

An expense typed into a closed day changed its expected cash under a count already taken: the
difference no longer said what the shop counted.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import cash_register as cr
from core import db_setup

D = "2026-10-24"
D2 = "2026-10-25"


class ClosedDay(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        db_setup.initialise(self.c)
        cr.ensure_tables(self.c)
        self.c.commit()
        cr.set_opening(self.c, D, 500)
        self.tea = cr.save_expense(self.c, {"date": D, "description": "Tea", "amount": 20})["id"]
        self.other = cr.save_expense(self.c, {"date": D2, "description": "Auto", "amount": 30})["id"]
        cr.close_day(self.c, D, 480)

    def expenses(self, day):
        return [(e["description"], e["amount"]) for e in cr.manual_lines(self.c, day)]

    def test_a_new_line_is_refused(self):
        with self.assertRaises(cr.DayClosed):
            cr.save_expense(self.c, {"date": D, "description": "Snacks", "amount": 15})
        with self.assertRaises(cr.DayClosed):
            cr.save_expense(self.c, {"date": D, "kind": "in", "description": "Owner added", "amount": 100})
        self.assertEqual(self.expenses(D), [("Tea", 20.0)])

    def test_an_edit_or_delete_is_refused_and_a_line_cannot_move_in_or_out(self):
        with self.assertRaises(cr.DayClosed):
            cr.save_expense(self.c, {"date": D, "id": self.tea, "description": "Tea", "amount": 200})
        with self.assertRaises(cr.DayClosed):
            cr.delete_expense(self.c, self.tea)
        with self.assertRaises(cr.DayClosed):     # out of the closed day
            cr.save_expense(self.c, {"date": D2, "id": self.tea, "description": "Tea", "amount": 20})
        with self.assertRaises(cr.DayClosed):     # into the closed day
            cr.save_expense(self.c, {"date": D, "id": self.other, "description": "Auto", "amount": 30})
        self.assertEqual(self.expenses(D), [("Tea", 20.0)])
        self.assertEqual(self.expenses(D2), [("Auto", 30.0)])

    def test_the_opening_typed_is_kept_but_a_missing_one_may_still_be_typed(self):
        with self.assertRaises(cr.DayClosed):
            cr.set_opening(self.c, D, 900)
        self.assertEqual(cr.day_report(self.c, D)["opening"]["value"], 500.0)
        cr.close_day(self.c, D2, 0)
        cr.set_opening(self.c, D2, 50)          # closed with no opening: it still asks for one
        self.assertEqual(cr.day_report(self.c, D2)["opening"]["value"], 50.0)

    def test_opened_again_it_takes_changes(self):
        cr.reopen_day(self.c, D)
        cr.save_expense(self.c, {"date": D, "description": "Snacks", "amount": 15})
        cr.delete_expense(self.c, self.tea)
        self.assertEqual(self.expenses(D), [("Snacks", 15.0)])

    def test_the_route_says_why(self):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            code, body = cr.handle_post("/api/cash/expense", {"date": D, "description": "Snacks", "amount": 15},
                                        self.c)
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])
        self.assertIn("closed", body["error"])


if __name__ == "__main__":
    unittest.main()
