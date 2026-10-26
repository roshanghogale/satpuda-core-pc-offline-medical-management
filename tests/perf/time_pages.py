"""Time what each page asks the engine for when it opens (a running engine, any port but 8765).

usage: python tests/perf/time_pages.py --port 8811 [--out timings.json] [--repeat 2]

The calls and their parameters are the ones the desktop UI makes on open (desktop/src pages).
Each call is made ``--repeat`` times; the first (cold) and the best of the rest (warm) are kept,
with the answer's size. ``--dump DIR`` also writes each answer, so two runs (before / after a
change) can be compared field by field with compare_answers.py.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date

TODAY = date.today()
FY_START = date(TODAY.year if TODAY.month >= 4 else TODAY.year - 1, 4, 1)
MONTH_START = TODAY.replace(day=1)
_last_month_end = MONTH_START.fromordinal(MONTH_START.toordinal() - 1)
LAST_MONTH = (_last_month_end.replace(day=1).isoformat(), _last_month_end.isoformat())
SORT = "Recent (newest first)"


def calls() -> list[tuple[str, str, str, dict | None]]:
    """(page, method, path, body)."""
    t = TODAY.isoformat()
    q = urllib.parse.quote
    return [
        ("Startup", "GET", "/api/health", None),
        ("Startup", "GET", "/api/license/status", None),
        ("Startup", "GET", "/api/login/status", None),
        ("Startup", "GET", "/api/meta", None),
        ("Startup", "GET", "/api/prefs", None),
        ("Startup", "GET", "/api/startup/alerts", None),
        ("Startup", "GET", "/api/sync/status", None),
        ("Startup", "GET", "/api/offline-first/status", None),
        ("Startup", "GET", "/api/wholesale/state", None),
        ("Startup", "GET", "/api/settings/layout_lists", None),
        ("Startup", "GET", "/api/settings/page_summaries", None),
        ("Home", "GET", "/api/home/dashboard", None),
        ("Sales History", "GET", f"/api/sales/history?sort={q(SORT)}", None),
        ("Sales History", "GET", "/api/sales/form", None),
        ("Purchase History", "GET", f"/api/purchase/history?sort={q(SORT)}", None),
        ("Purchase History", "GET", "/api/purchase/form", None),
        ("Inventory", "GET", "/api/inventory?low=0", None),
        ("General products", "GET", "/api/general-products", None),
        ("Returns", "GET", "/api/returns/summary", None),
        ("Returns", "GET", "/api/returns/sales/bills", None),
        ("Returns", "GET", "/api/medicines/search?limit=200", None),
        ("Settings", "GET", "/api/settings/bundle", None),
        ("Settings", "POST", "/api/settings/pharmacy/action", {"action": "get_signature"}),
        ("Contacts", "GET", "/api/settings/contacts", None),
        ("Alerts", "GET", "/api/settings/alerts", None),
        ("Alerts", "GET", "/api/settings/alert_prefs", None),
        ("Payments", "GET", "/api/settings/payments?kind=supplier", None),
        ("Payments", "GET", "/api/settings/payments?kind=customer", None),
        ("Ledger", "GET", f"/api/settings/ledger?kind=supplier&party=&from=&to={t}", None),
        ("Ledger", "GET", f"/api/settings/ledger?kind=customer&party=&from=&to={t}", None),
        ("Reorder", "GET", "/api/settings/reorder", None),
        ("Reorder", "POST", "/api/settings/reorder/action", {"action": "bulk_tabs"}),
        ("Shelf", "GET", "/api/settings/shelf", None),
        ("GST reports", "GET", f"/api/reports/gst/catalogue?from={LAST_MONTH[0]}&to={LAST_MONTH[1]}&mode=retail", None),
        ("GST reports", "GET", f"/api/reports/gst/screen?report=sale&from={LAST_MONTH[0]}&to={LAST_MONTH[1]}", None),
        ("Cash & Expenses", "GET", f"/api/cash/day?date={t}", None),
        ("Cash & Expenses", "GET", f"/api/cash/range?from={MONTH_START.isoformat()}&to={t}", None),
        ("Doctor margin", "GET", "/api/reports/doctor-margin/options", None),
        ("Doctor margin", "POST", "/api/reports/doctor-margin",
         {"from": FY_START.isoformat(), "to": t, "doctors": [], "rate_basis": "sale", "view": "doctor"}),
        ("Doctor margin", "POST", "/api/reports/doctor-margin",
         {"from": FY_START.isoformat(), "to": t, "doctor": "", "mode": "regular", "pct": 10, "types": [],
          "style": "summary"}),
    ]


def hit(base: str, method: str, path: str, body, timeout: float = 600):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"content-type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    return (time.perf_counter() - t0) * 1000.0, status, raw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out")
    ap.add_argument("--dump")
    ap.add_argument("--only", help="substring of page or path")
    ap.add_argument("--skip", action="append", default=[], help="substring of page or path to leave out")
    ap.add_argument("--timeout", type=float, default=600, help="seconds per call (a slower one is noted)")
    ap.add_argument("--customer", help="a customer for the Ledger with a party picked")
    ap.add_argument("--supplier", help="a supplier for the Ledger with a party picked")
    ap.add_argument("--sale-id", type=int, help="a bill to open from Sales History")
    a = ap.parse_args(argv)
    assert a.port != 8765, "never the shop engine's port"
    base = f"http://127.0.0.1:{a.port}"
    rows = []
    todo = calls()
    t = TODAY.isoformat()
    q = urllib.parse.quote
    if a.customer:
        todo.append(("Ledger", "GET", f"/api/settings/ledger?kind=customer&party={q(a.customer)}&from=&to={t}", None))
    if a.supplier:
        todo.append(("Ledger", "GET", f"/api/settings/ledger?kind=supplier&party={q(a.supplier)}&from=&to={t}", None))
    if a.sale_id:
        todo.append(("Sales History", "GET", f"/api/sales/bill/details?sale_id={a.sale_id}", None))
    for i, (page, method, path, body) in enumerate(todo):
        if a.only and a.only.lower() not in (page + path).lower():
            continue
        if any(x.lower() in (page + path).lower() for x in a.skip):
            continue
        times, size, status, raw = [], 0, 0, b""
        for _ in range(max(1, a.repeat)):
            try:
                ms, status, raw = hit(base, method, path, body, a.timeout)
            except (TimeoutError, OSError) as exc:
                ms, status, raw = a.timeout * 1000.0, f"timeout ({exc.__class__.__name__})", b""
                times.append(ms)
                break
            times.append(ms)
            size = len(raw)
        warm = min(times[1:]) if len(times) > 1 else times[0]
        rows.append({"page": page, "method": method, "path": path, "status": status, "cold_ms": round(times[0]),
                     "warm_ms": round(warm), "kb": round(size / 1024, 1)})
        print(f"{page:17s} {method:4s} {path[:70]:70s} {status} cold {times[0]:8.0f} ms  warm {warm:8.0f} ms"
              f"  {size / 1024:9.1f} KB", flush=True)
        if a.dump:
            os.makedirs(a.dump, exist_ok=True)
            with open(os.path.join(a.dump, f"{i:02d}.json"), "wb") as fh:
                fh.write(raw)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
