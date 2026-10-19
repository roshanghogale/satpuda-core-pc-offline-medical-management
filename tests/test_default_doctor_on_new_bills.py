"""Settings > Sales & Billing > Default doctor: a new Sales bill starts with them.

Owner, 19 Oct 2026: "option to select the default doctor". The choice is a store
setting -- it rides in ``billing_layout_prefs`` (the settings row the server KV
syncs), beside the other Sales defaults -- and the bill may still clear it.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core.sync_prefs as sync_prefs  # noqa: E402

PREFS = "core.billing_layout_prefs.load_billing_layout_prefs"
SAVE = "core.billing_layout_prefs.save_billing_layout_prefs"


def _src(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class _NoDiskMirror(unittest.TestCase):
    """The loader mirrors itself out to config/; a test must never rewrite that."""

    def setUp(self):
        self._patches = [
            mock.patch("core.billing_layout_prefs._mirror_to_legacy_modules", lambda *a, **k: None),
            mock.patch("core.billing_layout_prefs._write_legacy_file", lambda *a, **k: None),
            mock.patch("core.billing_layout_prefs._push_server", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT UNIQUE, value TEXT)"
        )
        self.conn.execute(
            "INSERT INTO settings (name, value) VALUES (?, ?)",
            ("billing_layout_prefs", json.dumps({"sales_margin_ui_v2": True, "version": 3})),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        for p in reversed(self._patches):
            p.stop()


class TheStoreKeepsTheChoice(_NoDiskMirror):
    def test_a_store_that_never_chose_has_none(self):
        from core.billing_layout_prefs import load_billing_layout_prefs, load_default_doctor

        self.assertEqual(load_billing_layout_prefs(self.conn)["default_doctor"], "")
        self.assertEqual(load_default_doctor(self.conn), "")

    def test_the_choice_is_saved_in_the_synced_settings_row(self):
        from core.billing_layout_prefs import (
            _read_sqlite,
            load_default_doctor,
            save_billing_layout_prefs,
        )

        save_billing_layout_prefs({"default_doctor": "  DR   PATIL "}, self.conn)
        self.assertEqual(load_default_doctor(self.conn), "DR PATIL")
        # In the billing_layout_prefs row -- the one the server KV carries.
        self.assertEqual(_read_sqlite(self.conn)["default_doctor"], "DR PATIL")

    def test_none_can_be_chosen_again(self):
        from core.billing_layout_prefs import load_default_doctor, save_billing_layout_prefs

        save_billing_layout_prefs({"default_doctor": "DR PATIL"}, self.conn)
        save_billing_layout_prefs({"default_doctor": None}, self.conn)
        self.assertEqual(load_default_doctor(self.conn), "")

    def test_saving_a_neighbour_keeps_the_doctor(self):
        from core.billing_layout_prefs import load_default_doctor, save_billing_layout_prefs

        save_billing_layout_prefs({"default_doctor": "DR PATIL"}, self.conn)
        save_billing_layout_prefs({"billing_show_total_margin": False}, self.conn)
        self.assertEqual(load_default_doctor(self.conn), "DR PATIL")

    def test_another_pc_receives_it_from_the_server(self):
        from core.billing_layout_prefs import (
            apply_server_billing_layout_prefs,
            load_default_doctor,
        )

        res = apply_server_billing_layout_prefs(
            {"item_discount_mode": "rupees", "default_doctor": "DR SHAH",
             "version": 50, "updated_at": "2099-01-01T00:00:00Z"},
            self.conn,
        )
        self.assertEqual(res, "applied")
        self.assertEqual(load_default_doctor(self.conn), "DR SHAH")


class TheSettingsScreenReadsAndWritesIt(unittest.TestCase):
    def test_it_is_reported(self):
        from core.desktop_settings_service import get_sales_billing

        with mock.patch(PREFS, return_value={"default_doctor": "DR PATIL"}):
            self.assertEqual(get_sales_billing()["default_doctor"], "DR PATIL")
        with mock.patch(PREFS, return_value={}):
            self.assertEqual(get_sales_billing()["default_doctor"], "")

    def _save(self, data):
        from core.desktop_settings_service import save_sales_billing

        seen = {}

        def capture(updates, *a, **kw):
            seen.update(updates)
            return dict(updates)

        with mock.patch(SAVE, side_effect=capture), mock.patch(PREFS, return_value={}):
            save_sales_billing(data)
        return seen

    def test_it_reaches_the_store(self):
        self.assertEqual(self._save({"default_doctor": "DR PATIL"})["default_doctor"], "DR PATIL")
        self.assertEqual(self._save({"default_doctor": None})["default_doctor"], "")

    def test_a_neighbour_save_does_not_reset_it(self):
        self.assertNotIn("default_doctor", self._save({"billing_show_total_margin": True}))

    def test_the_panel_sends_it_with_the_doctor_rule(self):
        src = _src("desktop", "src", "pages", "settings", "PrefsPanels.tsx")
        block = src[src.index("billing_layout: ["): src.index("batch_picker:")]
        self.assertIn("'default_doctor'", block)
        self.assertIn("<DefaultDoctorField", src)


class TheSalesFormCarriesIt(unittest.TestCase):
    def test_offline_defaults_name_the_doctor(self):
        from core import db_setup
        from core.desktop_pages_service import sales_form_defaults

        with mock.patch.object(sync_prefs, "is_online_mode", lambda: False), \
                mock.patch(PREFS, return_value={"default_doctor": "DR PATIL"}):
            conn = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "s.db"))
            db_setup.initialise(conn)
            conn.execute("INSERT INTO doctors (name, phone) VALUES ('DR PATIL', '9822001122')")
            conn.commit()
            d = sales_form_defaults(conn)
            conn.close()
        self.assertEqual(d["default_doctor"], "DR PATIL")
        # The number comes from the same map the doctor picker uses.
        self.assertEqual(d["doctor_phones"].get("DR PATIL"), "9822001122")

    def test_no_choice_means_no_doctor(self):
        from core.desktop_pages_service import _sales_default_doctor

        with mock.patch(PREFS, return_value={}):
            self.assertEqual(_sales_default_doctor(), "")


class NewBillsStartWithIt(unittest.TestCase):
    """The Sales screen: every new tab / cleared bill, never a bill being worked on."""

    @classmethod
    def setUpClass(cls):
        cls.src = _src("desktop", "src", "pages", "SalesPage.tsx")

    def test_new_tab_takes_the_doctor_and_their_number(self):
        self.assertIn("doctor: startDoctor.doctor", self.src)
        self.assertIn("doctorPhone: startDoctor.doctorPhone", self.src)
        self.assertIn("d?.doctor_phones?.[name.toUpperCase()]", self.src)

    def test_every_new_bill_passes_the_default(self):
        # newTab(1) is the first tab, built before settings arrive (reloadMeta fills it).
        calls = [m.start() for m in re.finditer(r"(?<!function )newTab\(\s*\n", self.src)]
        self.assertGreaterEqual(len(calls), 3)
        for at in calls:
            body = self.src[at: at + 400]
            self.assertIn("defaultDoctorOf(defaults)", body, body)

    def test_a_bill_being_worked_on_is_left_alone(self):
        i = self.src.index("const startDoctor = defaultDoctorOf(d)")
        block = self.src[i: i + 1200]
        for guard in ("!t.dirty", "!t.doctor.trim()", "!t.items.length",
                      "!t.editingSaleId", "!t.autosaveSaleId"):
            self.assertIn(guard, block)


if __name__ == "__main__":
    unittest.main()


class ContactsDoctorsSetsTheDefault(unittest.TestCase):
    """Owner, 19 Oct 2026: "this option should be in the contacts to where we add the
    doctors" -- Contacts > Doctors > Set default writes the same setting, follows a
    rename and clears when that doctor is deleted."""

    def setUp(self):
        self.saved = {"default_doctor": ""}
        self.doctors = [{"id": 1, "name": "DR A", "reg_no": "1", "phone": ""},
                        {"id": 2, "name": "DR B", "reg_no": "2", "phone": ""}]
        import core.desktop_settings_service as svc
        self.svc = svc
        self._p = [
            mock.patch("core.billing_layout_prefs.save_billing_layout_prefs",
                       lambda d: self.saved.update(d)),
            mock.patch("core.billing_layout_prefs.load_default_doctor",
                       lambda *a, **k: self.saved["default_doctor"]),
            mock.patch.object(svc, "get_contacts",
                              lambda conn: {"doctors": list(self.doctors),
                                            "default_doctor": self.saved["default_doctor"]}),
            mock.patch.object(svc, "_mutate_contact", self._fake_mutate),
            mock.patch("core.online_guard.ensure_can_mutate", lambda *a, **k: None),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()

    def _fake_mutate(self, conn, data):
        if data["action"] == "update":
            for d in self.doctors:
                if d["id"] == data["id"]:
                    d["name"] = data["name"].upper()
        elif data["action"] == "delete":
            self.doctors = [d for d in self.doctors if d["id"] != data["id"]]
        return {"doctors": list(self.doctors), "default_doctor": self.saved["default_doctor"]}

    def test_set_rename_delete(self):
        out = self.svc.mutate_contact(None, {"kind": "doctor", "action": "set_default", "id": 2})
        self.assertEqual(out["default_doctor"], "DR B")
        out = self.svc.mutate_contact(None, {"kind": "doctor", "action": "update", "id": 2,
                                             "name": "dr b2", "reg_no": "2"})
        self.assertEqual(self.saved["default_doctor"], "DR B2")
        self.assertEqual(out["default_doctor"], "DR B2")
        self.svc.mutate_contact(None, {"kind": "doctor", "action": "delete", "id": 1})
        self.assertEqual(self.saved["default_doctor"], "DR B2")
        out = self.svc.mutate_contact(None, {"kind": "doctor", "action": "delete", "id": 2})
        self.assertEqual(out["default_doctor"], "")
        self.svc.mutate_contact(None, {"kind": "doctor", "action": "set_default", "name": "DR A"})
        out = self.svc.mutate_contact(None, {"kind": "doctor", "action": "clear_default"})
        self.assertEqual(out["default_doctor"], "")
