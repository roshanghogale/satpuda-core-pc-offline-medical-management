"""
core/db_setup.py
────────────────
All database schema creation, migrations, triggers and views.
Called once at startup from main.py — no UI code here.
"""
import sqlite3
import re
import threading
import time


# ── Table definitions ─────────────────────────────────────────────────────────

_TABLES = [
    """CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, phone TEXT, address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        document_name TEXT,
        total_due    REAL DEFAULT 0,
        total_credit REAL DEFAULT 0,
        last_updated TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, phone TEXT, registration_number TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, address TEXT, phone TEXT,
        gstin TEXT, dl_numbers TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        total_due    REAL DEFAULT 0,
        total_credit REAL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS medicines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, type TEXT,
        stock_qty INTEGER DEFAULT 0, unit TEXT,
        gst_percent REAL, mrp REAL, rate REAL,
        manufacturer TEXT, batch_no TEXT, expiry_date DATE,
        hsn_code TEXT, schedule TEXT, location TEXT, content_drug TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_no TEXT UNIQUE, customer_id INTEGER, bill_date DATE,
        total_amount REAL, discount REAL DEFAULT 0, rounding REAL DEFAULT 0,
        amount_paid REAL DEFAULT 0, cash_paid REAL DEFAULT 0,
        online_paid REAL DEFAULT 0, previous_due REAL DEFAULT 0,
        previous_credit REAL DEFAULT 0, total_due REAL DEFAULT 0,
        due_amount REAL DEFAULT 0, credit_amount REAL DEFAULT 0,
        paid_due REAL DEFAULT 0, bill_cleared INTEGER DEFAULT 0,
        account_cleared INTEGER DEFAULT 0, doctor_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        discount_pct REAL DEFAULT 0,
        customer_name TEXT,
        customer_phone TEXT,
        customer_address TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS sales_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER, medicine_id INTEGER,
        qty INTEGER, rate REAL, gst_percent REAL, amount REAL,
        item_discount REAL DEFAULT 0, cost_price REAL DEFAULT 0,
        FOREIGN KEY (sale_id) REFERENCES sales (id),
        FOREIGN KEY (medicine_id) REFERENCES medicines (id)
    )""",
    """CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_no TEXT UNIQUE, supplier_id INTEGER,
        purchase_date DATE, bill_number TEXT,
        subtotal REAL DEFAULT 0, total_gst REAL DEFAULT 0,
        cgst REAL DEFAULT 0, sgst REAL DEFAULT 0,
        total_amount REAL DEFAULT 0, overall_discount REAL DEFAULT 0,
        rounding REAL DEFAULT 0, need_to_pay REAL DEFAULT 0,
        final_amount REAL DEFAULT 0,
        amount_paid REAL DEFAULT 0, cash_paid_at_entry REAL DEFAULT 0,
        online_paid_at_entry REAL DEFAULT 0, previous_due REAL DEFAULT 0,
        previous_credit REAL DEFAULT 0, due REAL DEFAULT 0,
        current_credit REAL DEFAULT 0, total_due REAL DEFAULT 0,
        bill_cleared INTEGER DEFAULT 0, account_cleared INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        due_amount           REAL DEFAULT 0,
        credit_amount        REAL DEFAULT 0,
        paid_due             REAL DEFAULT 0,
        gst_calc_method      TEXT,
        amount_paid_at_entry REAL DEFAULT 0,
        expenditure REAL DEFAULT 0,
        FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS purchase_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_id INTEGER, medicine_id INTEGER,
        qty REAL DEFAULT 0, free_qty REAL DEFAULT 0, type TEXT,
        hsn_code TEXT, gst_pct REAL DEFAULT 0, mrp REAL DEFAULT 0,
        rate REAL DEFAULT 0, manufacturer TEXT, batch_no TEXT,
        expiry_date DATE, schedule TEXT,
        discount_pct     REAL DEFAULT 0, taxable    REAL DEFAULT 0,
        gst_amt          REAL DEFAULT 0, item_amount REAL DEFAULT 0,
        discount_percent REAL DEFAULT 0,
        gst_value        REAL DEFAULT 0,
        amount           REAL DEFAULT 0,
        FOREIGN KEY (purchase_id) REFERENCES purchases (id),
        FOREIGN KEY (medicine_id) REFERENCES medicines (id)
    )""",
    """CREATE TABLE IF NOT EXISTS shelves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shelf_no TEXT UNIQUE, description TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS medicine_shelf (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_id INTEGER, shelf_id INTEGER,
        FOREIGN KEY (medicine_id) REFERENCES medicines (id),
        FOREIGN KEY (shelf_id) REFERENCES shelves (id)
    )""",
    """CREATE TABLE IF NOT EXISTS racks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rack_id INTEGER, name TEXT NOT NULL,
        FOREIGN KEY (rack_id) REFERENCES racks (id)
    )""",
    """CREATE TABLE IF NOT EXISTS boxes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER, name TEXT NOT NULL,
        FOREIGN KEY (section_id) REFERENCES sections (id)
    )""",
    """CREATE TABLE IF NOT EXISTS shelf_settings (
        id INTEGER PRIMARY KEY, show_location INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, value TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS pharmacy_profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, address TEXT, phone TEXT, email TEXT,
        gstin TEXT, dl_number TEXT,
        gst_enabled INTEGER DEFAULT 1, logo_path TEXT,
        fssai_number TEXT, show_fssai_on_bill INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS customer_payments (
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
        FOREIGN KEY (customer_id) REFERENCES customers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS supplier_payments (
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
        FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS purchase_returns (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        return_no     TEXT UNIQUE,
        purchase_id   INTEGER,
        supplier_id   INTEGER,
        return_date   DATE,
        refund_amount REAL DEFAULT 0,
        discount      REAL DEFAULT 0,
        reason        TEXT,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (purchase_id) REFERENCES purchases (id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS purchase_return_items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id   INTEGER,
        medicine_id INTEGER,
        qty         REAL DEFAULT 0,
        rate        REAL DEFAULT 0,
        amount      REAL DEFAULT 0,
        -- qty is in STRIPS for tablet medicines; stock_units is what the line
        -- actually took off the shelf, so deleting the return can give back the
        -- same amount instead of the raw strip count.
        stock_units REAL,
        FOREIGN KEY (return_id)   REFERENCES purchase_returns (id),
        FOREIGN KEY (medicine_id) REFERENCES medicines (id)
    )""",
    """CREATE TABLE IF NOT EXISTS sales_returns (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        return_no     TEXT UNIQUE,
        sale_id       INTEGER,
        customer_id   INTEGER,
        return_date   DATE,
        refund_amount REAL DEFAULT 0,
        discount      REAL DEFAULT 0,
        reason        TEXT,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (sale_id)     REFERENCES sales (id),
        FOREIGN KEY (customer_id) REFERENCES customers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS sales_return_items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        return_id   INTEGER,
        medicine_id INTEGER,
        qty         REAL DEFAULT 0,
        rate        REAL DEFAULT 0,
        amount      REAL DEFAULT 0,
        FOREIGN KEY (return_id)   REFERENCES sales_returns (id),
        FOREIGN KEY (medicine_id) REFERENCES medicines (id)
    )""",
    """CREATE TABLE IF NOT EXISTS general_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL COLLATE NOCASE UNIQUE,
        rate REAL DEFAULT 0,
        mrp REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS medicine_suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_name TEXT NOT NULL,
        supplier_id INTEGER NOT NULL,
        last_rate REAL DEFAULT 0,
        last_purchase_date DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(medicine_name, supplier_id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS pending_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT UNIQUE,
        medicine_id INTEGER,
        medicine_name TEXT NOT NULL,
        pack_size TEXT,
        supplier_id INTEGER,
        supplier_name_manual TEXT,
        supplier_phone TEXT,
        supplier_email TEXT,
        order_offline INTEGER DEFAULT 0,
        offline_note TEXT,
        quantity REAL NOT NULL DEFAULT 0,
        unit_price REAL DEFAULT 0,
        current_stock REAL DEFAULT 0,
        min_stock REAL DEFAULT 0,
        order_date DATE,
        expected_delivery_date DATE,
        status TEXT DEFAULT 'draft',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (medicine_id) REFERENCES medicines (id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
    )""",
    """CREATE TABLE IF NOT EXISTS stock_disposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        disposal_no TEXT UNIQUE,
        medicine_id INTEGER NOT NULL,
        batch_no TEXT,
        supplier_id INTEGER,
        purchase_id INTEGER,
        bill_number TEXT,
        quantity REAL NOT NULL DEFAULT 0,
        original_purchase_qty REAL,
        reason TEXT,
        disposal_type TEXT NOT NULL,
        expected_credit_note INTEGER DEFAULT 0,
        notes TEXT,
        disposal_date DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (medicine_id) REFERENCES medicines (id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers (id),
        FOREIGN KEY (purchase_id) REFERENCES purchases (id)
    )""",
]


