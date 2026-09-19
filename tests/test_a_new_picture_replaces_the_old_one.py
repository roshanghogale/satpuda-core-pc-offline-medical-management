"""Picking a new banner or logo has to REMOVE the one it replaces.

The owner: "banner ani icon badalla tar juna delete zala pahije" -- change the
banner or the shop icon and the old one should go. Today it is left behind, in
four different places, and two of them are what he actually sees:

  * config\\home_banner_custom_<timestamp>.png. copy_custom_home_banner stamps
    every copy with the time and nothing ever removed the previous one, so a
    shop that changed its banner ten times had ten full-size images sitting in
    the config folder and looked at exactly one of them. This is the one that
    ACCUMULATES without limit.
  * pharmacy_profile.logo_path and the AppData sidecar. Uploading a new logo
    wrote a new file and left both of them naming the old one -- and
    resolve_path takes an existing path first, so the bill and the preview went
    on printing the logo the shop had just replaced. Nothing piles up here; the
    stale value is simply the one that wins.
  * the per-store AppData cache. It does NOT accumulate: _write_cache writes
    <kind><ext> and drops the other extensions. Kept honest by a test here.
  * the store's settings row. A push replaces it, so a normal online change is
    clean -- but a change made OFFLINE, or one the server refused, left the old
    picture standing on the server for every other counter to print, and
    clearing the picture never touched the row at all, so the next read pulled
    the removed logo straight back down.

The order is the whole safety argument and it is only ever one way: write the
new picture, point everything at it, and only then remove the old copy. And a
delete is limited to copies THIS app made for THIS store -- never the picture
the shop picked out of its own folders, never another shop's.
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

OLD = b"\x89PNG\r\n\x1a\n" + b"the-old-picture" * 4
NEW = b"\x89PNG\r\n\x1a\n" + b"the-new-picture" * 4


class ReplaceCase(unittest.TestCase):
    """One temp AppData, one temp config folder, one store, no network."""

    store_key = "Store_ZZ_Replace"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = tempfile.mkdtemp()
        self.patches = [
            mock.patch.object(si, "_appdata_dir", return_value=self.dir),
            mock.patch.object(si, "_store_key", return_value=self.store_key),
            mock.patch.object(si, "_single_store_install", return_value=True),
            mock.patch.object(ppio, "_appdata_dir", return_value=self.dir),
            mock.patch.object(ppio, "_active_store_key", return_value=self.store_key),
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
        # No test may reach a real store server.
        self._no_server = mock.patch.object(si, "fetch_from_server", return_value="")
        self._no_server.start()

    def tearDown(self):
        self._no_server.stop()
        for p in reversed(self.patches):
            p.stop()
        si._reset_server_cache()
        ppio._empty_profile_cache = None

    # helpers -----------------------------------------------------------------
    def b64(self, raw):
        return base64.b64encode(raw).decode("ascii")

    def write(self, path, raw):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def loose_file(self, name="from_the_shops_pictures_folder.png", raw=OLD):
        return self.write(os.path.join(self.dir, name), raw)

    def banner_copies(self):
        return sorted(
            n for n in os.listdir(self.cfg) if n.startswith("home_banner_custom_")
        )

    def write_layout(self, **kw):
        cfg = lc.load_layout()
        cfg.update(kw)
        lc.save_layout(cfg)

    def empty_db(self):
        """The real pharmacy_profile table, in memory. No live store is opened."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        ppio.create_pharmacy_profile_table(conn)
        conn.commit()
        return conn


