"""The shop's own bank details on the Pharmacy Profile (owner, 18 Oct 2026).

Every store -- retail and wholesale -- keeps Bank / A/c No / IFSC / Branch in its own
profile. Locally they are pharmacy_profile columns. The store server's profile has no
columns for them and drops keys it does not know, so each value is also kept as a
store settings row (profile_bank_*), which the server stores as it is and hands to
the store's other devices. Reading takes the profile column first, the row second.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pharmacy_profile_io as ppio  # noqa: E402

PROFILE = {
    "name": "ZZ TEST MEDICAL", "address": "Main Road", "phone": "900",
    "bank_name": "TEST BANK", "bank_account_no": "000111222",
    "bank_ifsc": " test0000001 ", "bank_branch": "Main  Branch",
}


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [
            mock.patch("core.pharmacy_profile_io._appdata_dir", return_value=self.dir),
            mock.patch("core.license_manager._appdata_dir", return_value=self.dir),
            mock.patch("core.pharmacy_profile_io._active_store_key", return_value="ZZ_Test"),
            mock.patch("core.pharmacy_profile_io._is_online", return_value=False),
        ]
        for p in self.patches:
            p.start()
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)"
        )

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.conn.close()

    def kv(self, name):
        row = self.conn.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return row[0] if row else None


class SaveAndLoad(_Base):
    def test_the_four_fields_round_trip(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        out = ppio.load_pharmacy_profile(self.conn)
        self.assertEqual(out["bank_name"], "TEST BANK")
        self.assertEqual(out["bank_account_no"], "000111222")
        self.assertEqual(out["bank_ifsc"], "TEST0000001", "IFSC is kept trimmed and in capitals")
        self.assertEqual(out["bank_branch"], "Main Branch")

    def test_they_are_profile_columns(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        row = self.conn.execute(
            "SELECT bank_name, bank_account_no, bank_ifsc, bank_branch FROM pharmacy_profile"
        ).fetchone()
        self.assertEqual(row, ("TEST BANK", "000111222", "TEST0000001", "Main Branch"))

    def test_they_are_mirrored_to_the_settings_rows(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        self.assertEqual(self.kv("profile_bank_name"), "TEST BANK")
        self.assertEqual(self.kv("profile_bank_account_no"), "000111222")
        self.assertEqual(self.kv("profile_bank_ifsc"), "TEST0000001")
        self.assertEqual(self.kv("profile_bank_branch"), "Main Branch")

    def test_no_bank_details_writes_no_rows(self):
        ppio.save_pharmacy_profile(self.conn, {"name": "ZZ TEST MEDICAL"})
        self.assertIsNone(self.kv("profile_bank_name"))

    def test_clearing_a_field_clears_its_row(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE, bank_branch=""))
        self.assertEqual(self.kv("profile_bank_branch"), "")
        self.assertEqual(ppio.load_pharmacy_profile(self.conn)["bank_branch"], "")

    def test_a_save_that_does_not_mention_them_keeps_them(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        ppio.save_pharmacy_profile(self.conn, {"name": "ZZ TEST MEDICAL", "phone": "901"})
        out = ppio.load_pharmacy_profile(self.conn)
        self.assertEqual(out["phone"], "901")
        self.assertEqual(out["bank_account_no"], "000111222",
                         "the classic window's save wiped the bank details")

    def test_the_settings_row_fills_a_blank_column(self):
        # Another device saved them: the row arrived, this profile row has none.
        ppio.save_pharmacy_profile(self.conn, {"name": "ZZ TEST MEDICAL"})
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bank_ifsc', 'KV0000002')")
        self.assertEqual(ppio.load_pharmacy_profile(self.conn)["bank_ifsc"], "KV0000002")

    def test_the_column_is_read_first(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        self.conn.execute("UPDATE settings SET value='OTHER' WHERE name='profile_bank_name'")
        self.assertEqual(ppio.load_pharmacy_profile(self.conn)["bank_name"], "TEST BANK")

    def test_a_pulled_row_moves_the_column_too(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        from core.server_live import apply_server_doc

        apply_server_doc(self.conn, "settings", {"name": "profile_bank_account_no", "value": "999888"})
        self.assertEqual(self.kv("profile_bank_account_no"), "999888")
        self.assertEqual(ppio.load_pharmacy_profile(self.conn)["bank_account_no"], "999888")

    def test_a_pulled_profile_does_not_blank_them(self):
        # The server's profile never carries the bank columns.
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        from core.server_live import apply_server_doc

        apply_server_doc(self.conn, "pharmacy_profile", {"name": "ZZ TEST MEDICAL", "phone": "902"})
        out = ppio.load_pharmacy_profile(self.conn)
        self.assertEqual(out["phone"], "902")
        self.assertEqual(out["bank_name"], "TEST BANK")

    def test_an_old_store_file_gets_the_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE pharmacy_profile (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO pharmacy_profile (name) VALUES ('OLD')")
        from core.db_setup import ensure_recent_columns

        ensure_recent_columns(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pharmacy_profile)")}
        self.assertTrue({"bank_name", "bank_account_no", "bank_ifsc", "bank_branch"} <= cols)
        conn.close()


class Online(_Base):
    def test_online_save_pushes_the_rows_and_reads_them_back(self):
        server: dict = {}

        def push_kv(name, value):
            server[name] = value
            return True

        rows = lambda names=(): [{"name": n, "value": v} for n, v in server.items()]  # noqa: E731
        with mock.patch("core.pharmacy_profile_io._is_online", return_value=True), \
                mock.patch("core.online_guard.ensure_can_mutate"), \
                mock.patch("core.server_crud.push_pharmacy_profile") as push_profile, \
                mock.patch("core.server_live.push_settings_kv", side_effect=push_kv), \
                mock.patch("core.store_images._kv_rows_cached", side_effect=rows):
            ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
            sent = push_profile.call_args[0][0]
            self.assertEqual(sent["bank_name"], "TEST BANK")
            self.assertEqual(server["profile_bank_account_no"], "000111222")
            self.assertEqual(server["profile_bank_ifsc"], "TEST0000001")
            # The server's profile answers without the bank fields, as it does today.
            ppio.forget_cached_profile()
            with mock.patch("core.pharmacy_profile_io.fetch_profile_from_server",
                            return_value=ppio._normalize({"name": "ZZ TEST MEDICAL"})):
                out = ppio.load_pharmacy_profile(sqlite3.connect(":memory:"))
        self.assertEqual(out["bank_account_no"], "000111222")
        self.assertEqual(out["bank_branch"], "Main Branch")


class ShopProfile(_Base):
    def _shop(self):
        from core import wholesale_sale as ws

        return ws.shop_profile(self.conn)

    def test_the_profile_columns_print(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        self.assertEqual(self._shop()["bank"], [
            ("Bank", "TEST BANK"), ("A/c No", "000111222"),
            ("IFSC", "TEST0000001"), ("Branch", "Main Branch"),
        ])

    def test_the_settings_rows_print_when_the_columns_are_empty(self):
        ppio.save_pharmacy_profile(self.conn, {"name": "ZZ TEST MEDICAL"})
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bank_name', 'KV BANK')")
        self.assertEqual(self._shop()["bank"], [("Bank", "KV BANK")])

    def test_the_free_text_setting_still_works(self):
        ppio.save_pharmacy_profile(self.conn, {"name": "ZZ TEST MEDICAL"})
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('invoice_bank_details', "
                          "'Bank: FREE BANK\nIFSC: FREE0000001')")
        self.assertEqual(self._shop()["bank"], [("Bank", "FREE BANK"), ("IFSC", "FREE0000001")])

    def test_show_on_bill_off_hides_them(self):
        ppio.save_pharmacy_profile(self.conn, dict(PROFILE))
        with mock.patch("core.bill_config.load_bill_print_settings",
                        return_value={"show_store_bank": False}):
            self.assertEqual(self._shop()["bank"], [])


class SyncWiring(unittest.TestCase):
    def test_the_rows_are_shop_wide(self):
        from core.offline_first import schema
        from core import store_kv_carry

        self.assertIn("profile_bank_", schema.SHOP_WIDE_SETTING_PREFIXES)
        self.assertIn("profile_bank_", store_kv_carry.PREFIXES)
        for k, name in ppio.BANK_KV.items():
            self.assertTrue(name.startswith("profile_bank_"), name)

    def test_a_settings_row_marks_an_event(self):
        from core.offline_first import schema

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bank_ifsc', 'X1')")
        conn.execute("INSERT INTO settings (name, value) VALUES ('some_pc_marker', '1')")
        dirty = conn.execute("SELECT collection, local_id FROM of_dirty").fetchall()
        conn.close()
        self.assertEqual(len([d for d in dirty if d[0] == "settings"]), 1, dirty)

    def test_show_store_bank_is_a_bill_setting(self):
        from core.bill_config import BILL_PRINT_FIELD_OPTIONS, DEFAULT_BILL_PRINT_SETTINGS

        self.assertTrue(DEFAULT_BILL_PRINT_SETTINGS["show_store_bank"])
        keys = [k for _g, items in BILL_PRINT_FIELD_OPTIONS for k, _l in items]
        self.assertIn("show_store_bank", keys)


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts):
    with open(os.path.join(_REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


class TheProfileWindow(unittest.TestCase):
    """One window: each profile item with its own bill choice; no separate Bill Logo."""

    def test_bill_logo_is_not_a_separate_nav_section(self):
        cfg = _src("desktop", "src", "pages", "settings", "settingsConfig.ts")
        self.assertNotIn("id: 'bill_logo'", cfg)
        panel = _src("desktop", "src", "pages", "settings", "PharmacyPanels.tsx")
        self.assertIn("sectionId === 'profile' || sectionId === 'bill_logo'", panel,
                      "an old Bill Logo link must open the profile window")
        self.assertEqual(panel.count("sectionId === 'bill_logo'"), 1)

    def test_the_logo_place_is_chosen_in_the_profile(self):
        panel = _src("desktop", "src", "pages", "settings", "PharmacyPanels.tsx")
        for text in ("Where on the bill", "show_logo_top_right", "show_logo_center_watermark",
                     "logo_watermark_opacity", "logo_top_right_margin_top"):
            self.assertIn(text, panel)

    def test_every_store_item_has_its_show_on_bill_tick(self):
        panel = _src("desktop", "src", "pages", "settings", "PharmacyPanels.tsx")
        for key in ("show_store_name", "show_store_address", "show_store_phone",
                    "show_store_email", "show_store_gstin", "show_store_dl",
                    "show_store_fssai", "show_store_bank", "print_signature_image"):
            self.assertIn(f"setBillKey('{key}'", panel, key)
            self.assertIn(f"'{key}',", panel.split("PROFILE_WINDOW_BILL_KEYS = new Set([", 1)[1]
                          .split("])", 1)[0], f"{key} would show in Bill Fields too")

    def test_bank_fields_are_on_the_profile(self):
        panel = _src("desktop", "src", "pages", "settings", "PharmacyPanels.tsx")
        for key in ("bank_name", "bank_account_no", "bank_ifsc", "bank_branch"):
            self.assertIn(f"text('{key}')", panel)
        self.assertIn("upiId", panel, "the UPI ID is linked, not typed a second time")


if __name__ == "__main__":
    unittest.main()
