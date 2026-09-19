"""
ui/sales_history_actions.py
────────────────────────────
View bill details, edit bill, print bill, delete bill.
Called by SalesHistoryPage — no filter/tree UI here.
"""
import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno

PRINT_ALL_SLOT = 2  # Print All uses print slot 2 preset


def _chunk_sale_ids_for_print(sale_ids, paper: str):
    from core.bill_config import print_all_bills_per_page
    per = print_all_bills_per_page(paper)
    ids = [int(x) for x in sale_ids]
    return [ids[i : i + per] for i in range(0, len(ids), per)]


def _print_bills_batch(conn, sale_ids, *, paper, slot, hwnd_owner=0, settings_override=None):
    import os
    import tempfile
    import time
    from core.bill_config import apply_print_bill_layout, get_print_slot_settings, load_bill_print_settings
    from core.bill_output import _load_sale_data, _try_pdf_via_browser, print_bill_with_slot
    from core.printer_manager import PrinterManager

    if not sale_ids:
        return 0, []

    paper = (paper or "A6").upper()
    base = dict(load_bill_print_settings())
    if settings_override:
        base.update(settings_override)
    slot_settings = get_print_slot_settings(base, slot)
    slot_settings["paper_size"] = paper
    slot_settings["bill_copies"] = 1
    render_settings = apply_print_bill_layout(slot_settings, print_slot_copies=1)
    render_settings["paper_size"] = paper
    printer = PrinterManager.get_printer_for_slot(slot)
    failures = []
    pages = 0

    if paper == "A6":
        for sale_id in sale_ids:
            try:
                print_bill_with_slot(
                    conn, sale_id, slot, hwnd_owner=hwnd_owner,
                    settings_override=settings_override,
                )
                pages += 1
            except Exception as exc:
                failures.append(f"Sale {sale_id}: {exc}")
            time.sleep(0.35)
        return pages, failures

    from bill_templates.classic import render_classic_bill_html_multi
    for chunk in _chunk_sale_ids_for_print(sale_ids, paper):
        try:
            contexts = []
            for sale_id in chunk:
                _, ctx, _ = _load_sale_data(conn, sale_id, settings_override=render_settings)
                contexts.append(ctx)
            html = render_classic_bill_html_multi(contexts, render_settings)
            fd, html_path = tempfile.mkstemp(suffix=".html", prefix="batch_bills_")
            os.close(fd)
            pdf_path = html_path.replace(".html", ".pdf")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            if not _try_pdf_via_browser(html_path, pdf_path):
                raise RuntimeError("Could not create PDF for batch print.")
            PrinterManager.print_pdf_silently(pdf_path, printer, copies=1)
            pages += 1
        except Exception as exc:
            label = ", ".join(str(x) for x in chunk[:4])
            failures.append(f"Bills [{label}]: {exc}")
        time.sleep(0.35)
    return pages, failures

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.scroll_manager import DIALOG_SIZE_XLARGE, dialog_section, open_dialog


