import tkinter as tk
from tkinter import filedialog
from datetime import date, datetime

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.themed_messagebox import showinfo, showerror

# Startup alert dialog size (shown once after app opens)
_ALERT_WINDOW_WIDTH = 1000
_ALERT_WINDOW_HEIGHT = 920
_ALERT_TREE_ROWS = 14


def _center_alert_window(win):
    from core.scroll_manager import ensure_toplevel_fits_screen
    win.update_idletasks()
    ensure_toplevel_fits_screen(
        win, width=_ALERT_WINDOW_WIDTH, height=_ALERT_WINDOW_HEIGHT, resizable=True,
    )


def _parse_expiry(raw):
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%y", "%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            if fmt in ("%m/%y", "%m/%Y"):
                # Month-level expiry: treat as last day of month.
                if parsed.month == 12:
                    return parsed.replace(day=31)
                nxt = parsed.replace(month=parsed.month + 1, day=1)
                return nxt.fromordinal(nxt.toordinal() - 1)
            return parsed
        except Exception:
            continue
    return None


def _load_thresholds(conn):
    low_defaults = {}
    near_defaults = {}
    cur = conn.cursor()
    try:
        cur.execute("SELECT name, value FROM settings")
        for name, value in cur.fetchall():
            if name.startswith("low_stock_"):
                med_type = name.replace("low_stock_", "", 1).lower()
                try:
                    low_defaults[med_type] = float(value)
                except Exception:
                    low_defaults[med_type] = 10.0
            elif name.startswith("near_expiry_"):
                med_type = name.replace("near_expiry_", "", 1).lower()
                try:
                    near_defaults[med_type] = float(value)
                except Exception:
                    near_defaults[med_type] = 3.0
    except Exception:
        pass
    return low_defaults, near_defaults


def _collect_alert_data(conn):
    cur = conn.cursor()
    low_thr, near_thr = _load_thresholds(conn)
    low_stock = []
    near_expiry = []
    expired = []
    customer_due = []

    cur.execute("""
        SELECT name, COALESCE(type,''), COALESCE(batch_no,''), COALESCE(expiry_date,''),
               COALESCE(stock_qty,0), COALESCE(unit,'')
        FROM medicines
        ORDER BY name COLLATE NOCASE
    """)
    today = date.today()
    for name, med_type, batch_no, expiry_raw, stock_qty, unit in cur.fetchall():
        mtype = (med_type or "").lower()
        qty = float(stock_qty or 0)
        if qty <= low_thr.get(mtype, 10.0):
            low_stock.append((name, med_type, batch_no, qty, unit))

        expiry_dt = _parse_expiry(expiry_raw)
        if not expiry_dt:
            continue
        days_left = (expiry_dt - today).days
        if days_left < 0:
            expired.append((name, med_type, batch_no, expiry_raw, abs(days_left)))
            continue
        months = near_thr.get(mtype, 3.0)
        if days_left <= int(months * 30):
            near_expiry.append((name, med_type, batch_no, expiry_raw, days_left))

    cur.execute("""
        SELECT name, COALESCE(phone,''), COALESCE(total_due,0)
        FROM customers
        WHERE COALESCE(total_due,0) > 0
        ORDER BY total_due DESC, name COLLATE NOCASE
    """)
    customer_due = [(n, p, round(float(d or 0), 2)) for n, p, d in cur.fetchall()]

    return [
        {
            "title": "Low Stock Alerts",
            "columns": ["Medicine", "Type", "Batch", "Stock", "Unit"],
            "rows": low_stock,
        },
        {
            "title": "Near Expiry Alerts",
            "columns": ["Medicine", "Type", "Batch", "Expiry", "Days Left"],
            "rows": near_expiry,
        },
        {
            "title": "Expired Medicines",
            "columns": ["Medicine", "Type", "Batch", "Expiry", "Days Expired"],
            "rows": expired,
        },
        {
            "title": "Customer Due Alerts",
            "columns": ["Customer", "Phone", "Due Amount"],
            "rows": customer_due,
        },
    ]


def _export_alert_pdf(title, columns, rows):
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except Exception as e:
        raise RuntimeError(f"reportlab unavailable: {e}")

    filename = filedialog.asksaveasfilename(
        defaultextension=".pdf",
        filetypes=[("PDF files", "*.pdf")],
        initialfile=f"{title.lower().replace(' ', '_')}.pdf",
        title=f"Export {title} as PDF",
    )
    if not filename:
        return False

    doc = SimpleDocTemplate(filename, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 8)]
    data = [columns] + [list(map(str, r)) for r in rows]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f3e46")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
    ]))
    story.append(table)
    doc.build(story)
    return True


def show_startup_alerts(root, conn):
    alerts = [a for a in _collect_alert_data(conn) if a["rows"]]
    if not alerts:
        return

    index = 0
    cancel_all = {"value": False}

    while index < len(alerts):
        if cancel_all["value"]:
            break
        alert = alerts[index]
        done = {"action": None}

        win = tk.Toplevel(root)
        win.title(alert["title"])
        win.minsize(800, 680)
        try:
            from core.window_icon import apply_window_icon
            apply_window_icon(win, master=root)
        except Exception:
            pass

        wrap = ttk.Frame(win)
        wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Buttons pinned to bottom so they stay visible on all screen sizes
        bf = ttk.Frame(wrap)
        bf.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))

        ttk.Label(
            wrap,
            text=f"{alert['title']} ({len(alert['rows'])} records)",
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.TOP, anchor=tk.W, pady=(0, 8))

        tree_frame = ttk.Frame(wrap)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        cols = alert["columns"]
        tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings", height=_ALERT_TREE_ROWS,
        )
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=160, anchor=tk.W)
        for row in alert["rows"]:
            tree.insert("", tk.END, values=row)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        def _next():
            done["action"] = "next"
            win.destroy()

        def _cancel():
            cancel_all["value"] = True
            done["action"] = "cancel"
            win.destroy()

        def _export():
            try:
                if _export_alert_pdf(alert["title"], alert["columns"], alert["rows"]):
                    showinfo("Export", "PDF exported successfully.", parent=win)
            except Exception as e:
                showerror("Export", f"Failed to export PDF:\n{e}", parent=win)

        ttk.Button(bf, text="Cancel All", command=_cancel).pack(side=tk.LEFT)
        ttk.Button(bf, text="Export PDF", command=_export).pack(side=tk.LEFT, padx=8)
        ttk.Button(bf, text="Next", command=_next).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", _next)
        _center_alert_window(win)
        try:
            from core.window_icon import show_modal_toplevel
            show_modal_toplevel(win, root)
        except Exception:
            win.transient(root)
            win.lift()
            win.focus_force()
            win.grab_set()
        root.wait_window(win)
        if done["action"] in ("next", None):
            index += 1
