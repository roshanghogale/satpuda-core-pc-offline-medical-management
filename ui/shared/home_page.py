"""
ui/home_page.py
───────────────
Dashboard / home page.
"""
import tkinter as tk
from datetime import datetime
import os
import sqlite3
import threading

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.scroll_manager import make_scrollable

from core.layout_config import get_home_banner_path, get_home_banner_size
from core.column_config import is_quick_access_visible, is_dashboard_section_visible
from core.home_dashboard import fy_bounds as _fy_bounds
from core.home_dashboard import query_dashboard_stats as _query_dashboard_stats


def _load_banner_pil_image():
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is not installed in this Python. "
            "Run: pip install Pillow ttkbootstrap"
        ) from exc
    banner_path = get_home_banner_path()
    if not banner_path or not os.path.isfile(banner_path):
        raise FileNotFoundError(f"Banner image missing: {banner_path}")
    target_w, target_h = get_home_banner_size()
    img = Image.open(banner_path)
    if target_h <= 0:
        ratio = target_w / max(img.width, 1)
        target_h = max(1, int(img.height * ratio))
    return img.resize((target_w, target_h), Image.LANCZOS)


def _apply_banner_image(banner_frame, pil_image):
    from PIL import ImageTk
    try:
        if not banner_frame.winfo_exists():
            return
    except tk.TclError:
        return
    photo = ImageTk.PhotoImage(pil_image, master=banner_frame)
    banner_lbl = tk.Label(banner_frame, image=photo, bd=0)
    banner_lbl.image = photo
    banner_lbl.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)


def _schedule_home_dashboard_load(main_frame, conn, inner, stat_value_labels, banner_frame, db_path=None):
    """Load stats + banner in background; apply on UI thread."""
    path = db_path
    if path is None:
        try:
            row = conn.execute("PRAGMA database_list").fetchone()
            path = row[2] if row else None
        except Exception:
            path = None

    def _worker():
        stats = None
        banner_img = None
        banner_error = None
        try:
            if path:
                bg = sqlite3.connect(path, check_same_thread=False)
                try:
                    stats = _query_dashboard_stats(bg)
                finally:
                    bg.close()
            else:
                stats = _query_dashboard_stats(conn)
        except Exception:
            stats = None
        try:
            banner_img = _load_banner_pil_image()
        except Exception as exc:
            banner_error = exc
        return stats, banner_img, banner_error

    def _apply(result):
        try:
            if not inner.winfo_exists():
                return
        except tk.TclError:
            return
        stats, banner_img, banner_error = result
        if stats and stat_value_labels:
            for lbl, val in zip(stat_value_labels, stats['values']):
                try:
                    lbl.configure(text=val)
                except Exception:
                    pass
        if banner_img is not None:
            try:
                if not banner_frame.winfo_exists():
                    return
            except tk.TclError:
                return
            for child in banner_frame.winfo_children():
                child.destroy()
            _apply_banner_image(banner_frame, banner_img)
        elif banner_error is not None:
            for child in banner_frame.winfo_children():
                child.destroy()
            fb = ttk.Frame(banner_frame)
            fb.pack(fill=tk.X, padx=8, pady=8)
            ttk.Label(
                fb,
                text=f'Banner error: {banner_error}',
                font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
                foreground='red',
                wraplength=520,
                justify=tk.LEFT,
            ).pack()

    def _run():
        result = _worker()
        try:
            main_frame.after(0, lambda: _apply(result))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name='HomeDashboardLoad').start()


def warm_home_dashboard_during_splash(main_frame, conn, inner, stat_value_labels, banner_frame,
                                       db_path=None, pump_fn=None):
    """Load dashboard stats + banner during splash (not after the app opens)."""
    import queue
    import threading
    import time

    path = db_path
    if path is None:
        try:
            row = conn.execute("PRAGMA database_list").fetchone()
            path = row[2] if row else None
        except Exception:
            path = None

    def _worker():
        stats = None
        banner_img = None
        banner_error = None
        try:
            if path:
                bg = sqlite3.connect(path, check_same_thread=False)
                try:
                    stats = _query_dashboard_stats(bg)
                finally:
                    bg.close()
            else:
                stats = _query_dashboard_stats(conn)
        except Exception:
            stats = None
        try:
            banner_img = _load_banner_pil_image()
        except Exception as exc:
            banner_error = exc
        return stats, banner_img, banner_error

    result_q = queue.Queue()

    def _run():
        try:
            result_q.put(_worker())
        except Exception:
            result_q.put((None, None, None))

    threading.Thread(target=_run, daemon=True, name='HomeDashboardSplash').start()
    while True:
        try:
            result = result_q.get_nowait()
            break
        except queue.Empty:
            if pump_fn:
                try:
                    pump_fn()
                except Exception:
                    break
            else:
                time.sleep(0.02)

    stats, banner_img, banner_error = result
    if stats and stat_value_labels:
        for lbl, val in zip(stat_value_labels, stats['values']):
            try:
                lbl.configure(text=val)
            except Exception:
                pass
    if banner_img is not None:
        try:
            if banner_frame.winfo_exists():
                for child in banner_frame.winfo_children():
                    child.destroy()
                _apply_banner_image(banner_frame, banner_img)
        except Exception:
            pass
    elif banner_error is not None:
        try:
            if banner_frame.winfo_exists():
                for child in banner_frame.winfo_children():
                    child.destroy()
                fb = ttk.Frame(banner_frame)
                fb.pack(fill=tk.X, padx=8, pady=8)
                ttk.Label(
                    fb,
                    text=f'Banner error: {banner_error}',
                    font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
                    foreground='red',
                    wraplength=520,
                    justify=tk.LEFT,
                ).pack()
        except Exception:
            pass


