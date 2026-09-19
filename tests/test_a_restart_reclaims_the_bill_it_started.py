"""A reopened sales form must CLAIM the bill it already made, not make a second.

Autosave writes a REAL bill on the first tick and updates that same bill after
that. Which bill a form owns lived in two React state variables and two Tk
attributes -- memory, nothing else. So a crash, a close, or a power cut in the
middle of a sale left:

  * a named customer with a real Rs 20 bill on their account, and an operator
    with an empty form. They retype the sale -> TWO bills, due 40, stock -4 for
    2 items sold;
  * a counter sale whose lines are already sitting in the day's counter bill,
    and a retype that adds them to it again -> 4 units billed for 2 sold.

Under the old ASV draft that cost nothing, because a draft moved no stock and
no money. It is now the worst regression in the build.

The fix is that the ENGINE is asked, not remembered: the durable session record
(core/autosave_session.py) already knows the bill, the lines and the token, so a
reopened form asks it what it was in the middle of and resumes with the SAME
token. The token is the whole point -- it is what makes the next tick subtract
this form's own previous lines out of a shared counter bill before putting the
current ones in.

Every test here fails on the behaviour that shipped: there was no way for a
restarted form to find its own bill at all.
"""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import autosave_bill, autosave_session, db_setup, sync_prefs  # noqa: E402
from core import billing_service  # noqa: E402

BILL_DATE = "2026-09-10"


def _store():
    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    db_setup.initialise(conn)
    return conn


def _medicine(conn, name, stock=100, rate=10.0):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(medicines)")]
    # In the shop well before BILL_DATE: a save refuses a batch that came in after the
    # bill's own date (core.sale_availability), and the default created_at is today.
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
        "SELECT COALESCE(SUM(qty),0) FROM sales_items WHERE sale_id=? AND medicine_id=?",
        (sale_id, med_id),
    ).fetchone()
    return float(row[0] or 0)


def _stock(conn, med_id):
    return float(
        conn.execute("SELECT stock_qty FROM medicines WHERE id=?", (med_id,)).fetchone()[0]
    )


def _due(conn, name):
    row = conn.execute(
        "SELECT COALESCE(total_due,0) FROM customers WHERE UPPER(name)=?", (name.upper(),)
    ).fetchone()
    return float(row[0] or 0) if row else 0.0


