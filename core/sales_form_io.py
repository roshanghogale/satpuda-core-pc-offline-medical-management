"""
Load an existing sale into a BillingPage (main screen or edit window).
"""
from __future__ import annotations

import copy

from core.billing_service import load_sale_medicines
from core.margin_utils import enrich_medicine_margin_fields
from core.customer_service import get_customer_names


def load_sale_into_billing_page(conn, page, sale_id: int):
    """Populate BillingPage fields from a saved sale."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return _load_sale_online(conn, page, int(sale_id))
    except Exception as exc:
        print(f"[sales_form_io] online load failed: {exc}")

    cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.bill_no, s.customer_id, s.bill_date, s.total_amount,
               s.discount, s.amount_paid,
               COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
               s.previous_due, s.total_due, s.due_amount, s.credit_amount,
               s.doctor_name, s.created_at, c.name, c.phone,
               COALESCE(s.previous_credit,0), COALESCE(c.address, ''),
               COALESCE(s.discount_pct,0), COALESCE(s.rounding,0),
               COALESCE(s.is_autosave,0)
        FROM sales s JOIN customers c ON s.customer_id=c.id
        WHERE s.id=?
    """, (sale_id,))
    row = cur.fetchone()
    if not row:
        return False

    (sale_id, bill_no, customer_id, bill_date, total_amount,
     discount, amount_paid, cash_paid, online_paid,
     previous_due, total_due, due_amount, credit_amount,
     doctor_name, created_at, customer_name, customer_phone,
     previous_credit, customer_address, discount_pct, rounding,
     is_autosave) = row

    return _apply_sale_to_page(
        conn,
        page,
        sale_id=sale_id,
        bill_no=bill_no,
        customer_id=customer_id,
        bill_date=bill_date,
        cash_paid=cash_paid,
        online_paid=online_paid,
        previous_due=previous_due,
        previous_credit=previous_credit,
        doctor_name=doctor_name,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_address=customer_address,
        discount=discount,
        discount_pct=discount_pct,
        rounding=rounding,
        is_autosave=is_autosave,
        medicines=None,
        enrich_local=True,
    )


def _load_sale_online(conn, page, sale_id: int) -> bool:
    from core.server_crud import get_doc
    from core.online_catalog import find_customer_by_id, medicine_by_id

    doc = get_doc("sales", int(sale_id)) or {}
    if not doc or doc.get("deleted"):
        return False

    customer_id = int(doc.get("customer_id") or 0)
    cust = {}
    try:
        cust = find_customer_by_id(customer_id) or {}
    except Exception:
        cust = {}
    if not cust and customer_id:
        cust = get_doc("customers", customer_id) or {}

    medicines = load_sale_medicines(conn, sale_id)
    for med in medicines:
        mid = int(med.get("id") or 0)
        mp = medicine_by_id(mid) if mid else None
        if not mp:
            continue
        list_mrp = float(mp.get("mrp") or med.get("rate") or 0)
        purchase_rate = float(mp.get("rate") or 0)
        enrich_medicine_margin_fields(
            med,
            list_mrp,
            purchase_rate,
            mp.get("type") or med.get("type"),
            mp.get("unit") or med.get("unit") or "1",
        )
        med["location"] = str(mp.get("location") or "").strip()
        if "original_amount" not in med:
            med["original_amount"] = round(
                float(med.get("qty") or 0) * float(med.get("rate") or 0), 2
            )

    return _apply_sale_to_page(
        conn,
        page,
        sale_id=int(doc.get("id") or doc.get("local_id") or sale_id),
        bill_no=doc.get("bill_no") or "",
        customer_id=customer_id,
        bill_date=doc.get("bill_date") or "",
        cash_paid=doc.get("cash_paid") or 0,
        online_paid=doc.get("online_paid") or 0,
        previous_due=doc.get("previous_due") or 0,
        previous_credit=doc.get("previous_credit") or 0,
        doctor_name=doc.get("doctor_name") or "",
        customer_name=cust.get("name") or doc.get("customer_name") or "",
        customer_phone=cust.get("phone") or doc.get("customer_phone") or "",
        customer_address=cust.get("address") or doc.get("customer_address") or "",
        discount=doc.get("discount") or 0,
        discount_pct=doc.get("discount_pct") or 0,
        rounding=doc.get("rounding") or 0,
        is_autosave=doc.get("is_autosave") or 0,
        medicines=medicines,
        enrich_local=False,
    )


