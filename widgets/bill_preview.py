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


def show_bill_preview(parent, conn, bill_no, sale_id):
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, address, phone, email, gstin, dl_number,
               gst_enabled, created_at, COALESCE(logo_path, '') as logo_path
        FROM pharmacy_profile LIMIT 1
    """)
    profile = cursor.fetchone()

    cursor.execute("""
        SELECT s.bill_no, s.bill_date, c.name, c.phone, c.address,
               s.total_amount, s.discount, s.amount_paid, s.previous_due,
               COALESCE(s.due_amount, 0), COALESCE(s.credit_amount, 0),
               COALESCE(s.cash_paid, 0), COALESCE(s.online_paid, 0),
               COALESCE(s.rounding, 0)
        FROM sales s
        JOIN customers c ON s.customer_id = c.id
        WHERE s.id = ?
    """, (sale_id,))
    bill_info = cursor.fetchone()

    cursor.execute("""
        SELECT m.name, m.hsn_code, m.batch_no, m.manufacturer,
               m.expiry_date, si.qty, si.rate, si.amount,
               COALESCE(m.gst_percent, 0)
        FROM sales_items si
        JOIN medicines m ON si.medicine_id = m.id
        WHERE si.sale_id = ?
    """, (sale_id,))
    items = cursor.fetchall()

    cash     = float(bill_info[11] or 0)
    online   = float(bill_info[12] or 0)
    if cash == 0 and online == 0:
        pay_mode = "Due"
    elif cash > 0 and online == 0:
        pay_mode = "Cash"
    elif cash == 0 and online > 0:
        pay_mode = "Online"
    else:
        pay_mode = "Cash + Online"

    html_path = _generate_html(profile, bill_info, items, pay_mode)
    _show_preview_window(parent, html_path, bill_no)


def _logo_to_base64(logo_path):
    """Convert logo image file to a base64 data URI for embedding in HTML."""
    if not logo_path:
        return ''
    try:
        import base64, os
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


def _esc(text):
    """Escape HTML special characters."""
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def _generate_html(profile, bill_info, items, pay_mode):
    # ── Profile ────────────────────────────────────────────────────
    store_name = profile[1] if profile else "MEDICAL STORE"
    address    = profile[2] if profile else ""
    phone      = profile[3] if profile else ""
    email      = profile[4] if profile else ""
    gstin      = profile[5] if profile else ""
    dl_no      = profile[6] if profile else ""
    logo_path  = profile[9] if (profile and len(profile) > 9) else ''
    logo_src   = _logo_to_base64(logo_path)

    # ── Bill data ──────────────────────────────────────────────────
    bill_no    = bill_info[0]
    bill_date  = _fmt_date(bill_info[1])
    cust_name  = bill_info[2] or ""
    cust_phone = bill_info[3] or ""
    cust_addr  = bill_info[4] or ""
    gross      = float(bill_info[5] or 0)
    prev_due   = float(bill_info[8] or 0)
    due_amt    = float(bill_info[9] or 0)
    sub_total  = sum(float(it[7] or 0) for it in items)
    grand_total = gross

    # GST — only when gst_enabled=1 in pharmacy profile
    gst_enabled = bool(profile[7]) if profile else False
    gst_amount = 0.0
    if gst_enabled:
        for it in items:
            amt = float(it[7] or 0)
            gst_pct = float(it[8] or 0)
            if gst_pct > 0:
                gst_amount += round(amt * gst_pct / (100 + gst_pct), 2)
        gst_amount = round(gst_amount, 2)

    amount_paid = float(bill_info[7] or 0)
    total_outstanding = round(prev_due + due_amt, 2)
    if total_outstanding > 0:
        due_line = f"Due as per Date : &nbsp;&#8377;{total_outstanding:.2f}"
    else:
        due_line = "Due as per Date : &nbsp;Nil"

    contact_parts = []
    if phone: contact_parts.append(f"Ph: {_esc(phone)}")
    if email: contact_parts.append(f"Email: {_esc(email)}")
    contact_line = "  |  ".join(contact_parts)

    # ── Product rows ───────────────────────────────────────────────
    item_rows_html = ""
    for i, it in enumerate(items):
        name   = _esc(it[0] or "")
        batch  = _esc(it[2] or "")
        mfg    = _esc(it[3] or "")
        exp    = _fmt_date(it[4])
        qty    = int(it[5]) if it[5] else 0
        rate   = float(it[6] or 0)
        amount = float(it[7] or 0)
        item_rows_html += f"""
            <tr>
                <td class="c">{i+1}</td>
                <td class="l">{name}</td>
                <td class="c">{batch}</td>
                <td class="c">{mfg}</td>
                <td class="c">{exp}</td>
                <td class="r">&#8377;{rate:.2f}</td>
                <td class="c">{qty}</td>
                <td class="r">&#8377;{amount:.2f}</td>
            </tr>"""
    item_rows_html += '<tr class="spacer-row"><td colspan="8"></td></tr>'

    # ── Build one bill block (label = Customer Copy / Store Copy) ──
    def bill_block(label):
        return f"""
