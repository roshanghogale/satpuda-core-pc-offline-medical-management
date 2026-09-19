"""Test dot matrix sale bill print + write config/print_log.txt."""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, "config", "stores", "Store_Roshan", "veterinary.db")


def main() -> int:
    from core.print_log import print_log, print_log_path
    from core.printer_manager import PrinterManager
    from core.bill_output import print_bill_silent_with_slot

    print_log("=== test_dot_matrix_sale_print START ===")
    print_log(f"log_file={print_log_path()}")
    print_log(f"db={DB}")
    print_log(f"printer_type={PrinterManager.get_printer_type()}")
    print_log(f"dot_matrix_mode={PrinterManager.is_dot_matrix_mode()}")
    print_log(f"slot1={PrinterManager.get_printer_for_slot(1)}")

    if not os.path.isfile(DB):
        print_log(f"DB not found: {DB}", level="ERROR")
        return 1

    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, bill_no FROM sales WHERE bill_no LIKE ? ORDER BY id DESC LIMIT 1",
        ("SCB65",),
    )
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT id, bill_no FROM sales ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    if not row:
        print_log("No sales found in database", level="ERROR")
        return 1

    sale_id = int(row["id"])
    bill_no = row["bill_no"]
    print_log(f"testing sale_id={sale_id} bill_no={bill_no}")

    try:
        print_bill_silent_with_slot(conn, sale_id, 1, db_path=DB)
        print_log(f"print_bill_silent_with_slot OK bill_no={bill_no}")
    except Exception as exc:
        from core.print_log import print_log_exception
        print_log_exception("test print failed", exc)
        return 1
    finally:
        conn.close()

    print_log("=== test_dot_matrix_sale_print END ===")
    print("Log written to:", print_log_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())