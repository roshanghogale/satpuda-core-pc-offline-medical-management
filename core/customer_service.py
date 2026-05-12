"""
customer_service.py
-------------------
Service layer for customers and doctors.
All billing logic that touches customers/doctors goes through here.

Schema managed here:
  customers  — adds total_due, total_credit, last_updated if missing
  doctors    — no changes needed (already has name, phone, registration_number)
"""

from datetime import datetime


# ── Schema migration ──────────────────────────────────────────────────────────

def migrate_schema(conn):
    """Add customer summary columns if they don't exist. Safe to call every startup."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(customers)")
    existing = {row[1] for row in cur.fetchall()}
    for col, defn in [
        ('total_due',    'REAL DEFAULT 0'),
        ('total_credit', 'REAL DEFAULT 0'),
        ('last_updated', 'TIMESTAMP'),
    ]:
        if col not in existing:
            cur.execute(f"ALTER TABLE customers ADD COLUMN {col} {defn}")
    conn.commit()


# ── Customer functions ────────────────────────────────────────────────────────

def get_or_create_customer(conn, name, phone, address):
    cur = conn.cursor()
    name_upper = name.strip().upper()
    phone   = (phone or '').strip()
    address = (address or '').strip()

    if phone:
        cur.execute(
            "SELECT id FROM customers WHERE UPPER(name)=? AND phone=?",
            (name_upper, phone))
        row = cur.fetchone()
        if row:
            _update_customer_contact(conn, row[0], phone, address)
            return row[0]

    cur.execute("SELECT id FROM customers WHERE UPPER(name)=?", (name_upper,))
    row = cur.fetchone()
    if row:
        _update_customer_contact(conn, row[0], phone, address)
        return row[0]

    cur.execute(
        "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
        (name_upper, phone, address))
    conn.commit()
    return cur.lastrowid


def _update_customer_contact(conn, customer_id, phone, address):
    cur = conn.cursor()
    if phone and address:
        cur.execute(
            "UPDATE customers SET phone=?, address=? WHERE id=?",
            (phone, address, customer_id))
    elif phone:
        cur.execute("UPDATE customers SET phone=? WHERE id=?", (phone, customer_id))
    elif address:
        cur.execute("UPDATE customers SET address=? WHERE id=?", (address, customer_id))
    conn.commit()


def recalculate_customer_due(conn, customer_id):
    """
    Single source of truth — recompute from ALL raw transactions:

    net_balance = SUM(sales.total_amount)
                - SUM(sales.amount_paid)
                - SUM(customer_payments.amount)
                - SUM(sales_returns.refund_amount)

    net > 0  => total_due = net,  total_credit = 0
    net < 0  => total_due = 0,    total_credit = abs(net)
    net == 0 => both = 0
    """
    cur = conn.cursor()

    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(amount_paid),0) "
        "FROM sales WHERE customer_id=?",
        (customer_id,))
    row = cur.fetchone()
    total_billed = float(row[0])
    total_paid   = float(row[1])

    cur.execute(
        "SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns WHERE customer_id=?",
        (customer_id,))
    total_returns = float(cur.fetchone()[0])

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) FROM customer_payments WHERE customer_id=?",
        (customer_id,))
    total_standalone = float(cur.fetchone()[0])

    # Read old values for debug log
    cur.execute(
        "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) FROM customers WHERE id=?",
        (customer_id,))
    old = cur.fetchone()
    old_due    = float(old[0]) if old else 0.0
    old_credit = float(old[1]) if old else 0.0

    net_balance = round(total_billed - total_paid - total_standalone - total_returns, 2)
    net_due     = round(max(0.0, net_balance), 2)
    net_credit  = round(max(0.0, -net_balance), 2)

    cur.execute(
        "UPDATE customers SET total_due=?, total_credit=?, last_updated=? WHERE id=?",
        (net_due, net_credit, datetime.now().isoformat(), customer_id))

    # account_cleared logic:
    # When net_due == 0 (account fully settled) mark ALL bills cleared.
    # When net_due > 0 only clear bills whose individual due_amount == 0
    # and leave historical already-cleared bills untouched.
    if net_due == 0:
        cur.execute("UPDATE sales SET account_cleared=1 WHERE customer_id=?", (customer_id,))
    else:
        # Only reset bills that are NOT individually cleared (due_amount > 0)
        # Bills with due_amount == 0 keep their cleared status
        cur.execute(
            "UPDATE sales SET account_cleared=0 "
            "WHERE customer_id=? AND due_amount > 0",
            (customer_id,))
        cur.execute(
            "UPDATE sales SET account_cleared=1 "
            "WHERE customer_id=? AND due_amount = 0",
            (customer_id,))

    conn.commit()

    print(f"[DUE] customer_id={customer_id} "
          f"billed={total_billed:.2f} paid={total_paid:.2f} "
          f"standalone={total_standalone:.2f} returns={total_returns:.2f} "
          f"old_due={old_due:.2f} old_credit={old_credit:.2f} "
          f"=> due={net_due:.2f} credit={net_credit:.2f}")

    return net_due, net_credit


def get_customer_due(conn, customer_id):
    """Return (total_due, total_credit) using full ledger math."""
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(amount_paid),0) "
        "FROM sales WHERE customer_id=?", (customer_id,))
    row = cur.fetchone()
    total_billed, total_paid = float(row[0]), float(row[1])
    cur.execute(
        "SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns WHERE customer_id=?",
        (customer_id,))
    total_returns = float(cur.fetchone()[0])
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) FROM customer_payments WHERE customer_id=?",
        (customer_id,))
    total_standalone = float(cur.fetchone()[0])
    net = round(total_billed - total_paid - total_standalone - total_returns, 2)
    return round(max(0.0, net), 2), round(max(0.0, -net), 2)


def get_all_customers(conn):
    """Return list of dicts for the Customer List page."""
    cur = conn.cursor()
    cur.execute(
        """SELECT id, name, phone, address,
                  COALESCE(total_due,0), COALESCE(total_credit,0)
           FROM customers ORDER BY name""")
    cols = ('id', 'name', 'phone', 'address', 'total_due', 'total_credit')
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def search_customers(conn, query):
    cur = conn.cursor()
    like = f"%{query.upper()}%"
    cur.execute(
        """SELECT id, name, phone, address,
                  COALESCE(total_due,0), COALESCE(total_credit,0)
           FROM customers
           WHERE UPPER(name) LIKE ? OR phone LIKE ?
           ORDER BY name LIMIT 100""",
        (like, like))
    return cur.fetchall()


def get_customer_names(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM customers ORDER BY name")
    return [row[0] for row in cur.fetchall()]


def get_customer_by_name(conn, name):
    cur = conn.cursor()
    cur.execute(
        """SELECT id, name, phone, address,
                  COALESCE(total_due,0), COALESCE(total_credit,0)
           FROM customers WHERE UPPER(name)=?
           ORDER BY id DESC LIMIT 1""",
        (name.strip().upper(),))
    row = cur.fetchone()
    if row:
        return {'id': row[0], 'name': row[1], 'phone': row[2],
                'address': row[3], 'total_due': row[4], 'total_credit': row[5]}
    return None


# ── Doctor functions ──────────────────────────────────────────────────────────

def get_all_doctor_names(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM doctors ORDER BY name")
    return [row[0] for row in cur.fetchall()]


def save_doctor(conn, name, phone='', reg_no=''):
    cur = conn.cursor()
    name_upper = name.strip().upper()
    cur.execute("SELECT id FROM doctors WHERE UPPER(name)=?", (name_upper,))
    row = cur.fetchone()
    if row:
        if phone or reg_no:
            cur.execute(
                "UPDATE doctors SET phone=?, registration_number=? WHERE id=?",
                (phone or '', reg_no or '', row[0]))
    else:
        cur.execute(
            "INSERT INTO doctors (name, phone, registration_number) VALUES (?,?,?)",
            (name_upper, phone or '', reg_no or ''))
    conn.commit()
