"""Repair due_amount / total_due on all sales rows for the active store."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.store_manager import get_active_db_path
from core.db_setup import initialise
from core.billing_service import repair_sales_due_fields


def main():
    db_path = get_active_db_path()
    conn = sqlite3.connect(db_path)
    initialise(conn)
    n = repair_sales_due_fields(conn)
    conn.close()
    print(f"Repaired {n} sale(s) in {db_path}.")


if __name__ == "__main__":
    main()
