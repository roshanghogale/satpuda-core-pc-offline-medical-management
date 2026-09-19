"""The shop's logo and banner are pictures, and a path is not a picture.

Two things the owner reported, one cause:

  "bill war logo print hot nahi"  -- the logo does not print on the bill
  "banner change hot nahi"        -- changing the banner does nothing

and his own diagnosis: "logo ani banner che path local madhech store astat ani
files pn" -- the paths, and the files, only ever live on the local machine.

The logo was saved as pharmacy_profile.logo_path, an absolute path on whichever
PC picked it. Online the profile comes from the store server, and the server's
copy of that column is NULL for every store there is -- so the server answer
REPLACED the good local path with '' and the bill printed with no logo even on
the machine holding the picture. When a server row does carry a path it is some
other machine's C:\\Users\\..., and the file is not here either.

The banner was saved into layout_config.txt, which never leaves this machine at
all, and get_home_banner_path fell back to the built-in image without a word --
so the shop changed the banner and nothing visibly happened.

Both now resolve through core.store_images: the saved path is a hint, the bytes
are cached in AppData per store, and an explicit save carries the picture with
the store through the settings key/value channel that already exists. A shop
that has the file at the saved path still gets exactly that file, which is the
first rung of the ladder and the reason Offline is unchanged.
"""
import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import bill_context  # noqa: E402
from core import layout_config as lc  # noqa: E402
from core import pharmacy_profile_io as ppio  # noqa: E402
from core import store_images as si  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"satpuda-shop-logo" * 4
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"a-different-shop" * 4
# What the server actually holds for every store today: no path at all.
SERVER_PROFILE = {
    "name": "ZZ TEST MEDICAL",
    "address": "Main Road",
    "phone": "900",
    "gstin": "27ZZZZZ0000Z1Z5",
    "dl_number": "DL-1",
    "gst_enabled": True,
    "logo_path": "",
    "fssai_number": "",
    "show_fssai_on_bill": True,
}
FOREIGN_PATH = r"C:\Users\rosha\AppData\Local\VeterinaryApp\pharmacy_logo.png"


class StoreImageCase(unittest.TestCase):
    """One temp AppData, one store key, no network, no live shop."""

    store_key = "ZZ_Test"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [
            mock.patch.object(si, "_appdata_dir", return_value=self.dir),
            mock.patch.object(si, "_store_key", return_value=self.store_key),
            mock.patch.object(ppio, "_appdata_dir", return_value=self.dir),
            mock.patch.object(ppio, "_active_store_key", return_value=self.store_key),
        ]
        for p in self.patches:
            p.start()
        si._reset_server_cache()
        ppio._empty_profile_cache = None
        # No test may reach a real store server. Every case that wants a server
        # answer patches this itself.
        self._no_server = mock.patch.object(si, "fetch_from_server", return_value="")
        self._no_server.start()

    def tearDown(self):
        self._no_server.stop()
        for p in reversed(self.patches):
            p.stop()
        si._reset_server_cache()
        ppio._empty_profile_cache = None

    # helpers -----------------------------------------------------------------
    def put_store_picture(self, kind=si.BILL_LOGO, raw=PNG, ext=".png"):
        """The picture cached in AppData for THIS store."""
        os.makedirs(si.images_dir(), exist_ok=True)
        path = os.path.join(si.images_dir(), f"{kind}{ext}")
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def loose_file(self, name="picked.png", raw=PNG):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def online(self, remote=None):
        """Online mode with the server answering `remote` for the profile."""
        return (
            mock.patch.object(ppio, "_is_online", return_value=True),
            mock.patch.object(si, "_is_online", return_value=True),
            mock.patch.object(
                ppio,
                "fetch_profile_from_server",
                return_value=dict(remote if remote is not None else SERVER_PROFILE),
            ),
        )


