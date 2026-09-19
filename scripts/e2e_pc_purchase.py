"""PC half of the end-to-end matrix: save a purchase through the desktop UI path.

Uses desktop_purchase_service.save_purchase_bill -- the same entry point the
React UI posts to -- rather than the raw *_online_now internals, so it exercises
the application's own validation and calc.

Deliberately does NOT retry on failure. A transient read failure after a
successful write is indistinguishable from a rejected write, and re-running
would allocate a second FY serial and double-move stock.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_api  # noqa: E402
from core import desktop_purchase_service as pur  # noqa: E402
from core.sync_prefs import is_online_mode  # noqa: E402

NAME = "ZZ PCTEST TABLET 500"
BATCH = "PT1"
STRIPS = 15
TABS_PER_STRIP = 10          # -> 150 units
RATE_PER_STRIP = 80.0        # -> 8.00 per tab
MRP_PER_STRIP = 120.0        # -> 12.00 per tab


def main() -> int:
    assert is_online_mode(), "must be ONLINE"
    conn = desktop_api._adopt_online_runtime()

    body = {
        "supplier_name": "ZZ SUPPLIER ONE",
        "supplier_address": "",
        "supplier_phone": "",
        "bill_number": "ZZP-PC-E2E",
        "purchase_date": "2026-08-30",
        "gst_calc_method": "discount_before_gst",
        "overall_discount": 0,
        "discount_rs": 0,
        "rounding": 0,
        "cash_paid": 0,          # leave it fully due, for the payment test
        "online_paid": 0,
        "expenditure": 0,
        "items": [
            {
                "name": NAME,
                "type": "Tablet",
                "batch": BATCH,
                "expiry": "06/31",
                "qty": STRIPS,
                "free_qty": 0,
                "tablets_per_stripe": TABS_PER_STRIP,
                "unit": TABS_PER_STRIP,
                "rate": RATE_PER_STRIP,
                "mrp": MRP_PER_STRIP,
                "gst_pct": 12,
                "hsn_code": "30049099",
                "manufacturer": "ZZ PC MFR",
                "schedule": "H",
                "discount_pct": 0,
            }
        ],
    }

    print(f"  saving purchase ZZP-PC-E2E: {STRIPS} strips x {TABS_PER_STRIP} "
          f"= {STRIPS * TABS_PER_STRIP} units of {NAME} [{BATCH}]")
    res = pur.save_purchase_bill(conn, body)
    print(f"  result: {res}")
    if not res.get("ok"):
        print("  SAVE REPORTED FAILURE -- not retrying (a retry would duplicate "
              "the bill and double-move stock). Inspect the server before acting.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
