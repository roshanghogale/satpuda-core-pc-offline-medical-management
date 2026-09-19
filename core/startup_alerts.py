import tkinter as tk
from tkinter import filedialog
from datetime import date
import sqlite3
import threading

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.themed_messagebox import showinfo, showerror

_voice_cancel_all_fn = None

_ALERT_WINDOW_WIDTH = 1000
_ALERT_WINDOW_HEIGHT = 720
_ALERT_TREE_HEIGHT = 14
_DISPLAY_ROW_LIMIT = 400
_TAB_LABELS = {
    "Low Stock Alerts": "Low Stock",
    "Out of Stock": "Out of Stock",
    "Near Expiry Alerts": "Near Expiry",
    "Expired Medicines": "Expired",
    "Customer Due Alerts": "Customer Due",
}

# Per-tab row action: reorder, return, or none
# _TAB_ACTIONS now lives with the gatherer; imported below.


def register_voice_cancel_all(callback):
    global _voice_cancel_all_fn
    _voice_cancel_all_fn = callback


def voice_cancel_all_alerts() -> bool:
    fn = _voice_cancel_all_fn
    if callable(fn):
        fn()
        return True
    return False


def _center_alert_window(win):
    from core.scroll_manager import ensure_toplevel_fits_screen
    win.update_idletasks()
    ensure_toplevel_fits_screen(
        win, width=_ALERT_WINDOW_WIDTH, height=_ALERT_WINDOW_HEIGHT, resizable=True,
    )


def _restore_nav_after_alerts(root):
    try:
        from core.keyboard_registry import KeyboardRegistry
        KeyboardRegistry.finish_modal_session(defer_refresh=True, blur=False)
    except Exception:
        pass


def _schedule_deferred_alert_destroy(root, win):
    """Destroy alert window later — win.destroy() on large trees blocks navigation."""
    def _destroy():
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass

    try:
        root.after(8000, _destroy)
    except Exception:
        pass


def _parse_expiry(raw):
    from core.alert_thresholds import parse_expiry
    return parse_expiry(raw)


def _load_thresholds(conn):
    from core.alert_thresholds import load_thresholds
    return load_thresholds(conn)


# The gathering moved to core.startup_alerts_data so the headless desktop
# engine can use it without pulling in tkinter. Re-exported here so the
# classic UI keeps its existing imports.
from core.startup_alerts_data import (  # noqa: F401
    _TAB_ACTIONS,
    _collect_alert_data,
    collect_startup_alerts,
)


def _row_values(row, col_count):
    vals = [str(v) if v is not None else "" for v in row]
    while len(vals) < col_count:
        vals.append("")
    return tuple(vals[:col_count])


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


def _resolve_app(root):
    return getattr(root, "_main_app", None) or getattr(root, "_app_instance", None)


def _prefill_reorder_from_startup(title: str, values: tuple) -> dict:
    if title == "Low Stock Alerts":
        name = str(values[0] or "")
        med_type = str(values[1] or "Others")
        pack = str(values[4] or "")
        stock = float(values[3] or 0)
        return {
            "medicine_name": name,
            "pack_size": pack,
            "med_type": med_type,
            "current_stock": stock,
        }
    name = str(values[0] or "")
    pack = str(values[1] or "")
    rate = float(values[3] or 0)
    med_type = str(values[4] or "Others")
    return {
        "medicine_name": name,
        "pack_size": pack,
        "med_type": med_type,
        "unit_price": rate,
    }


def _prefill_disposal_from_startup(title: str, values: tuple, conn) -> dict:
    name = str(values[0] or "")
    batch = str(values[2] or "")
    expiry = str(values[3] or "")
    qty = 0.0
    if conn is not None:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(stock_qty),0) FROM medicines "
                "WHERE name=? AND COALESCE(batch_no,'')=?",
                (name, batch),
            )
            row = cur.fetchone()
            qty = float(row[0] or 0) if row else 0.0
        except Exception:
            pass
    return {
        "from_alert": True,
        "medicine_name": name,
        "batch_no": batch,
        "expiry_date": expiry,
        "available_qty": qty,
    }


