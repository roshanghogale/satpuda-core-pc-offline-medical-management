"""
ui/sales_history_exports.py
────────────────────────────
All export methods for SalesHistoryPage.
No UI building, no tree interaction.
"""
import os
import shutil
import tempfile
from tkinter import messagebox, filedialog
from datetime import datetime

from core.export_manager import export_data
from core.scroll_manager import open_dialog

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


def export_menu(parent, cursor, from_date_fn, to_date_fn, schedule_filter_fn,
                export_current_view_fn):
    dlg = open_dialog(parent, "Export Sales Reports", width=320, height=380, resizable=False)
    reports = [
        ("Current View (with filters)",  export_current_view_fn),
        ("Sales Register (all bills)",   lambda: export_sales_register(parent, cursor, from_date_fn, to_date_fn)),
        ("Monthly Summary",              lambda: export_monthly_summary(parent, cursor, from_date_fn, to_date_fn)),
        ("Daily Sales Summary",          lambda: export_daily_summary(parent, cursor, from_date_fn, to_date_fn)),
        ("Customer Due Report",          lambda: export_customer_due(parent, cursor)),
        ("Doctor-wise Sales",            lambda: export_doctor_sales(parent, cursor, from_date_fn, to_date_fn)),
        ("Payment Mode Report",          lambda: export_payment_mode(parent, cursor, from_date_fn, to_date_fn)),
        ("Schedule Report",              lambda: export_schedule_report(parent, cursor, from_date_fn, to_date_fn, schedule_filter_fn)),
    ]
    for label, cmd in reports:
        ttk.Button(dlg, text=label, width=36,
                   command=lambda c=cmd, d=dlg: [d.destroy(), c()]
                   ).pack(pady=3, padx=10)


def export_sales_register(parent, cursor, from_date_fn, to_date_fn):
    fd, td = from_date_fn(), to_date_fn()
    q = """SELECT s.bill_no, s.bill_date, c.name, c.phone,
                  s.total_amount, s.discount, s.cash_paid, s.online_paid,
                  s.amount_paid, s.previous_due, s.due_amount, s.total_due,
                  COALESCE(s.doctor_name,'')
           FROM sales s JOIN customers c ON s.customer_id=c.id WHERE 1=1"""
    params = []
    if fd: q += ' AND s.bill_date>=?'; params.append(fd)
    if td: q += ' AND s.bill_date<=?'; params.append(td)
    q += ' ORDER BY s.bill_date DESC'
    cursor.execute(q, params)
    rows = cursor.fetchall()
    if not rows:
        messagebox.showinfo("No Records", "No sales found."); return
    export_data(parent, 'Sales Register',
                ['Bill No','Date','Customer','Phone','Total','Discount',
                 'Cash','Online','Paid','Prev Due','Due','Total Due','Doctor'],
                rows, 'sales_register')


def export_monthly_summary(parent, cursor, from_date_fn, to_date_fn):
    fd, td = from_date_fn(), to_date_fn()
    q = """SELECT strftime('%Y-%m',s.bill_date), COUNT(*),
                  SUM(s.total_amount), SUM(s.discount),
                  SUM(s.cash_paid), SUM(s.online_paid),
                  SUM(s.amount_paid), SUM(s.due_amount)
           FROM sales s WHERE 1=1"""
    params = []
    if fd: q += ' AND s.bill_date>=?'; params.append(fd)
    if td: q += ' AND s.bill_date<=?'; params.append(td)
    q += " GROUP BY strftime('%Y-%m',s.bill_date) ORDER BY 1 DESC"
    cursor.execute(q, params)
    raw = cursor.fetchall()
    if not raw:
        messagebox.showinfo("No Records", "No sales found."); return
    def fmt(ym):
        try: return datetime.strptime(ym,'%Y-%m').strftime('%b-%Y')
        except: return ym
    rows = [[fmt(r[0]),r[1],f'{r[2]:.2f}',f'{r[3]:.2f}',
             f'{r[4]:.2f}',f'{r[5]:.2f}',f'{r[6]:.2f}',f'{r[7]:.2f}'] for r in raw]
    rows.append(['TOTAL', sum(r[1] for r in raw),
                 f'{sum(r[2] for r in raw):.2f}', f'{sum(r[3] for r in raw):.2f}',
                 f'{sum(r[4] for r in raw):.2f}', f'{sum(r[5] for r in raw):.2f}',
                 f'{sum(r[6] for r in raw):.2f}', f'{sum(r[7] for r in raw):.2f}'])
    export_data(parent, 'Monthly Sales Summary',
                ['Month','Bills','Total Sales','Discount','Cash','Online','Paid','Due'],
                rows, 'monthly_sales_summary')


