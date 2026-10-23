"""Offline-first audit: every save / edit / delete a shop does, on two PCs, through a REHEARSAL server.

Runs only when OF_TEST_BASE is set (an ssh tunnel to the rehearsal instance, never live):
    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 python -m pytest -q tests/test_offline_first_audit_server.py

Each test does one thing the shop does -- through the engine's own service functions, the ones
the screens call -- on PC A (or B), then both PCs sync, and checks:
  (a) the local rows (bills, lines, dues, cleared flags, party totals, stock),
  (b) every event of the PC that did it was accepted by the server (none left, none flagged),
  (c) the server's copy says the same,
  (d) syncing again changes nothing on that PC (no revert, nothing doubled),
  (e) the other PC holds the same rows, and the Sales / Purchase History and Payments answers
      read the same on both.
Every request is held to the rehearsal address: a call to any other server fails the test.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import tempfile
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

# Bookkeeping that differs between copies by design.
VOLATILE = {"created_at", "updated_at", "version", "device_id", "sync_status", "sync_state",
            "synced_at", "last_updated", "client_uuid", "is_autosave",
            # the bill's own copy of the party's name/phone: filled in from the party on a pull
            "customer_name", "customer_phone", "customer_address",
            # purchase_items' old twin columns (discount_pct / gst_pct are read first)
            "discount_percent", "gst_value"}
DOCS = ("sales", "purchases", "customer_payments", "supplier_payments", "sales_returns",
        "purchase_returns")
LINES = {"sales": ("sales_items", "sale_id"), "purchases": ("purchase_items", "purchase_id"),
         "sales_returns": ("sales_return_items", "return_id"),
         "purchase_returns": ("purchase_return_items", "return_id")}


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
        fd, self.path = tempfile.mkstemp(suffix=".db", prefix=f"ofa_{name}_")
        os.close(fd)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        db_setup.initialise(conn)
        conn.commit()
        conn.close()
        self.token = _post("/api/auth/pair", {"android_key": KEY, "device_id": install, "device_type": "pc"})["data"]["token"]
        self.till = None
        self.cycle = worker.SyncCycle(self.path)

    @contextlib.contextmanager
    def acting(self):
        prev = _ACTIVE.get("pc")
        _ACTIVE["pc"] = self
        try:
            yield
        finally:
            _ACTIVE["pc"] = prev

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
        c.commit()
        self.till = c
        c.execute("INSERT INTO pharmacy_profile (name) SELECT 'AUDIT SHOP' WHERE NOT EXISTS (SELECT 1 FROM pharmacy_profile)")
        c.commit()

    def sync(self, rounds=2):
        with self.acting():
            for _ in range(rounds):
                self.cycle.run_once()

    def q(self, sql, *a):
        return [tuple(r) for r in self.till.execute(sql, a).fetchall()]

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


def _rows(conn, table, where="1", args=()):
    cur = conn.execute(f"SELECT * FROM {table} WHERE {where}", args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _clean(row: dict, drop=()) -> dict:
    out = {}
    for k, v in row.items():
        if k in VOLATILE or k in drop:
            continue
        if isinstance(v, float):
            v = round(v, 2)
        if v == "":
            v = None
        if k == "allocations" and v:
            try:
                v = sorted((int(a.get("bill_id") or 0), round(float(a.get("amount") or 0), 2))
                           for a in json.loads(v))
            except Exception:
                pass
        if k == "deleted":
            v = int(v or 0)
        out[k] = v
    return out


def fingerprint(conn) -> dict:
    fp: dict = {}
    for t in ("customers", "suppliers"):
        for r in _rows(conn, t):
            fp[(t, r["id"])] = _clean(r)
    for r in _rows(conn, "medicines"):
        fp[("medicines", r["id"])] = _clean(r)
    for t in DOCS:
        for r in _rows(conn, t):
            fp[(t, r["id"])] = _clean(r)
            if t in LINES:
                lt, fk = LINES[t]
                lines = sorted(
                    (tuple(sorted(_clean(x, drop=("id",)).items(), key=lambda kv: kv[0]))
                     for x in _rows(conn, lt, f"{fk}=? AND COALESCE(deleted,0)=0", (r["id"],))),
                    key=repr,
                )
                fp[(lt, r["id"])] = lines
    return fp


def diff(a: dict, b: dict, label_a="A", label_b="B") -> list[str]:
    out = []
    for k in sorted(set(a) | set(b), key=repr):
        if k not in a:
            if not (isinstance(b[k], dict) and b[k].get("deleted")):  # deleted here, kept as deleted there
                out.append(f"{k}: only on {label_b}: {b[k]}")
        elif k not in b:
            if not (isinstance(a[k], dict) and a[k].get("deleted")):
                out.append(f"{k}: only on {label_a}: {a[k]}")
        elif a[k] != b[k]:
            if isinstance(a[k], dict):
                cols = {c: (a[k].get(c), b[k].get(c)) for c in set(a[k]) | set(b[k]) if a[k].get(c) != b[k].get(c)}
                out.append(f"{k}: {label_a}->{label_b} {cols}")
            else:
                out.append(f"{k}: lines {label_a}={a[k]} {label_b}={b[k]}")
    return out


def _item(mid, name, qty, rate, **extra):
    d = {"id": mid, "name": name, "qty": qty, "rate": rate, "amount": round(qty * rate, 2),
         "medicine_discount": 0, "unit": "1", "type": "Syrup", "batch": "B1", "gst_percent": 12}
    d.update(extra)
    return d


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class Audit(unittest.TestCase):
    """Two PCs of one shop, fresh for every test."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        from core import desktop_sales_service as sales_svc

        tok = lambda *a, **k: _ACTIVE["pc"].token if _ACTIVE.get("pc") else ""  # noqa: E731
        inst = lambda *a, **k: _ACTIVE["pc"].install if _ACTIVE.get("pc") else "none"  # noqa: E731
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
            # pulled shop settings must not rewrite this checkout's config files
            mock.patch("core.layout_config.save_layout", lambda *a, **k: None),
        ):
            stack.enter_context(p)
        run = uuid.uuid4().hex[:8].upper()
        self.run_id = run
        self.cust = f"AUD CUST {run}"
        self.supp = f"AUD AGENCY {run}"
        self.a = PC("A", f"pc-A-ofa-{run}")
        self.b = PC("B", f"pc-B-ofa-{run}")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.a.prepare()
        self.b.prepare()
        # Two medicines, made on A and on B's copy through a sync.
        self.m1 = self.medicine(self.a, f"AUD SYRUP {run}", 10.0, 100)
        self.m2 = self.medicine(self.a, f"AUD TAB {run}", 20.0, 100)
        self.settle()

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

    def pline(self, mid, name, qty, rate=5.0, free=0, **extra):
        # the medicine's own batch, expiry and MRP: the purchase adds to that row
        mrp = self.a.one("SELECT mrp FROM medicines WHERE id=?", mid)
        name = self.a.one("SELECT name FROM medicines WHERE id=?", mid)
        d = {"medicine_id": mid, "name": name, "type": "Syrup", "batch": "B1", "expiry": "12/28",
             "qty": qty, "rate": rate, "mrp": mrp, "unit": "1", "gst_pct": 0}
        if free:
            d["free_qty"] = free
        d.update(extra)
        return d

    def pay(self, pc, kind, amount, **extra):
        from core import desktop_settings_service as svc

        body = {"kind": kind, "party": self.cust if kind == "customer" else self.supp,
                "amount": amount, "mode": "Cash", "date": DAY}
        if kind == "customer":
            body["cash"] = amount
        body.update(extra)
        with pc.acting():
            res = svc.save_payment(pc.till, body)
        self.assertTrue(res.get("ok", True), res)
        return res

    def cid(self, pc):
        return pc.one("SELECT id FROM customers WHERE name=?", self.cust)

    def sid(self, pc):
        return pc.one("SELECT id FROM suppliers WHERE name=?", self.supp)

    def party(self, pc, kind="customer"):
        t = "customers" if kind == "customer" else "suppliers"
        name = self.cust if kind == "customer" else self.supp
        r = pc.till.execute(f"SELECT total_due, total_credit FROM {t} WHERE name=?", (name,)).fetchone()
        return (round(r[0] or 0, 2), round(r[1] or 0, 2)) if r else None

    def stock(self, pc, mid):
        return pc.one("SELECT stock_qty FROM medicines WHERE id=?", mid)

    # -- the checks ------------------------------------------------------------------------
    def settle(self):
        for _ in range(2):
            self.a.sync()
            self.b.sync()

    def screens(self, pc) -> dict:
        from core import desktop_pages_service as pages
        from core import desktop_settings_service as svc

        def norm(x):
            if isinstance(x, dict):
                return {k: norm(v) for k, v in x.items() if k not in VOLATILE and k not in (
                    "elapsed_ms", "generated_at", "took_ms", "cache", "ms")}
            if isinstance(x, list):
                return [norm(v) for v in x]
            if isinstance(x, float):
                return round(x, 2)
            return x

        with pc.acting():
            out = {
                "sales_history": norm(pages.list_sales_history(pc.till)),
                "purchase_history": norm(pages.list_purchase_history(pc.till)),
                "customer_payments": norm(svc.get_payments(pc.till, "customer")),
                "supplier_payments": norm(svc.get_payments(pc.till, "supplier")),
            }
        return out

    def server_mismatches(self, pc) -> list[str]:
        out = []
        fields = {
            "sales": ("total_amount", "amount_paid", "cash_paid", "online_paid", "due_amount",
                      "bill_cleared", "account_cleared", "discount", "rounding", "customer_id",
                      "doctor_name", "bill_no"),
            "purchases": ("total_amount", "final_amount", "amount_paid", "due_amount", "bill_cleared",
                          "account_cleared", "supplier_id", "bill_number"),
            "customer_payments": ("amount", "customer_id", "cash_amount", "online_amount", "reference_no"),
            "supplier_payments": ("amount", "supplier_id", "reference"),
            "sales_returns": ("refund_amount", "settle_mode", "customer_id", "sale_id"),
            "purchase_returns": ("refund_amount", "settle_mode", "supplier_id", "purchase_id"),
            "customers": ("total_due", "total_credit", "name"),
            "suppliers": ("total_due", "total_credit", "name"),
        }
        with pc.acting():
            tok = pc.token
            for t, cols in fields.items():
                for r in _rows(pc.till, t):
                    try:
                        doc = api.pull_doc(tok, t, int(r["id"]))
                    except Exception as exc:
                        out.append(f"server {t} {r['id']}: {exc}")
                        continue
                    deleted = int(r.get("deleted") or 0)
                    if doc is None:
                        if not deleted:
                            out.append(f"server {t} {r['id']}: missing")
                        continue
                    sdel = 1 if doc.get("deleted") in (True, 1, "1", "true") else 0
                    if sdel != deleted:
                        out.append(f"server {t} {r['id']}: deleted local={deleted} server={sdel}")
                        continue
                    if deleted:
                        continue
                    for c in cols:
                        if c not in r or c not in doc:
                            continue
                        lv, sv = r[c], doc.get(c)
                        try:
                            if isinstance(lv, (int, float)) or isinstance(sv, (int, float)):
                                if sv is True or sv is False:
                                    sv = int(sv)
                                if abs(float(lv or 0) - float(sv or 0)) > 0.01:
                                    out.append(f"server {t} {r['id']}.{c}: local={lv} server={sv}")
                                continue
                        except (TypeError, ValueError):
                            pass
                        if str(lv or "") != str(sv or ""):
                            out.append(f"server {t} {r['id']}.{c}: local={lv!r} server={sv!r}")
                    if t in LINES:
                        lt, fk = LINES[t]
                        loc = sorted((int(x["medicine_id"] or 0), round(float(x["qty"] or 0), 2))
                                     for x in _rows(pc.till, lt, f"{fk}=? AND COALESCE(deleted,0)=0", (r["id"],)))
                        srv = sorted((int(x.get("medicine_id") or 0), round(float(x.get("qty") or 0), 2))
                                     for x in (doc.get("items") or []) if not x.get("deleted"))
                        if loc != srv:
                            out.append(f"server {t} {r['id']} lines: local={loc} server={srv}")
            # stock
            mids = [r[0] for r in pc.q("SELECT id FROM medicines")]
            if mids:
                s = client.stock(mids)
                for mid, qty, _h in s.get("stock") or []:
                    loc = self.stock(pc, mid)
                    if loc is not None and int(loc) != int(qty):
                        out.append(f"server stock {mid}: local={loc} server={qty}")
        return out

    def events_problems(self, pc) -> list[str]:
        out = []
        for seq, col, op, status, outcome, code, detail in pc.q(
            "SELECT seq, collection, op, status, outcome, flag_code, flag_detail FROM of_events ORDER BY seq"
        ):
            if status != "sent":
                out.append(f"{pc.name} event {seq} {col}/{op} still {status}")
            elif col in ("customers", "suppliers") and code == "concurrent_edit":
                continue    # the party's due moved on both PCs; the server works it out again
            elif outcome not in ("applied", "duplicate", "duplicate_uuid"):
                out.append(f"{pc.name} event {seq} {col}/{op} {outcome} {code} {str(detail)[:200]}")
        n = pc.one("SELECT COUNT(*) FROM of_dirty")
        if n:
            out.append(f"{pc.name} of_dirty still holds {n}")
        return out

    def check(self, doer=None, label=""):
        """After an operation on `doer` (default A): sync both, then every check."""
        doer = doer or self.a
        other = self.b if doer is self.a else self.a
        before = fingerprint(doer.till)
        screens_before = self.screens(doer)
        self.settle()
        problems: list[str] = []
        problems += self.events_problems(self.a) + self.events_problems(self.b)
        after = fingerprint(doer.till)
        problems += [f"(d) {doer.name} changed by sync: {d}" for d in diff(before, after, "before", "after")]
        problems += [f"(e) {d}" for d in diff(fingerprint(doer.till), fingerprint(other.till), doer.name, other.name)]
        problems += [f"(c) {d}" for d in self.server_mismatches(doer)]
        s_doer, s_other = self.screens(doer), self.screens(other)
        for k in s_doer:
            if s_doer[k] != s_other[k]:
                problems.append(f"(e) screen {k} differs {doer.name} vs {other.name}: "
                                f"{json.dumps(s_doer[k], default=str)[:600]} | {json.dumps(s_other[k], default=str)[:600]}")
            if screens_before[k] != s_doer[k]:
                problems.append(f"(d) screen {k} on {doer.name} changed by sync: "
                                f"{json.dumps(screens_before[k], default=str)[:600]} | {json.dumps(s_doer[k], default=str)[:600]}")
        if problems:
            self.fail(f"{label}:\n  " + "\n  ".join(problems[:60]))

    # =====================================================================================
    # Sales
    # =====================================================================================
    def test_sale_new_cash(self):
        self.sell(self.a, [_item(self.m1, "S", 3, 10.0)])
        self.assertEqual(self.stock(self.a, self.m1), 97)
        self.check(label="sale new cash")
        self.assertEqual(self.stock(self.b, self.m1), 97)

    def test_sale_new_due_split_discount_rounding_doctor(self):
        res = self.sell(self.a, [_item(self.m1, "S", 3, 10.0), _item(self.m2, "T", 2, 20.0)],
                        payment_mode="Cash", pay_full=False, cash_paid=20, online_paid=15,
                        discount_rs=2, rounding=0.5, auto_rounding=False, doctor_name="DR AUDIT")
        self.assertEqual(self.party(self.a), (33.5, 0.0), res)
        self.check(label="sale with due + split + discount + rounding + doctor")
        self.assertEqual(self.party(self.b), (33.5, 0.0))

    def test_sale_due_then_edit_on_a(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        sid = res["sale_id"]
        self.sell(self.a, [_item(self.m1, "S", 2, 10.0), _item(self.m2, "T", 1, 20.0)], editing_sale_id=sid,
                  payment_mode="Cash", pay_full=False, cash_paid=10)
        self.assertEqual(self.stock(self.a, self.m1), 98)
        self.assertEqual(self.party(self.a), (30.0, 0.0))
        self.check(label="sale edit on A")
        self.assertEqual(self.stock(self.b, self.m1), 98)
        self.assertEqual(self.stock(self.b, self.m2), 99)

    def test_bill_paid_on_a_then_edited_on_b_after_sync(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        self.pay(self.a, "customer", 20)
        self.check(label="receipt on A")
        self.sell(self.b, [_item(self.m1, "S", 4, 10.0)], editing_sale_id=res["sale_id"],
                  payment_mode="Due", pay_full=False)
        self.check(doer=self.b, label="B edits the bill after seeing A's receipt")
        self.assertEqual(self.party(self.a), (20.0, 0.0))

    def test_sale_edit_on_the_other_pc(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        sid = res["sale_id"]
        self.assertEqual(self.party(self.b), (50.0, 0.0))
        self.sell(self.b, [_item(self.m1, "S", 2, 10.0)], editing_sale_id=sid,
                  payment_mode="Due", pay_full=False)
        self.assertEqual(self.party(self.b), (20.0, 0.0))
        self.check(doer=self.b, label="sale edit on B of A's bill")
        self.assertEqual(self.stock(self.a, self.m1), 98)
        self.assertEqual(self.party(self.a), (20.0, 0.0))

    def test_sale_delete_on_the_other_pc(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        from core import desktop_sales_service as sales_svc

        with self.b.acting():
            out = sales_svc.delete_saved_sale(self.b.till, {"sale_id": res["sale_id"]})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.stock(self.b, self.m1), 100)
        self.assertEqual(self.party(self.b), (0.0, 0.0))
        self.check(doer=self.b, label="sale delete on B")
        self.assertEqual(self.stock(self.a, self.m1), 100)
        self.assertEqual(self.party(self.a), (0.0, 0.0))

    def test_sale_delete_on_a(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)])
        from core import desktop_sales_service as sales_svc

        with self.a.acting():
            out = sales_svc.delete_saved_sale(self.a.till, {"sale_id": res["sale_id"]})
        self.assertTrue(out.get("ok"), out)
        self.check(label="sale new + delete before sync")
        self.assertEqual(self.stock(self.b, self.m1), 100)

    def test_sale_with_return_credit_applied(self):
        from core import desktop_returns_service as returns_svc

        s1 = self.sell(self.a, [_item(self.m1, "S", 3, 10.0)])
        with self.a.acting():
            ret = returns_svc.save_sales_return(self.a.till, {
                "sale_id": s1["sale_id"], "customer_id": self.cid(self.a), "settle_mode": "ledger",
                "items": [{"medicine_id": self.m1, "qty": 1, "rate": 10.0}]})
        self.assertTrue(ret.get("ok"), ret)
        self.settle()
        self.assertEqual(self.party(self.b), (0.0, 10.0))
        self.sell(self.b, [_item(self.m2, "T", 2, 20.0)], apply_returns=[ret["return_id"]])
        self.assertEqual(self.party(self.b), (0.0, 0.0))
        self.check(doer=self.b, label="sale on B using A's return credit")
        self.assertEqual(self.party(self.a), (0.0, 0.0))

    def test_sale_part_paid_then_paid_from_history_on_other_pc(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Cash", pay_full=False, cash_paid=20)
        self.settle()
        from core.history_pay import pay_bill

        with self.b.acting():
            pay_bill(self.b.till, {"kind": "sale", "id": res["sale_id"], "amount": 30, "mode": "Online"})
        self.assertEqual(self.party(self.b), (0.0, 0.0))
        self.assertEqual(self.b.one("SELECT bill_cleared FROM sales WHERE id=?", res["sale_id"]), 1)
        self.check(doer=self.b, label="sale paid from history on B")
        self.assertEqual(self.a.one("SELECT bill_cleared FROM sales WHERE id=?", res["sale_id"]), 1)

    def test_regular_medicines_list_reaches_the_other_pc(self):
        from core import regular_medicines as rm

        self.sell(self.a, [_item(self.m1, "S", 1, 10.0)])
        cid = self.cid(self.a)
        with self.a.acting():
            rm.save_regulars(self.a.till, cid, self.cust, "", [{"medicine_id": self.m1, "name": "S", "qty": 2}])
        self.check(label="regular medicines saved")
        with self.b.acting():
            got = rm.get_regulars(self.b.till, cid)
        self.assertEqual(len(got["items"]), 1, got)

    def test_wholesale_sale_new_edit_delete(self):
        import os as _os

        with mock.patch.dict(_os.environ, {"SATPUDA_WHOLESALE_OVERRIDE": "1"}):
            tab = self.medicine(self.a, f"AUD WS TAB {self.run_id}", 120.0, 500)
            self.a.till.execute("UPDATE medicines SET type='Tablet', unit='10', rate_a=90, rate_b=85, rate_c=80 WHERE id=?", (tab,))
            self.a.till.commit()
            self.settle()
            line = {"id": tab, "qty": 3, "free": 1, "rate": 90, "disc_pct": 10, "scheme_pct": 0,
                    "gst_percent": 12, "rate_tier": "A"}
            res = self.sell(self.a, [line], invoice_mode="wholesale", customer_id=0,
                            payment_mode="Due", pay_full=False)
            self.assertEqual(self.stock(self.a, tab), 460)
            self.check(label="wholesale sale new")
            sid = res["sale_id"]
            self.sell(self.b, [dict(line, qty=2)], invoice_mode="wholesale", customer_id=self.cid(self.b),
                      editing_sale_id=sid, payment_mode="Due", pay_full=False)
            self.assertEqual(self.stock(self.b, tab), 470)
            self.check(doer=self.b, label="wholesale sale edited on B")
            from core import desktop_sales_service as sales_svc

            with self.a.acting():
                out = sales_svc.delete_saved_sale(self.a.till, {"sale_id": sid})
            self.assertTrue(out.get("ok"), out)
            self.check(label="wholesale sale deleted on A")
            self.assertEqual(self.stock(self.b, tab), 500)
            self.assertEqual(self.party(self.b), (0.0, 0.0))

    def test_customer_refund_of_a_return(self):
        from core import desktop_returns_service as returns_svc
        from core import return_refunds

        s1 = self.sell(self.a, [_item(self.m1, "S", 3, 10.0)])
        with self.a.acting():
            ret = returns_svc.save_sales_return(self.a.till, {
                "sale_id": s1["sale_id"], "customer_id": self.cid(self.a), "settle_mode": "ledger",
                "items": [{"medicine_id": self.m1, "qty": 2, "rate": 10.0}]})
        self.assertTrue(ret.get("ok"), ret)
        self.settle()
        with self.b.acting():
            out = return_refunds.save_refund(self.b.till, {
                "kind": "customer", "party": self.cust, "mode": "cash", "date": DAY,
                "refunds": [{"return_id": ret["return_id"], "amount": 15}]})
        self.assertEqual(self.party(self.b), (0.0, 5.0))
        self.check(doer=self.b, label="refund of a return on B")
        from core import desktop_settings_service as svc

        with self.a.acting():
            svc.delete_payment(self.a.till, {"kind": "customer", "id": out["payment_ids"][0]})
        self.assertEqual(self.party(self.a), (0.0, 20.0))
        self.check(label="refund deleted on A")

    # -- both PCs working at the same time, before either syncs ------------------------------
    def check_both(self, label):
        """Both PCs changed things: sync, then both PCs and the server must agree."""
        self.settle()
        self.settle()
        problems = self.events_problems(self.a) + self.events_problems(self.b)
        problems += [f"(e) {d}" for d in diff(fingerprint(self.a.till), fingerprint(self.b.till))]
        problems += [f"(c) {d}" for d in self.server_mismatches(self.a)]
        sa, sb = self.screens(self.a), self.screens(self.b)
        for k in sa:
            if sa[k] != sb[k]:
                problems.append(f"(e) screen {k} differs")
        if problems:
            self.fail(f"{label}:\n  " + "\n  ".join(problems[:60]))

    def test_concurrent_due_sales_for_one_customer(self):
        self.sell(self.a, [_item(self.m1, "S", 1, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.sell(self.b, [_item(self.m2, "T", 3, 20.0)], payment_mode="Due", pay_full=False)
        self.check_both("two PCs sell on due to one customer at once")
        self.assertEqual(self.party(self.a), (120.0, 0.0))
        self.assertEqual(self.party(self.b), (120.0, 0.0))
        self.assertEqual(self.stock(self.a, self.m1), 94)

    def test_concurrent_walk_in_sales_on_both_pcs(self):
        """Walk-in (COUNTER SALE) lines are merged into the day's counter bill. Two counters
        selling at once must not lose either one's lines."""
        self.sell(self.a, [_item(self.m1, "S", 1, 10.0)], customer_name="")
        self.settle()
        self.sell(self.a, [_item(self.m1, "S", 2, 10.0)], customer_name="")
        self.sell(self.b, [_item(self.m2, "T", 3, 20.0)], customer_name="")
        self.check_both("walk-in sales on both PCs at once")
        total = self.a.one("SELECT SUM(total_amount) FROM sales WHERE COALESCE(deleted,0)=0")
        self.assertEqual(total, 10 + 20 + 60)
        qty = self.a.q("SELECT medicine_id, SUM(qty) FROM sales_items si JOIN sales s ON s.id=si.sale_id "
                       "WHERE COALESCE(s.deleted,0)=0 GROUP BY medicine_id ORDER BY medicine_id")
        self.assertEqual(qty, sorted([(self.m1, 3), (self.m2, 3)]))
        self.assertEqual(self.stock(self.a, self.m1), 97)
        self.assertEqual(self.stock(self.a, self.m2), 97)

    def test_concurrent_payment_and_bill_edit(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        # A takes Rs 20 for the bill; B (not yet seeing it) edits the bill to 3 bottles.
        self.pay(self.a, "customer", 20)
        self.sell(self.b, [_item(self.m1, "S", 3, 10.0)], editing_sale_id=res["sale_id"],
                  payment_mode="Due", pay_full=False)
        # B's edit is flagged on the server (the receipt's dues cascade moved the bill's
        # version under it); nothing is lost: the bill is B's and the dues are worked out again.
        try:
            self.check_both("payment on A while B edits the bill")
        except AssertionError as exc:
            rest = [ln for ln in str(exc).splitlines()[1:] if "sales/upsert flagged concurrent_edit" not in ln]
            if rest:
                raise
        for pc in (self.a, self.b):
            self.assertEqual(pc.q("SELECT medicine_id, qty FROM sales_items WHERE sale_id=?", res["sale_id"]),
                             [(self.m1, 3)], pc.name)
        self.assertEqual(self.party(self.a), (10.0, 0.0))
        self.assertEqual(self.stock(self.a, self.m1), 97)

    def test_a_receipt_on_one_pc_never_undoes_a_bill_edit_on_the_other(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        self.sell(self.b, [_item(self.m1, "S", 3, 10.0)], editing_sale_id=res["sale_id"],
                  payment_mode="Due", pay_full=False)
        self.pay(self.a, "customer", 20)
        self.b.sync()          # B's edit reaches the server first,
        self.a.sync()          # then A's receipt
        self.check_both("receipt on A after B's edit reached the server")
        for pc in (self.a, self.b):
            self.assertEqual(pc.q("SELECT medicine_id, qty FROM sales_items WHERE sale_id=?", res["sale_id"]),
                             [(self.m1, 3)], pc.name)
            self.assertEqual(self.party(pc), (10.0, 0.0), pc.name)
        self.assertEqual(self.stock(self.a, self.m1), 97)

    def test_a_supplier_payment_never_undoes_a_purchase_edit_on_the_other_pc(self):
        res = self.buy(self.a, f"P{self.run_id}", [self.pline(self.m1, "S", 10)])
        self.settle()
        from core import desktop_purchase_service as purchase_svc

        with self.b.acting():
            out = purchase_svc.save_purchase_bill(self.b.till, {
                "supplier_name": self.supp, "bill_number": f"P{self.run_id}", "purchase_date": DAY,
                "items": [self.pline(self.m1, "S", 6)], "cash_paid": 0, "rounding": 0,
                "editing_purchase_id": res["purchase_id"]})
        self.assertTrue(out.get("ok"), out)
        self.pay(self.a, "supplier", 10)
        self.b.sync()
        self.a.sync()
        self.check_both("supplier payment on A after B's purchase edit reached the server")
        for pc in (self.a, self.b):
            self.assertEqual(pc.q("SELECT medicine_id, qty FROM purchase_items WHERE purchase_id=?",
                                  res["purchase_id"]), [(self.m1, 6.0)], pc.name)
            self.assertEqual(self.party(pc, "supplier"), (20.0, 0.0), pc.name)
        self.assertEqual(self.stock(self.a, self.m1), 106)

    def test_concurrent_purchase_and_supplier_payment(self):
        self.buy(self.a, f"P{self.run_id}a", [self.pline(self.m1, "S", 10)])
        self.settle()
        self.buy(self.a, f"P{self.run_id}b", [self.pline(self.m2, "T", 4)])
        self.pay(self.b, "supplier", 30)
        self.check_both("purchase on A while B pays the agency")
        self.assertEqual(self.party(self.a, "supplier"), (40.0, 0.0))

    def test_concurrent_sale_on_both_then_return_on_one(self):
        from core import desktop_returns_service as returns_svc

        s1 = self.sell(self.a, [_item(self.m1, "S", 4, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        with self.a.acting():
            r = returns_svc.save_sales_return(self.a.till, {
                "sale_id": s1["sale_id"], "customer_id": self.cid(self.a), "settle_mode": "ledger",
                "items": [{"medicine_id": self.m1, "qty": 1, "rate": 10.0}]})
        self.assertTrue(r.get("ok"), r)
        self.sell(self.b, [_item(self.m1, "S", 2, 10.0)], payment_mode="Due", pay_full=False)
        self.check_both("return on A while B sells on due")
        self.assertEqual(self.party(self.a), (50.0, 0.0))
        self.assertEqual(self.stock(self.a, self.m1), 95)

    # =====================================================================================
    # Purchases
    # =====================================================================================
    def test_purchase_new_paid_at_entry_free_qty(self):
        self.buy(self.a, f"P{self.run_id}", [self.pline(self.m1, "S", 10, free=2)], cash_paid=20)
        self.assertEqual(self.stock(self.a, self.m1), 112)
        self.check(label="purchase new, part paid, free qty")
        self.assertEqual(self.stock(self.b, self.m1), 112)
        self.assertEqual(self.party(self.b, "supplier"), self.party(self.a, "supplier"))

    def test_purchase_with_line_discount_gst_and_after_gst_method(self):
        line = self.pline(self.m1, "S", 10, rate=6.0, gst_pct=12, discount_pct=10, hsn_code="3004")
        res = self.buy(self.a, f"P{self.run_id}", [line], gst_calc_method="discount_after_gst")
        pid = res["purchase_id"]
        before = self.a.q("SELECT discount_pct, taxable, gst_amt, item_amount FROM purchase_items WHERE purchase_id=?", pid)
        self.assertTrue(before[0][0] and before[0][1], before)
        self.check(label="purchase with line discount + GST + after-GST method")
        for pc in (self.a, self.b):
            self.assertEqual(pc.q("SELECT discount_pct, taxable, gst_amt, item_amount FROM purchase_items "
                                  "WHERE purchase_id=?", pid), before, pc.name)
            self.assertEqual(pc.one("SELECT gst_calc_method FROM purchases WHERE id=?", pid), "discount_after_gst")
        # opened for edit on B, it reads the same bill
        from core import desktop_purchase_service as purchase_svc

        with self.b.acting():
            form_b = purchase_svc.load_purchase(self.b.till, pid)
        with self.a.acting():
            form_a = purchase_svc.load_purchase(self.a.till, pid)
        self.assertEqual(json.dumps(form_a.get("form"), sort_keys=True, default=str),
                         json.dumps(form_b.get("form"), sort_keys=True, default=str))

    def test_purchase_edit_on_other_pc_with_supplier_change(self):
        res = self.buy(self.a, f"P{self.run_id}", [self.pline(self.m1, "S", 10)])
        self.settle()
        pid = res["purchase_id"]
        self.assertEqual(self.party(self.b, "supplier"), (50.0, 0.0))
        other = f"AUD AGENCY2 {self.run_id}"
        from core import desktop_purchase_service as purchase_svc

        with self.b.acting():
            out = purchase_svc.save_purchase_bill(self.b.till, {
                "supplier_name": other, "bill_number": f"P{self.run_id}", "purchase_date": DAY,
                "items": [self.pline(self.m1, "S", 6)], "cash_paid": 0, "rounding": 0,
                "editing_purchase_id": pid, "confirm_replace_purchase": True})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.stock(self.b, self.m1), 106)
        self.assertEqual(self.party(self.b, "supplier"), (0.0, 0.0))
        self.check(doer=self.b, label="purchase edit on B, supplier changed")
        self.assertEqual(self.stock(self.a, self.m1), 106)
        self.assertEqual(self.party(self.a, "supplier"), (0.0, 0.0))

    def test_purchase_delete(self):
        res = self.buy(self.a, f"P{self.run_id}", [self.pline(self.m1, "S", 10)])
        self.settle()
        from core import desktop_purchase_service as purchase_svc

        with self.b.acting():
            out = purchase_svc.delete_saved_purchase(self.b.till, {"purchase_id": res["purchase_id"]})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.stock(self.b, self.m1), 100)
        self.check(doer=self.b, label="purchase delete on B")
        self.assertEqual(self.stock(self.a, self.m1), 100)
        self.assertEqual(self.party(self.a, "supplier"), (0.0, 0.0))

    # =====================================================================================
    # Payments
    # =====================================================================================
    def test_customer_payment_allocated_then_edited_and_deleted_on_other_pc(self):
        s1 = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        s2 = self.sell(self.a, [_item(self.m2, "T", 2, 20.0)], payment_mode="Due", pay_full=False)
        out = self.pay(self.a, "customer", 40, allocations=[{"bill_id": s2["sale_id"], "amount": 40}])
        self.assertEqual(self.party(self.a), (50.0, 0.0))
        self.check(label="customer payment for a chosen bill")
        pid = out["payment_id"]
        self.pay(self.b, "customer", 70, id=pid)
        self.assertEqual(self.party(self.b), (20.0, 0.0))
        self.check(doer=self.b, label="customer payment edited on B")
        from core import desktop_settings_service as svc

        with self.a.acting():
            svc.delete_payment(self.a.till, {"kind": "customer", "id": pid})
        self.assertEqual(self.party(self.a), (90.0, 0.0))
        self.check(label="customer payment deleted on A")
        self.assertEqual(s1["sale_id"] != s2["sale_id"], True)

    def test_supplier_payment_and_pay_from_history(self):
        p1 = self.buy(self.a, f"P{self.run_id}a", [self.pline(self.m1, "S", 10)])
        self.buy(self.a, f"P{self.run_id}b", [self.pline(self.m2, "T", 4)])
        self.settle()
        out = self.pay(self.b, "supplier", 30)
        self.check(doer=self.b, label="supplier payment on B (oldest first)")
        from core.history_pay import pay_bill

        with self.a.acting():
            pay_bill(self.a.till, {"kind": "purchase", "id": p1["purchase_id"], "amount": 20})
        self.check(label="pay a purchase from history on A")
        self.pay(self.a, "supplier", 10, id=out["payment_id"])
        self.check(label="supplier payment edited on A")
        from core import desktop_settings_service as svc

        with self.b.acting():
            svc.delete_payment(self.b.till, {"kind": "supplier", "id": out["payment_id"]})
        self.check(doer=self.b, label="supplier payment deleted on B")

    # =====================================================================================
    # Returns
    # =====================================================================================
    def test_sales_return_ledger_cash_and_delete(self):
        from core import desktop_returns_service as returns_svc

        s1 = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        with self.b.acting():
            r1 = returns_svc.save_sales_return(self.b.till, {
                "sale_id": s1["sale_id"], "customer_id": self.cid(self.b), "settle_mode": "ledger",
                "items": [{"medicine_id": self.m1, "qty": 2, "rate": 10.0}]})
        self.assertTrue(r1.get("ok"), r1)
        self.assertEqual(self.stock(self.b, self.m1), 97)
        self.assertEqual(self.party(self.b), (30.0, 0.0))
        self.check(doer=self.b, label="sales return (credit) on B")
        with self.a.acting():
            r2 = returns_svc.save_sales_return(self.a.till, {
                "sale_id": s1["sale_id"], "customer_id": self.cid(self.a), "settle_mode": "cash",
                "items": [{"medicine_id": self.m1, "qty": 1, "rate": 10.0}]})
        self.assertTrue(r2.get("ok"), r2)
        self.check(label="sales return (cash refund) on A")
        with self.b.acting():
            d = returns_svc.delete_sales_return(self.b.till, {"id": r1["return_id"]})
        self.assertTrue(d.get("ok"), d)
        self.check(doer=self.b, label="sales return deleted on B")
        self.assertEqual(self.stock(self.a, self.m1), 96)

    def test_purchase_return_save_edit_delete(self):
        from core import desktop_returns_service as returns_svc

        p = self.buy(self.a, f"P{self.run_id}", [self.pline(self.m1, "S", 10)])
        self.settle()
        with self.b.acting():
            r = returns_svc.save_purchase_return(self.b.till, {
                "purchase_id": p["purchase_id"], "supplier_id": self.sid(self.b), "settle_mode": "ledger",
                "items": [{"medicine_id": self.m1, "qty": 3, "rate": 5.0}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.stock(self.b, self.m1), 107)
        self.check(doer=self.b, label="purchase return on B")
        with self.a.acting():
            r2 = returns_svc.replace_purchase_return(self.a.till, {
                "return_id": r["return_id"], "items": [{"medicine_id": self.m1, "qty": 5, "rate": 5.0}]})
        self.assertTrue(r2.get("ok"), r2)
        self.assertEqual(self.stock(self.a, self.m1), 105)
        self.check(label="purchase return edited on A")
        new_id = r2.get("return_id")
        with self.b.acting():
            d = returns_svc.delete_purchase_return(self.b.till, {"id": new_id})
        self.assertTrue(d.get("ok"), d)
        self.check(doer=self.b, label="purchase return deleted on B")
        self.assertEqual(self.stock(self.a, self.m1), 110)


if __name__ == "__main__":
    unittest.main()