def _run_tab_action(root, win, alert, tree):
    kind = alert.get("action_kind")
    if not kind:
        return
    sel = tree.selection()
    if not sel:
        showinfo("Action", "Select a row first.", parent=win)
        return
    values = tuple(tree.item(sel[0]).get("values") or ())
    app = _resolve_app(root)
    if app is None:
        showerror("Action", "Could not open page — restart the app.", parent=win)
        return
    title = alert.get("title", "")
    if kind == "reorder":
        prefill = _prefill_reorder_from_startup(title, values)
        conn = getattr(app, "conn", None)
        if conn is not None:
            from core.reorder_service import (
                current_stock_for_medicine,
                min_stock_level,
                suggest_order_quantity,
            )
            name = prefill.get("medicine_name", "")
            pack = prefill.get("pack_size", "")
            med_type = prefill.get("med_type", "Others")
            if "current_stock" not in prefill or title == "Out of Stock":
                stock = current_stock_for_medicine(conn, name, pack)
                prefill["current_stock"] = stock
            prefill.setdefault("min_stock", min_stock_level(conn, med_type))
            prefill.setdefault(
                "suggested_qty",
                suggest_order_quantity(
                    conn, name, med_type, float(prefill.get("current_stock") or 0), pack),
            )
        try:
            win.withdraw()
        except Exception:
            pass
        if hasattr(app, "open_reorder"):
            app.open_reorder(prefill)
    elif kind == "return":
        conn = getattr(app, "conn", None)
        try:
            win.withdraw()
        except Exception:
            pass
        if conn is not None and hasattr(app, "open_returns"):
            from core.stock_disposal_service import (
                build_bulk_return_by_purchase, collect_return_candidates,
            )
            name = (values[0] or "").strip() if values else ""
            batch = (values[1] or "").strip() if len(values) > 1 else ""
            items = [
                i for i in collect_return_candidates(
                    conn, include_expired=True, include_near_expiry=True)
                if (i.get("medicine_name") or "").strip() == name
                and (not batch or (i.get("batch_no") or "").strip() == batch)
            ]
            data = build_bulk_return_by_purchase(conn, items=items)
            if data.get("purchase_groups") or data.get("writeoff_lines"):
                app.open_returns(kind="purchase", prefill=data)
            else:
                prefill = _prefill_disposal_from_startup(title, values, conn)
                app.open_stock_disposal(prefill=prefill)
        return


def _bulk_reorder_from_alert(root, win):
    try:
        win.withdraw()
    except Exception:
        pass
    app = _resolve_app(root)
    if app and hasattr(app, "open_reorder"):
        app.open_reorder(bulk=True)


def _bulk_return_from_alert(root, win, *, include_expired=True, include_near_expiry=True):
    try:
        win.withdraw()
    except Exception:
        pass
    app = _resolve_app(root)
    if app and hasattr(app, "open_stock_disposal"):
        app.open_stock_disposal(
            bulk=True, include_expired=include_expired, include_near_expiry=include_near_expiry)
    elif app and getattr(app, "conn", None):
        from core.stock_disposal_service import run_bulk_return_by_supplier
        run_bulk_return_by_supplier(app.conn, parent=win)