class TheBannerCopiesStopPilingUp(ReplaceCase):
    """config\\home_banner_custom_*.png -- the one that actually accumulates."""

    def pick(self, raw):
        from core import desktop_settings_service as svc

        res = svc.save_uploaded_home_banner("banner.png", self.b64(raw))
        self.assertTrue(res.get("ok"), res)
        return res

    def test_changing_the_banner_three_times_leaves_one_file(self):
        for raw in (OLD, NEW, OLD):
            last = self.pick(raw)
        self.assertEqual(
            len(self.banner_copies()),
            1,
            "every banner the shop ever picked is still in the config folder",
        )
        self.assertEqual(
            os.path.basename(last["path"]),
            self.banner_copies()[0],
            "the file that survived is not the one in use",
        )

    def test_the_banner_the_shop_can_see_is_the_new_one(self):
        self.pick(OLD)
        self.pick(NEW)
        with open(lc.get_home_banner_path(), "rb") as fh:
            self.assertEqual(fh.read(), NEW, "Home still shows the replaced banner")

    def test_the_old_banner_only_goes_once_the_new_one_is_saved(self):
        first = self.pick(OLD)["path"]
        # A picture the engine refuses is not a replacement: nothing may go.
        from core import desktop_settings_service as svc

        res = svc.save_uploaded_home_banner("notes.txt", self.b64(NEW))
        self.assertFalse(res.get("ok"))
        self.assertTrue(
            os.path.isfile(first),
            "a refused save took the shop's working banner with it",
        )

    def test_a_sweep_with_nothing_to_keep_deletes_nothing(self):
        kept = self.pick(OLD)["path"]
        self.assertEqual(lc.prune_custom_home_banners(""), [])
        self.assertEqual(
            lc.prune_custom_home_banners(os.path.join(self.cfg, "not_here.png")), []
        )
        self.assertTrue(os.path.isfile(kept))

    def test_the_shops_own_picture_is_never_deleted(self):
        """We copied it. The original belongs to the shop, wherever it keeps it."""
        own = self.write(os.path.join(self.cfg, "my_shop_signboard.png"), OLD)
        self.write_layout(home_banner_use_default=False, home_banner_path=own)
        self.pick(NEW)
        self.assertTrue(
            os.path.isfile(own),
            "replacing the banner deleted the shop's own picture file",
        )