class RestartBase(unittest.TestCase):
    """The session file is what survives the restart -- everything else is lost."""

    def setUp(self):
        self._sessions = tempfile.mktemp(suffix=".json")
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = self._sessions
        self._store_patch = mock.patch.object(
            autosave_session, "_store_key", lambda: "test-store"
        )
        self._store_patch.start()
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = _store()

    def tearDown(self):
        sync_prefs.is_online_mode = self._online
        self._store_patch.stop()
        os.environ.pop("SATPUDA_AUTOSAVE_SESSION_FILE", None)
        try:
            os.remove(self._sessions)
        except OSError:
            pass

    def _customer(self, name):
        from core.customer_service import get_or_create_customer

        return get_or_create_customer(self.conn, name, "", "")

    def body(self, name, lines, **extra):
        """Exactly what the Sales page posts to /api/sales/autosave."""
        body = {
            "force": True,  # autosave is off by default in settings
            "customer_name": name,
            "customer_phone": "",
            "customer_address": "",
            "doctor_name": "",
            "doctor_phone": "",
            "bill_date": BILL_DATE,
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


class ANamedCustomerIsNotBilledTwice(RestartBase):
    def test_the_reopened_tab_is_offered_the_bill_it_left_behind(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        tick = dss.autosave_sale(self.conn, self.body("RAMESH", [_line(mid, 2)]))

        # --- the crash. Every bit of tab state is gone. ---
        found = dss.list_autosave_sessions(self.conn, {})

        self.assertTrue(found["ok"], found)
        self.assertEqual(
            len(found["sessions"]), 1,
            "the engine has to be able to tell a reopened tab that a real bill "
            "is already sitting there unfinished",
        )
        rec = found["sessions"][0]
        self.assertEqual(int(rec["sale_id"]), int(tick["autosave_sale_id"]))
        self.assertEqual(rec["customer_name"], "RAMESH")
        self.assertEqual(rec["items"], 1)
        self.assertAlmostEqual(float(rec["total"]), 20.0, places=2)
        self.assertFalse(rec["counter"])
        self.assertTrue(
            rec["token"],
            "without the token back, resuming cannot update the same bill",
        )

    def test_resuming_gives_the_form_back_line_for_line(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        dss.autosave_sale(
            self.conn,
            self.body(
                "RAMESH", [_line(mid, 2, name="AMOX")],
                customer_phone="9812345678", customer_address="SHAHADA",
                doctor_name="DR PATIL",
            ),
        )
        rec = dss.list_autosave_sessions(self.conn, {})["sessions"][0]

        out = dss.resume_autosave(self.conn, {"autosave_token": rec["token"]})

        self.assertTrue(out["ok"], out)
        form = out["form"]
        self.assertEqual(form["customer_name"], "RAMESH")
        self.assertEqual(form["customer_phone"], "9812345678")
        self.assertEqual(form["customer_address"], "SHAHADA")
        self.assertEqual(form["doctor_name"], "DR PATIL")
        self.assertEqual(len(form["items"]), 1)
        self.assertEqual(int(form["items"][0]["id"]), mid)
        self.assertEqual(float(form["items"][0]["qty"]), 2.0)
        self.assertEqual(form["items"][0]["name"], "AMOX")
        self.assertEqual(
            out["autosave_sale_id"], rec["sale_id"],
            "the resumed tab must point at the bill that already exists",
        )

    def test_finishing_a_resumed_sale_leaves_one_bill_not_two(self):
        """The whole bug, end to end: Rs 20 once, stock -2, due 20."""
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        dss.autosave_sale(
            self.conn, self.body("RAMESH", [_line(mid, 2)], cash_paid=0)
        )
        self.assertEqual(_stock(self.conn, mid), 98.0)

        # --- restart ---
        rec = dss.list_autosave_sessions(self.conn, {})["sessions"][0]
        resumed = dss.resume_autosave(self.conn, {"autosave_token": rec["token"]})
        items = resumed["form"]["items"]

        saved = dss.save_sale(
            self.conn,
            self.body(
                "RAMESH", items, cash_paid=0, payment_mode="Due",
                autosave_sale_id=resumed["autosave_sale_id"],
                autosave_token=resumed["autosave_token"],
            ),
        )

        self.assertTrue(saved["ok"], saved)
        self.assertEqual(
            len(_real_bills(self.conn)), 1,
            "the retype after a crash is exactly the double bill this fixes",
        )
        self.assertAlmostEqual(_due(self.conn, "RAMESH"), 20.0, places=2)
        self.assertEqual(_stock(self.conn, mid), 98.0)
        self.assertEqual(_qty(self.conn, saved["sale_id"], mid), 2.0)

    def test_a_resumed_tab_keeps_updating_that_same_bill(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        first = dss.autosave_sale(self.conn, self.body("RAMESH", [_line(mid, 2)]))

        rec = dss.list_autosave_sessions(self.conn, {})["sessions"][0]
        resumed = dss.resume_autosave(self.conn, {"autosave_token": rec["token"]})
        again = dss.autosave_sale(
            self.conn,
            self.body(
                "RAMESH", [_line(mid, 5)],
                autosave_sale_id=resumed["autosave_sale_id"],
                autosave_token=resumed["autosave_token"],
            ),
        )

        self.assertEqual(int(again["autosave_sale_id"]), int(first["autosave_sale_id"]))
        self.assertEqual(
            again["bill_no"], first["bill_no"],
            "a resumed tab must not burn a second bill number",
        )
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(_qty(self.conn, again["autosave_sale_id"], mid), 5.0)
        self.assertEqual(_stock(self.conn, mid), 95.0)

    def test_discarding_from_the_recovery_prompt_gives_it_all_back(self):
        """Resume / Discard: Discard has to undo the money AND the stock."""
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        dss.autosave_sale(self.conn, self.body("RAMESH", [_line(mid, 6)], cash_paid=0))
        rec = dss.list_autosave_sessions(self.conn, {})["sessions"][0]

        out = dss.discard_autosave(
            self.conn,
            {"autosave_token": rec["token"], "autosave_sale_id": rec["sale_id"]},
        )

        self.assertTrue(out["ok"], out)
        self.assertEqual(_real_bills(self.conn), [])
        self.assertEqual(_stock(self.conn, mid), 100.0)
        self.assertAlmostEqual(_due(self.conn, "RAMESH"), 0.0, places=2)
        self.assertEqual(
            dss.list_autosave_sessions(self.conn, {})["sessions"], [],
            "a discarded sale must stop being offered on the next restart",
        )


class ACounterSaleIsNotAddedToTheDayTwice(RestartBase):
    def test_resuming_replaces_this_forms_lines_and_keeps_the_rest(self):
        from core import desktop_sales_service as dss

        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)

        # Sale one is finished and banked in today's counter bill.
        done = dss.autosave_sale(self.conn, self.body("COUNTER SALE", [_line(a, 2)]))
        dss.save_sale(
            self.conn,
            self.body(
                "COUNTER SALE", [_line(a, 2)],
                autosave_sale_id=done["autosave_sale_id"],
                autosave_token=done.get("autosave_token", ""),
            ),
        )
        # Sale two is mid-form when the power goes.
        live = dss.autosave_sale(self.conn, self.body("COUNTER SALE", [_line(b, 3)]))
        day_bill = int(live["autosave_sale_id"])

        # --- restart ---
        sessions = dss.list_autosave_sessions(self.conn, {})["sessions"]
        self.assertEqual(
            len(sessions), 1,
            "the finished counter sale is done; only the unfinished form is offered",
        )
        self.assertTrue(sessions[0]["counter"])
        resumed = dss.resume_autosave(self.conn, {"autosave_token": sessions[0]["token"]})
        self.assertEqual(
            [int(i["id"]) for i in resumed["form"]["items"]], [b],
            "a resumed counter form gets ITS OWN lines back, not the whole day",
        )

        # The operator adds one more strip and finishes.
        dss.save_sale(
            self.conn,
            self.body(
                "COUNTER SALE", [_line(b, 4)],
                autosave_sale_id=resumed["autosave_sale_id"],
                autosave_token=resumed["autosave_token"],
            ),
        )

        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(
            _qty(self.conn, day_bill, b), 4.0,
            "reclaiming must subtract this form's own share and reapply it -- "
            "appending is how 2 sold becomes 4 billed",
        )
        self.assertEqual(
            _qty(self.conn, day_bill, a), 2.0,
            "the other counter sale in the day bill must not be touched",
        )
        self.assertEqual(_stock(self.conn, a), 98.0)
        self.assertEqual(_stock(self.conn, b), 96.0)

    def test_discarding_an_abandoned_counter_form_leaves_the_day_intact(self):
        from core import desktop_sales_service as dss

        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)

        done = dss.autosave_sale(self.conn, self.body("COUNTER SALE", [_line(a, 2)]))
        dss.save_sale(
            self.conn,
            self.body(
                "COUNTER SALE", [_line(a, 2)],
                autosave_sale_id=done["autosave_sale_id"],
                autosave_token=done.get("autosave_token", ""),
            ),
        )
        live = dss.autosave_sale(self.conn, self.body("COUNTER SALE", [_line(b, 3)]))
        day_bill = int(live["autosave_sale_id"])

        rec = dss.list_autosave_sessions(self.conn, {})["sessions"][0]
        dss.discard_autosave(
            self.conn,
            {"autosave_token": rec["token"], "autosave_sale_id": rec["sale_id"]},
        )

        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(_qty(self.conn, day_bill, b), 0.0)
        self.assertEqual(_qty(self.conn, day_bill, a), 2.0)
        self.assertEqual(_stock(self.conn, b), 100.0)
        self.assertEqual(_stock(self.conn, a), 98.0)


class TwoTabsComeBackAsTwoTabs(RestartBase):
    def test_both_in_progress_bills_are_offered_and_neither_is_duplicated(self):
        from core import desktop_sales_service as dss

        a = _medicine(self.conn, "PARA", stock=100)
        b = _medicine(self.conn, "AMOX", stock=100)

        t1 = dss.autosave_sale(self.conn, self.body("RAMESH", [_line(a, 2)]))
        t2 = dss.autosave_sale(self.conn, self.body("SUNITA", [_line(b, 3)]))
        self.assertEqual(len(_real_bills(self.conn)), 2)

        # --- restart with both tabs open ---
        sessions = dss.list_autosave_sessions(self.conn, {})["sessions"]
        self.assertEqual(len(sessions), 2)
        by_name = {s["customer_name"]: s for s in sessions}
        self.assertEqual(set(by_name), {"RAMESH", "SUNITA"})
        self.assertNotEqual(
            by_name["RAMESH"]["token"], by_name["SUNITA"]["token"],
            "each tab owns its own session -- one token for two bills would "
            "let one tab take the other's lines out",
        )

        for name, mid, qty, tick in (
            ("RAMESH", a, 2, t1),
            ("SUNITA", b, 3, t2),
        ):
            rec = by_name[name]
            resumed = dss.resume_autosave(self.conn, {"autosave_token": rec["token"]})
            self.assertEqual(
                int(resumed["autosave_sale_id"]), int(tick["autosave_sale_id"])
            )
            saved = dss.save_sale(
                self.conn,
                self.body(
                    name, [_line(mid, qty)],
                    autosave_sale_id=resumed["autosave_sale_id"],
                    autosave_token=resumed["autosave_token"],
                ),
            )
            self.assertTrue(saved["ok"], saved)

        self.assertEqual(
            len(_real_bills(self.conn)), 2,
            "two reopened tabs must finish two bills, not four",
        )
        self.assertEqual(_stock(self.conn, a), 98.0)
        self.assertEqual(_stock(self.conn, b), 97.0)


class OnlineTheBillLivesOnTheServer(RestartBase):
    """Online the engine's sqlite is :memory: and the bill is a server document.

    The recovery record is a device-local file precisely so this still works:
    the machine that was mid-sale is the machine that reopens, and it must be
    able to say what it was doing before the server answers anything at all.
    """

    def setUp(self):
        super().setUp()
        sync_prefs.is_online_mode = lambda *a, **k: True

    def _customer(self, name):
        return 5

    def test_the_unfinished_sale_is_offered_even_with_the_server_down(self):
        mid = _medicine(self.conn, "PARA", stock=100)

        with mock.patch.object(
            billing_service, "save_new_bill",
            lambda *a, **kw: ("SCB9/26-27", 101),
        ), mock.patch("core.server_crud.get_doc", lambda *a, **kw: {
            "id": 101, "local_id": 101, "bill_no": "SCB9/26-27",
            "bill_date": BILL_DATE, "customer_id": 5, "customer_name": "RAMESH",
            "cash_paid": 20.0, "online_paid": 0.0,
            "items": [{"medicine_id": mid, "qty": 2, "rate": 10.0, "amount": 20.0}],
        }):
            autosave_bill.write_autosave_bill(
                self.conn, customer_id=5, customer_name="RAMESH",
                medicines=[_line(mid, 2)], cash_paid=20.0, bill_date=BILL_DATE,
            )

        # The power cut took the server with it. Listing must still answer.
        with mock.patch("core.server_crud.get_doc", lambda *a, **kw: None):
            rows = autosave_bill.list_recoverable()

        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["sale_id"]), 101)
        self.assertAlmostEqual(float(rows[0]["total"]), 20.0, places=2)

    def test_resuming_online_updates_the_server_bill_instead_of_creating_one(self):
        mid = _medicine(self.conn, "PARA", stock=100)
        created: list = []
        updated: list = []
        doc = {
            "id": 101, "local_id": 101, "bill_no": "SCB9/26-27",
            "bill_date": BILL_DATE, "customer_id": 5, "customer_name": "RAMESH",
            "cash_paid": 20.0, "online_paid": 0.0,
            "items": [{"medicine_id": mid, "qty": 2, "rate": 10.0, "amount": 20.0,
                       "medicine_name": "PARA"}],
        }

        def fake_new(conn, customer_id, medicines, *a, **kw):
            created.append(list(medicines))
            return "SCB9/26-27", 101

        def fake_update(conn, sale_id, medicines, **kw):
            updated.append((sale_id, list(medicines)))

        with mock.patch.object(billing_service, "save_new_bill", fake_new), \
                mock.patch.object(billing_service, "update_existing_bill", fake_update), \
                mock.patch("core.store_query_client.list_sales", lambda **kw: {"rows": []}), \
                mock.patch("core.online_catalog.find_customer_by_id",
                           lambda cid: {"id": cid, "name": "RAMESH"}), \
                mock.patch("core.online_catalog.medicine_by_id",
                           lambda m: {"id": m, "name": "PARA"}), \
                mock.patch("core.server_crud.get_doc",
                           lambda coll, sid, *a, **kw: dict(doc) if int(sid) == 101 else {}):
            autosave_bill.write_autosave_bill(
                self.conn, customer_id=5, customer_name="RAMESH",
                medicines=[_line(mid, 2, name="PARA")], cash_paid=20.0,
                bill_date=BILL_DATE,
            )

            # --- restart ---
            rec = autosave_bill.list_recoverable()[0]
            resumed = autosave_bill.resume_autosave_bill(self.conn, token=rec["token"])
            self.assertTrue(resumed["ok"], resumed)
            self.assertEqual(int(resumed["sale_id"]), 101)

            autosave_bill.write_autosave_bill(
                self.conn,
                token=resumed["token"],
                sale_id=resumed["sale_id"],
                customer_id=5,
                customer_name="RAMESH",
                medicines=[_line(mid, 3, name="PARA")],
                cash_paid=30.0,
                bill_date=BILL_DATE,
                final=True,
            )

        self.assertEqual(
            len(created), 1,
            "the reopened form made a SECOND server bill instead of claiming its own",
        )
        self.assertEqual([sid for sid, _ in updated], [101])
        self.assertEqual(
            sum(float(m["qty"]) for m in updated[-1][1]), 3.0,
            "the server bill must end up holding the form as it stands, once",
        )

    def test_an_unreadable_server_is_never_reported_as_a_deleted_bill(self):
        """Resume has to refuse, not shrug. 'Gone' would mean 'type it again'."""
        mid = _medicine(self.conn, "PARA", stock=100)

        with mock.patch.object(
            billing_service, "save_new_bill", lambda *a, **kw: ("SCB9/26-27", 101)
        ), mock.patch("core.server_crud.get_doc", lambda *a, **kw: {
            "id": 101, "bill_no": "SCB9/26-27", "customer_id": 5,
            "cash_paid": 20.0, "online_paid": 0.0, "items": [],
        }):
            res = autosave_bill.write_autosave_bill(
                self.conn, customer_id=5, customer_name="RAMESH",
                medicines=[_line(mid, 2)], cash_paid=20.0, bill_date=BILL_DATE,
            )

        with mock.patch("core.server_crud.get_doc", lambda *a, **kw: None):
            out = autosave_bill.resume_autosave_bill(self.conn, token=res["token"])

        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "unavailable")
        self.assertEqual(
            len(autosave_bill.list_recoverable()), 1,
            "a blip must not throw away the record of a real unfinished bill",
        )


