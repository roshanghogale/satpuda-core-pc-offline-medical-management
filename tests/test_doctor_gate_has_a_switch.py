"""The doctor gate is a preference, not a law of physics.

Reported from the counter: a scheduled medicine could not be sold at all. Adding
the line answered "Please select a doctor for scheduled medicines." and saving
the bill answered "Scheduled medicine requires a doctor name." -- and there was
no setting anywhere in the new app to relax it.

The preference exists and the engine has always read it. The old Tk screen had
the checkbox (Settings -> Sales & Billing -> Doctor name on Sales); the React
port never rendered one, and the save path's key allowlist did not carry the key
either, so even a client that sent it would have been ignored in silence.

H1 and X always require a doctor name -- that part is not the shop's choice.
Every other schedule is, and the choice has to survive the whole round trip:
Settings screen -> save -> the sale itself, including the "Add No Stock" quick
line, which kept its own private copy of the rule in the browser.
"""
import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.billing_layout_prefs import (  # noqa: E402
    sale_requires_doctor,
    schedule_requires_doctor,
)

# Never let the real loader run in a test: on a PC with no store database it
# treats itself as a first-run migration and rewrites the developer's own
# config/billing_layout_prefs.json.
PREFS = "core.billing_layout_prefs.load_billing_layout_prefs"
SAVE = "core.billing_layout_prefs.save_billing_layout_prefs"


class H1AndXAreNotTheShopsChoice(unittest.TestCase):
    """The invariant the whole safety argument rests on."""

    def test_h1_and_x_ignore_the_switch(self):
        for code in ("H1", "X", "h1", "x", "H-1", "H 1", "  h1  "):
            with self.subTest(code=code):
                self.assertTrue(
                    schedule_requires_doctor(code, require_other=False),
                    "H1 and X must need a doctor even with the setting off",
                )

    def test_other_schedules_follow_the_switch(self):
        for code in ("H", "G", "C", "C1", "K", "P", "N", "M"):
            with self.subTest(code=code):
                self.assertFalse(schedule_requires_doctor(code, require_other=False))
                self.assertTrue(schedule_requires_doctor(code, require_other=True))

    def test_a_blank_schedule_never_needs_a_doctor(self):
        # An ordinary over-the-counter line must not be dragged into the gate.
        for blank in (None, "", "   "):
            with self.subTest(blank=repr(blank)):
                self.assertFalse(schedule_requires_doctor(blank, require_other=True))


class TheSettingsScreenCanSeeTheSwitch(unittest.TestCase):
    """The read half of the bug: the key was never in the panel's payload."""

    def _get(self, prefs):
        from core.desktop_settings_service import get_sales_billing

        with mock.patch(PREFS, return_value=prefs):
            return get_sales_billing()

    def test_the_switch_is_reported_when_it_is_off(self):
        got = self._get({"require_doctor_for_other_schedules": False})
        self.assertIs(got["require_doctor_for_other_schedules"], False)

    def test_the_switch_is_reported_when_it_is_on(self):
        got = self._get({"require_doctor_for_other_schedules": True})
        self.assertIs(got["require_doctor_for_other_schedules"], True)

    def test_a_store_that_never_set_it_reads_as_on(self):
        # The stored default. The checkbox must render ticked here, because the
        # engine is enforcing the rule.
        got = self._get({})
        self.assertIs(got["require_doctor_for_other_schedules"], True)


class TheSettingsScreenCanWriteTheSwitch(unittest.TestCase):
    """The write half: an unlisted key was dropped with no error and no warning."""

    def _save(self, data):
        from core.desktop_settings_service import save_sales_billing

        seen = {}

        def capture(updates, *a, **kw):
            seen.update(updates)
            return dict(updates)

        with mock.patch(SAVE, side_effect=capture), mock.patch(PREFS, return_value={}):
            save_sales_billing(data)
        return seen

    def test_turning_it_off_reaches_the_store(self):
        seen = self._save({"require_doctor_for_other_schedules": False})
        self.assertIn("require_doctor_for_other_schedules", seen)
        self.assertIs(seen["require_doctor_for_other_schedules"], False)

    def test_saving_a_neighbour_does_not_touch_it(self):
        # Only supplied keys travel, so one panel cannot reset another's setting.
        seen = self._save({"billing_show_total_margin": True})
        self.assertNotIn("require_doctor_for_other_schedules", seen)

    def test_the_string_false_is_not_true(self):
        # The whole trap: the value is stored through bool(), and
        # bool("false") is True -- the shop would untick the box, be told
        # "Saved.", and still be unable to sell.
        for raw in ("false", "0", "", "no", "off", "FALSE"):
            with self.subTest(raw=raw):
                seen = self._save({"require_doctor_for_other_schedules": raw})
                self.assertIs(seen["require_doctor_for_other_schedules"], False)
        for raw in ("true", "1", "yes"):
            with self.subTest(raw=raw):
                seen = self._save({"require_doctor_for_other_schedules": raw})
                self.assertIs(seen["require_doctor_for_other_schedules"], True)


