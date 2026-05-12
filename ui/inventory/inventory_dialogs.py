"""
ui/inventory_dialogs.py
────────────────────────
Edit medicine, view details, and delete medicine dialogs.
Called by InventoryPage — no tree/filter UI here.
"""
import tkinter as tk
from tkinter import messagebox

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.scroll_manager import open_dialog
from widgets.searchable_combo import SearchableCombo


def _expiry_to_display(expiry_date):
    if expiry_date and '-' in str(expiry_date):
        parts = str(expiry_date).split('-')
        if len(parts) >= 2:
            return f"{parts[1]}/{parts[0][2:]}"
    return expiry_date or ''


def _load_medicine_row(cursor, medicine_id):
    cursor.execute("""
        SELECT name, type, batch_no, expiry_date, stock_qty,
               unit, mrp, rate, manufacturer, schedule
        FROM medicines WHERE id=?
    """, (medicine_id,))
    return cursor.fetchone()


def open_edit_dialog(parent, conn, medicine_id, refresh_callback):
    cursor = conn.cursor()
    row = _load_medicine_row(cursor, medicine_id)
    if not row:
        return
    name, med_type, batch_no, expiry_date, stock_qty, unit, mrp, rate, manufacturer, schedule = row
    expiry_display = _expiry_to_display(expiry_date)
    db_values = [name, med_type or '', batch_no or '', expiry_display,
                 str(stock_qty or 0), unit or '', str(mrp or 0), str(rate or 0),
                 manufacturer or '', schedule or '']

    dlg = open_dialog(parent, "Edit Medicine", width=480, height=580, resizable=False)
    dlg.grid_columnconfigure(1, weight=1)

    from core.layout_config import load_layout, _DEFAULT_MED_TYPES, _DEFAULT_SCHEDULES
    layout = load_layout()
    med_types = layout.get('med_types', list(_DEFAULT_MED_TYPES))
    schedules = [s for s in layout.get('schedules', list(_DEFAULT_SCHEDULES)) if s]

    labels = ['Name','Type','Batch No','Expiry Date (MM/YY)','Stock Qty',
              'Unit','MRP','Rate','Manufacturer','Schedule']
    fields = {}
    for i, label in enumerate(labels):
        ttk.Label(dlg, text=f"{label}:").grid(row=i, column=0, sticky=tk.W, padx=12, pady=5)
        if label == 'Type':
            fields[label] = SearchableCombo(dlg, values=med_types, width=30)
        elif label == 'Schedule':
            fields[label] = SearchableCombo(dlg, values=schedules, width=30)
        else:
            fields[label] = ttk.Entry(dlg, width=34)
        fields[label].grid(row=i, column=1, padx=12, pady=5, sticky=tk.EW)
        v = db_values[i]
        if hasattr(fields[label], 'set'):
            fields[label].set(v)
        else:
            fields[label].delete(0, tk.END)
            fields[label].insert(0, v)

    def save():
        try:
            exp_input = fields['Expiry Date (MM/YY)'].get().strip()
            if '/' in exp_input:
                parts = exp_input.split('/')
                year = parts[1] if len(parts[1]) == 4 else '20' + parts[1]
                db_expiry = f"{year}-{parts[0]}-01"
            else:
                db_expiry = exp_input
            cursor.execute("""
                UPDATE medicines SET name=?,type=?,batch_no=?,expiry_date=?,stock_qty=?,
                unit=?,mrp=?,rate=?,manufacturer=?,schedule=? WHERE id=?
            """, (fields['Name'].get(), fields['Type'].get(), fields['Batch No'].get(),
                  db_expiry, int(fields['Stock Qty'].get() or 0), fields['Unit'].get(),
                  float(fields['MRP'].get() or 0), float(fields['Rate'].get() or 0),
                  fields['Manufacturer'].get(), fields['Schedule'].get(), medicine_id))
            conn.commit()
            messagebox.showinfo("Success", "Medicine updated successfully!")
            dlg.destroy()
            refresh_callback()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update medicine: {e}")

    plain = [fields[l] for l in labels if l not in ('Type','Schedule')]
    for idx, w in enumerate(plain):
        if idx < len(plain) - 1:
            nxt = plain[idx + 1]
            w.bind('<Return>', lambda e, n=nxt: n.focus())
            w.bind('<Down>',   lambda e, n=nxt: n.focus())
            if idx > 0:
                w.bind('<Up>', lambda e, p=plain[idx-1]: p.focus())
        else:
            w.bind('<Return>', lambda e: save())
    dlg.bind('<Escape>', lambda e: dlg.destroy())

    bf = ttk.Frame(dlg)
    bf.grid(row=len(labels), column=0, columnspan=2, pady=12)
    sb = ttk.Button(bf, text="Save Changes", command=save)
    sb.pack(side=tk.LEFT, padx=8)
    cb = ttk.Button(bf, text="Cancel", command=dlg.destroy)
    cb.pack(side=tk.LEFT, padx=8)
    sb.bind('<Return>', lambda e: save())
    cb.bind('<Return>', lambda e: dlg.destroy())
    fields[labels[0]].focus()


