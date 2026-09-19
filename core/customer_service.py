"""
customer_service.py
-------------------
Service layer for customers and doctors.
All billing logic that touches customers/doctors goes through here.

Schema managed here:
  customers  — adds total_due, total_credit, last_updated if missing
  doctors    — no changes needed (already has name, phone, registration_number)
"""
from __future__ import annotations

from datetime import datetime

COUNTER_SALE = "COUNTER SALE"

_COUNTER_SALE_DB_NAMES = frozenset({
    COUNTER_SALE,
    'COUNTER',
    'COUNTER SALES',
    'COUNTERSALE',
    'COUNTERSALES',
})


def is_counter_sale_name(name: str) -> bool:
    """True for counter sale walk-in aliases (counter, counter sales, etc.)."""
    n = (name or '').strip().upper()
    if not n:
        return False
    if n in _COUNTER_SALE_DB_NAMES:
        return True
    first = n.split()[0]
    return first == 'COUNTER'


def _remember_customer_village(conn, address) -> None:
    """Best-effort: typed address becomes a Settings village + dropdown row."""
    try:
        from core.village_service import ensure_village_from_address

        ensure_village_from_address(conn, address, silent=True)
    except Exception:
        pass


def counter_sale_customer_ids(conn) -> list[int]:
    """All customer row ids used for counter-sale bills (legacy aliases included)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM customers WHERE UPPER(TRIM(name)) IN (?, ?, ?, ?, ?)",
        tuple(_COUNTER_SALE_DB_NAMES),
    )
    return [int(row[0]) for row in cur.fetchall()]


def get_or_create_counter_customer(conn) -> int:
    """Return one canonical COUNTER SALE customer id (merge legacy alias rows)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM customers
        WHERE UPPER(TRIM(name)) IN (?, ?, ?, ?, ?)
        ORDER BY CASE UPPER(TRIM(name))
            WHEN ? THEN 0
            WHEN 'COUNTER' THEN 1
            ELSE 2
        END, id ASC
        LIMIT 1
        """,
        (
            COUNTER_SALE, 'COUNTER', 'COUNTER SALES', 'COUNTERSALE', 'COUNTERSALES',
            COUNTER_SALE,
        ),
    )
    row = cur.fetchone()
    if row:
        cid = int(row[0])
        cur.execute("UPDATE customers SET name=? WHERE id=?", (COUNTER_SALE, cid))
        conn.commit()
        return cid
    cur.execute(
        "INSERT INTO customers (name, phone, address) VALUES (?, '', '')",
        (COUNTER_SALE,),
    )
    conn.commit()
    cid = cur.lastrowid
    try:
        from core.sync_coordinator import after_customer_saved
        after_customer_saved(conn, cid)
    except Exception:
        pass
    return cid


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
    from core.name_utils import storage_name_from_entry
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        from core.server_crud import upsert_contact_online, allocate_id
        from core.online_catalog import find_customer_by_name, invalidate, patch_customer_cache

        name_upper = storage_name_from_entry(name) or (name or "").strip().upper()
        if is_counter_sale_name(name_upper):
            name_upper = COUNTER_SALE
        phone_s = (phone or "").strip()
        address_s = (address or "").strip()
        existing = find_customer_by_name(name_upper)
        if not existing or int(existing.get("id") or 0) <= 0:
            existing = find_customer_by_name(name_upper, force=True)
        if existing and int(existing.get("id") or 0) > 0:
            cid = int(existing["id"])
            # Push whenever UI has contact fields the catalog is missing/outdated.
            need = False
            if phone_s and phone_s != (existing.get("phone") or "").strip():
                need = True
            if address_s and address_s != (existing.get("address") or "").strip():
                need = True
            merged = {
                "id": cid,
                "local_id": cid,
                "name": existing.get("name") or name_upper,
                "phone": phone_s or (existing.get("phone") or ""),
                "address": address_s or (existing.get("address") or ""),
                "total_due": float(existing.get("total_due") or 0),
                "total_credit": float(existing.get("total_credit") or 0),
            }
            if need:
                upsert_contact_online("customers", merged)
                # Keep local catalog warm — do not wipe cache (would drop phone again).
                patch_customer_cache(merged)
            _remember_customer_village(conn, address_s or merged.get("address") or "")
            return cid
        cid = allocate_id("customers")
        created = {
            "id": cid,
            "local_id": cid,
            "name": name_upper,
            "phone": phone_s,
            "address": address_s,
            "total_due": 0,
            "total_credit": 0,
        }
        upsert_contact_online("customers", created)
        patch_customer_cache(created)
        _remember_customer_village(conn, address_s)
        return cid

    if is_counter_sale_name(name):
        return get_or_create_counter_customer(conn)

    cur = conn.cursor()
    name_upper = storage_name_from_entry(name) or name.strip().upper()
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

    from core.online_guard import ensure_can_mutate
    ensure_can_mutate()
    cur.execute(
        "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
        (name_upper, phone, address))
    conn.commit()
    cid = cur.lastrowid
    try:
        from core.sync_coordinator import after_customer_saved
        after_customer_saved(conn, cid)
    except Exception:
        pass
    _remember_customer_village(conn, address)
    return cid


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
    try:
        from core.sync_coordinator import after_customer_saved
        after_customer_saved(conn, customer_id)
    except Exception:
        pass
    _remember_customer_village(conn, address)


def recalculate_customer_due(conn, customer_id, *, commit: bool = True, sync: bool = True):
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
        "FROM sales WHERE customer_id=? "
        "AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0",
        (customer_id,))
    row = cur.fetchone()
    total_billed = float(row[0])
    total_paid   = float(row[1])

    cur.execute(
        "SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns "
        "WHERE customer_id=? AND COALESCE(deleted,0)=0",
        (customer_id,))
    total_returns = float(cur.fetchone()[0])

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
        "WHERE customer_id=? AND COALESCE(deleted,0)=0",
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

    # Cascade every bill for this customer: FIFO clear using payments/returns
    # plus excess paid on later bills (previous-due paid on a new invoice).
    from core.due_fifo import cascade_sales_fifo

    cur.execute(
        "SELECT id, COALESCE(total_amount,0), COALESCE(amount_paid,0) FROM sales "
        "WHERE customer_id=? AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0 "
        "ORDER BY bill_date ASC, id ASC",
        (customer_id,),
    )
    bills = [(int(r[0]), float(r[1] or 0), float(r[2] or 0)) for r in cur.fetchall()]
    for bid, remaining, cleared in cascade_sales_fifo(bills, total_standalone, total_returns):
        cur.execute(
            "UPDATE sales SET total_due=?, account_cleared=? WHERE id=?",
            (remaining, cleared, bid),
        )

    if commit:
        conn.commit()
        if sync:
            try:
                from core.sync_coordinator import after_customer_saved
                after_customer_saved(conn, customer_id)
            except Exception:
                pass

    print(f"[DUE] customer_id={customer_id} "
          f"billed={total_billed:.2f} paid={total_paid:.2f} "
          f"standalone={total_standalone:.2f} returns={total_returns:.2f} "
          f"old_due={old_due:.2f} old_credit={old_credit:.2f} "
          f"=> due={net_due:.2f} credit={net_credit:.2f}")

    return net_due, net_credit


def get_customer_due(conn, customer_id):
    """Return (total_due, total_credit) using full ledger math."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import find_customer_by_id

            found = find_customer_by_id(customer_id)
            if found:
                return (
                    round(float(found.get("total_due") or 0), 2),
                    round(float(found.get("total_credit") or 0), 2),
                )
            return 0.0, 0.0
    except Exception:
        pass
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(amount_paid),0) "
        "FROM sales WHERE customer_id=? "
        "AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0",
        (customer_id,))
    row = cur.fetchone()
    total_billed, total_paid = float(row[0]), float(row[1])
    cur.execute(
        "SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns "
        "WHERE customer_id=? AND COALESCE(deleted,0)=0",
        (customer_id,))
    total_returns = float(cur.fetchone()[0])
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
        "WHERE customer_id=? AND COALESCE(deleted,0)=0",
        (customer_id,))
    total_standalone = float(cur.fetchone()[0])
    net = round(total_billed - total_paid - total_standalone - total_returns, 2)
    return round(max(0.0, net), 2), round(max(0.0, -net), 2)