def _build_tree_tab(parent, alert, root, win):
    """One tab: Treeview with optional per-tab action button."""
    cols = alert["columns"]
    frame = ttk.Frame(parent)
    frame.pack(fill=tk.BOTH, expand=True)

    tree = ttk.Treeview(frame, columns=cols, show="headings", height=_ALERT_TREE_HEIGHT)
    vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
    hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    frame.grid_rowconfigure(0, weight=1)
    frame.grid_columnconfigure(0, weight=1)

    widths = {
        "Medicine": 220, "Customer": 200, "Type": 90, "Batch": 90, "Pack": 80,
        "Stock": 70, "Unit": 60, "Expiry": 90, "Days Left": 80, "MRP": 70,
        "Rate": 70, "Days Expired": 90, "Phone": 110, "Due Amount": 90,
        "Supplier": 140,
    }
    for c in cols:
        tree.heading(c, text=c)
        tree.column(c, width=widths.get(c, 120), anchor=tk.W, stretch=True)

    rows = alert["rows"]
    display = rows[:_DISPLAY_ROW_LIMIT] if len(rows) > _DISPLAY_ROW_LIMIT else rows
    col_count = len(cols)
    for row in display:
        try:
            tree.insert("", tk.END, values=_row_values(row, col_count))
        except tk.TclError:
            pass

    row_idx = 2
    if len(rows) > len(display):
        note = ttk.Label(
            frame,
            text=f"Showing first {len(display)} of {len(rows)} — Export PDF for full list.",
            foreground="#666",
        )
        note.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        row_idx = 3

    action_kind = alert.get("action_kind")
    action_label = alert.get("action_label")
    if action_kind and action_label:
        act = ttk.Frame(frame)
        act.grid(row=row_idx, column=0, columnspan=2, sticky=tk.EW, pady=(8, 4))
        act.grid_columnconfigure(0, weight=1)

        def _on_action(_e=None):
            _run_tab_action(root, win, alert, tree)

        btn_specs = [{
            "text": action_label,
            "command": _on_action,
            "bootstyle": "primary-outline" if action_kind == "reorder" else "warning-outline",
        }]
        title = alert.get("title", "")
        if title in ("Low Stock Alerts", "Out of Stock"):
            btn_specs.append({
                "text": "Reorder by Supplier",
                "command": lambda: _bulk_reorder_from_alert(root, win),
                "bootstyle": "success-outline",
            })
        elif title in ("Near Expiry Alerts", "Expired Medicines"):
            btn_specs.append({
                "text": "Return by Purchase",
                "command": lambda: _bulk_return_from_alert(
                    root, win, include_expired=True, include_near_expiry=True),
                "bootstyle": "success-outline",
            })

        from core.scroll_manager import pack_centered_buttons
        pack_centered_buttons(act, btn_specs, pady=0)

        tree.bind("<Double-1>", _on_action)

    return frame


