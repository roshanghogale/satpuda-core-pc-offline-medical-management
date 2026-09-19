"""A back-dated sale bill uses only what the shop had on that date, and that year's series.

A missed paper bill entered today with an old Bill Date may only carry batches the shop
already had then and that had not expired by then. Expiry was checked against the bill
date; "already had" was checked offline only (and there by the first purchase in
preference to the day the row was created, so opening stock restocked later vanished),
never Online, and nothing checked the lines again at save. The Invoice No box showed
today's next number whatever the date. Everything runs on an in-memory database or
patched server answers.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import (  # noqa: E402
    db_setup,
    desktop_pages_service,
    desktop_sales_service,
    online_catalog,
    sale_availability,
)
from core.batch_visibility import medicine_existed_sql

BILL_DATE = "2026-03-20"

# id, name, batch, created_at, expiry
MEDICINES = [
    (1, "OPENING SYRUP", "OS1", "2026-01-10 09:00:00", "2027-12-01"),  # in since January, restocked in May
    (2, "BACKDATED TAB", "BD1", "2026-09-13 10:00:00", "2027-12-01"),  # entered today, bought 1 March
    (3, "NEW ARRIVAL CAP", "NA1", "2026-06-01 10:00:00", "2027-12-01"),  # first came in June
    (4, "OLD SYRUP", "EX1", "2025-06-01 10:00:00", "2026-02-01"),  # good until 28 Feb 2026
]
# purchase id, date, medicine, deleted, draft -- NEW ARRIVAL's February purchase was
# deleted and its other February entry is an unfinished draft: neither brought it in.
PURCHASES = [
    (1, "2026-05-01", 1, 0, 0),
    (2, "2026-03-01", 2, 0, 0),
    (3, "2026-06-01", 3, 0, 0),
    (4, "2026-02-01", 3, 1, 0),
    (5, "2026-02-02", 3, 0, 1),
]


def _shop() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
    conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
    for mid, name, batch, created, expiry in MEDICINES:
        conn.execute(
            "INSERT INTO medicines (id, name, type, unit, gst_percent, mrp, rate, stock_qty, "
            "batch_no, expiry_date, created_at) VALUES (?, ?, 'SYRUP', '1', 12, 100, 60, 50, ?, ?, ?)",
            (mid, name, batch, expiry, created),
        )
    for pid, pdate, mid, deleted, draft in PURCHASES:
        conn.execute(
            "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, deleted, is_autosave) "
            "VALUES (?, ?, 1, ?, ?, ?)",
            (pid, f"{pid}/FY2025-26", pdate, deleted, draft),
        )
        conn.execute(
            "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate) VALUES (?, ?, 10, 60)",
            (pid, mid),
        )
    conn.commit()
    return conn


def _line(mid: int) -> dict:
    _, name, batch, _, expiry = next(m for m in MEDICINES if m[0] == mid)
    return {"id": mid, "name": name, "batch": batch, "expiry": expiry, "qty": 1,
            "rate": 100.0, "amount": 100.0}


class OfflineTheShopsOwnRecordsDecide(unittest.TestCase):
    def setUp(self):
        self.conn = _shop()
        self.addCleanup(self.conn.close)

    def test_the_earlier_of_first_purchase_and_created_at_decides(self):
        rows = self.conn.execute(
            f"SELECT id FROM medicines WHERE {medicine_existed_sql()} ORDER BY id", (BILL_DATE,)
        ).fetchall()
        self.assertEqual([r[0] for r in rows], [1, 2, 4])

    def test_the_tauri_picker_offers_only_what_was_there_and_not_expired(self):
        names = desktop_sales_service.list_medicine_names(self.conn, "", bill_date=BILL_DATE)
        self.assertEqual(sorted(n["name"] for n in names["names"]),
                         ["BACKDATED TAB", "OPENING SYRUP"])
        batches = desktop_sales_service.list_batches_for_name(
            self.conn, "NEW ARRIVAL CAP", bill_date=BILL_DATE)
        self.assertEqual(batches["batches"], [])

    def test_adding_a_batch_that_came_later_is_refused(self):
        later = desktop_sales_service.build_line(
            self.conn, {"medicine_id": 3, "qty": 1, "bill_date": BILL_DATE})
        self.assertEqual(later.get("code"), "not_available_yet", later)
        there = desktop_sales_service.build_line(
            self.conn, {"medicine_id": 2, "qty": 1, "bill_date": BILL_DATE})
        self.assertTrue(there.get("ok"), there)

    def test_the_save_checks_lines_picked_before_the_date_was_changed(self):
        problems = sale_availability.lines_unavailable_on(
            self.conn, [_line(1), _line(2), _line(3), _line(4)], BILL_DATE)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("NEW ARRIVAL CAP", problems[0])
        self.assertIn("was added after 2026-03-20", problems[0])
        self.assertIn("OLD SYRUP", problems[1])
        self.assertIn("expired", problems[1])

    def test_a_new_bill_is_refused_before_anything_is_written(self):
        body = {"items": [_line(2), _line(3)], "bill_date": BILL_DATE, "customer_name": "RAM",
                "payment_mode": "Cash", "cash_paid": 200.0}
        with mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True), \
                mock.patch("core.billing_service.save_new_bill") as save:
            res = desktop_sales_service.save_sale(self.conn, body)
        self.assertEqual(res.get("code"), "not_available_on_date", res)
        self.assertEqual(len(res.get("messages") or []), 1)
        save.assert_not_called()

    def test_an_edit_of_an_existing_bill_is_left_as_it_was_sold(self):
        body = {"items": [_line(3)], "bill_date": BILL_DATE, "customer_name": "RAM",
                "payment_mode": "Cash", "cash_paid": 100.0, "editing_sale_id": 99}
        with mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True), \
                mock.patch("core.desktop_returns_service.edit_below_returned_error", return_value=None), \
                mock.patch("core.billing_service.update_existing_bill") as update:
            res = desktop_sales_service.save_sale(self.conn, body)
        self.assertNotEqual(res.get("code"), "not_available_on_date", res)
        update.assert_called_once()


class OnlineTheStoreSaysWhatCameInWhen(unittest.TestCase):
    MEDICINES = [
        {"id": 11, "name": "OLD TAB", "created_at": "2026-01-05T04:00:00.000Z"},
        {"id": 12, "name": "BACKDATED TAB", "created_at": "2026-09-13T05:00:00.000Z"},
        {"id": 13, "name": "NEW ARRIVAL CAP", "created_at": "2026-06-01T05:00:00.000Z"},
        {"id": 14, "name": "NO DATE", "created_at": None},
    ]
    # Purchases updated after the bill date: a back-dated one for 12, the real one for 13,
    # and a February DRAFT for 13 that brought nothing in.
    PURCHASES = [
        {"id": 5, "purchase_date": "2026-03-01", "is_autosave": False, "items": [{"medicine_id": 12}]},
        {"id": 6, "purchase_date": "2026-06-01", "items": [{"medicine_id": 13}]},
        {"id": 7, "purchase_date": "2026-02-02", "is_autosave": True, "items": [{"medicine_id": 13}]},
    ]

    def setUp(self):
        sale_availability.invalidate()
        self.addCleanup(sale_availability.invalidate)
        self.pulls: list[tuple[str, object]] = []
        # Not `self.fail`: that is TestCase.fail, and shadowing it turned every failed
        # assertion in this class into "TypeError: 'bool' object is not callable".
        self.server_down = False

        def pull(token, collection, *, since=None, after_id=None, include_deleted=True,
                 limit=5000, timeout=120.0):
            self.pulls.append((collection, since))
            if self.server_down:
                raise RuntimeError("server unreachable")
            docs = self.MEDICINES if collection == "medicines" else self.PURCHASES
            return [dict(d) for d in docs], {}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_api.pull_collection", side_effect=pull),
            mock.patch("core.online_catalog._token", return_value="t"),
        ):
            stack.enter_context(patch)

    def test_only_a_batch_that_came_in_after_the_date_is_missing(self):
        self.assertEqual(sale_availability.batches_missing_on_online(BILL_DATE), frozenset({13}))
        self.assertIn(("purchases", BILL_DATE), self.pulls)

    def test_today_needs_no_question_to_the_store(self):
        from datetime import date

        self.assertEqual(sale_availability.batches_missing_on_online(date.today()), frozenset())
        self.assertEqual(self.pulls, [])

    def test_the_answer_is_kept_until_something_changes(self):
        sale_availability.batches_missing_on_online(BILL_DATE)
        asked = len(self.pulls)
        sale_availability.batches_missing_on_online(BILL_DATE)
        self.assertEqual(len(self.pulls), asked)
        online_catalog.invalidate("purchases")
        sale_availability.batches_missing_on_online(BILL_DATE)
        self.assertGreater(len(self.pulls), asked)

    def test_a_store_that_cannot_answer_hides_nothing_and_is_asked_again_a_minute_later(self):
        import time

        self.server_down = True
        self.assertEqual(sale_availability.batches_missing_on_online(BILL_DATE), frozenset())
        self.server_down = False
        # The failure is remembered for a minute, so each added line does not wait again.
        self.assertEqual(sale_availability.batches_missing_on_online(BILL_DATE), frozenset())
        with mock.patch("time.monotonic", return_value=time.monotonic() + 61):
            self.assertEqual(
                sale_availability.batches_missing_on_online(BILL_DATE), frozenset({13})
            )

    def _catalog_row(self, mid, name):
        return {"id": mid, "local_id": mid, "name": name, "batch_no": f"B{mid}",
                "expiry_date": "2027-12-01", "stock_qty": 5, "mrp": 100, "rate": 60,
                "unit": "1", "type": "TAB", "is_hidden": 0, "gst_percent": 12}

    def test_the_batch_list_and_the_name_list_leave_it_out(self):
        rows = [self._catalog_row(13, "PARA"), self._catalog_row(11, "PARA")]
        with mock.patch.object(online_catalog, "medicines_for_name", return_value=rows):
            self.assertEqual([b["id"] for b in online_catalog.batches_for_name(
                "PARA", as_of=BILL_DATE)], [11])
        by_name = {"new arrival cap": [self._catalog_row(13, "NEW ARRIVAL CAP")],
                   "old tab": [self._catalog_row(11, "OLD TAB")]}
        with mock.patch.object(online_catalog, "medicines", return_value=[]), \
                mock.patch.dict(online_catalog._medicine_by_name, by_name, clear=True):
            names = online_catalog.search_medicine_names("", as_of=BILL_DATE, show_zero=False)
        self.assertEqual([n["name"] for n in names], ["OLD TAB"])

    def test_adding_it_online_is_refused(self):
        conn = sqlite3.connect(":memory:")  # Online: nothing may read it
        self.addCleanup(conn.close)
        with mock.patch("core.online_catalog.medicine_by_id",
                        return_value=self._catalog_row(13, "NEW ARRIVAL CAP")):
            res = desktop_sales_service.build_line(
                conn, {"medicine_id": 13, "qty": 1, "bill_date": BILL_DATE})
        self.assertEqual(res.get("code"), "not_available_yet", res)


class TheInvoiceNoFollowsTheBillDate(unittest.TestCase):
    def test_a_back_dated_bill_shows_that_years_next_number(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
        for bill_no, bill_date, fy, serial in (
            ("SCB150/FY2025-26", "2026-03-10", 2025, 150),
            ("SCB4/FY2026-27", "2026-04-05", 2026, 4),
        ):
            conn.execute(
                "INSERT INTO sales (bill_no, customer_id, bill_date, total_amount, fy_start_year, "
                "fy_serial) VALUES (?, 1, ?, 10, ?, ?)",
                (bill_no, bill_date, fy, serial),
            )
        self.assertEqual(desktop_pages_service._next_sales_bill_hint(conn, BILL_DATE), "SCB151")
        self.assertEqual(desktop_pages_service._next_sales_bill_hint(conn, "2026-04-06"), "SCB5")


class TheV3SaleSaveTakesWhatClassicSends(unittest.TestCase):
    def test_customer_name_and_phone_reach_the_save(self):
        from core.sync_v3.repositories import sale_repository

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        with mock.patch("core.billing_service.save_new_bill", return_value=("SCB1", 1)) as legacy:
            out = sale_repository.save_new_bill_v3(
                conn, 1, [], 0, 0, 10.0, 0, "", "", 0,
                bill_date=BILL_DATE, customer_name="RAM", customer_phone="99",
            )
        self.assertEqual(out, ("SCB1", 1))
        self.assertEqual(legacy.call_args.kwargs["customer_name"], "RAM")
        self.assertEqual(legacy.call_args.kwargs["customer_phone"], "99")


if __name__ == "__main__":
    unittest.main()
