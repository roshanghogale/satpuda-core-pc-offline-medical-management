"""Regressions for the Gate A correctness fixes.

Each test here stands for a bug a pharmacy actually hit. They are cheap and
run without a server, so they are worth keeping ahead of every build.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PurchaseReturnStripsTests(unittest.TestCase):
    """A purchase return is entered in strips; stock is held in tablets."""

    def setUp(self):
        from core.desktop_returns_service import purchase_return_stock_units

        self.units = purchase_return_stock_units

    def test_strips_convert_to_tablets(self):
        self.assertEqual(self.units({"qty": 5, "type": "Tablet", "unit": "1x10"}), 50)

    def test_pack_size_read_from_the_medicine(self):
        got = self.units({"qty": 4, "type": "Tablet"}, medicine={"unit": "1x20"})
        self.assertEqual(got, 80)

    def test_loose_items_are_not_multiplied(self):
        self.assertEqual(self.units({"qty": 3, "type": "Bottle", "unit": "100ml"}), 3)

    def test_delete_gives_back_exactly_what_the_return_took(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE medicines (id INTEGER PRIMARY KEY, type TEXT, unit TEXT,"
            " tablets_per_stripe INTEGER, stock_qty REAL)"
        )
        conn.execute("INSERT INTO medicines VALUES (1,'Tablet','1x10',10,200)")
        taken = self.units({"qty": 5, "type": "Tablet", "unit": "1x10"})
        conn.execute("UPDATE medicines SET stock_qty = stock_qty - ?", (taken,))
        self.assertEqual(
            conn.execute("SELECT stock_qty FROM medicines").fetchone()[0], 150
        )
        given = self.units(
            {"qty": 5, "stock_units": taken},
            medicine={"type": "Tablet", "unit": "1x10", "tablets_per_stripe": 10},
        )
        conn.execute("UPDATE medicines SET stock_qty = stock_qty + ?", (given,))
        self.assertEqual(
            conn.execute("SELECT stock_qty FROM medicines").fetchone()[0], 200
        )

    def test_rows_saved_before_stock_units_existed_still_restore(self):
        got = self.units(
            {"qty": 3, "stock_units": None},
            medicine={"type": "Tablet", "unit": "1x10", "tablets_per_stripe": 10},
        )
        self.assertEqual(got, 30)


class OnlineReturnDeleteTests(unittest.TestCase):
    """The online reversal must move the same units the online save moved.

    A return document on the server carries only medicine_id, name, qty, rate
    and amount -- no type, no pack size. The delete path therefore has to look
    the medicine up before converting strips to tablets; without it a cancelled
    5-strip return of a 10s pack gave back 5 tablets against the 50 the save
    had taken, and 45 vanished from the shelf for good.
    """

    def _run_delete(self, collection, server):
        import core.online_catalog as oc
        import core.server_crud as sc

        deleted, pushed = [], []
        saved = (sc.get_doc, sc.delete_entity, sc.bump_meta, sc._device_id,
                 sc.upsert_docs, oc.medicine_by_id, oc.patch_docs)
        try:
            sc.get_doc = lambda col, i: dict(server.get(col, {}).get(int(i)) or {}) or None
            sc.delete_entity = lambda col, i: deleted.append((col, int(i)))
            sc.bump_meta = lambda d: {
                **d, "_meta": {"version": int((d.get("_meta") or {}).get("version", 0)) + 1}
            }
            sc._device_id = lambda: "test"

            def _upsert(col, docs):
                for d in docs:
                    server[col][int(d["id"])] = d
                    pushed.append(d)

            sc.upsert_docs = _upsert
            oc.medicine_by_id = lambda i: dict(server["medicines"].get(int(i)) or {}) or None
            oc.patch_docs = lambda col, docs: None

            from core.online_mutation_queue import _flush_return_delete

            rid = next(iter(server[collection]))
            _flush_return_delete(collection, {"id": rid}, rid)
        finally:
            (sc.get_doc, sc.delete_entity, sc.bump_meta, sc._device_id,
             sc.upsert_docs, oc.medicine_by_id, oc.patch_docs) = saved
        return deleted, pushed

    def _server(self, collection, qty):
        return {
            collection: {5: {"id": 5, "client_uuid": "cu",
                             # exactly the fields a server return document holds
                             "items": [{"medicine_id": 9, "name": "AMOXY", "qty": qty,
                                        "rate": 60.0, "amount": qty * 60.0}]}},
            "medicines": {9: {"id": 9, "local_id": 9, "name": "AMOXY", "type": "Tablet",
                              "unit": "1x10", "stock_qty": 50.0,
                              "_meta": {"version": 2}}},
        }

    def test_cancelling_a_purchase_return_gives_back_tablets_not_strips(self):
        server = self._server("purchase_returns", 5)
        self._run_delete("purchase_returns", server)
        # The save removed 5 strips = 50 tablets; the cancel must add 50 back.
        self.assertEqual(server["medicines"][9]["stock_qty"], 100.0)

    def test_the_ledger_entry_matches_the_stock_it_wrote(self):
        server = self._server("purchase_returns", 5)
        _, pushed = self._run_delete("purchase_returns", server)
        ops = [o for d in pushed for o in d.get("stock_ops", [])]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["qty_delta"], 50)
        self.assertEqual(ops[0]["op"], "purchase_return_delete")

    def test_a_sales_return_cancel_takes_stock_away(self):
        server = self._server("sales_returns", 12)
        self._run_delete("sales_returns", server)
        # Sales quantities are already in stock units -- no strip conversion.
        self.assertEqual(server["medicines"][9]["stock_qty"], 38.0)

    def test_the_return_is_deleted_after_the_stock_is_reversed(self):
        server = self._server("purchase_returns", 5)
        deleted, _ = self._run_delete("purchase_returns", server)
        self.assertEqual(deleted, [("purchase_returns", 5)])

    def test_the_medicine_version_is_bumped_or_the_server_skips_it(self):
        server = self._server("purchase_returns", 5)
        _, pushed = self._run_delete("purchase_returns", server)
        self.assertGreater(int(pushed[0]["_meta"]["version"]), 2)


class FinancialYearSerialTests(unittest.TestCase):
    """Editing a bill's date across 1 April must not disturb either year."""

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, bill_no TEXT,"
            " bill_date TEXT, deleted INTEGER DEFAULT 0, is_autosave INTEGER DEFAULT 0,"
            " fy_start_year INTEGER, fy_serial INTEGER)"
        )
        from core.fy_serial import encode_sales_bill_no

        for n in (148, 149, 150):
            conn.execute(
                "INSERT INTO sales (bill_no,bill_date,fy_start_year,fy_serial)"
                " VALUES (?,?,?,?)",
                (encode_sales_bill_no(n, 2025), "2026-03-21", 2025, n),
            )
        for n in (1, 2, 3):
            conn.execute(
                "INSERT INTO sales (bill_no,bill_date,fy_start_year,fy_serial)"
                " VALUES (?,?,?,?)",
                (encode_sales_bill_no(n, 2026), "2026-04-0%d" % n, 2026, n),
            )
        conn.commit()
        return conn

    def test_fy_is_read_from_the_bill_number(self):
        from core.fy_serial import fy_start_year_in_code

        self.assertEqual(fy_start_year_in_code("SCB150/FY2025-26"), 2025)
        self.assertEqual(fy_start_year_in_code("SCB3/FY2026-27"), 2026)
        self.assertIsNone(fy_start_year_in_code("SCB9"))

    def test_moving_a_bill_into_the_new_year_does_not_jump_the_series(self):
        from core.fy_serial import (
            allocate_sales_bill_no,
            display_sales_bill_no,
            patch_sale_fy_fields,
            resync_sale_fy_number,
        )

        conn = self._conn()
        cur = conn.cursor()
        sale_id = cur.execute(
            "SELECT id FROM sales WHERE bill_no LIKE 'SCB150%'"
        ).fetchone()[0]
        cur.execute("UPDATE sales SET bill_date=? WHERE id=?", ("2026-04-10", sale_id))
        new_no = resync_sale_fy_number(
            cur, conn, sale_id, "SCB150/FY2025-26", "2026-04-10"
        )
        patch_sale_fy_fields(cur, sale_id, new_no, "2026-04-10")
        conn.commit()

        self.assertEqual(display_sales_bill_no(new_no), "SCB4")
        nxt = allocate_sales_bill_no(conn, "2026-04-11")
        self.assertEqual(display_sales_bill_no(nxt), "SCB5")

    def test_a_date_change_inside_one_year_keeps_the_number(self):
        from core.fy_serial import resync_sale_fy_number

        conn = self._conn()
        cur = conn.cursor()
        sale_id = cur.execute(
            "SELECT id FROM sales WHERE bill_no LIKE 'SCB2/%'"
        ).fetchone()[0]
        same = resync_sale_fy_number(
            cur, conn, sale_id, "SCB2/FY2026-27", "2026-05-15"
        )
        self.assertEqual(same, "SCB2/FY2026-27")

    def test_untagged_old_numbers_are_left_alone(self):
        from core.fy_serial import resync_sale_fy_number

        conn = self._conn()
        cur = conn.cursor()
        self.assertEqual(
            resync_sale_fy_number(cur, conn, 1, "SCB77", "2026-04-10"), "SCB77"
        )