class TheLogoOnTheBill(StoreImageCase):
    def test_an_empty_server_logo_path_no_longer_wipes_the_shops_logo(self):
        """The server says '' for every store. That is not "this shop has none"."""
        self.put_store_picture()
        for p in self.online():
            self.addCleanup(p.stop)
            p.start()
        profile = ppio.load_pharmacy_profile(None)
        self.assertTrue(
            profile["logo_path"],
            "Online replaced the shop's logo with the server's empty column",
        )
        self.assertTrue(os.path.isfile(profile["logo_path"]))

    def test_another_machines_path_is_not_the_answer(self):
        self.put_store_picture()
        for p in self.online(dict(SERVER_PROFILE, logo_path=FOREIGN_PATH)):
            self.addCleanup(p.stop)
            p.start()
        profile = ppio.load_pharmacy_profile(None)
        self.assertNotEqual(
            profile["logo_path"],
            FOREIGN_PATH,
            "kept a path that only exists on the PC that uploaded the picture",
        )
        self.assertTrue(os.path.isfile(profile["logo_path"]))

    def test_the_bill_prints_the_logo_on_a_machine_that_never_uploaded_it(self):
        """Nothing on this disk -- the picture comes down with the store."""
        uri = si.to_data_uri(".png", PNG)
        self._no_server.stop()
        try:
            with mock.patch.object(si, "_is_online", return_value=True), \
                    mock.patch.object(si, "fetch_from_server", return_value=uri):
                out = bill_context._logo_to_base64("")
        finally:
            self._no_server.start()
        self.assertEqual(out, uri, "the bill printed with no logo")

    def test_a_shop_with_its_own_file_prints_exactly_that_file(self):
        """Offline / single PC must behave as it always did, and stay off the network."""
        path = self.loose_file(raw=OTHER_PNG)
        self.put_store_picture(raw=PNG)

        def explode(*_a, **_k):
            raise AssertionError("asked the server for a picture that is right here")

        with mock.patch.object(si, "fetch_from_server", side_effect=explode):
            out = bill_context._logo_to_base64(path)
        self.assertEqual(
            out,
            "data:image/png;base64," + base64.b64encode(OTHER_PNG).decode("ascii"),
            "the shop's own file stopped being the one that prints",
        )

    def test_no_picture_anywhere_still_prints_no_logo(self):
        self.assertEqual(bill_context._logo_to_base64(""), "")
        self.assertEqual(bill_context._logo_to_base64(FOREIGN_PATH), "")

    def test_a_logo_from_an_older_build_is_adopted_without_the_shop_re_picking(self):
        """Older builds wrote AppData\\pharmacy_logo.png. The file is still there."""
        legacy = self.loose_file("pharmacy_logo.png", raw=PNG)
        with mock.patch.object(si, "_single_store_install", return_value=True):
            found = si.resolve_path(si.BILL_LOGO, "")
        self.assertTrue(found, "the shop had to pick its logo again for no reason")
        with open(found, "rb") as fh:
            self.assertEqual(fh.read(), PNG)
        self.assertTrue(os.path.isfile(legacy))

    def test_an_unowned_logo_is_not_guessed_at_on_a_two_shop_pc(self):
        """It was written before pictures had a store. Nobody knows whose it is."""
        self.loose_file("pharmacy_logo.png", raw=PNG)
        with mock.patch.object(si, "_single_store_install", return_value=False):
            self.assertEqual(
                si.resolve_path(si.BILL_LOGO, ""),
                "",
                "one pharmacy's letterhead was handed to whichever shop asked first",
            )

    def test_offline_keeps_the_path_the_shop_saved(self):
        path = self.loose_file()
        out = ppio._repair_logo_path({"name": "ZZ", "logo_path": path})
        self.assertEqual(out["logo_path"], path)


class TheLogoBelongsToOneShop(StoreImageCase):
    def test_another_shop_on_this_pc_does_not_get_this_shops_logo(self):
        """One AppData\\bill_logo.png used to be shared by every store on the PC."""
        self.put_store_picture(raw=PNG)
        with mock.patch.object(si, "_store_key", return_value="Some_Other_Shop"):
            self.assertEqual(
                si.resolve_path(si.BILL_LOGO, ""),
                "",
                "one pharmacy's letterhead resolved for another pharmacy",
            )

    def test_each_shop_keeps_its_own(self):
        self.put_store_picture(raw=PNG)
        with mock.patch.object(si, "_store_key", return_value="Some_Other_Shop"):
            si.save_image(si.BILL_LOGO, ".png", OTHER_PNG, share=False)
            other = si.resolve_path(si.BILL_LOGO, "")
        with open(other, "rb") as fh:
            self.assertEqual(fh.read(), OTHER_PNG)
        with open(si.resolve_path(si.BILL_LOGO, ""), "rb") as fh:
            self.assertEqual(fh.read(), PNG)