def open_view_dialog(parent, conn, medicine_id):
    cursor = conn.cursor()
    row = _load_medicine_row(cursor, medicine_id)
    if not row:
        return
    name, med_type, batch_no, expiry_date, stock_qty, unit, mrp, rate, manufacturer, schedule = row
    expiry_display = _expiry_to_display(expiry_date)
    db_values = [name, med_type or '', batch_no or '', expiry_display,
                 str(stock_qty or 0), unit or '', str(mrp or 0), str(rate or 0),
                 manufacturer or '', schedule or '']

    dlg = open_dialog(parent, f"Medicine Details - {name}", width=660, height=580, resizable=False)

    info_frame = ttk.LabelFrame(dlg, text="Medicine Information")
    info_frame.pack(fill=tk.X, padx=10, pady=5)
    info_labels = ['Name','Type','Batch No','Expiry Date','Current Stock',
                   'Unit','MRP','Rate','Manufacturer','Schedule']
    for i, label in enumerate(info_labels):
        ttk.Label(info_frame, text=f"{label}:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=i//2, column=(i%2)*2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(info_frame, text=db_values[i]).grid(
            row=i//2, column=(i%2)*2+1, sticky=tk.W, padx=5, pady=2)

    # Purchase history
    pf = ttk.LabelFrame(dlg, text="Purchase History")
    pf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    pt = ttk.Treeview(pf, columns=('Date','Supplier','Qty','Rate','Amount'),
                      show='headings', height=4, style='Large.Treeview')
    for col in ('Date','Supplier','Qty','Rate','Amount'):
        pt.heading(col, text=col); pt.column(col, width=100)
    pt.pack(fill=tk.BOTH, expand=True)
    cursor.execute("""
        SELECT p.purchase_date, s.name, pi.qty, pi.rate,
               COALESCE(pi.item_amount, pi.amount, 0)
        FROM purchase_items pi
        JOIN purchases p ON pi.purchase_id=p.id
        JOIN suppliers s ON p.supplier_id=s.id
        WHERE pi.medicine_id=? ORDER BY p.purchase_date DESC
    """, (medicine_id,))
    for r in cursor.fetchall():
        pt.insert('', tk.END, values=r)

    # Sales history
    sf = ttk.LabelFrame(dlg, text="Sales History")
    sf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    st = ttk.Treeview(sf, columns=('Date','Customer','Qty','Rate','Amount'),
                      show='headings', height=4, style='Large.Treeview')
    for col in ('Date','Customer','Qty','Rate','Amount'):
        st.heading(col, text=col); st.column(col, width=100)
    st.pack(fill=tk.BOTH, expand=True)
    cursor.execute("""
        SELECT s.bill_date, c.name, si.qty, si.rate, si.amount
        FROM sales_items si
        JOIN sales s ON si.sale_id=s.id
        JOIN customers c ON s.customer_id=c.id
        WHERE si.medicine_id=? ORDER BY s.bill_date DESC
    """, (medicine_id,))
    for r in cursor.fetchall():
        st.insert('', tk.END, values=r)


def delete_medicine(conn, medicine_id, medicine_name, batch_no, refresh_callback):
    if not messagebox.askyesno(
        "Confirm Delete",
        f"Delete {medicine_name} (Batch: {batch_no})?"):
        return
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM sales_items WHERE medicine_id=?", (medicine_id,))
        if cursor.fetchone()[0] > 0:
            messagebox.showwarning("Cannot Delete",
                                   "This medicine has sales history and cannot be deleted.")
            return
        cursor.execute("DELETE FROM purchase_items WHERE medicine_id=?", (medicine_id,))
        cursor.execute("DELETE FROM medicines WHERE id=?", (medicine_id,))
        conn.commit()
        messagebox.showinfo("Success", "Medicine deleted successfully!")
        refresh_callback()
    except Exception as e:
        conn.rollback()
        messagebox.showerror("Error", f"Failed to delete medicine: {e}")
