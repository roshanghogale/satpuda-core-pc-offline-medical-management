"""A banner belongs to ONE store. So does the bill logo.

The owner's words: upload or pick a banner and it must stay with that store --
switch stores and you see that store's banner, or the built-in one, never the
picture belonging to the shop you just left.

The bytes were already kept per store (core.store_images keys its AppData
folder by store key). Three things that decide WHICH picture is shown were not,
and a store switch touched none of them:

  1. layout_config.txt is one file for the whole machine, and home_banner_path
     was one machine-wide value in it. Whichever shop picked a banner last, the
     next shop to open showed that file -- it exists on this disk, so it won,
     before anything per store was ever consulted. home_banner_use_default was
     shared the same way, so one shop ticking "use the built-in image" hid
     another shop's banner.
  2. core.store_images._server_cache held the store server's answer under the
     settings-row NAME alone -- "home_banner_image" is the same string for
     every shop there is. Switch stores inside the two-minute TTL and the
     picture fetched for the first shop was handed to the second, and
     resolve_path then WROTE those bytes into the second shop's folder. That
     one outlives the cache: the wrong picture is now that store's cached copy.
  3. core.pharmacy_profile_io._empty_profile_cache held the whole online
     profile -- name, address, GST, DL and logo_path -- for a minute, with
     nothing recording which store it came from.

Every case here is two shops on one PC, no network, and no live store.
"""
import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import layout_config as lc  # noqa: E402
from core import pharmacy_profile_io as ppio  # noqa: E402
from core import store_images as si  # noqa: E402
from core import store_manager as sm  # noqa: E402

AAI_BANNER = b"\x89PNG\r\n\x1a\n" + b"aai-medical-banner" * 4
BHAU_BANNER = b"\x89PNG\r\n\x1a\n" + b"bhau-medical-banner" * 4
AAI_LOGO = b"\x89PNG\r\n\x1a\n" + b"aai-medical-logo" * 4
BHAU_LOGO = b"\x89PNG\r\n\x1a\n" + b"bhau-medical-logo" * 4


def _profile(name: str) -> dict:
    return {
        "name": name,
        "address": f"{name} Road",
        "phone": "900",
        "email": "",
        "gstin": "",
        "dl_number": "",
        "gst_enabled": True,
        "logo_path": "",
        "fssai_number": "",
        "show_fssai_on_bill": True,
    }


