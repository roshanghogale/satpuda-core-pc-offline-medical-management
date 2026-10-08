"""Offline-first on the PC, the local half (no server): nothing a till does is missed, ids and
numbers never clash, and the switch the worker flips for pulled changes is never seen by the
till's own connection.

Design: "Satpuda Core: Offline-First Server Design" (7 Oct 2026). Everything runs on a temp
store file; no server is contacted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest

from core import db_setup
from core.offline_first import ids, numbers, outbox, pull, schema, worker
from core.offline_first.schema import meta_get, meta_set

DEVICE = 3
BASE = DEVICE * 1_000_000_000


def make_store() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="of_test_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    meta_set(conn, "device_no", DEVICE)
    meta_set(conn, "id_base", BASE)
    meta_set(conn, "id_max", BASE + 999_999_999)
    meta_set(conn, "install_id", "pc-test-install")
    conn.commit()
    conn.close()
    return path


def till(path: str) -> sqlite3.Connection:
    """The engine's connection in offline-first mode."""
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


class Base(unittest.TestCase):
    def setUp(self):
        self.path = make_store()
        self.conn = till(self.path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.conn.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def add_medicine(self, name="DOLO 650", stock=100):
        cur = self.conn.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) VALUES (?,?,?,?,?,?,?)",
            (name, "Tablet", stock, "10", 30, 20, "B1"),
        )
        self.conn.commit()
        return cur.lastrowid

    def dirty(self):
        return {(c, i, o) for c, i, o in self.conn.execute("SELECT collection, local_id, op FROM of_dirty")}


class IdsAndCapture(Base):
    def test_a_new_record_gets_an_id_in_this_devices_range(self):
        mid = self.add_medicine()
        self.assertTrue(BASE < mid < BASE + 1_000_000_000, mid)
        cid = self.conn.execute("INSERT INTO customers (name, phone) VALUES (?, ?)", ("RAM", "1")).lastrowid
        self.assertTrue(BASE < cid < BASE + 1_000_000_000, cid)

    def test_another_devices_bigger_ids_do_not_pull_this_device_into_their_range(self):
        # A record pulled from device 7 (id 7e9 + 5) is in the copy; the next id is still ours.
        self.conn.execute("INSERT INTO customers (id, name) VALUES (?, ?)", (7_000_000_005, "OTHER"))
        cid = self.conn.execute("INSERT INTO customers (name) VALUES (?)", ("MINE",)).lastrowid
        self.assertTrue(BASE < cid < BASE + 1_000_000_000, cid)

    def test_every_change_is_noted_and_lines_note_their_bill(self):
        mid = self.add_medicine()
        sid = self.conn.execute(
            "INSERT INTO sales (bill_no, bill_date, total_amount) VALUES ('SCB1/FY2026-27','2026-10-07',60)"
        ).lastrowid
        self.conn.execute(
            "INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) VALUES (?,?,2,30,60)", (sid, mid),
        )
        self.conn.execute("UPDATE customers SET name=name")  # no customers: nothing
        self.conn.commit()
        self.assertIn(("medicines", mid, "upsert"), self.dirty())
        self.assertIn(("sales", sid, "upsert"), self.dirty())
        self.conn.execute("DELETE FROM sales_items WHERE sale_id=?", (sid,))
        self.conn.execute("DELETE FROM sales WHERE id=?", (sid,))
        self.conn.commit()
        self.assertIn(("sales", sid, "delete"), self.dirty())

    def test_stock_moves_are_journaled_whoever_moves_them(self):
        mid = self.add_medicine(stock=100)
        self.conn.execute("UPDATE medicines SET stock_qty = stock_qty - 2 WHERE id=?", (mid,))
        self.conn.execute("UPDATE medicines SET stock_qty = stock_qty + 10 WHERE id=?", (mid,))
        self.conn.commit()
        deltas = [d for (d,) in self.conn.execute(
            "SELECT delta FROM of_stock_journal WHERE medicine_id=? ORDER BY id", (mid,))]
        self.assertEqual(deltas, [100, -2, 10])     # opening stock, sale, purchase

    def test_a_stock_only_change_does_not_resend_the_medicine(self):
        mid = self.add_medicine()
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.execute("UPDATE medicines SET stock_qty = stock_qty - 1 WHERE id=?", (mid,))
        self.conn.commit()
        self.assertEqual(self.dirty(), set())
        self.conn.execute("UPDATE medicines SET mrp = 35 WHERE id=?", (mid,))
        self.conn.commit()
        self.assertIn(("medicines", mid, "upsert"), self.dirty())


