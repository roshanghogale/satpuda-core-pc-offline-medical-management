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

from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
from core.purchase_calculator import PurchaseCalculator, reconcile_items_gst_with_header
from core.purchase_service import (
    get_or_create_supplier, update_purchase, expiry_to_display,
    refresh_inventory_if_loaded,
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

    _load_ok = _load_for_edit(conn, page, purchase_id)
    if not _load_ok:
        try:
            edit_window.destroy()
        except Exception:
            pass
        showerror(
            "Edit Purchase",
            f"Could not load purchase {bill_label} from the server.\n"
            "Refresh Purchase History and try again.",
            parent=parent,
        )
        return

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
    if hasattr(page, 'recalculate_btn'):
        page.recalculate_btn.pack(fill=tk.X, pady=(4, 0))
    edit_window.protocol("WM_DELETE_WINDOW", _close)
    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(edit_window, on_close=_close)


def _load_for_edit(conn, page, purchase_id):
    """Populate a PurchasePage with data from an existing purchase. Returns True on success."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            if _load_for_edit_online(conn, page, purchase_id):
                return True
    except Exception as exc:
        logging.getLogger(__name__).warning("online purchase load failed: %s", exc)

    # Old DBs (pre cash/online purchase fields) must be upgraded before SELECT.
    try:
        from core.db_setup import ensure_purchase_payment_columns
        ensure_purchase_payment_columns(conn)
    except Exception:
        pass
    cur = conn.cursor()

    # Supplier
    cur.execute("""
        SELECT s.name, s.address, s.phone, s.gstin, s.dl_numbers
        FROM suppliers s JOIN purchases p ON s.id=p.supplier_id WHERE p.id=?
    """, (purchase_id,))
    sup = cur.fetchone()

    # Purchase header — payment inputs + stored totals for GST reconciliation
    cur.execute("""
        SELECT bill_number, COALESCE(overall_discount,0),
               COALESCE(cash_paid_at_entry, 0), COALESCE(online_paid_at_entry, 0),
               COALESCE(amount_paid_at_entry, amount_paid, 0), COALESCE(previous_due,0),
               COALESCE(previous_credit,0), purchase_date,
               COALESCE(gst_calc_method, 'discount_before_gst'),
               COALESCE(cgst, 0), COALESCE(total_amount, 0),
               COALESCE(rounding, 0), COALESCE(expenditure, 0),
               COALESCE(subtotal, 0), COALESCE(sgst, 0), COALESCE(total_gst, 0),
               COALESCE(final_amount, 0), COALESCE(need_to_pay, 0),
               COALESCE(due, 0), COALESCE(current_credit, 0),
               COALESCE(due_amount, 0)
        FROM purchases WHERE id=?
    """, (purchase_id,))
    hdr = cur.fetchone()
    if not hdr:
        return False

    (bill_number, overall_disc, cash_paid, online_paid, amount_paid,
     prev_due, prev_credit, pur_date, gst_calc_method,
     stored_cgst, stored_total, stored_rounding, stored_expenditure,
     stored_subtotal, stored_sgst, stored_total_gst,
     stored_final, stored_need_to_pay, stored_due, stored_cur_credit,
     stored_due_amount) = hdr

    cur.execute("""
        SELECT COALESCE(need_to_pay, 0), COALESCE(current_credit, 0)
        FROM purchases WHERE id=?
    """, (purchase_id,))
    pay_row = cur.fetchone()
    if pay_row:
        need_to_pay, cur_credit = pay_row
        reconstructed = round(
            float(need_to_pay) - float(stored_total) + float(cur_credit), 2,
        )
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
    if hasattr(page, 'set_gst_calc_method'):
        method = (gst_calc_method or 'discount_before_gst').strip()
        if method not in ('discount_before_gst', 'discount_after_gst'):
            method = 'discount_before_gst'
        page.set_gst_calc_method(method)
    elif hasattr(page, 'gst_calc_method_var'):
        method = (gst_calc_method or 'discount_before_gst').strip()
        if method not in ('discount_before_gst', 'discount_after_gst'):
            method = 'discount_before_gst'
        page.gst_calc_method_var.set(method)
    else:
        method = 'discount_before_gst'

    # Items
    cur.execute("""
        SELECT pi.medicine_id, m.name, pi.type, pi.batch_no, pi.expiry_date,
               pi.qty, pi.free_qty, pi.rate,
               COALESCE(pi.gst_pct, pi.gst_value, 0),
               pi.mrp, pi.manufacturer, pi.schedule,
               COALESCE(pi.item_amount, pi.amount, 0),
               pi.hsn_code,
               COALESCE(pi.discount_pct, pi.discount_percent, 0),
               COALESCE(m.content_drug, ''),
               COALESCE(m.unit, ''),
               COALESCE(pi.taxable, 0),
               COALESCE(pi.gst_amt, 0)
        FROM purchase_items pi JOIN medicines m ON pi.medicine_id=m.id
        WHERE pi.purchase_id=?
    """, (purchase_id,))

    page.purchase_items = []
    for row in cur.fetchall():
        med_type = row[2] or ''
        qty = float(row[5] or 0)
        free_qty = float(row[6] or 0)
        unit = row[16] or ''
        item = {
            'medicine_id':   row[0],
            'name':          row[1],
            'type':          med_type,
            'batch':         row[3],
            'expiry':        expiry_to_display(row[4]),
            'qty':           qty,
            'free_qty':      free_qty,
            'rate':          row[7],
            'gst_pct':       row[8],
            'mrp':           row[9],
            'manufacturer':  row[10] or '',
            'schedule':      row[11] or '',
            'hsn_code':      row[13] or '',
            'discount_pct':  row[14],
            'content_drug':  row[15] or '',
            'item_amount':   float(row[12] or 0),
            'amount':        float(row[12] or 0),
            'taxable':       float(row[17] or 0),
            'gst_amt':       float(row[18] or 0),
        }
        if is_strip_count_type(med_type):
            tps = parse_tablets_per_stripe(unit) if unit else 1
            if tps <= 0:
                tps = 1
            item['tablets_per_stripe'] = tps
            item['total_tablets'] = qty * tps
            item['free_tablets'] = free_qty * tps
        elif unit:
            item['quantity_value'] = unit
        item['_preserve_line_totals'] = True
        page.purchase_items.append(item)

    _apply_edit_totals_to_page(
        page,
        overall_disc=overall_disc,
        cash_paid=cash_paid,
        online_paid=online_paid,
        amount_paid=amount_paid,
        prev_due=prev_due,
        prev_credit=prev_credit,
        method=method,
        stored_cgst=stored_cgst,
        stored_total=stored_total,
        stored_rounding=stored_rounding,
        stored_expenditure=stored_expenditure,
        stored_subtotal=stored_subtotal,
        stored_sgst=stored_sgst,
        stored_total_gst=stored_total_gst,
        stored_final=stored_final,
        stored_need_to_pay=stored_need_to_pay,
        stored_due=stored_due,
        stored_cur_credit=stored_cur_credit,
        stored_due_amount=stored_due_amount,
    )

    return True


def _load_for_edit_online(conn, page, purchase_id):
    """Online: load purchase + supplier from server (local SQLite may be empty)."""
    from core.server_crud import get_doc
    from core.online_catalog import medicine_by_id, suppliers as oc_suppliers

    doc = get_doc("purchases", int(purchase_id)) or {}
    if not doc or doc.get("deleted"):
        return False

    supplier_id = int(doc.get("supplier_id") or 0)
    sup = {}
    if supplier_id:
        try:
            for s in oc_suppliers() or []:
                try:
                    if int(s.get("id") or s.get("local_id") or 0) == supplier_id:
                        sup = s
                        break
                except (TypeError, ValueError):
                    continue
        except Exception:
            sup = {}
        if not sup:
            sup = get_doc("suppliers", supplier_id) or {}

    if not sup:
        # Fall back to denormalized fields on the purchase doc.
        sup = {
            "name": doc.get("supplier_name") or "",
            "address": doc.get("supplier_address") or "",
            "phone": doc.get("supplier_phone") or "",
            "gstin": doc.get("supplier_gstin") or "",
            "dl_numbers": doc.get("supplier_dl") or doc.get("dl_numbers") or "",
        }

    page.supplier_name.set(sup.get("name") or doc.get("supplier_name") or "")
    for entry, key in zip(
        [page.supplier_address, page.supplier_phone, page.supplier_gstin, page.supplier_dl],
        ["address", "phone", "gstin", "dl_numbers"],
    ):
        entry.delete(0, tk.END)
        entry.insert(0, (sup.get(key) or "") if isinstance(sup, dict) else "")

    bill_number = doc.get("bill_number") or ""
    overall_disc = float(doc.get("overall_discount") or 0)
    cash_paid = float(doc.get("cash_paid_at_entry") or 0)
    online_paid = float(doc.get("online_paid_at_entry") or 0)
    amount_paid = float(
        doc.get("amount_paid_at_entry")
        if doc.get("amount_paid_at_entry") is not None
        else (doc.get("amount_paid") or 0)
    )
    prev_due = float(doc.get("previous_due") or 0)
    prev_credit = float(doc.get("previous_credit") or 0)
    pur_date = doc.get("purchase_date") or ""
    gst_calc_method = doc.get("gst_calc_method") or "discount_before_gst"
    stored_cgst = float(doc.get("cgst") or 0)
    stored_total = float(doc.get("total_amount") or 0)
    stored_rounding = float(doc.get("rounding") or 0)
    stored_expenditure = float(doc.get("expenditure") or 0)
    stored_subtotal = float(doc.get("subtotal") or 0)
    stored_sgst = float(doc.get("sgst") or 0)
    stored_total_gst = float(doc.get("total_gst") or 0)
    stored_final = float(doc.get("final_amount") or stored_total or 0)
    stored_need_to_pay = float(doc.get("need_to_pay") or 0)
    stored_due = float(doc.get("due") or 0)
    stored_cur_credit = float(doc.get("current_credit") or 0)
    stored_due_amount = float(doc.get("due_amount") or stored_due or 0)

    reconstructed = round(
        float(stored_need_to_pay) - float(stored_total) + float(stored_cur_credit), 2,
    )
    if reconstructed >= 0 and abs(reconstructed - float(prev_due)) > 0.02:
        prev_due = reconstructed

    page.bill_number.delete(0, tk.END)
    page.bill_number.insert(0, bill_number or '')
    page.purchase_date.delete(0, tk.END)
    page.purchase_date.insert(0, str(pur_date)[:10] if pur_date else '')
    if hasattr(page, 'set_gst_calc_method'):
        method = (gst_calc_method or 'discount_before_gst').strip()
        if method not in ('discount_before_gst', 'discount_after_gst'):
            method = 'discount_before_gst'
        page.set_gst_calc_method(method)
    elif hasattr(page, 'gst_calc_method_var'):
        method = (gst_calc_method or 'discount_before_gst').strip()
        if method not in ('discount_before_gst', 'discount_after_gst'):
            method = 'discount_before_gst'
        page.gst_calc_method_var.set(method)
    else:
        method = 'discount_before_gst'

    page.purchase_items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        mid = int(it.get("medicine_id") or 0)
        mp = medicine_by_id(mid) if mid else None
        if not mp and mid:
            mp = get_doc("medicines", mid) or {}
        mp = mp or {}
        med_type = it.get("type") or mp.get("type") or ""
        qty = float(it.get("qty") or it.get("quantity") or 0)
        free_qty = float(it.get("free_qty") or 0)
        unit = str(
            it.get("unit")
            or it.get("tablets_per_stripe")
            or it.get("quantity_value")
            or mp.get("unit")
            or ""
        )
        amount = float(
            it.get("item_amount")
            if it.get("item_amount") is not None
            else (it.get("amount") or 0)
        )
        item = {
            "medicine_id": mid,
            "name": it.get("name") or it.get("medicine_name") or mp.get("name") or "",
            "type": med_type,
            "batch": it.get("batch_no") or it.get("batch") or mp.get("batch_no") or "",
            "expiry": expiry_to_display(it.get("expiry_date") or it.get("expiry") or mp.get("expiry_date") or ""),
            "qty": qty,
            "free_qty": free_qty,
            "rate": float(it.get("rate") or 0),
            "gst_pct": float(it.get("gst_pct") or it.get("gst_percent") or it.get("gst_value") or 0),
            "mrp": float(it.get("mrp") or mp.get("mrp") or 0),
            "manufacturer": it.get("manufacturer") or mp.get("manufacturer") or "",
            "schedule": it.get("schedule") or mp.get("schedule") or "",
            "hsn_code": it.get("hsn_code") or mp.get("hsn_code") or "",
            "discount_pct": float(it.get("discount_pct") or it.get("discount_percent") or 0),
            "content_drug": it.get("content_drug") or mp.get("content_drug") or "",
            "item_amount": amount,
            "amount": amount,
            "taxable": float(it.get("taxable") or 0),
            "gst_amt": float(it.get("gst_amt") or 0),
        }
        if is_strip_count_type(med_type):
            tps = parse_tablets_per_stripe(unit) if unit else 1
            if tps <= 0:
                tps = 1
            item["tablets_per_stripe"] = tps
            item["total_tablets"] = qty * tps
            item["free_tablets"] = free_qty * tps
        elif unit:
            item["quantity_value"] = unit
        item["_preserve_line_totals"] = True
        page.purchase_items.append(item)

    _apply_edit_totals_to_page(
        page,
        overall_disc=overall_disc,
        cash_paid=cash_paid,
        online_paid=online_paid,
        amount_paid=amount_paid,
        prev_due=prev_due,
        prev_credit=prev_credit,
        method=method,
        stored_cgst=stored_cgst,
        stored_total=stored_total,
        stored_rounding=stored_rounding,
        stored_expenditure=stored_expenditure,
        stored_subtotal=stored_subtotal,
        stored_sgst=stored_sgst,
        stored_total_gst=stored_total_gst,
        stored_final=stored_final,
        stored_need_to_pay=stored_need_to_pay,
        stored_due=stored_due,
        stored_cur_credit=stored_cur_credit,
        stored_due_amount=stored_due_amount,
    )
    return True


def _apply_edit_totals_to_page(
    page,
    *,
    overall_disc,
    cash_paid,
    online_paid,
    amount_paid,
    prev_due,
    prev_credit,
    method,
    stored_cgst,
    stored_total,
    stored_rounding,
    stored_expenditure,
    stored_subtotal,
    stored_sgst,
    stored_total_gst,
    stored_final,
    stored_need_to_pay,
    stored_due,
    stored_cur_credit,
    stored_due_amount,
):
    page.purchase_items[:] = reconcile_items_gst_with_header(
        page.purchase_items,
        stored_cgst,
        stored_total,
        overall_discount=float(overall_disc or 0),
        gst_calc_method=method,
    )

    page._edit_payment_snapshot = {
        'previous_due': float(prev_due),
        'previous_credit': float(prev_credit),
    }

    page._suppress_purchase_calc = True
    try:
        page._set_entry_quiet(page.overall_discount, overall_disc)
        page._set_entry_quiet(page.overall_discount_pct, '0')
        page._set_entry_quiet(page.rounding_entry, stored_rounding, lambda v: f"{v:.2f}")
        if hasattr(page, 'expenditure_entry'):
            page._set_entry_quiet(
                page.expenditure_entry, stored_expenditure, lambda v: f"{v:.2f}",
            )
        page.previous_due_var.set(f"{prev_due:.2f}")
        page.previous_credit_var.set(f"{prev_credit:.2f}")
    finally:
        page._suppress_purchase_calc = False

    cash_val = float(cash_paid or 0)
    online_val = float(online_paid or 0)
    total_paid = float(amount_paid or 0)
    if cash_val <= 0 and online_val <= 0 and total_paid > 0:
        cash_val = total_paid
    page._set_entry_quiet(
        page.cash_paid, cash_val, lambda v: f"{v:.2f}" if float(v or 0) else '',
    )
    page._set_entry_quiet(
        page.online_paid, online_val, lambda v: f"{v:.2f}" if float(v or 0) else '',
    )
    if hasattr(page, 'amount_paid_var'):
        page.amount_paid_var.set(f"₹{total_paid:.2f}" if total_paid else "")

    gross_subtotal = round(float(stored_subtotal or 0) + float(overall_disc or 0), 2)
    page.apply_stored_edit_totals({
        'gross_subtotal': gross_subtotal,
        'subtotal': float(stored_subtotal or 0),
        'cgst': float(stored_cgst or 0),
        'sgst': float(stored_sgst or 0),
        'total_gst': float(stored_total_gst or 0),
        'total_amount': float(stored_total or 0),
        'overall_discount': float(overall_disc or 0),
        'rounding': float(stored_rounding or 0),
        'expenditure': float(stored_expenditure or 0),
        'final_amount': float(stored_final or stored_total or 0),
        'need_to_pay': float(stored_need_to_pay or 0),
        'previous_due': float(prev_due or 0),
        'previous_credit': float(prev_credit or 0),
        'cash_paid': cash_val,
        'online_paid': online_val,
        'amount_paid': total_paid,
        'due': float(stored_due_amount or stored_due or 0),
        'due_amount': float(stored_due_amount or stored_due or 0),
        'current_credit': float(stored_cur_credit or 0),
        'total_due': float(stored_due_amount or stored_due or 0),
        'gst_calc_method': method,
        'items': page.purchase_items,
    })


def _save_edit(conn, page, purchase_id, bill_label, close_fn):
    """Run PurchaseCalculator and call purchase_service.update_purchase."""
    try:
        overall_discount = float(page.overall_discount.get() or 0)
        rounding         = float(page.rounding_entry.get() or 0)
        expenditure      = float(getattr(page, 'expenditure_entry', None) and page.expenditure_entry.get() or 0)
        amount_paid      = float(
            (page.amount_paid_var.get() or '').replace('₹', '').strip() or 0
        ) if hasattr(page, 'amount_paid_var') else 0.0
        cash_paid        = float((page.cash_paid.get() or 0) or 0)
        online_paid      = float((page.online_paid.get() or 0) or 0)
        snap = getattr(page, '_edit_payment_snapshot', None) or {}
        previous_due = float(snap.get('previous_due', page.previous_due_var.get() or 0))
        previous_credit = float(snap.get('previous_credit', page.previous_credit_var.get() or 0))
    except ValueError:
        showerror("Invalid Input", "Please check payment fields.")
        return

    page._calc_event = None
    result = page._run_purchase_calc(force_auto_round=False)
    if not result:
        showerror("Error", "Calculation failed.")
        return
    page._apply_calc_result(result)

    from core.background_workers import run_on_ui_with_busy

    def _do_update():
        # Always upsert supplier contact (phone/address/gstin/dl) from the form.
        supplier_id = get_or_create_supplier(
            conn,
            (page.supplier_name.get() or '').strip(),
            page.supplier_address.get() or '',
            page.supplier_phone.get() or '',
            page.supplier_gstin.get() or '',
            page.supplier_dl.get() or '',
        )
        update_purchase(
            conn, purchase_id, supplier_id,
            (page.bill_number.get() or '').strip(),
            (page.purchase_date.get() or '').strip(),
            result,
            page.purchase_items,
        )
        return True

    try:
        run_on_ui_with_busy(
            page.parent,
            "Updating Purchase",
            _do_update,
            message="Updating purchase… please wait.",
        )
        showinfo(
            "Success",
            f"Purchase {bill_label} updated successfully!",
            parent=page.parent,
        )
        refresh_inventory_if_loaded(page.parent)
        page._editing_purchase_id = None
        page._edit_payment_snapshot = None
        close_fn()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        showerror("Error", f"Failed to update purchase: {e}")


def delete_purchase(conn, purchase_id, bill_label, refresh_callback):
    """Validate not sold + stock, permanently delete purchase + reverse stock."""
    if not askyesno(
        "Confirm Delete",
        f"Delete purchase {bill_label}?\n"
        "This permanently deletes the purchase and reduces stock. Cannot be undone."):
        return
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.desktop_purchase_service import delete_saved_purchase

            result = delete_saved_purchase(conn, {"purchase_id": purchase_id})
            if not result.get("ok"):
                showerror("Delete purchase", result.get("error") or "Failed")
                return
            if refresh_callback:
                refresh_callback()
            return
    except Exception as exc:
        showerror("Error", f"Failed to delete purchase: {exc}")
        return

    from core.purchase_service import _reverse_stock_for_purchase, recalculate_supplier_due
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT supplier_id FROM purchases WHERE id=? AND COALESCE(deleted,0)=0",
            (purchase_id,),
        )
        sup_row = cur.fetchone()
        if not sup_row:
            showwarning("Delete", "Purchase not found or already deleted.")
            return
        supplier_id = sup_row[0]

        # Refuse if any item from this purchase was sold — list the blocking sales.
        # Prefer denormalized sales.customer_name when present; else customers.name.
        cur.execute("PRAGMA table_info(sales)")
        sales_cols = {r[1] for r in cur.fetchall()}
        cust_expr = (
            "COALESCE(NULLIF(TRIM(s.customer_name), ''),"
            " (SELECT c.name FROM customers c WHERE c.id=s.customer_id),"
            " 'Customer')"
            if 'customer_name' in sales_cols
            else "COALESCE((SELECT c.name FROM customers c WHERE c.id=s.customer_id),"
                 " 'Customer')"
        )
        cur.execute(
            f"""
            SELECT s.bill_no, s.bill_date,
                   {cust_expr} AS cust,
                   COALESCE(m.name, 'item') AS med
            FROM purchase_items pi
            JOIN sales_items si ON si.medicine_id = pi.medicine_id
            JOIN sales s ON s.id = si.sale_id
                 AND COALESCE(s.deleted,0)=0
                 AND COALESCE(s.is_autosave,0)=0
            LEFT JOIN medicines m ON m.id = pi.medicine_id
            WHERE pi.purchase_id=?
            GROUP BY s.id, s.bill_no, s.bill_date, cust, med
            ORDER BY s.bill_date DESC, s.id DESC
            LIMIT 40
            """,
            (purchase_id,),
        )
        blockers = cur.fetchall()
        if blockers:
            from core.fy_serial import display_sales_bill_no

            lines = []
            for bill_no, bill_date, cust, med in blockers[:15]:
                lines.append(
                    f"• Sale {display_sales_bill_no(bill_no)} ({bill_date}) — {cust} — sold: {med}"
                )
            more = ""
            if len(blockers) > 15:
                more = f"\n…and {len(blockers) - 15} more"
            showwarning(
                "Cannot Delete",
                "Cannot delete this purchase — items from it were already sold.\n\n"
                "Sales that used these items:\n"
                + "\n".join(lines)
                + more,
            )
            return

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

        cur.execute("""
            SELECT DISTINCT pi.medicine_id
            FROM purchase_items pi
            WHERE pi.purchase_id=?
        """, (purchase_id,))
        medicine_ids = [int(r[0]) for r in cur.fetchall() if r[0]]

        _reverse_stock_for_purchase(cur, purchase_id)

        cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
        cur.execute("DELETE FROM purchases WHERE id=?", (purchase_id,))

        from core.medicine_visibility import hide_medicines_with_zero_stock
        hide_medicines_with_zero_stock(conn, medicine_ids)

        conn.commit()

        if supplier_id:
            recalculate_supplier_due(conn, supplier_id)

        from core.sync_coordinator import after_purchase_deleted
        after_purchase_deleted(conn, purchase_id, supplier_id, medicine_ids)

        showinfo("Success", "Purchase deleted successfully!")
        refresh_callback()
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to delete purchase: {e}")
