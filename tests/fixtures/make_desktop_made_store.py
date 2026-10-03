"""Make desktop_made_store.db: a tiny store the way the PC writes it (3 Oct 2026).

The first leg of the PC -> phone -> PC -> phone round trip. The schema is the desktop's own
(core.db_setup.initialise on a new file, as the app does when it opens a store); the rows are
made up -- no real shop, customer or supplier.

    python tests/fixtures/make_desktop_made_store.py [android_test_resources_dir]

Writes tests/fixtures/desktop_made_store.db and, when a directory is given, the same file
gzipped as a Drive backup (desktop_made_store.sqlite.gz) into it -- the Android round-trip
test reads it from app/src/test/resources/roundtrip/.
"""
import gzip
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

# .sqlite.gz, not .db.gz: the Android repo's .gitignore drops *.db.gz.
BACKUP_NAME = "desktop_made_store.sqlite.gz"
WHEN = "2026-10-03 10:00:00"


def build(path: str) -> None:
    from core.db_setup import initialise

    for p in (path, path + "-wal", path + "-shm", path + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    c = sqlite3.connect(path)
    initialise(c)
    c.execute("DELETE FROM pharmacy_profile")
    c.execute("INSERT INTO pharmacy_profile (id, name, address, phone, gst_enabled) "
              "VALUES (1, 'DEMO MEDICAL', 'MAIN ROAD, DEMO NAGAR', '9000000000', 1)")
    c.executemany(
        "INSERT INTO customers (id, name, phone, address, created_at, total_due, total_credit) "
        "VALUES (?,?,?,?,?,?,?)",
        [(1, "RAMESH PATIL", "9000000001", "DEMO NAGAR", WHEN, 0, 0),
         (2, "SUNITA JADHAV", "9000000002", "DEMO NAGAR", WHEN, 70, 0)],
    )
    c.execute("INSERT INTO suppliers (id, name, phone, gstin, created_at, total_due, total_credit) "
              "VALUES (1, 'SHREE DEMO DISTRIBUTORS', '9000000010', '', ?, 300, 0)", (WHEN,))
    c.executemany(
        "INSERT INTO medicines (id, name, type, stock_qty, unit, gst_percent, mrp, rate, manufacturer, "
        "batch_no, expiry_date, hsn_code, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(1, "DEMOCETAMOL 500 TAB", "Tablet", 90, "10", 12, 30.0, 20.0, "DEMO PHARMA", "B101", "2028-03-31",
          "3004", WHEN),
         (2, "DEMOXYCILLIN 250 CAP", "Capsule", 45, "15", 12, 90.0, 60.0, "DEMO PHARMA", "B202", "2027-12-31",
          "3004", WHEN),
         (3, "DEMO COUGH SYRUP 100ML", "Syrup", 8, "1", 12, 110.0, 75.0, "DEMO LABS", "S303", "2027-06-30",
          "3004", WHEN)],
    )
    c.execute(
        "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, bill_number, subtotal, total_gst, "
        "cgst, sgst, total_amount, need_to_pay, final_amount, amount_paid, amount_paid_at_entry, "
        "cash_paid_at_entry, due, total_due, due_amount, created_at, fy_start_year, fy_serial) "
        "VALUES (1, 'P-2026-0001', 1, '2026-10-01', 'SDD/1001', 1000, 120, "
        "60, 60, 1120, 1120, 1120, 820, 820, 820, 300, 300, 300, ?, 2026, 1)", (WHEN,),
    )
    c.executemany(
        "INSERT INTO purchase_items (purchase_id, medicine_id, qty, free_qty, type, hsn_code, gst_pct, mrp, rate, "
        "manufacturer, batch_no, expiry_date, taxable, gst_amt, item_amount, amount, stock_units, created_at) "
        "VALUES (1,?,?,0,?,'3004',12,?,?,'DEMO PHARMA',?,?,?,?,?,?,?,?)",
        [(1, 10, "Tablet", 30.0, 20.0, "B101", "2028-03-31", 200, 24, 224, 224, 100, WHEN),
         (2, 4, "Capsule", 90.0, 60.0, "B202", "2027-12-31", 240, 28.8, 268.8, 268.8, 60, WHEN),
         (3, 8, "Syrup", 110.0, 70.0, "S303", "2027-06-30", 560, 67.2, 627.2, 627.2, 8, WHEN)],
    )
    c.executemany(
        "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, amount_paid, cash_paid, "
        "online_paid, due_amount, total_due, doctor_name, created_at, customer_name, customer_phone, "
        "customer_address, fy_start_year, fy_serial) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,2026,?)",
        [(1, "SCB1/FY2026-27", 1, "2026-10-02", 150.0, 150.0, 150.0, 0, 0, 0, "DR DEMO", WHEN,
          "RAMESH PATIL", "9000000001", "DEMO NAGAR", 1),
         (2, "SCB2/FY2026-27", 2, "2026-10-02", 220.0, 100.0, 50.0, 50.0, 120.0, 120.0, "", WHEN,
          "SUNITA JADHAV", "9000000002", "DEMO NAGAR", 2),
         (3, "SCB3/FY2026-27", 1, "2026-10-03", 90.0, 90.0, 0, 90.0, 0, 0, "", WHEN,
          "RAMESH PATIL", "9000000001", "DEMO NAGAR", 3)],
    )
    c.executemany(
        "INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount, item_discount, cost_price, "
        "created_at) VALUES (?,?,?,?,12,?,0,?,?)",
        [(1, 1, 10, 3.0, 30.0, 2.0, WHEN),
         (1, 2, 1, 120.0, 120.0, 4.0, WHEN),
         (2, 3, 2, 110.0, 220.0, 75.0, WHEN),
         (3, 2, 15, 6.0, 90.0, 4.0, WHEN)],
    )
    c.execute("INSERT INTO sales_returns (id, return_no, sale_id, customer_id, return_date, refund_amount, "
              "reason, created_at) VALUES (1, 'SR-2026-0001', 1, 1, '2026-10-03', 30.0, 'NOT NEEDED', ?)", (WHEN,))
    c.execute("INSERT INTO sales_return_items (return_id, medicine_id, qty, rate, amount, created_at) "
              "VALUES (1, 1, 10, 3.0, 30.0, ?)", (WHEN,))
    c.execute("INSERT INTO purchase_returns (id, return_no, purchase_id, supplier_id, return_date, refund_amount, "
              "reason, created_at) VALUES (1, 'PR-2026-0001', 1, 1, '2026-10-03', 56.0, 'DAMAGED', ?)", (WHEN,))
    c.execute("INSERT INTO purchase_return_items (return_id, medicine_id, qty, rate, amount, stock_units) "
              "VALUES (1, 1, 2, 28.0, 56.0, 20)")
    c.execute("INSERT INTO customer_payments (id, customer_id, payment_date, amount, payment_mode, cash_amount, "
              "online_amount, note, created_at) VALUES (1, 2, '2026-10-03', 50.0, 'cash', 50.0, 0, 'PART', ?)",
              (WHEN,))
    c.execute("INSERT INTO supplier_payments (id, payment_no, supplier_id, payment_date, amount, mode, due_before, "
              "due_after, created_at) VALUES (1, 'SP-2026-0001', 1, '2026-10-03', 100.0, 'Cash', 400, 300, ?)",
              (WHEN,))
    c.commit()
    # Opened once more, as the app does at its next start: the desktop's open-time repairs
    # (running balances, bill flags) have run, so later hops compare against a settled file.
    initialise(c)
    c.commit()
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("VACUUM")
    c.close()


def main(argv) -> None:
    from unittest import mock

    out = os.path.join(HERE, "desktop_made_store.db")
    # Offline, as a shop passing the file over Drive runs: Online the desktop reads the
    # pharmacy header from the server (this machine's own shop) instead of the file.
    with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
        build(out)
    print(out)
    if len(argv) > 1:
        os.makedirs(argv[1], exist_ok=True)
        gz = os.path.join(argv[1], BACKUP_NAME)
        with open(out, "rb") as src, gzip.open(gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        print(gz)


if __name__ == "__main__":
    main(sys.argv)
