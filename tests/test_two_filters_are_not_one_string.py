"""Using two history filters at once must not return an empty list.

Reported from the counter: applying several filters together showed no sales or
purchases even though matching records were right there.

The store API takes a single `q`, which it tests as ONE wildcard against
bill_no OR customer_name OR doctor_name (purchases: purchase_no OR
supplier_name OR bill_number). The page has two independent filters -- a typed
search box and a customer/supplier picker -- and it sent them JOINED BY A SPACE.
"SCB12 RAHUL" is not a substring of any column, so the server matched nothing
and the shop was told it had no such bills.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.desktop_pages_service import _server_search_term  # noqa: E402


class OnlyOneTermGoesToTheServer(unittest.TestCase):

    def test_the_two_filters_are_never_glued_together(self):
        """The whole bug in one assertion."""
        term = _server_search_term("SCB12", "RAHUL")
        self.assertNotIn(" ", term, "a joined term matches no column and returns nothing")
        self.assertEqual(term, "SCB12")

    def test_the_typed_search_wins_when_both_are_set(self):
        # The party filter is applied locally against customer_name /
        # supplier_name, so the narrower typed term is the useful one to send.
        self.assertEqual(_server_search_term("SCB12", "RAHUL PATIL"), "SCB12")

    def test_a_lone_party_filter_still_reaches_the_server(self):
        # One filter on its own must keep using the server, not drag the whole
        # date range back to be filtered on this PC.
        self.assertEqual(_server_search_term("", "RAHUL PATIL"), "RAHUL PATIL")
        self.assertEqual(_server_search_term(None, "RAHUL PATIL"), "RAHUL PATIL")

    def test_a_lone_search_box_is_unchanged(self):
        self.assertEqual(_server_search_term("SCB12", ""), "SCB12")
        self.assertEqual(_server_search_term("SCB12", None), "SCB12")

    def test_no_filters_means_no_search_term(self):
        self.assertEqual(_server_search_term("", ""), "")
        self.assertEqual(_server_search_term(None, None), "")

    def test_whitespace_is_not_a_filter(self):
        self.assertEqual(_server_search_term("   ", "RAHUL"), "RAHUL")
        self.assertEqual(_server_search_term("   ", "   "), "")

    def test_the_term_is_trimmed_so_it_can_match(self):
        self.assertEqual(_server_search_term("  SCB12  ", ""), "SCB12")
        self.assertEqual(_server_search_term("", "  RAHUL  "), "RAHUL")

    def test_a_party_name_with_spaces_is_sent_whole(self):
        # Only the JOIN of two different filters was the problem; one filter
        # that happens to contain a space is a legitimate substring search.
        self.assertEqual(
            _server_search_term("", "SHREE GAJANAN MEDICAL"),
            "SHREE GAJANAN MEDICAL",
        )



class SalesScheduleMeansTheSameThingBothWays(unittest.TestCase):
    """The same filter on the Sales side -- and this one drives Print All.

    Sales history folded medicine, batch and schedule into ONE EXISTS, so it
    asked for a single line satisfying all three at once where the store server
    asks three independent questions. And "Non-Scheduled" was EXISTS(schedule
    ='') -- "this bill has at least one unscheduled item", which is nearly every
    bill a shop ever wrote -- where the server asks whether they ALL are.

    Because Print All prints whatever this filter returns, the disagreement was
    not a listing curiosity: it decided which bills came out of the printer.
    """

    def setUp(self):
        import sqlite3
        from unittest import mock

        from core import db_setup

        self._offline = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        self._offline.start()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO customers (id, name) VALUES (1, 'ZZ CUSTOMER ONE')")
        cur.execute(
            "INSERT INTO medicines (id, name, batch_no, schedule, stock_qty, mrp, rate) "
            "VALUES (1, 'ZZ SCHEDULED MED', 'B1', 'H1', 10, 20, 12)"
        )
        cur.execute(
            "INSERT INTO medicines (id, name, batch_no, schedule, stock_qty, mrp, rate) "
            "VALUES (2, 'ZZ PLAIN MED', 'B2', '', 10, 20, 12)"
        )

        def sale(sid, no, med_ids):
            cur.execute(
                "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, "
                "amount_paid) VALUES (?, ?, 1, '2026-09-01', 100, 100)",
                (sid, no),
            )
            for mid in med_ids:
                cur.execute(
                    "INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) "
                    "VALUES (?, ?, 1, 12, 12)",
                    (sid, mid),
                )

        sale(1, "S1", [2, 2])      # every line unscheduled
        sale(2, "S2", [1, 2])      # mixed: one scheduled line
        sale(3, "S3", [1])         # entirely scheduled
        self.conn.commit()

    def tearDown(self):
        self._offline.stop()
        self.conn.close()

    def _nos(self, **kw):
        from core import desktop_pages_service as pages

        res = pages.list_sales_history(self.conn, **kw)
        col = res["columns"].index("Bill No") if "Bill No" in res["columns"] else 1
        return sorted(str(r[col]) for r in res["rows"])

    def test_non_scheduled_means_every_line_not_any_line(self):
        """S2 has a scheduled line, so it is not a non-scheduled bill."""
        self.assertEqual(
            self._nos(schedule="Non-Scheduled"), ["S1"],
            "any-line hands back nearly the whole history -- and prints it",
        )

    def test_a_named_schedule_finds_the_bills_that_contain_it(self):
        self.assertEqual(self._nos(schedule="H1"), ["S2", "S3"])

    def test_medicine_and_batch_are_asked_separately(self):
        """S2 holds ZZ SCHEDULED MED (B1) and ZZ PLAIN MED (B2). Asking for one
        medicine and the OTHER medicine's batch is two independent questions --
        the server's answer -- not "one line that is both"."""
        self.assertEqual(
            self._nos(medicine="ZZ SCHEDULED", batch="B2"), ["S2"],
            "the two filters were glued into one EXISTS",
        )

    def test_a_medicine_filter_still_narrows(self):
        self.assertEqual(self._nos(medicine="ZZ PLAIN"), ["S1", "S2"])

    def test_schedule_and_medicine_together_still_narrow(self):
        self.assertEqual(self._nos(schedule="H1", medicine="ZZ PLAIN"), ["S2"])

    def test_no_filter_returns_every_bill(self):
        self.assertEqual(self._nos(), ["S1", "S2", "S3"])



