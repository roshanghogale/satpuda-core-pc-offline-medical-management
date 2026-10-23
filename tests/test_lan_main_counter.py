"""Main PC + Counter PC on one LAN (core/lan; owner, 23 Oct 2026): "when one adds a sale or
purchase it instantly updates in the other -- without internet".

Part 1 runs in this process on two temp store files (the Main PC's copy and a counter's copy):
pairing codes and tokens, change hints, the handover of a counter's own bills to the Main PC,
the Main PC's feed into the counter's copy, the one-bill bundle for printing, files coming back,
and the handed-over bills going on to a stand-in for the cloud that keeps one copy of every
event the way the server does. Every one of them checks nothing is lost and nothing counted
twice -- bills, bill numbers and stock.

Part 2 starts TWO real engines in two processes (tests/lan_engine_child.py: the real engine
handler, its own store file, its own LAN settings folder, ports 187xx -- never 8765), pairs them
over HTTP and checks: requests without the token are refused, a counter's sale is saved on the
Main PC, a sale on the Main PC reaches the counter's screen as a hint, a bill printed on the
counter prints THERE, and with the Main PC switched off the counter bills on its own copy and
hands those bills to the Main PC when it is back -- once each.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import db_setup  # noqa: E402
from core.lan import bridge, files, hints, pairing  # noqa: E402
from core.offline_first import ids, numbers, outbox, schema  # noqa: E402
from core.offline_first.schema import meta_get, meta_set  # noqa: E402

MAIN_DEVICE, COUNTER_DEVICE = 2, 3


def _fy() -> int:
    from core.offline_first.runtime import current_fy

    return current_fy()


def make_stores(folder: str) -> tuple[str, str, list[int]]:
    """Two copies of one shop, as two PCs hold them after the offline-first switch: the same
    records, each with its own device number, id range and bill-number block."""
    base = os.path.join(folder, "base.db")
    conn = sqlite3.connect(base)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    mids = []
    for name, stock in (("DOLO 650", 100), ("CROCIN", 50)):
        mids.append(conn.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no, expiry_date, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (name, "Tablet", stock, "1", 10, 10, "B1", "2029-12-01", "2026-01-01 00:00:00"),
        ).lastrowid)
    conn.execute("INSERT INTO pharmacy_profile (name) VALUES ('LAN TEST MEDICAL')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return device_store(folder, MAIN_DEVICE), device_store(folder, COUNTER_DEVICE), mids


def device_store(folder: str, dev: int) -> str:
    """This device's copy of the shop in ``folder`` (made from base.db)."""
    path = os.path.join(folder, f"store_dev{dev}.db")
    shutil.copyfile(os.path.join(folder, "base.db"), path)
    block = dev * 1000 - 999
    c = sqlite3.connect(path)
    schema.ensure_schema(c)
    meta_set(c, "device_no", dev)
    meta_set(c, "id_base", dev * 1_000_000_000)
    meta_set(c, "id_max", dev * 1_000_000_000 + 999_999_999)
    meta_set(c, "install_id", f"pc-test-{dev}")
    meta_set(c, "pull_cursor", 0)
    numbers.add_block(c, "sales", _fy(), block, block + 199)
    numbers.add_block(c, "purchases", _fy(), block, block + 49)
    c.commit()
    c.close()
    return path


def till(path: str) -> sqlite3.Connection:
    """The engine's own connection on an offline-first PC."""
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def stock(path: str, mid: int) -> int:
    c = sqlite3.connect(path)
    try:
        return int(c.execute("SELECT stock_qty FROM medicines WHERE id=?", (mid,)).fetchone()[0])
    finally:
        c.close()


def bills(path: str) -> list[tuple]:
    c = sqlite3.connect(path)
    try:
        return c.execute(
            "SELECT id, bill_no, total_amount FROM sales WHERE COALESCE(deleted,0)=0 AND "
            "COALESCE(is_autosave,0)=0 ORDER BY id").fetchall()
    finally:
        c.close()