def _apply_sale_to_page(
    conn,
    page,
    *,
    sale_id,
    bill_no,
    customer_id,
    bill_date,
    cash_paid,
    online_paid,
    previous_due,
    previous_credit,
    doctor_name,
    customer_name,
    customer_phone,
    customer_address,
    discount,
    discount_pct,
    rounding,
    is_autosave,
    medicines,
    enrich_local: bool,
):
    is_as = int(is_autosave or 0)
    page._editing_sale_id = None if is_as else sale_id
    page._autosave_sale_id = sale_id if is_as else None
    page._edit_payment_snapshot = {
        "previous_due": float(previous_due or 0),
        "previous_credit": float(previous_credit or 0),
    }

    cash_f = float(cash_paid or 0)
    online_f = float(online_paid or 0)

    page.cash_paid.delete(0, "end")
    page.cash_paid.insert(0, f"{cash_f:g}" if cash_f else "0")
    page.online_paid.delete(0, "end")
    page.online_paid.insert(0, f"{online_f:g}" if online_f else "0")

    if cash_f == 0 and online_f == 0:
        page.payment_mode.set("Due")
    else:
        page.payment_mode.set("Cash")
    page._on_payment_mode_change()

    page.customer_name.configure(values=get_customer_names(conn))
    page.customer_name.set(customer_name or "")
    page._customer_id = customer_id

    page.customer_phone.delete(0, "end")
    page.customer_phone.insert(0, customer_phone or "")
    page.customer_address.set(customer_address or "")

    page.doctor_name.set(doctor_name or "")
    page.doctor_phone.delete(0, "end")

    bill_dt = bill_date or ""
    if bill_dt and " " in str(bill_dt):
        bill_dt = str(bill_dt).split(" ")[0]
    if hasattr(page, "_set_bill_date_value"):
        page._set_bill_date_value(bill_dt)
    elif hasattr(page, "bill_date_var"):
        page.bill_date_var.set(str(bill_dt or ""))

    page.discount_pct.delete(0, "end")
    page.discount_pct.insert(0, f"{float(discount_pct or 0):.4g}")
    page.discount.delete(0, "end")
    page.discount.insert(0, f"{float(discount or 0):.2f}")
    page.rounding.delete(0, "end")
    page.rounding.insert(0, f"{float(rounding or 0):.2f}")
    page._rounding_touched = True

    page.previous_due = float(previous_due or 0)
    page.previous_credit = float(previous_credit or 0)
    page.previous_due_var.set(f"{page.previous_due:.2f}")

    if medicines is None:
        medicines = load_sale_medicines(conn, sale_id)
    if enrich_local:
        cur = conn.cursor()
        for med in medicines:
            _enrich_med_from_db(cur, med)

    page.selected_medicines = copy.deepcopy(medicines)
    page._sync_medicine_reserved_stock()
    page.update_medicine_tree()
    page.calculate_total()
    return True


def _enrich_med_from_db(cur, med):
    cur.execute("""
        SELECT m.type, COALESCE(m.unit,'1'), COALESCE(m.location,''), COALESCE(m.mrp,0),
               (SELECT pi.rate FROM purchase_items pi
                WHERE pi.medicine_id=m.id ORDER BY pi.id DESC LIMIT 1)
        FROM medicines m WHERE m.id=?
    """, (med['id'],))
    info = cur.fetchone()
    if not info:
        return
    list_mrp = float(info[3] or med.get('rate') or 0)
    purchase_rate = float(info[4] or 0)
    enrich_medicine_margin_fields(med, list_mrp, purchase_rate, info[0], info[1])
    med['location'] = (info[2] or '').strip()
    if 'original_amount' not in med:
        med['original_amount'] = round(med['qty'] * med['rate'], 2)
