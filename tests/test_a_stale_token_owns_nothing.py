"""A tab whose session is gone owns NOTHING -- not another tab's counter share.

Two tabs are both in today's counter bill. Tab A switches to a named customer:
its counter lines are released from the day bill and its record is dropped,
then creating the new bill fails (a server blip). The tab keeps the day bill's
id and its now-dead token -- SalesPage only takes a tick's answer when it is
ok, the Tk page swallows the exception -- and sends both on the next tick, on
Clear and on F7.

The engine used to answer ``load_session(token) or find_session_for_sale(id)``.
The only live record left on that bill is TAB B's, so A was handed it: B's paid
lines came out of the day bill, the stock went back and B's record was dropped.
A lost sale. The rule now (Android's ownSession): a caller with a token owns
that token's record or nothing; lookup by id is for a caller with no token,
and never for a counter bill.

The second half is Android's "bug L": a token invented inside the write and
only handed to the tab when the write came back.
"""

import unittest
from unittest import mock

from core import autosave_bill, autosave_session, billing_service
from tests.test_autosave_makes_one_real_bill import (
    AutosaveBase,
    _line,
    _medicine,
    _qty,
    _real_bills,
    _stock,
)

DAY = "2026-09-10"


def _body(name, lines, **extra):
    body = {
        "force": True,
        "customer_name": name,
        "customer_phone": "",
        "customer_address": "",
        "doctor_name": "",
        "doctor_phone": "",
        "bill_date": DAY,
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


class AStaleTokenDoesNotTakeOverAnotherTabsCounterShare(AutosaveBase):
    def setUp(self):
        super().setUp()
        self.a = _medicine(self.conn, "PARA", stock=100)
        self.b = _medicine(self.conn, "AMOX", stock=100)
        self.ta = self.tick(name="COUNTER SALE", lines=[_line(self.a, 2)])
        self.tb = self.tick(name="COUNTER SALE", lines=[_line(self.b, 3)])
        self.day = int(self.ta["sale_id"])
        self.assertEqual(int(self.tb["sale_id"]), self.day)

        # Tab A switches to a named customer; the create after the release fails.
        def _blip(*a, **k):
            raise RuntimeError("server blip")

        with mock.patch.object(billing_service, "save_new_bill", _blip):
            with self.assertRaises(RuntimeError):
                self.tick(
                    name="RAMESH", lines=[_line(self.a, 2)],
                    token=self.ta["token"], sale_id=self.day,
                )
        self.assertIsNone(autosave_session.load_session(self.ta["token"]))
        self.assertEqual(_qty(self.conn, self.day, self.a), 0.0)
        self.assertEqual(_qty(self.conn, self.day, self.b), 3.0)

    def _b_is_intact(self):
        self.assertEqual(
            _qty(self.conn, self.day, self.b), 3.0,
            "tab B's paid lines must stay in the day bill",
        )
        self.assertEqual(_stock(self.conn, self.b), 97.0)
        rec = autosave_session.load_session(self.tb["token"])
        self.assertIsNotNone(rec, "tab B's record must not be dropped")
        self.assertFalse(rec.get("closed"))

    def test_the_next_tick_opens_its_own_bill_and_leaves_b_alone(self):
        res = self.tick(
            name="RAMESH", lines=[_line(self.a, 2)],
            token=self.ta["token"], sale_id=self.day,
        )
        self._b_is_intact()
        self.assertNotEqual(int(res["sale_id"]), self.day)
        self.assertEqual(_qty(self.conn, res["sale_id"], self.a), 2.0)
        self.assertEqual(_stock(self.conn, self.a), 98.0)

    def test_clear_does_not_release_b(self):
        out = autosave_bill.discard_autosave_bill(
            self.conn, token=self.ta["token"], sale_id=self.day
        )
        self.assertTrue(out["ok"], out)
        self._b_is_intact()

    def test_f7_neither_releases_b_nor_rewrites_the_day_bill(self):
        from core import desktop_sales_service as dss

        saved = dss.save_sale(
            self.conn,
            _body(
                "RAMESH", [_line(self.a, 2)],
                autosave_sale_id=self.day, autosave_token=self.ta["token"],
            ),
        )
        self.assertTrue(saved["ok"], saved)
        self._b_is_intact()
        self.assertNotEqual(int(saved["sale_id"]), self.day)
        self.assertEqual(_qty(self.conn, saved["sale_id"], self.a), 2.0)

    def test_id_only_lookup_never_answers_for_a_counter_bill(self):
        self.assertIsNone(autosave_session.find_session_for_sale(self.day))
        self.assertIsNone(autosave_session.own_session("", self.day))


class ATokenMintedBeforeTheWriteMakesOneBill(AutosaveBase):
    def test_f7_that_lands_before_the_first_tick_leaves_a_tombstone(self):
        """The tab minted T; F7 (same T) wins the write lock; the tick follows."""
        from core import desktop_sales_service as dss

        mid = _medicine(self.conn, "AMOX", stock=100)
        token = autosave_session.new_token()
        saved = dss.save_sale(
            self.conn, _body("RAMESH", [_line(mid, 2)], autosave_token=token)
        )
        late = dss.autosave_sale(
            self.conn, _body("RAMESH", [_line(mid, 2)], autosave_token=token)
        )
        self.assertTrue(saved["ok"] and late["ok"], (saved, late))
        self.assertEqual(len(_real_bills(self.conn)), 1, "one sale, one bill")
        self.assertEqual(int(late["autosave_sale_id"]), int(saved["sale_id"]))
        self.assertEqual(_stock(self.conn, mid), 98.0)

    def test_tk_tab_keeps_its_token_when_the_write_throws_after_pinning(self):
        from tests.test_a_restart_reclaims_the_bill_it_started import (
            _StubBillingPage,
        )
        from ui.billing import billing_session

        mid = _medicine(self.conn, "AMOX", stock=100)
        page = _StubBillingPage(self.conn)

        class _Var:
            def __init__(self, v=""):
                self.v = v

            def get(self):
                return self.v

        page._resolve_customer_name = lambda touch_ui=False: "RAMESH"
        page._validate_scheduled_requirements = lambda: True
        page.discount, page.discount_pct, page.rounding = _Var("0"), _Var("0"), _Var("0")
        page.cash_paid, page.online_paid = _Var("20"), _Var("0")
        page.customer_phone, page.customer_address = _Var(), _Var()
        page.doctor_name, page.doctor_phone = _Var(), _Var()
        page.get_bill_date_value = lambda: DAY
        page._get_edit_previous_due = lambda: 0.0
        page.selected_medicines = [_line(mid, 2)]

        real = autosave_bill.write_autosave_bill

        def _pins_then_throws(*a, **k):
            real(*a, **k)
            raise RuntimeError("snapshot write failed")

        with mock.patch.object(billing_session, "write_autosave_bill", _pins_then_throws):
            page._persist_autosave()
        self.assertTrue(page._autosave_token, "the tab must hold its token already")
        page._persist_autosave()

        self.assertEqual(len(_real_bills(self.conn)), 1, "the retry must not open bill two")
        self.assertEqual(_stock(self.conn, mid), 98.0)


if __name__ == "__main__":
    unittest.main()
