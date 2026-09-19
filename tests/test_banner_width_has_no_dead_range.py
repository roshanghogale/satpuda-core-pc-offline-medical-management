"""Settings -> Appearance -> Banner width, second round: the dead range.

The owner went from the 1500 default to 1200, pressed Save Appearance, and the
banner did not change. The first fix was installed; his AppData file holds
"home_banner_size": 1200, so the save worked and the engine served it. His PC
runs 1440x900 at 96 DPI with the window maximized, and measured against the real
stylesheet at 1440x829 the panel the banner lives in is 1204 px:

    1200 px -> 1200x604        1250 / 1500 / 2500 / 4000 px -> 1204x606

A pixel width above the panel's own width draws nothing new, so his change moved
the banner by four pixels. The setting is now a share of that panel (10-100 %),
which has no dead range on any screen; a value already saved in pixels keeps
drawing what it drew until the shop types a percentage.

Also pinned: the frozen engine reads the AppData file, never the copy bundled in
_internal/config (that one only seeds a first install), and a release no longer
carries the builder's own layout_config.txt.
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
_SRC = os.path.join(_REPO, "desktop", "src")
_FAKE_STATS = {"today_str": "11-09-2026", "fy_label": "2026-27", "values": ["0.00"] * 12}


def _read(*parts):
    with open(os.path.join(_SRC, *parts), encoding="utf-8") as fh:
        return fh.read()


class _TempLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "layout_config.txt")
        patcher = mock.patch.object(lc, "_get_config_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _save(self, **fields):
        from core.desktop_settings_service import save_appearance

        return save_appearance(fields)

    def _payload(self):
        import core.desktop_api as api

        with mock.patch("core.home_dashboard.query_dashboard_stats",
                        return_value=dict(_FAKE_STATS)):
            return api._dashboard_payload(sqlite3.connect(":memory:"))

    def _on_disk(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


class TheWidthIsAShareOfThePanel(_TempLayout):

    def test_a_percentage_is_saved_read_back_and_served(self):
        self._save(home_banner_width_pct=70)
        from core.desktop_settings_service import get_appearance

        self.assertEqual(get_appearance()["home_banner_width_pct"], 70)
        self.assertEqual(self._payload()["home_banner_width_pct"], 70,
                         "Home reads the size from this payload and nothing else")

    def test_online_mode_serves_it_from_the_local_file(self):
        """Online is server-only; the width is per screen, not per database."""
        from core import sync_prefs

        self._save(home_banner_width_pct=55)
        with mock.patch.object(sync_prefs, "is_online_mode", return_value=True):
            self.assertEqual(self._payload()["home_banner_width_pct"], 55)

    def test_it_survives_a_restart_and_a_store_switch(self):
        self._save(home_banner_width_pct=62)
        self.assertEqual(self._on_disk()["home_banner_width_pct"], 62)
        for store in ("shop-a", "shop-b", ""):
            with mock.patch.object(lc, "_active_store_key", return_value=store):
                self.assertEqual(lc.get_home_banner_width_pct(), 62, store)

    def test_another_settings_save_does_not_drop_it(self):
        """save_layout(load_layout()) is how every settings save writes."""
        self._save(home_banner_width_pct=45)
        lc.save_layout(lc.load_layout())
        self._save(font_size=11, quick_access={"new_bill": True})
        self.assertEqual(lc.get_home_banner_width_pct(), 45)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(self._save(home_banner_width_pct=5)["home_banner_width_pct"], 10)
        self.assertEqual(self._save(home_banner_width_pct=250)["home_banner_width_pct"], 100)

    def test_junk_or_blank_keeps_what_was_saved(self):
        self._save(home_banner_width_pct=60)
        for junk in ("", None, "wide", "12abc"):
            out = self._save(home_banner_width_pct=junk)
            self.assertEqual(out["home_banner_width_pct"], 60, repr(junk))
        # The old pixel key must not 500 on junk either.
        self.assertIsInstance(self._save(home_banner_size="abc"), dict)

    def test_junk_on_disk_does_not_throw_away_the_other_settings(self):
        with open(self.path, "w") as fh:
            json.dump({"billing_rows": 9, "home_banner_width_pct": "wide"}, fh)
        layout = lc.load_layout()
        self.assertEqual(layout["billing_rows"], 9)
        self.assertEqual(layout["home_banner_width_pct"], 0)


class APixelValueAlreadySavedIsReadLosslessly(_TempLayout):
    """The owner's file: {"home_banner_size": 1200}, no percentage yet."""

    def setUp(self):
        super().setUp()
        with open(self.path, "w") as fh:
            json.dump({"home_banner_size": 1200, "billing_rows": 9}, fh)

    def test_it_is_served_as_pixels_with_no_percentage(self):
        payload = self._payload()
        self.assertEqual(payload["home_banner_size"], 1200)
        self.assertEqual(payload["home_banner_width_pct"], 0,
                         "0 tells Home the pixel value still decides")

    def test_choosing_a_percentage_leaves_the_pixels_for_older_builds(self):
        self._save(home_banner_width_pct=70)
        disk = self._on_disk()
        self.assertEqual(disk["home_banner_width_pct"], 70)
        self.assertEqual(disk["home_banner_size"], 1200)


