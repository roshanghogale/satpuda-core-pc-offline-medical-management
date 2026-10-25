"""How long a change takes to travel between two offline-first devices (REHEARSAL server only).

    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 python -m pytest -q -s tests/test_offline_first_sync_timing_server.py

Two PCs of one shop. PC A saves (a sale, an edit, a delete, a purchase, a payment, a return, a
medicine edit, a stock edit); the test measures, in milliseconds:

    push   A's worker cycle sending it (the server has it when this returns)
    hint   the server's WebSocket sync_hint reaching B, counted from the start of A's push
    pull   B's worker cycle bringing it in (B's copy has it when this returns)

and checks B's copy really holds the change. The waiting that the code adds on top of these
(the worker's own interval, the screen poller) is not in the numbers: those are constants in
core/offline_first/worker.py and desktop/src/hooks/useSyncRefreshPoller.ts.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import ExitStack
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup  # noqa: E402
from core import server_api as api  # noqa: E402
from core.offline_first import client, ids, runtime, worker  # noqa: E402

BASE = os.environ.get("OF_TEST_BASE", "").rstrip("/")
KEY = os.environ.get("OF_TEST_KEY", "SC-V2TEST01")
DAY = date.today().isoformat()
HINT_WAIT = 8.0


def _post(path, body, token=None):
    import urllib.request

    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


_real_request = api._request


def _rehearsal_only(method, path, *, base=None, **kw):
    b = (base or api.api_base()).rstrip("/")
    if b != BASE:
        raise AssertionError(f"a request left the rehearsal server: {method} {b}{path}")
    return _real_request(method, path, base=b, **kw)


_ACTIVE: dict = {}


class PC:
    def __init__(self, name: str, install: str):
        self.name, self.install = name, install
        fd, self.path = tempfile.mkstemp(suffix=".db", prefix=f"oft_{name}_")
        os.close(fd)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        db_setup.initialise(conn)
        conn.commit()
        conn.close()
        self.token = _post("/api/auth/pair", {"android_key": KEY, "device_id": install, "device_type": "pc"})["data"]["token"]
        self.till = None
        self.cycle = worker.SyncCycle(self.path)

    def acting(self):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            prev = _ACTIVE.get("pc")
            _ACTIVE["pc"] = self
            try:
                yield
            finally:
                _ACTIVE["pc"] = prev

        return _cm()

    def prepare(self):
        with self.acting():
            conn = sqlite3.connect(self.path)
            runtime.register_and_prepare(conn, head_revision=client.head_revision())
            conn.close()
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        from core.db_setup import ensure_recent_columns

        ensure_recent_columns(c)
        c.execute("INSERT INTO pharmacy_profile (name) SELECT 'TIMING SHOP' WHERE NOT EXISTS (SELECT 1 FROM pharmacy_profile)")
        c.commit()
        self.till = c

    def push_ms(self) -> float:
        with self.acting():
            t = time.perf_counter()
            self.cycle.push()
            return (time.perf_counter() - t) * 1000

    def pull_ms(self) -> float:
        with self.acting():
            t = time.perf_counter()
            self.cycle.pull()
            return (time.perf_counter() - t) * 1000

    def sync(self, rounds=2):
        with self.acting():
            for _ in range(rounds):
                self.cycle.run_once()

    def one(self, sql, *a):
        r = self.till.execute(sql, a).fetchone()
        return r[0] if r else None

    def close(self):
        self.cycle.close()
        if self.till:
            self.till.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + s)
            except OSError:
                pass


class HintListener:
    """B's ear on the server: /ws/sync with B's token; every sync_hint is timestamped."""

    def __init__(self, token: str):
        import websocket
        from core.sync_ws import _ws_url

        self.hints: list[tuple[float, dict]] = []
        self.opened = threading.Event()
        self._cv = threading.Condition()

        def on_open(ws):
            ws.send(json.dumps({"type": "subscribe"}))
            self.opened.set()

        def on_message(_ws, message):
            try:
                msg = json.loads(message)
            except Exception:
                return
            if isinstance(msg, dict) and msg.get("type") == "sync_hint":
                with self._cv:
                    self.hints.append((time.perf_counter(), msg))
                    self._cv.notify_all()

        self.app = websocket.WebSocketApp(_ws_url(BASE, token), on_open=on_open, on_message=on_message)
        self.thread = threading.Thread(target=self.app.run_forever, daemon=True)
        self.thread.start()
        if not self.opened.wait(10):
            raise RuntimeError("the hint socket did not open")

    def wait_after(self, n: int, timeout: float):
        """The first hint beyond the n-th, or None."""
        end = time.monotonic() + timeout
        with self._cv:
            while len(self.hints) <= n:
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._cv.wait(left)
            return self.hints[n]

    def close(self):
        try:
            self.app.close()
        except Exception:
            pass


def _item(mid, name, qty, rate, **extra):
    d = {"id": mid, "name": name, "qty": qty, "rate": rate, "amount": round(qty * rate, 2),
         "medicine_discount": 0, "unit": "1", "type": "Syrup", "batch": "B1", "gst_percent": 12}
    d.update(extra)
    return d


ROWS: list[tuple[str, float, float, float, float]] = []


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class SyncTiming(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        from core import desktop_sales_service as sales_svc

        # The main thread acts as whichever PC is inside ``acting()``; B's own background
        # worker and hint socket (the real threads, see the second test) are always B.
        def _who():
            if threading.current_thread().name in ("offline-first-sync", "SyncWebSocket"):
                return getattr(self, "b", None)
            return _ACTIVE.get("pc")

        tok = lambda *a, **k: _who().token if _who() else ""  # noqa: E731
        inst = lambda *a, **k: _who().install if _who() else "none"  # noqa: E731
        for p in (
            mock.patch.object(api, "_request", _rehearsal_only),
            mock.patch.object(api, "api_base", lambda: BASE),
            mock.patch.object(api, "store_token_for_active", tok),
            mock.patch.object(api, "_pc_device_id", inst),
            mock.patch.object(client, "_token", tok),
            mock.patch.object(client, "install_id", inst),
            mock.patch("core.sync_prefs.get_sync_mode", lambda *a, **k: "offline_first"),
            mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: False),
            mock.patch("core.offline_first.runtime.is_active", lambda *a, **k: True),
            mock.patch.object(worker, "start_for", lambda *a, **k: None),
            mock.patch.object(worker, "kick", lambda *a, **k: None),
            mock.patch.object(sales_svc, "_has_pharmacy_profile", return_value=True),
            mock.patch.object(sales_svc, "_try_save_sale_pdf", return_value=None),
            mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=""),
            mock.patch("core.autosave_prefs.load_autosave_enabled", return_value=False),
            mock.patch("core.layout_config.save_layout", lambda *a, **k: None),
        ):
            stack.enter_context(p)
        run = uuid.uuid4().hex[:8].upper()
        self.run_id = run
        self.cust = f"TIM CUST {run}"
        self.supp = f"TIM AGENCY {run}"
        self.a = PC("A", f"pc-A-oft-{run}")
        self.b = PC("B", f"pc-B-oft-{run}")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.a.prepare()
        self.b.prepare()
        self.m1 = self.medicine(self.a, f"TIM SYRUP {run}", 10.0, 100)
        self.m2 = self.medicine(self.a, f"TIM TAB {run}", 20.0, 100)
        self.settle()
        self.ear = HintListener(self.b.token)
        self.addCleanup(self.ear.close)

    # -- what a shop does ------------------------------------------------------------------
    def medicine(self, pc, name, rate, stock):
        cols = {r[1] for r in pc.till.execute("PRAGMA table_info(medicines)")}
        vals = {"name": name, "stock_qty": stock, "mrp": rate, "rate": rate, "unit": "1",
                "type": "Syrup", "batch_no": "B1", "expiry_date": "2028-12-01",
                "gst_percent": 12, "created_at": "2026-01-01 00:00:00"}
        use = {k: v for k, v in vals.items() if k in cols}
        mid = pc.till.execute(
            f"INSERT INTO medicines ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
            list(use.values())).lastrowid
        pc.till.commit()
        return mid

    def sell(self, pc, items, **extra):
        from core import desktop_sales_service as sales_svc

        body = {"items": items, "customer_name": self.cust, "payment_mode": "Cash", "pay_full": True,
                "bill_date": DAY, "discount_rs": 0, "auto_rounding": False, "rounding": 0}
        body.update(extra)
        with pc.acting():
            res = sales_svc.save_sale(pc.till, body)
        self.assertTrue(res.get("ok"), res)
        return res

    def buy(self, pc, bill, lines, **extra):
        from core import desktop_purchase_service as purchase_svc

        payload = {"supplier_name": self.supp, "bill_number": bill, "purchase_date": DAY,
                   "items": lines, "cash_paid": 0, "rounding": 0}
        payload.update(extra)
        with pc.acting():
            res = purchase_svc.save_purchase_bill(pc.till, payload)
        self.assertTrue(res.get("ok"), res)
        return res

    def pline(self, mid, qty, rate=5.0):
        mrp = self.a.one("SELECT mrp FROM medicines WHERE id=?", mid)
        name = self.a.one("SELECT name FROM medicines WHERE id=?", mid)
        return {"medicine_id": mid, "name": name, "type": "Syrup", "batch": "B1", "expiry": "12/28",
                "qty": qty, "rate": rate, "mrp": mrp, "unit": "1", "gst_pct": 0}

    def pay(self, pc, kind, amount):
        from core import desktop_settings_service as svc

        body = {"kind": kind, "party": self.cust if kind == "customer" else self.supp,
                "amount": amount, "mode": "Cash", "date": DAY}
        if kind == "customer":
            body["cash"] = amount
        with pc.acting():
            res = svc.save_payment(pc.till, body)
        self.assertTrue(res.get("ok", True), res)
        return res

    def settle(self):
        for _ in range(2):
            self.a.sync()
            self.b.sync()

    # -- the clock -------------------------------------------------------------------------
    def travel(self, label: str, doer, check, *, hinted=True):
        """A saves; measure push / hint / pull; B's copy must pass ``check``."""
        n_before = len(self.ear.hints)
        t0 = time.perf_counter()
        doer()
        save_ms = (time.perf_counter() - t0) * 1000
        t_push = time.perf_counter()
        push_ms = self.a.push_ms()
        hint = self.ear.wait_after(n_before, HINT_WAIT) if hinted else None
        hint_ms = (hint[0] - t_push) * 1000 if hint else float("nan")
        pull_ms = self.b.pull_ms()
        ok = check(self.b)
        if not ok:
            # the stock figure can trail by one round trip when the push answer asked for it
            self.b.pull_ms()
            ok = check(self.b)
        if not ok:
            dump = {t: [tuple(r) for r in self.b.till.execute(q).fetchall()] for t, q in (
                ("sales", "SELECT id, COALESCE(deleted,0), total_amount FROM sales"),
                ("purchases", "SELECT id, COALESCE(deleted,0) FROM purchases"),
                ("medicines", "SELECT id, stock_qty, rate FROM medicines"),
                ("events", "SELECT collection, op, outcome, flag_code FROM of_events ORDER BY seq DESC LIMIT 5"),
            )}
            dump["a_medicines"] = [tuple(r) for r in self.a.till.execute("SELECT id, stock_qty FROM medicines").fetchall()]
            dump["a_events"] = [tuple(r) for r in self.a.till.execute(
                "SELECT collection, op, outcome, flag_code, flag_detail FROM of_events ORDER BY seq DESC LIMIT 5").fetchall()]
            self.fail(f"{label}: B's copy does not show A's change: {dump}")
        if hinted:
            self.assertIsNotNone(hint, f"{label}: no sync_hint reached B within {HINT_WAIT}s")
            self.assertEqual(hint[1].get("source_device_id"), self.a.install, hint[1])
        ROWS.append((label, save_ms, push_ms, hint_ms, pull_ms))
        return push_ms, hint_ms, pull_ms

    def test_every_record_type_travels_from_a_to_b(self):
        a, b = self.a, self.b
        m1, m2 = self.m1, self.m2

        s = {}
        self.travel("new sale (cash)",
                    lambda: s.update(self.sell(a, [_item(m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)),
                    lambda pc: pc.one("SELECT total_amount FROM sales WHERE id=?", s["sale_id"]) == 50.0
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m1) == 95)
        self.travel("sale edit",
                    lambda: self.sell(a, [_item(m1, "S", 2, 10.0)], editing_sale_id=s["sale_id"],
                                      payment_mode="Due", pay_full=False),
                    lambda pc: pc.one("SELECT total_amount FROM sales WHERE id=?", s["sale_id"]) == 20.0
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m1) == 98)
        self.travel("customer payment",
                    lambda: self.pay(a, "customer", 20),
                    lambda pc: pc.one("SELECT COUNT(*) FROM customer_payments WHERE amount=20 AND COALESCE(deleted,0)=0") == 1)

        def delete_sale():
            from core import desktop_sales_service as sales_svc

            with a.acting():
                out = sales_svc.delete_saved_sale(a.till, {"sale_id": s["sale_id"]})
            self.assertTrue(out.get("ok"), out)

        self.travel("sale delete", delete_sale,
                    lambda pc: pc.one("SELECT COALESCE(deleted,0) FROM sales WHERE id=?", s["sale_id"]) in (1, None)
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m1) == 100)

        p = {}
        self.travel("new purchase",
                    lambda: p.update(self.buy(a, f"P{self.run_id}", [self.pline(m1, 10)])),
                    lambda pc: pc.one("SELECT COUNT(*) FROM purchases WHERE id=?", p["purchase_id"]) == 1
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m1) == 110)
        self.travel("purchase edit",
                    lambda: self.buy(a, f"P{self.run_id}", [self.pline(m1, 6)], editing_purchase_id=p["purchase_id"]),
                    lambda pc: pc.one("SELECT qty FROM purchase_items WHERE purchase_id=?", p["purchase_id"]) == 6
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m1) == 106)
        self.travel("supplier payment",
                    lambda: self.pay(a, "supplier", 10),
                    lambda pc: pc.one("SELECT COUNT(*) FROM supplier_payments WHERE amount=10 AND COALESCE(deleted,0)=0") == 1)

        r = {}

        def purchase_return():
            from core import desktop_returns_service as returns_svc

            with a.acting():
                out = returns_svc.save_purchase_return(a.till, {
                    "purchase_id": p["purchase_id"], "supplier_id": a.one("SELECT id FROM suppliers WHERE name=?", self.supp),
                    "settle_mode": "ledger", "items": [{"medicine_id": m1, "qty": 2, "rate": 5.0}]})
            self.assertTrue(out.get("ok"), out)
            r.update(out)

        self.travel("purchase return", purchase_return,
                    lambda pc: pc.one("SELECT COUNT(*) FROM purchase_returns WHERE id=?", r["return_id"]) == 1
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m1) == 104)

        def delete_purchase():
            from core import desktop_purchase_service as purchase_svc

            with a.acting():
                out = purchase_svc.delete_saved_purchase(a.till, {"purchase_id": p["purchase_id"]})
            self.assertTrue(out.get("ok"), out)

        self.travel("purchase delete", delete_purchase,
                    lambda pc: pc.one("SELECT COALESCE(deleted,0) FROM purchases WHERE id=?", p["purchase_id"]) in (1, None))

        s2 = {}
        self.travel("new sale (for a return)",
                    lambda: s2.update(self.sell(a, [_item(m2, "T", 3, 20.0)])),
                    lambda pc: pc.one("SELECT COUNT(*) FROM sales WHERE id=?", s2["sale_id"]) == 1
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m2) == 97)
        sr = {}

        def sales_return():
            from core import desktop_returns_service as returns_svc

            with a.acting():
                out = returns_svc.save_sales_return(a.till, {
                    "sale_id": s2["sale_id"], "customer_id": a.one("SELECT id FROM customers WHERE name=?", self.cust),
                    "settle_mode": "ledger", "items": [{"medicine_id": m2, "qty": 1, "rate": 20.0}]})
            self.assertTrue(out.get("ok"), out)
            sr.update(out)

        self.travel("sales return", sales_return,
                    lambda pc: pc.one("SELECT COUNT(*) FROM sales_returns WHERE id=?", sr["return_id"]) == 1
                    and pc.one("SELECT stock_qty FROM medicines WHERE id=?", m2) == 98)

        def edit_medicine():
            from core import desktop_inventory_service as inv

            with a.acting():
                out = inv.update_medicine(a.till, {"id": m2, "name": f"TIM TAB {self.run_id} X", "type": "Syrup",
                                                   "unit": "1", "batch": "B1", "expiry": "12/28", "mrp": 25.0,
                                                   "rate": 22.0, "gst_percent": 12})
            self.assertTrue(out.get("ok"), out)

        self.travel("medicine edit (name/rate)", edit_medicine,
                    lambda pc: pc.one("SELECT rate FROM medicines WHERE id=?", m2) == 22.0)

        def stock_edit():
            from core import desktop_inventory_service as inv

            with a.acting():
                out = inv.update_medicine(a.till, {"id": m2, "name": f"TIM TAB {self.run_id} X", "type": "Syrup",
                                                   "unit": "1", "batch": "B1", "expiry": "12/28", "mrp": 25.0,
                                                   "rate": 22.0, "gst_percent": 12, "stock_qty": 150})
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(a.one("SELECT stock_qty FROM medicines WHERE id=?", m2), 150)

        self.travel("manual stock edit", stock_edit,
                    lambda pc: pc.one("SELECT stock_qty FROM medicines WHERE id=?", m2) == 150)

        # An empty poll: what a worker cycle costs when nothing happened.
        idle = [b.pull_ms() for _ in range(3)]
        push_idle = [a.push_ms() for _ in range(3)]
        ROWS.append(("(idle pull, nothing new)", 0, 0, float("nan"), sum(idle) / len(idle)))
        ROWS.append(("(idle push, nothing new)", 0, sum(push_idle) / len(push_idle), float("nan"), 0))

    def test_with_b_s_worker_running_a_s_bill_is_in_b_s_copy_in_about_a_second(self):
        """The real loop on B (core/offline_first/worker.py) with its hint socket: A saves and its
        worker pushes at once (the kick after a write); the server's hint wakes B; B pulls."""
        a, b = self.a, self.b
        self.ear.close()                       # one ear per device: B's worker has its own now
        w = worker.Worker(b.path)
        self.addCleanup(w.stop)
        w.start()
        end = time.monotonic() + 10
        while not (w.ws is not None and w.ws.connected) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertTrue(w.ws is not None and w.ws.connected, "B's hint socket did not open")
        time.sleep(1.5)                        # B's first cycle
        took = []
        for i in range(3):
            t0 = time.perf_counter()
            res = self.sell(a, [_item(self.m1, "S", 1, 10.0)])
            a.push_ms()                        # what the kick after the save does on A
            sid = res["sale_id"]
            end = time.monotonic() + 8
            while time.monotonic() < end:
                if b.one("SELECT COUNT(*) FROM sales WHERE id=?", sid) == 1:
                    break
                time.sleep(0.02)
            ms = (time.perf_counter() - t0) * 1000
            self.assertEqual(b.one("SELECT COUNT(*) FROM sales WHERE id=?", sid), 1,
                             f"bill {i + 1} did not reach B's copy within 8 s")
            took.append(ms)
            time.sleep(1.2)                    # let B's busy tick pass
        self.assertGreaterEqual(w.hints, 3, "B's worker was not woken by the server's hints")
        for ms in took:
            self.assertLess(ms, worker.INTERVAL_IDLE * 1000, f"{ms:.0f} ms: the bill came by the slow tick")
        ROWS.append(("(A save -> B copy, workers on)", 0, 0, float("nan"), sum(took) / len(took)))
        print("\nA save -> B's copy with the real worker + hint socket: %s ms" % ", ".join("%.0f" % t for t in took))

    @classmethod
    def tearDownClass(cls):
        if not ROWS:
            return
        print("\n\n%-28s %8s %8s %8s %8s" % ("A -> server -> B (ms)", "save", "push", "hint", "pull"))
        for label, save_ms, push_ms, hint_ms, pull_ms in ROWS:
            h = "-" if hint_ms != hint_ms else "%8.0f" % hint_ms
            print("%-28s %8.0f %8.0f %8s %8.0f" % (label, save_ms, push_ms, h, pull_ms))
        print()


if __name__ == "__main__":
    unittest.main()
