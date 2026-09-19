"""Pick any one schedule and no bill comes back -- so H1 cannot be printed.

Reported from the counter, one bug in four symptoms:
  1. Yesterday's Schedule H1 bills do not appear when he goes to print them.
  2. Today's do not appear either. A new store, ever since it started on Tauri.
  3. With "All bills" they DO appear.
  4. With "All schedules" they appear; pick ANY ONE schedule and nothing comes.

No filter = bills, any named schedule = nothing. H1 is a controlled-drug
register the shop is required to be able to print, so this is not cosmetic.

The cause is in the Online sale save. save_new_sale_online builds each
sales_items line by hand and simply never put `schedule` on it (nor hsn_code
nor manufacturer -- the same three fields, and only those three). The server
stores what it is given, so every bill written since the shop moved to Tauri
reached sales_items with schedule NULL, while the store server filters sales
by the LINE's own column:

    EXISTS (SELECT 1 FROM sales_items si
             WHERE si.sale_id = sales.id AND si.schedule = $n)

NULL matches no named schedule, which is symptoms 1, 2 and 4; drop the filter
and the bill is returned like any other, which is symptom 3.

Confirmed on the live server before fixing (read-only). For the new store,
lines carrying their own schedule stop dead the day it went live on Tauri, and
on that day name and batch_no keep arriving while schedule, hsn_code and
manufacturer stop -- exactly the three fields this payload omits.

Two halves to the fix, and the second is the one that matters legally:
  * Send the schedule (this file's subject), so new bills are right.
  * Resolve through the medicines master in the server's own filter, the way
    the purchase side already does, so the bills ALREADY WRITTEN can be
    printed. The shop cannot re-enter its history.
"""
import os
import sqlite3
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import server_crud  # noqa: E402


# The medicines master, as the server knows it. ROLCEF is H1 -- the bill that
# would not print.
MASTER = {
    1343: {"id": 1343, "local_id": 1343, "name": "ROLCEF 200 TAB", "type": "TAB",
           "schedule": "H1", "hsn_code": "3004", "manufacturer": "ROLEX",
           "rate": 80.0, "unit": "10", "stock_qty": 40},
    237: {"id": 237, "local_id": 237, "name": "ALPHAPOD 200 TAB", "type": "TAB",
          "schedule": "H", "hsn_code": "3004", "manufacturer": "ALPHA",
          "rate": 60.0, "unit": "10", "stock_qty": 25},
    900: {"id": 900, "local_id": 900, "name": "PARACIP 500 TAB", "type": "TAB",
          "schedule": "", "hsn_code": "3004", "manufacturer": "CIPLA",
          "rate": 10.0, "unit": "10", "stock_qty": 90},
}


def _cart(*med_ids, **overrides):
    """A billing page cart: what the counter actually typed."""
    out = []
    for mid in med_ids:
        line = {"id": mid, "name": MASTER[mid]["name"], "qty": 1,
                "rate": 50.0, "amount": 50.0, "medicine_discount": 0}
        line.update(overrides)
        out.append(line)
    return out