if __name__ == "__main__":
    unittest.main(verbosity=2)


class PurchaseScheduleMeansTheSameThingBothWays(unittest.TestCase):
    """Offline and online must answer the Schedule filter identically.

    They did not. "Non-Scheduled" asked ANY line offline and ALL lines on the
    server; on a real store that is 247 bills against 61, out of 367 -- so the
    same shop, the same window, got two different histories depending on which
    mode it was in. And a named schedule matched the line OR the medicines
    master offline, where the server takes the line and falls back to the
    master. The line is what was actually bought that day.
    """

    def setUp(self):
        import sqlite3
        from unittest import mock

        from core import db_setup

        # This box is paired to a live store in Online mode, where
        # list_purchase_history reads the SERVER and ignores the connection
        # entirely. Pin it offline so the assertions are about the SQL.
        self._offline = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        self._offline.start()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO suppliers (id, name) VALUES (1, 'ZZ SUPPLIER ONE')")
        # Two medicines: one scheduled in the master, one not.
        cur.execute(
            "INSERT INTO medicines (id, name, batch_no, schedule, stock_qty, mrp, rate) "
            "VALUES (1, 'ZZ SCHEDULED MED', 'B1', 'H1', 10, 20, 12)"
        )
        cur.execute(
            "INSERT INTO medicines (id, name, batch_no, schedule, stock_qty, mrp, rate) "
            "VALUES (2, 'ZZ PLAIN MED', 'B2', '', 10, 20, 12)"
        )

        def purchase(pid, no, lines):
            cur.execute(
                "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, "
                "total_amount, final_amount) VALUES (?, ?, 1, '2026-09-01', 100, 100)",
                (pid, no),
            )
            for med_id, sched in lines:
                cur.execute(
                    "INSERT INTO purchase_items (purchase_id, medicine_id, schedule, "
                    "qty, rate, amount) VALUES (?, ?, ?, 1, 12, 12)",
                    (pid, med_id, sched),
                )

        # every line resolves to nothing
        purchase(1, "P1", [(2, ""), (2, "")])
        # mixed: one line resolves to H1 through the master, one to nothing
        purchase(2, "P2", [(1, ""), (2, "")])
        # the line itself says H, overriding a master that says H1
        purchase(3, "P3", [(1, "H")])
        self.conn.commit()

    def tearDown(self):
        self._offline.stop()
        self.conn.close()

    def _nos(self, schedule):
        from core import desktop_pages_service as pages

        res = pages.list_purchase_history(self.conn, schedule=schedule)
        col = res["columns"].index("Purchase No") if "Purchase No" in res["columns"] else 0
        return sorted(str(r[col]) for r in res["rows"])

    def test_non_scheduled_means_every_line_not_any_line(self):
        """P2 has one scheduled line, so it is NOT a non-scheduled purchase."""
        self.assertEqual(self._nos("Non-Scheduled"), ["P1"])

    def test_a_named_schedule_uses_the_master_when_the_line_is_blank(self):
        self.assertEqual(self._nos("H1"), ["P2"])

    def test_the_line_wins_over_the_master(self):
        """P3's line says H; its medicine's master says H1. H is what was bought."""
        self.assertEqual(self._nos("H"), ["P3"])
        self.assertNotIn("P3", self._nos("H1"))
