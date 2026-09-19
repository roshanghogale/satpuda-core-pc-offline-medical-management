"""PC half of the end-to-end matrix: sales return through the desktop UI path.

Returns part of the PC sale, so stock should come back and the customer's due
should drop by the refund. Uses desktop_returns_service.save_sales_return --
the entry point the React UI posts to.

This is the path that previously persisted a NEGATIVE temporary id as the
permanent local_id, which dragged the server's MAX(local_id) allocator negative
and made Android returns impossible. The verification therefore asserts the id
is POSITIVE and that exactly one changelog row was produced.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_api  # noqa: E402
from core import desktop_returns_service as ret  # noqa: E402
from core import online_mutation_queue as queue  # noqa: E402
from core import store_query_client as sq  # noqa: E402
from core.sync_prefs import is_online_mode  # noqa: E402

CUSTOMER = "ZZ CUSTOMER TWO"
RETURN_QTY = 1


def main() -> int:
    assert is_online_mode(), "must be ONLINE"

    sale = None
    for s in sq.list_sales(limit=50).get("rows", []):
        if s.get("customer_name") == CUSTOMER:
            sale = s
            break
    assert sale, f"no sale found for {CUSTOMER}"
    sale_id = int(sale.get("id"))
    detail = sq.get_sale(sale_id)
    lines = detail.get("items") or []
    assert lines, "sale has no lines"
    ln = lines[0]
    rate = float(ln.get("rate") or 0)
    orig_qty = float(ln.get("qty") or 0)
    assert RETURN_QTY <= orig_qty, "cannot return more than was sold"

    print(f"  sale {sale.get('bill_no')} (local_id={sale_id}) due={sale.get('due_amount')}")
    print(f"  returning {RETURN_QTY} of {orig_qty} x {ln.get('name')} @ {rate} "
          f"-> refund {RETURN_QTY * rate}")

    conn = desktop_api._adopt_online_runtime()
    body = {
        "sale_id": sale_id,
        "customer_name": CUSTOMER,
        "discount": 0,
        "items": [
            {
                "medicine_id": ln.get("medicine_id"),
                "name": ln.get("name"),
                "qty": RETURN_QTY,
                "orig_qty": orig_qty,
                "rate": rate,
                "orig_amount": float(ln.get("amount") or 0),
                "line_amount": round(RETURN_QTY * rate, 2),
            }
        ],
    }
    res = ret.save_sales_return(conn, body)
    print(f"  result: ok={res.get('ok')} return_id={res.get('return_id')} "
          f"return_no={res.get('return_no')} error={res.get('error')}")
    if not res.get("ok"):
        return 1

    # The write may be queued rather than pushed inline; a short-lived process
    # exits before the background flush thread runs.
    pending = len(queue.pending_rows())
    if pending:
        print(f"  {pending} queued write(s) -- flushing")
        queue.flush_now(wait_sec=60.0)
        print(f"  pending after flush: {len(queue.pending_rows())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