class AnAbandonedFormIsNeverASilentOrphan(RestartBase):
    """The rule: an unfinished bill is reclaimable for as long as it takes, and
    it is visible as unfinished the whole time. Nothing is ever deleted behind
    the operator's back -- deleting a real bill on a timer is the same class of
    mistake as making a second one."""

    def test_an_unfinished_bill_is_still_offered_weeks_later(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        tick = dss.autosave_sale(
            self.conn, self.body("RAMESH", [_line(mid, 2)], bill_date="2026-06-01")
        )

        # Age the record past every housekeeping cutoff there is.
        rec = autosave_session.list_open_sessions()[0]
        raw = autosave_session._read()
        raw["sessions"][rec["token"]]["updated_at"] = time.time() - 90 * 86400
        autosave_session._write(raw)

        # Any save at all runs the pruner.
        autosave_session.set_counter_pointer(BILL_DATE, 0)
        rows = dss.list_autosave_sessions(self.conn, {})["sessions"]

        self.assertEqual(
            len(rows), 1,
            "pruning an OPEN session orphans a real bill: the charge stays on "
            "the customer and nothing anywhere knows it was never finished",
        )
        self.assertEqual(int(rows[0]["sale_id"]), int(tick["autosave_sale_id"]))
        self.assertTrue(
            rows[0]["stale"],
            "an unfinished bill from an earlier day has to be flagged as one",
        )

    def test_a_saved_bill_is_not_offered_back(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        tick = dss.autosave_sale(self.conn, self.body("RAMESH", [_line(mid, 2)]))
        dss.save_sale(
            self.conn,
            self.body(
                "RAMESH", [_line(mid, 2)],
                autosave_sale_id=tick["autosave_sale_id"],
                autosave_token=tick.get("autosave_token", ""),
            ),
        )

        self.assertEqual(dss.list_autosave_sessions(self.conn, {})["sessions"], [])
        out = dss.resume_autosave(
            self.conn, {"autosave_token": tick.get("autosave_token", "")}
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "already_saved")

    def test_history_shows_the_unfinished_bill_as_unfinished(self):
        from core import desktop_pages_service as dps
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        live = dss.autosave_sale(self.conn, self.body("RAMESH", [_line(mid, 2)]))
        done = dss.autosave_sale(self.conn, self.body("SUNITA", [_line(mid, 1)]))
        dss.save_sale(
            self.conn,
            self.body(
                "SUNITA", [_line(mid, 1)],
                autosave_sale_id=done["autosave_sale_id"],
                autosave_token=done.get("autosave_token", ""),
            ),
        )

        hist = dps.list_sales_history(
            self.conn, from_date=BILL_DATE, to_date=BILL_DATE
        )
        styles = {
            sid: (style or {}).get("status")
            for sid, style in zip(hist["row_ids"], hist["row_styles"])
        }

        self.assertEqual(
            styles.get(int(live["autosave_sale_id"])), "unfinished",
            "a real bill left behind by an abandoned form must not read as a "
            "completed sale in the one list a shop audits",
        )
        self.assertNotEqual(styles.get(int(done["autosave_sale_id"])), "unfinished")

    def test_a_bill_deleted_from_history_stops_being_offered(self):
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        tick = dss.autosave_sale(self.conn, self.body("RAMESH", [_line(mid, 2)]))
        dss.delete_saved_sale(self.conn, {"sale_id": tick["autosave_sale_id"]})

        self.assertEqual(
            dss.list_autosave_sessions(self.conn, {})["sessions"], [],
            "a session pointing at a bill that no longer exists must not keep "
            "asking the operator about it",
        )
        out = dss.resume_autosave(
            self.conn, {"autosave_token": tick.get("autosave_token", "")}
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "gone")


class _StubBillingPage:
    """The Tk BillingPage with the widgets taken out.

    The mixin under test is the real one -- only the parts that need a screen
    are stubbed, so the tab bookkeeping, the token and the lines are exercised
    exactly as they are on the counter's machine.
    """

    def __init__(self, conn):
        from ui.billing.billing_session import BillingSessionMixin

        # The stub first in the MRO: everything that needs a screen is
        # overridden here, everything else is the real mixin.
        self.__class__ = type(
            "StubBillingPage", (_StubBillingPage, BillingSessionMixin), {}
        )
        self.conn = conn
        self.parent = None
        self._in_edit_window = False
        self._doc_tabs = []
        self._active_tab_idx = 0
        self._editing_sale_id = None
        self._autosave_sale_id = None
        self._autosave_token = None
        self._edit_payment_snapshot = None
        self.selected_medicines: list = []
        self._ensure_initial_tab()

    # --- the parts that would need a screen -------------------------------
    def _capture_tab_state(self):
        state = dict(self._doc_tabs[self._active_tab_idx])
        state["autosave_sale_id"] = self._autosave_sale_id
        state["autosave_token"] = self._autosave_token
        state["selected_medicines"] = list(self.selected_medicines)
        return state

    def _restore_tab_state(self, state):
        self._autosave_sale_id = state.get("autosave_sale_id")
        self._autosave_token = state.get("autosave_token")
        self.selected_medicines = list(state.get("selected_medicines") or [])

    def _tab_has_data(self):
        return bool(self.selected_medicines)

    def _refresh_tab_bar(self):
        pass


class TheTkBillingPageRecoversTheSameWay(RestartBase):
    """Both UIs share one engine, so both must offer the same bill back.

    Nothing here is device- or browser-specific: the record is the engine's, so
    a sale started on the Tk page and a sale started in the desktop tabs are
    recovered by the same call.
    """

    def _page(self):
        from ui.billing import billing_session

        return _StubBillingPage(self.conn), billing_session

    def test_resume_puts_the_bill_and_its_token_back_on_the_tab(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        first = autosave_bill.write_autosave_bill(
            self.conn,
            customer_id=self._customer("RAMESH"),
            customer_name="RAMESH",
            medicines=[_line(mid, 2, name="AMOX")],
            cash_paid=20.0,
            bill_date=BILL_DATE,
        )

        page, mod = self._page()  # the restart: a page with no memory at all
        with mock.patch.object(mod, "ask_choice", lambda *a, **k: "resume"):
            page.offer_autosave_recovery()

        self.assertEqual(int(page._autosave_sale_id), int(first["sale_id"]))
        self.assertEqual(page._autosave_token, first["token"])
        self.assertEqual(
            [int(m["id"]) for m in page.selected_medicines], [mid],
            "the recovered form has to come back with its lines on it",
        )

        # Finishing from the recovered tab must update that one bill.
        saved = autosave_bill.write_autosave_bill(
            self.conn,
            token=page._autosave_token,
            sale_id=int(page._autosave_sale_id),
            customer_id=self._customer("RAMESH"),
            customer_name="RAMESH",
            medicines=page.selected_medicines,
            cash_paid=20.0,
            bill_date=BILL_DATE,
            final=True,
        )
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(int(saved["sale_id"]), int(first["sale_id"]))
        self.assertEqual(_stock(self.conn, mid), 98.0)

    def test_not_now_changes_nothing_and_keeps_the_offer(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        autosave_bill.write_autosave_bill(
            self.conn,
            customer_id=self._customer("RAMESH"),
            customer_name="RAMESH",
            medicines=[_line(mid, 2)],
            cash_paid=20.0,
            bill_date=BILL_DATE,
        )

        page, mod = self._page()
        with mock.patch.object(mod, "ask_choice", lambda *a, **k: "later"):
            page.offer_autosave_recovery()

        self.assertIsNone(page._autosave_sale_id)
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(
            len(autosave_bill.list_recoverable()), 1,
            "'Not now' must leave the sale claimable, not quietly drop it",
        )

    def test_discard_reverses_the_bill_from_the_tk_prompt(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        autosave_bill.write_autosave_bill(
            self.conn,
            customer_id=self._customer("RAMESH"),
            customer_name="RAMESH",
            medicines=[_line(mid, 4)],
            cash_paid=0.0,
            bill_date=BILL_DATE,
        )
        self.assertEqual(_stock(self.conn, mid), 96.0)

        page, mod = self._page()
        with mock.patch.object(mod, "ask_choice", lambda *a, **k: "discard"), \
                mock.patch.object(mod, "askyesno", lambda *a, **k: True):
            page.offer_autosave_recovery()

        self.assertEqual(_real_bills(self.conn), [])
        self.assertEqual(_stock(self.conn, mid), 100.0)
        self.assertEqual(autosave_bill.list_recoverable(), [])

    def test_a_tab_that_already_owns_the_session_is_not_offered_it_again(self):
        mid = _medicine(self.conn, "AMOX", stock=100)
        first = autosave_bill.write_autosave_bill(
            self.conn,
            customer_id=self._customer("RAMESH"),
            customer_name="RAMESH",
            medicines=[_line(mid, 2)],
            cash_paid=20.0,
            bill_date=BILL_DATE,
        )

        page, mod = self._page()
        page._doc_tabs[0]["autosave_token"] = first["token"]
        asked: list = []

        def _ask(*a, **k):
            asked.append(a)
            return "resume"

        with mock.patch.object(mod, "ask_choice", _ask):
            page.offer_autosave_recovery()

        self.assertEqual(
            asked, [],
            "a live tab already holds that bill -- offering it again would let "
            "two tabs write the same one",
        )


if __name__ == "__main__":
    unittest.main()


class ACreatedBillIsNeverLeftWithNothingPointingAtIt(RestartBase):
    """The record write must never be able to cost us the bill it describes.

    Autosave creates a REAL bill and then writes the session record that owns
    it. Everything between those two points is a window: a bill exists, and if
    nothing lands in the record then

      * recovery cannot offer it (it is not in list_recoverable),
      * History shows it as a completed sale, not Unfinished, and
      * the next tick finds no session and creates ANOTHER real bill -- and the
        tick after that another, once per timer interval.

    The window got wider when the record started carrying the whole form: the
    snapshot stores line dicts exactly as the UI handed them in, so one value
    json cannot write (a date in `expiry`, a widget object) turned a routine
    autosave into a stream of duplicate bills on a live customer's account.
    """

    def test_a_line_value_json_cannot_write_does_not_bill_the_customer_twice(self):
        import datetime

        mid = _medicine(self.conn, "PARA", stock=100)
        cid = self._customer("RAMESH")

        class _Widget:
            def __str__(self):
                return "widget"

        line = _line(mid, 2)
        # Exactly the shapes a Tk form line can carry: a real date object in
        # expiry, and an object that is not a number and not a string.
        line["expiry"] = datetime.date(2027, 1, 1)
        line["source_widget"] = _Widget()

        token, sale_id = "", 0
        for _ in range(4):  # the autosave timer keeps firing
            res = autosave_bill.write_autosave_bill(
                self.conn, token=token, sale_id=sale_id, customer_id=cid,
                customer_name="RAMESH", medicines=[line], cash_paid=0.0,
                bill_date=BILL_DATE,
            )
            token, sale_id = res["token"], res["sale_id"]

        self.assertEqual(
            len(_real_bills(self.conn)), 1,
            "one form, one bill -- a snapshot that will not serialise must not "
            "turn every autosave tick into another real bill",
        )
        self.assertEqual(_stock(self.conn, mid), 98.0)
        self.assertAlmostEqual(_due(self.conn, "RAMESH"), 20.0, places=2)
        self.assertEqual(len(autosave_bill.list_recoverable()), 1)

        back = autosave_bill.resume_autosave_bill(self.conn, token=token)
        self.assertTrue(back["ok"], back)
        med = back["form"]["medicines"][0]
        self.assertEqual(med["expiry"], "2027-01-01")
        self.assertEqual(med["source_widget"], "widget")
        self.assertEqual(float(med["qty"]), 2.0)

    def test_a_crash_right_after_the_create_still_leaves_the_bill_claimable(self):
        """Counter sale: the worst case, because the day bill is shared."""
        from core.customer_service import COUNTER_SALE

        mid = _medicine(self.conn, "PARA", stock=100)
        cid = self._customer(COUNTER_SALE)

        def _die(**_kw):
            raise KeyboardInterrupt("power cut")

        with mock.patch.object(autosave_session, "snapshot_form", _die):
            with self.assertRaises(KeyboardInterrupt):
                autosave_bill.write_autosave_bill(
                    self.conn, customer_id=cid, customer_name=COUNTER_SALE,
                    medicines=[_line(mid, 2)], cash_paid=0.0, bill_date=BILL_DATE,
                )

        rows = autosave_bill.list_recoverable()
        self.assertEqual(
            len(rows), 1,
            "the bill is on the books and the stock has moved -- something has "
            "to be able to hand it back",
        )
        day_bill = int(rows[0]["sale_id"])
        self.assertTrue(rows[0]["counter"])
        self.assertIn(
            day_bill, autosave_session.open_sale_ids(),
            "History must show it as Unfinished, not as a completed sale",
        )

        # The form ticks again with the same two units on screen.
        res = autosave_bill.write_autosave_bill(
            self.conn, token=rows[0]["token"], sale_id=day_bill, customer_id=cid,
            customer_name=COUNTER_SALE, medicines=[_line(mid, 2)], cash_paid=0.0,
            bill_date=BILL_DATE,
        )
        self.assertFalse(res["created"], "it must claim the bill, not start a second")
        self.assertEqual(len(_real_bills(self.conn)), 1)
        self.assertEqual(
            _qty(self.conn, day_bill, mid), 2.0,
            "a pinned record must carry this form's share, or the next tick "
            "merges lines the day bill already holds",
        )
        self.assertEqual(_stock(self.conn, mid), 98.0)

    def test_an_unwritable_snapshot_loses_the_snapshot_not_the_bill(self):
        """Last line of defence, tested at the record itself."""
        token = autosave_session.new_token()
        autosave_session.save_session(
            token,
            sale_id=77,
            bill_no="SCB77",
            bill_date=BILL_DATE,
            counter=False,
            form={"customer_name": "RAMESH", "medicines": [{"x": object()}]},
        )
        rec = autosave_session.load_session(token)
        self.assertIsNotNone(rec, "the record must survive a snapshot json refuses")
        self.assertEqual(int(rec["sale_id"]), 77)
        self.assertNotIn("form", rec)
        self.assertEqual([r["sale_id"] for r in autosave_bill.list_recoverable()], [77])