# ── Public entry point ────────────────────────────────────────────────────────

def initialise(conn: sqlite3.Connection):
    """Create all tables, run all migrations, create triggers and views."""
    cur = conn.cursor()
    for sql in _TABLES:
        cur.execute(sql)
    conn.commit()

    _migrate_all(cur, conn)

    from core.customer_service import migrate_schema
    migrate_schema(conn)

    _run_one_time_migration(conn)

    try:
        from core.pharmacy_profile_io import ensure_pharmacy_profile_table

        ensure_pharmacy_profile_table(conn)
    except Exception:
        pass

    try:
        from core.name_utils import normalize_medicine_names_in_db
        normalize_medicine_names_in_db(conn)
    except Exception:
        pass


# ── Migrations ────────────────────────────────────────────────────────────────

# Bump when expensive startup-only migrations change (triggers, views, location fix).
_STARTUP_MIGRATION_VERSION = 1


def _get_startup_migration_version(cur) -> int:
    try:
        cur.execute(
            "SELECT value FROM settings WHERE name='startup_migration_version'")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _set_startup_migration_version(cur, conn, version: int) -> None:
    cur.execute(
        "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
        ('startup_migration_version', str(version)),
    )
    conn.commit()


def _migrate_all(cur, conn):
    _migrate_doctors(cur)
    _migrate_medicines(cur, conn)
    _migrate_purchases(cur)
    _migrate_purchase_items(cur)
    _migrate_line_item_medicine_id(cur)
    _migrate_pharmacy_profile(cur)
    _migrate_sales(cur)
    _migrate_sales_due_formula(conn)
    _migrate_bill_credit_overpay_only(conn)
    _migrate_customer_payments(cur)
    _migrate_suppliers(cur)
    _migrate_purchase_items_entry_paid(cur)
    _migrate_purchase_payment_split(cur)
    _migrate_purchase_numbers_compact(cur, conn)
    try:
        from core.fy_serial import migrate_fy_serial_numbers

        migrate_fy_serial_numbers(conn)
    except Exception as e:
        print(f"fy serial migration: {e}")
    try:
        from core.db_repair import repair_missing_purchases
        from core.store_manager import get_active_db_path

        repair_missing_purchases(conn, db_path=get_active_db_path())
    except Exception as e:
        print(f"purchase repair: {e}")
    _migrate_pending_orders(cur)
    # Sync metadata columns (created_at/updated_at/version/device_id/deleted/sync_status).
    # Schema only — conflict logic / bootstrap / listeners unchanged this step.
    from core.sync_metadata_schema import ensure_sync_metadata_schema
    ensure_sync_metadata_schema(conn)
    try:
        from core.sync_v3.schema import ensure_sync_v3_schema

        ensure_sync_v3_schema(conn)
    except Exception as e:
        print(f"sync_v3 schema: {e}")

    if _get_startup_migration_version(cur) < _STARTUP_MIGRATION_VERSION:
        _create_purchase_triggers(cur)
        _create_purchase_views(cur)
        _create_sales_triggers(cur)
        _migrate_location_format(cur)
        _set_startup_migration_version(cur, conn, _STARTUP_MIGRATION_VERSION)

    # Runs on every open, not only behind the migration-version gate: both the
    # Tauri engine and the classic UI write this column, and a store that misses
    # it fails the purchase-return save outright.
    try:
        _alter_if_missing(cur, 'purchase_return_items', 'stock_units', 'REAL')
    except Exception as e:
        print(f"purchase_return_items.stock_units migration: {e}")

    conn.commit()


