"""
core/purchase_service.py
────────────────────────
All database operations for the purchase flow.
No UI code. No calculation code.
Called by ui/purchase.py and ui/purchase_history.py.
"""
import sqlite3
from datetime import datetime


# ── Helpers ───────────────────────────────────────────────────────────────────

def expiry_to_db(expiry_mmyy: str) -> str:
    """Convert MM/YY or MM/YYYY → YYYY-MM-01 for DB storage."""
    month, year = expiry_mmyy.split('/')
    if len(year) == 2:
        year = '20' + year
    return f"{year}-{month}-01"


def expiry_to_display(db_expiry: str) -> str:
    """Convert YYYY-MM-01 → MM/YY for display."""
    if db_expiry and '-' in db_expiry:
        parts = db_expiry.split('-')
        if len(parts) >= 2:
            return f"{parts[1]}/{parts[0][2:]}"
    return db_expiry or ''


def _next_id(cur, table: str, id_col: str = 'id') -> int:
    """Return MAX(id)+1 — safe after deletions, never reuses a number."""
    cur.execute(f"SELECT COALESCE(MAX({id_col}),0)+1 FROM {table}")
    return cur.fetchone()[0]


# ── Supplier ──────────────────────────────────────────────────────────────────

def get_or_create_supplier(conn, name, address, phone, gstin, dl_numbers) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM suppliers WHERE name=?", (name,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE suppliers SET address=?,phone=?,gstin=?,dl_numbers=? WHERE id=?",
            (address, phone, gstin, dl_numbers, row[0]))
        return row[0]
    cur.execute(
        "INSERT INTO suppliers (name,address,phone,gstin,dl_numbers) VALUES (?,?,?,?,?)",
        (name, address, phone, gstin, dl_numbers))
    return cur.lastrowid


def recalculate_supplier_due(conn, supplier_id: int) -> tuple:
    """
    Single source of truth for supplier balance.

    net = SUM(purchases.total_amount)
        - SUM(purchases.amount_paid_at_entry)   ← entry-time only, never mutated
        - SUM(supplier_payments.amount)
        - SUM(purchase_returns.refund_amount)

    net > 0  → total_due = net,  total_credit = 0
    net < 0  → total_due = 0,    total_credit = abs(net)

    Updates suppliers.total_due / total_credit.
    Also sets purchases.account_cleared per-bill.
    Returns (total_due, total_credit).
    """
    cur = conn.cursor()

    cur.execute(
        "SELECT COALESCE(SUM(total_amount),0), COALESCE(SUM(amount_paid_at_entry),0) "
        "FROM purchases WHERE supplier_id=?",
        (supplier_id,))
    row = cur.fetchone()
    total_purchased  = round(float(row[0] or 0), 2)
    total_entry_paid = round(float(row[1] or 0), 2)

    try:
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE supplier_id=?",
            (supplier_id,))
        total_payments = round(float(cur.fetchone()[0] or 0), 2)
    except Exception:
        total_payments = 0.0

    try:
        cur.execute("""
            SELECT COALESCE(SUM(pr.refund_amount),0)
            FROM purchase_returns pr
            JOIN purchases p ON pr.purchase_id=p.id
            WHERE p.supplier_id=?
        """, (supplier_id,))
        total_returns = round(float(cur.fetchone()[0] or 0), 2)
    except Exception:
        total_returns = 0.0

    net = round(total_purchased - total_entry_paid - total_payments - total_returns, 2)
    total_due    = round(max(0.0,  net), 2)
    total_credit = round(max(0.0, -net), 2)

    cur.execute(
        "UPDATE suppliers SET total_due=?, total_credit=? WHERE id=?",
        (total_due, total_credit, supplier_id))

    # Update account_cleared per purchase bill
    # A bill is cleared when the cumulative running balance up to that bill is zero
    cur.execute(
        "SELECT id, total_amount, amount_paid_at_entry FROM purchases "
        "WHERE supplier_id=? ORDER BY id ASC",
        (supplier_id,))
    bills = cur.fetchall()

    # Distribute payments + returns oldest-first to determine per-bill cleared status
    remaining_credit = total_payments + total_returns
    for bill_id, bill_total, bill_entry_paid in bills:
        bill_total      = float(bill_total or 0)
        bill_entry_paid = float(bill_entry_paid or 0)
        unpaid = round(bill_total - bill_entry_paid, 2)
        if unpaid <= 0:
            cur.execute("UPDATE purchases SET account_cleared=1 WHERE id=?", (bill_id,))
            continue
        if remaining_credit >= unpaid:
            remaining_credit = round(remaining_credit - unpaid, 2)
            cur.execute("UPDATE purchases SET account_cleared=1 WHERE id=?", (bill_id,))
        else:
            cur.execute("UPDATE purchases SET account_cleared=0 WHERE id=?", (bill_id,))

    conn.commit()
    print(f"[SUPPLIER] id={supplier_id} purchased={total_purchased:.2f} "
          f"entry_paid={total_entry_paid:.2f} payments={total_payments:.2f} "
          f"returns={total_returns:.2f} => due={total_due:.2f} credit={total_credit:.2f}")
    return total_due, total_credit


