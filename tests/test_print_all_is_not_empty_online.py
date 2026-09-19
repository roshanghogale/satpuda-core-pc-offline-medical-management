"""Print All printed nothing at all, and called it "no bills in the range".

Reported from the counter: "Print All Bills" does not print all the bills.

In Online mode it printed NONE of them. The engine keeps no business data of
its own there -- its database is an empty in-memory SQLite -- and
fetch_sales_for_print had only a local SQL query. So it matched nothing, every
time, on every range. The dialog opened, said "Print 0 bill(s)", and refused.

What made it look like a printer fault rather than a missing branch: printing
ONE bill kept working, because that path (bill_output._load_sale_data) has an
online fallback and this one never had.

Two more things the same complaint was carrying. The count in the dialog and
the set that actually printed were separate derivations -- the page threw the
bill list away and the engine went and queried again -- so they could disagree.
And offline, the picker's query and the history list's query disagreed about
which bills exist: the picker used an INNER JOIN on customers (dropping a bill
whose customer row is missing) and did not hide autosave drafts (printing bills
the list deliberately does not show).
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, history_print, sync_prefs  # noqa: E402

SERVER_ROWS = [
    {"id": 11, "bill_no": "SCB10", "bill_date": "2026-09-02", "customer_name": "RAHUL",
     "total_amount": 240.0, "schedules": "H1"},
    {"id": 12, "bill_no": "SCB11", "bill_date": "2026-09-01", "customer_name": "SEEMA",
     "total_amount": 90.5, "schedules": ""},
    {"id": 13, "bill_no": "SCB12", "bill_date": "2026-09-03", "customer_name": "",
     "total_amount": 12.0, "schedules": "H"},
]


class OnlineModeCanFindTheBills(unittest.TestCase):
    """The whole bug: an empty local database was the only place it looked."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: True
        # Online mode's engine connection: an empty in-memory shell.
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _fetch(self, rows):
        with mock.patch("core.store_query_client.list_sales", return_value={"rows": rows}), \
             mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]):
            return history_print.fetch_sales_for_print(
                self.conn, "2026-09-01", "2026-09-30"
            )

    def test_the_bills_on_the_server_are_found(self):
        got = self._fetch(SERVER_ROWS)
        self.assertEqual(len(got), 3, "an empty local database is not an empty shop")
        self.assertEqual({b["sale_id"] for b in got}, {11, 12, 13})

    def test_they_come_back_oldest_first_like_the_local_query(self):
        got = self._fetch(SERVER_ROWS)
        self.assertEqual([b["bill_date"] for b in got],
                         ["2026-09-01", "2026-09-02", "2026-09-03"])

    def test_a_bill_carries_what_the_picker_shows(self):
        got = self._fetch([SERVER_ROWS[0]])[0]
        self.assertEqual(got["bill_no"], "SCB10")
        self.assertEqual(got["customer"], "RAHUL")
        self.assertEqual(got["total"], 240.0)
        self.assertEqual(got["schedules"], "H1")

    def test_a_deleted_bill_does_not_come_back_on_paper(self):
        rows = SERVER_ROWS + [dict(SERVER_ROWS[0], id=99, deleted=True)]
        self.assertNotIn(99, {b["sale_id"] for b in self._fetch(rows)})

    def test_a_draft_is_not_printed(self):
        # The history list hides autosave drafts; the printer must too.
        rows = SERVER_ROWS + [dict(SERVER_ROWS[0], id=98, is_autosave=1)]
        self.assertNotIn(98, {b["sale_id"] for b in self._fetch(rows)})

    def test_a_bill_not_yet_pushed_is_shown_but_cannot_be_ticked(self):
        """It used to vanish in silence, so the picker showed fewer bills than
        the list behind it and nothing said why."""
        rows = SERVER_ROWS + [dict(SERVER_ROWS[0], id=0, local_id=0)]
        got = self._fetch(rows)
        self.assertEqual(len(got), 4, "it is listed now")
        unprintable = [b for b in got if not b["printable"]]
        self.assertEqual(len(unprintable), 1)
        self.assertIn("sync", unprintable[0]["reason"].lower())

    def test_a_server_that_cannot_be_reached_says_so(self):
        """The failure that hid this bug: silence read as "you have no bills"."""
        with mock.patch("core.store_query_client.list_sales",
                        side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError) as ctx:
                history_print.fetch_sales_for_print(self.conn, "2026-09-01", "2026-09-30")
        self.assertIn("server", str(ctx.exception).lower())

    def test_the_page_is_told_why_instead_of_being_shown_zero(self):
        from core.desktop_sales_service import list_print_all_candidates

        with mock.patch("core.store_query_client.list_sales",
                        side_effect=RuntimeError("connection refused")):
            res = list_print_all_candidates(
                self.conn, {"from": "2026-09-01", "to": "2026-09-30"}
            )
        self.assertFalse(res["ok"])
        self.assertIn("server", res["error"].lower())


