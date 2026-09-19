"""PC half of the end-to-end matrix: sell through the desktop UI path.

Uses desktop_sales_service.save_sale -- the entry point the React UI posts to.
Sells 12 units of the medicine the PC purchase created, partly paid, so the
remainder can be cleared by the customer-payment step.

Does NOT retry on failure: a re-run would allocate a second bill number and
decrement stock twice.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_api  # noqa: E402
from core import desktop_sales_service as sales  # noqa: E402
from core import online_catalog  # noqa: E402
from core.sync_prefs import is_online_mode  # noqa: E402

NEEDLE = "PCTEST"
QTY = 12
CASH = 50.0          # partial payment -> leaves a due for the payment test


def main() -> int:
    assert is_online_mode(), "must be ONLINE"

    # Read the medicine out of the SALES PICKER, so the sale uses exactly what a
    # biller would have picked -- and so a missing entry fails here rather than
    # silently selling something else.
    picked = [r for r in online_catalog.search_medicines_flat(NEEDLE)]
    assert picked, f"{NEEDLE} is not in the sales picker -- nothing to sell"
    m = picked[0]
    print(f"  picker: {m.get('name')} batch={m.get('batch')} stock={m.get('stock')} "
          f"rate={m.get('rate')} mrp={m.get('mrp')}")
    assert float(m.get("stock") or 0) >= QTY, "not enough stock"

    # The picker exposes rate/mrp as STRIP prices while stock is counted in
    # tablets. SalesPage.tsx divides by tabs-per-strip before billing
    # (parseTabletsPerStrip + "list / tps"); anything that passes the raw strip
    # rate straight through overcharges by exactly that factor.
    import re

    tps = 1
    m_unit = re.search(r"(\d+)", str(m.get("unit") or ""))
    if m_unit:
        tps = max(1, int(m_unit.group(1)))
    rate_per_tab = round(float(m.get("rate") or 0) / tps, 2)
    mrp_per_tab = round(float(m.get("mrp") or 0) / tps, 2)
    print(f"  tabs/strip={tps} -> rate {m.get('rate')}/strip = {rate_per_tab}/tab, "
          f"mrp {m.get('mrp')}/strip = {mrp_per_tab}/tab")

    conn = desktop_api._adopt_online_runtime()
    body = {
        "customer_name": "ZZ CUSTOMER TWO",
        "customer_address": "",
        "customer_phone": "",
        "doctor_name": "",
        "bill_date": "2026-08-30",
        "payment_mode": "DUE",
        "cash_paid": CASH,
        "online_paid": 0,
        "discount_pct": 0,
        "discount_rs": 0,
        "items": [
            {
                "id": m.get("id"),
                "name": m.get("name"),
                "batch": m.get("batch"),
                "expiry": m.get("expiry"),
                "type": m.get("type"),
                "unit": m.get("unit"),
                "qty": QTY,
                "rate": rate_per_tab,
                "mrp": mrp_per_tab,
                "gst_percent": m.get("gst_percent"),
                "disc": 0,
            }
        ],
    }
    print(f"  selling {QTY} units, cash paid {CASH}")
    res = sales.save_sale(conn, body)
    ok = bool(res.get("ok"))
    print(f"  ok={ok} bill_no={res.get('bill_no')} "
          f"total={(res.get('calc') or {}).get('total_amount')} "
          f"due={(res.get('calc') or {}).get('due_amount')}")
    if not ok:
        print(f"  FAILURE: {res.get('error')} -- not retrying")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
