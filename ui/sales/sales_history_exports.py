"""
ui/sales_history_exports.py
────────────────────────────
All export methods for SalesHistoryPage.
No UI building, no tree interaction.
"""
import tkinter as tk
from core.themed_messagebox import showinfo, showwarning
from datetime import datetime, date
import calendar

from core.column_config import export_table, filter_export_table
from core.export_prefs import (
    load_schedule_report_layout,
    save_schedule_report_layout,
    load_schedule_dm_prefs,
    save_schedule_dm_prefs,
    SCHEDULE_LAYOUT_LANDSCAPE,
    SCHEDULE_LAYOUT_PORTRAIT,
    SCHEDULE_LAYOUT_STYLED,
    SCHEDULE_LAYOUT_LABELS,
    SCHEDULE_DM_STYLE_CLASSIC,
    SCHEDULE_DM_STYLE_SIGN,
    SCHEDULE_DM_STYLE_LABELS,
)
from core.layout_config import get_configured_schedules
from core.scroll_manager import (
    DIALOG_SIZE_LARGE,
    dialog_root,
    dialog_section,
    open_dialog,
    refresh_dialog_geometry,
)
from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


def _online_mode() -> bool:
    try:
        from core.sync_prefs import is_online_mode
        return bool(is_online_mode())
    except Exception:
        return False


def _fetch_online_sales(from_date="", to_date=""):
    from core import store_query_client as sq
    data = sq.list_sales(from_date=from_date or "", to_date=to_date or "", limit=5000)
    return list(data.get("rows") or [])


