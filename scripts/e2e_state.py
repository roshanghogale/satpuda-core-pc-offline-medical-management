"""Print the server-side state the end-to-end matrix asserts against.

Reads through the same StoreQueryClient the desktop uses, so what it prints is
what a client actually sees -- not a local cache. Usage:

    python3 scripts/e2e_state.py            # summary
    python3 scripts/e2e_state.py LIVETEST   # focus one medicine name fragment
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import online_catalog  # noqa: E402
from core import store_query_client as sq  # noqa: E402


def rows(res):
    if isinstance(res, dict):
        return res.get("rows", [])
    return res or []


def main() -> int:
    needle = (sys.argv[1] if len(sys.argv) > 1 else "").upper()

    counts = {
        "medicines": len(rows(sq.list_inventory(limit=5000))),
        "sales": len(rows(sq.list_sales(limit=5000))),
        "purchases": len(rows(sq.list_purchases(limit=5000))),
        "sales_returns": len(rows(sq.list_sales_returns(limit=5000))),
        "purchase_returns": len(rows(sq.list_purchase_returns(limit=5000))),
        "customer_payments": len(rows(sq.list_customer_payments(limit=5000))),
        "supplier_payments": len(rows(sq.list_supplier_payments(limit=5000))),
    }
    print("  counts: " + "  ".join(f"{k}={v}" for k, v in counts.items()))

    if not needle:
        return 0

    print(f"\n  INVENTORY matching {needle!r}:")
    hit = False
    for m in rows(sq.list_inventory(limit=5000)):
        if needle in str(m.get("name") or "").upper():
            hit = True
            print(f"    id={m.get('id')} batch={m.get('batch_no')} stock={m.get('stock_qty')} "
                  f"rate={m.get('rate')} mrp={m.get('mrp')} exp={m.get('expiry_date')}")
    if not hit:
        print("    (none)")

    # The sales picker is the projection a biller actually sees.
    print(f"\n  SALES PICKER for {needle!r}:")
    try:
        picked = online_catalog.search_medicines_flat(needle)
        if not picked:
            print("    (none -- not sellable)")
        for r in picked:
            print(f"    {r.get('name')} batch={r.get('batch')} stock={r.get('stock')} "
                  f"rate={r.get('rate')} mrp={r.get('mrp')}")
    except Exception as exc:
        print(f"    picker error: {type(exc).__name__}: {exc}")

    print("\n  PARTY BALANCES:")
    for c in rows(sq.list_customers(limit=5000)):
        if float(c.get("total_due") or 0) or float(c.get("total_credit") or 0):
            print(f"    customer {c.get('name')}: due={c.get('total_due')} credit={c.get('total_credit')}")
    for s in rows(sq.list_suppliers(limit=5000)):
        if float(s.get("total_due") or 0) or float(s.get("total_credit") or 0):
            print(f"    supplier {s.get('name')}: due={s.get('total_due')} credit={s.get('total_credit')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