class CustomerReceiptEditTests(unittest.TestCase):
    """Re-saving a receipt must not take the money off the due a second time."""

    @staticmethod
    def _balance(old_due, old_credit, amount, editing_id, prev_amt):
        applied = round(amount - prev_amt, 2) if editing_id > 0 else amount
        net = round(old_due - old_credit - applied, 2)
        return max(0.0, net), max(0.0, -net)

    def test_a_new_receipt_comes_off_the_due(self):
        self.assertEqual(self._balance(1000, 0, 500, 0, 0), (500.0, 0.0))

    def test_resaving_the_same_receipt_changes_nothing(self):
        self.assertEqual(self._balance(500, 0, 500, 7, 500), (500.0, 0.0))

    def test_raising_a_receipt_applies_only_the_difference(self):
        self.assertEqual(self._balance(500, 0, 600, 7, 500), (400.0, 0.0))

    def test_lowering_a_receipt_gives_the_difference_back(self):
        self.assertEqual(self._balance(500, 0, 300, 7, 500), (700.0, 0.0))

    def test_overpaying_becomes_credit(self):
        self.assertEqual(self._balance(500, 0, 1200, 7, 500), (0.0, 200.0))


class ActivationFingerprintTests(unittest.TestCase):
    """Ordinary changes to a PC must not look like a different PC."""

    BASE = {
        "cpu": "BFEBFBFF000906EA",
        "board": "MB-77281",
        "disk": "WD-A1|WD-B2",
        "mac": "112394521950",
    }

    def setUp(self):
        import core.license_manager as lm

        self.lm = lm
        self._real_parts = lm._hardware_parts
        self._real_hash = lm._get_hardware_hash
        self.current = dict(self.BASE)
        lm._hardware_parts = lambda: dict(self.current)
        lm._get_hardware_hash = lambda **k: lm._hash_parts(self.current)
        self.record = {
            "hw": lm._hash_parts(self.BASE),
            "hw_parts": dict(self.BASE),
            "date": "2026-01-01",
        }

    def tearDown(self):
        self.lm._hardware_parts = self._real_parts
        self.lm._get_hardware_hash = self._real_hash

    def test_unchanged_machine(self):
        self.assertTrue(self.lm.hardware_matches(self.record))

    def test_a_new_network_adapter_is_tolerated(self):
        self.current["mac"] = "999999999"
        self.assertTrue(self.lm.hardware_matches(self.record))

    def test_one_unreadable_component_is_tolerated(self):
        self.current["board"] = ""
        self.assertTrue(self.lm.hardware_matches(self.record))

    def test_an_extra_disk_is_tolerated(self):
        self.current["disk"] = "WD-A1|WD-B2|NEW-SSD"
        self.assertTrue(self.lm.hardware_matches(self.record))

    def test_nothing_readable_does_not_lock_the_shop_out(self):
        self.current = {"cpu": "", "board": "", "disk": "", "mac": ""}
        self.assertTrue(self.lm.hardware_matches(self.record))

    def test_a_different_machine_is_still_rejected(self):
        self.current = {
            "cpu": "OTHER", "board": "OTHER", "disk": "OTHER", "mac": "1",
        }
        self.assertFalse(self.lm.hardware_matches(self.record))

    def test_two_changed_components_are_rejected(self):
        self.current["cpu"] = "OTHER"
        self.current["board"] = "OTHER"
        self.assertFalse(self.lm.hardware_matches(self.record))

    def test_a_record_from_an_older_build_still_matches(self):
        legacy = {"hw": self.lm._hash_parts(self.BASE), "date": "2025-06-01"}
        self.assertTrue(self.lm.hardware_matches(legacy))

    def test_an_invented_mac_is_not_part_of_the_identity(self):
        import uuid

        real = uuid.getnode
        try:
            uuid.getnode = lambda: 0x0323456789AB      # multicast bit set
            self.assertEqual(self.lm._stable_mac(), "")
            uuid.getnode = lambda: 0x001A2B3C4D5E      # burnt-in address
            self.assertEqual(self.lm._stable_mac(), "112394521950")
        finally:
            uuid.getnode = real


