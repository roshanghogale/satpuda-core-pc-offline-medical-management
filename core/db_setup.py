"""
core/db_setup.py
────────────────
All database schema creation, migrations, triggers and views.
Called once at startup from main.py — no UI code here.
"""
import sqlite3
import re


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
        amount_paid REAL DEFAULT 0, previous_due REAL DEFAULT 0,
        previous_credit REAL DEFAULT 0, due REAL DEFAULT 0,
        current_credit REAL DEFAULT 0, total_due REAL DEFAULT 0,
        bill_cleared INTEGER DEFAULT 0, account_cleared INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        due_amount           REAL DEFAULT 0,
        credit_amount        REAL DEFAULT 0,
        paid_due             REAL DEFAULT 0,
        gst_calc_method      TEXT,
        amount_paid_at_entry REAL DEFAULT 0,
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


# ── Migrations ────────────────────────────────────────────────────────────────

def _migrate_all(cur, conn):
    _migrate_doctors(cur)
    _migrate_medicines(cur, conn)
    _migrate_purchases(cur)
    _migrate_purchase_items(cur)
    _migrate_pharmacy_profile(cur)
    _migrate_sales(cur)
    _create_purchase_triggers(cur)
    _create_purchase_views(cur)
    _create_sales_triggers(cur)
    _migrate_location_format(cur)
    _migrate_customer_payments(cur)
    _migrate_suppliers(cur)
    _migrate_purchase_items_entry_paid(cur)
    conn.commit()


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
        # Rebuild if unexpected columns exist
        expected = {'id','name','type','stock_qty','unit','gst_percent','mrp','rate',
                    'manufacturer','batch_no','expiry_date','hsn_code','schedule',
                    'location','content_drug','created_at'}
        if len(cols) > len(expected) or any(c not in expected for c in cols if c != 'id'):
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
                    " content_drug, created_at FROM medicines")
        backup = cur.fetchall()

        cur.execute("DROP TABLE IF EXISTS medicines")
        cur.execute("""CREATE TABLE medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, type TEXT,
            stock_qty INTEGER DEFAULT 0, unit TEXT,
            gst_percent REAL, mrp REAL, rate REAL,
            manufacturer TEXT, batch_no TEXT, expiry_date DATE,
            hsn_code TEXT, schedule TEXT, location TEXT, content_drug TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        for row in backup:
            cur.execute("""
                INSERT INTO medicines
                    (id, name, type, stock_qty, unit, gst_percent, mrp, rate,
                     manufacturer, batch_no, expiry_date, hsn_code, schedule,
                     location, content_drug, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, row)  # row has exactly 16 values matching the SELECT above
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
    except Exception as e:
        print(f"pharmacy_profile migration: {e}")


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
        ]
        for col, col_type in new_cols:
            if col not in cols:
                cur.execute(f"ALTER TABLE sales ADD COLUMN {col} {col_type}")
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
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )""")
        cur.execute("""INSERT INTO sales_new
            (id,bill_no,customer_id,bill_date,total_amount,discount,rounding,
             amount_paid,cash_paid,online_paid,previous_due,previous_credit,
             total_due,due_amount,credit_amount,paid_due,bill_cleared,
             account_cleared,doctor_name,created_at)
            SELECT id,bill_no,customer_id,bill_date,total_amount,discount,rounding,
             amount_paid,cash_paid,online_paid,previous_due,previous_credit,
             total_due,due_amount,credit_amount,paid_due,bill_cleared,
             account_cleared,doctor_name,created_at FROM sales""")
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