class TheHomeBanner(StoreImageCase):
    def setUp(self):
        super().setUp()
        self.cfg = tempfile.mkdtemp()
        self._c = mock.patch.object(lc, "_get_config_dir", return_value=self.cfg)
        self._c.start()
        self._p = mock.patch.object(
            lc, "_get_config_path", return_value=os.path.join(self.cfg, "layout.txt")
        )
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._c.stop()
        super().tearDown()

    def write_layout(self, **kw):
        cfg = lc.load_layout()
        cfg.update(kw)
        lc.save_layout(cfg)

    def test_a_machine_that_did_not_pick_the_banner_still_shows_it(self):
        self.write_layout(home_banner_use_default=False, home_banner_path="")
        self.put_store_picture(kind=si.HOME_BANNER)
        found = lc.get_home_banner_path()
        self.assertNotEqual(
            found,
            lc.default_home_banner_path(),
            "the shop changed its banner and the built-in picture came back",
        )
        with open(found, "rb") as fh:
            self.assertEqual(fh.read(), PNG)

    def test_a_banner_path_from_another_machine_falls_through_to_the_picture(self):
        self.write_layout(
            home_banner_use_default=False,
            home_banner_path=r"D:\SatpudaCounter\config\home_banner_custom_1.png",
        )
        self.put_store_picture(kind=si.HOME_BANNER)
        with open(lc.get_home_banner_path(), "rb") as fh:
            self.assertEqual(fh.read(), PNG)

    def test_the_shops_own_banner_file_still_wins(self):
        own = os.path.join(self.cfg, "home_banner_custom_own.png")
        with open(own, "wb") as fh:
            fh.write(OTHER_PNG)
        self.write_layout(home_banner_use_default=False, home_banner_path=own)
        self.put_store_picture(kind=si.HOME_BANNER, raw=PNG)
        self.assertEqual(lc.get_home_banner_path(), own)

    def test_use_default_still_means_the_built_in_banner(self):
        self.write_layout(home_banner_use_default=True, home_banner_path="")
        self.put_store_picture(kind=si.HOME_BANNER)
        self.assertEqual(lc.get_home_banner_path(), lc.default_home_banner_path())

    def test_a_shop_with_no_banner_gets_the_built_in_one(self):
        self.write_layout(home_banner_use_default=False, home_banner_path="")
        self.assertEqual(lc.get_home_banner_path(), lc.default_home_banner_path())


class ThePictureTravelsWithTheStore(StoreImageCase):
    """Saving sends the BYTES. Reading sends nothing at all."""

    def pushes(self):
        calls = []

        def record(name, value):
            calls.append((name, value))
            return True

        return calls, mock.patch("core.server_live.push_settings_kv", side_effect=record)

    def test_saving_the_bill_logo_carries_the_picture_not_just_the_path(self):
        from core import desktop_settings_service as svc

        calls, patch = self.pushes()
        with mock.patch.object(si, "_is_online", return_value=True), \
                mock.patch("core.online_guard.ensure_can_mutate"), patch:
            res = svc.save_uploaded_bill_logo(
                "logo.png", base64.b64encode(PNG).decode("ascii")
            )
        self.assertTrue(res["ok"], res)
        self.assertTrue(os.path.isfile(res["path"]))
        self.assertEqual(
            [n for n, _ in calls],
            ["bill_logo_image"],
            "only the path went to the server, so no other machine can print it",
        )
        self.assertEqual(si.from_data_uri(calls[0][1])[1], PNG)

    def test_saving_the_banner_carries_the_picture(self):
        from core import desktop_settings_service as svc

        cfg = tempfile.mkdtemp()
        calls, patch = self.pushes()
        with mock.patch.object(lc, "_get_config_dir", return_value=cfg), \
                mock.patch.object(
                    lc, "_get_config_path", return_value=os.path.join(cfg, "layout.txt")
                ), \
                mock.patch.object(si, "_is_online", return_value=True), \
                mock.patch("core.online_guard.ensure_can_mutate"), patch:
            res = svc.save_uploaded_home_banner(
                "banner.png", base64.b64encode(PNG).decode("ascii")
            )
        self.assertTrue(res["ok"], res)
        self.assertEqual([n for n, _ in calls], ["home_banner_image"])
        self.assertEqual(si.from_data_uri(calls[0][1])[1], PNG)

    def test_offline_sends_nothing(self):
        from core import desktop_settings_service as svc

        calls, patch = self.pushes()
        with mock.patch.object(si, "_is_online", return_value=False), patch:
            svc.save_uploaded_bill_logo(
                "logo.png", base64.b64encode(PNG).decode("ascii")
            )
        self.assertEqual(calls, [], "an offline save reached out to a live store")

    def test_printing_a_bill_never_writes_to_the_store(self):
        calls, patch = self.pushes()
        self.put_store_picture()
        with mock.patch.object(si, "_is_online", return_value=True), patch:
            bill_context._logo_to_base64("")
            si.resolve_path(si.BILL_LOGO, "")
            ppio._repair_logo_path({"name": "ZZ", "logo_path": ""})
        self.assertEqual(calls, [], "a read pushed to a live store")

    def test_a_server_that_will_not_take_it_does_not_lose_the_local_copy(self):
        from core import desktop_settings_service as svc

        def refuse(*_a, **_k):
            raise RuntimeError("no route to host")

        with mock.patch.object(si, "_is_online", return_value=True), \
                mock.patch("core.online_guard.ensure_can_mutate"), \
                mock.patch("core.server_live.push_settings_kv", side_effect=refuse):
            res = svc.save_uploaded_bill_logo(
                "logo.png", base64.b64encode(PNG).decode("ascii")
            )
        self.assertTrue(res["ok"], res)
        self.assertTrue(os.path.isfile(res["path"]))
        self.assertFalse(res["shared_with_store"])
        self.assertIn("could not be sent", res["message"])