<div class="bill">
  <div class="copy-label">{label}</div>
  <div class="tax-invoice">TAX INVOICE &nbsp;&mdash;&nbsp; Bill No : {_esc(bill_no)}</div>

  <div class="info-row">
    <div class="info-left">
      <div>Name &nbsp;&nbsp;&nbsp;&nbsp;: &nbsp;{_esc(cust_name)}</div>
      <div>Contact &nbsp;: &nbsp;{_esc(cust_phone)}</div>
      {"<div>Address : &nbsp;" + _esc(cust_addr) + "</div>" if cust_addr else ""}
      <div>Date &nbsp;&nbsp;&nbsp;&nbsp;: &nbsp;{bill_date}</div>
      <div>Payment &nbsp;: &nbsp;{_esc(pay_mode)}</div>
    </div>
    <div class="info-center">
      {f'<img src="{logo_src}" style="max-height:16mm;max-width:26mm;object-fit:contain;" alt="logo">' if logo_src else ''}
    </div>
    <div class="info-right">
      <div class="store-name">{_esc(store_name)}</div>
      {"<div>" + _esc(address) + "</div>" if address else ""}
      {"<div>" + contact_line + "</div>" if contact_line else ""}
      {"<div>GSTIN: " + _esc(gstin) + "</div>" if gstin else ""}
      {"<div>DL No: " + _esc(dl_no) + "</div>" if dl_no else ""}
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th class="c" style="width:5%">Sr</th>
        <th class="l" style="width:28%">Item Name</th>
        <th class="c" style="width:10%">Batch No</th>
        <th class="c" style="width:12%">Mfg</th>
        <th class="c" style="width:10%">Exp</th>
        <th class="r" style="width:12%">Rate</th>
        <th class="c" style="width:7%">Qty</th>
        <th class="r" style="width:16%">Amount</th>
      </tr>
    </thead>
    <tbody>
      {item_rows_html}
    </tbody>
  </table>

  <div class="totals-row">
    <table class="totals-table">
      <tr>
        <td>Sub Total</td>
        <td class="r">&#8377;{sub_total:.2f}</td>
        <td style="width:6mm"></td>
        <td>Total Amount</td>
        <td class="r">&#8377;{grand_total:.2f}</td>
      </tr>
      <tr>
        <td>Discount</td>
        <td class="r">&#8377;{float(bill_info[6] or 0):.2f}</td>
        <td></td>
        <td>Amount Paid</td>
        <td class="r">&#8377;{amount_paid:.2f}</td>
      </tr>
    </table>
  </div>

  <div class="due-row">
    <span>{due_line}</span>
    {f'<span>GST (Incl.) : &#8377;{gst_amount:.2f}</span>' if gst_enabled else ''}
  </div>

  <div class="sig-row">
    <div>Customer Signature : ___________</div>
    <div>Authorised Signatory</div>
  </div>

  <div class="footer-row">
    <div>All Subject to Sangrampur Jurisdiction</div>
    <div>सातपुडा मेडिकल</div>
  </div>
</div>"""

    customer_block = bill_block("Customer Copy")
    store_block    = bill_block("Store Copy")

    html = f"""<!DOCTYPE html>