def build_home(main_frame, conn, nav_click_fn, open_billing_fn,
               open_purchase_fn, open_inventory_fn, open_contacts_fn,
               open_ledger_fn, open_alerts_fn, open_general_products_fn,
               input_ctrl, register_canvas_fn,
               db_path=None):
    """Build and pack the home dashboard into main_frame."""
    inner = make_scrollable(main_frame)
    inner.configure(padding=(10, 10))
    app = getattr(main_frame.winfo_toplevel(), '_main_app', None)
    if app is not None:
        app._home_inner_frame = inner

    today     = datetime.now().date()

    # ── Stats bar (footer) ───────────────────────────────────────────────
    stats_frame = None
    if is_dashboard_section_visible('home_dashboard'):
        stats_frame = ttk.LabelFrame(inner, text='📊 Dashboard')

    if stats_frame is not None:
        month_start, fy_start, fy_end, fy_label = _fy_bounds(today)

        today_cols = [
            ('Today Sales',     '…'),
            ('Today Collected', '…'),
            ('Today Bills',     '…'),
            ('Customer Due',    '…'),
            ('Supplier Due',    '…'),
            ('Stock Value',     '…'),
        ]
        month_year_cols = [
            ('Month Sales',                 '…'),
            ('Month Collected',             '…'),
            ('Month Bills',                 '…'),
            (f'Year Sales ({fy_label})',    '…'),
            (f'Year Collected ({fy_label})','…'),
            (f'Year Bills ({fy_label})',    '…'),
        ]

        stat_value_labels = []

        for i, (label, value) in enumerate(today_cols):
            sf = ttk.Frame(stats_frame)
            sf.grid(row=0, column=i, padx=12, pady=(6, 2), sticky='ew')
            stats_frame.grid_columnconfigure(i, weight=1)
            vl = ttk.Label(sf, text=value, font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold'))
            vl.pack()
            stat_value_labels.append(vl)
            ttk.Label(sf, text=label, font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack()

        ttk.Separator(stats_frame, orient='horizontal').grid(
            row=1, column=0, columnspan=6, sticky='ew', padx=8, pady=2)

        for i, (label, value) in enumerate(month_year_cols):
            sf = ttk.Frame(stats_frame)
            sf.grid(row=2, column=i, padx=12, pady=(2, 6), sticky='ew')
            vl = ttk.Label(sf, text=value, font=(FONT_FAMILY, FONT_SIZE_BUTTONS, 'bold'))
            vl.pack()
            stat_value_labels.append(vl)
            ttk.Label(sf, text=label, font=(FONT_FAMILY, FONT_SIZE_DEFAULT - 1)).pack()

        def _refresh_stats():
            try:
                stats = _query_dashboard_stats(conn, today)
                for lbl, val in zip(stat_value_labels, stats['values']):
                    lbl.configure(text=val)
            except Exception:
                pass

        inner._home_stat_refresh = _refresh_stats

    # ── Main body: Quick Actions (left column) + Banner (right) ──────────
    body = ttk.Frame(inner)
    body.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

    qa_frame = ttk.LabelFrame(body, text='Quick Actions')
    qa_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
    btn_row = ttk.Frame(qa_frame)          # vertical column of buttons
    btn_row.pack(fill=tk.Y, padx=8, pady=6)

    def _export(kind):
        from ui.settings.settings_tabs.database_tab import DatabaseTab
        import tkinter as tk
        # Create a hidden notebook just to satisfy DatabaseTab's constructor
        _nb = ttk.Notebook(main_frame)
        db = DatabaseTab(_nb, conn, main_frame)
        getattr(db, f'export_{kind}')()
        _nb.destroy()

    def _make_btn(parent, text, cmd, style, key):
        if not is_quick_access_visible(key):
            return None
        try:
            b = ttk.Button(parent, text=text, command=cmd,
                           bootstyle=style, width=22)
        except Exception:
            b = ttk.Button(parent, text=text, command=cmd, width=22)
        b.pack(fill=tk.X, pady=2)
        return b

    def _sep_if_needed():
        ttk.Separator(btn_row, orient='horizontal').pack(fill=tk.X, pady=4)

    primary_btns = [
        _make_btn(btn_row, '➕ New Bill', open_billing_fn, 'success', 'new_bill'),
        _make_btn(btn_row, '📦 New Purchase', open_purchase_fn, 'primary', 'new_purchase'),
        _make_btn(btn_row, '🔍 Search Medicine', open_inventory_fn, 'info', 'search_medicine'),
        _make_btn(btn_row, '👤 Contacts', open_contacts_fn, 'secondary', 'contacts'),
        _make_btn(btn_row, '📊 Ledger', open_ledger_fn, 'danger', 'ledger'),
    ]
    export_btns = [
        _make_btn(btn_row, '📊 Export Sales', lambda: _export('sales'), 'success', 'export_sales'),
        _make_btn(btn_row, '📦 Export Purchases', lambda: _export('purchases'), 'primary', 'export_purchases'),
        _make_btn(btn_row, '🗃 Export Inventory', lambda: _export('inventory'), 'info', 'export_inventory'),
        _make_btn(btn_row, '📁 Export All', lambda: _export('all'), 'warning', 'export_all'),
    ]
    if any(primary_btns) and any(export_btns):
        _sep_if_needed()
    alerts_btn = _make_btn(btn_row, '🔔 Alerts', open_alerts_fn, 'warning', 'alerts')
    if (any(primary_btns) or any(export_btns)) and alerts_btn:
        _sep_if_needed()
    general_btn = _make_btn(
        btn_row, '🏷 General Products', open_general_products_fn, 'info', 'general_products',
    )
    if not any(primary_btns + export_btns + [alerts_btn, general_btn]):
        ttk.Label(
            btn_row,
            text='No quick actions enabled.\nEnable buttons in Settings → Appearance.',
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        ).pack(pady=8)

    # ── Banner (fills remaining space to the right) ───────────────────────
    banner_frame = ttk.LabelFrame(body, text='')
    banner_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    banner_placeholder = ttk.Label(
        banner_frame,
        text='Loading banner…',
        font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
    )
    banner_placeholder.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    stat_labels_for_async = stat_value_labels if stats_frame is not None else []
    prewarm = getattr(main_frame.winfo_toplevel(), '_startup_prewarm', False)
    if prewarm and app is not None:
        app._home_warm = {
            'main_frame': main_frame,
            'inner': inner,
            'stat_value_labels': stat_labels_for_async,
            'banner_frame': banner_frame,
            'db_path': db_path,
        }
    elif not prewarm:
        _schedule_home_dashboard_load(
            main_frame, conn, inner, stat_labels_for_async, banner_frame,
            db_path=db_path,
        )

    # ── Register canvas ───────────────────────────────────────────────────
    # Pack stats footer last so it appears at the bottom
    if stats_frame is not None:
        stats_frame.pack(fill=tk.X, pady=(8, 0))

    inner.update_idletasks()
    if hasattr(inner, '_canvas'):
        inner._canvas.configure(scrollregion=inner._canvas.bbox('all'))

    def _export_menu_dialog():
        from core.export_manager import show_export_option_dialog
        show_export_option_dialog(main_frame, 'Export Data', [
            ('Export Sales', lambda: _export('sales')),
            ('Export Purchases', lambda: _export('purchases')),
            ('Export Inventory', lambda: _export('inventory')),
            ('Export All', lambda: _export('all')),
        ], width=420)

    def _register():
        app = getattr(main_frame.winfo_toplevel(), '_main_app', None)
        if app is not None and getattr(app, 'active_nav', None) != '🏠 Home':
            return
        try:
            if not inner.winfo_exists() or not inner.winfo_ismapped():
                return
        except tk.TclError:
            return
        try:
            register_canvas_fn(inner)
        except Exception:
            pass
        from core.keyboard_registry import KeyboardRegistry, PageBindings
        qa_buttons = [b for b in primary_btns + export_btns + [alerts_btn, general_btn] if b]
        bindings = PageBindings(
            page_id='home',
            first_focus=lambda: qa_buttons[0].focus_set() if qa_buttons else None,
            on_ctrl_e=_export_menu_dialog,
            sub_keys={
                'b': open_billing_fn,
                'p': open_purchase_fn,
                'i': open_inventory_fn,
                'e': _export_menu_dialog,
            },
            f2_target=lambda: qa_buttons[0].focus_set() if qa_buttons else None,
        )
        inner._keyboard_bindings = bindings
        app = getattr(main_frame.winfo_toplevel(), '_main_app', None)
        if app is not None:
            app._home_keyboard_bindings = bindings
        if app is None or getattr(app, 'active_nav', None) == '🏠 Home':
            KeyboardRegistry.register_page(inner, bindings)

    _register()
    if not getattr(main_frame.winfo_toplevel(), '_startup_prewarm', False):
        main_frame.after(50, _register)
        main_frame.after(500, _register)


def refresh_home_dashboard(container, conn):
    """Update dashboard numbers when re-showing the cached home page."""
    app = getattr(container.winfo_toplevel(), '_main_app', None)
    inner = getattr(app, '_home_inner_frame', None) if app is not None else None
    if inner is None:
        return

    def _worker():
        try:
            return _query_dashboard_stats(conn)
        except Exception:
            return None

    def _apply(stats):
        if stats is None:
            return
        refresh = getattr(inner, '_home_stat_refresh', None)
        if refresh is not None:
            try:
                refresh()
            except Exception:
                pass

    def _run():
        stats = _worker()
        try:
            container.after(0, lambda: _apply(stats))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name='HomeDashboardRefresh').start()