class WhatTravelsIsKeptSmall(StoreImageCase):
    """Every device pulls these rows. A signboard photo is not a letterhead."""

    def big_png(self, width=3000, height=1200):
        try:
            import io

            from PIL import Image
        except ImportError:  # pragma: no cover - Pillow ships with the app
            self.skipTest("Pillow not installed")
        img = Image.new("RGB", (width, height))
        for x in range(0, width, 7):
            for y in range(0, height, 11):
                img.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, (x + y) % 256))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_a_huge_picture_is_trimmed_before_it_goes_to_the_store(self):
        raw = self.big_png()
        calls = []
        with mock.patch.object(si, "_is_online", return_value=True), \
                mock.patch("core.online_guard.ensure_can_mutate"), \
                mock.patch(
                    "core.server_live.push_settings_kv",
                    side_effect=lambda n, v: calls.append((n, v)) or True,
                ):
            shared, note = si.push_to_server(si.BILL_LOGO, ".png", raw)
        self.assertTrue(shared, note)
        sent = si.from_data_uri(calls[0][1])[1]
        self.assertLess(len(sent), len(raw), "the full-size picture went up as-is")
        self.assertLessEqual(len(sent), si._MAX_SHARED_BYTES)

    def test_the_local_copy_is_the_shops_own_file_untouched(self):
        raw = self.big_png()
        with mock.patch.object(si, "_is_online", return_value=False):
            out = si.save_image(si.BILL_LOGO, ".png", raw)
        with open(out["path"], "rb") as fh:
            self.assertEqual(fh.read(), raw, "the shop's own file was downscaled")

    def test_one_that_will_not_fit_stays_here_and_says_so(self):
        """The megabytes stay here -- but so does nothing else.

        This used to assert the settings row was not touched at all, and that
        left the picture the shop had just REPLACED standing on the server for
        every other counter to go on printing. The row is emptied instead, which
        sends nothing but an empty string, and the note says so.
        """
        raw = b"\x89PNG\r\n\x1a\n" + os.urandom(si._MAX_SHARED_BYTES + 1000)
        with mock.patch.object(si, "_is_online", return_value=True), \
                mock.patch("core.online_guard.ensure_can_mutate"), \
                mock.patch("core.server_live.push_settings_kv") as push:
            shared, note = si.push_to_server(si.BILL_LOGO, ".png", raw)
        self.assertFalse(shared)
        self.assertIn("too big", note)
        self.assertIn("no picture instead of the old one", note)
        push.assert_called_once_with("bill_logo_image", "")

    def test_the_pictures_are_not_copied_into_the_settings_mirror(self):
        from core import settings_mirror

        self.assertFalse(settings_mirror._is_durable("bill_logo_image"))
        self.assertFalse(settings_mirror._is_durable("home_banner_image"))
        self.assertTrue(settings_mirror._is_durable("low_stock_threshold"))


class TheServerAnswerIsRead(StoreImageCase):
    def test_both_shapes_the_settings_pull_has_answered_with(self):
        rows = [{"name": "bill_logo_image", "value": "x"}]
        self.assertEqual(si._kv_rows(rows), rows)
        self.assertEqual(si._kv_rows([{"id": 0, "settings": rows}]), rows)
        self.assertEqual(si._kv_rows(None), [])

    def test_a_picture_that_comes_down_is_kept_for_next_time(self):
        uri = si.to_data_uri(".png", PNG)
        self._no_server.stop()
        try:
            with mock.patch.object(si, "_is_online", return_value=True), \
                    mock.patch.object(si, "fetch_from_server", return_value=uri) as fetch:
                first = si.resolve_path(si.BILL_LOGO, "")
                second = si.resolve_path(si.BILL_LOGO, "")
        finally:
            self._no_server.start()
        self.assertEqual(first, second)
        self.assertTrue(os.path.isfile(first))
        self.assertEqual(fetch.call_count, 1, "every bill went back to the server")

    def test_a_data_uri_round_trips(self):
        for ext, raw in ((".png", PNG), (".jpg", OTHER_PNG)):
            got_ext, got_raw = si.from_data_uri(si.to_data_uri(ext, raw))
            self.assertEqual(got_raw, raw)
            self.assertEqual(got_ext, ext)

    def test_rubbish_is_not_a_picture(self):
        self.assertEqual(si.from_data_uri(""), ("", b""))
        self.assertEqual(si.from_data_uri("http://example.com/x.png"), ("", b""))
        self.assertEqual(si.from_data_uri("data:image/png,notbase64"), ("", b""))


if __name__ == "__main__":
    unittest.main()
