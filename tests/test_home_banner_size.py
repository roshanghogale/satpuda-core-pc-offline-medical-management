"""Settings -> Appearance -> Banner width, end to end.

The number saved, stored, was served on the dashboard payload and was read by
Home -- and the banner never changed size, because the LAST link drew it:
.home-banner-img pinned `width: 100%` and capped the picture at
`max-height: min(58vh, 540px)`, with .home-body capping the row on top of that.
Measured against the real stylesheet at 1280x800, every value from about 950 up
to the 4000 maximum drew one identical 1044x472 banner -- and 1500, the shipped
default, is inside that dead range, so the shop's first change did nothing.

The engine half of the chain is pinned here (it must keep working in Online mode
too, where the sqlite connection is an empty :memory: one), and so is the front
half a Python test can still see: the stylesheet and the page that feeds it.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import layout_config as lc  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STYLES = os.path.join(_REPO, "desktop", "src", "styles.css")
_HOME_PAGE = os.path.join(_REPO, "desktop", "src", "pages", "HomePage.tsx")

_FAKE_STATS = {
    "today_str": "10-09-2026",
    "fy_label": "2026-27",
    "values": ["0.00"] * 12,
}


def _css_block(css: str, selector: str) -> str:
    """The declarations of one rule -- comments stripped, they are prose."""
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert match, f"{selector} is gone from styles.css"
    return re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)


class TheSavedBannerWidthReachesTheScreen(unittest.TestCase):
    """Save -> disk -> /api/home/dashboard, in both modes and after a restart."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "layout_config.txt")
        patcher = mock.patch.object(lc, "_get_config_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _save(self, width):
        from core.desktop_settings_service import save_appearance

        return save_appearance({"home_banner_size": width})

    def _payload(self, conn):
        import core.desktop_api as api

        with mock.patch("core.home_dashboard.query_dashboard_stats",
                        return_value=dict(_FAKE_STATS)):
            return api._dashboard_payload(conn)

    def test_the_dashboard_payload_carries_the_number_the_shop_typed(self):
        self._save(820)
        payload = self._payload(sqlite3.connect(":memory:"))
        self.assertEqual(
            payload["home_banner_size"], 820,
            "Home reads dash.home_banner_size; nothing else tells it the size")

    def test_online_mode_gets_it_too(self):
        """Online is server-only: the width must not come from the store DB."""
        from core import sync_prefs

        self._save(640)
        with mock.patch.object(sync_prefs, "is_online_mode", return_value=True):
            # Exactly what the engine holds in Online mode: an empty database.
            payload = self._payload(sqlite3.connect(":memory:"))
        self.assertEqual(payload["home_banner_size"], 640)

    def test_it_survives_a_restart(self):
        self._save(910)
        # A restart is a fresh read of this file and nothing else.
        self.assertEqual(json.load(open(self.path))["home_banner_size"], 910)
        self.assertEqual(lc.load_layout()["home_banner_size"], 910)
        self.assertEqual(lc.get_home_banner_size()[0], 910)

    def test_switching_stores_keeps_it(self):
        """The picture is per store; the width is per screen, so it is not."""
        self._save(1234)
        for store in ("shop-a", "shop-b", ""):
            with mock.patch.object(lc, "_active_store_key", return_value=store):
                self.assertEqual(lc.get_home_banner_size()[0], 1234, store)

    def test_the_settings_screen_reads_back_what_it_saved(self):
        from core.desktop_settings_service import get_appearance

        self._save(777)
        self.assertEqual(get_appearance()["home_banner_size"], 777)

    def test_out_of_range_numbers_are_clamped_not_dropped(self):
        self.assertEqual(self._save(99_999)["home_banner_size"], 4000)
        self.assertEqual(self._save(1)["home_banner_size"], 300)

    def test_height_stays_derived_from_the_picture(self):
        """One control on purpose -- a forced height could only squash it."""
        self._save(1000)
        self.assertEqual(lc.get_home_banner_size()[1], 0)


class TheStylesheetLetsTheWidthThrough(unittest.TestCase):
    """The break was here: the last link drew the banner at its own size."""

    def setUp(self):
        with open(_STYLES, encoding="utf-8") as fh:
            self.css = fh.read()
        with open(_HOME_PAGE, encoding="utf-8") as fh:
            self.tsx = fh.read()

    def test_the_banner_rule_does_not_pin_the_width(self):
        block = _css_block(self.css, ".home-banner-img")
        self.assertNotRegex(
            block, r"(?<![-\w])width:\s*100%",
            "width:100% here overrides the saved number for every value")
        self.assertIn(
            "var(--home-banner-width", block,
            "the saved width has to be what sizes the banner")

    def test_the_banner_still_cannot_overflow_the_window(self):
        block = _css_block(self.css, ".home-banner-img")
        self.assertRegex(block, r"max-width:\s*100%")

    def test_the_height_cap_no_longer_freezes_the_top_of_the_range(self):
        """58vh clamped the 2:1 picture long before the width ran out."""
        img = _css_block(self.css, ".home-banner-img")
        self.assertNotIn("58vh", img)
        row = _css_block(self.css, ".home-body")
        self.assertNotIn("max-height", row,
                         "the row cap re-imposed the same ceiling")

    def test_home_hands_the_saved_width_to_the_stylesheet(self):
        self.assertIn("--home-banner-width", self.tsx)
        self.assertIn("home_banner_size", self.tsx)


if __name__ == "__main__":
    unittest.main()