def get_supplier_due(conn, supplier_name: str) -> tuple:
    """
    Return (total_due, total_credit) for a supplier by name.
    Reads from suppliers.total_due / total_credit (maintained by recalculate_supplier_due).
    Falls back to dynamic calculation if columns not yet populated.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT id, COALESCE(total_due,0), COALESCE(total_credit,0) "
        "FROM suppliers WHERE name=? LIMIT 1",
        (supplier_name,))
    row = cur.fetchone()
    if not row:
        return (0.0, 0.0)

    supplier_id, cached_due, cached_credit = row[0], float(row[1]), float(row[2])

    # If both are 0 and purchases exist, recalculate (first-run / migration case)
    if cached_due == 0.0 and cached_credit == 0.0:
        cur.execute("SELECT COUNT(*) FROM purchases WHERE supplier_id=?", (supplier_id,))
        if cur.fetchone()[0] > 0:
            return recalculate_supplier_due(conn, supplier_id)

    return (cached_due, cached_credit)


# ── Medicine ──────────────────────────────────────────────────────────────────

def get_or_create_medicine(conn, name, med_type, batch, expiry_mmyy,
                            gst_pct, mrp, rate, manufacturer,
                            hsn_code, schedule, content_drug) -> int:
    cur = conn.cursor()
    db_expiry = expiry_to_db(expiry_mmyy)

    try:
        cur.execute("ALTER TABLE medicines ADD COLUMN content_drug TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    cur.execute(
        "SELECT id FROM medicines WHERE name=? AND batch_no=? AND expiry_date=?",
        (name, batch, db_expiry))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE medicines SET content_drug=? WHERE id=?",
                    (content_drug, row[0]))
        return row[0]

    cur.execute("""
        INSERT INTO medicines
            (name,type,batch_no,expiry_date,gst_percent,mrp,rate,
             manufacturer,hsn_code,schedule,content_drug,location,stock_qty)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'',0)
    """, (name, med_type, batch, db_expiry, gst_pct, mrp, rate,
          manufacturer, hsn_code, schedule, content_drug))
    return cur.lastrowid


def lookup_medicine_details(conn, medicine_name: str) -> dict:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(medicines)")
    cols = [c[1] for c in cur.fetchall()]
    has_cd = 'content_drug' in cols

    if has_cd:
        cur.execute("""
            SELECT type,manufacturer,hsn_code,gst_percent,mrp,rate,schedule,content_drug
            FROM medicines WHERE name=? ORDER BY id DESC LIMIT 1
        """, (medicine_name,))
    else:
        cur.execute("""
            SELECT type,manufacturer,hsn_code,gst_percent,mrp,rate,schedule
            FROM medicines WHERE name=? ORDER BY id DESC LIMIT 1
        """, (medicine_name,))
    row = cur.fetchone()
    if row:
        return {
            'type': row[0] or '', 'manufacturer': row[1] or '',
            'hsn_code': row[2] or '', 'gst_percent': row[3] or 0,
            'mrp': row[4] or 0, 'rate': row[5] or 0,
            'schedule': row[6] or '',
            'content_drug': (row[7] if has_cd and len(row) > 7 else '') or '',
        }

    try:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='medicines_master'")
        if cur.fetchone():
            cur.execute("""
                SELECT med_type,manufacturer,mrp,content_drug
                FROM medicines_master WHERE name=? LIMIT 1
            """, (medicine_name,))
            m = cur.fetchone()
            if m:
                return {
                    'type': m[0] or '', 'manufacturer': m[1] or '',
                    'hsn_code': '', 'gst_percent': 0,
                    'mrp': float(m[2] or 0), 'rate': 0,
                    'schedule': '', 'content_drug': m[3] or '',
                }
    except sqlite3.Error:
        pass
    return {}


# ── Stock helpers ─────────────────────────────────────────────────────────────

def _get_unit_value(item) -> str:
    t = item['type'].lower()
    if t in ('tablet', 'bolus'):
        return str(item.get('tablets_per_stripe', 1))
    if t == 'injection - vial':
        return 'Vial'
    qty_raw = str(item.get('quantity_value', '1')).strip()
    unit_suffix = item.get('auto_unit', '')
    if any(qty_raw.lower().endswith(s) for s in ('ml', 'gm', 'g', 'l', 'kg', 'doses', 'vial')):
        return qty_raw
    return f"{qty_raw}{unit_suffix}" if unit_suffix else qty_raw


def _get_stock_increase(item) -> float:
    """Stock added when a purchase is saved (tablets stored as individual tablets)."""
    if item['type'].lower() in ('tablet', 'bolus'):
        return item.get('total_tablets', 0) + item.get('free_tablets', 0)
    return item['qty'] + item['free_qty']


def _get_stock_decrease(item) -> float:
    """
    Stock to subtract when a purchase is deleted or reversed.
    Mirrors _get_stock_increase exactly so the net is always zero.
    """
    if item['type'].lower() in ('tablet', 'bolus'):
        # Derive total_tablets from qty × tablets_per_strip stored in medicines.unit
        tps = item.get('tablets_per_stripe') or item.get('tps') or 1
        try:
            tps = int(float(tps))
        except (ValueError, TypeError):
            tps = 1
        qty      = float(item.get('qty', 0))
        free_qty = float(item.get('free_qty', 0))
        return qty * tps + free_qty * tps
    return float(item.get('qty', 0)) + float(item.get('free_qty', 0))


def _reverse_stock_for_purchase(cur, purchase_id: int):
    """
    Fetch purchase_items for purchase_id and subtract the correct stock amount.
    Used by delete_purchase and update_purchase (before re-inserting new items).
    """
    cur.execute("""
        SELECT pi.medicine_id, pi.qty, pi.free_qty, pi.type,
               COALESCE(m.unit, '1')
        FROM purchase_items pi
        JOIN medicines m ON pi.medicine_id = m.id
        WHERE pi.purchase_id = ?
    """, (purchase_id,))
    rows = cur.fetchall()
    for med_id, qty, free_qty, med_type, unit_str in rows:
        item = {
            'type':     med_type or '',
            'qty':      float(qty or 0),
            'free_qty': float(free_qty or 0),
        }
        if (med_type or '').lower() in ('tablet', 'bolus'):
            import re as _re
            nums = _re.findall(r'\d+', str(unit_str or ''))
            tps = int(nums[0]) if nums else 1
            item['tablets_per_stripe'] = tps
        decrease = _get_stock_decrease(item)
        cur.execute(
            "UPDATE medicines SET stock_qty = MAX(0, stock_qty - ?) WHERE id=?",
            (decrease, med_id))


# ── Purchase record ───────────────────────────────────────────────────────────

def save_purchase(conn, supplier_id: int, purchase_date_str: str,
                  bill_number: str, calc_result: dict,
                  items: list) -> str:
    """
    Insert purchase header + items, update stock, recalculate supplier due.
    calc_result must be the dict returned by PurchaseCalculator.calculate().
    Returns the generated purchase_no.
    """
    cur = conn.cursor()

    try:
        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
    except ValueError:
        purchase_date = datetime.now().date()

    purchase_no = f"PUR{datetime.now().strftime('%Y%m%d%H%M%S')}"
    entry_paid  = round(float(calc_result.get('amount_paid', 0)), 2)

    cur.execute("""
        INSERT INTO purchases (
            purchase_no, supplier_id, purchase_date, bill_number,
            subtotal, total_gst, cgst, sgst, total_amount,
            overall_discount, rounding, need_to_pay, final_amount,
            amount_paid, amount_paid_at_entry,
            previous_due, previous_credit, due, current_credit, total_due,
            bill_cleared, account_cleared,
            due_amount, credit_amount
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        purchase_no, supplier_id, purchase_date, bill_number,
        calc_result['subtotal'], calc_result['total_gst'],
        calc_result['cgst'], calc_result['sgst'], calc_result['total_amount'],
        calc_result['overall_discount'], calc_result['rounding'],
        calc_result['need_to_pay'], calc_result['final_amount'],
        entry_paid, entry_paid,                          # amount_paid + amount_paid_at_entry
        calc_result['previous_due'], calc_result['previous_credit'],
        calc_result['due'], calc_result['current_credit'], calc_result['total_due'],
        calc_result['bill_cleared'], calc_result['account_cleared'],
        calc_result['due'], calc_result['current_credit'],
    ))
    purchase_id = cur.lastrowid

    _insert_items(cur, purchase_id, items)
    conn.commit()

    recalculate_supplier_due(conn, supplier_id)
    return purchase_no


