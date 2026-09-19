"""Test FY serial migration on Shivkrupa store (creates backup first)."""
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SRC = os.path.join(
    ROOT,
    "config",
    "stores",
    "Store_Shivkrupa_Medical_General_Store",
    "veterinary.db.pre_fy_serial.bak",
)


def main() -> None:
    if not os.path.isfile(SRC):
        print("Backup DB missing:", SRC)
        return

    work = SRC + ".fy_test_working"
    shutil.copy2(SRC, work)
    print("Testing on copy:", work)

    from core.db_setup import initialise
    from core.fy_serial import (
        allocate_purchase_number,
        allocate_sales_bill_no,
        migrate_fy_serial_numbers,
    )

    conn = sqlite3.connect(work)
    initialise(conn)
    print("Migration:", migrate_fy_serial_numbers(conn))
    c = conn.cursor()

    print("\n=== Sales by FY ===")
    for row in c.execute(
        """
        SELECT fy_start_year, MIN(bill_no), MAX(bill_no), COUNT(*)
        FROM sales WHERE COALESCE(is_autosave,0)=0
        GROUP BY fy_start_year ORDER BY 1
        """
    ):
        print(tuple(row))

    print("\n=== Purchases by FY ===")
    for row in c.execute(
        """
        SELECT fy_start_year, MIN(purchase_no), MAX(purchase_no), COUNT(*)
        FROM purchases WHERE COALESCE(is_autosave,0)=0
        GROUP BY fy_start_year ORDER BY 1
        """
    ):
        print(tuple(row))

    print("\n=== Apr 2026 sales (should be SCB1..) ===")
    for row in c.execute(
        """
        SELECT bill_no, bill_date, fy_serial FROM sales
        WHERE bill_date >= '2026-04-01' ORDER BY id LIMIT 8
        """
    ):
        print(tuple(row))

    print("\nNext numbers (FY 2026-27):", allocate_sales_bill_no(conn, "2026-08-09"))
    print("Next purchase:", allocate_purchase_number(conn, "2026-08-09"))
    conn.close()


if __name__ == "__main__":
    main()
