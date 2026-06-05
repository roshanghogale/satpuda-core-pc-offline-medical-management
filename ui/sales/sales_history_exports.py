"""
ui/sales_history_exports.py
────────────────────────────
All export methods for SalesHistoryPage.
No UI building, no tree interaction.
"""
import os
import shutil
import tempfile
import tkinter as tk
from tkinter import messagebox, filedialog
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import datetime

from core.column_config import export_table, filter_export_table, prompt_export_columns
from core.layout_config import get_configured_schedules
from core.scroll_manager import open_dialog
from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


def ask_schedules_for_report(parent, initial_filter=""):
    """
  Show schedule picker for Schedule Report export.
  Returns dict: mode 'all' | 'non_scheduled' | 'selected' with schedules list, or None if cancelled.
    """
    schedules = get_configured_schedules()
    dlg = open_dialog(parent, "Schedule Report — Select Schedule", width=400, height=460, resizable=False)
    body = dlg.content
    result = {"cancelled": True}

    ttk.Label(
        body,
        text="Choose which schedule(s) to include in the report:",
        font=(FONT_FAMILY, FONT_SIZE_LABELS),
        wraplength=360,
    ).pack(padx=12, pady=(12, 8))

    mode = tk.StringVar(value="all")
    initial = (initial_filter or "").strip()

    if initial == "Non-Scheduled":
        mode.set("non_scheduled")
    elif initial in schedules:
        mode.set("selected")

    rb_frame = ttk.Frame(body)
    rb_frame.pack(fill=tk.X, padx=12, pady=4)
    ttk.Radiobutton(rb_frame, text="All Schedules", variable=mode, value="all").pack(anchor=tk.W)
    ttk.Radiobutton(rb_frame, text="Non-Scheduled only", variable=mode, value="non_scheduled").pack(anchor=tk.W)
    ttk.Radiobutton(rb_frame, text="Selected schedules (check below):", variable=mode, value="selected").pack(anchor=tk.W)

    chk_outer = ttk.LabelFrame(body, text="Schedules (H, H1, X, …)")
    chk_outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

    canvas = tk.Canvas(chk_outer, highlightthickness=0, height=200)
    scroll = ttk.Scrollbar(chk_outer, orient=tk.VERTICAL, command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor=tk.NW)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)

    chk_vars = {}
    for sch in schedules:
        var = tk.BooleanVar(value=(mode.get() == "selected" and initial == sch))
        chk_vars[sch] = var
        ttk.Checkbutton(inner, text=sch, variable=var).pack(anchor=tk.W, padx=8, pady=2)

    if not schedules:
        ttk.Label(inner, text="No schedules in layout settings.", foreground="gray").pack(padx=8, pady=8)

    def select_all_checks():
        mode.set("selected")
        for var in chk_vars.values():
            var.set(True)

    def clear_checks():
        for var in chk_vars.values():
            var.set(False)

    btn_row = ttk.Frame(body)
    btn_row.pack(fill=tk.X, padx=12, pady=(0, 4))
    ttk.Button(btn_row, text="Select all listed", command=select_all_checks).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(btn_row, text="Clear checks", command=clear_checks).pack(side=tk.LEFT)

    def on_ok():
        m = mode.get()
        if m == "all":
            result.update({"cancelled": False, "mode": "all", "schedules": [], "label": "All Schedules"})
        elif m == "non_scheduled":
            result.update({"cancelled": False, "mode": "non_scheduled", "schedules": [], "label": "Non-Scheduled"})
        else:
            picked = [s for s, v in chk_vars.items() if v.get()]
            if not picked:
                showwarning("Select Schedule", "Check at least one schedule, or choose All / Non-Scheduled.", parent=dlg)
                return
            result.update({
                "cancelled": False,
                "mode": "selected",
                "schedules": picked,
                "label": ", ".join(picked),
            })
        dlg.destroy()

    def on_cancel():
        dlg.destroy()

    try:
        ttk.Button(dlg.footer, text="Export Report", command=on_ok, bootstyle="primary").pack(side=tk.LEFT, padx=4)
        ttk.Button(dlg.footer, text="Cancel", command=on_cancel, bootstyle="secondary").pack(side=tk.RIGHT, padx=4)
    except Exception:
        ttk.Button(dlg.footer, text="Export Report", command=on_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(dlg.footer, text="Cancel", command=on_cancel).pack(side=tk.RIGHT, padx=4)

    dlg.wait_window()
    return None if result.get("cancelled") else result


def _schedule_sql_filter(choice):
    """Return (sql_fragment, params) for schedule filter."""
    if not choice or choice.get("mode") == "all":
        return "", []
    if choice.get("mode") == "non_scheduled":
        return " AND (m.schedule IS NULL OR TRIM(m.schedule)='')", []
    names = choice.get("schedules") or []
    if not names:
        return "", []
    placeholders = ",".join("?" * len(names))
    return f" AND m.schedule IN ({placeholders})", list(names)


def export_menu(parent, cursor, from_date_fn, to_date_fn, schedule_filter_fn,
                export_current_view_fn):
    dlg = open_dialog(parent, "Export Sales Reports", width=320, height=400, resizable=False)
    body = dlg.content
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
        ttk.Button(body, text=label, width=36,
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
        showinfo("No Records", "No sales found."); return
    export_table(parent, 'Sales Register',
                 ['Bill No', 'Date', 'Customer', 'Phone', 'Total Amount', 'Discount',
                  'Cash Paid', 'Online Paid', 'Amount Paid', 'Previous Due', 'Due Amount',
                  'Total Due', 'Doctor'],
                 rows, 'sales_register', 'sales_history', 'sales_register')


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
        showinfo("No Records", "No sales found."); return
    def fmt(ym):
        try: return datetime.strptime(ym,'%Y-%m').strftime('%b-%Y')
        except: return ym
    rows = [[fmt(r[0]),r[1],f'{r[2]:.2f}',f'{r[3]:.2f}',
             f'{r[4]:.2f}',f'{r[5]:.2f}',f'{r[6]:.2f}',f'{r[7]:.2f}'] for r in raw]
    rows.append(['TOTAL', sum(r[1] for r in raw),
                 f'{sum(r[2] for r in raw):.2f}', f'{sum(r[3] for r in raw):.2f}',
                 f'{sum(r[4] for r in raw):.2f}', f'{sum(r[5] for r in raw):.2f}',
                 f'{sum(r[6] for r in raw):.2f}', f'{sum(r[7] for r in raw):.2f}'])
    export_table(parent, 'Monthly Sales Summary',
                 ['Month', 'Bills', 'Total Sales', 'Discount', 'Cash Paid', 'Online Paid',
                  'Amount Paid', 'Due Amount'],
                 rows, 'monthly_sales_summary', 'sales_history', 'monthly_summary')


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
        showinfo("No Records", "No sales found."); return
    rows = [[r[0],r[1],f'{r[2]:.2f}',f'{r[3]:.2f}',
             f'{r[4]:.2f}',f'{r[5]:.2f}',f'{r[6]:.2f}'] for r in raw]
    export_table(parent, 'Daily Sales Summary',
                 ['Date', 'Bills', 'Total Amount', 'Cash Paid', 'Online Paid', 'Amount Paid', 'Due Amount'],
                 rows, 'daily_sales_summary', 'sales_history', 'daily_summary')


def export_customer_due(parent, cursor):
    cursor.execute("""
        SELECT c.name, c.phone, s.bill_date, s.bill_no, s.total_amount, s.total_due
        FROM sales s JOIN customers c ON s.customer_id=c.id
        WHERE s.total_due>0 AND s.account_cleared=0
        ORDER BY s.total_due DESC
    """)
    rows = cursor.fetchall()
    if not rows:
        showinfo("No Records", "No outstanding dues."); return
    export_table(parent, 'Customer Due Report',
                 ['Customer', 'Phone', 'Date', 'Bill No', 'Total Amount', 'Due Amount'],
                 rows, 'customer_due_report', 'sales_history', 'customer_due')


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
        showinfo("No Records", "No doctor-linked sales found."); return
    export_table(parent, 'Doctor-wise Sales',
                 ['Doctor', 'Date', 'Customer', 'Bill No', 'Medicine', 'Schedule', 'Qty', 'Rate', 'Amount'],
                 rows, 'doctor_wise_sales', 'sales_history', 'doctor_wise')


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
        showinfo("No Records", "No sales found."); return
    export_table(parent, 'Payment Mode Report',
                 ['Date', 'Bill No', 'Customer', 'Total Amount', 'Cash Paid', 'Online Paid',
                  'Amount Paid', 'Due Amount'],
                 rows, 'payment_mode_report', 'sales_history', 'payment_mode')


def export_schedule_report(parent, cursor, from_date_fn, to_date_fn, schedule_filter_fn):
    choice = ask_schedules_for_report(parent, schedule_filter_fn())
    if choice is None:
        return

    fd, td = from_date_fn(), to_date_fn()
    sch_label = choice.get("label", "All Schedules")
    q = """SELECT s.bill_date, c.name, COALESCE(s.doctor_name,''),
                  m.name, COALESCE(m.content_drug,''), COALESCE(m.schedule,''), m.expiry_date,
                  si.qty, si.rate, si.amount
           FROM sales s
           JOIN customers c   ON s.customer_id  = c.id
           JOIN sales_items si ON s.id           = si.sale_id
           JOIN medicines m   ON si.medicine_id  = m.id
           WHERE 1=1"""
    params = []
    if fd:  q += ' AND s.bill_date>=?'; params.append(fd)
    if td:  q += ' AND s.bill_date<=?'; params.append(td)
    sch_sql, sch_params = _schedule_sql_filter(choice)
    q += sch_sql
    params.extend(sch_params)
    q += ' ORDER BY m.schedule, s.bill_date, c.name'
    cursor.execute(q, params)
    rows = cursor.fetchall()
    if not rows:
        showinfo("No Data", "No records found."); return

    def fmt_exp(raw):
        if not raw: return ''
        try:
            p = str(raw).split('-')
            return f"{p[2]}/{p[1]}/{p[0][2:]}" if len(p) == 3 else raw
        except Exception: return str(raw)

    date_range = f"{fd} to {td}" if fd or td else 'All Dates'
    safe_label = sch_label.replace(',', '_').replace(' ', '')

    headers = [
        'Date', 'Customer', 'Doctor', 'Medicine', 'Content/Drug', 'Schedule',
        'Expiry', 'Qty', 'Rate', 'Amount',
    ]
    col_vis = prompt_export_columns(parent, 'sales_history', 'schedule_report', headers)
    if col_vis is None:
        return
    table_rows = []
    for r in rows:
        table_rows.append([
            r[0], r[1], r[2] or '—', r[3], r[4] or '—', r[5] or '—', fmt_exp(r[6]),
            int(r[7]) if r[7] else 0,
            f"{float(r[8] or 0):.2f}",
            f"{float(r[9] or 0):.2f}",
        ])
    headers, table_rows = filter_export_table(
        headers, table_rows, 'sales_history', 'schedule_report', column_vis=col_vis,
    )
    if not table_rows:
        showinfo("No Data", "No columns selected for export. Enable columns in Settings → Appearance.")
        return

    qty_idx = headers.index('Qty') if 'Qty' in headers else None
    amt_idx = headers.index('Amount') if 'Amount' in headers else None
    total_qty = sum(int(row[qty_idx]) for row in table_rows) if qty_idx is not None else 0
    total_amount = sum(float(row[amt_idx]) for row in table_rows) if amt_idx is not None else 0.0

    th_cells = '<th style="width:4%">Sr</th>' + ''.join(
        f'<th>{h}</th>' for h in headers
    )
    row_html = ""
    for i, row in enumerate(table_rows, 1):
        bg = '#f9f9f9' if i % 2 == 0 else '#ffffff'
        cells = ''.join(f'<td>{cell}</td>' for cell in row)
        row_html += f'<tr style="background:{bg}"><td class="c">{i}</td>{cells}</tr>'

    foot_cols = len(headers) + 1
    foot_left = max(1, foot_cols - 3)
    tfoot = ""
    if qty_idx is not None or amt_idx is not None:
        tfoot = f"""<tfoot><tr>
  <td colspan="{foot_left}" class="r">Total</td>
  {'<td class="c">' + str(total_qty) + '</td>' if qty_idx is not None else ''}
  {'<td></td>' if qty_idx is not None and amt_idx is not None else ''}
  {'<td class="r">' + f'{total_amount:.2f}' + '</td>' if amt_idx is not None else ''}
</tr></tfoot>"""

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
<div class="subtitle">Schedule: <b>{sch_label}</b> | Period: <b>{date_range}</b> | Records: <b>{len(table_rows)}</b></div>
<table>
<thead><tr>{th_cells}</tr></thead>
<tbody>{row_html}</tbody>
{tfoot}
</table>
<button class="print-btn" onclick="window.print()">&#128424; Print Report</button>
</body></html>"""

    tmp = tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8')
    tmp.write(html); tmp.close()

    choice = askyesno(
        "Export Schedule Report",
        f"Report ready with {len(table_rows)} records.\n\n"
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
            initialfile=f'Schedule_Report_{safe_label}_{fd}_{td}.html')
        if dest:
            shutil.copy2(tmp.name, dest)
            showinfo("Saved", f"Report saved to:\n{dest}")