class SettingsThatBelongToOnePC(Base):
    def test_only_shop_wide_settings_are_sent(self):
        up = ("INSERT INTO settings (name, value) VALUES (?, ?) "
              "ON CONFLICT(name) DO UPDATE SET value=excluded.value")
        self.conn.execute("DELETE FROM of_dirty")
        for name, value in (("fy_serial_migrated_v1", "2"), ("regular_meds:12", "[]"),
                            ("billing_layout_prefs", "{}")):
            self.conn.execute(up, (name, value))
        self.conn.commit()
        sent = {i for c, i, _ in self.dirty() if c == "settings"}
        names = {n for (n,) in self.conn.execute(
            f"SELECT name FROM settings WHERE id IN ({','.join('?' * len(sent))})", tuple(sent))}
        self.assertEqual(names, {"regular_meds:12", "billing_layout_prefs"})


class PausedCaptureIsPrivate(Base):
    def test_the_till_is_never_uncaptured_while_the_worker_writes(self):
        mid = self.add_medicine(stock=50)
        self.conn.execute("DELETE FROM of_stock_journal")
        self.conn.commit()
        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        started, release = threading.Event(), threading.Event()
        result = {}

        def worker_batch():
            with worker.write_batch(w, capture_paused=True):
                w.execute("UPDATE medicines SET stock_qty = 999 WHERE id=?", (mid,))   # a pulled figure
                started.set()
                release.wait(5)

        def till_sale():
            started.wait(5)
            # The till's write has to wait for the worker's batch; it never runs uncaptured.
            self.conn.execute("UPDATE medicines SET stock_qty = stock_qty - 4 WHERE id=?", (mid,))
            self.conn.commit()
            result["done"] = True

        t1 = threading.Thread(target=worker_batch)
        t2 = threading.Thread(target=till_sale)
        t1.start(); t2.start()
        started.wait(5)
        cap = sqlite3.connect(self.path).execute("SELECT v FROM of_flags WHERE k='capture'").fetchone()[0]
        self.assertEqual(cap, "1", "another connection saw capture switched off")
        release.set()
        t1.join(10); t2.join(10)
        self.assertTrue(result.get("done"))
        deltas = [d for (d,) in self.conn.execute("SELECT delta FROM of_stock_journal ORDER BY id")]
        self.assertEqual(deltas, [-4], "the pulled figure was journaled, or the sale was not")
        self.assertEqual(self.conn.execute("SELECT stock_qty FROM medicines WHERE id=?", (mid,)).fetchone()[0], 995)


class EventsAndNumbers(Base):
    def test_events_are_numbered_masters_first_then_documents_then_stock(self):
        mid = self.add_medicine(stock=10)
        cid = self.conn.execute("INSERT INTO customers (name) VALUES ('RAM')").lastrowid
        sid = self.conn.execute(
            "INSERT INTO sales (bill_no, bill_date, customer_id, total_amount) VALUES ('SCB7/FY2026-27','2026-10-07',?,60)",
            (cid,),
        ).lastrowid
        self.conn.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) VALUES (?,?,2,30,60)", (sid, mid))
        self.conn.execute("UPDATE medicines SET stock_qty = stock_qty - 2 WHERE id=?", (mid,))
        self.conn.commit()
        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        with worker.write_batch(w):
            outbox.build_events(w, "pc-test-install")
        evs = outbox.pending_events(w)
        self.assertEqual([e["seq"] for e in evs], list(range(1, len(evs) + 1)))
        kinds = [e["collection"] for e in evs]
        self.assertLess(kinds.index("customers"), kinds.index("sales"))
        self.assertLess(kinds.index("medicines"), kinds.index("sales"))
        self.assertEqual(kinds[-1], "stock_operations")
        stock = evs[-1]["stock_ops"]
        self.assertEqual(sum(o["qty_delta"] for o in stock), 8)          # 10 opening - 2 sold
        sale = next(e for e in evs if e["collection"] == "sales")
        self.assertEqual(sale["doc"]["id"], sid)
        self.assertEqual(len(sale["doc"]["items"]), 1)
        self.assertEqual(w.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)

    def test_the_server_answer_is_recorded_and_unsent_stock_stays_pending(self):
        mid = self.add_medicine(stock=10)
        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        with worker.write_batch(w):
            outbox.build_events(w, "pc-test-install")
        evs = outbox.pending_events(w)
        self.assertEqual(outbox.pending_stock_by_medicine(w), {mid: 10})
        with worker.write_batch(w):
            outbox.apply_push_answer(w, {"last_seq": evs[-1]["seq"], "results": [
                {"seq": e["seq"], "outcome": "applied"} for e in evs]})
        self.assertEqual(outbox.pending_stock_by_medicine(w), {})
        self.assertEqual(int(meta_get(w, "server_last_seq")), evs[-1]["seq"])

    def test_a_restored_pc_moves_its_unsent_events_after_the_servers_numbers(self):
        self.add_medicine(stock=5)
        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        with worker.write_batch(w):
            outbox.build_events(w, "pc-test-install")
        before = [(e["seq"], e["event_uuid"]) for e in outbox.pending_events(w)]
        with worker.write_batch(w):
            outbox.renumber_pending(w, 41)
        after = [(e["seq"], e["event_uuid"]) for e in outbox.pending_events(w)]
        self.assertEqual([u for _, u in after], [u for _, u in before])
        self.assertEqual([s for s, _ in after], list(range(41, 41 + len(before))))
        js = {s for (s,) in w.execute("SELECT DISTINCT event_seq FROM of_stock_journal")}
        self.assertTrue(js <= {s for s, _ in after})

    def test_numbers_come_from_blocks_and_documents_carry_the_device(self):
        numbers.add_block(self.conn, "sales", 2026, 101, 103)
        got = [numbers.take_serial(self.conn, "sales", 2026) for _ in range(4)]
        self.assertEqual(got, [101, 102, 103, None])
        self.assertEqual(numbers.doc_number(self.conn, "SR"), "SR3-1")
        self.assertEqual(numbers.doc_number(self.conn, "SR"), "SR3-2")