<html lang="mr">
<head>
<meta charset="UTF-8">
<title>Bill - {_esc(bill_no)}</title>
<style>
  @page {{
    size: A4 landscape;
    margin: 0;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: 'Nirmala UI', 'Mangal', Arial, sans-serif;
    font-size: 7.5pt;
    color: #000;
    width: 297mm;
    height: 210mm;
    display: flex;
    flex-direction: row;
    overflow: hidden;
    padding: 6px;
  }}

  /* Each bill takes exactly half the A4 landscape width */
  .bill {{
    width: 144mm;
    height: 100%;
    padding: 3mm 4mm;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    flex-shrink: 0;
  }}

  /* Dashed cut line between the two copies */
  .cut-line {{
    width: 12px;
    border-left: 1pt dashed #888;
    height: 210mm;
    flex-shrink: 0;
    margin: 0 6px;
  }}

  .copy-label {{
    text-align: center;
    font-size: 6.5pt;
    color: #555;
    margin-bottom: 0.5mm;
    letter-spacing: 0.5pt;
    text-transform: uppercase;
  }}

  .tax-invoice {{
    text-align: center;
    font-size: 8pt;
    font-weight: bold;
    padding-bottom: 0.8mm;
    border-bottom: 0.5pt solid #000;
    margin-bottom: 0.8mm;
    flex-shrink: 0;
  }}

  .info-row {{
    display: flex;
    align-items: center;
    border-bottom: 0.5pt solid #000;
    padding: 0.8mm 0;
    flex-shrink: 0;
  }}
  .info-left   {{ flex: 1; font-size: 7pt; line-height: 1.35; }}
  .info-center {{ flex: 0 0 auto; text-align: center; padding: 0 1.5mm; }}
  .info-right  {{ flex: 1; font-size: 7pt; line-height: 1.35; text-align: right; }}
  .info-right .store-name {{ font-size: 9pt; font-weight: bold; }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 6.5pt;
    flex: 1;
    min-height: 0;
  }}
  thead tr th {{
    border-top: 0.5pt solid #000;
    border-bottom: 0.5pt solid #000;
    padding: 0.4mm 0.8mm;
    font-weight: bold;
    white-space: nowrap;
  }}
  tbody tr td {{
    padding: 0mm 0.8mm 8px 0.8mm;
    vertical-align: middle;
  }}
  tbody tr.spacer-row td {{
    height: 100%;
    padding: 0;
    border: none;
  }}

  .totals-row {{
    flex-shrink: 0;
    padding: 0.4mm 0;
    border-top: 0.5pt solid #000;
  }}
  .totals-table {{
    width: 100%;
    font-size: 7pt;
    height: auto;
    flex: none;
  }}
  .totals-table td {{ padding: 0.25mm 0.8mm; }}

  .due-row {{
    display: flex;
    justify-content: space-between;
    font-size: 7pt;
    font-weight: bold;
    padding: 0.6mm 0;
    border-top: 0.5pt solid #000;
    flex-shrink: 0;
  }}
  .sig-row {{
    display: flex;
    justify-content: space-between;
    padding: 1mm 0 0.4mm 0;
    font-size: 7pt;
    flex-shrink: 0;
  }}
  .footer-row {{
    display: flex;
    justify-content: space-between;
    font-size: 6pt;
    padding-top: 0.4mm;
    border-top: 0.5pt solid #000;
    flex-shrink: 0;
  }}

  .c {{ text-align: center; }}
  .r {{ text-align: right; }}
  .l {{ text-align: left; }}

  .print-btn {{
    position: fixed;
    bottom: 8px;
    left: 50%;
    transform: translateX(-50%);
    padding: 2mm 8mm;
    font-size: 11pt;
    cursor: pointer;
    background: #1a73e8;
    color: white;
    border: none;
    border-radius: 3px;
    z-index: 999;
  }}
  @media print {{
    .print-btn {{ display: none; }}
    @page {{ size: A4 landscape; margin: 0; }}
    body {{ width: 297mm; height: 210mm; overflow: hidden; }}
  }}
</style>
</head>
<body>

{customer_block}
<div class="cut-line"></div>
{store_block}

<button class="print-btn" onclick="window.print()">&#128424; Print Bill</button>
<script>window.onload = function() {{ window.print(); }}</script>

</body>
</html>"""

    tmp = tempfile.NamedTemporaryFile(
        suffix='.html', delete=False, mode='w', encoding='utf-8')
    tmp.write(html)
    tmp.close()
    return tmp.name


def _show_preview_window(parent, html_path, bill_no):
    win = tk.Toplevel(parent)
    win.title(f"Bill — {bill_no}")
    win.geometry("520x320")
    win.minsize(520, 320)
    win.maxsize(520, 320)
    win.resizable(False, False)

    # Apply app icon
    try:
        from core.scroll_manager import _apply_icon
        _apply_icon(win)
    except Exception:
        pass

    if html_path is None:
        ttk.Label(win, text="Could not generate bill.",
                  font=(FONT_FAMILY, 11)).pack(expand=True)
        return

    # Info label
    ttk.Label(win,
              text=(f"Bill {bill_no} generated!\n\n"
                    f"Click 'Print Bill' to print.\n"
                    f"In the print dialog set:\n"
                    f"  Paper size : A4\n"
                    f"  Orientation : Landscape\n"
                    f"  Margins : None / Minimum\n"
                    f"  Scale : 100%\n\n"
                    f"Left half = Customer Copy\n"
                    f"Right half = Store Copy"),
              font=(FONT_FAMILY, 10), justify='center',
              wraplength=420).pack(expand=True, pady=10)

    bf = ttk.Frame(win)
    bf.pack(fill=tk.X, padx=10, pady=8)

    def open_and_print():
        """Open in browser which auto-triggers the browser's print dialog via JS."""
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
        ("🖨️ Print Bill",  open_and_print, "primary"),
        ("Save HTML",      save_html,      "success"),
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
        # Delete temp file after a short delay (browser needs time to load it)
        threading.Timer(30.0, lambda: os.unlink(html_path)
                        if os.path.exists(html_path) else None).start()

    win.protocol("WM_DELETE_WINDOW", on_close)