class _SalePatches:
    """What a save needs that is not the store file (no server, no PDF, no profile prompt)."""

    def __init__(self, folder: str):
        self.folder = folder
        self.stack = ExitStack()

    def __enter__(self):
        from core import autosave_session, desktop_sales_service, sync_prefs

        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = os.path.join(self.folder, "sessions.json")
        for p in (
            mock.patch.object(sync_prefs, "get_sync_mode", lambda: sync_prefs.MODE_OFFLINE_FIRST),
            mock.patch.object(autosave_session, "_store_key", lambda: "lan-test-store"),
            mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True),
            mock.patch.object(desktop_sales_service, "_try_save_sale_pdf", return_value=None),
            mock.patch.object(desktop_sales_service, "_next_hint", return_value=""),
            mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=self.folder),
        ):
            self.stack.enter_context(p)
        return self

    def __exit__(self, *exc):
        self.stack.close()
        os.environ.pop("SATPUDA_AUTOSAVE_SESSION_FILE", None)


def sale_body(mid: int, qty: int, customer: str) -> dict:
    return {"items": [{"id": mid, "name": "MED", "qty": qty, "rate": 10.0, "amount": qty * 10.0,
                       "medicine_discount": 0, "unit": "1", "type": "Tablet", "batch": "B1"}],
            "customer_name": customer, "payment_mode": "Cash", "pay_full": True, "discount_rs": 0}


def save_sale(path: str, mid: int, qty: int, customer: str) -> dict:
    from core import desktop_sales_service

    conn = till(path)
    try:
        res = desktop_sales_service.save_sale(conn, sale_body(mid, qty, customer))
        conn.commit()
    finally:
        conn.close()
    assert res.get("ok"), res
    return res


def build_events(path: str) -> None:
    from core.offline_first.worker import open_worker_connection, write_batch

    conn = open_worker_connection(path)
    try:
        with write_batch(conn):
            outbox.build_events(conn, meta_get(conn, "install_id"))
    finally:
        conn.close()


class FakeCloud:
    """The server's push, as far as counting goes (server-live syncV2.pushEvents): one copy of
    each event per device + number and per event id; stock movements kept once per op id."""

    def __init__(self):
        self.events: dict[tuple[str, int], dict] = {}
        self.uuids: set[str] = set()
        self.stock_ops: dict[str, int] = {}
        self.docs: dict[tuple[str, int], dict] = {}

    def push(self, install: str, events: list[dict]) -> dict:
        results = []
        for ev in sorted(events, key=lambda e: e["seq"]):
            key = (install, int(ev["seq"]))
            if key in self.events:
                results.append({"seq": ev["seq"], "outcome": "duplicate", "stored_outcome": "applied"})
                continue
            if ev["event_uuid"] in self.uuids:
                results.append({"seq": ev["seq"], "outcome": "duplicate_uuid"})
                continue
            self.events[key] = ev
            self.uuids.add(ev["event_uuid"])
            if ev.get("op") == "stock":
                for op in ev.get("stock_ops") or []:
                    self.stock_ops.setdefault(op["op_uuid"], int(op["qty_delta"]))
            else:
                self.docs[(ev["collection"], int((ev.get("doc") or {}).get("id") or 0))] = ev.get("doc")
            results.append({"seq": ev["seq"], "outcome": "applied"})
        return {"results": results, "last_seq": max([s for (i, s) in self.events if i == install] or [0])}


