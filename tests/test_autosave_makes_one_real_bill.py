"""Autosave must produce a REAL bill, and keep updating that same one.

Autosave used to write an ``is_autosave=1`` draft numbered ``ASV…``. A draft is
filtered out of history, the ledger, stock and every report, and Online it was
written to the engine's ``:memory:`` sqlite and never reached the server -- so
the thing the counter thought was saved did not exist anywhere that mattered.

The owner asked for the opposite: the first tick makes a real saved bill, every
later tick UPDATES that one bill, and all of a day's counter sales go into a
single counter bill.

The money in these tests is in the repeats. A tick that ADDS the form to the
counter bill instead of replacing its own previous contribution charges the same
customer again every couple of minutes, and the second such bug -- saving after
autosaving -- doubles the last one. Both are asserted below.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import autosave_bill, autosave_session, db_setup, sync_prefs  # noqa: E402
from core import billing_service  # noqa: E402

import datetime as _dt  # noqa: E402

# The bills in this suite are dated 2026-09-10. The session file forgets a day's counter
# pointer once that day is more than a week old (autosave_session._prune), so with the real
# calendar every pointer here is dropped from 2026-09-18 on and the counter tests fail by
# date alone. The session module's "today" is held at the suite's bill day.
BILL_DAY = _dt.date(2026, 9, 10)


class _BillDay(_dt.date):
    @classmethod
    def today(cls):
        return cls(BILL_DAY.year, BILL_DAY.month, BILL_DAY.day)


def _store():
    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    db_setup.initialise(conn)
    return conn


def _medicine(conn, name, stock=100, rate=10.0):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(medicines)")]
    # In the shop well before the bills' dates: a save refuses a batch that came in after
    # the bill's own date (core.sale_availability), and the default created_at is today.
    vals = {"name": name, "stock_qty": stock, "mrp": rate, "rate": rate,
            "created_at": "2026-01-01 00:00:00"}
    use = {k: v for k, v in vals.items() if k in cols}
    mid = conn.execute(
        f"INSERT INTO medicines ({','.join(use)}) "
        f"VALUES ({','.join('?' * len(use))})",
        list(use.values()),
    ).lastrowid
    conn.commit()
    return mid


def _line(mid, qty, rate=10.0, name="MED"):
    return {
        "id": mid,
        "name": name,
        "qty": qty,
        "rate": rate,
        "amount": round(qty * rate, 2),
        "medicine_discount": 0,
        "gst_percent": 0,
        "batch": "",
        "expiry": "",
        "schedule": "",
    }


def _real_bills(conn):
    return conn.execute(
        "SELECT id, bill_no FROM sales "
        "WHERE COALESCE(is_autosave,0)=0 AND COALESCE(deleted,0)=0 "
        "ORDER BY id"
    ).fetchall()


def _qty(conn, sale_id, med_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(qty),0) FROM sales_items "
        "WHERE sale_id=? AND medicine_id=?",
        (sale_id, med_id),
    ).fetchone()
    return float(row[0] or 0)


def _stock(conn, med_id):
    return float(
        conn.execute(
            "SELECT stock_qty FROM medicines WHERE id=?", (med_id,)
        ).fetchone()[0]
    )


class AutosaveBase(unittest.TestCase):
    def setUp(self):
        self._sessions = tempfile.mktemp(suffix=".json")
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = self._sessions
        self._store_patch = mock.patch.object(
            autosave_session, "_store_key", lambda: "test-store"
        )
        self._store_patch.start()
        self._day_patch = mock.patch.object(autosave_session, "date", _BillDay)
        self._day_patch.start()
        self.addCleanup(self._day_patch.stop)
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = _store()

    def tearDown(self):
        sync_prefs.is_online_mode = self._online
        self._store_patch.stop()
        os.environ.pop("SATPUDA_AUTOSAVE_SESSION_FILE", None)
        for path in (self._sessions,):
            try:
                os.remove(path)
            except OSError:
                pass

    def _customer(self, name):
        from core.customer_service import get_or_create_customer

        return get_or_create_customer(self.conn, name, "", "")

    def tick(self, *, name, lines, token="", sale_id=0, cash=None,
             bill_date="2026-09-10", final=False, doctor=""):
        total = sum(l["amount"] for l in lines)
        return autosave_bill.write_autosave_bill(
            self.conn,
            token=token,
            sale_id=sale_id,
            customer_id=self._customer(name),
            customer_name=name,
            customer_phone="",
            medicines=lines,
            discount_pct=0,
            discount_rs=0,
            rounding=0,
            cash_paid=total if cash is None else cash,
            online_paid=0,
            doctor_name=doctor,
            doctor_phone="",
            previous_due=0,
            bill_date=bill_date,
            final=final,
        )


class ANormalSaleGetsOneRealBill(AutosaveBase):
    def test_the_first_tick_saves_a_real_bill_not_a_draft(self):
        """Old behaviour: is_autosave=1, numbered ASV1, invisible everywhere."""
        mid = _medicine(self.conn, "AMOX")
        res = self.tick(name="RAMESH", lines=[_line(mid, 2)])

        bills = _real_bills(self.conn)
        self.assertEqual(
            len(bills), 1,
            "the first autosave must leave a REAL saved bill behind",
        )
        self.assertEqual(int(bills[0][0]), int(res["sale_id"]))
        self.assertFalse(
            str(bills[0][1]).upper().startswith("ASV"),
            "a real bill must not carry the ASV draft prefix",
        )
        self.assertTrue(
            billing_service.fetch_recent_sales(self.conn, 5),
            "the autosaved bill must show up in recent/saved bills",
        )

    def test_autosave_moves_stock_and_the_customer_balance(self):
        """A draft touched neither. A real bill must touch both."""
        mid = _medicine(self.conn, "AMOX", stock=100)
        self.tick(name="RAMESH", lines=[_line(mid, 3)], cash=0)

        self.assertEqual(_stock(self.conn, mid), 97.0)
        due = self.conn.execute(
            "SELECT COALESCE(total_due,0) FROM customers WHERE UPPER(name)='RAMESH'"
        ).fetchone()[0]
        self.assertAlmostEqual(float(due), 30.0, places=2)

    def test_every_later_tick_updates_that_same_bill(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        first = self.tick(name="RAMESH", lines=[_line(mid, 2)])
        second = self.tick(
            name="RAMESH", lines=[_line(mid, 5)],
            token=first["token"], sale_id=first["sale_id"],
        )

        self.assertEqual(first["sale_id"], second["sale_id"])
        self.assertEqual(
            first["bill_no"], second["bill_no"],
            "an updating bill keeps its number -- a tick must not burn one",
        )
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(_qty(self.conn, second["sale_id"], mid), 5.0)
        self.assertEqual(
            _stock(self.conn, mid), 95.0,
            "stock must reflect the bill as it stands, not tick after tick",
        )

    def test_saving_after_autosave_does_not_make_a_second_bill(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        first = self.tick(name="RAMESH", lines=[_line(mid, 2)])
        final = self.tick(
            name="RAMESH", lines=[_line(mid, 2)],
            token=first["token"], sale_id=first["sale_id"], final=True,
        )

        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(first["bill_no"], final["bill_no"])
        self.assertEqual(_qty(self.conn, final["sale_id"], mid), 2.0)

    def test_a_tick_that_lands_after_the_save_changes_nothing(self):
        """The timer fires every couple of minutes; F7 can land mid-tick.

        Without a tombstone for the finished bill the late tick finds no
        session at all and opens a SECOND bill for the same form.
        """
        mid = _medicine(self.conn, "AMOX", stock=100)
        first = self.tick(name="RAMESH", lines=[_line(mid, 2)])
        self.tick(
            name="RAMESH", lines=[_line(mid, 2)],
            token=first["token"], sale_id=first["sale_id"], final=True,
        )

        late = self.tick(
            name="RAMESH", lines=[_line(mid, 9)],
            token=first["token"], sale_id=first["sale_id"],
        )

        self.assertTrue(late.get("closed"))
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(
            _qty(self.conn, first["sale_id"], mid), 2.0,
            "a late tick must not rewrite a bill that was already printed",
        )

    def test_clearing_the_form_after_saving_does_not_delete_the_bill(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        first = self.tick(name="RAMESH", lines=[_line(mid, 2)])
        saved = self.tick(
            name="RAMESH", lines=[_line(mid, 2)],
            token=first["token"], sale_id=first["sale_id"], final=True,
        )

        out = autosave_bill.discard_autosave_bill(
            self.conn, token=first["token"], sale_id=saved["sale_id"]
        )

        self.assertFalse(out.get("deleted"))
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(_stock(self.conn, mid), 98.0)


class OneCounterBillPerDay(AutosaveBase):
    def test_two_counter_sales_land_in_one_bill(self):
        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)

        first = self.tick(name="COUNTER SALE", lines=[_line(a, 2)], final=True)
        second = self.tick(name="COUNTER SALE", lines=[_line(b, 3)], final=True)

        bills = _real_bills(self.conn)
        self.assertEqual(
            len(bills), 1,
            "all of a day's counter sales belong in ONE bill",
        )
        self.assertEqual(first["sale_id"], second["sale_id"])
        self.assertEqual(_qty(self.conn, first["sale_id"], a), 2.0)
        self.assertEqual(_qty(self.conn, first["sale_id"], b), 3.0)

    def test_ticking_the_same_form_again_does_not_bill_it_twice(self):
        """The money bug: a tick that appends charges again every interval."""
        mid = _medicine(self.conn, "PARA", stock=100)
        r = self.tick(name="COUNTER SALE", lines=[_line(mid, 2)])
        for _ in range(3):
            r = self.tick(
                name="COUNTER SALE", lines=[_line(mid, 2)],
                token=r["token"], sale_id=r["sale_id"],
            )

        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(
            _qty(self.conn, r["sale_id"], mid), 2.0,
            "four ticks of the same two strips is still two strips",
        )
        self.assertEqual(_stock(self.conn, mid), 98.0)

    def test_saving_after_autosave_does_not_double_the_counter_lines(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        first = self.tick(name="COUNTER SALE", lines=[_line(mid, 2)])
        final = self.tick(
            name="COUNTER SALE", lines=[_line(mid, 2)],
            token=first["token"], sale_id=first["sale_id"], final=True,
        )

        self.assertEqual(first["sale_id"], final["sale_id"])
        self.assertEqual(
            _qty(self.conn, final["sale_id"], mid), 2.0,
            "the save is the last update of the same bill, not a second sale",
        )

    def test_a_second_counter_form_adds_to_the_day_bill_while_the_first_is_open(self):
        """Two counter tabs at once: both edit the day bill, neither loses."""
        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)

        one = self.tick(name="COUNTER SALE", lines=[_line(a, 2)])
        two = self.tick(name="COUNTER SALE", lines=[_line(b, 4)])
        one = self.tick(
            name="COUNTER SALE", lines=[_line(a, 3)],
            token=one["token"], sale_id=one["sale_id"],
        )

        self.assertEqual(one["sale_id"], two["sale_id"])
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(_qty(self.conn, one["sale_id"], a), 3.0)
        self.assertEqual(
            _qty(self.conn, one["sale_id"], b), 4.0,
            "the other tab's lines must survive this tab's tick",
        )

    def test_a_restart_finds_the_days_counter_bill_again(self):
        """No token, no id -- the day bill is still the one to update."""
        mid = _medicine(self.conn, "PARA", stock=100)
        first = self.tick(name="COUNTER SALE", lines=[_line(mid, 2)], final=True)

        again = self.tick(name="COUNTER SALE", lines=[_line(mid, 1)])

        self.assertEqual(first["sale_id"], again["sale_id"])
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(_qty(self.conn, again["sale_id"], mid), 3.0)

    def test_a_new_day_starts_a_new_counter_bill(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        day1 = self.tick(
            name="COUNTER SALE", lines=[_line(mid, 2)],
            bill_date="2026-09-10", final=True,
        )
        day2 = self.tick(
            name="COUNTER SALE", lines=[_line(mid, 2)],
            bill_date="2026-09-11", final=True,
        )

        self.assertNotEqual(day1["sale_id"], day2["sale_id"])
        self.assertEqual(len(_real_bills(self.conn)), 2)

    def test_a_finished_form_is_not_mistaken_for_another_tabs_session(self):
        """The day bill is shared, so its finished sessions are not up for grabs.

        A tab that kept the bill id but lost its token must not be handed the
        tombstone of the form that saved before it -- that would subtract the
        other sale's lines out of the day's counter bill.
        """
        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)
        done = self.tick(name="COUNTER SALE", lines=[_line(a, 2)], final=True)

        joined = self.tick(
            name="COUNTER SALE", lines=[_line(b, 3)], sale_id=done["sale_id"]
        )

        self.assertEqual(joined["sale_id"], done["sale_id"])
        self.assertEqual(_qty(self.conn, done["sale_id"], a), 2.0)
        self.assertEqual(_qty(self.conn, done["sale_id"], b), 3.0)

    def test_a_scheduled_or_doctor_line_keeps_its_own_bill(self):
        """Same gate as the existing counter merge -- these never fold in."""
        mid = _medicine(self.conn, "PARA", stock=100)
        counter = self.tick(name="COUNTER SALE", lines=[_line(mid, 1)], final=True)
        with_doc = self.tick(
            name="COUNTER SALE", lines=[_line(mid, 1)],
            doctor="DR PATIL", final=True,
        )

        self.assertNotEqual(counter["sale_id"], with_doc["sale_id"])


class AbandoningAnAutosavedBill(AutosaveBase):
    def test_discarding_a_normal_sale_takes_the_bill_and_its_stock_back(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        res = self.tick(name="RAMESH", lines=[_line(mid, 4)], cash=0)
        self.assertEqual(_stock(self.conn, mid), 96.0)

        autosave_bill.discard_autosave_bill(
            self.conn, token=res["token"], sale_id=res["sale_id"]
        )

        self.assertEqual(_real_bills(self.conn), [])
        self.assertEqual(_stock(self.conn, mid), 100.0)
        due = self.conn.execute(
            "SELECT COALESCE(total_due,0) FROM customers WHERE UPPER(name)='RAMESH'"
        ).fetchone()[0]
        self.assertAlmostEqual(float(due), 0.0, places=2)

    def test_discarding_a_counter_form_leaves_the_days_other_sales_alone(self):
        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)
        kept = self.tick(name="COUNTER SALE", lines=[_line(a, 2)], final=True)
        dropped = self.tick(name="COUNTER SALE", lines=[_line(b, 5)])

        autosave_bill.discard_autosave_bill(
            self.conn, token=dropped["token"], sale_id=dropped["sale_id"]
        )

        bills = _real_bills(self.conn)
        self.assertEqual(len(bills), 1)
        self.assertEqual(int(bills[0][0]), int(kept["sale_id"]))
        self.assertEqual(_qty(self.conn, kept["sale_id"], a), 2.0)
        self.assertEqual(
            _qty(self.conn, kept["sale_id"], b), 0.0,
            "only the abandoned form's lines come back out",
        )
        self.assertEqual(_stock(self.conn, b), 100.0)
        self.assertEqual(_stock(self.conn, a), 98.0)

    def test_discarding_the_only_counter_form_removes_the_day_bill(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        res = self.tick(name="COUNTER SALE", lines=[_line(mid, 2)])

        autosave_bill.discard_autosave_bill(
            self.conn, token=res["token"], sale_id=res["sale_id"]
        )

        self.assertEqual(_real_bills(self.conn), [])
        self.assertEqual(_stock(self.conn, mid), 100.0)

    def test_an_old_asv_draft_is_still_discarded_the_old_way(self):
        """Builds before this change left ASV drafts in the field."""
        mid = _medicine(self.conn, "PARA", stock=100)
        cid = self._customer("COUNTER SALE")
        _bill_no, draft_id = billing_service.save_autosave_bill(
            self.conn, cid, [_line(mid, 2)], 0, 0, 20, 0, "", "", 0,
        )

        out = autosave_bill.discard_autosave_bill(self.conn, sale_id=draft_id)

        self.assertTrue(out["ok"])
        self.assertTrue(out.get("deleted"))
        self.assertIsNone(
            self.conn.execute(
                "SELECT id FROM sales WHERE id=?", (draft_id,)
            ).fetchone()
        )


class TheFormChangingItsMind(AutosaveBase):
    def test_typing_a_real_name_over_counter_sale_moves_the_lines_out(self):
        """The lines were in the day bill. They must not stay there too."""
        mid = _medicine(self.conn, "PARA", stock=100)
        other = self.tick(name="COUNTER SALE", lines=[_line(mid, 1)], final=True)
        started = self.tick(name="COUNTER SALE", lines=[_line(mid, 4)])
        self.assertEqual(started["sale_id"], other["sale_id"])

        moved = self.tick(
            name="RAMESH", lines=[_line(mid, 4)],
            token=started["token"], sale_id=started["sale_id"],
        )

        self.assertNotEqual(moved["sale_id"], other["sale_id"])
        self.assertEqual(
            _qty(self.conn, other["sale_id"], mid), 1.0,
            "the counter bill keeps only what actually stayed a counter sale",
        )
        self.assertEqual(_qty(self.conn, moved["sale_id"], mid), 4.0)
        self.assertEqual(_stock(self.conn, mid), 95.0)


class OnlineIsServerOnly(AutosaveBase):
    """Online, the engine's sqlite is :memory: -- the bill lives on the server.

    Finding "today's counter bill" and updating it has to go through the server
    client, and the create has to be a real server write (sync=True), not the
    local-only draft insert the old autosave did.
    """

    def setUp(self):
        super().setUp()
        sync_prefs.is_online_mode = lambda *a, **k: True

    def _customer(self, name):
        # Online, the customer lives on the server too. The suite's socket
        # guard would (rightly) refuse the lookup, so stand one in.
        return 5

    def test_the_first_online_tick_writes_a_real_server_bill(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        calls = {}

        def fake_save(conn, customer_id, medicines, *a, **kw):
            calls.update(kw)
            calls["medicines"] = medicines
            return "SCB7/26-27", 41

        with mock.patch.object(billing_service, "save_new_bill", fake_save), \
                mock.patch(
                    "core.store_query_client.list_sales",
                    lambda **kw: {"rows": []},
                ), \
                mock.patch(
                    "core.online_catalog.find_customer_by_id",
                    lambda cid: {"id": cid, "name": "COUNTER SALE"},
                ), \
                mock.patch("core.server_crud.get_doc", lambda *a, **kw: {}):
            res = self.tick(name="COUNTER SALE", lines=[_line(mid, 2)])

        self.assertEqual(res["sale_id"], 41)
        self.assertEqual(res["bill_no"], "SCB7/26-27")
        self.assertTrue(
            calls.get("sync"),
            "Online the bill must be written to the server, not queued as "
            "PENDING with no number the counter can read out",
        )

    def test_an_online_tick_updates_the_server_counter_bill_for_today(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        server_bill = {
            "id": 77,
            "local_id": 77,
            "bill_no": "SCB12/26-27",
            "bill_date": "2026-09-10",
            "customer_id": 5,
            "customer_name": "COUNTER SALE",
            "cash_paid": 30.0,
            "online_paid": 0.0,
            "items": [
                {
                    "medicine_id": mid,
                    "qty": 3,
                    "rate": 10.0,
                    "amount": 30.0,
                    "medicine_name": "PARA",
                }
            ],
        }
        seen = {}

        def fake_update(conn, sale_id, medicines, **kw):
            seen["sale_id"] = sale_id
            seen["medicines"] = medicines
            seen["cash"] = kw.get("cash_paid")

        def fail_new(*a, **kw):
            raise AssertionError(
                "Online must reuse today's counter bill from the server, "
                "not open a second one"
            )

        with mock.patch.object(billing_service, "update_existing_bill", fake_update), \
                mock.patch.object(billing_service, "save_new_bill", fail_new), \
                mock.patch(
                    "core.store_query_client.list_sales",
                    lambda **kw: {"rows": [dict(server_bill)]},
                ), \
                mock.patch(
                    "core.online_catalog.find_customer_by_id",
                    lambda cid: {"id": cid, "name": "COUNTER SALE"},
                ), \
                mock.patch(
                    "core.online_catalog.medicine_by_id",
                    lambda m: {"id": m, "name": "PARA"},
                ), \
                mock.patch(
                    "core.server_crud.get_doc",
                    lambda coll, sid, *a, **kw: (
                        dict(server_bill) if int(sid) == 77 else {}
                    ),
                ):
            res = self.tick(name="COUNTER SALE", lines=[_line(mid, 2)])

        self.assertEqual(res["sale_id"], 77)
        self.assertEqual(seen["sale_id"], 77)
        self.assertEqual(
            sum(float(m["qty"]) for m in seen["medicines"]), 5.0,
            "today's server counter bill keeps its 3 and gains this form's 2",
        )
        self.assertAlmostEqual(float(seen["cash"]), 50.0, places=2)


class ThroughTheEndpointsTheAppActuallyCalls(AutosaveBase):
    """/api/sales/autosave, /api/sales/save, /api/sales/autosave/discard.

    These are the calls the Sales page and the Tk billing page make, and they
    are what the old draft behaviour was reachable through -- so this is where
    "autosave produced a draft, not a bill" is pinned down.
    """

    def _body(self, name, lines, **extra):
        body = {
            "force": True,  # autosave is off by default in settings
            "customer_name": name,
            "customer_phone": "",
            "customer_address": "",
            "doctor_name": "",
            "doctor_phone": "",
            "bill_date": "2026-09-10",
            "payment_mode": "Cash",
            "discount_pct": 0,
            "discount_rs": 0,
            "rounding": 0,
            "cash_paid": sum(l["amount"] for l in lines),
            "online_paid": 0,
            "previous_due": 0,
            "items": lines,
        }
        body.update(extra)
        return body

    def test_the_autosave_endpoint_returns_a_real_bill(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        res = dss.autosave_sale(self.conn, self._body("RAMESH", [_line(mid, 2)]))

        self.assertTrue(res["ok"], res)
        self.assertTrue(
            res.get("real_bill"),
            "autosave must report a real saved bill, not a draft",
        )
        self.assertTrue(res.get("autosave_token"))
        row = self.conn.execute(
            "SELECT COALESCE(is_autosave,0), bill_no FROM sales WHERE id=?",
            (res["autosave_sale_id"],),
        ).fetchone()
        self.assertEqual(
            int(row[0]), 0,
            "the autosaved row must be a saved bill, not an is_autosave draft",
        )
        self.assertFalse(str(row[1]).upper().startswith("ASV"))
        self.assertEqual(_stock(self.conn, mid), 98.0)

    def test_ticking_then_saving_through_the_endpoints_makes_one_bill(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        tick = dss.autosave_sale(self.conn, self._body("RAMESH", [_line(mid, 2)]))
        # Mid-flight, before any Save: the bill is already real and the stock
        # has already moved. A draft did neither.
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(_stock(self.conn, mid), 98.0)
        again = dss.autosave_sale(
            self.conn,
            self._body(
                "RAMESH", [_line(mid, 2)],
                autosave_sale_id=tick["autosave_sale_id"],
                autosave_token=tick.get("autosave_token", ""),
            ),
        )
        saved = dss.save_sale(
            self.conn,
            self._body(
                "RAMESH", [_line(mid, 2)],
                autosave_sale_id=again["autosave_sale_id"],
                autosave_token=again.get("autosave_token", ""),
            ),
        )

        self.assertTrue(saved["ok"], saved)
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(int(saved["sale_id"]), int(tick["autosave_sale_id"]))
        self.assertEqual(_qty(self.conn, saved["sale_id"], mid), 2.0)
        self.assertEqual(_stock(self.conn, mid), 98.0)

    def test_counter_sales_through_the_endpoints_share_one_bill(self):
        from core import desktop_sales_service as dss

        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)

        t1 = dss.autosave_sale(self.conn, self._body("COUNTER SALE", [_line(a, 2)]))
        self.assertEqual(
            len(_real_bills(self.conn)), 1,
            "the first counter tick opens the day's real counter bill",
        )
        s1 = dss.save_sale(
            self.conn,
            self._body(
                "COUNTER SALE", [_line(a, 2)],
                autosave_sale_id=t1["autosave_sale_id"],
                autosave_token=t1.get("autosave_token", ""),
            ),
        )
        t2 = dss.autosave_sale(self.conn, self._body("COUNTER SALE", [_line(b, 3)]))
        self.assertEqual(
            len(_real_bills(self.conn)), 1,
            "the next counter sale joins that same bill from its first tick",
        )
        self.assertEqual(_qty(self.conn, t2["autosave_sale_id"], b), 3.0)
        s2 = dss.save_sale(
            self.conn,
            self._body(
                "COUNTER SALE", [_line(b, 3)],
                autosave_sale_id=t2["autosave_sale_id"],
                autosave_token=t2.get("autosave_token", ""),
            ),
        )

        self.assertTrue(s1["ok"] and s2["ok"], (s1, s2))
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(int(s1["sale_id"]), int(s2["sale_id"]))
        self.assertEqual(_qty(self.conn, s2["sale_id"], a), 2.0)
        self.assertEqual(_qty(self.conn, s2["sale_id"], b), 3.0)
        self.assertEqual(_stock(self.conn, a), 98.0)
        self.assertEqual(_stock(self.conn, b), 97.0)

    def test_discarding_through_the_endpoint_gives_the_stock_back(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        tick = dss.autosave_sale(self.conn, self._body("RAMESH", [_line(mid, 6)]))
        self.assertEqual(
            _stock(self.conn, mid), 94.0,
            "autosave is a real bill, so it took the stock",
        )
        out = dss.discard_autosave(
            self.conn,
            {
                "autosave_sale_id": tick["autosave_sale_id"],
                "autosave_token": tick.get("autosave_token", ""),
            },
        )

        self.assertTrue(out["ok"], out)
        self.assertEqual(_real_bills(self.conn), [])
        self.assertEqual(_stock(self.conn, mid), 100.0)


class AServerThatCannotBeReadIsNotAMissingBill(AutosaveBase):
    """Online, an unreadable bill must never become a second bill.

    ``server_crud.get_doc`` answers None for a row that is gone AND for a
    server it could not reach (core/server_crud.py:106-116 -- network errors
    are printed and swallowed). Autosave treated both as "the bill is gone",
    dropped the session and created a new one: one lost packet between two
    ticks and the customer had two bills, two numbers, two stock movements and
    twice the due. The same read is the counter bill's, so a blip could also
    fork the day.
    """

    def setUp(self):
        super().setUp()
        sync_prefs.is_online_mode = lambda *a, **k: True

    def _customer(self, name):
        return 5

    def _server(self, mid, sale_id=101, qty=4, name="COUNTER SALE"):
        return {
            "id": sale_id, "local_id": sale_id, "bill_no": "SCB9/26-27",
            "bill_date": "2026-09-10", "customer_id": 5, "customer_name": name,
            "cash_paid": qty * 10.0, "online_paid": 0.0,
            "items": [{"medicine_id": mid, "qty": qty, "rate": 10.0,
                       "amount": qty * 10.0, "medicine_name": "PARA"}],
        }

    def test_a_dropped_packet_does_not_open_a_second_bill(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        created = []
        doc = self._server(mid)
        down = {"now": False}

        def fake_new(conn, customer_id, medicines, *a, **kw):
            created.append(list(medicines))
            return "SCB9/26-27", 101

        def fake_update(conn, sale_id, medicines, **kw):
            pass

        def get_doc(coll, sid, *a, **kw):
            # Exactly what an unreachable server hands back.
            return None if down["now"] else (dict(doc) if int(sid) == 101 else {})

        with mock.patch.object(billing_service, "save_new_bill", fake_new), \
                mock.patch.object(billing_service, "update_existing_bill", fake_update), \
                mock.patch("core.store_query_client.list_sales",
                           lambda **kw: {"rows": []}), \
                mock.patch("core.online_catalog.find_customer_by_id",
                           lambda cid: {"id": cid, "name": "COUNTER SALE"}), \
                mock.patch("core.online_catalog.medicine_by_id",
                           lambda m: {"id": m, "name": "PARA"}), \
                mock.patch("core.server_crud.get_doc", get_doc):
            first = self.tick(name="COUNTER SALE", lines=[_line(mid, 4)])
            down["now"] = True
            with self.assertRaises(autosave_bill.AutosaveTargetUnavailable):
                self.tick(name="COUNTER SALE", lines=[_line(mid, 5)],
                          token=first["token"], sale_id=first["sale_id"])

        self.assertEqual(
            len(created), 1,
            "a tick that could not read its own bill opened another one")
        self.assertEqual(
            autosave_session.get_counter_pointer("2026-09-10"), 101,
            "the day's pointer must survive a blip -- clearing it is how the "
            "next tick opens a second counter bill")

    def test_the_day_pointer_is_not_thrown_away_when_the_store_is_silent(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        autosave_session.set_counter_pointer("2026-09-10", 101)
        created = []

        with mock.patch.object(
                billing_service, "save_new_bill",
                lambda *a, **kw: created.append(1) or ("SCB1/26-27", 202)), \
                mock.patch.object(billing_service, "update_existing_bill",
                                  lambda *a, **kw: None), \
                mock.patch("core.store_query_client.list_sales",
                           lambda **kw: {"rows": []}), \
                mock.patch("core.online_catalog.find_customer_by_id",
                           lambda cid: {"id": cid, "name": "COUNTER SALE"}), \
                mock.patch("core.server_crud.get_doc", lambda *a, **kw: None):
            with self.assertRaises(autosave_bill.AutosaveTargetUnavailable):
                self.tick(name="COUNTER SALE", lines=[_line(mid, 2)])

        self.assertEqual(created, [], "opened a second counter bill for the day")
        self.assertEqual(autosave_session.get_counter_pointer("2026-09-10"), 101)


class TheSaveIsOnTheServerBeforeItIsPrinted(AutosaveBase):
    """Online, the F7 save of an autosaved bill is an UPDATE, and an update only
    goes into the mutation queue. The printer reads the server document straight
    (core/bill_output.py:209 -- no pending overlay), so printing before the queue
    drains puts the PREVIOUS tick's bill on the customer's slip.
    """

    def setUp(self):
        super().setUp()
        sync_prefs.is_online_mode = lambda *a, **k: True

    def _customer(self, name):
        return 5

    def test_the_final_save_waits_for_the_queue(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        doc = {
            "id": 55, "local_id": 55, "bill_no": "SCB3/26-27",
            "bill_date": "2026-09-10", "customer_id": 5,
            "customer_name": "RAMESH", "cash_paid": 0.0, "online_paid": 0.0,
            "items": [{"medicine_id": mid, "qty": 2, "rate": 10.0, "amount": 20.0}],
        }
        flushes = []

        with mock.patch.object(billing_service, "save_new_bill",
                               lambda *a, **kw: ("SCB3/26-27", 55)), \
                mock.patch.object(billing_service, "update_existing_bill",
                                  lambda *a, **kw: None), \
                mock.patch("core.online_mutation_queue.flush_now",
                           lambda **kw: flushes.append(kw) or True), \
                mock.patch("core.online_catalog.find_customer_by_id",
                           lambda cid: {"id": cid, "name": "RAMESH"}), \
                mock.patch("core.online_catalog.medicine_by_id",
                           lambda m: {"id": m, "name": "PARA"}), \
                mock.patch("core.server_crud.get_doc",
                           lambda coll, sid, *a, **kw: (
                               dict(doc) if int(sid) == 55 else {})):
            first = self.tick(name="RAMESH", lines=[_line(mid, 2)], cash=0)
            self.assertEqual(flushes, [], "a create is already a server write")
            self.tick(name="RAMESH", lines=[_line(mid, 3)], cash=0,
                      token=first["token"], sale_id=first["sale_id"], final=True)

        self.assertTrue(
            flushes,
            "the queued final save must reach the server before the bill is "
            "printed from it")


class AQueuedBillStillHoldsTheOtherSales(AutosaveBase):
    """Online with the server down, the create is QUEUED and has no document.

    A second counter tab then attaches to that queued bill through the device
    pointer, reads nothing back for it, and rewrote the day bill as its own
    lines alone -- the first sale vanished before it was ever pushed. The queue
    payload is the only record of those lines, so it is what the merge has to
    subtract from.
    """

    def setUp(self):
        super().setUp()
        sync_prefs.is_online_mode = lambda *a, **k: True

    def _customer(self, name):
        return 5

    def test_a_second_tab_does_not_wipe_the_queued_lines(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        writes = []
        pending = {
            "client_uuid": "cu-1", "local_id": -42,
            "payload": {"bill_no": "PENDING", "cash_paid": 0.0, "online_paid": 0.0},
        }

        def fake_new(conn, customer_id, medicines, *a, **kw):
            writes.append(("create", [dict(m) for m in medicines]))
            pending["payload"]["medicines"] = [dict(m) for m in medicines]
            pending["payload"]["cash_paid"] = sum(
                float(m["amount"]) for m in medicines)
            return "PENDING", -42

        def fake_update(conn, sale_id, medicines, **kw):
            writes.append(("update", sale_id, [dict(m) for m in medicines]))
            pending["payload"]["medicines"] = [dict(m) for m in medicines]
            pending["payload"]["cash_paid"] = kw.get("cash_paid") or 0.0

        with mock.patch.object(billing_service, "save_new_bill", fake_new), \
                mock.patch.object(billing_service, "update_existing_bill", fake_update), \
                mock.patch("core.online_mutation_queue.pending_by_local_id",
                           lambda coll, lid: (
                               dict(pending) if int(lid) == -42 else None)), \
                mock.patch("core.store_query_client.list_sales",
                           lambda **kw: {"rows": []}), \
                mock.patch("core.online_catalog.find_customer_by_id",
                           lambda cid: {"id": cid, "name": "COUNTER SALE"}), \
                mock.patch("core.online_catalog.medicine_by_id",
                           lambda m: {"id": m, "name": "PARA"}), \
                mock.patch("core.server_crud.get_doc", lambda *a, **kw: {}):
            first = self.tick(name="COUNTER SALE", lines=[_line(mid, 4)])
            second = self.tick(name="COUNTER SALE", lines=[_line(mid, 6)])

        self.assertEqual(first["sale_id"], -42)
        self.assertEqual(second["sale_id"], -42, "both tabs share the day bill")
        last = writes[-1]
        self.assertEqual(last[0], "update")
        self.assertEqual(
            sum(float(m["qty"]) for m in last[2]), 10.0,
            "the queued bill must keep the first tab's 4 and gain the second's 6")

    def test_a_queued_create_that_has_flushed_is_not_billed_again(self):
        """The queue only hands back rows still WAITING.

        Once the create is pushed the negative id resolves to nothing, which is
        not "the bill is gone": it is a real bill under a server id this device
        never saw. Autosave used to answer that by opening a second one.
        """
        mid = _medicine(self.conn, "PARA", stock=100)
        creates = []
        updates = []
        flushed = {
            "id": 88, "local_id": 88, "bill_no": "SCB4/26-27",
            "bill_date": "2026-09-10", "customer_id": 5,
            "customer_name": "COUNTER SALE", "cash_paid": 40.0, "online_paid": 0.0,
            "items": [{"medicine_id": mid, "qty": 4, "rate": 10.0, "amount": 40.0,
                       "medicine_name": "PARA"}],
        }
        state = {"queued": True}

        def fake_new(conn, customer_id, medicines, *a, **kw):
            creates.append([dict(m) for m in medicines])
            return "PENDING", -42

        with mock.patch.object(billing_service, "save_new_bill", fake_new), \
                mock.patch.object(
                    billing_service, "update_existing_bill",
                    lambda conn, sid, meds, **kw: updates.append((sid, list(meds)))), \
                mock.patch("core.online_mutation_queue.pending_by_local_id",
                           lambda coll, lid: (
                               {"client_uuid": "cu-1", "local_id": -42,
                                "payload": {"bill_no": "PENDING", "cash_paid": 40.0,
                                            "online_paid": 0.0,
                                            "medicines": [_line(mid, 4)]}}
                               if state["queued"] and int(lid) == -42 else None)), \
                mock.patch("core.store_query_client.list_sales",
                           lambda **kw: {
                               "rows": [] if state["queued"] else [dict(flushed)]}), \
                mock.patch("core.online_catalog.find_customer_by_id",
                           lambda cid: {"id": cid, "name": "COUNTER SALE"}), \
                mock.patch("core.online_catalog.medicine_by_id",
                           lambda m: {"id": m, "name": "PARA"}), \
                mock.patch("core.server_crud.get_doc",
                           lambda coll, sid, *a, **kw: (
                               dict(flushed) if int(sid) == 88 else {})):
            first = self.tick(name="COUNTER SALE", lines=[_line(mid, 4)])
            state["queued"] = False  # the queue drained; -42 is now server id 88
            second = self.tick(name="COUNTER SALE", lines=[_line(mid, 6)],
                               token=first["token"], sale_id=first["sale_id"])

        self.assertEqual(len(creates), 1, "the flushed bill was billed twice")
        self.assertEqual(second["sale_id"], 88, "the form must follow its bill")
        self.assertEqual(
            sum(float(m["qty"]) for m in updates[-1][1]), 6.0,
            "the form's own 4 come out, its 6 go in -- the day bill holds 6")


if __name__ == "__main__":
    unittest.main()