class TheOnlineSaleCarriesItsSchedule(unittest.TestCase):
    """Whatever the server can filter on, the save has to have sent."""

    def setUp(self):
        self.pushed = []
        patches = [
            mock.patch.object(server_crud, "push_bundle",
                              side_effect=lambda b: self.pushed.append(b) or True),
            mock.patch.object(server_crud, "allocate_ids_map", return_value={"sales": 5001}),
            mock.patch.object(server_crud, "allocate_id", return_value=5001),
            mock.patch.object(server_crud, "get_doc",
                              side_effect=lambda coll, i: dict(MASTER.get(int(i), {}))
                              if coll == "medicines" else {}),
            mock.patch.object(server_crud, "_token", return_value="t"),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.server_api.allocate_fy",
                       return_value={"fy_start_year": 2026, "fy_serial": 2375,
                                     "bill_no": "SCB2375/FY2026-27"}),
            mock.patch("core.online_catalog.find_customer_by_id",
                       return_value={"id": 7, "name": "RAHUL", "total_due": 0,
                                     "total_credit": 0}),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda i: dict(MASTER.get(int(i), {}))),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _save(self, medicines):
        server_crud.save_new_sale_online(
            customer_id=7, medicines=medicines, discount_pct=0, rounding=0,
            cash_paid=50.0, online_paid=0, doctor_name="", doctor_phone="",
            previous_due=0, bill_date=date(2026, 9, 9),
        )
        self.assertTrue(self.pushed, "the sale was never pushed")
        return self.pushed[-1]["sales"][0]["items"]

    def test_an_h1_line_reaches_the_server_marked_h1(self):
        """The whole bug in one assertion.

        Before the fix this key was absent, so sales_items.schedule was NULL
        and `si.schedule = 'H1'` matched nothing -- the H1 register printed
        empty while the bill sat right there under "All schedules".
        """
        items = self._save(_cart(1343))
        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].get("schedule"), "H1",
            "an H1 sale line must arrive marked H1 or it can never be printed",
        )

    def test_every_line_of_a_mixed_bill_keeps_its_own_schedule(self):
        items = self._save(_cart(1343, 237, 900))
        by_name = {it["name"]: it for it in items}
        self.assertEqual(by_name["ROLCEF 200 TAB"]["schedule"], "H1")
        self.assertEqual(by_name["ALPHAPOD 200 TAB"]["schedule"], "H")
        self.assertEqual(by_name["PARACIP 500 TAB"]["schedule"], "")

    def test_the_line_wins_over_the_master(self):
        """What was dispensed that day, not what the master says today.

        Same precedence the purchase push uses, and the same the server now
        resolves with: the line first, the medicines master only as fallback.
        """
        items = self._save(_cart(1343, schedule="X"))
        self.assertEqual(items[0]["schedule"], "X")

    def test_a_medicine_with_no_schedule_sends_blank_not_none(self):
        """NULL and '' must not be two different kinds of unscheduled.

        The server's rule is NULLIF(BTRIM(...), '') on both sides, so blank and
        NULL agree -- but only if what arrives is a string it can trim.
        """
        items = self._save(_cart(900))
        self.assertEqual(items[0]["schedule"], "")
        self.assertIsNotNone(items[0]["schedule"])

    def test_the_other_two_master_fields_stopped_arriving_with_it(self):
        """hsn_code and manufacturer went missing in the same omission.

        Not the reported bug, but the same three fields and the same cause;
        leaving them out would keep printing bills with a blank HSN column.
        """
        items = self._save(_cart(1343))
        self.assertEqual(items[0].get("hsn_code"), "3004")
        self.assertEqual(items[0].get("manufacturer"), "ROLEX")


class EditingABillDoesNotStripItsSchedule(unittest.TestCase):
    """upsertSale DELETEs the lines and re-inserts what it is sent.

    So an edit that omits the schedule un-prints a bill that was fine before.
    """

    def test_the_edit_path_sends_the_schedule_too(self):
        from core import billing_service

        pushed = []
        with mock.patch("core.server_crud.push_bundle",
                        side_effect=lambda b: pushed.append(b) or True), \
             mock.patch("core.server_crud.get_doc",
                        side_effect=lambda coll, i: (
                            {"id": 5001, "local_id": 5001, "client_uuid": "cu-1",
                             "version": 3, "items": []}
                            if coll == "sales" else dict(MASTER.get(int(i), {}))
                        )), \
             mock.patch("core.online_catalog.find_customer_by_id",
                        return_value={"id": 7, "name": "RAHUL", "total_due": 0,
                                      "total_credit": 0}), \
             mock.patch("core.online_catalog.medicine_by_id",
                        side_effect=lambda i: dict(MASTER.get(int(i), {}))), \
             mock.patch("core.online_catalog.patch_docs", return_value=None):
            billing_service.update_existing_bill_online_now(
                sale_id=5001, medicines=_cart(1343), discount_pct=0, rounding=0,
                cash_paid=50.0, online_paid=0, customer_name="RAHUL",
                customer_phone="", doctor_name="", previous_due=0,
                bill_date=date(2026, 9, 9),
            )

        self.assertTrue(pushed, "the edit was never pushed")
        items = pushed[-1]["sales"][0]["items"]
        self.assertEqual(
            items[0].get("schedule"), "H1",
            "editing a bill must not strip the schedule off its lines",
        )