def _f(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return float(default)


def ask_schedules_for_report(parent, initial_filter="", from_date_fn=None, to_date_fn=None):
    """
    Schedule picker + date range for Schedule Report export.
    Returns dict with mode, schedules, label, from_date, to_date — or None if cancelled.
    """
    schedules = get_configured_schedules()
    today = date.today()
    today_str = today.isoformat()
    init_from = (from_date_fn() if callable(from_date_fn) else "") or ""
    init_to = (to_date_fn() if callable(to_date_fn) else "") or today_str

    saved_layout = load_schedule_report_layout()
    layout_labels = [
        SCHEDULE_LAYOUT_LABELS[SCHEDULE_LAYOUT_PORTRAIT],
        SCHEDULE_LAYOUT_LABELS[SCHEDULE_LAYOUT_LANDSCAPE],
        SCHEDULE_LAYOUT_LABELS[SCHEDULE_LAYOUT_STYLED],
    ]
    layout_values = [
        SCHEDULE_LAYOUT_PORTRAIT,
        SCHEDULE_LAYOUT_LANDSCAPE,
        SCHEDULE_LAYOUT_STYLED,
    ]
    init_layout_i = 0
    if saved_layout in layout_values:
        init_layout_i = layout_values.index(saved_layout)
    layout_var = tk.StringVar(value=layout_labels[init_layout_i])
    dm_prefs = load_schedule_dm_prefs()
    dm_style_values = [SCHEDULE_DM_STYLE_CLASSIC, SCHEDULE_DM_STYLE_SIGN]
    dm_style_labels = [SCHEDULE_DM_STYLE_LABELS[k] for k in dm_style_values]
    saved_dm = dm_prefs.get("style") or SCHEDULE_DM_STYLE_CLASSIC
    dm_style_var = tk.StringVar(
        value=SCHEDULE_DM_STYLE_LABELS.get(saved_dm, dm_style_labels[0]),
    )
    dm_borders_var = tk.BooleanVar(value=bool(dm_prefs.get("borders", True)))

    w, h = DIALOG_SIZE_LARGE
    dlg = open_dialog(parent, "Schedule Report", width=w, height=h + 40, resizable=True)
    root = dialog_root(dlg.content)
    result = {"cancelled": True}

    # ── Date range ────────────────────────────────────────────────────────
    date_mode = tk.StringVar(value="year")
    year_var = tk.StringVar(value=str(today.year))
    month_var = tk.StringVar(value=f"{today.year}-{today.month:02d}")
    from_var = tk.StringVar(value=init_from or f"{today.year}-{today.month:02d}-01")
    to_var = tk.StringVar(value=init_to or today_str)

    date_lf, date_inner = dialog_section(root, "Report period")
    date_lf.pack(fill=tk.X, pady=(0, 8))

    ttk.Radiobutton(date_inner, text="Full year", variable=date_mode, value="year").grid(
        row=0, column=0, sticky=tk.W, padx=8, pady=2)
    ttk.Label(date_inner, text="Year:").grid(row=0, column=1, sticky=tk.E, padx=4)
    ttk.Entry(date_inner, textvariable=year_var, width=8).grid(row=0, column=2, sticky=tk.W, pady=2)

    ttk.Radiobutton(date_inner, text="Month", variable=date_mode, value="month").grid(
        row=1, column=0, sticky=tk.W, padx=8, pady=2)
    ttk.Label(date_inner, text="YYYY-MM:").grid(row=1, column=1, sticky=tk.E, padx=4)
    ttk.Entry(date_inner, textvariable=month_var, width=10).grid(row=1, column=2, sticky=tk.W, pady=2)

    ttk.Radiobutton(date_inner, text="Custom dates", variable=date_mode, value="custom").grid(
        row=2, column=0, sticky=tk.W, padx=8, pady=2)
    ttk.Label(date_inner, text="From (YYYY-MM-DD):").grid(row=3, column=1, sticky=tk.E, padx=4)
    ttk.Entry(date_inner, textvariable=from_var, width=12).grid(row=3, column=2, sticky=tk.W, pady=2)
    ttk.Label(date_inner, text="To (defaults today):").grid(row=4, column=1, sticky=tk.E, padx=4)
    ttk.Entry(date_inner, textvariable=to_var, width=12).grid(row=4, column=2, sticky=tk.W, pady=2)

    def _resolve_dates():
        dm = date_mode.get()
        try:
            if dm == "year":
                y = int(year_var.get().strip())
                return f"{y}-01-01", f"{y}-12-31"
            if dm == "month":
                raw = month_var.get().strip()
                y_s, m_s = raw.split("-", 1)
                y, m = int(y_s), int(m_s)
                last = calendar.monthrange(y, m)[1]
                return f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last:02d}"
            fd = from_var.get().strip()
            td = to_var.get().strip() or today_str
            return fd, td
        except Exception:
            return init_from, today_str

    # ── Page layout ───────────────────────────────────────────────────────
    layout_lf, layout_inner = dialog_section(root, "Export style")
    layout_lf.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(layout_inner, text="Page layout:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=4)
    layout_combo = ttk.Combobox(
        layout_inner,
        textvariable=layout_var,
        values=layout_labels,
        state="readonly",
        width=42,
    )
    layout_combo.grid(row=0, column=1, sticky=tk.W, padx=4, pady=4)
    if layout_var.get() in layout_labels:
        layout_combo.current(layout_labels.index(layout_var.get()))

    ttk.Label(layout_inner, text="Dot matrix preset:").grid(
        row=1, column=0, sticky=tk.W, padx=8, pady=4,
    )
    dm_style_combo = ttk.Combobox(
        layout_inner,
        textvariable=dm_style_var,
        values=dm_style_labels,
        state="readonly",
        width=42,
    )
    dm_style_combo.grid(row=1, column=1, sticky=tk.W, padx=4, pady=4)
    if dm_style_var.get() in dm_style_labels:
        dm_style_combo.current(dm_style_labels.index(dm_style_var.get()))
    ttk.Checkbutton(
        layout_inner,
        text="Dot matrix table borders (| / +---+)",
        variable=dm_borders_var,
    ).grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=8, pady=(2, 4))

    def _resolve_page_layout() -> str:
        label = (layout_var.get() or "").strip()
        if label in layout_labels:
            return layout_values[layout_labels.index(label)]
        return saved_layout or SCHEDULE_LAYOUT_PORTRAIT

    def _resolve_dm_style() -> str:
        label = (dm_style_var.get() or "").strip()
        if label in dm_style_labels:
            return dm_style_values[dm_style_labels.index(label)]
        return saved_dm or SCHEDULE_DM_STYLE_CLASSIC

    # ── Schedule filter ───────────────────────────────────────────────────
    ttk.Label(
        root,
        text="Schedules to include:",
        font=(FONT_FAMILY, FONT_SIZE_LABELS),
    ).pack(anchor=tk.W, pady=(4, 4))

    mode = tk.StringVar(value="all")
    initial = (initial_filter or "").strip()
    if initial == "Non-Scheduled":
        mode.set("non_scheduled")
    elif initial in schedules:
        mode.set("selected")

    rb_frame = ttk.Frame(root)
    rb_frame.pack(fill=tk.X, pady=2)
    rb_all = ttk.Radiobutton(rb_frame, text="All Schedules", variable=mode, value="all")
    rb_all.pack(anchor=tk.W)
    rb_non = ttk.Radiobutton(rb_frame, text="Non-Scheduled only", variable=mode, value="non_scheduled")
    rb_non.pack(anchor=tk.W)
    rb_sel = ttk.Radiobutton(rb_frame, text="Selected schedules (check below):", variable=mode, value="selected")
    rb_sel.pack(anchor=tk.W)
    mode_radios = [rb_all, rb_non, rb_sel]

    chk_outer, chk_inner_wrap = dialog_section(root, "Schedules (H, H1, X, …)")
    chk_outer.pack(fill=tk.BOTH, expand=True, pady=6)

    canvas = tk.Canvas(chk_inner_wrap, highlightthickness=0, height=200)
    scroll = ttk.Scrollbar(chk_inner_wrap, orient=tk.VERTICAL, command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor=tk.NW)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)

    chk_vars = {}
    schedule_checks = []
    for sch in schedules:
        var = tk.BooleanVar(value=(mode.get() == "selected" and initial == sch))
        chk_vars[sch] = var
        cb = ttk.Checkbutton(inner, text=sch, variable=var)
        cb.pack(anchor=tk.W, padx=8, pady=2)
        schedule_checks.append(cb)

    if not schedules:
        ttk.Label(inner, text="No schedules in layout settings.", foreground="gray").pack(padx=8, pady=8)

    def select_all_checks():
        mode.set("selected")
        for var in chk_vars.values():
            var.set(True)

    def clear_checks():
        for var in chk_vars.values():
            var.set(False)

    btn_row = ttk.Frame(root)
    btn_row.pack(fill=tk.X, pady=(0, 4))
    select_all_btn = ttk.Button(btn_row, text="Select all listed", command=select_all_checks)
    select_all_btn.pack(side=tk.LEFT, padx=(0, 6))
    clear_btn = ttk.Button(btn_row, text="Clear checks", command=clear_checks)
    clear_btn.pack(side=tk.LEFT)

    def _finish(action: str):
        m = mode.get()
        fd, td = _resolve_dates()
        page_layout = _resolve_page_layout()
        dm_style = _resolve_dm_style()
        dm_borders = bool(dm_borders_var.get())
        try:
            save_schedule_report_layout(page_layout)
        except Exception:
            pass
        try:
            save_schedule_dm_prefs(style=dm_style, borders=dm_borders)
        except Exception:
            pass
        base = {
            "cancelled": False,
            "from_date": fd,
            "to_date": td,
            "action": action,
            "page_layout": page_layout,
            "dm_style": dm_style,
            "dm_borders": dm_borders,
        }
        if m == "all":
            result.update({**base, "mode": "all", "schedules": [], "label": "All Schedules"})
        elif m == "non_scheduled":
            result.update({**base, "mode": "non_scheduled", "schedules": [], "label": "Non-Scheduled"})
        else:
            picked = [s for s, v in chk_vars.items() if v.get()]
            if not picked:
                showwarning("Select Schedule", "Check at least one schedule, or choose All / Non-Scheduled.", parent=dlg)
                return
            result.update({
                **base,
                "mode": "selected",
                "schedules": picked,
                "label": ", ".join(picked),
            })
        dlg.destroy()

    def on_export():
        _finish("export")

    def on_print():
        _finish("print")

    def on_cancel():
        dlg.destroy()

    try:
        export_btn = ttk.Button(dlg.footer, text="Export Report", command=on_export, bootstyle="primary")
        export_btn.pack(side=tk.LEFT, padx=4)
        print_btn = ttk.Button(dlg.footer, text="Print", command=on_print, bootstyle="success")
        print_btn.pack(side=tk.LEFT, padx=4)
        cancel_btn = ttk.Button(dlg.footer, text="Cancel", command=on_cancel, bootstyle="secondary")
        cancel_btn.pack(side=tk.RIGHT, padx=4)
    except Exception:
        export_btn = ttk.Button(dlg.footer, text="Export Report", command=on_export)
        export_btn.pack(side=tk.LEFT, padx=4)
        print_btn = ttk.Button(dlg.footer, text="Print", command=on_print)
        print_btn.pack(side=tk.LEFT, padx=4)
        cancel_btn = ttk.Button(dlg.footer, text="Cancel", command=on_cancel)
        cancel_btn.pack(side=tk.RIGHT, padx=4)

    from core.dialog_keyboard import wire_dialog_arrow_nav, wire_radiobutton_values
    wire_radiobutton_values(mode_radios, mode, ["all", "non_scheduled", "selected"])
    wire_dialog_arrow_nav(
        mode_radios + schedule_checks + [
            layout_combo, dm_style_combo, select_all_btn, clear_btn, export_btn, print_btn, cancel_btn,
        ],
        dlg,
        initial_focus=rb_all,
    )

    from core.voice.voice_dialog import (
        consume_voice_dialog_hints,
        register_schedule_dialog,
        speak_voice_dialog_hint,
    )

    register_schedule_dialog(
        dlg,
        mode=mode,
        chk_vars=chk_vars,
        schedules=schedules,
        on_export=on_export,
        on_print=on_print,
        on_select_all=select_all_checks,
        on_clear=clear_checks,
    )
    if consume_voice_dialog_hints():
        dlg.after(
            50,
            lambda: speak_voice_dialog_hint(
                "Schedule picker open. Say export report or print report, or show schedules."
            ),
        )

    dlg.after(300, lambda: refresh_dialog_geometry(dlg))

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
    from core.export_manager import show_export_option_dialog
    show_export_option_dialog(parent, "Export Sales Reports", [
        ("Current View (with filters)",  export_current_view_fn),
        ("Sales Register (all bills)",   lambda: export_sales_register(parent, cursor, from_date_fn, to_date_fn)),
        ("Monthly Summary",              lambda: export_monthly_summary(parent, cursor, from_date_fn, to_date_fn)),
        ("Daily Sales Summary",          lambda: export_daily_summary(parent, cursor, from_date_fn, to_date_fn)),
        ("Customer Due Report",          lambda: export_customer_due(parent, cursor)),
        ("Doctor-wise Sales",            lambda: export_doctor_sales(parent, cursor, from_date_fn, to_date_fn)),
        ("Payment Mode Report",          lambda: export_payment_mode(parent, cursor, from_date_fn, to_date_fn)),
        ("Schedule Report",              lambda: export_schedule_report(parent, cursor, from_date_fn, to_date_fn, schedule_filter_fn)),
    ], width=360, height=400)


