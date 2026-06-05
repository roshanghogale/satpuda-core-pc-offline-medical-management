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
from core.bill_config import (
    BillContext,
    BillItem,
    load_bill_print_settings,
    render_bill_html,
)


def show_bill_preview(parent, conn, bill_no, sale_id):
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, address, phone, email, gstin, dl_number,
               gst_enabled, created_at, COALESCE(logo_path, '') as logo_path,
               COALESCE(fssai_number, '') as fssai_number,
               COALESCE(show_fssai_on_bill, 0) as show_fssai_on_bill
        FROM pharmacy_profile LIMIT 1
    """)
    profile = cursor.fetchone()

    cursor.execute("""
        SELECT s.bill_no, s.bill_date, c.name, c.phone, c.address,
               s.total_amount, s.discount, s.amount_paid, s.previous_due,
               COALESCE(s.due_amount, 0), COALESCE(s.credit_amount, 0),
               COALESCE(s.cash_paid, 0), COALESCE(s.online_paid, 0),
               COALESCE(s.rounding, 0), COALESCE(s.doctor_name, '')
        FROM sales s
        JOIN customers c ON s.customer_id = c.id
        WHERE s.id = ?
    """, (sale_id,))
    bill_info = cursor.fetchone()

    cursor.execute("""
        SELECT m.name, m.hsn_code, m.batch_no, m.manufacturer,
               m.expiry_date, si.qty, si.rate, si.amount,
               COALESCE(m.gst_percent, 0), COALESCE(m.mrp, si.rate, 0)
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

    ctx = _build_bill_context(profile, bill_info, items, pay_mode, cursor)
    settings = load_bill_print_settings()
    html_path = _write_html_file(render_bill_html(ctx, settings))
    _show_preview_window(parent, html_path, bill_no, settings.get("template", "classic"))


def _logo_to_base64(logo_path):
    if not logo_path:
        return ''
    try:
        import base64
        if not os.path.exists(logo_path):
            return ''
        ext = os.path.splitext(logo_path)[1].lower().lstrip('.')
        mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png',
                'gif': 'gif', 'bmp': 'bmp', 'webp': 'webp'}.get(ext, 'png')
        with open(logo_path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('ascii')
        return f"data:image/{mime};base64,{data}"
    except Exception:
        return ''


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


def _build_bill_context(profile, bill_info, items, pay_mode, cursor):
    store_name = profile[1] if profile else "MEDICAL STORE"
    address = profile[2] if profile else ""
    phone = profile[3] if profile else ""
    email = profile[4] if profile else ""
    gstin = profile[5] if profile else ""
    dl_no = profile[6] if profile else ""
    gst_enabled = bool(profile[7]) if profile else False
    logo_path = profile[9] if (profile and len(profile) > 9) else ''
    fssai_number = (profile[10] or '').strip() if (profile and len(profile) > 10) else ''
    show_fssai_on_bill = bool(profile[11]) if (profile and len(profile) > 11) else False
    logo_src = _logo_to_base64(logo_path)

    bill_no = bill_info[0]
    bill_date = _fmt_date(bill_info[1])
    cust_name = bill_info[2] or ""
    cust_phone = bill_info[3] or ""
    cust_addr = bill_info[4] or ""
    grand_total = float(bill_info[5] or 0)
    discount = float(bill_info[6] or 0)
    amount_paid = float(bill_info[7] or 0)
    prev_due = float(bill_info[8] or 0)
    due_amt = float(bill_info[9] or 0)
    doctor_name = (bill_info[14] or "").strip()

    doctor_reg = ""
    if doctor_name:
        cursor.execute(
            "SELECT registration_number FROM doctors "
            "WHERE UPPER(name)=? LIMIT 1",
            (doctor_name.upper(),),
        )
        doc_row = cursor.fetchone()
        if doc_row:
            doctor_reg = (doc_row[0] or "").strip()

    bill_items = []
    gst_amount = 0.0
    sub_total = 0.0
    for it in items:
        amt = float(it[7] or 0)
        gst_pct = float(it[8] or 0)
        sub_total += amt
        if gst_enabled and gst_pct > 0:
            gst_amount += round(amt * gst_pct / (100 + gst_pct), 2)
        mrp = float(it[9] or it[6] or 0)
        bill_items.append(BillItem(
            name=it[0] or "",
            batch=it[2] or "",
            expiry=it[4] or "",
            qty=float(it[5] or 0),
            rate=float(it[6] or 0),
            mrp=mrp,
            amount=amt,
            gst_percent=gst_pct,
            manufacturer=it[3] or "",
        ))

    gst_amount = round(gst_amount, 2)
    sub_total = round(sub_total, 2)
    taxable = round(max(0, grand_total - gst_amount), 2) if gst_enabled else round(
        max(0, sub_total - discount), 2
    )

    settings = load_bill_print_settings()
    return BillContext(
        store_name=(store_name or "").upper(),
        address=address or "",
        email=email or "",
        phone=phone or "",
        gstin=gstin or "",
        dl_no=dl_no or "",
        fssai=fssai_number,
        show_fssai_on_bill=show_fssai_on_bill,
        logo_src=logo_src,
        bill_no=bill_no,
        bill_date=bill_date,
        bill_date_landscape=bill_date,
        cust_name=cust_name,
        cust_phone=cust_phone,
        cust_addr=cust_addr,
        pay_mode=pay_mode,
        doctor_name=doctor_name,
        doctor_reg=doctor_reg,
        items=bill_items,
        sub_total=sub_total,
        discount=discount,
        taxable_amount=taxable,
        gst_amount=gst_amount,
        grand_total=grand_total,
        rounding=float(bill_info[13] or 0),
        amount_paid=amount_paid,
        gst_enabled=gst_enabled,
        blessing_line=settings.get("blessing_line", "SHREE GANESHAY NAMAH"),
        previous_due=prev_due,
        due_amount=due_amt,
    )


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
    try:
        from core.scroll_manager import ensure_toplevel_fits_screen
        win.after(1, lambda: ensure_toplevel_fits_screen(win, width=560, height=380, resizable=True))
    except Exception:
        pass