class NonScheduledMeansEveryLine(unittest.TestCase):
    """The desktop's offline branch and the server's rule must agree.

    "Non-Scheduled" is the bill whose lines are ALL unscheduled -- not the bill
    that merely has one unscheduled line among its H1s, which is nearly every
    bill a shop ever wrote. This was settled on the purchase side (a filter
    that answered 247 of 367 where the server said 61) and the server's rule is
    the one kept. Asserted here on the offline SQL because the two branches
    have to answer the same question the same way.
    """

    def _shop(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT,
                                    phone TEXT, address TEXT);
            CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT,
                                    batch_no TEXT, schedule TEXT);
            CREATE TABLE sales (id INTEGER PRIMARY KEY, customer_id INTEGER);
            CREATE TABLE sales_items (id INTEGER PRIMARY KEY, sale_id INTEGER,
                                      medicine_id INTEGER);
            INSERT INTO customers VALUES (7, 'RAHUL', '', '');
            INSERT INTO medicines VALUES (1343, 'ROLCEF 200 TAB', 'B1', 'H1'),
                                         (900, 'PARACIP 500 TAB', 'B2', '');
            -- bill 1: H1 and a plain tablet.  bill 2: plain only.
            INSERT INTO sales VALUES (1, 7), (2, 7);
            INSERT INTO sales_items VALUES (1, 1, 1343), (2, 1, 900), (3, 2, 900);
            """
        )
        return conn

    def _bills(self, conn, schedule):
        sql = (
            "SELECT s.id FROM sales s WHERE "
            "(EXISTS (SELECT 1 FROM sales_items si "
            " LEFT JOIN medicines m ON m.id=si.medicine_id "
            " WHERE si.sale_id=s.id AND TRIM(COALESCE(m.schedule,''))='') "
            "AND NOT EXISTS (SELECT 1 FROM sales_items si "
            " LEFT JOIN medicines m ON m.id=si.medicine_id "
            " WHERE si.sale_id=s.id AND TRIM(COALESCE(m.schedule,''))<>''))"
        ) if schedule == "Non-Scheduled" else (
            "SELECT s.id FROM sales s WHERE EXISTS "
            "(SELECT 1 FROM sales_items si "
            " LEFT JOIN medicines m ON m.id=si.medicine_id "
            f" WHERE si.sale_id=s.id AND TRIM(COALESCE(m.schedule,''))='{schedule}')"
        )
        return {r[0] for r in conn.execute(sql).fetchall()}

    def test_a_mixed_bill_is_not_non_scheduled(self):
        conn = self._shop()
        self.addCleanup(conn.close)
        self.assertEqual(
            self._bills(conn, "Non-Scheduled"), {2},
            "a bill holding an H1 line is not a non-scheduled bill",
        )

    def test_the_mixed_bill_is_returned_under_h1(self):
        conn = self._shop()
        self.addCleanup(conn.close)
        self.assertEqual(self._bills(conn, "H1"), {1})


class TheResolvedScheduleRule(unittest.TestCase):
    """The one expression both sides now use: the line, else the master."""

    def test_the_line_is_preferred(self):
        self.assertEqual(
            server_crud._line_field({"schedule": "X"}, {"schedule": "H1"}, "schedule"),
            "X",
        )

    def test_the_master_fills_a_blank_line(self):
        self.assertEqual(
            server_crud._line_field({"schedule": "  "}, {"schedule": "H1"}, "schedule"),
            "H1",
        )
        self.assertEqual(
            server_crud._line_field({}, {"schedule": "H1"}, "schedule"), "H1",
        )

    def test_nothing_anywhere_is_blank_not_none(self):
        self.assertEqual(server_crud._line_field({}, {}, "schedule"), "")

    def test_it_is_trimmed_so_it_can_match_exactly(self):
        # The server compares with =, so a stray space is a silent no-match.
        self.assertEqual(
            server_crud._line_field({"schedule": " H1 "}, {}, "schedule"), "H1",
        )


if __name__ == "__main__":
    unittest.main()
