"""Build a big store database for speed checks -- through the app's own copy path.

The store is written exactly as a shop PC gets it when it goes offline-first: the real
``online_migrate.download_store_for_offline`` (schema, migrations, every document through
``apply_server_doc``, stock corrected from the server's figures, FY serials), with the server's
pages handed over from here instead of the network; then the offline-first bookkeeping (tables,
triggers, a device registration, number blocks), as ``runtime.register_and_prepare`` leaves it.

Sources:
  * synthetic (default) -- made-up shop at a chosen size, the same every run (seeded):
        --preset shrihari   64,032 medicines, 8,539 customers, 13,115 bills, 3,110 purchases
        --preset lakh       5,00,000 medicines, 40,000 customers, 1,00,000 bills, 20,000 purchases
  * --bundle DIR -- a folder of server-format JSON pages (NNN_<collection>.json, each
        {"<collection>": [docs]}). Real shop data stays on the machine it was given on: never
        commit or upload such a folder or the database made from it.

Usage (from the repo root, or anywhere with --root):
  python tests/perf/make_big_store.py --out C:/tmp/perfdata --preset shrihari
  python tests/perf/make_big_store.py --out C:/tmp/perfdata5l --preset lakh
  python tests/perf/make_big_store.py --out C:/tmp/realcopy --bundle D:/somewhere/stable

``--out`` is an app-data folder: it gets stores/Store_<name>/veterinary.db, the store registry
and sync_mode.txt = offline_first, so an engine whose config folder is ``--out`` opens it.
Nothing here talks to a server.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterator
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PRESETS = {
    # Shrihari Medical Jalna after its Medivision import (Oct 2026).
    "shrihari": dict(medicines=64032, names=55700, stock_share=0.08, customers=8539, suppliers=64,
                     doctors=21, sales=13115, sale_lines=5.1, purchases=3110, purchase_lines=6,
                     sales_returns=268, purchase_returns=40, customer_payments=900,
                     supplier_payments=1200),
    # What the PC must still carry: 5 lakh medicines, 1 lakh bills.
    "lakh": dict(medicines=500000, names=300000, stock_share=0.08, customers=40000, suppliers=300,
                 doctors=120, sales=100000, sale_lines=5.0, purchases=20000, purchase_lines=8,
                 sales_returns=2000, purchase_returns=300, customer_payments=6000,
                 supplier_payments=8000),
    # A quick one for tests.
    "tiny": dict(medicines=600, names=450, stock_share=0.5, customers=120, suppliers=8, doctors=5,
                 sales=400, sale_lines=3.0, purchases=60, purchase_lines=4, sales_returns=12,
                 purchase_returns=4, customer_payments=30, supplier_payments=30),
}

TYPES = [("Tablet", 41), ("Liquid", 19), ("Others", 15), ("Capsule", 8), ("Cream", 6),
         ("Injection - Vial", 5), ("Drops", 4), ("Powder", 2)]
UNITS = {"Tablet": ["10", "15", "6", "30"], "Capsule": ["10", "15"], "Liquid": ["100 ML", "60 ML", "200 ML"],
         "Cream": ["15 GM", "30 GM"], "Injection - Vial": ["VIAL", "1 ML"], "Drops": ["10 ML", "15 ML"],
         "Powder": ["100 GM", "200 GM"], "Others": ["1", "PCS"]}
MAKERS = ["CIPLA LIMITED", "SUN PHARMA", "MANKIND PHARMACEUTICALS LTD", "ALKEM LABORATORIES LTD",
          "DR REDDY LABS", "LUPIN LTD", "ZYDUS CADILA", "TORRENT PHARMA", "INTAS PHARMA", "ABBOTT INDIA",
          "GLENMARK", "MICRO LABS", "FDC LIMITED", "BAXTER", "ARISTO PHARMA", "IPCA LABS"]
SYL = ["AZ", "MO", "XI", "CEF", "PAN", "DOL", "RA", "TOR", "LEV", "ZE", "NO", "VA", "LO", "SAR", "TAN",
       "MET", "FOR", "GLI", "PRI", "DEX", "AMO", "CLA", "VIT", "CAL", "ZIN", "BET", "ON", "DOM", "SI", "FLU"]
STRENGTH = ["", " 5", " 10", " 20", " 40", " 50", " 100", " 250", " 500", " 625", " 650", " DS", " SP", " CV",
            " MR", " XR", " PLUS", " FORTE", " KID"]
SUFFIX = {"Tablet": " TAB", "Capsule": " CAP", "Liquid": " SYRUP", "Cream": " CREAM",
          "Injection - Vial": " INJ", "Drops": " DROPS", "Powder": " POWDER", "Others": ""}
SCHEDULES = ["H", "H", "H", "G", "", "H1", "X", ""]
FIRST = ["RAJESH", "SUNITA", "GANESH", "PRIYA", "SANTOSH", "ANITA", "VIJAY", "KAVITA", "SURESH", "MEENA",
         "RAMESH", "SHOBHA", "ASHOK", "LATA", "PRAKASH", "ASHA", "DINESH", "USHA", "SACHIN", "POOJA"]
LAST = ["PATIL", "JADHAV", "DESHMUKH", "KALE", "PAWAR", "SHINDE", "MORE", "GAIKWAD", "WAGH", "BHOSALE",
        "KULKARNI", "JOSHI", "DETHE", "MAHURE", "GAYKE", "SALVE", "RATHOD", "CHAVAN", "THORAT", "INGLE"]
VILLAGES = ["JALNA", "KADRABAD", "AMBAD", "BADNAPUR", "PARTUR", "BHOKARDAN", "GHANSAWANGI", "MANTHA"]


def _fy(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def _fy_tag(fy: int) -> str:
    return f"FY{fy}-{(fy + 1) % 100:02d}"


def _stamp(i: int) -> str:
    """A distinct server updated_at per document (ms resolution, like the server sends)."""
    base = datetime(2026, 10, 25, tzinfo=timezone.utc)
    return (base + timedelta(milliseconds=i)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Synthetic:
    """A made-up shop, the same every run for the same size and seed."""

    def __init__(self, sizes: dict, seed: int = 20261026, end: date | None = None):
        self.s = dict(sizes)
        self.seed = seed
        self.end = end or date(2026, 10, 25)
        self.start = date(2025, 4, 1)
        self._ts = 0
        rnd = random.Random(seed)
        # medicine masters: names, then batch rows over them
        n_names = int(self.s["names"])
        self.names: list[tuple[str, str, str, str, str]] = []
        seen: set[str] = set()
        while len(self.names) < n_names:
            t = rnd.choices([t for t, _ in TYPES], weights=[w for _, w in TYPES])[0]
            base = "".join(rnd.choice(SYL) for _ in range(rnd.randint(2, 3)))
            nm = base + rnd.choice(STRENGTH) + SUFFIX[t]
            if nm in seen:
                nm = f"{nm} {len(self.names)}"
            seen.add(nm)
            self.names.append((nm, t, rnd.choice(UNITS[t]), rnd.choice(MAKERS), rnd.choice(SCHEDULES)))
        self.meds: list[dict] = []
        n_meds = int(self.s["medicines"])
        for i in range(1, n_meds + 1):
            nm, t, unit, maker, sch = self.names[(i - 1) % n_names] if i <= n_names else \
                self.names[rnd.randrange(n_names)]
            mrp = round(rnd.uniform(8, 900), 2)
            exp = date(2025, 1, 31) + timedelta(days=rnd.randint(0, 1460))
            stock = rnd.randint(1, 300) if rnd.random() < float(self.s["stock_share"]) else 0
            self.meds.append({
                "name": nm, "type": t, "unit": unit, "gst_percent": rnd.choice([5.0, 12.0, 18.0, 0.0]),
                "hsn_code": "3004", "manufacturer": maker, "schedule": sch, "content_drug": "",
                "location": "", "supplier_name": "", "is_hidden": False,
                "batch_no": f"B{rnd.randint(10000, 99999)}{chr(65 + i % 26)}",
                "expiry_date": exp.isoformat(), "mrp": mrp, "rate": round(mrp * rnd.uniform(0.6, 0.8), 2),
                "stock_qty": stock, "id": i,
            })
        self.sellable = [m for m in self.meds if m["stock_qty"] > 0] or self.meds[:50]
        self.customers = []
        for i in range(1, int(self.s["customers"]) + 1):
            self.customers.append({
                "name": f"{rnd.choice(FIRST)} {rnd.choice(FIRST)} {rnd.choice(LAST)} {i}",
                "phone": f"9{rnd.randint(100000000, 999999999)}" if rnd.random() < 0.7 else "",
                "address": rnd.choice(VILLAGES), "total_due": 0, "total_credit": 0, "id": i,
            })
        self.suppliers = [{
            "name": f"{rnd.choice(LAST)} {rnd.choice(['AGENCIES', 'DISTRIBUTORS', 'PHARMA', 'MEDICAL AGENCY'])} {i}",
            "address": rnd.choice(VILLAGES), "phone": f"02482{rnd.randint(100000, 999999)}",
            "gstin": f"27AAAAA{i:04d}A1Z5", "dl_numbers": "", "total_due": 0, "total_credit": 0, "id": i,
        } for i in range(1, int(self.s["suppliers"]) + 1)]
        self.doctors = [{"name": f"DR {rnd.choice(FIRST)} {rnd.choice(LAST)} {i}", "phone": "",
                         "registration_number": "", "id": i} for i in range(1, int(self.s["doctors"]) + 1)]
        days = (self.end - self.start).days
        self._dates = sorted(self.start + timedelta(days=rnd.randint(0, days)) for _ in range(int(self.s["sales"])))
        self._pdates = sorted(self.start + timedelta(days=rnd.randint(0, days))
                              for _ in range(int(self.s["purchases"])))

    def _common(self, doc: dict, created: str = "2026-10-25T00:00:00.000Z") -> dict:
        self._ts += 1
        doc.setdefault("version", 1)
        doc.update(device_id="PERF-GENERATOR", created_at=created, updated_at=_stamp(self._ts), deleted=False)
        return doc

    def docs(self, col: str) -> Iterator[dict]:
        fn = getattr(self, "_" + col, None)
        if fn is None:
            return iter(())
        return fn()

    def _medicines(self):
        for m in self.meds:
            yield self._common(dict(m))

    def _customers(self):
        for c in self.customers:
            yield self._common(dict(c))

    def _suppliers(self):
        for s in self.suppliers:
            yield self._common(dict(s))

    def _doctors(self):
        for d in self.doctors:
            yield self._common(dict(d))

    def _sales(self):
        rnd = random.Random(self.seed + 1)
        serial: dict[int, int] = {}
        for i, d in enumerate(self._dates, 1):
            fy = _fy(d)
            serial[fy] = serial.get(fy, 0) + 1
            n = max(1, int(rnd.expovariate(1.0 / float(self.s["sale_lines"]))) + 1)
            items, total = [], 0.0
            for _ in range(min(n, 25)):
                m = rnd.choice(self.sellable)
                qty = rnd.randint(1, 10)
                rate = round(m["mrp"] * rnd.uniform(0.85, 1.0), 3)
                amt = round(qty * rate, 2)
                total += amt
                items.append({"medicine_id": m["id"], "name": m["name"], "type": m["type"],
                              "batch_no": m["batch_no"], "expiry_date": m["expiry_date"], "hsn_code": "3004",
                              "schedule": m["schedule"], "manufacturer": m["manufacturer"], "qty": qty,
                              "rate": rate, "amount": amt, "item_discount": 0.0,
                              "gst_percent": m["gst_percent"], "cost_price": m["rate"]})
            total = round(total, 2)
            walk_in = rnd.random() < 0.2
            cust = None if walk_in else rnd.choice(self.customers)
            credit = (not walk_in) and rnd.random() < 0.12
            paid = 0.0 if credit else total
            online = round(paid, 2) if (paid and rnd.random() < 0.3) else 0.0
            doc = {
                "bill_no": f"SCB{serial[fy]}/{_fy_tag(fy)}", "fy_start_year": fy, "fy_serial": serial[fy],
                "bill_date": d.isoformat(), "customer_id": cust["id"] if cust else None,
                "customer_name": cust["name"] if cust else "", "customer_phone": cust["phone"] if cust else "",
                "customer_address": cust["address"] if cust else "",
                "doctor_name": rnd.choice(self.doctors)["name"] if rnd.random() < 0.6 else "",
                "total_amount": total, "discount": 0.0, "discount_pct": 0.0, "rounding": 0.0,
                "amount_paid": paid, "cash_paid": round(paid - online, 2), "online_paid": online,
                "previous_due": 0, "previous_credit": 0, "due_amount": round(total - paid, 2),
                "credit_amount": 0.0, "total_due": round(total - paid, 2), "paid_due": 0,
                "bill_cleared": not credit, "account_cleared": not credit, "is_autosave": False,
                "items": items, "item_count": len(items), "id": i,
            }
            yield self._common(doc, created=f"{d.isoformat()}T10:{i % 60:02d}:00+05:30")

    def _purchases(self):
        rnd = random.Random(self.seed + 2)
        serial: dict[int, int] = {}
        for i, d in enumerate(self._pdates, 1):
            fy = _fy(d)
            serial[fy] = serial.get(fy, 0) + 1
            sup = rnd.choice(self.suppliers)
            items, sub, gst = [], 0.0, 0.0
            for _ in range(max(1, int(rnd.gauss(float(self.s["purchase_lines"]), 2)))):
                m = rnd.choice(self.meds)
                qty = float(rnd.randint(1, 30))
                taxable = round(qty * m["rate"], 2)
                g = round(taxable * m["gst_percent"] / 100.0, 2)
                sub += taxable
                gst += g
                items.append({"medicine_id": m["id"], "name": m["name"], "type": m["type"],
                              "batch_no": m["batch_no"], "expiry_date": m["expiry_date"], "hsn_code": "3004",
                              "manufacturer": m["manufacturer"], "qty": qty, "free_qty": 0.0, "rate": m["rate"],
                              "mrp": m["mrp"], "discount_pct": 0.0, "taxable": taxable,
                              "gst_pct": m["gst_percent"], "gst_amt": g, "item_amount": round(taxable + g, 2),
                              "unit": m["unit"], "tablets_per_stripe": 1, "schedule": m["schedule"]})
            total = round(sub + gst)
            paid = float(total) if rnd.random() < 0.3 else 0.0
            doc = {
                "purchase_no": f"{serial[fy]}/{_fy_tag(fy)}", "fy_start_year": fy, "fy_serial": serial[fy],
                "purchase_date": d.isoformat(), "bill_number": f"INV{rnd.randint(1000, 99999)}",
                "supplier_id": sup["id"], "supplier_name": sup["name"], "supplier_phone": sup["phone"],
                "subtotal": round(sub, 2), "total_gst": round(gst, 2), "cgst": round(gst / 2, 2),
                "sgst": round(gst / 2, 2), "total_amount": total, "final_amount": total, "need_to_pay": total,
                "overall_discount": 0.0, "rounding": round(total - sub - gst, 2), "amount_paid": paid,
                "amount_paid_at_entry": paid, "cash_paid_at_entry": paid, "online_paid_at_entry": 0,
                "previous_due": 0, "previous_credit": 0, "due": round(total - paid, 2), "current_credit": 0,
                "total_due": round(total - paid, 2), "due_amount": round(total - paid, 2), "credit_amount": 0,
                "paid_due": 0, "bill_cleared": paid >= total, "account_cleared": paid >= total,
                "gst_calc_method": "discount_before_gst", "expenditure": 0, "is_autosave": False,
                "items": items, "item_count": len(items), "id": i,
            }
            yield self._common(doc, created=f"{d.isoformat()}T12:00:00+05:30")

    def _sales_returns(self):
        rnd = random.Random(self.seed + 3)
        n_sales = len(self._dates)
        for i in range(1, int(self.s["sales_returns"]) + 1):
            sid = rnd.randint(1, n_sales)
            d = self._dates[sid - 1]
            m = rnd.choice(self.sellable)
            qty = float(rnd.randint(1, 3))
            amt = round(qty * m["mrp"], 2)
            doc = {"return_no": f"SR1-{i}", "sale_id": None, "bill_no": None, "customer_id": None,
                   "customer_name": "", "return_date": d.isoformat(), "refund_amount": amt, "discount": 0.0,
                   "reason": "perf", "settle_mode": "cash",
                   "items": [{"medicine_id": m["id"], "name": m["name"], "batch_no": m["batch_no"],
                              "qty": qty, "rate": m["mrp"], "amount": amt}], "item_count": 1, "id": i}
            yield self._common(doc, created=f"{d.isoformat()}T18:00:00+05:30")

    def _purchase_returns(self):
        rnd = random.Random(self.seed + 4)
        for i in range(1, int(self.s["purchase_returns"]) + 1):
            sup = rnd.choice(self.suppliers)
            m = rnd.choice(self.meds)
            d = self._pdates[rnd.randrange(len(self._pdates))]
            qty = float(rnd.randint(1, 10))
            amt = round(qty * m["rate"], 2)
            doc = {"return_no": f"PR1-{i}", "purchase_id": None, "purchase_no": None, "supplier_id": sup["id"],
                   "supplier_name": sup["name"], "return_date": d.isoformat(), "refund_amount": amt,
                   "discount": 0.0, "reason": "perf", "settle_mode": "ledger",
                   "items": [{"medicine_id": m["id"], "name": m["name"], "batch_no": m["batch_no"],
                              "qty": qty, "rate": m["rate"], "amount": amt}], "item_count": 1, "id": i}
            yield self._common(doc, created=f"{d.isoformat()}T18:00:00+05:30")

    def _customer_payments(self):
        rnd = random.Random(self.seed + 5)
        for i in range(1, int(self.s["customer_payments"]) + 1):
            c = rnd.choice(self.customers)
            d = self._dates[rnd.randrange(len(self._dates))]
            amt = round(rnd.uniform(50, 3000), 2)
            doc = {"customer_id": c["id"], "customer_name": c["name"], "payment_date": d.isoformat(),
                   "amount": amt, "payment_mode": "Cash", "cash_amount": amt, "online_amount": 0,
                   "reference_no": "", "note": "", "id": i}
            yield self._common(doc, created=f"{d.isoformat()}T19:00:00+05:30")

    def _supplier_payments(self):
        rnd = random.Random(self.seed + 6)
        for i in range(1, int(self.s["supplier_payments"]) + 1):
            s = rnd.choice(self.suppliers)
            d = self._pdates[rnd.randrange(len(self._pdates))]
            amt = round(rnd.uniform(500, 30000), 2)
            doc = {"supplier_id": s["id"], "supplier_name": s["name"], "payment_date": d.isoformat(),
                   "amount": amt, "mode": "Cash", "reference": "", "receipt_no": None, "due_before": 0.0,
                   "due_after": 0.0, "allocations": [], "id": i, "payment_no": f"PAY1-{i}"}
            yield self._common(doc, created=f"{d.isoformat()}T19:00:00+05:30")


class Bundle:
    """Server-format JSON pages from a folder (NNN_<collection>.json)."""

    def __init__(self, folder: str):
        self.files: dict[str, list[str]] = {}
        for p in sorted(glob.glob(os.path.join(folder, "*.json"))):
            m = re.match(r"\d+_(.+)\.json$", os.path.basename(p))
            if m:
                self.files.setdefault(m.group(1), []).append(p)

    def docs(self, col: str) -> Iterator[dict]:
        for p in self.files.get(col, []):
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            for d in data.get(col) or []:
                yield d


def _pages(source, col: str, size: int = 1000) -> Iterator[list]:
    page: list = []
    for d in source.docs(col):
        page.append(d)
        if len(page) >= size:
            yield page
            page = []
    if page:
        yield page


def build(out: str, source, *, store_name: str = "Perf Big", progress: bool = True,
          register: bool = True) -> str:
    """Write the store into app-data folder ``out``; returns the database path. ``register``:
    also make it the active store of ``out`` (the registry), for an engine to open."""
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from core import store_manager
    from core.store_manager import display_name_key

    key = display_name_key(store_name)
    db = os.path.join(out, "stores", key, "veterinary.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)

    from core import online_migrate as om
    from core import server_live
    from core import sync_bootstrap as boot
    from core import sync_watermarks as wm

    def fake_pages(token, collection, *, since=None, page_size=None, meta=None, **_kw):
        if meta is not None:
            meta["server_time"] = _stamp(10 ** 9)
        yield from _pages(source, collection)

    t0 = time.time()

    def say(msg):
        if progress:
            print(f"  [{time.time() - t0:6.1f}s] {msg}", flush=True)

    last = {"t": 0.0}

    def cb(msg):
        if progress and time.time() - last["t"] > 5:
            last["t"] = time.time()
            say(str(msg)[:100])

    patches = [
        mock.patch("core.license_manager._appdata_dir", lambda: out),
        mock.patch("core.store_manager.get_active_store_key", lambda: key),
        mock.patch("core.sync_prefs.get_sync_mode", lambda: "online"),
        mock.patch("core.sync_prefs.is_online_mode", lambda: True),
        mock.patch("core.server_live._token", lambda *a, **k: "perf"),
        mock.patch("core.server_live._iter_collection_pages", fake_pages),
        mock.patch("core.server_live.start_poller", lambda *a, **k: None),
        mock.patch("core.server_live.stop_poller", lambda *a, **k: None),
        mock.patch("core.online_migrate._token_for_pull", lambda: ""),
        mock.patch("core.offline_first.plain_docs.copy_from_server", lambda conn: 0),
        mock.patch("core.store_kv_carry.try_pull", lambda conn: {}),
        mock.patch.object(wm, "_path", lambda: os.path.join(out, "watermarks.json")),
        mock.patch.object(boot, "_done_path", lambda *a, **k: os.path.join(out, "bootstrap_done.txt")),
        mock.patch.object(om, "store_db_path", lambda *a, **k: db),
    ]
    for p in patches:
        p.start()
    try:
        say(f"copying into {db}")
        res = om.download_store_for_offline(db_path=db, progress_cb=cb)
        if not res.get("ok"):
            raise SystemExit(f"copy failed: {res}")
        say(f"copy done: {res.get('rows')} records")
        _register_offline_first(db)
        say("offline-first bookkeeping in place")
        if register:
            store_manager._register_existing_disk_store(key, store_name, activate=True)
    finally:
        for p in reversed(patches):
            p.stop()
    with open(os.path.join(out, "sync_mode.txt"), "w", encoding="utf-8") as fh:
        fh.write("offline_first")
    say("done")
    return db


def _register_offline_first(db: str) -> None:
    """What runtime.register_and_prepare leaves on the file, with a made-up registration."""
    from core.offline_first import numbers, schema
    from core.offline_first.runtime import _seed_versions, current_fy
    from core.offline_first.schema import meta_set

    conn = sqlite3.connect(db, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        schema.ensure_schema(conn)
        meta_set(conn, "install_id", "perf-generator-install")
        meta_set(conn, "device_no", 7)
        meta_set(conn, "id_base", 7_000_000_000)
        meta_set(conn, "id_max", 7_999_999_999)
        meta_set(conn, "server_last_seq", 0)
        meta_set(conn, "pull_cursor", 0)
        _seed_versions(conn)
        fy = current_fy()
        for kind, size in numbers.BLOCK_SIZE.items():
            tbl = "sales" if kind == "sales" else "purchases"
            top = conn.execute(
                f"SELECT COALESCE(MAX(fy_serial), 0) FROM {tbl} WHERE fy_start_year=?", (fy,)
            ).fetchone()[0]
            numbers.add_block(conn, kind, fy, int(top) + 1, int(top) + size)
        conn.commit()
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="app-data folder to write (stores/, registry, sync mode)")
    ap.add_argument("--preset", default="shrihari", choices=sorted(PRESETS))
    ap.add_argument("--bundle", help="folder of server-format JSON pages instead of the synthetic shop")
    ap.add_argument("--name", default="Perf Big", help="store display name")
    ap.add_argument("--seed", type=int, default=20261026)
    ap.add_argument("--root", help="app source folder to import core from (default: this repo)")
    a = ap.parse_args(argv)
    if a.root:
        global ROOT
        ROOT = os.path.abspath(a.root)
        sys.path.insert(0, ROOT)
    if a.bundle:
        src = Bundle(a.bundle)
    else:
        t = time.time()
        src = Synthetic(PRESETS[a.preset], seed=a.seed)
        print(f"  synthetic {a.preset} prepared in {time.time() - t:.1f}s", flush=True)
    db = build(a.out, src, store_name=a.name)
    c = sqlite3.connect(db)
    for t in ("medicines", "customers", "suppliers", "doctors", "sales", "sales_items", "purchases",
              "purchase_items", "sales_returns", "purchase_returns", "customer_payments", "supplier_payments"):
        try:
            print(f"  {t:18s} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>9,}")
        except sqlite3.Error as exc:
            print(f"  {t:18s} {exc}")
    c.close()
    print(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