class OfflineAgreesWithTheHistoryList(unittest.TestCase):
    """The picker and the list must not disagree about which bills exist."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self.conn.execute("DELETE FROM sales")
        cid = self.conn.execute(
            "INSERT INTO customers (name, phone) VALUES ('RAHUL', '9')"
        ).lastrowid
        self._ins(bill_no="SCB10", customer_id=cid, customer_name="RAHUL")
        # A bill whose customer row is gone -- pulled from another device with
        # customer_id 0. The list shows it; the picker used to drop it.
        self._ins(bill_no="SCB11", customer_id=0, customer_name="WALK IN")
        self._ins(bill_no="DRAFT", customer_id=cid, customer_name="RAHUL",
                  is_autosave=1)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _ins(self, **vals):
        base = {"bill_date": "2026-09-02", "total_amount": 100.0}
        base.update(vals)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(sales)")]
        use = {k: v for k, v in base.items() if k in cols}
        self.conn.execute(
            f"INSERT INTO sales ({','.join(use)}) "
            f"VALUES ({','.join('?' * len(use))})",
            list(use.values()),
        )

    def _bills(self):
        return history_print.fetch_sales_for_print(
            self.conn, "2026-09-01", "2026-09-30"
        )

    def test_a_bill_with_no_customer_row_is_still_printable(self):
        nos = {b["bill_no"] for b in self._bills()}
        self.assertIn("SCB11", nos, "the history list shows it; so must the printer")

    def test_that_bill_still_has_a_name_on_it(self):
        row = next(b for b in self._bills() if b["bill_no"] == "SCB11")
        self.assertEqual(row["customer"], "WALK IN")

    def test_a_draft_is_not_offered_for_printing(self):
        self.assertNotIn("DRAFT", {b["bill_no"] for b in self._bills()})


class TheSetThatPrintsIsTheSetThatWasCounted(unittest.TestCase):

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def test_the_chosen_ids_are_printed_and_nothing_is_re_queried(self):
        from core.desktop_sales_service import print_all_sales

        seen = {}

        def fake_batch(conn, sale_ids, **kw):
            seen["ids"] = list(sale_ids)
            seen["paper"] = kw.get("paper")
            return len(sale_ids), []

        with mock.patch("core.history_print.print_bills_batch", side_effect=fake_batch), \
             mock.patch("core.history_print.fetch_sales_for_print",
                        side_effect=AssertionError("must not re-query")):
            res = print_all_sales(
                self.conn,
                {"from": "2026-09-01", "to": "2026-09-30",
                 "sale_ids": [11, 13], "paper": "A5"},
            )
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(seen["ids"], [11, 13])
        self.assertEqual(seen["paper"], "A5", "the paper the shop chose, not a hardcoded A6")

    def test_a_selection_that_cannot_all_be_printed_is_refused_not_trimmed(self):
        # Printing fewer bills than were ticked, with no failure listed, is how
        # a day's bills go missing without anyone noticing.
        from core.desktop_sales_service import print_all_sales

        with mock.patch("core.history_print.print_bills_batch",
                        side_effect=AssertionError("must not print")):
            res = print_all_sales(
                self.conn, {"sale_ids": [11, 0, 13], "from": "a", "to": "b"}
            )
        self.assertFalse(res["ok"])
        self.assertIn("1 of the 3", res["error"])



class ThePickerAndThePrinterAgree(unittest.TestCase):
    """Things an adversarial pass found after the first fix landed."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: True
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _fetch(self, rows, schedule=""):
        self.sent = {}

        def fake_list_sales(**kw):
            self.sent = kw
            return {"rows": rows}

        with mock.patch("core.store_query_client.list_sales", side_effect=fake_list_sales), \
             mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]):
            return history_print.fetch_sales_for_print(
                self.conn, "2026-09-01", "2026-09-30", schedule
            )

    def test_the_schedule_is_asked_of_the_server(self):
        # A server sale row carries no per-bill schedule aggregate, so a filter
        # applied here would have matched nothing and emptied the selection.
        self._fetch(SERVER_ROWS, "H1")
        self.assertEqual(self.sent.get("schedule"), "H1")

    def test_no_schedule_asks_for_everything(self):
        self._fetch(SERVER_ROWS)
        self.assertEqual(self.sent.get("schedule"), "")

    def test_a_queued_edit_is_listed_but_refused(self):
        """Printing it would fetch the OLD server document, so the paper would
        not match what the picker showed -- but the shop should still see the
        bill and be told why it cannot go yet."""
        rows = SERVER_ROWS + [dict(SERVER_ROWS[0], id=77, bill_no="PENDING")]
        got = {b["sale_id"]: b for b in self._fetch(rows)}
        self.assertIn(77, got)
        self.assertFalse(got[77]["printable"])
        self.assertIn("synced", got[77]["reason"].lower())

    def test_an_overlay_row_outside_the_window_is_not_smuggled_in(self):
        rows = SERVER_ROWS + [dict(SERVER_ROWS[0], id=78, bill_date="2026-03-11")]
        self.assertNotIn(78, {b["sale_id"] for b in self._fetch(rows)})

    def test_a_half_printed_batch_says_what_failed(self):
        """An ok:False with no "error" reaches the page as a bare "HTTP 400"."""
        from core.desktop_sales_service import print_all_sales

        with mock.patch("core.history_print.print_bills_batch",
                        return_value=(8, ["Sale 3: printer offline"])):
            res = print_all_sales(self.conn, {"sale_ids": [1, 2, 3], "from": "a", "to": "b"})
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("error"), "without this the page shows only HTTP 400")
        self.assertIn("8 page(s)", res["error"])
        self.assertEqual(res["failures"], ["Sale 3: printer offline"])