class SettingsAcrossModeSwitchTests(unittest.TestCase):
    """Online runs on :memory:; the shop's settings must still survive."""

    def setUp(self):
        import core.settings_mirror as sm

        self.sm = sm
        self.tmp = tempfile.mkdtemp()
        self._real_path = sm._mirror_path
        sm._mirror_path = lambda: os.path.join(self.tmp, "mirror.json")

    def tearDown(self):
        self.sm._mirror_path = self._real_path

    def _offline_store(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)")
        for n, v in [
            ("low_stock_threshold", "25"),
            ("near_expiry_days", "120"),
            ("sync_watermark", "99999"),
        ]:
            conn.execute("INSERT INTO settings VALUES (?,?)", (n, v))
        conn.commit()
        return conn

    def test_thresholds_survive_the_switch_to_online(self):
        self.sm.capture_from(self._offline_store())
        mem = sqlite3.connect(":memory:")
        self.sm.apply_to(mem)
        got = dict(mem.execute("SELECT name, value FROM settings").fetchall())
        self.assertEqual(got.get("low_stock_threshold"), "25")
        self.assertEqual(got.get("near_expiry_days"), "120")

    def test_sync_bookkeeping_does_not_travel_between_modes(self):
        self.sm.capture_from(self._offline_store())
        mem = sqlite3.connect(":memory:")
        self.sm.apply_to(mem)
        got = dict(mem.execute("SELECT name, value FROM settings").fetchall())
        self.assertNotIn("sync_watermark", got)

    def test_a_change_made_online_survives_a_restart(self):
        self.sm.remember("low_stock_threshold", "7")
        mem = sqlite3.connect(":memory:")
        self.sm.apply_to(mem)
        got = dict(mem.execute("SELECT name, value FROM settings").fetchall())
        self.assertEqual(got.get("low_stock_threshold"), "7")

    def test_an_offline_store_keeps_its_own_value(self):
        conn = self._offline_store()
        self.sm.capture_from(conn)
        self.sm.remember("low_stock_threshold", "7")
        self.sm.apply_to(conn)
        got = dict(conn.execute("SELECT name, value FROM settings").fetchall())
        self.assertEqual(got.get("low_stock_threshold"), "25")

    def test_a_missing_mirror_is_harmless(self):
        self.assertEqual(self.sm.apply_to(sqlite3.connect(":memory:")), 0)


