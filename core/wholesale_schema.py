"""Wholesale mode: the columns every part of it reads and writes (owner, 18 Oct 2026).

A store the server has allowed (stores.wholesale_enabled) can be switched from retail to
wholesale with the switch beside the Online status. The switch is the store setting
``business_mode`` ('retail' | 'wholesale', settings kv). Retail bills, stock and reports work
exactly as before; a wholesale store sells to other medical shops:

* no MRP / per-tablet rate on the sale: whole packs at Rate A / B / C, GST added on top
  (rates exclude GST), the buyer's rate tier picks which rate comes first;
* a B2B tax invoice: buyer GSTIN (core/customer_gst.py keeps it), drug licence numbers,
  place of supply, IGST across states else CGST + SGST, HSN-wise tax summary, due date
  from the party's credit days, free qty and scheme %;
* purchase keeps its purchase rate and also takes Rate A / B / C for each line, which
  become the medicine's sale rates;
* a payment, customer or supplier, can carry the number of the paper receipt given or
  taken (receipt_no) -- in both modes.

See D:\\projects\\WHOLESALE_DESIGN.md. Each step is an ALTER only when the column is missing.
"""
from __future__ import annotations

BUSINESS_MODE_KEY = "business_mode"
RETAIL = "retail"
WHOLESALE = "wholesale"
RATE_TIERS = ("A", "B", "C")

COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    # the buyer: a medical shop (party_type 'medical') or a walk-in ('retail')
    "customers": (
        ("party_type", "TEXT"),        # 'retail' (default) | 'medical'
        ("dl_numbers", "TEXT"),        # drug licence numbers, free text "20B: ..., 21B: ..."
        ("state_code", "TEXT"),        # two-digit GST state code when no GSTIN is on file
        ("rate_tier", "TEXT"),         # 'A' | 'B' | 'C', NULL = 'A'
        ("credit_limit", "REAL"),      # 0 / NULL = no limit
        ("credit_days", "INTEGER"),    # bill due date = bill date + credit days
    ),
    # the sale rates of a batch, per pack, GST excluded
    "medicines": (
        ("purchase_rate", "REAL"),
        ("rate_a", "REAL"),
        ("rate_b", "REAL"),
        ("rate_c", "REAL"),
    ),
    "purchase_items": (
        ("rate_a", "REAL"),
        ("rate_b", "REAL"),
        ("rate_c", "REAL"),
    ),
    "sales": (
        ("invoice_mode", "TEXT"),      # NULL / 'retail' | 'wholesale' (the mode it was made in)
        ("buyer_gstin", "TEXT"),
        ("buyer_dl", "TEXT"),
        ("place_of_supply", "TEXT"),   # two-digit state code
        ("taxable_total", "REAL"),
        ("cgst_total", "REAL"),
        ("sgst_total", "REAL"),
        ("igst_total", "REAL"),
        ("due_date", "TEXT"),          # YYYY-MM-DD
        ("transport", "TEXT"),
        ("lr_no", "TEXT"),
        ("eway_bill_no", "TEXT"),
    ),
    "sales_items": (
        ("rate_tier", "TEXT"),
        ("free_qty", "REAL"),
        ("scheme_pct", "REAL"),
        ("taxable", "REAL"),
        ("gst_amt", "REAL"),
        ("batch_no", "TEXT"),
        ("expiry_date", "TEXT"),
        ("hsn_code", "TEXT"),
    ),
    "customer_payments": (("receipt_no", "TEXT"),),
    "supplier_payments": (("receipt_no", "TEXT"),),
}


def ensure_wholesale_columns(conn) -> None:
    """Add every wholesale column a store is missing. Safe to call on every open."""
    cur = conn.cursor()
    for table, cols in COLUMNS.items():
        try:
            have = {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            continue
        if not have:
            continue
        for name, typ in cols:
            if name not in have:
                try:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
                except Exception as e:  # a column another build added meanwhile
                    print(f"{table}.{name} wholesale column: {e}")
    conn.commit()


def get_business_mode(conn) -> str:
    try:
        row = conn.execute("SELECT value FROM settings WHERE name = ?", (BUSINESS_MODE_KEY,)).fetchone()
    except Exception:
        return RETAIL
    v = str(row[0] if row else "").strip().lower()
    return WHOLESALE if v == WHOLESALE else RETAIL