class NonScheduledMeansNoCodeAtAll(unittest.TestCase):
    """The label the history list and the old picker both read as "no tokens"."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self.conn.execute("DELETE FROM sales")
        self.conn.execute("DELETE FROM medicines")
        cid = self.conn.execute(
            "INSERT INTO customers (name, phone) VALUES ('RAHUL', '9')"
        ).lastrowid
        h1 = self.conn.execute(
            "INSERT INTO medicines (name, schedule, stock_qty) VALUES ('ALPRAX', 'H1', 5)"
        ).lastrowid
        plain = self.conn.execute(
            "INSERT INTO medicines (name, schedule, stock_qty) VALUES ('CALPOL', '', 5)"
        ).lastrowid
        for bill_no, mid in (("SCHED", h1), ("PLAIN", plain)):
            sid = self.conn.execute(
                "INSERT INTO sales (bill_no, bill_date, customer_id, customer_name, "
                "total_amount) VALUES (?, '2026-09-02', ?, 'RAHUL', 100)",
                (bill_no, cid),
            ).lastrowid
            self.conn.execute(
                "INSERT INTO sales_items (sale_id, medicine_id, qty) VALUES (?,?,1)",
                (sid, mid),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _nos(self, schedule=""):
        return {
            b["bill_no"]
            for b in history_print.fetch_sales_for_print(
                self.conn, "2026-09-01", "2026-09-30", schedule
            )
        }

    def test_no_filter_offers_both(self):
        self.assertEqual(self._nos(), {"SCHED", "PLAIN"})

    def test_a_real_code_selects_only_its_bills(self):
        self.assertEqual(self._nos("H1"), {"SCHED"})

    def test_non_scheduled_selects_the_ordinary_bills_not_none(self):
        # Compared as a token it matches nothing, so the whole selection
        # emptied and the Print button went dead.
        self.assertEqual(self._nos("Non-Scheduled"), {"PLAIN"})


class ACashBillFinishedWithoutSplittingPays(unittest.TestCase):
    """#10: the first save of a cash bill was refused, every time.

    Finishing a bill without touching Cash or Online means the whole amount was
    taken in cash. The engine has always had a pay_full branch that works the
    figure out itself -- deliberately, because the screen's total comes from a
    debounced preview and a discount typed a moment before Enter would not be
    in it yet. Nothing ever set the flag, so the engine's own guard refused the
    bill and the counter typed an amount and saved again. That second press is
    the whole complaint.
    """

    def test_the_engine_still_refuses_when_nothing_says_pay_in_full(self):
        import core.desktop_sales_service as svc

        src = open(svc.__file__, encoding="utf-8").read()
        self.assertIn('"code": "payment_required"', src,
                      "a cash bill with no payment and no pay_full is still refused")

    def test_the_pay_full_branch_computes_the_amount_itself(self):
        import core.desktop_sales_service as svc

        src = open(svc.__file__, encoding="utf-8").read()
        i = src.index('if not is_due and (cash + online) <= 0 and bool(body.get("pay_full")):')
        branch = src[i:i + 900]
        self.assertIn("calc_bill_summary", branch,
                      "the figure must not come from the screen's debounced preview")

    def test_the_screen_now_sets_the_flag(self):
        p = "desktop/src/pages/SalesPage.tsx"
        src = open(p, encoding="utf-8").read()
        i = src.index("const payFull = payFullRef.current")
        # The arming has to happen before the flag is read, in saveSales.
        self.assertIn("if (wantsPayFull()) payFullRef.current = true", src[:i],
                      "nothing set pay_full, so every such bill was refused once")

    def test_a_due_bill_is_never_marked_paid(self):
        p = "desktop/src/pages/SalesPage.tsx"
        src = open(p, encoding="utf-8").read()
        i = src.index("const wantsPayFull = () => {")
        body = src[i:i + 320]
        self.assertIn("=== 'due'", body)
        self.assertIn("return false", body)


class ThePickerOffersWhatTheListShows(unittest.TestCase):
    """#4: the picker took only the dates, so it offered every bill in the
    range while the list behind it showed the few the shop had filtered to."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: True
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _fetch(self, **kw):
        with mock.patch("core.store_query_client.list_sales",
                        return_value={"rows": SERVER_ROWS}), \
             mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]):
            return history_print.fetch_sales_for_print(
                self.conn, "2026-09-01", "2026-09-30", **kw
            )

    def test_no_filter_offers_everything(self):
        self.assertEqual(len(self._fetch()), 3)

    def test_a_customer_filter_narrows_it(self):
        got = self._fetch(customer="RAHUL")
        self.assertEqual([b["customer"] for b in got], ["RAHUL"])

    def test_the_search_box_matches_a_bill_number(self):
        self.assertEqual([b["bill_no"] for b in self._fetch(q="SCB11")], ["SCB11"])

    def test_the_search_box_also_matches_a_name(self):
        self.assertEqual([b["customer"] for b in self._fetch(q="seema")], ["SEEMA"])

    def test_filters_the_picker_cannot_honour_are_named_not_hidden(self):
        from core.desktop_sales_service import list_print_all_candidates

        with mock.patch("core.store_query_client.list_sales",
                        return_value={"rows": SERVER_ROWS}), \
             mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]):
            res = list_print_all_candidates(self.conn, {
                "from": "2026-09-01", "to": "2026-09-30",
                "medicine": "AMOXY", "batch": "B1", "due": "due",
            })
        self.assertEqual(res["unapplied_filters"], ["Medicine", "Batch", "Due status"])

    def test_an_absent_server_schedule_is_not_reported_as_non_scheduled(self):
        """"" from the server means UNKNOWN; the picker printed it as a fact."""
        rows = [dict(SERVER_ROWS[0])]
        rows[0].pop("schedules", None)
        with mock.patch("core.store_query_client.list_sales", return_value={"rows": rows}), \
             mock.patch("core.online_mutation_queue.overlay_sales_dicts", return_value=[]):
            got = history_print.fetch_sales_for_print(
                self.conn, "2026-09-01", "2026-09-30"
            )
        self.assertFalse(got[0]["schedules_known"])

    def test_the_local_query_really_does_know(self):
        from core import db_setup
        sync_prefs.is_online_mode = lambda *a, **k: False
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db_setup.initialise(conn)
        conn.execute("DELETE FROM sales")
        conn.execute(
            "INSERT INTO sales (bill_no, bill_date, customer_id, customer_name,"
            " total_amount) VALUES ('S1', '2026-09-02', 0, 'WALK IN', 10)"
        )
        conn.commit()
        got = history_print.fetch_sales_for_print(conn, "2026-09-01", "2026-09-30")
        conn.close()
        self.assertTrue(got[0]["schedules_known"])


class TheA6RunHonoursTheChosenPaper(unittest.TestCase):
    """#4: the A6 branch forwarded the caller's override, which is None on every
    desktop Print All -- so each bill was re-resolved from the slot on disk."""

    def test_the_batch_passes_its_own_resolved_settings(self):
        src = open("core/history_print.py", encoding="utf-8").read()
        i = src.index('if paper_u == "A6":')
        branch = src[i:i + 1200]
        self.assertIn("settings_override=render_settings", branch)
        self.assertNotIn("settings_override=settings_override", branch)

    def test_render_settings_carries_the_chosen_paper_flat(self):
        # bill_output resolves the slot from disk BEFORE applying the override,
        # so only a flat top-level paper_size survives.
        src = open("core/history_print.py", encoding="utf-8").read()
        self.assertIn('render_settings["paper_size"] = paper_u', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