def _show_alerts_dialog(root, alerts, *, start_hidden=False, progress_cb=None, on_closed=None):
    if not alerts:
        _restore_nav_after_alerts(root)
        return None

    win = tk.Toplevel(root)
    if start_hidden:
        try:
            win.withdraw()
        except Exception:
            pass
    win.title("Startup Alerts")
    state = {"closed": False, "progress_after_id": None}
    tab_alerts = list(alerts)

    def _cancel_progress_schedule():
        aid = state.get("progress_after_id")
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
            state["progress_after_id"] = None

    def _close():
        if state["closed"]:
            return
        state["closed"] = True
        _cancel_progress_schedule()
        from core.splash_screen import stop_post_alert_loading
        stop_post_alert_loading(root)
        register_voice_cancel_all(None)
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.withdraw()
        except Exception:
            pass
        _restore_nav_after_alerts(root)
        _schedule_deferred_alert_destroy(root, win)
        if callable(on_closed):
            try:
                on_closed()
            except Exception:
                pass

    try:
        from core.window_icon import apply_window_icon
        apply_window_icon(win, master=root)
    except Exception:
        pass

    wrap = ttk.Frame(win)
    wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    wrap.grid_rowconfigure(1, weight=1)
    wrap.grid_columnconfigure(0, weight=1)

    total_records = sum(len(a["rows"]) for a in tab_alerts)
    ttk.Label(
        wrap,
        text=f"Startup alerts ({len(tab_alerts)} categories, {total_records} records)",
        font=("Segoe UI", 11, "bold"),
    ).grid(row=0, column=0, sticky=tk.W, pady=(0, 8))

    notebook = ttk.Notebook(wrap)
    notebook.grid(row=1, column=0, sticky="nsew")

    tab_frames = []
    built_tabs = set()

    for alert in tab_alerts:
        tab = ttk.Frame(notebook)
        short = _TAB_LABELS.get(alert["title"], alert["title"])
        notebook.add(tab, text=f"{short} ({len(alert['rows'])})")
        tab_frames.append(tab)

    def _ensure_tab_built(index: int):
        if index < 0 or index >= len(tab_alerts) or index in built_tabs:
            return
        alert = tab_alerts[index]
        if progress_cb:
            try:
                short = _TAB_LABELS.get(alert["title"], alert["title"])
                progress_cb(f"Preparing {short}…")
            except Exception:
                pass
        _build_tree_tab(tab_frames[index], alert, root, win)
        built_tabs.add(index)

    def _on_tab_changed(_event=None):
        try:
            idx = notebook.index(notebook.select())
        except Exception:
            idx = 0
        _ensure_tab_built(int(idx))

    notebook.bind("<<NotebookTabChanged>>", _on_tab_changed, add="+")
    _ensure_tab_built(0)

    bf = ttk.Frame(wrap)
    bf.grid(row=2, column=0, sticky=tk.EW, pady=(12, 0))

    def _skip_today():
        from core.startup_alerts_prefs import snooze_startup_alerts_for_today
        snooze_startup_alerts_for_today()
        _close()

    def _export_current():
        try:
            idx = notebook.index(notebook.select())
        except Exception:
            idx = 0
        alert = tab_alerts[int(idx)]
        try:
            if _export_alert_pdf(alert["title"], alert["columns"], alert["rows"]):
                showinfo("Export", "PDF exported successfully.", parent=win)
        except Exception as e:
            showerror("Export", f"Failed to export PDF:\n{e}", parent=win)

    from core.scroll_manager import pack_centered_buttons
    pack_centered_buttons(bf, [
        {"text": "Close", "command": _close},
        {"text": "Skip Today", "command": _skip_today},
        {"text": "Export PDF (current tab)", "command": _export_current},
    ], pady=0)

    register_voice_cancel_all(_close)
    win.protocol("WM_DELETE_WINDOW", _close)
    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(win, on_close=_close)

    def _present():
        if state["closed"]:
            return
        try:
            _center_alert_window(win)
        except Exception:
            pass
        try:
            from core.window_icon import show_modal_toplevel
            show_modal_toplevel(win, root)
        except Exception:
            win.transient(root)
            win.lift()
            win.focus_force()
            win.grab_set()
        _wait_for_dialog_then_start_progress()

    def _dialog_is_visible() -> bool:
        try:
            return (
                bool(win.winfo_ismapped())
                and bool(win.winfo_viewable())
                and int(win.winfo_width()) > 1
                and int(win.winfo_height()) > 1
            )
        except Exception:
            return False

    def _wait_for_dialog_then_start_progress(attempt: int = 0):
        if state["closed"]:
            return
        if not _dialog_is_visible():
            if attempt < 80:
                state["progress_after_id"] = root.after(
                    50, lambda: _wait_for_dialog_then_start_progress(attempt + 1),
                )
            return
        try:
            win.update_idletasks()
            root.update_idletasks()
        except Exception:
            pass
        state["progress_after_id"] = root.after(120, _start_fullscreen_progress)

    def _start_fullscreen_progress():
        if state["closed"]:
            return
        if not _dialog_is_visible():
            _wait_for_dialog_then_start_progress()
            return
        from core.splash_screen import trigger_post_alert_loading
        trigger_post_alert_loading(root, alert_window=win)

    def _dismiss_hidden():
        if state["closed"]:
            return
        state["closed"] = True
        _cancel_progress_schedule()
        register_voice_cancel_all(None)
        _schedule_deferred_alert_destroy(root, win)

    _present.dismiss_hidden = _dismiss_hidden  # type: ignore[attr-defined]

    if not start_hidden:
        _present()
    return _present


def show_startup_alerts_from_cache(root, alerts, on_closed=None):
    """Show pre-loaded alerts immediately (no second loading dialog)."""
    _show_alerts_dialog(root, alerts, on_closed=on_closed)


def prepare_startup_alerts_dialog(root, alerts, progress_cb=None, on_closed=None):
    """Build alert dialog hidden during splash; call returned function to show it."""
    if not alerts:
        return None
    return _show_alerts_dialog(
        root, alerts, start_hidden=True, progress_cb=progress_cb, on_closed=on_closed,
    )


def show_startup_alerts(root, conn, db_path=None):
    """Fallback: load alerts in background if not pre-loaded during splash."""
    session = {"cancelled": False}

    def _worker():
        try:
            return collect_startup_alerts(conn, db_path)
        except Exception:
            return []

    def _on_ready(alerts):
        if session["cancelled"]:
            _restore_nav_after_alerts(root)
            return
        show_startup_alerts_from_cache(root, alerts)

    def _run():
        alerts = _worker()
        if session["cancelled"]:
            return
        try:
            root.after(0, lambda: _on_ready(alerts))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="StartupAlertsCollect").start()
