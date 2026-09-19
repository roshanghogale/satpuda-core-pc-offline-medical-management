"""Debug active DB + desktop_pages_service against real store."""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.store_manager import get_active_db_path, get_active_display_name
from core import desktop_pages_service as pages

p = get_active_db_path()
print("db", p)
print("exists", bool(p and os.path.isfile(p)))
print("store", get_active_display_name())
if not p or not os.path.isfile(p):
    raise SystemExit(1)

c = sqlite3.connect(p)
tables = [
    r[0]
    for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
    ).fetchall()
]
print("table_count", len(tables))
for t in tables:
    if any(
        x in t.lower()
        for x in (
            "med",
            "sale",
            "purch",
            "cust",
            "doc",
            "supp",
            "bill",
            "return",
            "invoice",
        )
    ):
        n = c.execute(f"SELECT COUNT(*) FROM [{t}]").fetchall()[0][0]
        cols = [r[1] for r in c.execute(f"PRAGMA table_info([{t}])").fetchall()]
        print(f"{t}: {n} rows; cols={cols[:16]}")

inv = pages.list_inventory(c)
print("list_inventory rows", len(inv.get("rows") or []), "summary", inv.get("summary"))
sh = pages.list_sales_history(c)
print("sales_history rows", len(sh.get("rows") or []), "summary", sh.get("summary"))
ph = pages.list_purchase_history(c)
print("purchase_history rows", len(ph.get("rows") or []), "summary", ph.get("summary"))
sf = pages.sales_form_defaults(c)
print(
    "sales_form customers",
    len(sf.get("customers") or []),
    "doctors",
    len(sf.get("doctors") or []),
    "modes",
    sf.get("payment_modes"),
)
pf = pages.purchase_form_defaults(c)
print("purchase_form suppliers", len(pf.get("suppliers") or []))
c.close()
