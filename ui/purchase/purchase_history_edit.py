"""
ui/purchase_history_edit.py
────────────────────────────
Edit and delete operations for purchase history.
Called by PurchaseHistoryPage — no UI building here.
"""
import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
import logging

from core.purchase_calculator import PurchaseCalculator
from core.purchase_service import (
    get_or_create_supplier, update_purchase, expiry_to_display
)
from core.scroll_manager import open_dialog


def open_edit_window(parent, conn, purchase_id, bill_label, refresh_callback):
    """Open the purchase edit window and wire the Update button."""
    from ui.purchase import PurchasePage

    edit_window = open_dialog(
        parent, f"Edit Purchase - {bill_label}",
        width=1400, height=900, resizable=False)
    edit_window.state('zoomed')

    page = PurchasePage(edit_window, conn)
    edit_window.update_idletasks()

    _load_for_edit(conn, page, purchase_id)

    def _update():
        _save_edit(conn, page, purchase_id, bill_label, edit_window, refresh_callback)

    page.save_btn.config(text="Update Purchase", command=_update)
    edit_window.protocol("WM_DELETE_WINDOW",
                         lambda: [edit_window.destroy(), refresh_callback()])


def _load_for_edit(conn, page, purchase_id):
    """Populate a PurchasePage with data from an existing purchase."""
    cur = conn.cursor()

    # Supplier
    cur.execute("""
        SELECT s.name, s.address, s.phone, s.gstin, s.dl_numbers
        FROM suppliers s JOIN purchases p ON s.id=p.supplier_id WHERE p.id=?
    """, (purchase_id,))
    sup = cur.fetchone()

    # Purchase header
    cur.execute("""
        SELECT bill_number, COALESCE(overall_discount,0), COALESCE(rounding,0),
               COALESCE(amount_paid_at_entry, amount_paid, 0), COALESCE(previous_due,0),
               COALESCE(previous_credit,0), COALESCE(due,0),
               COALESCE(current_credit,0), purchase_date
        FROM purchases WHERE id=?
    """, (purchase_id,))
    hdr = cur.fetchone()
    if not hdr:
        return

    (bill_number, overall_disc, rounding, amount_paid,
     prev_due, prev_credit, due, cur_credit, pur_date) = hdr

    if sup:
        page.supplier_name.set(sup[0])
        for entry, val in zip(
            [page.supplier_address, page.supplier_phone,
             page.supplier_gstin, page.supplier_dl], sup[1:]):
            entry.delete(0, tk.END)
            entry.insert(0, val or '')

    page.bill_number.delete(0, tk.END)
    page.bill_number.insert(0, bill_number or '')
    page.purchase_date.delete(0, tk.END)
    page.purchase_date.insert(0, str(pur_date) if pur_date else '')

    # Items
    cur.execute("""
        SELECT pi.medicine_id, m.name, pi.type, pi.batch_no, pi.expiry_date,
               pi.qty, pi.free_qty, pi.rate,
               COALESCE(pi.gst_pct, pi.gst_value, 0),
               pi.mrp, pi.manufacturer, pi.schedule,
               COALESCE(pi.item_amount, pi.amount, 0),
               pi.hsn_code,
               COALESCE(pi.discount_pct, pi.discount_percent, 0)
        FROM purchase_items pi JOIN medicines m ON pi.medicine_id=m.id
        WHERE pi.purchase_id=?
    """, (purchase_id,))

    page.purchase_items = []
    for row in cur.fetchall():
        item = {
            'medicine_id':   row[0],
            'name':          row[1],
            'type':          row[2],
            'batch':         row[3],
            'expiry':        expiry_to_display(row[4]),
            'qty':           row[5],
            'free_qty':      row[6],
            'rate':          row[7],
            'gst_pct':       row[8],
            'mrp':           row[9],
            'manufacturer':  row[10] or '',
            'schedule':      row[11] or '',
            'item_amount':   row[12],
            'amount':        row[12],
            'hsn_code':      row[13] or '',
            'discount_pct':  row[14],
            'taxable':       0,   # will be recomputed by PurchaseCalculator
            'gst_amt':       0,
        }
        page.purchase_items.append(item)

    # Payment fields
    page.overall_discount.delete(0, tk.END)
    page.overall_discount.insert(0, str(overall_disc))
    page.rounding_entry.delete(0, tk.END)
    page.rounding_entry.insert(0, str(rounding))
    page.previous_due_var.set(f"{prev_due:.2f}")
    page.previous_credit_var.set(f"{prev_credit:.2f}")
    page.amount_paid.delete(0, tk.END)
    page.amount_paid.insert(0, str(amount_paid))

    page.update_items_tree()
    page.calculate_total()