def _migrate_pending_orders(cur):
    try:
        _alter_if_missing(cur, 'pending_orders', 'order_group_id', 'TEXT')
    except Exception as e:
        print(f"pending_orders migration: {e}")


def _alter_if_missing(cur, table, col, col_type):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [c[1] for c in cur.fetchall()]
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    return cols


def _migrate_doctors(cur):
    try:
        _alter_if_missing(cur, 'doctors', 'registration_number', 'TEXT')
    except Exception as e:
        print(f"doctors migration: {e}")


def _migrate_medicines(cur, conn):
    try:
        cur.execute("PRAGMA table_info(medicines)")
        cols = [c[1] for c in cur.fetchall()]
        for col in ('content_drug', 'unit', 'location'):
            if col not in cols:
                cur.execute(f"ALTER TABLE medicines ADD COLUMN {col} TEXT")
        if 'is_hidden' not in cols:
            cur.execute("ALTER TABLE medicines ADD COLUMN is_hidden INTEGER DEFAULT 0")
        if 'synced_at' not in cols:
            cur.execute("ALTER TABLE medicines ADD COLUMN synced_at TEXT")
        from core.medicine_sync_merge import ensure_medicine_sync_triggers
        ensure_medicine_sync_triggers(cur)
        # Rebuild only for truly legacy/unexpected columns.
        # Sync metadata columns are normal — do NOT recreate every startup.
        expected = {
            'id', 'name', 'type', 'stock_qty', 'unit', 'gst_percent', 'mrp', 'rate',
            'manufacturer', 'batch_no', 'expiry_date', 'hsn_code', 'schedule',
            'location', 'content_drug', 'is_hidden', 'synced_at', 'created_at',
            'updated_at', 'version', 'device_id', 'deleted', 'sync_status',
        }
        unexpected = [c for c in cols if c not in expected]
        if unexpected:
            _recreate_medicines(cur)
    except Exception as e:
        print(f"medicines migration: {e}")