def export_sales_register(parent, cursor, from_date_fn, to_date_fn):
    fd, td = from_date_fn(), to_date_fn()
    if _online_mode():
        try:
            raw = _fetch_online_sales(fd, td)
        except Exception as exc:
            showwarning("Export", f"Could not load sales from server:\n{exc}")
            return
        rows = []
        for r in raw:
            rows.append((
                r.get("bill_no") or "",
                r.get("bill_date") or "",
                r.get("customer_name") or "",
                r.get("customer_phone") or r.get("phone") or "",
                _f(r.get("total_amount")),
                _f(r.get("discount")),
                _f(r.get("cash_paid")),
                _f(r.get("online_paid")),
                _f(r.get("amount_paid")),
                _f(r.get("previous_due")),
                _f(r.get("due_amount")),
                _f(r.get("total_due")),
                r.get("doctor_name") or "",
            ))
        if not rows:
            showinfo("No Records", "No sales found."); return
        export_table(parent, 'Sales Register',
                     ['Bill No', 'Date', 'Customer', 'Phone', 'Total Amount', 'Discount',
                      'Cash Paid', 'Online Paid', 'Amount Paid', 'Previous Due', 'Due Amount',
                      'Total Due', 'Doctor'],
                     rows, 'sales_register', 'sales_history', 'sales_register')
        return
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
    if _online_mode():
        try:
            raw = _fetch_online_sales(fd, td)
        except Exception as exc:
            showwarning("Export", f"Could not load sales from server:\n{exc}")
            return
        buckets = {}
        for r in raw:
            bd = str(r.get("bill_date") or "")[:7]
            if not bd:
                continue
            b = buckets.setdefault(bd, [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            b[0] += 1
            b[1] += _f(r.get("total_amount"))
            b[2] += _f(r.get("discount"))
            b[3] += _f(r.get("cash_paid"))
            b[4] += _f(r.get("online_paid"))
            b[5] += _f(r.get("amount_paid"))
            b[6] += _f(r.get("due_amount"))
        if not buckets:
            showinfo("No Records", "No sales found."); return
        def fmt(ym):
            try: return datetime.strptime(ym,'%Y-%m').strftime('%b-%Y')
            except: return ym
        rows = [[fmt(k), v[0], f'{v[1]:.2f}', f'{v[2]:.2f}',
                 f'{v[3]:.2f}', f'{v[4]:.2f}', f'{v[5]:.2f}', f'{v[6]:.2f}']
                for k, v in sorted(buckets.items(), reverse=True)]
        rows.append(['TOTAL', sum(v[0] for v in buckets.values()),
                     f'{sum(v[1] for v in buckets.values()):.2f}',
                     f'{sum(v[2] for v in buckets.values()):.2f}',
                     f'{sum(v[3] for v in buckets.values()):.2f}',
                     f'{sum(v[4] for v in buckets.values()):.2f}',
                     f'{sum(v[5] for v in buckets.values()):.2f}',
                     f'{sum(v[6] for v in buckets.values()):.2f}'])
        export_table(parent, 'Monthly Sales Summary',
                     ['Month', 'Bills', 'Total Sales', 'Discount', 'Cash Paid', 'Online Paid',
                      'Amount Paid', 'Due Amount'],
                     rows, 'monthly_sales_summary', 'sales_history', 'monthly_summary')
        return
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
    if _online_mode():
        try:
            raw = _fetch_online_sales(fd, td)
        except Exception as exc:
            showwarning("Export", f"Could not load sales from server:\n{exc}")
            return
        buckets = {}
        for r in raw:
            bd = str(r.get("bill_date") or "")[:10]
            if not bd:
                continue
            b = buckets.setdefault(bd, [0, 0.0, 0.0, 0.0, 0.0, 0.0])
            b[0] += 1
            b[1] += _f(r.get("total_amount"))
            b[2] += _f(r.get("cash_paid"))
            b[3] += _f(r.get("online_paid"))
            b[4] += _f(r.get("amount_paid"))
            b[5] += _f(r.get("due_amount"))
        if not buckets:
            showinfo("No Records", "No sales found."); return
        rows = [[k, v[0], f'{v[1]:.2f}', f'{v[2]:.2f}',
                 f'{v[3]:.2f}', f'{v[4]:.2f}', f'{v[5]:.2f}']
                for k, v in sorted(buckets.items(), reverse=True)]
        export_table(parent, 'Daily Sales Summary',
                     ['Date', 'Bills', 'Total Amount', 'Cash Paid', 'Online Paid', 'Amount Paid', 'Due Amount'],
                     rows, 'daily_sales_summary', 'sales_history', 'daily_summary')
        return
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
    if _online_mode():
        try:
            raw = _fetch_online_sales("", "")
        except Exception as exc:
            showwarning("Export", f"Could not load sales from server:\n{exc}")
            return
        rows = []
        for r in raw:
            total_due = _f(r.get("total_due") or r.get("due_amount"))
            if total_due <= 0 or r.get("account_cleared"):
                continue
            rows.append((
                r.get("customer_name") or "",
                r.get("customer_phone") or r.get("phone") or "",
                r.get("bill_date") or "",
                r.get("bill_no") or "",
                _f(r.get("total_amount")),
                total_due,
            ))
        rows.sort(key=lambda x: float(x[5]), reverse=True)
        if not rows:
            showinfo("No Records", "No outstanding dues."); return
        export_table(parent, 'Customer Due Report',
                     ['Customer', 'Phone', 'Date', 'Bill No', 'Total Amount', 'Due Amount'],
                     rows, 'customer_due_report', 'sales_history', 'customer_due')
        return
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
    if _online_mode():
        # Bill-level only Online (no line items on list_sales).
        try:
            raw = _fetch_online_sales(fd, td)
        except Exception as exc:
            showwarning("Export", f"Could not load sales from server:\n{exc}")
            return
        rows = []
        for r in raw:
            doctor = (r.get("doctor_name") or "").strip()
            if not doctor:
                continue
            rows.append((
                doctor,
                r.get("bill_date") or "",
                r.get("customer_name") or "",
                r.get("bill_no") or "",
                "",
                "",
                "",
                "",
                f'{_f(r.get("total_amount")):.2f}',
            ))
        if not rows:
            showinfo("No Records", "No doctor-linked sales found."); return
        export_table(parent, 'Doctor-wise Sales',
                     ['Doctor', 'Date', 'Customer', 'Bill No', 'Medicine', 'Schedule', 'Qty', 'Rate', 'Amount'],
                     rows, 'doctor_wise_sales', 'sales_history', 'doctor_wise')
        return
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
    if _online_mode():
        try:
            raw = _fetch_online_sales(fd, td)
        except Exception as exc:
            showwarning("Export", f"Could not load sales from server:\n{exc}")
            return
        rows = []
        for r in raw:
            rows.append((
                r.get("bill_date") or "",
                r.get("bill_no") or "",
                r.get("customer_name") or "",
                _f(r.get("total_amount")),
                _f(r.get("cash_paid")),
                _f(r.get("online_paid")),
                _f(r.get("amount_paid")),
                _f(r.get("due_amount")),
            ))
        if not rows:
            showinfo("No Records", "No sales found."); return
        export_table(parent, 'Payment Mode Report',
                     ['Date', 'Bill No', 'Customer', 'Total Amount', 'Cash Paid', 'Online Paid',
                      'Amount Paid', 'Due Amount'],
                     rows, 'payment_mode_report', 'sales_history', 'payment_mode')
        return
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


def _fmt_schedule_expiry(raw):
    if not raw:
        return ""
    try:
        p = str(raw).split("-")
        return f"{p[2]}/{p[1]}/{p[0][2:]}" if len(p) == 3 else raw
    except Exception:
        return str(raw)


def _load_schedule_report_data(cursor, choice):
    """Query + column filter for schedule report. Returns dict or None if no rows."""
    from core.fy_serial import display_sales_bill_no

    fd = choice.get("from_date") or ""
    td = choice.get("to_date") or ""
    sch_label = choice.get("label", "All Schedules")
    q = """SELECT s.bill_date, s.bill_no, c.name, COALESCE(s.doctor_name,''),
                  m.name, COALESCE(m.batch_no,''), COALESCE(m.content_drug,''), COALESCE(m.schedule,''), m.expiry_date,
                  si.qty, si.rate, si.amount
           FROM sales s
           JOIN customers c   ON s.customer_id  = c.id
           JOIN sales_items si ON s.id           = si.sale_id
           JOIN medicines m   ON si.medicine_id  = m.id
           WHERE 1=1"""
    params = []
    if fd:
        q += " AND s.bill_date>=?"
        params.append(fd)
    if td:
        q += " AND s.bill_date<=?"
        params.append(td)
    sch_sql, sch_params = _schedule_sql_filter(choice)
    q += sch_sql
    params.extend(sch_params)
    q += " ORDER BY m.schedule, s.bill_date, c.name"
    cursor.execute(q, params)
    rows = cursor.fetchall()
    if not rows:
        return None

    headers = [
        "Date", "Bill No", "Customer", "Doctor", "Medicine", "Batch", "Content/Drug", "Schedule",
        "Expiry", "Qty", "Rate", "Amount",
    ]
    table_rows = []
    for r in rows:
        table_rows.append([
            r[0],
            display_sales_bill_no(str(r[1] or "")),
            r[2], r[3] or "—", r[4], r[5] or "—", r[6] or "—", r[7] or "—", _fmt_schedule_expiry(r[8]),
            int(r[9]) if r[9] else 0,
            f"{float(r[10] or 0):.2f}",
            f"{float(r[11] or 0):.2f}",
        ])
    headers, table_rows = filter_export_table(
        headers, table_rows, "sales_history", "schedule_report",
    )
    if not table_rows:
        return {"empty_columns": True}

    qty_idx = headers.index("Qty") if "Qty" in headers else None
    amt_idx = headers.index("Amount") if "Amount" in headers else None
    total_qty = sum(int(row[qty_idx]) for row in table_rows) if qty_idx is not None else 0
    total_amount = sum(float(row[amt_idx]) for row in table_rows) if amt_idx is not None else 0.0
    date_range = f"{fd} to {td}" if fd or td else "All Dates"
    safe_label = sch_label.replace(",", "_").replace(" ", "")

    return {
        "sch_label": sch_label,
        "date_range": date_range,
        "fd": fd,
        "td": td,
        "safe_label": safe_label,
        "headers": headers,
        "table_rows": table_rows,
        "qty_idx": qty_idx,
        "amt_idx": amt_idx,
        "total_qty": total_qty,
        "total_amount": total_amount,
    }


def _single_schedule_code(choice, sch_label: str) -> str | None:
    """When report is for one schedule only — used to drop Schedule column and title."""
    schedules = list(choice.get("schedules") or [])
    mode = choice.get("mode")
    if mode == "selected" and len(schedules) == 1:
        return str(schedules[0]).strip() or None
    label = (sch_label or "").strip()
    if label and "," not in label and label not in ("All Schedules", "Non-Scheduled"):
        if mode == "selected":
            return label
    return None


def _schedule_report_pdf_name(data: dict, page_layout: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "L" if (page_layout or "").startswith("land") else "P"
    return f"Schedule_Report_{data['safe_label']}_{data['fd']}_{data['td']}_{suffix}_{ts}"


def _run_schedule_report(parent, cursor, choice, *, do_print: bool) -> None:
    from core.document_output import (
        build_schedule_report_html,
        deliver_schedule_report_pdf,
        prepare_schedule_report_display,
        schedule_report_plain_from_display,
        schedule_report_title,
    )

    data = _load_schedule_report_data(cursor, choice)
    if data is None:
        showinfo("No Data", "No records found.", parent=parent)
        return
    if data.get("empty_columns"):
        showinfo(
            "No Data",
            "No columns selected for export. Enable columns in Settings → Appearance → "
            "Export report columns.",
            parent=parent,
        )
        return

    page_layout = choice.get("page_layout") or SCHEDULE_LAYOUT_PORTRAIT
    single = _single_schedule_code(choice, data["sch_label"])
    report_title = schedule_report_title(data["sch_label"], single_schedule=single)
    if single:
        subtitle = f"Period: {data['date_range']} | Records: {len(data['table_rows'])}"
    else:
        subtitle = (
            f"Schedule: {data['sch_label']} | Period: {data['date_range']} | "
            f"Records: {len(data['table_rows'])}"
        )

    html = build_schedule_report_html(
        sch_label=data["sch_label"],
        date_range=data["date_range"],
        headers=data["headers"],
        table_rows=data["table_rows"],
        total_qty=data["total_qty"],
        total_amount=data["total_amount"],
        qty_idx=data["qty_idx"],
        amt_idx=data["amt_idx"],
        page_layout=page_layout,
        single_schedule=single,
        report_title=report_title,
    )

    dot_matrix_fn = None
    if do_print:
        try:
            from core.printer_manager import PrinterManager
            if PrinterManager.is_dot_matrix_mode():
                display = prepare_schedule_report_display(
                    data["headers"],
                    data["table_rows"],
                    page_layout=page_layout,
                    total_qty=data["total_qty"],
                    total_amount=data["total_amount"],
                    qty_idx=data["qty_idx"],
                    amt_idx=data["amt_idx"],
                    single_schedule=single,
                )
                plain = schedule_report_plain_from_display(display)
                _title = report_title
                _subtitle = subtitle
                _layout = page_layout
                _dm_style = choice.get("dm_style")
                _dm_borders = choice.get("dm_borders")

                def dot_matrix_fn():
                    from core.dot_matrix_print import print_schedule_report_dot_matrix
                    print_schedule_report_dot_matrix(
                        _title, _subtitle, plain,
                        page_layout=_layout,
                        dm_style=_dm_style,
                        borders=_dm_borders,
                    )
        except Exception:
            dot_matrix_fn = None

    deliver_schedule_report_pdf(
        parent,
        html,
        _schedule_report_pdf_name(data, page_layout),
        do_print=do_print,
        page_layout=page_layout,
        dot_matrix_print=dot_matrix_fn,
    )


def export_schedule_report(parent, cursor, from_date_fn, to_date_fn, schedule_filter_fn):
    choice = ask_schedules_for_report(
        parent, schedule_filter_fn(), from_date_fn, to_date_fn,
    )
    if choice is None:
        return
    do_print = (choice.get("action") or "export") == "print"
    _run_schedule_report(parent, cursor, choice, do_print=do_print)


def run_sales_export_voice(report_id, runner, fmt="csv"):
    """Run a sales export function with voice — no picker dialogs."""
    import core.column_config as cc
    old = cc.export_table
    result = {"path": None, "msg": "Export finished."}

    def _patched(parent, title, headers, rows, filename, page_key, report_key):
        path, msg = cc.export_table_voice(
            parent, title, headers, rows, filename, page_key, report_key, fmt,
        )
        result["path"] = path
        result["msg"] = msg or "Export finished."
        return path

    cc.export_table = _patched
    try:
        runner()
    finally:
        cc.export_table = old
    return result["path"], result["msg"]


def export_schedule_report_voice(parent, cursor, from_date_fn, to_date_fn, choice, fmt="pdf"):
    """Schedule report for Satpuda — saves to documents/reports folder, no picker dialogs."""
    from core.column_config import filter_export_table
    from core.export_manager import export_data_direct
    from core.document_output import build_schedule_report_html, deliver_schedule_report_pdf

    if not choice:
        return None, "No schedule selected."

    if fmt in ("csv", "xlsx"):
        fd, td = from_date_fn(), to_date_fn()
        sch_label = choice.get("label", "All Schedules")
        data = _load_schedule_report_data(cursor, {
            **choice,
            "from_date": choice.get("from_date") or fd,
            "to_date": choice.get("to_date") or td,
            "label": sch_label,
        })
        if data is None:
            return None, "No records found for that schedule."
        if data.get("empty_columns"):
            return None, "No export columns enabled for schedule report."
        safe = sch_label.replace(",", "_").replace(" ", "_")[:40]
        return export_data_direct(
            parent, f"Schedule Report {sch_label}", data["headers"], data["table_rows"],
            f"schedule_report_{safe}", fmt,
        )

    data = _load_schedule_report_data(cursor, {
        **choice,
        "from_date": choice.get("from_date") or from_date_fn(),
        "to_date": choice.get("to_date") or to_date_fn(),
    })
    if data is None:
        return None, "No records found for that schedule."
    if data.get("empty_columns"):
        return None, "No export columns enabled for schedule report."

    page_layout = choice.get("page_layout") or load_schedule_report_layout()
    single = _single_schedule_code(choice, data["sch_label"])
    from core.document_output import schedule_report_title

    html = build_schedule_report_html(
        sch_label=data["sch_label"],
        date_range=data["date_range"],
        headers=data["headers"],
        table_rows=data["table_rows"],
        total_qty=data["total_qty"],
        total_amount=data["total_amount"],
        qty_idx=data["qty_idx"],
        amt_idx=data["amt_idx"],
        page_layout=page_layout,
        single_schedule=single,
        report_title=schedule_report_title(data["sch_label"], single_schedule=single),
    )
    do_print = str(fmt).lower() == "print"
    path = deliver_schedule_report_pdf(
        parent,
        html,
        _schedule_report_pdf_name(data, page_layout),
        do_print=do_print,
        page_layout=page_layout,
    )
    if not path:
        return None, "Could not save schedule report PDF."
    return path, f"Saved to {path}"