def update_purchase(conn, purchase_id: int, supplier_id: int,
                    bill_number: str, purchase_date_str: str,
                    calc_result: dict, items: list):
    """
    Update an existing purchase.
    Correctly reverses old stock BEFORE inserting new items.
    """
    cur = conn.cursor()

    try:
        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
    except ValueError:
        purchase_date = datetime.now().date()

    entry_paid = round(float(calc_result.get('amount_paid', 0)), 2)

    # ── PHASE 2.2: reverse old stock before touching items ────────────────
    _reverse_stock_for_purchase(cur, purchase_id)

    cur.execute("""
        UPDATE purchases SET
            supplier_id=?, purchase_date=?, bill_number=?,
            subtotal=?, total_gst=?, cgst=?, sgst=?, total_amount=?,
            overall_discount=?, rounding=?, need_to_pay=?, final_amount=?,
            amount_paid=?, amount_paid_at_entry=?,
            previous_due=?, previous_credit=?, due=?, current_credit=?, total_due=?,
            bill_cleared=?, account_cleared=?,
            due_amount=?, credit_amount=?
        WHERE id=?
    """, (
        supplier_id, purchase_date, bill_number,
        calc_result['subtotal'], calc_result['total_gst'],
        calc_result['cgst'], calc_result['sgst'], calc_result['total_amount'],
        calc_result['overall_discount'], calc_result['rounding'],
        calc_result['need_to_pay'], calc_result['final_amount'],
        entry_paid, entry_paid,
        calc_result['previous_due'], calc_result['previous_credit'],
        calc_result['due'], calc_result['current_credit'], calc_result['total_due'],
        calc_result['bill_cleared'], calc_result['account_cleared'],
        calc_result['due'], calc_result['current_credit'],
        purchase_id,
    ))

    cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
    _insert_items(cur, purchase_id, items)
    conn.commit()

    recalculate_supplier_due(conn, supplier_id)


