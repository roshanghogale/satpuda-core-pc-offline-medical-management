"""The standard bill drops "I WISH FOR YOUR SPEEDY RECOVERY", as the dot matrix slip did on
25 Sep 2026 (owner, 17 Oct 2026).

Every saved settings file says show_recovery_wish True -- it was the default it was saved
with -- so changing the default alone would change nothing. It is switched off once, and a
shop that ticks "Recovery wish line" again keeps it.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from tests._bill_fixtures import discounted_bill_context


class _SettingsFile(unittest.TestCase):
    def setUp(self):
        import core.bill_config as bc

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "bill_print_settings.json")
        for p in (mock.patch.object(bc, "SETTINGS_PATH", self.path),
                  mock.patch.object(bc, "_bill_settings_path", lambda: self.path)):
            p.start()
            self.addCleanup(p.stop)
        self.bc = bc

    def write(self, data: dict) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def stored(self) -> dict:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


class SwitchedOffOnce(_SettingsFile):
    def test_an_old_file_with_the_wish_on_is_switched_off_and_remembered(self):
        self.write({"show_recovery_wish": True, "show_terms": True})
        self.assertFalse(self.bc.load_bill_print_settings()["show_recovery_wish"])
        saved = self.stored()
        self.assertFalse(saved["show_recovery_wish"])
        self.assertTrue(saved["recovery_wish_off_migrated"])
        self.assertTrue(saved["show_terms"])

    def test_a_shop_that_turns_it_back_on_keeps_it(self):
        self.write({"show_recovery_wish": True})
        st = self.bc.load_bill_print_settings()          # switched off once
        st["show_recovery_wish"] = True                   # the shop ticks it again
        self.bc.save_bill_print_settings(st)
        self.assertTrue(self.bc.load_bill_print_settings()["show_recovery_wish"])
        self.assertTrue(self.bc.load_bill_print_settings()["show_recovery_wish"])

    def test_a_save_without_the_flag_still_records_it(self):
        # the settings page sends back what it shows, which may not carry the flag
        self.bc.save_bill_print_settings({"show_recovery_wish": True})
        self.assertTrue(self.stored()["recovery_wish_off_migrated"])
        self.assertTrue(self.bc.load_bill_print_settings()["show_recovery_wish"])

    def test_a_new_install_starts_without_it(self):
        self.assertFalse(self.bc.load_bill_print_settings()["show_recovery_wish"])
        self.assertFalse(self.bc.load_bill_print_settings()["show_recovery_wish"])


class OnTheBill(_SettingsFile):
    def render(self) -> str:
        from bill_templates.classic import render_classic_bill_html_multi
        from core.bill_config import apply_print_bill_layout, load_bill_print_settings

        with mock.patch("core.bill_context._logo_to_base64", return_value=""), \
             mock.patch("core.upi_qr.apply_upi_qr_to_context", lambda ctx: None):
            st = apply_print_bill_layout(dict(load_bill_print_settings()), print_slot_copies=1)
            st["paper_size"] = "A5"
            html = render_classic_bill_html_multi([discounted_bill_context()] * 2, st)
        return html.split("</style>", 1)[1]

    def test_the_default_bill_has_no_wish(self):
        self.assertNotIn("SPEEDY RECOVERY", self.render())

    def test_ticking_it_brings_it_back(self):
        self.bc.save_bill_print_settings({"show_recovery_wish": True})
        self.assertIn("SPEEDY RECOVERY", self.render())


if __name__ == "__main__":
    unittest.main()