class TheLogoTheBillPrintsIsTheNewOne(ReplaceCase):
    """The stale value that wins, rather than a file that piles up."""

    def profile_row(self, conn):
        return conn.execute(
            "SELECT COALESCE(logo_path,'') FROM pharmacy_profile ORDER BY id LIMIT 1"
        ).fetchone()[0]

    def db(self):
        conn = self.empty_db()
        conn.execute(
            "INSERT INTO pharmacy_profile (name, logo_path) VALUES ('ZZ TEST', '')"
        )
        conn.commit()
        return conn

    def test_uploading_a_new_logo_repoints_the_profile_at_it(self):
        from core import desktop_settings_service as svc

        conn = self.db()
        old = self.loose_file(raw=OLD)
        conn.execute("UPDATE pharmacy_profile SET logo_path=?", (old,))
        conn.commit()

        res = svc.save_uploaded_bill_logo("logo.png", self.b64(NEW), conn)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(
            self.profile_row(conn),
            res["path"],
            "the row still named the old logo, so the bill still printed it",
        )

    def test_the_bill_gets_the_new_logo_not_the_one_it_replaced(self):
        from core import bill_context
        from core import desktop_settings_service as svc

        conn = self.db()
        old = self.loose_file(raw=OLD)
        conn.execute("UPDATE pharmacy_profile SET logo_path=?", (old,))
        conn.commit()
        svc.save_uploaded_bill_logo("logo.png", self.b64(NEW), conn)

        hint = self.profile_row(conn)
        self.assertEqual(
            bill_context._logo_to_base64(hint),
            si.to_data_uri(".png", NEW),
            "the bill printed the logo the shop had already replaced",
        )

    def test_an_offline_shop_that_never_uploaded_still_prints_its_bill(self):
        """One PC, no server, a local file and nothing else. Replace and print."""
        from core import bill_context
        from core import desktop_settings_service as svc

        conn = self.db()
        own = self.loose_file("counter_pc_logo.png", raw=OLD)
        conn.execute("UPDATE pharmacy_profile SET logo_path=?", (own,))
        conn.commit()

        def explode(*_a, **_k):
            raise AssertionError("an offline shop reached for a live store")

        with mock.patch.object(si, "_is_online", return_value=False), \
                mock.patch("core.server_live.push_settings_kv", side_effect=explode):
            res = svc.save_uploaded_bill_logo("logo.png", self.b64(NEW), conn)
            self.assertTrue(res.get("ok"), res)
            printed = bill_context._logo_to_base64(self.profile_row(conn))
        self.assertEqual(printed, si.to_data_uri(".png", NEW))
        self.assertTrue(os.path.isfile(own), "deleted the shop's own file")

    def test_the_shops_own_file_survives_being_replaced(self):
        """It is in the shop's own folder. We copied it; we do not own it."""
        from core import desktop_settings_service as svc

        conn = self.db()
        own = self.loose_file("shop_letterhead.png", raw=OLD)
        conn.execute("UPDATE pharmacy_profile SET logo_path=?", (own,))
        conn.commit()
        svc.save_uploaded_bill_logo("logo.png", self.b64(NEW), conn)
        self.assertTrue(
            os.path.isfile(own), "we deleted a picture out of the shop's own folder"
        )

    def test_an_older_builds_appdata_copy_goes_with_it(self):
        """AppData\\pharmacy_logo.png is ours, and _legacy_path re-adopts it."""
        from core import desktop_settings_service as svc

        legacy = self.write(os.path.join(self.dir, "pharmacy_logo.png"), OLD)
        conn = self.db()
        conn.execute("UPDATE pharmacy_profile SET logo_path=?", (legacy,))
        conn.commit()
        svc.save_uploaded_bill_logo("logo.png", self.b64(NEW), conn)
        self.assertFalse(
            os.path.isfile(legacy),
            "the old copy stays and comes back the moment the cache is lost",
        )
        with open(si.resolve_path(si.BILL_LOGO, ""), "rb") as fh:
            self.assertEqual(fh.read(), NEW)

    def test_a_two_shop_pc_keeps_its_hands_off_the_unowned_copy(self):
        from core import desktop_settings_service as svc

        legacy = self.write(os.path.join(self.dir, "pharmacy_logo.png"), OLD)
        conn = self.db()
        with mock.patch.object(si, "_single_store_install", return_value=False):
            svc.save_uploaded_bill_logo("logo.png", self.b64(NEW), conn)
        self.assertTrue(
            os.path.isfile(legacy),
            "deleted a picture that could just as well be the other pharmacy's",
        )

    def test_saving_the_profile_with_a_new_logo_drops_the_old_copy(self):
        conn = self.db()
        first = si.save_image(si.BILL_LOGO, ".png", OLD, share=False)["path"]
        ppio._write_sidecar({"name": "ZZ TEST", "logo_path": first})
        picked = self.loose_file("newly_picked.png", raw=NEW)

        ppio.save_pharmacy_profile(conn, {"name": "ZZ TEST", "logo_path": picked})

        kept = si.local_path(si.BILL_LOGO)
        with open(kept, "rb") as fh:
            self.assertEqual(fh.read(), NEW)
        self.assertTrue(os.path.isfile(picked), "deleted the shop's own file")

    def test_a_jpg_replacing_a_png_does_not_leave_the_png_to_win(self):
        si.save_image(si.BILL_LOGO, ".png", OLD, share=False)
        si.save_image(si.BILL_LOGO, ".jpg", NEW, share=False)
        folder = si.images_dir()
        left = sorted(
            n for n in os.listdir(folder) if n.startswith(si.BILL_LOGO)
        )
        self.assertEqual(left, ["bill_logo.jpg"], "the cache kept both pictures")
        with open(si.resolve_path(si.BILL_LOGO, ""), "rb") as fh:
            self.assertEqual(fh.read(), NEW)

    def test_nothing_is_removed_when_the_new_picture_never_landed(self):
        """Delete-then-write is how a shop ends up with no logo at all."""
        old = si.save_image(si.BILL_LOGO, ".png", OLD, share=False)["path"]
        self.assertEqual(
            si.discard_previous(si.BILL_LOGO, old, keep=os.path.join(self.dir, "nope.png")),
            [],
        )
        self.assertTrue(os.path.isfile(old))