def _recreate_medicines(cur):
    """
    Rebuild medicines table preserving ALL columns including content_drug.
    Uses explicit column mapping — never relies on positional row[:N] slicing.
    """
    try:
        # Read existing column names so we can map safely
        cur.execute("PRAGMA table_info(medicines)")
        existing_cols = {r[1] for r in cur.fetchall()}

        cur.execute("SELECT id, name, type, stock_qty, unit, gst_percent, mrp, rate,"
                    " manufacturer, batch_no, expiry_date, hsn_code, schedule, location,"
                    " content_drug, created_at, COALESCE(is_hidden, 0), synced_at FROM medicines")
        backup = cur.fetchall()

        cur.execute("DROP TABLE IF EXISTS medicines")
        cur.execute("""CREATE TABLE medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, type TEXT,
            stock_qty INTEGER DEFAULT 0, unit TEXT,
            gst_percent REAL, mrp REAL, rate REAL,
            manufacturer TEXT, batch_no TEXT, expiry_date DATE,
            hsn_code TEXT, schedule TEXT, location TEXT, content_drug TEXT,
            is_hidden INTEGER DEFAULT 0,
            synced_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        for row in backup:
            cur.execute("""
                INSERT INTO medicines
                    (id, name, type, stock_qty, unit, gst_percent, mrp, rate,
                     manufacturer, batch_no, expiry_date, hsn_code, schedule,
                     location, content_drug, created_at, is_hidden, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, row)
        from core.medicine_sync_merge import ensure_medicine_sync_triggers
        ensure_medicine_sync_triggers(cur)
    except Exception as e:
        print(f"recreate medicines: {e}")


def _migrate_suppliers(cur):
    """Add total_due / total_credit to suppliers if missing."""
    try:
        _alter_if_missing(cur, 'suppliers', 'total_due',    'REAL DEFAULT 0')
        _alter_if_missing(cur, 'suppliers', 'total_credit', 'REAL DEFAULT 0')
    except Exception as e:
        print(f"suppliers migration: {e}")


def _migrate_purchase_items_entry_paid(cur):
    """
    Add amount_paid_at_entry to purchases.
    This column is write-once (set on INSERT, never mutated by payment tab).

    Back-fill logic:
      entry_paid = total_amount - due
      where 'due' was stored at purchase-save time (before payment tab touched amount_paid).
    Falls back to amount_paid if due column is also 0.
    """
    try:
        _alter_if_missing(cur, 'purchases', 'amount_paid_at_entry', 'REAL DEFAULT 0')
        # Reconstruct from total_amount - due (due was stored at save time)
        cur.execute("""
            UPDATE purchases
            SET amount_paid_at_entry = MAX(0, COALESCE(total_amount,0) - COALESCE(due,0))
            WHERE (amount_paid_at_entry = 0 OR amount_paid_at_entry IS NULL)
              AND COALESCE(total_amount,0) > 0
        """)
        # For rows where due is also 0 (fully paid at entry), use amount_paid
        cur.execute("""
            UPDATE purchases
            SET amount_paid_at_entry = COALESCE(amount_paid, 0)
            WHERE (amount_paid_at_entry = 0 OR amount_paid_at_entry IS NULL)
        """)
    except Exception as e:
        print(f"purchase entry_paid migration: {e}")


def _migrate_purchase_payment_split(cur):
    """Add cash/online entry columns; backfill legacy paid-at-entry into Cash.

    Semantics:
      cash_paid_at_entry   = cash paid when the purchase was entered
      online_paid_at_entry = online paid when the purchase was entered

    Old databases only had a single entry payment (amount_paid_at_entry /
    amount_paid). On upgrade that amount is treated as Cash by default;
    Online stays 0 until the user edits it.
    """
    try:
        _alter_if_missing(cur, 'purchases', 'cash_paid_at_entry', 'REAL DEFAULT 0')
        _alter_if_missing(cur, 'purchases', 'online_paid_at_entry', 'REAL DEFAULT 0')

        # One-time: put the whole legacy entry payment into Cash.
        # Re-run safely while both split columns are still at default 0
        # (meaning the row was never edited with the new cash/online UI).
        cur.execute("""
            UPDATE purchases
            SET cash_paid_at_entry = COALESCE(
                    NULLIF(amount_paid_at_entry, 0),
                    NULLIF(amount_paid, 0),
                    0
                ),
                online_paid_at_entry = 0
            WHERE COALESCE(cash_paid_at_entry, 0) = 0
              AND COALESCE(online_paid_at_entry, 0) = 0
              AND COALESCE(
                    NULLIF(amount_paid_at_entry, 0),
                    NULLIF(amount_paid, 0),
                    0
                  ) > 0
        """)
    except Exception as e:
        print(f"purchase payment split migration: {e}")


_purchase_pay_migrate_lock = threading.Lock()


def ensure_purchase_payment_columns(conn):
    """Idempotent: add cash/online purchase columns and backfill Cash from legacy entry paid.

    Safe to call from UI threads — uses a lock and skips work when columns already exist.
    """
    with _purchase_pay_migrate_lock:
        cur = conn.cursor()
        try:
            cur.execute('PRAGMA busy_timeout=30000')
        except Exception:
            pass
        try:
            cur.execute('PRAGMA table_info(purchases)')
            cols = {r[1] for r in cur.fetchall()}
        except Exception:
            cols = set()
        need_entry = 'amount_paid_at_entry' not in cols
        need_split = (
            'cash_paid_at_entry' not in cols or 'online_paid_at_entry' not in cols
        )
        if not need_entry and not need_split:
            # Columns exist — still backfill cash from legacy when both split cols are 0.
            try:
                _migrate_purchase_payment_split(cur)
                conn.commit()
            except Exception as e:
                print(f"purchase payment split migration: {e}")
                try:
                    conn.rollback()
                except Exception:
                    pass
            return

        last_err = None
        for _attempt in range(5):
            try:
                if need_entry:
                    _migrate_purchase_items_entry_paid(cur)
                _migrate_purchase_payment_split(cur)
                conn.commit()
                return
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                try:
                    conn.rollback()
                except Exception:
                    pass
                if 'locked' in msg or 'busy' in msg:
                    time.sleep(0.35)
                    continue
                print(f"ensure_purchase_payment_columns: {e}")
                return
        if last_err:
            print(f"ensure_purchase_payment_columns: {last_err}")


def _migrate_purchase_numbers_compact(cur, conn):
    """Renumber saved purchases to compact 1, 2, 3… by entry date order."""
    try:
        cur.execute(
            "SELECT value FROM settings WHERE name='purchase_no_compact_migrated'"
        )
        row = cur.fetchone()
        if row and str(row[0]) == '1':
            return
        cur.execute("""
            SELECT id FROM purchases
            WHERE COALESCE(is_autosave, 0) = 0
            ORDER BY purchase_date ASC, id ASC
        """)
        ids = [int(r[0]) for r in cur.fetchall()]
        for seq, pid in enumerate(ids, start=1):
            cur.execute(
                "UPDATE purchases SET purchase_no=? WHERE id=?",
                (str(seq), pid),
            )
        cur.execute(
            "INSERT OR REPLACE INTO settings (name, value) VALUES "
            "('purchase_no_compact_migrated', '1')"
        )
        conn.commit()
        if ids:
            print(f"[MIGRATION] Purchase numbers compacted 1..{len(ids)}.")
    except Exception as e:
        print(f"purchase_no compact migration: {e}")


def _migrate_purchases(cur):
    try:
        cur.execute("PRAGMA table_info(purchases)")
        cols = [c[1] for c in cur.fetchall()]
        new_cols = [
            ('bill_number','TEXT'), ('subtotal','REAL DEFAULT 0'),
            ('total_gst','REAL DEFAULT 0'), ('cgst','REAL DEFAULT 0'),
            ('sgst','REAL DEFAULT 0'), ('total_amount','REAL DEFAULT 0'),
            ('overall_discount','REAL DEFAULT 0'), ('rounding','REAL DEFAULT 0'),
            ('need_to_pay','REAL DEFAULT 0'), ('final_amount','REAL DEFAULT 0'),
            ('amount_paid','REAL DEFAULT 0'), ('previous_due','REAL DEFAULT 0'),
            ('previous_credit','REAL DEFAULT 0'), ('due','REAL DEFAULT 0'),
            ('current_credit','REAL DEFAULT 0'), ('total_due','REAL DEFAULT 0'),
            ('bill_cleared','INTEGER DEFAULT 0'), ('account_cleared','INTEGER DEFAULT 0'),
            # legacy aliases
            ('due_amount','REAL DEFAULT 0'), ('credit_amount','REAL DEFAULT 0'),
            ('paid_due','REAL DEFAULT 0'), ('gst_calc_method','TEXT'),
            ('expenditure','REAL DEFAULT 0'),
            ('is_autosave', 'INTEGER DEFAULT 0'),
        ]
        for col, col_type in new_cols:
            if col not in cols:
                cur.execute(f"ALTER TABLE purchases ADD COLUMN {col} {col_type}")
        if 'due' not in cols and 'due_amount' in cols:
            cur.execute("UPDATE purchases SET due=COALESCE(due_amount,0) WHERE due IS NULL OR due=0")
        if 'current_credit' not in cols and 'credit_amount' in cols:
            cur.execute("UPDATE purchases SET current_credit=COALESCE(credit_amount,0) WHERE current_credit IS NULL OR current_credit=0")
        if 'final_amount' not in cols:
            cur.execute("UPDATE purchases SET final_amount=COALESCE(total_amount,0) WHERE final_amount IS NULL OR final_amount=0")
    except Exception as e:
        print(f"purchases migration: {e}")


def _migrate_line_item_medicine_id(cur):
    """Legacy DBs may use med_id on line-item tables instead of medicine_id."""
    for table in (
        'sales_items', 'purchase_items', 'sales_return_items', 'purchase_return_items',
    ):
        try:
            cur.execute(f'PRAGMA table_info({table})')
            cols = {c[1] for c in cur.fetchall()}
            if 'medicine_id' not in cols and 'med_id' in cols:
                cur.execute(f'ALTER TABLE {table} RENAME COLUMN med_id TO medicine_id')
            elif 'medicine_id' in cols and 'med_id' in cols:
                cur.execute(
                    f"UPDATE {table} SET medicine_id=med_id "
                    f"WHERE (medicine_id IS NULL OR medicine_id=0) AND med_id IS NOT NULL AND med_id!=0"
                )
        except Exception as e:
            print(f'line item medicine_id migration ({table}): {e}')


def _migrate_purchase_items(cur):
    try:
        cur.execute("PRAGMA table_info(purchase_items)")
        cols = [c[1] for c in cur.fetchall()]
        new_cols = [
            ('discount_pct','REAL DEFAULT 0'), ('taxable','REAL DEFAULT 0'),
            ('gst_amt','REAL DEFAULT 0'), ('item_amount','REAL DEFAULT 0'),
            ('gst_pct','REAL DEFAULT 0'),
            # legacy aliases
            ('discount_percent','REAL DEFAULT 0'), ('gst_value','REAL DEFAULT 0'),
            ('amount','REAL DEFAULT 0'),
            # Stock units the line added when it was saved (NULL on older rows), so an
            # edit takes back exactly that and not a recomputation from today's unit.
            ('stock_units','REAL'),
        ]
        for col, col_type in new_cols:
            if col not in cols:
                cur.execute(f"ALTER TABLE purchase_items ADD COLUMN {col} {col_type}")
        if 'gst_pct' not in cols and 'gst_value' in cols:
            cur.execute("UPDATE purchase_items SET gst_pct=COALESCE(gst_value,0) WHERE gst_pct IS NULL OR gst_pct=0")
        if 'discount_pct' not in cols and 'discount_percent' in cols:
            cur.execute("UPDATE purchase_items SET discount_pct=COALESCE(discount_percent,0) WHERE discount_pct IS NULL OR discount_pct=0")
        if 'item_amount' not in cols and 'amount' in cols:
            cur.execute("UPDATE purchase_items SET item_amount=COALESCE(amount,0) WHERE item_amount IS NULL OR item_amount=0")
    except Exception as e:
        print(f"purchase_items migration: {e}")


def _migrate_pharmacy_profile(cur):
    try:
        _alter_if_missing(cur, 'pharmacy_profile', 'gst_enabled', 'INTEGER DEFAULT 1')
        _alter_if_missing(cur, 'pharmacy_profile', 'logo_path', 'TEXT')
        _alter_if_missing(cur, 'pharmacy_profile', 'fssai_number', 'TEXT')
        _alter_if_missing(cur, 'pharmacy_profile', 'show_fssai_on_bill', 'INTEGER DEFAULT 0')
        _migrate_fssai_print_flag(cur)
    except Exception as e:
        print(f"pharmacy_profile migration: {e}")


def _migrate_fssai_print_flag(cur):
    """One-shot: keep printing FSSAI for shops that were already printing it.

    "Print FSSAI on sale bills" was saved and never read -- the bill templates
    printed the number whenever one existed. Making the checkbox real would
    therefore have removed the FSSAI line from every shop that has a number and
    never ticked the box, which is not a change to make silently to a licence
    number on a bill. Turn it on once for exactly those shops; after that the
    checkbox is theirs.
    """
    try:
        cur.execute(
            "SELECT value FROM settings WHERE name='fssai_print_flag_migrated'"
        )
        if cur.fetchone():
            return
        cur.execute(
            "UPDATE pharmacy_profile SET show_fssai_on_bill=1 "
            "WHERE COALESCE(TRIM(fssai_number), '') != '' "
            "AND COALESCE(show_fssai_on_bill, 0) = 0"
        )
        n = cur.rowcount or 0
        cur.execute(
            "INSERT OR REPLACE INTO settings (name, value) "
            "VALUES ('fssai_print_flag_migrated', '1')"
        )
        if n:
            print(f"[MIGRATION] FSSAI printing kept on for {n} store profile(s).")
    except Exception as e:
        print(f"fssai print flag migration: {e}")


def _migrate_sales(cur):
    try:
        cur.execute("PRAGMA table_info(sales)")
        cols = [c[1] for c in cur.fetchall()]
        new_cols = [
            ('rounding',         'REAL DEFAULT 0'),
            ('cash_paid',        'REAL DEFAULT 0'),
            ('online_paid',      'REAL DEFAULT 0'),
            ('previous_credit',  'REAL DEFAULT 0'),
            ('paid_due',         'REAL DEFAULT 0'),
            ('bill_cleared',     'INTEGER DEFAULT 0'),
            ('account_cleared',  'INTEGER DEFAULT 0'),
            ('discount_pct',     'REAL DEFAULT 0'),
            ('is_autosave',      'INTEGER DEFAULT 0'),
            # Align with server + Android denormalized customer fields
            ('customer_name',    'TEXT'),
            ('customer_phone',   'TEXT'),
            ('customer_address', 'TEXT'),
        ]
        for col, col_type in new_cols:
            if col not in cols:
                cur.execute(f"ALTER TABLE sales ADD COLUMN {col} {col_type}")
        # Backfill names/phones from customers (same as Android MIGRATION_10_11)
        cur.execute("PRAGMA table_info(sales)")
        cols_after = [c[1] for c in cur.fetchall()]
        if 'customer_name' in cols_after:
            cur.execute(
                """
                UPDATE sales SET
                    customer_name = COALESCE(
                        NULLIF(TRIM(customer_name), ''),
                        (SELECT c.name FROM customers c WHERE c.id = sales.customer_id)
                    ),
                    customer_phone = COALESCE(
                        NULLIF(TRIM(COALESCE(customer_phone, '')), ''),
                        (SELECT c.phone FROM customers c WHERE c.id = sales.customer_id)
                    ),
                    customer_address = COALESCE(
                        NULLIF(TRIM(COALESCE(customer_address, '')), ''),
                        (SELECT c.address FROM customers c WHERE c.id = sales.customer_id)
                    )
                WHERE customer_id IS NOT NULL
                  AND (
                    customer_name IS NULL OR TRIM(customer_name) = ''
                    OR customer_phone IS NULL OR TRIM(COALESCE(customer_phone,'')) = ''
                    OR customer_address IS NULL OR TRIM(COALESCE(customer_address,'')) = ''
                  )
                """
            )
        if 'phone_pay_paid' in cols:
            _rebuild_sales_table(cur)
        # sales_items columns
        cur.execute("PRAGMA table_info(sales_items)")
        si_cols = [c[1] for c in cur.fetchall()]
        for col, col_type in [('item_discount', 'REAL DEFAULT 0'),
                               ('cost_price',   'REAL DEFAULT 0')]:
            if col not in si_cols:
                cur.execute(f"ALTER TABLE sales_items ADD COLUMN {col} {col_type}")
    except Exception as e:
        print(f"sales migration: {e}")


def _migrate_sales_due_formula(conn):
    """One-time repair: bill due = total - paid; total due = prev due + bill due."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT value FROM settings WHERE name='sales_due_formula_migrated'")
        row = cur.fetchone()
        if row and row[0] == '1':
            return

        from core.billing_service import repair_sales_due_fields
        n = repair_sales_due_fields(conn)
        print(f"[MIGRATION] Repaired sales due fields on {n} bill(s).")
        cur.execute(
            "INSERT OR REPLACE INTO settings (name, value) VALUES ('sales_due_formula_migrated','1')")
        conn.commit()
    except Exception as e:
        print(f"sales due formula migration: {e}")


def _migrate_bill_credit_overpay_only(conn):
    """One-time: bill credit = overpay only (not leftover previous credit)."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT value FROM settings WHERE name='bill_credit_overpay_only_v1'")
        row = cur.fetchone()
        if row and row[0] == '1':
            return

        from core.billing_service import repair_sales_due_fields
        from core.purchase_service import repair_purchase_credit_fields

        n_sales = repair_sales_due_fields(conn)
        n_purch = repair_purchase_credit_fields(conn)
        print(
            f"[MIGRATION] Cleared false bill credit on {n_sales} sale(s) "
            f"and {n_purch} purchase(s)."
        )
        cur.execute(
            "INSERT OR REPLACE INTO settings (name, value) "
            "VALUES ('bill_credit_overpay_only_v1','1')"
        )
        conn.commit()
    except Exception as e:
        print(f"bill credit overpay migration: {e}")