def _save_edit(conn, page, purchase_id, bill_label, edit_window, refresh_callback):
    """Run PurchaseCalculator and call purchase_service.update_purchase."""
    try:
        overall_discount = float(page.overall_discount.get() or 0)
        rounding         = float(page.rounding_entry.get() or 0)
        previous_due     = float(page.previous_due_var.get() or 0)
        previous_credit  = float(page.previous_credit_var.get() or 0)
        amount_paid      = float(page.amount_paid.get() or 0)
    except ValueError:
        showerror("Invalid Input", "Please check payment fields.")
        return

    result = PurchaseCalculator(
        items=page.purchase_items,
        overall_discount=overall_discount,
        rounding=rounding,
        previous_due=previous_due,
        previous_credit=previous_credit,
        amount_paid=amount_paid,
    ).calculate()

    try:
        supplier_id = get_or_create_supplier(
            conn,
            page.supplier_name.get().strip(),
            page.supplier_address.get(),
            page.supplier_phone.get(),
            page.supplier_gstin.get(),
            page.supplier_dl.get(),
        )
        update_purchase(
            conn, purchase_id, supplier_id,
            page.bill_number.get().strip(),
            page.purchase_date.get().strip(),
            result,
            page.purchase_items,
        )
        showinfo("Success", f"Purchase {bill_label} updated successfully!")
        edit_window.destroy()
        refresh_callback()
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to update purchase: {e}")


def delete_purchase(conn, purchase_id, bill_label, refresh_callback):
    """Validate stock, delete purchase + items, restore stock correctly."""
    if not askyesno(
        "Confirm Delete",
        f"Delete purchase {bill_label}?\n"
        "This will reduce stock quantities and cannot be undone."):
        return

    from core.purchase_service import _reverse_stock_for_purchase, recalculate_supplier_due
    cur = conn.cursor()
    try:
        # Fetch supplier_id before deleting
        cur.execute("SELECT supplier_id FROM purchases WHERE id=?", (purchase_id,))
        sup_row = cur.fetchone()
        supplier_id = sup_row[0] if sup_row else None

        # Validate stock won't go negative
        cur.execute("""
            SELECT pi.medicine_id, pi.qty, pi.free_qty, pi.type,
                   COALESCE(m.unit,'1'), m.stock_qty
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id=m.id
            WHERE pi.purchase_id=?
        """, (purchase_id,))
        import re as _re
        for med_id, qty, free_qty, med_type, unit_str, stock in cur.fetchall():
            if (med_type or '').lower() in ('tablet', 'bolus'):
                nums = _re.findall(r'\d+', str(unit_str or ''))
                tps  = int(nums[0]) if nums else 1
                decrease = (float(qty or 0) + float(free_qty or 0)) * tps
            else:
                decrease = float(qty or 0) + float(free_qty or 0)
            if float(stock or 0) < decrease:
                showwarning(
                    "Insufficient Stock",
                    f"Cannot delete — stock would go negative.\n"
                    f"Current: {stock}, Required to remove: {decrease:.0f}")
                return

        # Reverse stock using correct tablet-aware logic
        _reverse_stock_for_purchase(cur, purchase_id)

        cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
        cur.execute("DELETE FROM purchases WHERE id=?", (purchase_id,))
        conn.commit()

        if supplier_id:
            recalculate_supplier_due(conn, supplier_id)

        showinfo("Success", "Purchase deleted successfully!")
        refresh_callback()
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to delete purchase: {e}")
