"""Preview dot matrix bill text layout."""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.bill_config import apply_print_bill_layout
from core.bill_output import _load_sale_data_for_pdf
from core.dot_matrix_print import format_bill_text, format_test_page


def main() -> int:
    print("=== TEST PAGE ===")
    print(format_test_page(""))
    print()
    print("=== SCB67 ===")
    db = os.path.join(ROOT, "config", "stores", "Store_Roshan", "veterinary.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id FROM sales WHERE bill_no='SCB67' LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("SCB67 not found")
        return 1
    _bill_no, ctx, settings = _load_sale_data_for_pdf(
        conn, int(row["id"]), print_slot=1, db_path=db,
    )
    html_settings = apply_print_bill_layout(settings, print_slot_copies=1)
    print(format_bill_text(ctx, html_settings))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