def _rebuild_sales_table(cur):
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS sales_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_no TEXT UNIQUE, customer_id INTEGER, bill_date DATE,
            total_amount REAL, discount REAL DEFAULT 0, rounding REAL DEFAULT 0,
            amount_paid REAL DEFAULT 0, cash_paid REAL DEFAULT 0,
            online_paid REAL DEFAULT 0, previous_due REAL DEFAULT 0,
            previous_credit REAL DEFAULT 0, total_due REAL DEFAULT 0,
            due_amount REAL DEFAULT 0, credit_amount REAL DEFAULT 0,
            paid_due REAL DEFAULT 0, bill_cleared INTEGER DEFAULT 0,
            account_cleared INTEGER DEFAULT 0, doctor_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            discount_pct REAL DEFAULT 0,
            customer_name TEXT,
            customer_phone TEXT,
            customer_address TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )""")
        cur.execute("PRAGMA table_info(sales)")
        old_cols = {c[1] for c in cur.fetchall()}
        has_name = 'customer_name' in old_cols
        name_sel = "customer_name" if has_name else "NULL"
        phone_sel = "customer_phone" if 'customer_phone' in old_cols else "NULL"
        addr_sel = "customer_address" if 'customer_address' in old_cols else "NULL"
        disc_sel = "discount_pct" if 'discount_pct' in old_cols else "0"
        cur.execute(f"""INSERT INTO sales_new
            (id,bill_no,customer_id,bill_date,total_amount,discount,rounding,
             amount_paid,cash_paid,online_paid,previous_due,previous_credit,
             total_due,due_amount,credit_amount,paid_due,bill_cleared,
             account_cleared,doctor_name,created_at,discount_pct,
             customer_name,customer_phone,customer_address)
            SELECT id,bill_no,customer_id,bill_date,total_amount,discount,rounding,
             amount_paid,cash_paid,online_paid,previous_due,previous_credit,
             total_due,due_amount,credit_amount,paid_due,bill_cleared,
             account_cleared,doctor_name,created_at,{disc_sel},
             {name_sel},{phone_sel},{addr_sel} FROM sales""")
        cur.execute("DROP TABLE sales")
        cur.execute("ALTER TABLE sales_new RENAME TO sales")
    except Exception as e:
        print(f"rebuild sales: {e}")


# ── Triggers ──────────────────────────────────────────────────────────────────

def _create_purchase_triggers(cur):
    try:
        for name in ('trg_purchases_after_insert', 'trg_purchases_after_update',
                     'trg_purchases_insert_log', 'trg_purchases_update_log'):
            cur.execute(f"DROP TRIGGER IF EXISTS {name}")
        cur.execute("""
            CREATE TRIGGER trg_purchases_after_insert AFTER INSERT ON purchases BEGIN
                UPDATE purchases SET
                    bill_cleared    = CASE WHEN NEW.due_amount = 0 THEN 1 ELSE 0 END,
                    account_cleared = CASE WHEN NEW.total_due  = 0 THEN 1 ELSE 0 END
                WHERE id = NEW.id;
                UPDATE purchases SET account_cleared = 1
                WHERE supplier_id = NEW.supplier_id AND id <= NEW.id AND NEW.total_due = 0;
                UPDATE purchases SET account_cleared = 0
                WHERE supplier_id = NEW.supplier_id AND id > (
                    SELECT COALESCE(MAX(id),0) FROM purchases
                    WHERE supplier_id = NEW.supplier_id AND total_due = 0
                ) AND NEW.total_due > 0;
            END;
        """)
        cur.execute("""
            CREATE TRIGGER trg_purchases_after_update AFTER UPDATE ON purchases BEGIN
                UPDATE purchases SET
                    bill_cleared    = CASE WHEN NEW.due_amount = 0 THEN 1 ELSE 0 END,
                    account_cleared = CASE WHEN NEW.total_due  = 0 THEN 1 ELSE 0 END
                WHERE id = NEW.id;
                UPDATE purchases SET account_cleared = 1
                WHERE supplier_id = NEW.supplier_id AND id <= NEW.id AND NEW.total_due = 0;
                UPDATE purchases SET account_cleared = 0
                WHERE supplier_id = NEW.supplier_id AND id > (
                    SELECT COALESCE(MAX(id),0) FROM purchases
                    WHERE supplier_id = NEW.supplier_id AND total_due = 0
                ) AND NEW.total_due > 0;
            END;
        """)
    except Exception as e:
        print(f"purchase triggers: {e}")


def _create_purchase_views(cur):
    try:
        for v in ('bills_cleared', 'accounts_cleared', 'supplier_due_status'):
            cur.execute(f"DROP VIEW IF EXISTS {v}")
        cur.execute("CREATE VIEW bills_cleared AS SELECT * FROM purchases WHERE bill_cleared=1;")
        cur.execute("""
            CREATE VIEW accounts_cleared AS
            SELECT s.id AS supplier_id, s.name AS supplier_name,
                   p.id AS cleared_at_purchase_id,
                   p.purchase_no AS cleared_at_purchase_no,
                   p.purchase_date AS cleared_date
            FROM suppliers s JOIN purchases p ON p.supplier_id=s.id
            WHERE p.account_cleared=1
              AND p.id=(SELECT MAX(p2.id) FROM purchases p2
                        WHERE p2.supplier_id=s.id AND p2.account_cleared=1);
        """)
        # Fixed view: reads from suppliers.total_due (single source of truth)
        cur.execute("""
            CREATE VIEW supplier_due_status AS
            SELECT
                s.id   AS supplier_id,
                s.name AS supplier_name,
                COALESCE(s.total_due,    0) AS total_due,
                COALESCE(s.total_credit, 0) AS total_credit
            FROM suppliers s;
        """)
    except Exception as e:
        print(f"purchase views: {e}")


def _create_sales_triggers(cur):
    """Only maintain bill_cleared flag. account_cleared is managed by recalculate_customer_due."""
    try:
        for name in ('trg_sales_after_insert', 'trg_sales_after_update',
                     'trg_sales_insert_log', 'trg_sales_update_log'):
            cur.execute(f"DROP TRIGGER IF EXISTS {name}")
        cur.execute("""
            CREATE TRIGGER trg_sales_after_insert AFTER INSERT ON sales BEGIN
                UPDATE sales SET
                    bill_cleared = CASE WHEN NEW.due_amount = 0 THEN 1 ELSE 0 END
                WHERE id = NEW.id;
            END;
        """)
        cur.execute("""
            CREATE TRIGGER trg_sales_after_update AFTER UPDATE ON sales BEGIN
                UPDATE sales SET
                    bill_cleared = CASE WHEN NEW.due_amount = 0 THEN 1 ELSE 0 END
                WHERE id = NEW.id;
            END;
        """)
    except Exception as e:
        print(f"sales triggers: {e}")


# ── Location format migration ─────────────────────────────────────────────────

def _migrate_customer_payments(cur):
    """Ensure customer_payments table exists on existing DBs."""
    try:
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
                FOREIGN KEY (customer_id) REFERENCES customers (id)
            )
        """)
    except Exception as e:
        print(f"customer_payments migration: {e}")