class TheBillGateReadsTheSwitch(unittest.TestCase):
    """End to end through real storage, without touching the disk."""

    def setUp(self):
        # Belt and braces. load_billing_layout_prefs mirrors itself back out to
        # config/ whenever it thinks it migrated something -- a first run, or
        # the one-shot margin flag -- and a test has no business rewriting the
        # developer's own settings on its way past.
        self._mirror = mock.patch(
            "core.billing_layout_prefs._mirror_to_legacy_modules", lambda *a, **k: None
        )
        self._legacy = mock.patch(
            "core.billing_layout_prefs._write_legacy_file", lambda *a, **k: None
        )
        self._mirror.start()
        self._legacy.start()
        self.conn = sqlite3.connect(":memory:")
        # The exact shape the loader declares. Get a column name wrong and the
        # SELECT raises, the loader swallows it, calls this a first run, and
        # reads the DEVELOPER'S OWN config file instead -- so the test would
        # pass here and fail on a clean checkout, while quietly rewriting
        # config/billing_layout_prefs.json on the way past.
        self.conn.execute(
            "CREATE TABLE settings ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT UNIQUE,"
            " value TEXT)"
        )
        self.conn.execute(
            "INSERT INTO settings (name, value) VALUES (?, ?)",
            (
                "billing_layout_prefs",
                json.dumps({"require_doctor_for_other_schedules": False}),
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self._legacy.stop()
        self._mirror.stop()

    def test_the_seeded_store_is_really_the_one_being_read(self):
        # Without this, a mis-shaped table would send the loader off to the
        # developer's own config file and every assertion below would be
        # measuring that instead.
        from core.billing_layout_prefs import _read_sqlite

        self.assertEqual(
            _read_sqlite(self.conn),
            {"require_doctor_for_other_schedules": False},
        )

    def test_a_plain_schedule_bill_saves_without_a_doctor(self):
        self.assertFalse(sale_requires_doctor([{"schedule": "H"}], self.conn))

    def test_an_h1_line_still_demands_one(self):
        self.assertTrue(sale_requires_doctor([{"schedule": "H1"}], self.conn))

    def test_one_h1_line_is_enough_to_demand_one(self):
        self.assertTrue(
            sale_requires_doctor(
                [{"schedule": "H"}, {"schedule": "X"}], self.conn
            )
        )

    def test_an_empty_bill_demands_nothing(self):
        self.assertFalse(sale_requires_doctor([], self.conn))


class QuickSaleUsesTheSameSwitchAsEveryOtherLine(unittest.TestCase):
    """"Add No Stock" kept its own rule in the browser, so the switch missed it."""

    def _line(self, prefs, **body):
        from core.desktop_sales_service import build_quick_sale_line

        base = {"name": "AMOXY 500", "batch": "B1", "qty": 1}
        base.update(body)
        with mock.patch(PREFS, return_value=prefs):
            return build_quick_sale_line(base)

    def test_with_the_switch_off_a_plain_schedule_goes_through(self):
        res = self._line({"require_doctor_for_other_schedules": False}, schedule="H")
        self.assertTrue(res["ok"], res.get("error"))

    def test_with_the_switch_on_it_is_refused(self):
        res = self._line({"require_doctor_for_other_schedules": True}, schedule="H")
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "doctor_required")

    def test_h1_is_refused_even_with_the_switch_off(self):
        res = self._line({"require_doctor_for_other_schedules": False}, schedule="H1")
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "doctor_required")

    def test_a_doctor_name_lets_h1_through(self):
        res = self._line(
            {"require_doctor_for_other_schedules": False},
            schedule="H1",
            doctor_name="Dr Patil",
        )
        self.assertTrue(res["ok"], res.get("error"))

    def test_an_unscheduled_line_never_asks(self):
        res = self._line({"require_doctor_for_other_schedules": True}, schedule="")
        self.assertTrue(res["ok"], res.get("error"))

    def test_the_gate_fails_closed(self):
        # An unreadable preference must mean "doctor required", never "allowed".
        from core.desktop_sales_service import build_quick_sale_line

        with mock.patch(PREFS, side_effect=RuntimeError("prefs unreadable")):
            res = build_quick_sale_line(
                {"name": "AMOXY 500", "batch": "B1", "qty": 1, "schedule": "H"}
            )
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "doctor_required")


if __name__ == "__main__":
    unittest.main(verbosity=2)