def export_daily_summary(parent, cursor, from_date_fn, to_date_fn):
    fd, td = from_date_fn(), to_date_fn()
    q = """SELECT s.bill_date, COUNT(*), SUM(s.total_amount),
                  SUM(s.cash_paid), SUM(s.online_paid),
                  SUM(s.amount_paid), SUM(s.due_amount)
           FROM sales s WHERE 1=1"""
    params = []
    if fd: q += ' AND s.bill_date>=?'; params.append(fd)
    if td: q += ' AND s.bill_date<=?'; params.append(td)
    q += ' GROUP BY s.bill_date ORDER BY s.bill_date DESC'
    cursor.execute(q, params)
    raw = cursor.fetchall()
    if not raw:
        messagebox.showinfo("No Records", "No sales found."); return
    rows = [[r[0],r[1],f'{r[2]:.2f}',f'{r[3]:.2f}',
             f'{r[4]:.2f}',f'{r[5]:.2f}',f'{r[6]:.2f}'] for r in raw]
    export_data(parent, 'Daily Sales Summary',
                ['Date','Bills','Total','Cash','Online','Paid','Due'],
                rows, 'daily_sales_summary')


def export_customer_due(parent, cursor):
    cursor.execute("""
        SELECT c.name, c.phone, s.bill_date, s.bill_no, s.total_amount, s.total_due
        FROM sales s JOIN customers c ON s.customer_id=c.id
        WHERE s.total_due>0 AND s.account_cleared=0
        ORDER BY s.total_due DESC
    """)
    rows = cursor.fetchall()
    if not rows:
        messagebox.showinfo("No Records", "No outstanding dues."); return
    export_data(parent, 'Customer Due Report',
                ['Customer','Phone','Bill Date','Bill No','Bill Amount','Total Due'],
                rows, 'customer_due_report')


def export_doctor_sales(parent, cursor, from_date_fn, to_date_fn):
    fd, td = from_date_fn(), to_date_fn()
    q = """SELECT s.doctor_name, s.bill_date, c.name, s.bill_no,
                  m.name, COALESCE(m.schedule,''), si.qty, si.rate, si.amount
           FROM sales s
           JOIN customers c ON s.customer_id=c.id
           JOIN sales_items si ON s.id=si.sale_id
           JOIN medicines m ON si.medicine_id=m.id
           WHERE s.doctor_name IS NOT NULL AND TRIM(s.doctor_name)!=''"""
    params = []
    if fd: q += ' AND s.bill_date>=?'; params.append(fd)
    if td: q += ' AND s.bill_date<=?'; params.append(td)
    q += ' ORDER BY s.doctor_name, s.bill_date'
    cursor.execute(q, params)
    rows = cursor.fetchall()
    if not rows:
        messagebox.showinfo("No Records", "No doctor-linked sales found."); return
    export_data(parent, 'Doctor-wise Sales',
                ['Doctor','Date','Customer','Bill No','Medicine','Schedule','Qty','Rate','Amount'],
                rows, 'doctor_wise_sales')


def export_payment_mode(parent, cursor, from_date_fn, to_date_fn):
    fd, td = from_date_fn(), to_date_fn()
    q = """SELECT s.bill_date, s.bill_no, c.name,
                  s.total_amount, s.cash_paid, s.online_paid,
                  s.amount_paid, s.due_amount
           FROM sales s JOIN customers c ON s.customer_id=c.id WHERE 1=1"""
    params = []
    if fd: q += ' AND s.bill_date>=?'; params.append(fd)
    if td: q += ' AND s.bill_date<=?'; params.append(td)
    q += ' ORDER BY s.bill_date DESC'
    cursor.execute(q, params)
    rows = cursor.fetchall()
    if not rows:
        messagebox.showinfo("No Records", "No sales found."); return
    export_data(parent, 'Payment Mode Report',
                ['Date','Bill No','Customer','Total','Cash','Online','Paid','Due'],
                rows, 'payment_mode_report')