def _migrate_location_format(cur):
    try:
        cur.execute("""
            SELECT r.name, s.name, b.name FROM boxes b
            JOIN sections s ON b.section_id=s.id
            JOIN racks r ON s.rack_id=r.id
        """)
        valid = set()
        for rn, sn, bn in cur.fetchall():
            rnum = re.findall(r'\d+', rn)
            snum = re.findall(r'\d+', sn)
            bnum = re.findall(r'\d+', bn)
            if rnum and snum and bnum:
                valid.add(f"rack{rnum[0]}section{snum[0]}box{bnum[0]}")
        cur.execute("SELECT r.name, s.name FROM sections s JOIN racks r ON s.rack_id=r.id")
        for rn, sn in cur.fetchall():
            rnum = re.findall(r'\d+', rn)
            snum = re.findall(r'\d+', sn)
            if rnum and snum:
                valid.add(f"rack{rnum[0]}section{snum[0]}")
        cur.execute("SELECT id, location FROM medicines WHERE location IS NOT NULL AND location!=''")
        rows = cur.fetchall()
        clear_ids, updates = [], []
        for med_id, loc in rows:
            s = loc.strip()
            if not ('rack' in s or 'section' in s):
                m = re.match(r'^r(\d+)s(\d+)b(\d+)$', s)
                if m:
                    s = f"rack{m.group(1)}section{m.group(2)}box{m.group(3)}"
                else:
                    m = re.match(r'^r(\d+)s(\d+)$', s)
                    if m:
                        s = f"rack{m.group(1)}section{m.group(2)}"
            if s in valid:
                if s != loc.strip():
                    updates.append((s, med_id))
            else:
                clear_ids.append((med_id,))
        if updates:
            cur.executemany("UPDATE medicines SET location=? WHERE id=?", updates)
        if clear_ids:
            cur.executemany("UPDATE medicines SET location='' WHERE id=?", clear_ids)
    except Exception as e:
        print(f"location migration: {e}")


