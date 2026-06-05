"""
core/billing_service.py
────────────────────────
All database write operations for the billing flow.
Used by ui/billing.py (generate_bill) and widgets/bill_edit.py (save_bill).
No UI code. No calculation code.
"""
from datetime import datetime, date


def save_new_bill(conn, customer_id, medicines, discount_pct,
                  rounding, cash_paid, online_paid,
                  doctor_name, doctor_phone, previous_due,
                  discount_rs=None, bill_date=None):
    """
    Insert a new sale + items, update stock, update customer balance.
    Returns (bill_no, sale_id).
    """
    from core.calc_engine import calc_bill_summary, calc_payment_result

    cur = conn.cursor()

    # Bill number — MAX(id)+1 is collision-safe after deletions
    cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM sales")
    bill_no = f"SCB{cur.fetchone()[0]}"

    summary  = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    total    = summary['total_amount']
    disc_amt = summary['discount_amount']
    disc_pct = summary['discount_pct']

    # Read live customer credit too
    cur.execute(
        "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) FROM customers WHERE id=?",
        (customer_id,))
    cust_row     = cur.fetchone()
    prev_due     = round(float(cust_row[0]), 2) if cust_row else round(max(0, previous_due), 2)
    prev_credit  = round(float(cust_row[1]), 2) if cust_row else 0.0

    pay = calc_payment_result(total, cash_paid, online_paid, prev_due, prev_credit)

    amount_paid   = pay['amount_paid']
    due_amount    = pay['due_amount']
    credit_amount = pay['credit_amount']
    bill_cleared  = 1 if due_amount == 0 else 0

    # Upsert doctor
    doc_upper = doctor_name.strip().upper() if doctor_name else ''
    if doc_upper:
        cur.execute("SELECT id FROM doctors WHERE UPPER(name)=?", (doc_upper,))
        existing = cur.fetchone()
        if not existing:
            cur.execute("INSERT INTO doctors (name, phone) VALUES (?,?)",
                        (doc_upper, doctor_phone))
        elif doctor_phone:
            cur.execute("UPDATE doctors SET phone=? WHERE UPPER(name)=?",
                        (doctor_phone, doc_upper))

    cur.execute("""
        INSERT INTO sales
            (bill_no, customer_id, bill_date, total_amount, discount, discount_pct, rounding,
             amount_paid, cash_paid, online_paid, doctor_name,
             previous_due, previous_credit, due_amount, credit_amount, total_due,
             bill_cleared, account_cleared)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
    """, (bill_no, customer_id, bill_date or date.today(), total, disc_amt, disc_pct, rounding,
          amount_paid, cash_paid, online_paid, doc_upper,
          prev_due, prev_credit, due_amount, credit_amount, due_amount, bill_cleared))
    sale_id = cur.lastrowid

    _insert_items_and_update_stock(cur, sale_id, medicines)
    conn.commit()

    from core.customer_service import recalculate_customer_due
    recalculate_customer_due(conn, customer_id)

    # Debug
    print(f"[BILL] {bill_no} total={total:.2f} paid={amount_paid:.2f} "
          f"prev_due={prev_due:.2f} prev_credit={prev_credit:.2f} "
          f"→ due={due_amount:.2f} credit={credit_amount:.2f}")

    return bill_no, sale_id


def update_existing_bill(conn, sale_id, medicines, discount_pct,
                         rounding, cash_paid, online_paid,
                         customer_name, customer_phone, doctor_name,
                         previous_due, discount_rs=None):
    """
    Update an existing sale: restore old stock, replace items, recalculate.
    """
    from core.calc_engine import calc_bill_summary, calc_payment_result

    cur = conn.cursor()

    summary  = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    total    = summary['total_amount']
    disc_amt = summary['discount_amount']
    disc_pct = summary['discount_pct']

    # Read stored previous_due/credit snapshots for this bill (display only)
    cur.execute(
        "SELECT COALESCE(previous_due,0), COALESCE(previous_credit,0) FROM sales WHERE id=?",
        (sale_id,))
    snap = cur.fetchone()
    prev_due    = float(snap[0]) if snap else 0.0
    prev_credit = float(snap[1]) if snap else 0.0

    pay = calc_payment_result(total, cash_paid, online_paid, prev_due, prev_credit)
    amount_paid   = pay['amount_paid']
    due_amount    = pay['due_amount']
    credit_amount = pay['credit_amount']
    bill_cleared  = 1 if due_amount == 0 else 0

    # Update customer info
    cur.execute(
        "UPDATE customers SET name=?, phone=? "
        "WHERE id=(SELECT customer_id FROM sales WHERE id=?)",
        (customer_name.strip().upper(), customer_phone.strip(), sale_id))

    cur.execute("""
        UPDATE sales SET
            total_amount=?, discount=?, discount_pct=?, rounding=?,
            amount_paid=?, cash_paid=?, online_paid=?, doctor_name=?,
            due_amount=?, credit_amount=?, total_due=?,
            bill_cleared=?
        WHERE id=?
    """, (total, disc_amt, disc_pct, rounding,
          amount_paid, cash_paid, online_paid,
          doctor_name.strip().upper() if doctor_name else '',
          due_amount, credit_amount, due_amount, bill_cleared, sale_id))

    cur.execute("SELECT customer_id FROM sales WHERE id=?", (sale_id,))
    customer_id = cur.fetchone()[0]

    # Restore old stock
    cur.execute("SELECT medicine_id, qty FROM sales_items WHERE sale_id=?", (sale_id,))
    for med_id, qty in cur.fetchall():
        restore_qty = abs(float(qty or 0))
        cur.execute("UPDATE medicines SET stock_qty=stock_qty+? WHERE id=?", (restore_qty, med_id))

    cur.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
    _insert_items_and_update_stock(cur, sale_id, medicines)
    conn.commit()

    from core.customer_service import recalculate_customer_due
    recalculate_customer_due(conn, customer_id)

    print(f"[EDIT] sale_id={sale_id} total={total:.2f} paid={amount_paid:.2f} "
          f"→ due={due_amount:.2f} credit={credit_amount:.2f}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_items_and_update_stock(cur, sale_id, medicines):
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    for med in medicines:
        # Snapshot cost_price at sale time from latest purchase
        med_id = med['id']
        cur.execute("""
            SELECT pi.rate, m.type, COALESCE(m.unit, '1')
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            WHERE pi.medicine_id = ?
            ORDER BY pi.id DESC LIMIT 1
        """, (med_id,))
        cp_row = cur.fetchone()
        if cp_row and cp_row[0] is not None:
            pi_rate, mtype, unit = cp_row
            if is_strip_count_type(mtype or ''):
                tps = parse_tablets_per_stripe(unit)
                cost_price = round(float(pi_rate) / tps, 4) if tps else round(float(pi_rate), 4)
            else:
                cost_price = round(float(pi_rate), 4)
        else:
            cost_price = 0.0

        cur.execute("""
            INSERT INTO sales_items
                (sale_id, medicine_id, qty, rate, gst_percent, amount, item_discount, cost_price)
            VALUES (?,?,?,?,?,?,?,?)
        """, (sale_id, med_id, med['qty'], med['rate'],
              med.get('gst_percent', 0), med['amount'],
              med.get('medicine_discount', 0), cost_price))
        cur.execute(
            "UPDATE medicines SET stock_qty=MAX(0, stock_qty-?) WHERE id=?",
            (med['qty'], med_id))