def export_schedule_report(parent, cursor, from_date_fn, to_date_fn, schedule_filter_fn):
    fd, td = from_date_fn(), to_date_fn()
    sch = schedule_filter_fn()
    q = """SELECT s.bill_date, c.name, COALESCE(s.doctor_name,''),
                  m.name, COALESCE(m.schedule,''), m.expiry_date,
                  si.qty, si.rate, si.amount
           FROM sales s
           JOIN customers c   ON s.customer_id  = c.id
           JOIN sales_items si ON s.id           = si.sale_id
           JOIN medicines m   ON si.medicine_id  = m.id
           WHERE 1=1"""
    params = []
    if fd:  q += ' AND s.bill_date>=?'; params.append(fd)
    if td:  q += ' AND s.bill_date<=?'; params.append(td)
    if sch and sch != 'All':
        if sch == 'Non-Scheduled': q += " AND (m.schedule IS NULL OR m.schedule='')"
        else:                      q += " AND m.schedule=?"; params.append(sch)
    q += ' ORDER BY m.schedule, s.bill_date, c.name'
    cursor.execute(q, params)
    rows = cursor.fetchall()
    if not rows:
        messagebox.showinfo("No Data", "No records found."); return

    def fmt_exp(raw):
        if not raw: return ''
        try:
            p = str(raw).split('-')
            return f"{p[2]}/{p[1]}/{p[0][2:]}" if len(p) == 3 else raw
        except Exception: return str(raw)

    sch_label  = sch if sch else 'All Schedules'
    date_range = f"{fd} to {td}" if fd or td else 'All Dates'
    row_html = ""
    for i, r in enumerate(rows, 1):
        bg = '#f9f9f9' if i % 2 == 0 else '#ffffff'
        row_html += f"""
        <tr style="background:{bg}">
            <td class="c">{i}</td><td>{r[0]}</td><td>{r[1]}</td>
            <td>{r[2] or '—'}</td><td>{r[3]}</td>
            <td class="c">{r[4] or '—'}</td><td class="c">{fmt_exp(r[5])}</td>
            <td class="c">{int(r[6]) if r[6] else 0}</td>
            <td class="r">{float(r[7] or 0):.2f}</td>
            <td class="r">{float(r[8] or 0):.2f}</td>
        </tr>"""

    total_amount = sum(float(r[8] or 0) for r in rows)
    total_qty    = sum(int(r[6] or 0) for r in rows)

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Schedule Sales Report</title>
<style>
@page{{size:A4 portrait;margin:10mm}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Nirmala UI',Arial,sans-serif;font-size:10pt;color:#000}}
h2{{text-align:center;font-size:13pt;margin-bottom:2mm}}
.subtitle{{text-align:center;font-size:9pt;color:#444;margin-bottom:4mm}}
table{{width:100%;border-collapse:collapse;font-size:9pt}}
thead th{{background:#2c3e50;color:#fff;padding:2mm 1.5mm;text-align:center;border:0.3pt solid #000}}
tbody td{{padding:1.5mm;border:0.3pt solid #ccc;vertical-align:middle}}
tfoot td{{padding:2mm 1.5mm;border-top:1pt solid #000;font-weight:bold;font-size:10pt}}
.c{{text-align:center}}.r{{text-align:right}}
.print-btn{{display:block;margin:5mm auto;padding:2mm 10mm;font-size:11pt;
  background:#2c3e50;color:white;border:none;border-radius:3px;cursor:pointer}}
@media print{{.print-btn{{display:none}}}}
</style></head><body>
<h2>Schedule Sales Report</h2>
<div class="subtitle">Schedule: <b>{sch_label}</b> | Period: <b>{date_range}</b> | Records: <b>{len(rows)}</b></div>
<table>
<thead><tr>
  <th style="width:4%">Sr</th><th style="width:9%">Date</th><th style="width:14%">Customer</th>
  <th style="width:12%">Doctor</th><th style="width:22%">Medicine</th>
  <th style="width:7%">Schedule</th><th style="width:9%">Expiry</th>
  <th style="width:5%">Qty</th><th style="width:9%">Rate</th><th style="width:9%">Amount</th>
</tr></thead>
<tbody>{row_html}</tbody>
<tfoot><tr>
  <td colspan="7" class="r">Total</td>
  <td class="c">{total_qty}</td><td></td><td class="r">{total_amount:.2f}</td>
</tr></tfoot>
</table>
<button class="print-btn" onclick="window.print()">&#128424; Print Report</button>
</body></html>"""

    tmp = tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8')
    tmp.write(html); tmp.close()

    choice = messagebox.askyesno(
        "Export Schedule Report",
        f"Report ready with {len(rows)} records.\n\n"
        "YES = open in browser (print from there)\nNO = save as HTML file")
    if choice:
        if os.name == 'nt':
            os.startfile(tmp.name)
        else:
            import subprocess
            subprocess.Popen(['xdg-open', tmp.name])
    else:
        dest = filedialog.asksaveasfilename(
            defaultextension='.html',
            filetypes=[('HTML files','*.html')],
            initialfile=f'Schedule_Report_{sch_label}_{fd}_{td}.html')
        if dest:
            shutil.copy2(tmp.name, dest)
            messagebox.showinfo("Saved", f"Report saved to:\n{dest}")