# ── One-time data migration (Phase 11) ───────────────────────────────────────

def _run_one_time_migration(conn):
    """
    Run once at startup to bring existing data into the new accounting model.
    Guarded by a settings flag — only runs once per DB.

    Steps:
      1. Remove orphaned supplier_payments (reference deleted suppliers)
      2. Remove orphaned purchase_returns (reference deleted purchases)
      3. Remove orphaned sales_returns (reference deleted sales)
      4. Recalculate all supplier balances
      5. Recalculate all customer balances
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT value FROM settings WHERE name='accounting_v2_migrated'")
        row = cur.fetchone()
        if row and row[0] == '1':
            return  # already done

        print("[MIGRATION] Running one-time accounting v2 migration...")

        # Step 1: Remove orphaned supplier_payments
        cur.execute("""
            DELETE FROM supplier_payments
            WHERE supplier_id NOT IN (SELECT id FROM suppliers)
        """)
        n = cur.rowcount
        if n: print(f"[MIGRATION] Removed {n} orphaned supplier_payment(s).")

        # Step 2: Remove orphaned purchase_returns
        cur.execute("""
            DELETE FROM purchase_return_items
            WHERE return_id IN (
                SELECT pr.id FROM purchase_returns pr
                LEFT JOIN purchases p ON pr.purchase_id=p.id
                WHERE p.id IS NULL
            )
        """)
        cur.execute("""
            DELETE FROM purchase_returns
            WHERE purchase_id NOT IN (SELECT id FROM purchases)
        """)
        n = cur.rowcount
        if n: print(f"[MIGRATION] Removed {n} orphaned purchase_return(s).")

        # Step 3: Remove orphaned sales_returns
        cur.execute("""
            DELETE FROM sales_return_items
            WHERE return_id IN (
                SELECT sr.id FROM sales_returns sr
                LEFT JOIN sales s ON sr.sale_id=s.id
                WHERE s.id IS NULL
            )
        """)
        cur.execute("""
            DELETE FROM sales_returns
            WHERE sale_id NOT IN (SELECT id FROM sales)
        """)
        n = cur.rowcount
        if n: print(f"[MIGRATION] Removed {n} orphaned sales_return(s).")

        conn.commit()

        # Step 4: Recalculate all supplier balances
        from core.purchase_service import recalculate_supplier_due
        cur.execute("SELECT id FROM suppliers")
        for (sid,) in cur.fetchall():
            try:
                recalculate_supplier_due(conn, sid)
            except Exception as e:
                print(f"[MIGRATION] supplier {sid}: {e}")

        # Step 5: Recalculate all customer balances
        from core.customer_service import recalculate_customer_due
        cur.execute("SELECT id FROM customers")
        for (cid,) in cur.fetchall():
            try:
                recalculate_customer_due(conn, cid)
            except Exception as e:
                print(f"[MIGRATION] customer {cid}: {e}")

        cur.execute(
            "INSERT OR REPLACE INTO settings (name, value) VALUES ('accounting_v2_migrated','1')")
        conn.commit()
        print("[MIGRATION] Done.")
    except Exception as e:
        print(f"[MIGRATION] Failed: {e}")