class TwoShopsOnOnePc(unittest.TestCase):
    """One AppData, one config folder, two store folders, and a switch."""

    AAI = "Store_Aai_Medical"
    BHAU = "Store_Bhau_Medical"

    def setUp(self):
        self.appdata = tempfile.mkdtemp()
        self.cfg = tempfile.mkdtemp()
        self.stores_root = tempfile.mkdtemp()
        for key in (self.AAI, self.BHAU):
            os.makedirs(os.path.join(self.stores_root, key), exist_ok=True)
        self.active = self.AAI

        self.patches = [
            mock.patch.object(si, "_appdata_dir", return_value=self.appdata),
            mock.patch.object(ppio, "_appdata_dir", return_value=self.appdata),
            # The one switch every module reads through. Nothing here patches
            # store_images or pharmacy_profile_io's own idea of the store, so
            # each of them has to follow the switch on its own -- which is the
            # thing under test.
            mock.patch.object(
                sm, "get_active_store_key", side_effect=lambda: self.active
            ),
            mock.patch.object(sm, "get_stores_root", return_value=self.stores_root),
            mock.patch.object(lc, "_get_config_dir", return_value=self.cfg),
            mock.patch.object(
                lc,
                "_get_config_path",
                return_value=os.path.join(self.cfg, "layout_config.txt"),
            ),
        ]
        for p in self.patches:
            p.start()
        si._reset_server_cache()
        ppio._empty_profile_cache = None
        # Nothing in this file may reach a store server. Cases that want a
        # server answer patch it themselves.
        self._no_server = mock.patch.object(si, "fetch_from_server", return_value="")
        self._no_server.start()

    def tearDown(self):
        self._no_server.stop()
        for p in reversed(self.patches):
            p.stop()
        si._reset_server_cache()
        ppio._empty_profile_cache = None

    # helpers -----------------------------------------------------------------
    def switch_to(self, store_key):
        """What the shop does in Settings: this store is the active one now."""
        self.active = store_key

    def pick_banner(self, raw):
        """The whole Choose Image path, exactly as the screen calls it."""
        from core import desktop_settings_service as svc

        res = svc.save_uploaded_home_banner(
            "banner.png", base64.b64encode(raw).decode("ascii")
        )
        self.assertTrue(res.get("ok"), res)
        return str(res.get("path") or "")

    def bytes_at(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def shown_banner(self):
        return self.bytes_at(lc.get_home_banner_path())

    def default_banner(self):
        return self.bytes_at(lc.default_home_banner_path())


class TheBannerOnTheHomePage(TwoShopsOnOnePc):
    def test_the_other_shops_banner_is_not_this_shops_banner(self):
        self.pick_banner(AAI_BANNER)
        self.switch_to(self.BHAU)
        shown = self.shown_banner()
        self.assertNotEqual(
            shown,
            AAI_BANNER,
            "switching stores left the previous shop's banner on the home page",
        )
        self.assertEqual(
            shown,
            self.default_banner(),
            "a shop with no banner of its own must get the built-in image",
        )

    def test_each_shop_shows_its_own(self):
        self.pick_banner(AAI_BANNER)
        self.switch_to(self.BHAU)
        self.pick_banner(BHAU_BANNER)
        self.assertEqual(self.shown_banner(), BHAU_BANNER)
        self.switch_to(self.AAI)
        self.assertEqual(
            self.shown_banner(),
            AAI_BANNER,
            "the first shop lost its own banner when the second picked one",
        )

    def test_one_shop_choosing_the_built_in_image_does_not_hide_anothers(self):
        """home_banner_use_default was machine-wide too."""
        self.switch_to(self.BHAU)
        self.pick_banner(BHAU_BANNER)
        self.switch_to(self.AAI)
        from core.desktop_settings_service import save_appearance

        save_appearance({"home_banner_use_default": True})
        self.assertEqual(self.shown_banner(), self.default_banner())
        self.switch_to(self.BHAU)
        self.assertEqual(
            self.shown_banner(),
            BHAU_BANNER,
            "one shop asking for the built-in banner took another shop's away",
        )

    def test_saving_one_shops_banner_does_not_delete_the_other_shops_file(self):
        """The sweep of old copies was written when only one could be in use."""
        aai_file = self.pick_banner(AAI_BANNER)
        self.switch_to(self.BHAU)
        self.pick_banner(BHAU_BANNER)
        self.assertTrue(
            os.path.isfile(aai_file),
            "one shop picking a banner deleted the file the other shop shows",
        )

    def test_the_saved_path_is_reported_per_store(self):
        """Settings must not show the other shop's file as this shop's choice."""
        from core.desktop_settings_service import get_appearance

        self.pick_banner(AAI_BANNER)
        mine = str(get_appearance().get("home_banner_path") or "")
        self.assertTrue(mine)
        self.switch_to(self.BHAU)
        self.assertEqual(
            str(get_appearance().get("home_banner_path") or ""),
            "",
            "Settings offered one shop the other shop's banner file",
        )

    def test_a_banner_from_a_build_that_had_no_stores_is_not_guessed_at(self):
        """An unstamped machine-wide path can only be one shop's, and nobody knows whose."""
        loose = os.path.join(self.cfg, "home_banner_custom_old.png")
        with open(loose, "wb") as fh:
            fh.write(AAI_BANNER)
        # Written straight to the file, the way the older build wrote it: no
        # store recorded anywhere.
        import json

        with open(os.path.join(self.cfg, "layout_config.txt"), "w") as fh:
            json.dump({"home_banner_path": loose, "home_banner_use_default": False}, fh)
        self.assertEqual(
            self.shown_banner(),
            self.default_banner(),
            "an unowned banner was handed to whichever shop opened first",
        )

    def test_a_single_shop_pc_keeps_the_banner_it_already_had(self):
        """One store on this PC: the unstamped path can only be that store's."""
        import json
        import shutil

        shutil.rmtree(os.path.join(self.stores_root, self.BHAU))
        loose = os.path.join(self.cfg, "home_banner_custom_old.png")
        with open(loose, "wb") as fh:
            fh.write(AAI_BANNER)
        with open(os.path.join(self.cfg, "layout_config.txt"), "w") as fh:
            json.dump({"home_banner_path": loose, "home_banner_use_default": False}, fh)
        self.assertEqual(
            self.shown_banner(),
            AAI_BANNER,
            "a one-shop PC had to pick its banner again for no reason",
        )


class TheStoreServerAnswer(TwoShopsOnOnePc):
    """_server_cache was keyed by the settings-row name, which every shop shares."""

    def setUp(self):
        super().setUp()
        self._no_server.stop()
        self.server = {
            self.AAI: {
                "home_banner_image": si.to_data_uri(".png", AAI_BANNER),
                "bill_logo_image": si.to_data_uri(".png", AAI_LOGO),
            },
            self.BHAU: {
                "home_banner_image": si.to_data_uri(".png", BHAU_BANNER),
                "bill_logo_image": si.to_data_uri(".png", BHAU_LOGO),
            },
        }
        self.online = [
            mock.patch.object(si, "_is_online", return_value=True),
            mock.patch("core.server_live._token", return_value="tok"),
            mock.patch("core.server_api._request", side_effect=self._answer),
        ]
        for p in self.online:
            p.start()

    def tearDown(self):
        for p in reversed(self.online):
            p.stop()
        self._no_server.start()
        super().tearDown()

    def _answer(self, method, path, *a, **kw):
        """The server answers for the store this machine is paired to."""
        rows = [
            {"name": name, "value": value}
            for name, value in self.server[self.active].items()
        ]
        return {"ok": True, "data": rows}

    def test_the_picture_fetched_for_one_shop_is_not_served_to_the_next(self):
        self.assertEqual(
            si.fetch_from_server(si.HOME_BANNER),
            self.server[self.AAI]["home_banner_image"],
        )
        self.switch_to(self.BHAU)
        self.assertEqual(
            si.fetch_from_server(si.HOME_BANNER),
            self.server[self.BHAU]["home_banner_image"],
            "the second shop was served the banner cached for the first",
        )

    def test_a_cached_answer_is_not_written_into_the_next_shops_folder(self):
        """The worst of it: the wrong picture becomes that store's cached copy."""
        si.fetch_from_server(si.HOME_BANNER)
        self.switch_to(self.BHAU)
        found = si.resolve_path(si.HOME_BANNER, "")
        self.assertTrue(found)
        self.assertEqual(
            self.bytes_at(found),
            BHAU_BANNER,
            "one shop's banner was cached as the other shop's own picture",
        )

    def test_the_bill_logo_is_no_different(self):
        si.fetch_from_server(si.BILL_LOGO)
        self.switch_to(self.BHAU)
        found = si.resolve_path(si.BILL_LOGO, "")
        self.assertTrue(found)
        self.assertEqual(
            self.bytes_at(found),
            BHAU_LOGO,
            "one pharmacy's letterhead was cached as another pharmacy's logo",
        )

    def test_the_shops_own_cached_picture_still_answers_without_the_server(self):
        """Nothing above may cost the machine that already has the file."""
        si.save_image(si.HOME_BANNER, ".png", BHAU_BANNER, share=False)

        def explode(*_a, **_k):
            raise AssertionError("asked the server for a picture that is right here")

        with mock.patch("core.server_api._request", side_effect=explode):
            self.assertEqual(
                self.bytes_at(si.resolve_path(si.HOME_BANNER, "")), BHAU_BANNER
            )


class TheOnlineProfile(TwoShopsOnOnePc):
    """The logo travels inside the profile, and the profile was cached flat."""

    def setUp(self):
        super().setUp()
        # The real fetch, not a stand-in for it: the cache is written INSIDE
        # fetch_profile_from_server, so patching that function out is exactly
        # how this bug hides from a test.
        self.online = [
            mock.patch.object(ppio, "_is_online", return_value=True),
            mock.patch("core.server_live._token", return_value="tok"),
            mock.patch("core.server_api._request", side_effect=self._answer),
        ]
        for p in self.online:
            p.start()

    def tearDown(self):
        for p in reversed(self.online):
            p.stop()
        super().tearDown()

    def _answer(self, method, path, *a, **kw):
        """The server answers for the store this machine is paired to."""
        return {"ok": True, "data": _profile(self.active)}

    def test_the_next_shop_does_not_print_the_previous_shops_header(self):
        self.assertEqual(ppio.load_pharmacy_profile(None)["name"], self.AAI)
        self.switch_to(self.BHAU)
        self.assertEqual(
            ppio.load_pharmacy_profile(None)["name"],
            self.BHAU,
            "a bill printed after the switch carried the other shop's name",
        )

    def test_the_next_shop_does_not_print_the_previous_shops_logo(self):
        """Saving a profile caches it too -- logo path and all -- for a minute."""
        picked = os.path.join(self.appdata, "picked_by_aai.png")
        with open(picked, "wb") as fh:
            fh.write(AAI_LOGO)
        with mock.patch("core.online_guard.ensure_can_mutate"), \
                mock.patch("core.server_crud.push_pharmacy_profile"), \
                mock.patch.object(si, "_is_online", return_value=False):
            ppio.save_pharmacy_profile(
                None, dict(_profile(self.AAI), logo_path=picked)
            )
        self.switch_to(self.BHAU)
        second = ppio.load_pharmacy_profile(None)
        self.assertEqual(
            second["name"],
            self.BHAU,
            "the shop header cached on save outlived the store it belonged to",
        )
        logo = str(second.get("logo_path") or "")
        if logo:
            self.assertNotEqual(
                self.bytes_at(logo),
                AAI_LOGO,
                "the other pharmacy's letterhead came back with the profile",
            )


class WhatAStoreSwitchDrops(TwoShopsOnOnePc):
    def test_switching_stores_leaves_no_picture_cached_from_the_last_one(self):
        si._server_cache[(self.AAI, "home_banner_image")] = (
            9e9,
            si.to_data_uri(".png", AAI_BANNER),
        )
        ppio._empty_profile_cache = (9e9, self.AAI, _profile(self.AAI))
        entry = {"store_key": self.BHAU, "display_name": "Bhau Medical"}
        with mock.patch.object(sm, "_find_store_by_key", return_value=entry), \
                mock.patch.object(sm, "load_registry", return_value={"stores": [entry]}), \
                mock.patch.object(sm, "save_registry"), \
                mock.patch.object(sm, "_sync_backup_config_for_store"):
            self.assertTrue(sm.set_active_store(self.BHAU))
        self.assertEqual(
            si._server_cache, {}, "the store switch kept the last store's pictures"
        )
        self.assertIsNone(
            ppio._empty_profile_cache,
            "the store switch kept the last store's profile, logo path and all",
        )

    def test_switching_stores_leaves_no_catalogue_cached_from_the_last_one(self):
        """The third copy of this defect, and the one that reaches the bill.

        core.online_catalog holds the whole shop -- customers, suppliers,
        doctors, medicines -- under the bare collection name, "customers",
        which is the same string for every shop on the PC. It is the same
        mistake store_images made with the settings-row name, and a bill is
        built straight out of it: core/bill_output.py:205 resolves the customer
        and every medicine through find_customer_by_id / medicine_by_id.

        Nothing restarts the app on a switch to clear it -- the desktop's
        switch_store branch is the one that ignores needs_restart -- so this
        has to be dropped here or shop B prints shop A's customer.
        """
        from core import online_catalog as oc

        self.addCleanup(oc.invalidate)
        oc.invalidate()
        aai_customer = {"id": 41, "name": "AAI WALK-IN", "total_due": 120.0}
        with oc._lock:
            oc._cache["customers"] = (9e9, [aai_customer])
            oc._apply_list_indexes("customers", [aai_customer])
        # The bill renderer's own call, before the switch.
        with mock.patch.object(oc, "customers", return_value=[aai_customer]):
            self.assertIsNotNone(oc.find_customer_by_id(41))

        entry = {"store_key": self.BHAU, "display_name": "Bhau Medical"}
        with mock.patch.object(sm, "_find_store_by_key", return_value=entry), \
                mock.patch.object(sm, "load_registry", return_value={"stores": [entry]}), \
                mock.patch.object(sm, "save_registry"), \
                mock.patch.object(sm, "_sync_backup_config_for_store"):
            self.assertTrue(sm.set_active_store(self.BHAU))

        self.assertNotIn(
            "customers",
            oc._cache,
            "the store switch kept the last store's customer list",
        )
        # `customers` patched to answer nothing is the store server not having
        # replied yet -- the window the TTL used to paper over with shop A's
        # rows. Bhau must get nobody here, never Aai's walk-in.
        with mock.patch.object(oc, "customers", return_value=[]):
            self.assertIsNone(
                oc.find_customer_by_id(41),
                "a bill for the next shop was built from the last shop's customer",
            )


class TheUnkeyedFileInAppData(TwoShopsOnOnePc):
    """AppData\\pharmacy_logo.png was written before pictures had a store."""

    def test_it_is_not_matched_by_name_for_whichever_shop_asks(self):
        loose = os.path.join(self.appdata, "pharmacy_logo.png")
        with open(loose, "wb") as fh:
            fh.write(AAI_LOGO)
        # The hint is a path from the shop's OTHER machine. Only the file name
        # survives it, and that name matches a file nobody on this PC owns.
        # Written with forward slashes so the name really is split off on every
        # platform the suite runs on -- a Windows path on a Mac has no basename
        # at all, and the hole hides.
        found = si.resolve_path(
            si.BILL_LOGO, "//OTHER-PC/VeterinaryApp/pharmacy_logo.png"
        )
        self.assertEqual(
            found,
            "",
            "a name match handed one pharmacy's letterhead to another pharmacy",
        )


if __name__ == "__main__":
    unittest.main()