class _Stores(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lan_test_")
        self.main, self.counter, self.mids = make_stores(self.dir)
        self.patches = _SalePatches(self.dir).__enter__()

    def tearDown(self):
        self.patches.__exit__(None, None, None)
        bridge.close_all()
        shutil.rmtree(self.dir, ignore_errors=True)


# =============================================================================================
# Part 1: in this process
# =============================================================================================

class PairingAndTokens(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lan_cfg_")
        os.environ["SATPUDA_LAN_DIR"] = self.dir
        pairing.forget_cache()

    def tearDown(self):
        os.environ.pop("SATPUDA_LAN_DIR", None)
        pairing.clear_code()
        pairing.forget_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_code_pairs_once_and_its_token_is_the_only_way_in(self):
        code = pairing.new_code()["code"]
        self.assertEqual(len(code), 6)
        self.assertIsNotNone(pairing.check_code("000000" if code != "000000" else "111111"))
        self.assertIsNone(pairing.check_code(code))
        self.assertIsNotNone(pairing.check_code(code), "a code is used up by the pairing")
        token, dev = pairing.add_device("COUNTER-1", "pc-test-3", "10.0.0.5")
        pairing.forget_cache()
        self.assertEqual(pairing.device_for_token(token)["id"], dev["id"])
        self.assertIsNone(pairing.device_for_token(token + "x"))
        self.assertIsNone(pairing.device_for_token(""))
        stored = json.load(open(os.path.join(self.dir, "lan_config.json"), encoding="utf-8"))
        self.assertNotIn(token, json.dumps(stored), "only the token's hash is kept")
        self.assertTrue(pairing.remove_device(dev["id"]))
        pairing.forget_cache()
        self.assertIsNone(pairing.device_for_token(token), "a removed counter is refused")

    def test_guessing_the_code_closes_it(self):
        code = pairing.new_code()["code"]
        wrong = "123456" if code != "123456" else "654321"
        for _ in range(pairing.MAX_TRIES):
            pairing.check_code(wrong)
        self.assertIsNotNone(pairing.check_code(code), "after too many wrong codes even the right one fails")

    def test_pairing_again_replaces_the_old_token(self):
        t1, _ = pairing.add_device("C", "pc-same", "")
        t2, _ = pairing.add_device("C", "pc-same", "")
        pairing.forget_cache()
        self.assertIsNone(pairing.device_for_token(t1))
        self.assertIsNotNone(pairing.device_for_token(t2))


class Hints(unittest.TestCase):
    def test_a_counter_hears_of_others_changes_not_its_own(self):
        start = hints.since(hints.head(), "", hints.EPOCH)
        hints.note(["sales", "medicines"], origin="counter-A")
        hints.note(["purchases"], origin="")
        a = hints.since(start["seq"], "counter-A", hints.EPOCH)
        self.assertEqual(a["collections"], ["purchases"])
        b = hints.since(start["seq"], "counter-B", hints.EPOCH)
        self.assertEqual(b["collections"], ["medicines", "purchases", "sales"])

    def test_a_restarted_main_pc_makes_the_counter_refresh_everything(self):
        out = hints.since(5, "x", "an-old-epoch")
        self.assertTrue(out.get("reset"))
        self.assertIn("sales", out["collections"])

    def test_what_a_write_touches(self):
        self.assertIn("sales", hints.collections_for_write("/api/sales/save"))
        self.assertEqual(hints.collections_for_write("/api/sales/calc"), ())
        self.assertEqual(hints.collections_for_write("/api/sales/autosave"), ())
        self.assertEqual(hints.collections_for_write("/api/settings/save/appearance"), ())
        self.assertIn("purchases", hints.collections_for_write("/api/purchase/save"))


class Handover(_Stores):
    """Main PC off: the counter bills on its own copy; back on: the bills go to the Main PC."""

    def test_counter_bills_reach_the_main_pc_once_with_their_own_numbers_and_stock(self):
        dolo = self.mids[0]
        a = save_sale(self.counter, dolo, 3, "RAMESH")
        b = save_sale(self.counter, dolo, 2, "SURESH")
        self.assertGreater(a["sale_id"], COUNTER_DEVICE * 1_000_000_000)
        install, events = bridge.handover_batch(self.counter)
        self.assertEqual(install, "pc-test-3")
        self.assertTrue(events)
        res = bridge.receive_handover(self.main, install, events, origin="dev3")
        bridge.mark_handed(self.counter, events, res["accepted"], res["results"])
        main_bills = bills(self.main)
        self.assertEqual({r[0] for r in main_bills}, {a["sale_id"], b["sale_id"]})
        self.assertEqual({r[1] for r in main_bills}, {r[1] for r in bills(self.counter)})
        self.assertEqual(stock(self.main, dolo), 95, "the counter's 5 tablets leave the Main PC's stock once")
        self.assertEqual(stock(self.counter, dolo), 95)
        # The same events again (a lost answer, a resend): nothing doubles.
        again = bridge.receive_handover(self.main, install, events, origin="dev3")
        self.assertEqual(again["applied"]["new_events"], 0)
        self.assertEqual(len(bills(self.main)), 2)
        self.assertEqual(stock(self.main, dolo), 95)
        _i, left = bridge.handover_batch(self.counter)
        self.assertEqual(left, [], "handed events are not offered again")

    def test_the_main_pcs_own_bills_and_the_counters_never_share_a_number(self):
        dolo = self.mids[0]
        m = save_sale(self.main, dolo, 1, "MAIN CUSTOMER")
        c = save_sale(self.counter, dolo, 1, "COUNTER CUSTOMER")
        install, events = bridge.handover_batch(self.counter)
        bridge.receive_handover(self.main, install, events)
        nums = [r[1] for r in bills(self.main)]
        self.assertEqual(len(nums), len(set(nums)), nums)
        self.assertEqual(len(nums), 2)
        self.assertTrue(any(n.startswith(m["bill_no"]) for n in nums), (m["bill_no"], nums))
        self.assertTrue(any(n.startswith(c["bill_no"]) for n in nums), (c["bill_no"], nums))
        self.assertEqual(stock(self.main, dolo), 98)

    def test_handed_over_bills_go_on_to_the_cloud_once_whoever_sends_them(self):
        dolo = self.mids[0]
        save_sale(self.counter, dolo, 4, "RAMESH")
        install, events = bridge.handover_batch(self.counter)
        bridge.receive_handover(self.main, install, events)
        cloud = FakeCloud()
        # The counter gets internet first and sends its own events itself ...
        cloud.push(install, events)
        # ... then the Main PC relays the same ones.
        conn = bridge.connection(self.main)
        sent = bridge.relay_to_cloud(conn, push=cloud.push)
        self.assertEqual(sent, len(events))
        self.assertEqual(len(cloud.events), len(events), "each event is on the cloud once")
        self.assertEqual(sum(cloud.stock_ops.values()), -4)
        # Until the cloud had them the Main PC counted them as its own unsent movements; not now.
        self.assertEqual(bridge.pending_relay_stock(conn), {})
        # The answers go back to the counter, which marks its events sent.
        res = bridge.receive_handover(self.main, install, [])
        bridge.mark_handed(self.counter, [], [], res["results"])
        c = sqlite3.connect(self.counter)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM of_events WHERE status='pending'").fetchone()[0], 0)
        c.close()

    def test_a_relayed_movement_is_kept_while_the_cloud_does_not_have_it(self):
        dolo = self.mids[0]
        save_sale(self.counter, dolo, 6, "RAMESH")
        install, events = bridge.handover_batch(self.counter)
        bridge.receive_handover(self.main, install, events)
        conn = bridge.connection(self.main)
        self.assertEqual(outbox.pending_stock_by_medicine(conn).get(dolo), -6)
        # A pull from the server sets stock to the server's figure + unsent movements: the
        # counter's 6 stay out of the Main PC's stock until the server has them.
        from core.offline_first import pull
        from core.offline_first.worker import write_batch

        with write_batch(conn, capture_paused=True):
            pull.set_stock_from_server(conn, [[dolo, 100, 0]])
        self.assertEqual(stock(self.main, dolo), 94)

    def test_the_cloud_refusing_leaves_them_waiting(self):
        save_sale(self.counter, self.mids[0], 1, "RAMESH")
        install, events = bridge.handover_batch(self.counter)
        bridge.receive_handover(self.main, install, events)
        conn = bridge.connection(self.main)

        def offline(_i, _e):
            raise RuntimeError("no internet")

        with self.assertRaises(RuntimeError):
            bridge.relay_to_cloud(conn, push=offline)
        self.assertEqual(bridge.relay_counts(conn)["relay_waiting"], len(events))


class FeedAndBundle(_Stores):
    """The Main PC's bills copied into the counter's copy (for a failover with no internet)."""

    def test_the_main_pcs_bills_reach_the_counters_copy_once(self):
        dolo = self.mids[0]
        m = save_sale(self.main, dolo, 7, "MAIN CUSTOMER")
        build_events(self.main)
        page = bridge.feed(self.main, -1, -1, "pc-test-3")
        self.assertTrue(page["events"])
        bridge.apply_feed(self.counter, page)
        self.assertIn(m["sale_id"], [r[0] for r in bills(self.counter)])
        self.assertEqual(stock(self.counter, dolo), 93)
        bridge.apply_feed(self.counter, page)          # the same page again
        self.assertEqual(stock(self.counter, dolo), 93)
        self.assertEqual(len(bills(self.counter)), 1)
        # Nothing the feed wrote is sent from the counter as its own.
        _i, mine = bridge.handover_batch(self.counter)
        self.assertEqual(mine, [])

    def test_a_counters_bills_do_not_come_back_to_it_but_reach_the_other_counters(self):
        save_sale(self.counter, self.mids[0], 1, "RAMESH")
        install, events = bridge.handover_batch(self.counter)
        bridge.receive_handover(self.main, install, events)
        own = bridge.feed(self.main, 0, 0, "pc-test-3")
        self.assertEqual(own["relay_events"], [])
        other = bridge.feed(self.main, 0, 0, "pc-test-9")
        self.assertEqual(len(other["relay_events"]), len(events))

    def test_a_bill_to_print_is_copied_without_moving_stock_twice(self):
        dolo = self.mids[0]
        m = save_sale(self.main, dolo, 5, "MAIN CUSTOMER")
        docs = bridge.bundle(self.main, "sales", m["sale_id"])
        self.assertIn("sales", [d["collection"] for d in docs])
        bridge.apply_docs(self.counter, docs)
        self.assertIn(m["sale_id"], [r[0] for r in bills(self.counter)])
        self.assertEqual(stock(self.counter, dolo), 100, "the bundle moves no stock")
        build_events(self.main)
        bridge.apply_feed(self.counter, bridge.feed(self.main, -1, -1, "pc-test-3"))
        self.assertEqual(stock(self.counter, dolo), 95, "the feed's stock event moves it once")
        self.assertEqual(len(bills(self.counter)), 1)


class FilesComeBack(unittest.TestCase):
    def test_a_file_the_main_pc_made_is_written_into_the_counters_folder(self):
        main_dir = tempfile.mkdtemp(prefix="lan_main_files_")
        counter_dir = tempfile.mkdtemp(prefix="lan_counter_files_")
        try:
            os.makedirs(os.path.join(main_dir, "Exports"))
            made = os.path.join(main_dir, "Exports", "Sales_2026-10-23.pdf")
            with open(made, "wb") as fh:
                fh.write(b"%PDF-1.4 test")
            outside = os.path.join(tempfile.gettempdir(), "lan_not_shared.txt")
            with open(outside, "w") as fh:
                fh.write("x")
            payload = {"ok": True, "path": made, "folder": os.path.dirname(made), "other": outside}
            sent = files.attach(payload, roots=[("exports", os.path.join(main_dir, "Exports"))])
            self.assertEqual(len(sent["_lan_files"]), 1, "only files inside the save folders go")
            got = files.receive(json.loads(json.dumps(sent)),
                                roots=[("exports", os.path.join(counter_dir, "Exports"))])
            local = os.path.join(counter_dir, "Exports", "Sales_2026-10-23.pdf")
            self.assertEqual(got["path"], local)
            self.assertEqual(got["folder"], os.path.dirname(local))
            self.assertEqual(open(local, "rb").read(), b"%PDF-1.4 test")
            self.assertNotIn("_lan_files", got)
        finally:
            shutil.rmtree(main_dir, ignore_errors=True)
            shutil.rmtree(counter_dir, ignore_errors=True)


class RoutingRules(unittest.TestCase):
    def test_what_stays_on_the_counter(self):
        from core.lan.counter import is_local

        for p in ("/api/health", "/api/prefs", "/api/sync/status", "/api/files/open", "/api/lan/status",
                  "/api/settings/save/appearance", "/api/settings/printer/test", "/api/license/status",
                  "/api/offline-first/status"):
            self.assertTrue(is_local(p), p)
        for p in ("/api/sales/save", "/api/sales/history", "/api/inventory", "/api/purchase/save",
                  "/api/settings/contacts", "/api/history/pay-bill", "/api/export/run"):
            self.assertFalse(is_local(p), p)


# =============================================================================================
# Part 2: two real engines in two processes
# =============================================================================================

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(method: str, url: str, body=None, headers=None, timeout=60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json",
                                                                         **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}


class Engine:
    def __init__(self, name: str, db: str, folder: str, install: str, role: str, lan_port: int, disc_port: int,
                 counter: dict | None = None):
        self.name, self.db, self.install = name, db, install
        self.dir = os.path.join(folder, name)
        os.makedirs(self.dir, exist_ok=True)
        self.port = _free_port()
        cfg = {"role": role, "port": lan_port, "discovery_port": disc_port}
        if counter:
            cfg["counter"] = counter
        # utf-8-sig, as the installer writes it.
        with open(os.path.join(self.dir, "lan_config.json"), "w", encoding="utf-8-sig") as fh:
            json.dump(cfg, fh)
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "tests", "lan_engine_child.py"), "--db", self.db,
             "--port", str(self.port), "--lan-dir", self.dir, "--install", self.install,
             "--saves", os.path.join(self.dir, "saves")],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        line = ""
        end = time.time() + 90
        while time.time() < end:
            line = self.proc.stdout.readline()
            if "READY" in line or not line:
                break
        if "READY" not in line:
            raise RuntimeError(f"{self.name} did not start: {line}")
        return self

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(15)
            except Exception:
                self.proc.kill()
            self.proc = None

    def kill(self):
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait(15)
            self.proc = None

    def api(self, method, path, body=None, timeout=120):
        return http(method, f"http://127.0.0.1:{self.port}{path}", body, timeout=timeout)

    def printed(self) -> list[str]:
        p = os.path.join(self.dir, "printed.txt")
        return open(p, encoding="utf-8").read().split("\n")[:-1] if os.path.isfile(p) else []


