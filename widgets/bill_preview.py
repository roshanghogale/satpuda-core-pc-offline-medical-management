import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
import tempfile
import os
import threading
from core.font_config import *
from core.bill_context import _build_bill_context, _logo_to_base64  # noqa: F401
from core.bill_config import (
    BillContext,
    BillItem,
    apply_a5_portrait_bill_layout,
    load_bill_print_settings,
    render_bill_html,
)


def show_bill_preview(parent, conn, bill_no, sale_id):
    from core.background_workers import run_with_progress

    def _worker(put):
        put("Loading bill data…")
        cursor = conn.cursor()
        from core.pharmacy_profile_io import fetch_pharmacy_profile_row

        profile = fetch_pharmacy_profile_row(conn)
        cursor.execute("""
            SELECT s.bill_no, s.bill_date, c.name, c.phone, c.address,
                   s.total_amount, s.discount, s.amount_paid, s.previous_due,
                   COALESCE(s.due_amount, 0), COALESCE(s.credit_amount, 0),
                   COALESCE(s.cash_paid, 0), COALESCE(s.online_paid, 0),
                   COALESCE(s.rounding, 0), COALESCE(s.doctor_name, ''),
                   COALESCE(s.total_due, 0), COALESCE(s.previous_credit, 0)
            FROM sales s
            JOIN customers c ON s.customer_id = c.id
            WHERE s.id = ?
        """, (sale_id,))
        bill_info = cursor.fetchone()
        # The GST % the line was sold at, as bill_output._load_sale_data prints it.
        cursor.execute("""
            SELECT m.name, m.hsn_code, m.batch_no, m.manufacturer,
                   m.expiry_date, si.qty, si.rate, si.amount,
                   COALESCE(si.gst_percent, m.gst_percent, 0), COALESCE(m.mrp, si.rate, 0)
            FROM sales_items si
            JOIN medicines m ON si.medicine_id = m.id
            WHERE si.sale_id = ?
        """, (sale_id,))
        items = cursor.fetchall()
        cash = float(bill_info[11] or 0)
        online = float(bill_info[12] or 0)
        if cash == 0 and online == 0:
            pay_mode = "Due"
        elif cash > 0 and online == 0:
            pay_mode = "Cash"
        elif cash == 0 and online > 0:
            pay_mode = "Online"
        else:
            pay_mode = "Cash + Online"
        put("Rendering bill…")
        ctx = _build_bill_context(profile, bill_info, items, pay_mode, cursor)
        settings = apply_a5_portrait_bill_layout(load_bill_print_settings())
        html_path = _write_html_file(render_bill_html(ctx, settings))
        return html_path, bill_no, settings.get("template", "classic")

    def _done(result):
        html_path, bill_no, template = result
        _show_preview_window(parent, html_path, bill_no, template)

    run_with_progress(
        parent,
        "Bill Preview",
        _worker,
        on_complete=_done,
        on_error=lambda exc: messagebox.showerror("Preview Error", str(exc), parent=parent),
    )


def _fmt_date(raw):
    if not raw:
        return ""
    try:
        parts = str(raw).split('-')
        if len(parts) == 3:
            return f"{parts[2]}/{parts[1]}/{parts[0][2:]}"
    except Exception:
        pass
    return str(raw)


def _write_html_file(html: str) -> str:
    tmp = tempfile.NamedTemporaryFile(
        suffix='.html', delete=False, mode='w', encoding='utf-8')
    tmp.write(html)
    tmp.close()
    return tmp.name


def _show_preview_window(parent, html_path, bill_no, template="classic"):
    win = tk.Toplevel(parent)
    win.title(f"Bill — {bill_no}")
    win.geometry("560x380")
    win.minsize(480, 340)
    win.resizable(True, True)

    try:
        from core.scroll_manager import _apply_icon
        _apply_icon(win)
    except Exception:
        pass

    if html_path is None:
        ttk.Label(win, text="Could not generate bill.",
                  font=(FONT_FAMILY, 11)).pack(expand=True)
        return

    # Buttons at bottom first so they are never clipped by long instructions.
    bf = ttk.Frame(win)
    bf.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

    if template == "legacy":
        hint = (
            f"Bill {bill_no} is ready.\n\n"
            f"Click Print Bill below — your browser will open.\n"
            f"Use the blue Print Bill bar at the top of the page, then in the dialog:\n"
            f"A4 · Landscape · minimum margins · 100% scale.\n\n"
            f"Left = Customer Copy · Right = Store Copy"
        )
    else:
        hint = (
            f"Bill {bill_no} is ready.\n\n"
            f"Click Print Bill below — your browser will open.\n"
            f"Use the blue Print Bill bar at the top of the page, then in the dialog:\n"
            f"A5 · Landscape · minimum margins · 100% scale.\n\n"
            f"Two bills print on one A5 sheet (cut in the middle)."
        )

    body = ttk.Frame(win)
    body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=8)
    ttk.Label(body, text=hint, font=(FONT_FAMILY, 10), justify='center',
              wraplength=500).pack(expand=True)

    def open_and_print():
        import webbrowser
        webbrowser.open('file:///' + html_path.replace('\\', '/'))

    def save_html():
        from tkinter import filedialog
        import shutil
        dest = filedialog.asksaveasfilename(
            defaultextension='.html',
            filetypes=[('HTML files', '*.html')],
            initialfile=f"Bill_{bill_no}.html"
        )
        if dest:
            shutil.copy2(html_path, dest)
            messagebox.showinfo("Saved", f"Saved to:\n{dest}")

    for text, cmd, style in [
        ("🖨️ Print Bill", open_and_print, "primary"),
        ("Save HTML", save_html, "success"),
    ]:
        try:
            ttk.Button(bf, text=text, command=cmd,
                       bootstyle=style).pack(side=tk.LEFT, padx=5)
        except Exception:
            ttk.Button(bf, text=text, command=cmd).pack(side=tk.LEFT, padx=5)

    try:
        ttk.Button(bf, text="Close", command=win.destroy,
                   bootstyle="secondary").pack(side=tk.RIGHT, padx=5)
    except Exception:
        ttk.Button(bf, text="Close", command=win.destroy).pack(side=tk.RIGHT, padx=5)

    def on_close():
        win.destroy()
        threading.Timer(30.0, lambda: os.unlink(html_path)
                        if os.path.exists(html_path) else None).start()

    win.protocol("WM_DELETE_WINDOW", on_close)
    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(win, on_close=on_close)
    try:
        from core.scroll_manager import ensure_toplevel_fits_screen
        win.after(1, lambda: ensure_toplevel_fits_screen(win, width=560, height=380, resizable=True))
    except Exception:
        pass