class PullAndStock(Base):
    def test_local_stock_is_the_servers_plus_this_pcs_unsent_moves(self):
        mid = self.add_medicine(stock=100)              # journal: +100, not sent
        self.conn.execute("UPDATE medicines SET stock_qty = stock_qty - 5 WHERE id=?", (mid,))
        self.conn.commit()                              # journal: -5, not sent
        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        # The server knows the opening 100 (sent) and another device sold 7: 93.
        with worker.write_batch(w):
            outbox.build_events(w, "pc-test-install")
        evs = outbox.pending_events(w)
        # Only the masters were sent; the stock event (with -5 and +100) is still pending here.
        with worker.write_batch(w, capture_paused=True):
            pull.set_stock_from_server(w, [[mid, 93, 0]])
        # pending = +100 -5 = 95 ... the server has none of it yet => 93 + 95
        self.assertEqual(w.execute("SELECT stock_qty FROM medicines WHERE id=?", (mid,)).fetchone()[0], 188)
        with worker.write_batch(w):
            outbox.apply_push_answer(w, {"last_seq": evs[-1]["seq"], "results": [
                {"seq": e["seq"], "outcome": "applied"} for e in evs]})
        with worker.write_batch(w, capture_paused=True):
            pull.set_stock_from_server(w, [[mid, 188, 0]])
        self.assertEqual(w.execute("SELECT stock_qty FROM medicines WHERE id=?", (mid,)).fetchone()[0], 188)
        self.assertEqual(w.execute("SELECT COUNT(*) FROM of_stock_journal WHERE event_seq IS NULL").fetchone()[0], 0,
                         "setting stock from the server was journaled as a local move")

    def test_a_record_with_an_unsent_local_change_is_not_overwritten(self):
        cid = self.conn.execute("INSERT INTO customers (name, phone) VALUES ('RAM', '1')").lastrowid
        self.conn.commit()
        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        with worker.write_batch(w, capture_paused=True):
            pull.apply_page(w, [{"collection": "customers", "local_id": cid, "operation": "upsert",
                                 "doc": {"id": cid, "name": "SERVER NAME", "version": 5}}], to_revision=9)
        self.assertEqual(w.execute("SELECT name FROM customers WHERE id=?", (cid,)).fetchone()[0], "RAM")
        self.assertEqual(int(meta_get(w, "pull_cursor")), 9)

    def test_touched_medicines_cover_bills_stock_ops_and_medicines(self):
        got = pull.touched_medicines([
            {"collection": "sales", "doc": {"items": [{"medicine_id": 5}, {"medicine_id": 6}]}},
            {"collection": "stock_operations", "doc": {"medicine_id": 7}},
            {"collection": "medicines", "local_id": 8, "doc": {}},
            {"collection": "customers", "local_id": 9, "doc": {}},
        ])
        self.assertEqual(got, {5, 6, 7, 8})