def wait_for(fn, timeout=20.0, every=0.25):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    return last


@unittest.skipIf(os.environ.get("LAN_SKIP_PROCESSES"), "two-process LAN test switched off")
class TwoEngines(unittest.TestCase):
    """The real thing, on one machine: a Main PC engine and a Counter PC engine."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="lan_two_")
        cls.main_db, cls.counter_db, cls.mids = make_stores(cls.dir)
        cls.lan_port, cls.disc_port = _free_port(), _free_port()
        cls.main = Engine("main", cls.main_db, cls.dir, "pc-test-2", "main", cls.lan_port, cls.disc_port).start()
        cls.counter = Engine("counter", cls.counter_db, cls.dir, "pc-test-3", "counter", cls.lan_port,
                             cls.disc_port).start()
        status, code = cls.main.api("POST", "/api/lan/pairing-code", {})
        assert status == 200, code
        status, out = cls.counter.api("POST", "/api/lan/pair", {"host": "127.0.0.1", "port": cls.lan_port,
                                                                 "code": code["code"]})
        assert status == 200 and out.get("ok"), out
        cls.token = json.load(open(os.path.join(cls.counter.dir, "lan_config.json")))["counter"]["token"]
        ok = wait_for(lambda: cls.counter.api("GET", "/api/lan/status")[1].get("counter", {}).get(
            "main_reachable"), 20)
        assert ok, cls.counter.api("GET", "/api/lan/status")

    @classmethod
    def tearDownClass(cls):
        cls.counter.stop()
        cls.main.stop()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def lan(self, method, path, body=None, token=None):
        headers = {"X-Satpuda-Lan-Token": token} if token is not None else {}
        return http(method, f"http://127.0.0.1:{self.lan_port}{path}", body, headers)

    def test_1_the_lan_listener_refuses_anyone_without_the_token(self):
        self.assertEqual(self.lan("GET", "/api/sales/history")[0], 401)
        self.assertEqual(self.lan("GET", "/api/sales/history", token="wrong")[0], 401)
        self.assertEqual(self.lan("GET", "/lan/feed?after=0", token="")[0], 401)
        self.assertEqual(self.lan("POST", "/lan/pair", {"code": "000000"})[0], 403)
        status, _ = self.lan("GET", "/lan/ping", token=self.token)
        self.assertEqual(status, 200)

    def test_1b_the_counter_finds_the_main_pc_and_the_main_pc_lists_the_counter(self):
        status, out = self.counter.api("POST", "/api/lan/discover", {})
        self.assertEqual(status, 200, out)
        self.assertIn(self.lan_port, [m["port"] for m in out["found"]], out)
        status, st = self.main.api("GET", "/api/lan/status")
        self.assertEqual(st["role"], "main")
        self.assertEqual(len(st["devices"]), 1)
        self.assertTrue(st["devices"][0]["connected"], st["devices"])

    def test_2_a_sale_made_on_the_counter_is_saved_on_the_main_pc(self):
        status, res = self.counter.api("POST", "/api/sales/save", sale_body(self.mids[1], 2, "COUNTER BUYER"))
        self.assertEqual(status, 200, res)
        sid = res["sale_id"]
        self.assertTrue(MAIN_DEVICE * 1_000_000_000 < sid < COUNTER_DEVICE * 1_000_000_000,
                        f"made by the Main PC's engine, in its id range: {sid}")
        self.assertIn(sid, [r[0] for r in bills(self.main_db)])
        # The counter's copy gets the bill at once (for a failover a moment later).
        self.assertTrue(wait_for(lambda: sid in [r[0] for r in bills(self.counter_db)], 10))
        # The counter's screen reads the Main PC's data -- even a bill only the Main PC's file has.
        from datetime import date

        c = sqlite3.connect(self.main_db)
        c.execute("INSERT INTO sales (id, bill_no, bill_date, total_amount, customer_name) VALUES "
                  "(2000000777, 'MAINONLY-77', ?, 1, 'X')", (date.today().isoformat(),))
        c.commit()
        c.close()
        status, hist = self.counter.api("GET", "/api/sales/history")
        self.assertEqual(status, 200)
        self.assertIn(res["bill_no"], json.dumps(hist))
        self.assertIn("MAINONLY-77", json.dumps(hist))
        self.assertNotIn(2000000777, [r[0] for r in bills(self.counter_db)])

    def test_3_a_sale_on_the_main_pc_reaches_the_counters_screen(self):
        self.counter.api("GET", "/api/sync/status")         # clear what was waiting
        status, res = self.main.api("POST", "/api/sales/save", sale_body(self.mids[1], 1, "MAIN BUYER"))
        self.assertEqual(status, 200, res)

        def heard():
            _s, st = self.counter.api("GET", "/api/sync/status")
            return "sales" in (st.get("refresh_collections") or [])

        t0 = time.time()
        self.assertTrue(wait_for(heard, 10), "the counter's screen was told")
        self.assertLess(time.time() - t0, 5)
        _s, st = self.counter.api("GET", "/api/sync/status")
        self.assertEqual(st["lan"]["role"], "counter")
        self.assertTrue(st["lan"]["main_reachable"])

    def test_4_a_bill_printed_on_the_counter_prints_on_the_counter(self):
        body = dict(sale_body(self.mids[1], 1, "PRINT BUYER"), save_first=True, slot=1)
        status, res = self.counter.api("POST", "/api/sales/print", body)
        self.assertEqual(status, 200, res)
        self.assertIn(res["sale_id"], [r[0] for r in bills(self.main_db)], "saved on the Main PC")
        self.assertTrue(any(line.startswith(f"{res['sale_id']}\t") for line in self.counter.printed()),
                        self.counter.printed())
        self.assertFalse(any(line.startswith(f"{res['sale_id']}\t") for line in self.main.printed()))

    def test_5_main_pc_off_the_counter_bills_on_its_own_and_joins_up_once(self):
        dolo = self.mids[0]
        before_main = {r[0] for r in bills(self.main_db)}
        stock_main_before = stock(self.main_db, dolo)
        self.main.kill()
        try:
            def down():
                _s, st = self.counter.api("GET", "/api/sync/status")
                return not st["lan"].get("main_reachable")

            self.assertTrue(wait_for(down, 15))
            _s, st = self.counter.api("GET", "/api/sync/status")
            self.assertIn("Main PC not reachable", st["lan"]["banner"])
            made = []
            for qty in (1, 2, 3):
                status, res = self.counter.api("POST", "/api/sales/save", sale_body(dolo, qty, f"OFFLINE {qty}"))
                self.assertEqual(status, 200, res)
                self.assertGreater(res["sale_id"], COUNTER_DEVICE * 1_000_000_000, "the counter's own range")
                made.append(res)
            self.assertNotIn(made[0]["sale_id"], before_main)
        finally:
            self.main.start()

        def joined():
            have = {r[0] for r in bills(self.main_db)}
            return all(m["sale_id"] in have for m in made)

        self.assertTrue(wait_for(joined, 30), "the counter's bills reached the Main PC")
        main_bills = bills(self.main_db)
        ids_ = [r[0] for r in main_bills]
        self.assertEqual(len(ids_), len(set(ids_)))
        numbers_ = [r[1] for r in main_bills]
        self.assertEqual(len(numbers_), len(set(numbers_)), "no bill number twice")
        self.assertEqual(stock(self.main_db, dolo), stock_main_before - 6, "stock moved once")
        self.assertTrue(wait_for(lambda: self.counter.api("GET", "/api/sync/status")[1]["lan"].get(
            "main_reachable"), 20))
        # Once more round the loop: nothing doubles.
        time.sleep(4)
        self.assertEqual(len(bills(self.main_db)), len(main_bills))
        self.assertEqual(stock(self.main_db, dolo), stock_main_before - 6)
        c = sqlite3.connect(self.main_db)
        held = c.execute("SELECT COUNT(*), COUNT(DISTINCT event_uuid) FROM of_lan_relay").fetchone()
        c.close()
        self.assertEqual(held[0], held[1])

    def test_6_a_counter_set_up_by_the_installer_finds_and_pairs_by_itself(self):
        # What installer/SatpudaCore.iss writes for "Counter PC" with no address typed.
        status, code = self.main.api("POST", "/api/lan/pairing-code", {})
        self.assertEqual(status, 200, code)
        db = device_store(self.dir, 4)
        second = Engine("counter2", db, self.dir, "pc-test-4", "counter", self.lan_port, self.disc_port,
                        counter={"main_host": "", "main_port": self.lan_port,
                                 "pending_code": code["code"]}).start()
        try:
            self.assertTrue(wait_for(lambda: second.api("GET", "/api/lan/status")[1].get("counter", {}).get(
                "main_reachable"), 30), second.api("GET", "/api/lan/status"))
            cfg = json.load(open(os.path.join(second.dir, "lan_config.json"), encoding="utf-8-sig"))
            self.assertEqual(cfg["counter"].get("pending_code"), "", "the code is used once and forgotten")
            # A sale on the second counter reaches the first counter's screen.
            self.counter.api("GET", "/api/sync/status")
            status, res = second.api("POST", "/api/sales/save", sale_body(self.mids[1], 1, "SECOND COUNTER"))
            self.assertEqual(status, 200, res)
            self.assertTrue(wait_for(lambda: "sales" in (self.counter.api("GET", "/api/sync/status")[1].get(
                "refresh_collections") or []), 10))
        finally:
            second.stop()


if __name__ == "__main__":
    unittest.main()
