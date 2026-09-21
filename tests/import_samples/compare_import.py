"""Does a bill import land the same in the Tauri app as in the classic app?

    python tests\\import_samples\\compare_import.py [file ...]

Each file is imported twice, each time into its own fresh offline store:

  classic  the real Tk PurchasePage (hidden window): import_into_purchase_page
           applies the rows, recalculate_purchase_totals() works the bill out,
           and the page's own save arguments go to maybe_save_purchase.
  tauri    start_purchase_import -> apply_purchase_import (the engine's API),
           the rows through the page's REAL lineFromPayload/tabPayload (node),
           calc_purchase for the auto rounding, then save_purchase_bill.

Then the two stores are read back and compared: the purchase, every line, and
what reached the Inventory (stock, pack, expiry, supplier).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import core.sync_prefs as sync_prefs  # noqa: E402

sync_prefs.is_online_mode = lambda: False
sync_prefs.get_sync_mode = lambda: "offline"

from core import db_setup  # noqa: E402
from ts_bridge import page_roundtrip  # noqa: E402


def fresh_db() -> sqlite3.Connection:
    path = os.path.join(tempfile.mkdtemp(prefix="imp_cmp_"), "store.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db_setup.initialise(conn)
    conn.commit()
    return conn


def parse(path):
    from core.purchase_importer import parse_purchase_excel, parse_purchase_pdf

    return parse_purchase_pdf(path) if path.lower().endswith(".pdf") else parse_purchase_excel(path)


# ------------------------------------------------------------------ classic
_ROOT = None


def run_classic(path):
    global _ROOT
    import tkinter as tk

    from core.layout_config import get_med_types
    from core.medicine_type_detector import enrich_invoice_medicine_types
    from core.purchase_importer import import_into_purchase_page
    from core.purchase_service import get_or_create_supplier
    from ui.purchase.purchase import PurchasePage

    try:
        from core.sync_v3.repositories.purchase_repository import maybe_save_purchase
    except Exception:
        from core.purchase_service import save_purchase as maybe_save_purchase

    from core.keyboard_registry import KeyboardRegistry

    if not hasattr(KeyboardRegistry, "_grave_bind_cache"):
        KeyboardRegistry._grave_bind_cache = None  # set by the app's startup
    conn = fresh_db()
    if _ROOT is None:
        _ROOT = tk.Tk()
        _ROOT.withdraw()
    frame = tk.Frame(_ROOT)
    page = PurchasePage(frame, conn)

    # ImportPurchaseDialog._parse_worker + _load_invoice, then its Import button.
    invoice = parse(path)
    enrich_invoice_medicine_types(invoice, conn, get_med_types())  # in place
    # The preview refuses highlighted rows; the owner deletes them, then Import.
    bad = [it for it in invoice.items if not it.is_valid]
    for it in bad:
        print(f"  classic preview flags row {it.source_row} {it.name!r}: {'; '.join(it.issues)}")
    invoice.items = [it for it in invoice.items if it.is_valid]
    import_into_purchase_page(page, invoice, list(invoice.items), replace_existing=True)
    _ROOT.update()

    # save_purchase(): recalc, then _do_save with the page's own fields.
    page.recalculate_purchase_totals()
    result = page._last_calc
    if not page.supplier_name.get().strip():
        page.supplier_name.set("TEST SUPPLIER")  # the owner types it in
    supplier_id = get_or_create_supplier(
        conn, page.supplier_name.get().strip(), page.supplier_address.get(),
        page.supplier_phone.get(), page.supplier_gstin.get(), page.supplier_dl.get(),
    )
    maybe_save_purchase(conn, supplier_id, page.purchase_date.get().strip(),
                        page.bill_number.get().strip(), result, page.purchase_items)
    conn.commit()
    frame.destroy()
    return conn, len(invoice.items)


# ------------------------------------------------------------------ tauri
def run_tauri(path):
    from core import desktop_purchase_service as svc

    conn = fresh_db()
    started = svc.start_purchase_import(conn, {"paths": [path]})
    assert started.get("ok"), started
    token = started["import_token"]
    body = {"import_token": token}
    res = svc.apply_purchase_import(conn, body)
    while res.get("need_confirm"):  # the page answers Yes to each question
        print(f"  tauri asks [{res.get('code')}]: {res.get('message', '').splitlines()[0:2]}")
        body.update({"skip_invalid": True, "continue_count_mismatch": True})
        res = svc.apply_purchase_import(conn, body)
    assert res.get("ok"), res
    form = res["form"]

    # applyImportedForm(): the tab fields as the page sets them.
    tab = {
        # An EDI file carries no supplier; the owner types it in.
        "supplier": form.get("supplier_name") or "TEST SUPPLIER",
        "phone": form.get("supplier_phone") or "",
        "address": form.get("supplier_address") or "",
        "gstin": form.get("gstin") or "",
        "dl": form.get("dl") or "",
        "billNumber": form.get("bill_number") or "",
        "purchaseDate": form.get("purchase_date") or "",
        "gstMethod": form.get("gst_calc_method") or "discount_before_gst",
        "overallDisc": str(form.get("overall_discount") or 0),
        "overallDiscPct": str(form.get("discount_pct") or 0),
        "rounding": "0",
        "delivery": str(form.get("expenditure") or 0),
        "cash": str(form.get("cash_paid") or 0),
        "online": str(form.get("online_paid") or 0),
        "prevDue": 0, "prevCredit": 0,
        "importBillMode": bool(res.get("import_bill_mode")),
        "importInvoiceSummary": res.get("import_invoice_summary"),
    }
    payload = page_roundtrip(form["items"], tab)["payload"]
    # The debounced live calc fills the auto rounding before Save is pressed.
    calc = svc.calc_purchase(conn, dict(payload, auto_rounding=True, skip_party_due=True))
    if calc.get("ok") and isinstance(calc["calc"].get("rounding"), (int, float)):
        tab["rounding"] = str(calc["calc"]["rounding"])
        payload = page_roundtrip(form["items"], tab)["payload"]
    saved = svc.save_purchase_bill(conn, payload)
    assert saved.get("ok"), saved
    conn.commit()
    return conn, len(form["items"])


# ------------------------------------------------------------------ compare
PURCHASE_COLS = ("bill_number", "purchase_date", "gross_total", "total_discount", "cgst",
                 "sgst", "rounding", "total_amount", "due")
LINE_COLS = ("batch", "expiry", "qty", "free_qty", "rate", "mrp", "discount_pct", "gst_pct",
             "amount")
MED_COLS = ("type", "unit", "stock_qty", "batch_no", "expiry_date", "mrp", "rate", "hsn_code",
            "manufacturer", "supplier_name")


def cols(conn, table, want):
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    return [c for c in want if c in have]


def snapshot(conn):
    pc = cols(conn, "purchases", PURCHASE_COLS)
    p = conn.execute(f"SELECT {', '.join(pc)} FROM purchases").fetchall()
    sup = conn.execute("SELECT name, gstin, phone FROM suppliers").fetchall()
    lc = cols(conn, "purchase_items", LINE_COLS)
    lines = conn.execute(
        f"SELECT m.name, {', '.join('pi.' + c for c in lc)} FROM purchase_items pi "
        "JOIN medicines m ON m.id = pi.medicine_id ORDER BY m.name").fetchall()
    mc = cols(conn, "medicines", MED_COLS)
    meds = conn.execute(f"SELECT name, {', '.join(mc)} FROM medicines ORDER BY name").fetchall()
    return {
        "purchase": [dict(r) for r in p],
        "suppliers": [dict(r) for r in sup],
        "lines": {r[0]: dict(r) for r in lines},
        "medicines": {r[0]: dict(r) for r in meds},
    }


def same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.011
    return (a if a is not None else "") == (b if b is not None else "")


def diff(tag, a, b, out):
    for k in sorted(set(a) | set(b)):
        if not same(a.get(k), b.get(k)):
            out.append(f"  {tag} {k}: classic={a.get(k)!r}  tauri={b.get(k)!r}")


def compare(path):
    name = os.path.basename(path)
    c_conn, c_n = run_classic(path)
    t_conn, t_n = run_tauri(path)
    c, t = snapshot(c_conn), snapshot(t_conn)
    out = []
    if c_n != t_n:
        out.append(f"  rows parsed: classic={c_n} tauri={t_n}")
    for i, (pa, pb) in enumerate(zip(c["purchase"], t["purchase"])):
        diff("purchase", pa, pb, out)
    if len(c["purchase"]) != len(t["purchase"]):
        out.append(f"  purchases saved: classic={len(c['purchase'])} tauri={len(t['purchase'])}")
    diff("supplier", (c["suppliers"] or [{}])[0], (t["suppliers"] or [{}])[0], out)
    for key in ("lines", "medicines"):
        for med in sorted(set(c[key]) | set(t[key])):
            if med not in c[key] or med not in t[key]:
                out.append(f"  {key} {med}: only in {'tauri' if med in t[key] else 'classic'}")
                continue
            diff(f"{key[:-1]} {med} /", c[key][med], t[key][med], out)
    print(f"\n=== {name}: {c_n} rows, total classic "
          f"{(c['purchase'] or [{}])[0].get('total_amount')} / tauri "
          f"{(t['purchase'] or [{}])[0].get('total_amount')}")
    print("\n".join(out) if out else "  IDENTICAL: purchase, lines and inventory match")
    return c, t, out


if __name__ == "__main__":
    files = sys.argv[1:] or [os.path.join(HERE, f) for f in
                             ("distributor_bill.xlsx", "distributor_bill.csv", "distributor_bill.pdf")]
    for f in files:
        compare(f)