def view_bill_details(parent, conn, sale_id, tree_values, sales_data):
    cursor = conn.cursor()
    w, h = DIALOG_SIZE_XLARGE
    dlg = open_dialog(parent, f"Bill Details - {tree_values[0]} ({tree_values[1]})",
                      width=w, height=h, resizable=True)
    body = dlg.content

    # Header
    hf, hf_inner = dialog_section(body, "Bill Information")
    hf.pack(fill=tk.X, pady=5)

    sale_row = next((s for s in sales_data if s[15] == sale_id), None)
    doctor   = (sale_row[16] or 'N/A') if sale_row else 'N/A'
    info_data   = [tree_values[0], tree_values[1], tree_values[2], tree_values[3], doctor]
    info_labels = ['Bill No','Date','Customer','Phone','Doctor']
    for i, (lbl, val) in enumerate(zip(info_labels, info_data)):
        ttk.Label(hf_inner, text=f"{lbl}:", font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=i//3, column=(i%3)*2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(hf_inner, text=str(val)).grid(
            row=i//3, column=(i%3)*2+1, sticky=tk.W, padx=5, pady=2)

    # Items
    items_frame, items_inner = dialog_section(body, "Bill Items")
    items_frame.pack(fill=tk.BOTH, expand=True, pady=5)
    cols = ('Medicine','Batch','Type','Qty','Rate','GST%','Amount')
    it = ttk.Treeview(items_inner, columns=cols, show='headings', height=8, style='Large.Treeview')
    for col in cols:
        it.heading(col, text=col); it.column(col, width=120)
    it.pack(fill=tk.BOTH, expand=True)
    item_rows = []
    try:
        from core.sync_prefs import is_online_mode
        from core.billing_service import load_sale_medicines

        if is_online_mode():
            for m in load_sale_medicines(conn, sale_id) or []:
                item_rows.append(
                    (
                        m.get("name") or "",
                        m.get("batch") or "",
                        m.get("type") or m.get("display_type") or "N/A",
                        m.get("qty"),
                        m.get("rate"),
                        m.get("gst_percent"),
                        m.get("amount"),
                    )
                )
    except Exception as exc:
        print(f"[sales history] online bill items: {exc}")
    if not item_rows:
        cursor.execute(
            """
            SELECT COALESCE(m.name, ''), COALESCE(m.batch_no, ''),
                   COALESCE(m.type, 'N/A'),
                   si.qty, si.rate, si.gst_percent, si.amount
            FROM sales_items si
            LEFT JOIN medicines m ON si.medicine_id = m.id
            WHERE si.sale_id=?
            """,
            (sale_id,),
        )
        item_rows = list(cursor.fetchall())
    for row in item_rows:
        it.insert('', tk.END, values=row)

    # Summary
    sf = ttk.LabelFrame(body, text="Bill Summary")
    sf.pack(fill=tk.X, padx=10, pady=5)
    cash_paid = online_paid = 0
    if sale_row:
        cash_paid, online_paid = sale_row[6], sale_row[7]
    summary_labels = ['Total Amount','Discount','Cash Paid','Online Paid','Amount Paid',
                      'Previous Due','Due Amount','Credit Amount','Total Due']
    summary_values = [tree_values[6], tree_values[7], cash_paid, online_paid, tree_values[8],
                      tree_values[11], tree_values[12], tree_values[13], tree_values[14]]
    for i, (lbl, val) in enumerate(zip(summary_labels, summary_values)):
        ttk.Label(sf, text=f"{lbl}:", font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=i//3, column=(i%3)*2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(sf, text=f"₹{val}").grid(
            row=i//3, column=(i%3)*2+1, sticky=tk.W, padx=5, pady=2)

    ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.RIGHT, padx=6)


def edit_bill(parent, conn, sale_id, tree_values, refresh_callback):
    from ui.sales.sales_history_edit import open_edit_window
    bill_label = tree_values[0] if tree_values else str(sale_id)
    open_edit_window(parent, conn, sale_id, bill_label, refresh_callback)


def print_bill(parent, conn, sale_id, tree_values):
    bill_no = tree_values[0] if tree_values else str(sale_id)
    from widgets.bill_print_dialog import show_bill_print_dialog
    show_bill_print_dialog(parent, conn, bill_no, sale_id)


def save_bill_pdf_a6(parent, conn, sale_id, tree_values):
    """Save A6 landscape PDF to the configured sales bill folder."""
    from core.background_workers import run_with_progress
    from core.bill_output import save_bill_pdf_a6 as _save_bill_pdf_a6

    bill_no = tree_values[0] if tree_values else str(sale_id)

    def _worker(put):
        put("Generating A6 PDF…")
        _, pdf_path = _save_bill_pdf_a6(conn, sale_id)
        if not pdf_path:
            raise RuntimeError(
                'Bill HTML was saved, but PDF could not be created.\n'
                'Install Microsoft Edge (or Google Chrome) and try again.'
            )
        return pdf_path

    def _done(pdf_path):
        from core.document_output import offer_open_saved_file
        offer_open_saved_file(parent, pdf_path, title="Save PDF")

    run_with_progress(
        parent,
        "Saving PDF",
        _worker,
        on_complete=_done,
        on_error=lambda exc: showerror(
            "Save PDF", f"Could not save bill PDF:\n{exc}", parent=parent,
        ),
    )


def print_bill_slot_silent(parent, conn, sale_id, tree_values, slot: int):
    """Print a saved sale silently using Print Sales 1/2 presets."""
    import threading
    from core.bill_output import print_bill_with_slot
    from core.themed_messagebox import showinfo

    bill_no = tree_values[0] if tree_values else str(sale_id)
    try:
        hwnd = int(parent.winfo_toplevel().winfo_id())
    except Exception:
        hwnd = 0

    def _work():
        try:
            print_bill_with_slot(conn, sale_id, slot, hwnd_owner=hwnd)
            parent.after(
                0,
                lambda: showinfo(
                    "Print Bill",
                    f"Bill {bill_no} sent to printer.",
                    parent=parent.winfo_toplevel(),
                ),
            )
        except Exception as exc:
            parent.after(
                0,
                lambda e=str(exc): showerror(
                    "Print Bill",
                    f"Could not print bill {bill_no}:\n{e}",
                    parent=parent.winfo_toplevel(),
                ),
            )

    threading.Thread(
        target=_work,
        daemon=True,
        name=f"HistoryPrint{slot}",
    ).start()


def print_all_bills_sequential(parent, conn, items, slot: int = PRINT_ALL_SLOT, *, paper: str = "A6"):
    """Print bills using page-size batching (A4=4/page, A5=2/page, A6=1/page)."""
    import threading
    from core.themed_messagebox import showinfo, showwarning

    if not items:
        showwarning("Print All", "No bills to print.", parent=parent.winfo_toplevel())
        return

    try:
        hwnd = int(parent.winfo_toplevel().winfo_id())
    except Exception:
        hwnd = 0

    sale_ids = [int(sale_id) for sale_id, _ in items]
    total = len(sale_ids)
    paper_u = (paper or "A6").upper()

    force_slot_override = None
    if slot == 2 and paper_u == "A6":
        try:
            from core.bill_config import load_bill_print_settings
            base = load_bill_print_settings()
            current_slot = base.get("print_slot_2") if isinstance(base.get("print_slot_2"), dict) else {}
            a6_half = str(
                current_slot.get("a6_source_half")
                or base.get("a6_source_half")
                or "bottom"
            ).strip().lower()
        except Exception:
            a6_half = "bottom"
        force_slot_override = {
            "print_slot_2": {
                "paper_size": "A6",
                "copies": 1,
                "bill_size_mode": "dot_matrix",
                "a6_source_half": "top" if a6_half == "top" else "bottom",
                "label": "Print Sales 2",
            }
        }

    def _work():
        try:
            pages, failed = _print_bills_batch(
                conn,
                sale_ids,
                paper=paper_u,
                slot=slot,
                hwnd_owner=hwnd,
                settings_override=force_slot_override,
            )
        except Exception as exc:
            parent.after(
                0,
                lambda: showerror(
                    "Print All",
                    f"Could not print bills:\n{exc}",
                    parent=parent.winfo_toplevel(),
                ),
            )
            return

        def _done():
            top = parent.winfo_toplevel()
            if failed:
                showerror(
                    "Print All",
                    f"Printed {pages} page(s) for {total} bills.\n\nFailed:\n" + "\n".join(failed[:8]),
                    parent=top,
                )
            else:
                showinfo(
                    "Print All",
                    f"All {total} bills sent to printer ({pages} page(s) on {paper_u}).",
                    parent=top,
                )

        parent.after(0, _done)

    threading.Thread(
        target=_work,
        daemon=True,
        name=f"HistoryPrintAll{slot}",
    ).start()


def delete_bill(conn, sale_id, tree_values, refresh_callback):
    if not askyesno(
        "Confirm Delete",
        f"Delete bill {tree_values[0]} for {tree_values[2]} on {tree_values[1]}?\n"
        "Stock will be restored. This permanently deletes the bill."):
        return
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.desktop_sales_service import delete_saved_sale

            result = delete_saved_sale(conn, {"sale_id": sale_id})
            if not result.get("ok"):
                showerror("Delete bill", result.get("error") or "Failed")
                return
            if refresh_callback:
                refresh_callback()
            return
    except Exception as exc:
        showerror("Delete bill", str(exc))
        return

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT customer_id FROM sales WHERE id=? AND COALESCE(deleted,0)=0",
            (sale_id,),
        )
        row = cursor.fetchone()
        if not row:
            showwarning("Delete", "Bill not found or already deleted.")
            return
        customer_id = row[0]

        cursor.execute("SELECT medicine_id, qty FROM sales_items WHERE sale_id=?", (sale_id,))
        sale_lines = cursor.fetchall()
        medicine_ids = [int(med_id) for med_id, _ in sale_lines if med_id]
        for med_id, qty in sale_lines:
            restore_qty = abs(float(qty or 0))
            cursor.execute(
                "UPDATE medicines SET stock_qty=stock_qty+?, is_hidden=0 WHERE id=?",
                (restore_qty, med_id),
            )

        # Permanent delete
        cursor.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
        cursor.execute("DELETE FROM sales WHERE id=?", (sale_id,))
        conn.commit()

        if customer_id:
            from core.customer_service import recalculate_customer_due
            recalculate_customer_due(conn, customer_id)

        from core.sync_coordinator import after_sale_deleted
        after_sale_deleted(conn, sale_id, customer_id, medicine_ids)

        showinfo("Success", "Bill deleted successfully!")
        refresh_callback()
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to delete bill: {e}")