class NoRestoreOverTheLiveCopy(unittest.TestCase):
    def test_drive_and_usb_restores_are_refused_in_offline_first(self):
        from unittest import mock

        from core import backup_manager

        with mock.patch("core.sync_prefs.get_sync_mode", return_value="offline_first"), \
                mock.patch.object(backup_manager, "restore_backup_from_drive") as drive:
            ok, msg = backup_manager.restore_latest_backup_to_store("Shop", "Store_Shop")
            self.assertFalse(ok)
            self.assertIn("offline-first", msg)
            drive.assert_not_called()  # refused before anything is downloaded
            ok, msg = backup_manager.restore_local_backup_to_store("Shop", "Store_Shop", path="x.db")
            self.assertFalse(ok)
            self.assertIn("offline-first", msg)
        with mock.patch("core.sync_prefs.get_sync_mode", return_value="offline"):
            self.assertIsNone(backup_manager._offline_first_restore_refusal())


class LeavingAndComingBack(Base):
    def test_leaving_is_refused_while_anything_is_unsent(self):
        self.conn.execute("INSERT INTO customers (name) VALUES ('UNSENT')")
        self.conn.commit()
        self.assertGreater(schema.unsent_work(self.conn), 0)
        with self.assertRaises(RuntimeError):
            schema.clear_bookkeeping(self.conn)
        self.assertTrue(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='of_ins_customers'").fetchone())

    def test_leaving_clears_notes_and_triggers_so_a_later_switch_starts_clean(self):
        self.conn.execute(
            "INSERT INTO medicines (name, stock_qty, sync_status) VALUES ('M', 10, 'pending')")
        self.conn.commit()
        # Everything reached the server: the outbox is empty, the journal row is in an event.
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.execute("UPDATE of_stock_journal SET event_seq=1")
        self.conn.execute(
            "INSERT INTO of_events (seq, event_uuid, collection, op, payload, created_at, status) "
            "VALUES (1, 'u1', 'stock', 'move', '{}', 'now', 'acked')")
        self.conn.commit()
        self.assertEqual(schema.unsent_work(self.conn), 0)
        schema.clear_bookkeeping(self.conn)
        trig = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall() if r[0].startswith("of_")]
        self.assertEqual(trig, [])
        for t in ("of_dirty", "of_stock_journal", "of_events", "of_meta"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0, t)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM medicines WHERE sync_status <> 'synced'").fetchone()[0], 0)
        # Another mode now: changes are no longer noted.
        self.conn.execute("UPDATE medicines SET stock_qty = 4")
        self.conn.commit()
        self.assertEqual(schema.unsent_work(self.conn), 0)


class OfflineModeIdsStayBelowDeviceRanges(unittest.TestCase):
    def test_offline_mode_on_a_copy_with_device_ids_makes_ids_below_the_limit(self):
        fd, path = tempfile.mkstemp(suffix=".db", prefix="of_legacy_")
        os.close(fd)
        self.addCleanup(lambda: [os.path.exists(path + x) and os.remove(path + x) for x in ("", "-wal", "-shm")])
        c = sqlite3.connect(path)
        db_setup.initialise(c)
        c.execute("INSERT INTO customers (id, name) VALUES (41, 'OLD')")
        c.execute("INSERT INTO customers (id, name) VALUES (8000000001, 'PHONE')")
        c.commit()
        self.assertTrue(ids.has_device_range_ids(c))
        c.close()
        conn = sqlite3.connect(path, factory=ids.LegacyIdConnection)
        conn.execute("INSERT INTO customers (name) VALUES ('NEW 1')")
        conn.executemany("INSERT INTO customers (name) VALUES (?)", [("NEW 2",), ("NEW 3",)])
        conn.commit()
        got = [r[0] for r in conn.execute(
            "SELECT id FROM customers WHERE name LIKE 'NEW%' ORDER BY id").fetchall()]
        conn.close()
        self.assertEqual(got, [42, 43, 44])


class ModeChangesFromSettings(unittest.TestCase):
    def test_plain_offline_is_only_kept_by_a_pc_already_on_it(self):
        from unittest import mock

        from core import desktop_api, desktop_settings_service

        want = {
            "online": ({"offline": "offline_retired", "online": None, "offline_first": "offline_first_via_switch"},
                       ["online"]),
            "offline": ({"offline": None, "online": None, "offline_first": "offline_first_via_switch"},
                        ["offline", "online"]),
            "offline_first": ({"offline": "offline_retired", "online": None, "offline_first": None},
                              ["offline_first", "online"]),
        }
        for current, (refusals, options) in want.items():
            with mock.patch("core.sync_prefs.get_sync_mode", return_value=current):
                for target, code in refusals.items():
                    got = desktop_api._sync_mode_refusal(target)
                    self.assertEqual((got or {}).get("code"), code, f"{current} -> {target}")
                self.assertEqual([o["value"] for o in desktop_settings_service._sync_mode_options()], options)


if __name__ == "__main__":
    unittest.main()
