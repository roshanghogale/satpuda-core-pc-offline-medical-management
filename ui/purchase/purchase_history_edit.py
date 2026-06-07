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

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


def open_edit_window(parent, conn, purchase_id, bill_label, refresh_callback):
    """Open the purchase edit window and wire the Update button."""
    from ui.purchase import PurchasePage
    from core.scroll_manager import bind_scroll_descendants, refresh_scroll_region

    edit_window = tk.Toplevel(parent)
    edit_window.title(f"Edit Purchase - {bill_label}")
    try:
        from core.window_icon import apply_window_icon
        apply_window_icon(edit_window, master=parent.winfo_toplevel(), is_root=False)
    except Exception:
        pass
    edit_window.state('zoomed')

    container = ttk.Frame(edit_window)
    container.pack(fill=tk.BOTH, expand=True)

    page = PurchasePage(container, conn)
    page._editing_purchase_id = purchase_id

    root = parent.winfo_toplevel()
    ctrl = getattr(root, '_input_ctrl', None)
    prev_canvas = prev_frame = None
    if ctrl is not None:
        prev_canvas = ctrl._canvas
        prev_frame = getattr(ctrl, '_active_frame', None)
        ctrl.set_active_canvas(getattr(page._inner_frame, '_canvas', None))
        ctrl.set_active_frame(page._inner_frame)

    try:
        bind_scroll_descendants(page._inner_frame, force=True)
        refresh_scroll_region(page._inner_frame)
    except TypeError:
        bind_scroll_descendants(page._inner_frame)
        refresh_scroll_region(page._inner_frame)

    _load_for_edit(conn, page, purchase_id)

    _closed = [False]

    def _close():
        if _closed[0]:
            return
        _closed[0] = True
        if ctrl is not None:
            ctrl.set_active_canvas(prev_canvas)
            ctrl.set_active_frame(prev_frame)
        try:
            edit_window.destroy()
        except Exception:
            pass
        refresh_callback()

    def _update():
        _save_edit(conn, page, purchase_id, bill_label, _close)

    page.save_btn.config(text="Update Purchase", command=_update)
    edit_window.protocol("WM_DELETE_WINDOW", _close)
    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(edit_window, on_close=_close)


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
               COALESCE(current_credit,0), purchase_date,
               COALESCE(need_to_pay,0), COALESCE(total_amount,0)
        FROM purchases WHERE id=?
    """, (purchase_id,))
    hdr = cur.fetchone()
    if not hdr:
        return

    (bill_number, overall_disc, rounding, amount_paid,
     prev_due, prev_credit, due, cur_credit, pur_date,
     need_to_pay, total_amount) = hdr

    # Reconstruct opening balance (excludes this bill) — fixes inflated previous_due
    if total_amount is not None and need_to_pay is not None:
        reconstructed = round(float(need_to_pay) - float(total_amount) + float(prev_credit), 2)
        if reconstructed >= 0 and abs(reconstructed - float(prev_due)) > 0.02:
            prev_due = reconstructed

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

    page._edit_payment_snapshot = {
        'previous_due': float(prev_due),
        'previous_credit': float(prev_credit),
    }

    page.update_items_tree()
    page.sync_overall_discount_fields('rupees')
    page.calculate_total()


def _save_edit(conn, page, purchase_id, bill_label, close_fn):
    """Run PurchaseCalculator and call purchase_service.update_purchase."""
    try:
        overall_discount = float(page.overall_discount.get() or 0)
        rounding         = float(page.rounding_entry.get() or 0)
        amount_paid      = float(page.amount_paid.get() or 0)
        snap = getattr(page, '_edit_payment_snapshot', None) or {}
        previous_due = float(snap.get('previous_due', page.previous_due_var.get() or 0))
        previous_credit = float(snap.get('previous_credit', page.previous_credit_var.get() or 0))
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
        page._editing_purchase_id = None
        page._edit_payment_snapshot = None
        close_fn()
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

        # Capture medicine/batch rows before delete so we can clean inventory rows.
        cur.execute("""
            SELECT DISTINCT
                pi.medicine_id,
                COALESCE(pi.batch_no, ''),
                COALESCE(m.name, ''),
                COALESCE(pi.expiry_date, '')
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            WHERE pi.purchase_id=?
        """, (purchase_id,))
        touched_rows = cur.fetchall()

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

        # Remove inventory rows when no purchase exists for same medicine+batch.
        # This keeps inventory clean instead of leaving dead rows at stock 0.
        for med_id, batch_no, med_name, expiry_db in touched_rows:
            cur.execute("""
                SELECT 1
                FROM purchase_items
                WHERE medicine_id=? AND COALESCE(batch_no,'')=?
                LIMIT 1
            """, (med_id, batch_no))
            still_exists = cur.fetchone() is not None
            if still_exists:
                continue

            # Guardrail: keep medicine row if sales history still references it.
            cur.execute("SELECT 1 FROM sales_items WHERE medicine_id=? LIMIT 1", (med_id,))
            has_sales_history = cur.fetchone() is not None
            if has_sales_history:
                continue

            cur.execute("""
                DELETE FROM medicines
                WHERE id=? AND COALESCE(batch_no,'')=? AND COALESCE(name,'')=? AND COALESCE(expiry_date,'')=?
            """, (med_id, batch_no, med_name, expiry_db))
        conn.commit()

        if supplier_id:
            recalculate_supplier_due(conn, supplier_id)

        showinfo("Success", "Purchase deleted successfully!")
        refresh_callback()
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to delete purchase: {e}")