class PharmacyProfileTests(unittest.TestCase):
    """Replacing the build folder must not blank the shop header on bills."""

    REAL = {
        "name": "Shivkrupa Medical",
        "address": "Shirpur",
        "gstin": "27ABCDE1234F1Z5",
        "dl_number": "MH-DL-991",
        "phone": "9876543210",
        "email": "",
        "gst_enabled": True,
        "logo_path": "",
        "fssai_number": "",
        "show_fssai_on_bill": False,
    }

    def setUp(self):
        import core.pharmacy_profile_io as ppio

        self.ppio = ppio
        self.tmp = tempfile.mkdtemp()
        self._saved = (
            ppio._appdata_dir, ppio._is_online, ppio._load_local_fallback,
            ppio._keep_logo_in_appdata, ppio._active_store_key,
            ppio.fetch_profile_from_server,
        )
        ppio._appdata_dir = lambda: self.tmp
        ppio._is_online = lambda: True
        ppio._load_local_fallback = lambda: None
        ppio._keep_logo_in_appdata = lambda p: p
        self.store = "shivkrupa"
        ppio._active_store_key = lambda: self.store
        ppio.remember_local_profile(self.REAL)

    def tearDown(self):
        p = self.ppio
        (p._appdata_dir, p._is_online, p._load_local_fallback,
         p._keep_logo_in_appdata, p._active_store_key,
         p.fetch_profile_from_server) = self._saved

    def _load(self):
        self.ppio._empty_profile_cache = None
        return self.ppio.load_pharmacy_profile(None)

    def test_a_blank_server_answer_does_not_erase_the_header(self):
        self.ppio.fetch_profile_from_server = lambda: self.ppio._normalize({})
        self.assertEqual(self._load()["name"], "Shivkrupa Medical")

    def test_an_unreachable_server_does_not_erase_the_header(self):
        self.ppio.fetch_profile_from_server = lambda: None
        self.assertEqual(self._load()["gstin"], "27ABCDE1234F1Z5")

    def test_the_server_wins_when_it_has_a_profile(self):
        self.ppio.fetch_profile_from_server = lambda: self.ppio._normalize(
            dict(self.REAL, name="Shivkrupa Medical & General")
        )
        self.assertEqual(self._load()["name"], "Shivkrupa Medical & General")

    def test_another_store_does_not_inherit_this_shops_name(self):
        self.ppio.fetch_profile_from_server = lambda: self.ppio._normalize({})
        self.store = "roshan"
        self.assertEqual(self._load()["name"], "")

    def test_two_stores_keep_separate_profiles(self):
        self.ppio.fetch_profile_from_server = lambda: self.ppio._normalize({})
        self.store = "roshan"
        self.ppio.remember_local_profile(dict(self.REAL, name="Roshan Agencies"))
        self.assertEqual(self._load()["name"], "Roshan Agencies")
        self.store = "shivkrupa"
        self.assertEqual(self._load()["name"], "Shivkrupa Medical")


if __name__ == "__main__":
    unittest.main(verbosity=2)
