"""
ui/sales_history_actions.py
────────────────────────────
View bill details, edit bill, print bill, delete bill.
Called by SalesHistoryPage — no filter/tree UI here.
"""
import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.scroll_manager import open_dialog


def view_bill_details(parent, conn, sale_id, tree_values, sales_data):
    cursor = conn.cursor()
    dlg = open_dialog(parent, f"Bill Details - {tree_values[0]} ({tree_values[1]})",
                      width=880, height=660, resizable=False)
    body = dlg.content

    # Header
    hf = ttk.LabelFrame(body, text="Bill Information")
    hf.pack(fill=tk.X, padx=10, pady=5)

    sale_row = next((s for s in sales_data if s[15] == sale_id), None)
    doctor   = (sale_row[16] or 'N/A') if sale_row else 'N/A'
    info_data   = [tree_values[0], tree_values[1], tree_values[2], tree_values[3], doctor]
    info_labels = ['Bill No','Date','Customer','Phone','Doctor']
    for i, (lbl, val) in enumerate(zip(info_labels, info_data)):
        ttk.Label(hf, text=f"{lbl}:", font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=i//3, column=(i%3)*2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(hf, text=str(val)).grid(
            row=i//3, column=(i%3)*2+1, sticky=tk.W, padx=5, pady=2)

    # Items
    items_frame = ttk.LabelFrame(body, text="Bill Items")
    items_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    cols = ('Medicine','Batch','Type','Qty','Rate','GST%','Amount')
    it = ttk.Treeview(items_frame, columns=cols, show='headings', height=4, style='Large.Treeview')
    for col in cols:
        it.heading(col, text=col); it.column(col, width=120)
    it.pack(fill=tk.BOTH, expand=True)
    cursor.execute("""
        SELECT m.name, m.batch_no, COALESCE(m.type,'N/A'),
               si.qty, si.rate, si.gst_percent, si.amount
        FROM sales_items si JOIN medicines m ON si.medicine_id=m.id
        WHERE si.sale_id=?
    """, (sale_id,))
    for row in cursor.fetchall():
        it.insert('', tk.END, values=row)

    # Summary
    sf = ttk.LabelFrame(body, text="Bill Summary")
    sf.pack(fill=tk.X, padx=10, pady=5)
    cash_paid = online_paid = 0
    if sale_row:
        cash_paid, online_paid = sale_row[6], sale_row[7]
    summary_labels = ['Total Amount','Cash Paid','Online Paid','Amount Paid',
                      'Previous Due','Due Amount','Credit Amount','Total Due']
    summary_values = [tree_values[4], cash_paid, online_paid, tree_values[5],
                      tree_values[8], tree_values[9], tree_values[10], tree_values[11]]
    for i, (lbl, val) in enumerate(zip(summary_labels, summary_values)):
        ttk.Label(sf, text=f"{lbl}:", font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=i//3, column=(i%3)*2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(sf, text=f"₹{val}").grid(
            row=i//3, column=(i%3)*2+1, sticky=tk.W, padx=5, pady=2)

    ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.RIGHT, padx=6)


def edit_bill(parent, conn, sale_id, tree_values, refresh_callback):
    from widgets.bill_edit import BillEditPage
    edit_window = open_dialog(
        parent, f"Edit Bill - {tree_values[0]} - {tree_values[2]}",
        width=1200, height=800, resizable=False)
    BillEditPage(edit_window.content, conn, sale_id, refresh_callback)
    edit_window.protocol("WM_DELETE_WINDOW",
                         lambda: [edit_window.destroy(), refresh_callback()])


def print_bill(parent, conn, sale_id, tree_values):
    bill_no = tree_values[0] if tree_values else str(sale_id)
    from widgets.bill_preview import show_bill_preview
    show_bill_preview(parent, conn, bill_no, sale_id)


def delete_bill(conn, sale_id, tree_values, refresh_callback):
    if not askyesno(
        "Confirm Delete",
        f"Delete bill {tree_values[0]} for {tree_values[2]} on {tree_values[1]}?\n"
        "This action cannot be undone."):
        return
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT customer_id FROM sales WHERE id=?", (sale_id,))
        row = cursor.fetchone()
        customer_id = row[0] if row else None

        cursor.execute("SELECT medicine_id, qty FROM sales_items WHERE sale_id=?", (sale_id,))
        for med_id, qty in cursor.fetchall():
            restore_qty = abs(float(qty or 0))
            cursor.execute(
                "UPDATE medicines SET stock_qty=stock_qty+? WHERE id=?", (restore_qty, med_id))

        cursor.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
        cursor.execute("DELETE FROM sales WHERE id=?", (sale_id,))
        conn.commit()

        if customer_id:
            from core.customer_service import recalculate_customer_due
            recalculate_customer_due(conn, customer_id)

        showinfo("Success", "Bill deleted successfully!")
        refresh_callback()
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to delete bill: {e}")