class TheFrontEndDrawsEveryValue(unittest.TestCase):

    def setUp(self):
        # Code only: the header comments describe the old field in its own words.
        strip = lambda s: re.sub(r"/\*.*?\*/", "", s, flags=re.S)  # noqa: E731
        self.field = strip(_read("pages", "settings", "BannerWidthField.tsx"))
        self.prefs = strip(_read("pages", "settings", "PrefsPanels.tsx"))
        self.home = _read("pages", "HomePage.tsx")
        self.lib = _read("homeBanner.ts")
        self.css = _read("styles.css")

    def test_home_sizes_the_banner_as_a_percentage_of_its_panel(self):
        self.assertIn("home_banner_width_pct", self.home)
        self.assertIn("bannerCssWidth(", self.home)
        self.assertRegex(self.lib, r"return `\$\{clampBannerPct\(pct\)\}%`",
                         "a percentage has to reach the stylesheet as a %")

    def test_no_height_cap_brings_the_dead_range_back(self):
        m = re.search(r"\.home-banner-img\s*\{(.*?)\}", self.css, re.S)
        block = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
        self.assertNotIn("max-height", block)

    def test_the_field_is_typed_text_not_a_spinner(self):
        self.assertIn("<BannerWidthField", self.prefs)
        self.assertNotRegex(self.prefs, r"home_banner_size:\s*Number\(e\.target\.value\)",
                            "Number('') is 0: clearing the box fought the operator")
        self.assertNotIn('type="number"', self.field)
        self.assertIn('type="text"', self.field)
        self.assertIn('inputMode="numeric"', self.field)
        self.assertNotIn("Number(e.target.value)", self.field)
        self.assertIn("onBlur={commit}", self.field)

    def test_it_previews_on_home_while_typing(self):
        self.assertIn("previewBannerPct(", self.field)
        self.assertIn("addEventListener(BANNER_PREVIEW_EVENT", self.home)

    def test_the_parser_never_turns_blank_into_zero(self):
        self.assertIn("if (!t) return { kind: 'empty' }", self.lib)


class TheFrozenEngineReadsAppDataNotTheBundle(unittest.TestCase):
    """H2, disproved and pinned: _internal/config only seeds a first install."""

    def test_the_saved_file_wins_over_the_bundled_one(self):
        appdata = tempfile.mkdtemp()
        bundle = tempfile.mkdtemp()
        os.makedirs(os.path.join(appdata, "VeterinaryApp"))
        os.makedirs(os.path.join(bundle, "config"))
        with open(os.path.join(bundle, "config", "layout_config.txt"), "w") as fh:
            json.dump({"home_banner_size": 1000, "home_banner_width_pct": 30}, fh)
        with open(os.path.join(appdata, "VeterinaryApp", "layout_config.txt"), "w") as fh:
            json.dump({"home_banner_size": 1200}, fh)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", bundle, create=True), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": appdata}):
            self.assertEqual(
                lc._get_config_path(),
                os.path.join(appdata, "VeterinaryApp", "layout_config.txt"))
            self.assertEqual(lc.get_home_banner_size()[0], 1200)
            self.assertEqual(lc.get_home_banner_width_pct(), 0)

    def test_a_release_does_not_carry_the_builders_layout(self):
        import build_release_filter as brf

        pairs = [("config/layout_config.txt", "config"),
                 ("config/theme_config.txt", "config")]
        with mock.patch.object(brf, "CLEAN_RELEASE", True):
            kept = [src for src, _ in brf.clean_release(pairs)]
        self.assertEqual(kept, ["config/theme_config.txt"],
                         "1.0.2 shipped home_banner_size 1000 from the build machine")


if __name__ == "__main__":
    unittest.main()