class TheOldOneLeavesTheStoreToo(ReplaceCase):
    """The settings row every other counter reads."""

    def pushes(self):
        calls = []
        return calls, mock.patch(
            "core.server_live.push_settings_kv",
            side_effect=lambda n, v: calls.append((n, v)) or True,
        )

    def online(self, calls_patch):
        return (
            mock.patch.object(si, "_is_online", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate"),
            calls_patch,
        )

    def test_a_replacement_overwrites_the_value_it_replaces(self):
        calls, patch = self.pushes()
        for p in self.online(patch):
            self.addCleanup(p.stop)
            p.start()
        si.save_image(si.BILL_LOGO, ".png", OLD)
        si.save_image(si.BILL_LOGO, ".png", NEW)
        self.assertEqual([n for n, _ in calls], ["bill_logo_image"] * 2)
        self.assertEqual(
            si.from_data_uri(calls[-1][1])[1],
            NEW,
            "the store still holds the picture the shop replaced",
        )

    def test_clearing_the_logo_clears_it_on_the_server(self):
        calls, patch = self.pushes()
        for p in self.online(patch):
            self.addCleanup(p.stop)
            p.start()
        si.save_image(si.BILL_LOGO, ".png", OLD)
        calls.clear()
        si.forget(si.BILL_LOGO)
        self.assertEqual(
            calls,
            [("bill_logo_image", "")],
            "the removed logo stayed on the server and came straight back",
        )

    def test_a_cleared_logo_does_not_come_back_from_the_cache(self):
        si.save_image(si.BILL_LOGO, ".png", OLD, share=False)
        self.write(os.path.join(self.dir, "pharmacy_logo.png"), OLD)
        si.forget(si.BILL_LOGO, clear_server=False)
        self.assertEqual(
            si.resolve_path(si.BILL_LOGO, ""),
            "",
            "Remove Logo removed nothing the next read could not undo",
        )

    def test_removing_the_logo_on_the_profile_screen_actually_removes_it(self):
        conn = self.empty_db()
        kept = si.save_image(si.BILL_LOGO, ".png", OLD, share=False)["path"]
        ppio._write_sidecar({"name": "ZZ TEST", "logo_path": kept})

        ppio.save_pharmacy_profile(conn, {"name": "ZZ TEST", "logo_path": ""})

        self.assertEqual(
            si.local_path(si.BILL_LOGO), "", "the logo the shop removed is still here"
        )

    def test_remove_works_when_the_only_copy_is_the_cache(self):
        """An upload leaves the bytes and no path. That is still a picture."""
        conn = self.empty_db()
        si.save_image(si.BILL_LOGO, ".png", OLD, share=False)
        ppio.save_pharmacy_profile(conn, {"name": "ZZ TEST", "logo_path": ""})
        self.assertEqual(si.local_path(si.BILL_LOGO), "")
        self.assertEqual(si.resolve_path(si.BILL_LOGO, ""), "")

    def test_a_profile_save_that_never_mentions_the_logo_keeps_it(self):
        """An absent field is not "the shop said no" -- that mistake printed
        blank letterheads for a fortnight when the SERVER sent logo_path ''."""
        conn = self.empty_db()
        kept = si.save_image(si.BILL_LOGO, ".png", OLD, share=False)["path"]
        ppio._write_sidecar({"name": "ZZ TEST", "logo_path": kept})

        ppio.save_pharmacy_profile(conn, {"name": "ZZ TEST", "phone": "900"})

        self.assertTrue(
            os.path.isfile(kept), "a save that said nothing about the logo deleted it"
        )

    def test_a_change_made_offline_is_sent_the_next_time_there_is_a_server(self):
        """Offline, the OLD picture is still the one the other counters print."""
        with mock.patch.object(si, "_is_online", return_value=False):
            si.save_image(si.BILL_LOGO, ".png", NEW)
        calls, patch = self.pushes()
        for p in self.online(patch):
            self.addCleanup(p.stop)
            p.start()
        si.save_image(si.HOME_BANNER, ".png", OLD)
        sent = dict(calls)
        self.assertIn(
            "bill_logo_image",
            sent,
            "the logo changed while the line was down never reached the store",
        )
        self.assertEqual(si.from_data_uri(sent["bill_logo_image"])[1], NEW)

    def test_a_push_that_fails_is_retried_and_the_local_copy_survives(self):
        def refuse(*_a, **_k):
            raise RuntimeError("no route to host")

        with mock.patch.object(si, "_is_online", return_value=True), \
                mock.patch("core.online_guard.ensure_can_mutate"), \
                mock.patch("core.server_live.push_settings_kv", side_effect=refuse):
            out = si.save_image(si.BILL_LOGO, ".png", NEW)
        self.assertTrue(os.path.isfile(out["path"]))
        self.assertFalse(out["shared"])

        calls, patch = self.pushes()
        for p in self.online(patch):
            self.addCleanup(p.stop)
            p.start()
        self.assertEqual(si.flush_pending(), [si.BILL_LOGO])
        self.assertEqual(si.from_data_uri(dict(calls)["bill_logo_image"])[1], NEW)

    def test_printing_a_bill_still_never_writes_to_the_store(self):
        from core import bill_context

        calls, patch = self.pushes()
        si.save_image(si.BILL_LOGO, ".png", OLD, share=False)
        with mock.patch.object(si, "_is_online", return_value=True), patch:
            bill_context._logo_to_base64("")
            si.resolve_path(si.BILL_LOGO, "")
            si.flush_pending()
        self.assertEqual(calls, [], "a read pushed to a live store")


class NoShopDeletesAnothersPicture(ReplaceCase):
    def test_two_store_keys_never_share_a_folder(self):
        """The sanitiser alone is not injective: "A B" and "A_B" both came out
        "A_B", the two shops shared one folder, and one shop replacing its logo
        removed the other's."""
        seen = {}
        for key in ("ZZ Medical", "ZZ_Medical", "ZZ-Medical", "ZZ/Medical", "ZZ.Medical"):
            with mock.patch.object(si, "_store_key", return_value=key):
                folder = si.images_dir()
            self.assertNotIn(
                folder,
                seen,
                f"{key!r} and {seen.get(folder)!r} land in the same folder",
            )
            seen[folder] = key

    def test_a_canonical_store_key_keeps_the_folder_it_already_has(self):
        """store_manager.display_name_key output must not move on upgrade."""
        for key in ("Store_ZZ_Medical", "Store_Matoshree-2", "Store_Unnamed"):
            with mock.patch.object(si, "_store_key", return_value=key):
                self.assertEqual(os.path.basename(si.images_dir()), key)

    def test_replacing_one_shops_logo_leaves_the_others_alone(self):
        other_key = "Store_ZZ_Other"
        with mock.patch.object(si, "_store_key", return_value=other_key):
            theirs = si.save_image(si.BILL_LOGO, ".png", OLD, share=False)["path"]
        si.save_image(si.BILL_LOGO, ".png", OLD, share=False)
        si.save_image(si.BILL_LOGO, ".png", NEW, share=False)
        self.assertTrue(os.path.isfile(theirs), "took the other pharmacy's logo")
        with open(theirs, "rb") as fh:
            self.assertEqual(fh.read(), OLD)

    def test_a_file_outside_our_folders_is_never_ours_to_delete(self):
        outside = self.loose_file("somewhere_else.png", raw=OLD)
        self.assertFalse(si.owned_copy(si.BILL_LOGO, outside))
        self.assertFalse(si.owned_copy(si.BILL_LOGO, ""))
        self.assertFalse(
            si.owned_copy(si.BILL_LOGO, os.path.join(self.dir, "never_existed.png"))
        )
        mine = si.save_image(si.BILL_LOGO, ".png", NEW, share=False)["path"]
        self.assertTrue(si.owned_copy(si.BILL_LOGO, mine))


if __name__ == "__main__":
    unittest.main()
