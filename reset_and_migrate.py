"""
reset_and_migrate.py
────────────────────
Run this ONCE to:
  1. Audit the current DB state
  2. Fix all schema gaps
  3. Clean up orphaned/stale data
  4. Recalculate all balances from raw transactions
  5. Verify final consistency

Usage:
    python reset_and_migrate.py

Safe to run on existing data — does NOT delete any transaction records.
"""
import sqlite3
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'veterinary.db')


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def backup_db():
    ts  = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = DB_PATH.replace('.db', f'_backup_{ts}.db')
    shutil.copy2(DB_PATH, dst)
    log(f"Backup created: {dst}")
    return dst


# ── Schema helpers ────────────────────────────────────────────────────────────

def col_names(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def add_col_if_missing(cur, table, col, col_type):
    if col not in col_names(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        log(f"  Added column {table}.{col}")
        return True
    return False


def table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


# ── Phase 1: Ensure all required tables exist ─────────────────────────────────

def ensure_tables(conn):
    cur = conn.cursor()
    log("Phase 1: Ensuring all tables exist...")

    # supplier_payments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_payments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_no   TEXT UNIQUE,
            supplier_id  INTEGER,
            payment_date DATE,
            amount       REAL DEFAULT 0,
            mode         TEXT DEFAULT 'Cash',
            reference    TEXT,
            due_before   REAL DEFAULT 0,
            due_after    REAL DEFAULT 0,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    """)

    # purchase_returns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchase_returns (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            return_no     TEXT UNIQUE,
            purchase_id   INTEGER,
            supplier_id   INTEGER,
            return_date   DATE,
            refund_amount REAL DEFAULT 0,
            discount      REAL DEFAULT 0,
            reason        TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (purchase_id) REFERENCES purchases(id),
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    """)

    # purchase_return_items
    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchase_return_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id   INTEGER,
            medicine_id INTEGER,
            qty         REAL DEFAULT 0,
            rate        REAL DEFAULT 0,
            amount      REAL DEFAULT 0,
            FOREIGN KEY (return_id)   REFERENCES purchase_returns(id),
            FOREIGN KEY (medicine_id) REFERENCES medicines(id)
        )
    """)

    # sales_returns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sales_returns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            return_no       TEXT UNIQUE,
            sale_id         INTEGER,
            customer_id     INTEGER,
            return_date     DATE,
            refund_amount   REAL DEFAULT 0,
            discount        REAL DEFAULT 0,
            reason          TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sale_id)     REFERENCES sales(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)

    # sales_return_items
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sales_return_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id       INTEGER,
            medicine_id     INTEGER,
            qty             INTEGER,
            rate            REAL,
            amount          REAL,
            FOREIGN KEY (return_id)   REFERENCES sales_returns(id),
            FOREIGN KEY (medicine_id) REFERENCES medicines(id)
        )
    """)

    # customer_payments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_payments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id   INTEGER NOT NULL,
            payment_date  DATE NOT NULL,
            amount        REAL NOT NULL,
            payment_mode  TEXT DEFAULT 'cash',
            cash_amount   REAL DEFAULT 0,
            online_amount REAL DEFAULT 0,
            reference_no  TEXT,
            note          TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)

    # settings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT UNIQUE,
            value TEXT
        )
    """)

    conn.commit()
    log("  All tables verified.")


# ── Phase 2: Ensure all required columns exist ────────────────────────────────

def ensure_columns(conn):
    cur = conn.cursor()
    log("Phase 2: Ensuring all columns exist...")

    # suppliers
    add_col_if_missing(cur, 'suppliers', 'total_due',    'REAL DEFAULT 0')
    add_col_if_missing(cur, 'suppliers', 'total_credit', 'REAL DEFAULT 0')

    # customers
    add_col_if_missing(cur, 'customers', 'total_due',    'REAL DEFAULT 0')
    add_col_if_missing(cur, 'customers', 'total_credit', 'REAL DEFAULT 0')
    add_col_if_missing(cur, 'customers', 'last_updated', 'TIMESTAMP')

    # purchases — new write-once column
    add_col_if_missing(cur, 'purchases', 'amount_paid_at_entry', 'REAL DEFAULT 0')

    # purchases — legacy columns kept for backward compat
    for col, typ in [
        ('bill_number',    'TEXT'),
        ('subtotal',       'REAL DEFAULT 0'),
        ('total_gst',      'REAL DEFAULT 0'),
        ('cgst',           'REAL DEFAULT 0'),
        ('sgst',           'REAL DEFAULT 0'),
        ('total_amount',   'REAL DEFAULT 0'),
        ('overall_discount','REAL DEFAULT 0'),
        ('rounding',       'REAL DEFAULT 0'),
        ('need_to_pay',    'REAL DEFAULT 0'),
        ('final_amount',   'REAL DEFAULT 0'),
        ('amount_paid',    'REAL DEFAULT 0'),
        ('previous_due',   'REAL DEFAULT 0'),
        ('previous_credit','REAL DEFAULT 0'),
        ('due',            'REAL DEFAULT 0'),
        ('current_credit', 'REAL DEFAULT 0'),
        ('total_due',      'REAL DEFAULT 0'),
        ('bill_cleared',   'INTEGER DEFAULT 0'),
        ('account_cleared','INTEGER DEFAULT 0'),
        ('due_amount',     'REAL DEFAULT 0'),
        ('credit_amount',  'REAL DEFAULT 0'),
    ]:
        add_col_if_missing(cur, 'purchases', col, typ)

    # purchase_items
    for col, typ in [
        ('discount_pct',    'REAL DEFAULT 0'),
        ('taxable',         'REAL DEFAULT 0'),
        ('gst_amt',         'REAL DEFAULT 0'),
        ('item_amount',     'REAL DEFAULT 0'),
        ('gst_pct',         'REAL DEFAULT 0'),
        ('discount_percent','REAL DEFAULT 0'),
        ('gst_value',       'REAL DEFAULT 0'),
        ('amount',          'REAL DEFAULT 0'),
        ('free_qty',        'REAL DEFAULT 0'),
    ]:
        add_col_if_missing(cur, 'purchase_items', col, typ)

    # sales
    for col, typ in [
        ('rounding',        'REAL DEFAULT 0'),
        ('cash_paid',       'REAL DEFAULT 0'),
        ('online_paid',     'REAL DEFAULT 0'),
        ('previous_credit', 'REAL DEFAULT 0'),
        ('paid_due',        'REAL DEFAULT 0'),
        ('bill_cleared',    'INTEGER DEFAULT 0'),
        ('account_cleared', 'INTEGER DEFAULT 0'),
        ('discount_pct',    'REAL DEFAULT 0'),
        ('due_amount',      'REAL DEFAULT 0'),
        ('credit_amount',   'REAL DEFAULT 0'),
    ]:
        add_col_if_missing(cur, 'sales', col, typ)

    # sales_items
    add_col_if_missing(cur, 'sales_items', 'item_discount', 'REAL DEFAULT 0')
    add_col_if_missing(cur, 'sales_items', 'cost_price',    'REAL DEFAULT 0')

    conn.commit()
    log("  All columns verified.")


# ── Phase 3: Back-fill amount_paid_at_entry ───────────────────────────────────

def backfill_entry_paid(conn):
    """
    amount_paid_at_entry = the payment made at purchase-save time.
    For existing rows: if it's 0/NULL, set it from amount_paid.
    This is safe because old code stored entry-time payment in amount_paid
    before the payment tab started mutating it.
    For rows where supplier_payments exist and amount_paid was mutated,
    we reconstruct entry-time payment as:
        amount_paid_at_entry = total_amount - original_due
    where original_due = purchases.due (stored at save time).
    """
    cur = conn.cursor()
    log("Phase 3: Back-filling amount_paid_at_entry...")

    cur.execute("""
        SELECT id, total_amount, amount_paid, COALESCE(due, 0),
               COALESCE(amount_paid_at_entry, 0)
        FROM purchases
    """)
    rows = cur.fetchall()
    updated = 0
    for pid, total, amt_paid, due_at_save, entry_paid in rows:
        total      = float(total or 0)
        amt_paid   = float(amt_paid or 0)
        due_at_save= float(due_at_save or 0)
        entry_paid = float(entry_paid or 0)

        if entry_paid > 0:
            # Already set — trust it
            continue

        # Reconstruct: entry_paid = total - due_at_save
        # 'due' column was set at purchase-save time before any payment tab touched it
        reconstructed = round(total - due_at_save, 2)
        if reconstructed < 0:
            reconstructed = 0.0

        cur.execute(
            "UPDATE purchases SET amount_paid_at_entry=? WHERE id=?",
            (reconstructed, pid))
        updated += 1

    conn.commit()
    log(f"  Back-filled {updated} purchase rows.")


# ── Phase 4: Clean up orphaned supplier_payments ──────────────────────────────

def clean_orphaned_payments(conn):
    """
    Remove supplier_payments that reference non-existent supplier_ids.
    These are left over from deleted suppliers and corrupt the balance calc.
    """
    cur = conn.cursor()
    log("Phase 4: Cleaning orphaned supplier_payments...")

    cur.execute("""
        SELECT sp.id, sp.payment_no, sp.supplier_id, sp.amount
        FROM supplier_payments sp
        LEFT JOIN suppliers s ON sp.supplier_id = s.id
        WHERE s.id IS NULL
    """)
    orphans = cur.fetchall()
    if orphans:
        log(f"  Found {len(orphans)} orphaned payment(s): {[(r[1], r[2]) for r in orphans]}")
        cur.executemany("DELETE FROM supplier_payments WHERE id=?",
                        [(r[0],) for r in orphans])
        conn.commit()
        log(f"  Deleted {len(orphans)} orphaned payment(s).")
    else:
        log("  No orphaned payments found.")


def clean_orphaned_purchase_returns(conn):
    """Remove purchase_returns referencing non-existent purchases."""
    cur = conn.cursor()
    log("Phase 4b: Cleaning orphaned purchase_returns...")

    cur.execute("""
        SELECT pr.id, pr.return_no, pr.purchase_id
        FROM purchase_returns pr
        LEFT JOIN purchases p ON pr.purchase_id = p.id
        WHERE p.id IS NULL
    """)
    orphans = cur.fetchall()
    if orphans:
        log(f"  Found {len(orphans)} orphaned return(s): {[(r[1], r[2]) for r in orphans]}")
        ids = [(r[0],) for r in orphans]
        # Delete items first
        cur.executemany("""
            DELETE FROM purchase_return_items WHERE return_id=?
        """, ids)
        cur.executemany("DELETE FROM purchase_returns WHERE id=?", ids)
        conn.commit()
        log(f"  Deleted {len(orphans)} orphaned return(s).")
    else:
        log("  No orphaned purchase returns found.")


def clean_orphaned_sales_returns(conn):
    """Remove sales_returns referencing non-existent sales."""
    cur = conn.cursor()
    log("Phase 4c: Cleaning orphaned sales_returns...")

    cur.execute("""
        SELECT sr.id, sr.return_no, sr.sale_id
        FROM sales_returns sr
        LEFT JOIN sales s ON sr.sale_id = s.id
        WHERE s.id IS NULL
    """)
    orphans = cur.fetchall()
    if orphans:
        log(f"  Found {len(orphans)} orphaned sales return(s): {[(r[1], r[2]) for r in orphans]}")
        ids = [(r[0],) for r in orphans]
        cur.executemany("DELETE FROM sales_return_items WHERE return_id=?", ids)
        cur.executemany("DELETE FROM sales_returns WHERE id=?", ids)
        conn.commit()
        log(f"  Deleted {len(orphans)} orphaned sales return(s).")
    else:
        log("  No orphaned sales returns found.")


# ── Phase 5: Recalculate all supplier balances ────────────────────────────────

def recalculate_all_suppliers(conn):
    cur = conn.cursor()
    log("Phase 5: Recalculating all supplier balances...")

    cur.execute("SELECT id, name FROM suppliers")
    suppliers = cur.fetchall()
    log(f"  Found {len(suppliers)} supplier(s).")

    for sid, sname in suppliers:
        # total purchased
        cur.execute(
            "SELECT COALESCE(SUM(total_amount),0) FROM purchases WHERE supplier_id=?",
            (sid,))
        total_purchased = round(float(cur.fetchone()[0] or 0), 2)

        # entry-time payments
        cur.execute(
            "SELECT COALESCE(SUM(amount_paid_at_entry),0) FROM purchases WHERE supplier_id=?",
            (sid,))
        total_entry_paid = round(float(cur.fetchone()[0] or 0), 2)

        # standalone payments
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE supplier_id=?",
            (sid,))
        total_payments = round(float(cur.fetchone()[0] or 0), 2)

        # purchase returns
        cur.execute("""
            SELECT COALESCE(SUM(pr.refund_amount),0)
            FROM purchase_returns pr
            JOIN purchases p ON pr.purchase_id = p.id
            WHERE p.supplier_id=?
        """, (sid,))
        total_returns = round(float(cur.fetchone()[0] or 0), 2)

        net          = round(total_purchased - total_entry_paid - total_payments - total_returns, 2)
        total_due    = round(max(0.0,  net), 2)
        total_credit = round(max(0.0, -net), 2)

        cur.execute(
            "UPDATE suppliers SET total_due=?, total_credit=? WHERE id=?",
            (total_due, total_credit, sid))

        # Set account_cleared per purchase bill
        cur.execute(
            "SELECT id, total_amount, amount_paid_at_entry FROM purchases "
            "WHERE supplier_id=? ORDER BY id ASC",
            (sid,))
        bills = cur.fetchall()
        remaining_credit = total_payments + total_returns
        for bill_id, bill_total, bill_entry_paid in bills:
            bill_total       = float(bill_total or 0)
            bill_entry_paid  = float(bill_entry_paid or 0)
            unpaid = round(bill_total - bill_entry_paid, 2)
            if unpaid <= 0:
                cur.execute("UPDATE purchases SET account_cleared=1 WHERE id=?", (bill_id,))
            elif remaining_credit >= unpaid:
                remaining_credit = round(remaining_credit - unpaid, 2)
                cur.execute("UPDATE purchases SET account_cleared=1 WHERE id=?", (bill_id,))
            else:
                cur.execute("UPDATE purchases SET account_cleared=0 WHERE id=?", (bill_id,))

        log(f"  Supplier '{sname}': purchased={total_purchased} entry_paid={total_entry_paid} "
            f"payments={total_payments} returns={total_returns} "
            f"=> due={total_due} credit={total_credit}")

    conn.commit()
    log("  All supplier balances recalculated.")


# ── Phase 6: Recalculate all customer balances ────────────────────────────────

def recalculate_all_customers(conn):
    cur = conn.cursor()
    log("Phase 6: Recalculating all customer balances...")

    cur.execute("SELECT id, name FROM customers")
    customers = cur.fetchall()
    log(f"  Found {len(customers)} customer(s).")

    for cid, cname in customers:
        cur.execute(
            "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(amount_paid),0) "
            "FROM sales WHERE customer_id=?", (cid,))
        row = cur.fetchone()
        total_billed = float(row[0])
        total_paid   = float(row[1])

        cur.execute(
            "SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns WHERE customer_id=?",
            (cid,))
        total_returns = float(cur.fetchone()[0])

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM customer_payments WHERE customer_id=?",
            (cid,))
        total_standalone = float(cur.fetchone()[0])

        net        = round(total_billed - total_paid - total_standalone - total_returns, 2)
        net_due    = round(max(0.0,  net), 2)
        net_credit = round(max(0.0, -net), 2)

        cur.execute(
            "UPDATE customers SET total_due=?, total_credit=?, last_updated=? WHERE id=?",
            (net_due, net_credit, datetime.now().isoformat(), cid))

        # account_cleared: bills with due_amount=0 stay cleared;
        # bills with due_amount>0 cleared only if total balance is 0
        if net_due == 0:
            cur.execute("UPDATE sales SET account_cleared=1 WHERE customer_id=?", (cid,))
        else:
            cur.execute(
                "UPDATE sales SET account_cleared=0 WHERE customer_id=? AND due_amount>0",
                (cid,))
            cur.execute(
                "UPDATE sales SET account_cleared=1 WHERE customer_id=? AND due_amount=0",
                (cid,))

        log(f"  Customer '{cname}': billed={total_billed} paid={total_paid} "
            f"standalone={total_standalone} returns={total_returns} "
            f"=> due={net_due} credit={net_credit}")

    conn.commit()
    log("  All customer balances recalculated.")


# ── Phase 7: Verify views and triggers ───────────────────────────────────────

def rebuild_views_and_triggers(conn):
    cur = conn.cursor()
    log("Phase 7: Rebuilding views and triggers...")

    # Drop and recreate supplier_due_status view
    cur.execute("DROP VIEW IF EXISTS supplier_due_status")
    cur.execute("DROP VIEW IF EXISTS bills_cleared")
    cur.execute("DROP VIEW IF EXISTS accounts_cleared")

    cur.execute("CREATE VIEW bills_cleared AS SELECT * FROM purchases WHERE bill_cleared=1")
    cur.execute("""
        CREATE VIEW accounts_cleared AS
        SELECT s.id AS supplier_id, s.name AS supplier_name,
               p.id AS cleared_at_purchase_id,
               p.purchase_no AS cleared_at_purchase_no,
               p.purchase_date AS cleared_date
        FROM suppliers s JOIN purchases p ON p.supplier_id=s.id
        WHERE p.account_cleared=1
          AND p.id=(SELECT MAX(p2.id) FROM purchases p2
                    WHERE p2.supplier_id=s.id AND p2.account_cleared=1)
    """)
    cur.execute("""
        CREATE VIEW supplier_due_status AS
        SELECT
            s.id   AS supplier_id,
            s.name AS supplier_name,
            COALESCE(s.total_due,    0) AS total_due,
            COALESCE(s.total_credit, 0) AS total_credit
        FROM suppliers s
    """)

    # Sales triggers
    for name in ('trg_sales_after_insert', 'trg_sales_after_update'):
        cur.execute(f"DROP TRIGGER IF EXISTS {name}")
    cur.execute("""
        CREATE TRIGGER trg_sales_after_insert AFTER INSERT ON sales BEGIN
            UPDATE sales SET
                bill_cleared = CASE WHEN NEW.due_amount = 0 THEN 1 ELSE 0 END
            WHERE id = NEW.id;
        END
    """)
    cur.execute("""
        CREATE TRIGGER trg_sales_after_update AFTER UPDATE ON sales BEGIN
            UPDATE sales SET
                bill_cleared = CASE WHEN NEW.due_amount = 0 THEN 1 ELSE 0 END
            WHERE id = NEW.id;
        END
    """)

    conn.commit()
    log("  Views and triggers rebuilt.")


# ── Phase 8: Final consistency audit ─────────────────────────────────────────

def audit(conn):
    cur = conn.cursor()
    log("Phase 8: Running final consistency audit...")
    errors = 0

    # Supplier audit
    cur.execute("SELECT id, name, total_due, total_credit FROM suppliers")
    for sid, sname, stored_due, stored_credit in cur.fetchall():
        cur.execute(
            "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(amount_paid_at_entry),0) "
            "FROM purchases WHERE supplier_id=?", (sid,))
        r = cur.fetchone()
        purchased, entry_paid = float(r[0]), float(r[1])

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE supplier_id=?",
            (sid,))
        payments = float(cur.fetchone()[0])

        cur.execute("""
            SELECT COALESCE(SUM(pr.refund_amount),0)
            FROM purchase_returns pr JOIN purchases p ON pr.purchase_id=p.id
            WHERE p.supplier_id=?
        """, (sid,))
        returns = float(cur.fetchone()[0])

        net          = round(purchased - entry_paid - payments - returns, 2)
        expected_due = round(max(0.0,  net), 2)
        expected_crd = round(max(0.0, -net), 2)

        if abs(float(stored_due) - expected_due) > 0.01:
            log(f"  [FAIL] Supplier '{sname}': stored_due={stored_due} expected={expected_due}")
            errors += 1
        else:
            log(f"  [OK]   Supplier '{sname}': due={expected_due} credit={expected_crd}")

    # Customer audit
    cur.execute("SELECT id, name, total_due, total_credit FROM customers")
    for cid, cname, stored_due, stored_credit in cur.fetchall():
        cur.execute(
            "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(amount_paid),0) "
            "FROM sales WHERE customer_id=?", (cid,))
        r = cur.fetchone()
        billed, paid = float(r[0]), float(r[1])

        cur.execute(
            "SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns WHERE customer_id=?",
            (cid,))
        returns = float(cur.fetchone()[0])

        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM customer_payments WHERE customer_id=?",
            (cid,))
        standalone = float(cur.fetchone()[0])

        net          = round(billed - paid - standalone - returns, 2)
        expected_due = round(max(0.0,  net), 2)
        expected_crd = round(max(0.0, -net), 2)

        if abs(float(stored_due) - expected_due) > 0.01:
            log(f"  [FAIL] Customer '{cname}': stored_due={stored_due} expected={expected_due}")
            errors += 1
        else:
            log(f"  [OK]   Customer '{cname}': due={expected_due} credit={expected_crd}")

    # Stock sanity
    cur.execute("SELECT id, name, stock_qty FROM medicines WHERE stock_qty < 0")
    neg = cur.fetchall()
    if neg:
        log(f"  [WARN] {len(neg)} medicine(s) have negative stock: {[(r[1], r[2]) for r in neg]}")
        errors += 1
    else:
        log("  [OK]   No negative stock.")

    # Orphan check
    cur.execute("""
        SELECT COUNT(*) FROM supplier_payments sp
        LEFT JOIN suppliers s ON sp.supplier_id=s.id WHERE s.id IS NULL
    """)
    n = cur.fetchone()[0]
    if n:
        log(f"  [WARN] {n} orphaned supplier_payments remain.")
        errors += 1
    else:
        log("  [OK]   No orphaned supplier_payments.")

    cur.execute("""
        SELECT COUNT(*) FROM purchase_returns pr
        LEFT JOIN purchases p ON pr.purchase_id=p.id WHERE p.id IS NULL
    """)
    n = cur.fetchone()[0]
    if n:
        log(f"  [WARN] {n} orphaned purchase_returns remain.")
        errors += 1
    else:
        log("  [OK]   No orphaned purchase_returns.")

    if errors == 0:
        log("Audit PASSED — all balances consistent.")
    else:
        log(f"Audit completed with {errors} issue(s).")

    return errors


# ── Phase 9: Reset migration flag so startup re-runs it ──────────────────────

def reset_migration_flag(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO settings (name, value) VALUES ('accounting_v2_migrated','1')")
    conn.commit()
    log("Migration flag set to '1' (complete).")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(DB_PATH):
        log(f"ERROR: Database not found at {DB_PATH}")
        return

    log(f"Database: {DB_PATH}")
    log("=" * 60)

    # Step 0: Backup
    backup_db()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")  # allow cleanup without FK errors

    try:
        ensure_tables(conn)
        ensure_columns(conn)
        backfill_entry_paid(conn)
        clean_orphaned_payments(conn)
        clean_orphaned_purchase_returns(conn)
        clean_orphaned_sales_returns(conn)
        recalculate_all_suppliers(conn)
        recalculate_all_customers(conn)
        rebuild_views_and_triggers(conn)
        errors = audit(conn)
        reset_migration_flag(conn)

        log("=" * 60)
        if errors == 0:
            log("SUCCESS: Database is fully consistent with the new accounting model.")
        else:
            log(f"COMPLETED WITH {errors} WARNING(S) — review output above.")
    except Exception as e:
        conn.rollback()
        log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


if __name__ == '__main__':
    main()