# ── Items + stock ─────────────────────────────────────────────────────────────

def _insert_items(cur, purchase_id: int, items: list):
    item_rows  = []
    stock_rows = []

    for item in items:
        db_expiry = expiry_to_db(item['expiry'])
        item_rows.append((
            purchase_id, item['medicine_id'],
            item['qty'], item['free_qty'], item['type'],
            item.get('hsn_code', ''), item.get('gst_pct', item.get('gst_value', 0)),
            item.get('mrp', 0), item['rate'],
            item.get('manufacturer', ''), item['batch'], db_expiry,
            item.get('schedule', ''),
            item.get('discount_pct', item.get('item_discount', 0)),
            item.get('taxable', 0),
            item.get('gst_amt', 0),
            item.get('item_amount', item.get('amount', 0)),
            # legacy aliases
            item.get('discount_pct', item.get('item_discount', 0)),
            item.get('gst_pct', item.get('gst_value', 0)),
            item.get('item_amount', item.get('amount', 0)),
        ))
        stock_rows.append((_get_unit_value(item), _get_stock_increase(item), item['medicine_id']))

    cur.executemany("""
        INSERT INTO purchase_items (
            purchase_id, medicine_id, qty, free_qty, type,
            hsn_code, gst_pct, mrp, rate, manufacturer,
            batch_no, expiry_date, schedule,
            discount_pct, taxable, gst_amt, item_amount,
            discount_percent, gst_value, amount
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, item_rows)

    cur.executemany(
        "UPDATE medicines SET unit=?, stock_qty=stock_qty+? WHERE id=?",
        stock_rows)