def get_all_customers(conn):
    """Return list of dicts for the Customer List page."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import customers

            out = []
            for c in customers(force=True):
                try:
                    cid = int(c.get("id") or c.get("local_id") or 0)
                except (TypeError, ValueError):
                    continue
                if cid <= 0:
                    continue
                out.append({
                    "id": cid,
                    "name": c.get("name") or "",
                    "phone": c.get("phone") or "",
                    "address": c.get("address") or "",
                    "total_due": float(c.get("total_due") or 0),
                    "total_credit": float(c.get("total_credit") or 0),
                })
            out.sort(key=lambda r: str(r.get("name") or "").upper())
            return out
    except Exception:
        pass
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
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import customer_names

            return customer_names()
    except Exception:
        pass
    cur = conn.cursor()
    cur.execute("SELECT name FROM customers ORDER BY name")
    names = [row[0] for row in cur.fetchall()]
    if not any(n.upper() == COUNTER_SALE for n in names):
        return [COUNTER_SALE] + names
    return names


def get_customer_by_name(conn, name, *, force_refresh: bool = False):
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import find_customer_by_name

            found = find_customer_by_name(name, force=bool(force_refresh))
            if found:
                try:
                    from core.desktop_settings_service import (
                        online_customer_remaining_due,
                    )
                    from core.online_catalog import patch_customer_cache

                    cid = int(found.get("id") or found.get("local_id") or 0)
                    live = online_customer_remaining_due(
                        cid, str(found.get("name") or name)
                    )
                    if live is not None:
                        due = round(max(0.0, float(live)), 2)
                        merged = dict(found)
                        merged["total_due"] = due
                        if due > 0.01:
                            merged["total_credit"] = 0.0
                        patch_customer_cache(merged)
                        return merged
                except Exception:
                    pass
                return found
            if is_counter_sale_name(name):
                # Ensure COUNTER SALE exists on server
                cid = get_or_create_customer(conn, COUNTER_SALE, "", "")
                return find_customer_by_name(COUNTER_SALE, force=True) or {
                    "id": cid,
                    "name": COUNTER_SALE,
                    "phone": "",
                    "address": "",
                    "total_due": 0,
                    "total_credit": 0,
                }
            return None
    except Exception:
        pass
    if is_counter_sale_name(name):
        cid = get_or_create_counter_customer(conn)
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, phone, address,
                      COALESCE(total_due,0), COALESCE(total_credit,0)
               FROM customers WHERE id=?""",
            (cid,),
        )
        row = cur.fetchone()
        if row:
            return {'id': row[0], 'name': row[1], 'phone': row[2],
                    'address': row[3], 'total_due': row[4], 'total_credit': row[5]}
        return None
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
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import doctor_names

            return doctor_names()
    except Exception:
        pass
    cur = conn.cursor()
    cur.execute("SELECT name FROM doctors ORDER BY name")
    return [row[0] for row in cur.fetchall()]


def save_doctor(conn, name, phone='', reg_no=''):
    from core.online_guard import ensure_can_mutate
    ensure_can_mutate()
    cur = conn.cursor()
    name_upper = name.strip().upper()
    cur.execute("SELECT id FROM doctors WHERE UPPER(name)=?", (name_upper,))
    row = cur.fetchone()
    if row:
        doctor_id = row[0]
        if phone or reg_no:
            cur.execute(
                "UPDATE doctors SET phone=?, registration_number=? WHERE id=?",
                (phone or '', reg_no or '', doctor_id))
    else:
        cur.execute(
            "INSERT INTO doctors (name, phone, registration_number) VALUES (?,?,?)",
            (name_upper, phone or '', reg_no or ''))
        doctor_id = cur.lastrowid
    conn.commit()
    from core.sync_coordinator import after_doctor_saved
    after_doctor_saved(conn, doctor_id)
