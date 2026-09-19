"""Print All Bills - date/day/month/year picker; always A6 single copy per bill."""
from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date, timedelta

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.bill_config import get_print_slot_settings, load_bill_print_settings, print_all_bills_per_page, print_all_total_pages
from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS
from core.scroll_manager import (
    DIALOG_SIZE_LARGE,
    dialog_root,
    dialog_section,
    open_dialog,
    refresh_dialog_geometry,
)
from core.themed_messagebox import showwarning
from ui.sales.sales_history_actions import PRINT_ALL_SLOT
from widgets.searchable_combo import SearchableCombo


def _month_names() -> list[str]:
    return [calendar.month_name[m] for m in range(1, 13)]


def _year_values(center: int | None = None) -> list[str]:
    y = center or date.today().year
    return [str(yr) for yr in range(y - 5, y + 2)]


def resolve_date_range(
    mode: str,
    *,
    from_date: str,
    to_date: str,
    specific_date: str,
    month_name: str,
    year: str,
) -> tuple[str, str]:
    today = date.today()
    if mode == 'today':
        d = today.isoformat()
        return d, d
    if mode == 'yesterday':
        d = (today - timedelta(days=1)).isoformat()
        return d, d
    if mode == 'specific':
        d = (specific_date or '').strip()
        return d, d
    if mode == 'month':
        try:
            yr = int(year)
            mo = _month_names().index(month_name) + 1
        except (ValueError, TypeError):
            yr, mo = today.year, today.month
        last = calendar.monthrange(yr, mo)[1]
        return date(yr, mo, 1).isoformat(), date(yr, mo, last).isoformat()
    if mode == 'year':
        try:
            yr = int(year)
        except (ValueError, TypeError):
            yr = today.year
        return f'{yr}-01-01', f'{yr}-12-31'
    fd = (from_date or '').strip()
    td = (to_date or '').strip()
    if fd and not td:
        td = fd
    elif td and not fd:
        fd = td
    return fd, td


def fetch_sales_for_print(conn, from_iso: str, to_iso: str) -> list[tuple[int, tuple]]:
    if not from_iso or not to_iso:
        return []
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, s.bill_no, s.bill_date, COALESCE(c.name, ''), s.total_amount,
               (SELECT GROUP_CONCAT(DISTINCT NULLIF(TRIM(m.schedule), ''))
                FROM sales_items si
                JOIN medicines m ON m.id = si.medicine_id
                WHERE si.sale_id = s.id) AS bill_schedules
        FROM sales s
        JOIN customers c ON s.customer_id = c.id
        WHERE date(s.bill_date) >= date(?) AND date(s.bill_date) <= date(?)
        ORDER BY s.bill_date ASC, s.created_at ASC
        """,
        (from_iso, to_iso),
    )
    rows = cur.fetchall()
    return [(int(r[0]), (r[1], r[2], r[3], r[4], r[5] or '')) for r in rows]


def _bill_schedule_tokens(schedules_raw) -> set[str]:
    text = (schedules_raw or '').strip()
    if not text:
        return set()
    return {p.strip() for p in text.replace(';', ',').split(',') if p.strip()}


def _bill_matches_schedule(schedules_raw, selected: str) -> bool:
    sel = (selected or 'All').strip()
    if not sel or sel.lower() == 'all':
        return True
    tokens = _bill_schedule_tokens(schedules_raw)
    if sel.lower() in ('non-scheduled', 'nonscheduled'):
        return not tokens
    return sel in tokens


def show_print_bills_selection_dialog(
    parent,
    items: list[tuple[int, tuple]],
    *,
    paper: str = "A6",
) -> list[tuple[int, tuple]] | None:
    """Let the user pick which bills to print from a fetched list."""
    if not items:
        return None

    from core.layout_config import get_configured_schedules

    paper = (paper or "A6").upper()
    per_page = print_all_bills_per_page(paper)
    schedule_choices = ['All'] + get_configured_schedules() + ['Non-Scheduled']

    w, h = DIALOG_SIZE_LARGE
    dlg = open_dialog(parent, 'Select Bills to Print', width=w, height=h + 40, resizable=True)
    root = dialog_root(dlg.content)
    result: dict[str, list[tuple[int, tuple]] | None] = {'items': None}

    ttk.Label(
        root,
        text='Pick a schedule to auto-select matching bills, or All to choose any bills manually.',
        font=(FONT_FAMILY, FONT_SIZE_LABELS),
        wraplength=w - 60,
    ).pack(anchor=tk.W, pady=(0, 8))

    sch_row = ttk.Frame(root)
    sch_row.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(sch_row, text='Schedule:', font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(side=tk.LEFT)
    # Readonly combobox so default "All" does not filter the list down to only that option.
    schedule_var = tk.StringVar(value='All')
    schedule_combo = ttk.Combobox(
        sch_row,
        textvariable=schedule_var,
        values=schedule_choices,
        width=18,
        state='readonly',
    )
    schedule_combo.pack(side=tk.LEFT, padx=(8, 0))

    count_var = tk.StringVar(value=f'{len(items)} bill(s) found — all selected.')
    pages_var = tk.StringVar(
        value=f'Estimated pages: {print_all_total_pages(len(items), paper)} '
              f'({per_page} bill(s) per {paper} page)',
    )
    ttk.Label(root, textvariable=count_var, font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(
        anchor=tk.W, pady=(0, 2),
    )
    ttk.Label(root, textvariable=pages_var, font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(
        anchor=tk.W, pady=(0, 6),
    )

    list_frame, list_inner = dialog_section(root, 'Bills')
    list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

    canvas = tk.Canvas(list_inner, highlightthickness=0, height=320)
    scroll = ttk.Scrollbar(list_inner, orient=tk.VERTICAL, command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0, 0), window=inner, anchor=tk.NW)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)

    chk_vars: dict[int, tk.BooleanVar] = {}
    chk_widgets: dict[int, ttk.Checkbutton] = {}
    for sale_id, values in items:
        bill_no = values[0] if values else str(sale_id)
        bill_date = values[1] if len(values) > 1 else ''
        customer = values[2] if len(values) > 2 else ''
        total = values[3] if len(values) > 3 else 0
        schedules = values[4] if len(values) > 4 else ''
        try:
            total_txt = f'₹{float(total):.2f}'
        except (TypeError, ValueError):
            total_txt = str(total or '')
        sch_txt = (schedules or '').strip() or 'Non-Scheduled'
        var = tk.BooleanVar(value=True)
        chk_vars[sale_id] = var
        label = f'{bill_no}  ·  {bill_date}  ·  {customer}  ·  {total_txt}  ·  {sch_txt}'
        cb = ttk.Checkbutton(inner, text=label, variable=var)
        cb.pack(anchor=tk.W, padx=8, pady=2)
        chk_widgets[sale_id] = cb

    def _refresh_count(*_):
        picked = sum(1 for var in chk_vars.values() if var.get())
        count_var.set(f'{picked} of {len(items)} bill(s) selected.')
        pages_var.set(
            f'Estimated pages: {print_all_total_pages(picked, paper)} '
            f'({per_page} bill(s) per {paper} page)',
        )

    def _apply_schedule_filter(*_):
        selected = (schedule_var.get() or 'All').strip() or 'All'
        is_all = selected.lower() == 'all'
        for sale_id, values in items:
            schedules = values[4] if len(values) > 4 else ''
            match = _bill_matches_schedule(schedules, selected)
            var = chk_vars.get(sale_id)
            if var is None:
                continue
            if is_all:
                try:
                    chk_widgets[sale_id].configure(state=tk.NORMAL)
                except Exception:
                    pass
            else:
                var.set(bool(match))
                try:
                    chk_widgets[sale_id].configure(state=tk.NORMAL if match else tk.DISABLED)
                except Exception:
                    pass
        _refresh_count()

    btn_row = ttk.Frame(root)
    btn_row.pack(fill=tk.X, pady=(0, 6))

    def _select_all():
        selected = (schedule_var.get() or 'All').strip() or 'All'
        is_all = selected.lower() == 'all'
        for sale_id, values in items:
            schedules = values[4] if len(values) > 4 else ''
            if is_all or _bill_matches_schedule(schedules, selected):
                chk_vars[sale_id].set(True)
        _refresh_count()

    def _clear_all():
        for var in chk_vars.values():
            var.set(False)
        _refresh_count()

    ttk.Button(btn_row, text='Select all', command=_select_all).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(btn_row, text='Clear all', command=_clear_all).pack(side=tk.LEFT)

    for var in chk_vars.values():
        var.trace_add('write', _refresh_count)
    schedule_combo.bind('<<ComboboxSelected>>', _apply_schedule_filter)

    def _close():
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()

    def _print_selected():
        selected = [
            (sale_id, values)
            for sale_id, values in items
            if chk_vars.get(sale_id) and chk_vars[sale_id].get()
        ]
        if not selected:
            showwarning('Select Bills', 'Select at least one bill to print.', parent=dlg)
            return
        result['items'] = selected
        _close()

    try:
        print_btn = ttk.Button(dlg.footer, text='Print Selected', command=_print_selected, bootstyle='primary')
        cancel_btn = ttk.Button(dlg.footer, text='Cancel', command=_close, bootstyle='secondary')
    except Exception:
        print_btn = ttk.Button(dlg.footer, text='Print Selected', command=_print_selected)
        cancel_btn = ttk.Button(dlg.footer, text='Cancel', command=_close)
    cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))
    print_btn.pack(side=tk.RIGHT)

    print_btn.bind('<Return>', lambda e: (_print_selected(), 'break'), add='+')
    dlg.bind('<Escape>', lambda e: (_close(), 'break'), add='+')

    from core.voice.voice_dialog import register_print_all_select_dialog
    register_print_all_select_dialog(
        dlg,
        on_print=_print_selected,
        on_select_all=_select_all,
        on_clear=_clear_all,
        on_cancel=_close,
    )

    _refresh_count()
    dlg.after(300, lambda: refresh_dialog_geometry(dlg))
    dlg.after(120, lambda: print_btn.focus_set())
    dlg.wait_window()
    return result.get('items')


def show_print_all_bills_dialog(parent, conn, *, default_from: str = '', default_to: str = ''):
    slot_cfg = get_print_slot_settings(load_bill_print_settings(), PRINT_ALL_SLOT)
    slot_label = slot_cfg.get('label') or 'Print Sales 2'
    paper = (slot_cfg.get('paper_size') or 'A6').upper()
    size_mode = (slot_cfg.get('bill_size_mode') or 'dot_matrix').replace('_', ' ')

    w, h = DIALOG_SIZE_LARGE
    dlg = open_dialog(parent, 'Print All Bills', width=w, height=h, resizable=True)
    root = dialog_root(dlg.content)

    ttk.Label(
        root,
        text='Choose page size for Print All. Bills are grouped to save paper.',
        font=(FONT_FAMILY, FONT_SIZE_LABELS),
        wraplength=w - 60,
    ).pack(anchor=tk.W, pady=(0, 8))

    paper_frame = ttk.Frame(root)
    paper_frame.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(paper_frame, text='Page size:').pack(side=tk.LEFT)
    paper_var = tk.StringVar(value=paper)
    paper_combo = ttk.Combobox(
        paper_frame, textvariable=paper_var, width=8, state='readonly',
        values=['A4', 'A5', 'A6'],
    )
    paper_combo.pack(side=tk.LEFT, padx=(8, 0))
    per_page_var = tk.StringVar(value='1 bill per page')
    ttk.Label(paper_frame, textvariable=per_page_var).pack(side=tk.LEFT, padx=(12, 0))

    def _refresh_paper_hint(*_):
        p = (paper_var.get() or 'A6').upper()
        n = print_all_bills_per_page(p)
        per_page_var.set(f'{n} bill(s) per page')
        _refresh_count()

    paper_var.trace_add('write', _refresh_paper_hint)

    ttk.Label(
        root,
        text=f'Each bill prints using slot: {slot_label}. Batch layout groups bills per page.',
        font=(FONT_FAMILY, FONT_SIZE_LABELS),
        wraplength=w - 60,
    ).pack(anchor=tk.W, pady=(0, 8))

    mode_var = tk.StringVar(value='today')
    mode_frame, mode_inner = dialog_section(root, 'Period')
    mode_frame.pack(fill=tk.X, pady=(0, 8))

    for val, label in (
        ('today', 'Today'),
        ('yesterday', 'Yesterday'),
        ('specific', 'Specific date'),
        ('range', 'Date range (from - to)'),
        ('month', 'Month'),
        ('year', 'Year'),
    ):
        ttk.Radiobutton(mode_inner, text=label, variable=mode_var, value=val).pack(anchor=tk.W, pady=1)

    day_frame = ttk.Frame(root)
    day_frame.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(day_frame, text='Date:').grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=4)
    specific_entry = ttk.Entry(day_frame, width=14)
    specific_entry.grid(row=0, column=1, sticky=tk.W, pady=4)
    specific_entry.insert(0, date.today().isoformat())

    range_frame = ttk.Frame(root)
    range_frame.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(range_frame, text='From:').grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=4)
    from_entry = ttk.Entry(range_frame, width=14)
    from_entry.grid(row=0, column=1, sticky=tk.W, pady=4)
    ttk.Label(range_frame, text='To:').grid(row=0, column=2, sticky=tk.W, padx=(12, 6), pady=4)
    to_entry = ttk.Entry(range_frame, width=14)
    to_entry.grid(row=0, column=3, sticky=tk.W, pady=4)

    today = date.today()
    from_entry.insert(0, (default_from or '').strip() or today.replace(day=1).isoformat())
    to_entry.insert(0, (default_to or '').strip() or today.isoformat())

    month_frame = ttk.Frame(root)
    month_frame.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(month_frame, text='Month:').grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=4)
    month_combo = SearchableCombo(month_frame, values=_month_names(), width=14, listbox_height=8)
    month_combo.grid(row=0, column=1, sticky=tk.W, pady=4)
    month_combo.set(calendar.month_name[today.month])
    ttk.Label(month_frame, text='Year:').grid(row=0, column=2, sticky=tk.W, padx=(12, 6), pady=4)
    month_year_combo = SearchableCombo(month_frame, values=_year_values(), width=8, listbox_height=8)
    month_year_combo.grid(row=0, column=3, sticky=tk.W, pady=4)
    month_year_combo.set(str(today.year))

    year_frame = ttk.Frame(root)
    year_frame.pack(fill=tk.X, pady=(0, 6))
    ttk.Label(year_frame, text='Year:').grid(row=0, column=0, sticky=tk.W, padx=(0, 6), pady=4)
    year_combo = SearchableCombo(year_frame, values=_year_values(), width=10, listbox_height=8)
    year_combo.grid(row=0, column=1, sticky=tk.W, pady=4)
    year_combo.set(str(today.year))

    count_var = tk.StringVar(value='')
    ttk.Label(root, textvariable=count_var, font=(FONT_FAMILY, FONT_SIZE_LABELS), wraplength=w - 60).pack(
        anchor=tk.W, pady=(8, 4),
    )

    result = {'items': None, 'paper': 'A6'}

    def _set_entry_state(entry, enabled: bool):
        try:
            entry.configure(state='normal' if enabled else 'disabled')
        except tk.TclError:
            pass

    def _set_combo_state(combo, enabled: bool):
        try:
            combo.entry.configure(state='normal' if enabled else 'disabled')
        except tk.TclError:
            pass

    def _active_year() -> str:
        mode = mode_var.get()
        if mode == 'month':
            return month_year_combo.get()
        if mode == 'year':
            return year_combo.get()
        return str(today.year)

    def _refresh_count(*_):
        fd, td = resolve_date_range(
            mode_var.get(),
            from_date=from_entry.get(),
            to_date=to_entry.get(),
            specific_date=specific_entry.get(),
            month_name=month_combo.get(),
            year=_active_year(),
        )
        if not fd or not td:
            count_var.set('Enter a valid period.')
            return
        try:
            items = fetch_sales_for_print(conn, fd, td)
        except Exception as exc:
            count_var.set(f'Could not count bills: {exc}')
            return
        if not items:
            count_var.set(f'No bills found from {fd} to {td}.')
        else:
            pages = print_all_total_pages(len(items), paper_var.get())
            per = print_all_bills_per_page(paper_var.get())
            count_var.set(
                f'{len(items)} bill(s) found ({fd} to {td}) — '
                f'about {pages} page(s) on {paper_var.get().upper()} ({per} per page).',
            )

    def _toggle_frames(*_):
        mode = mode_var.get()
        _set_entry_state(specific_entry, mode == 'specific')
        for entry in (from_entry, to_entry):
            _set_entry_state(entry, mode == 'range')
        _set_combo_state(month_combo, mode == 'month')
        _set_combo_state(month_year_combo, mode == 'month')
        _set_combo_state(year_combo, mode == 'year')
        _refresh_count()

    mode_var.trace_add('write', _toggle_frames)
    specific_entry.bind('<KeyRelease>', lambda e: _refresh_count(), add='+')
    for w in (from_entry, to_entry):
        w.bind('<KeyRelease>', lambda e: _refresh_count(), add='+')
    month_combo.var.trace_add('write', lambda *_: _refresh_count())
    month_year_combo.var.trace_add('write', lambda *_: _refresh_count())
    year_combo.var.trace_add('write', lambda *_: _refresh_count())

    def _close():
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()

    def _select_bills():
        fd, td = resolve_date_range(
            mode_var.get(),
            from_date=from_entry.get(),
            to_date=to_entry.get(),
            specific_date=specific_entry.get(),
            month_name=month_combo.get(),
            year=_active_year(),
        )
        if not fd or not td:
            showwarning('Print All', 'Please enter a valid period.', parent=dlg)
            return
        items = fetch_sales_for_print(conn, fd, td)
        if not items:
            showwarning('Print All', f'No bills found from {fd} to {td}.', parent=dlg)
            return
        selected = show_print_bills_selection_dialog(dlg, items, paper=paper_var.get())
        if not selected:
            return
        result['items'] = selected
        result['paper'] = (paper_var.get() or 'A6').upper()
        _close()

    try:
        print_btn = ttk.Button(dlg.footer, text='Select Bills…', command=_select_bills, bootstyle='primary')
        cancel_btn = ttk.Button(dlg.footer, text='Cancel', command=_close, bootstyle='secondary')
    except Exception:
        print_btn = ttk.Button(dlg.footer, text='Select Bills…', command=_select_bills)
        cancel_btn = ttk.Button(dlg.footer, text='Cancel', command=_close)
    cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))
    print_btn.pack(side=tk.RIGHT)

    specific_entry.bind('<Return>', lambda e: (_select_bills(), 'break'), add='+')
    from_entry.bind('<Return>', lambda e: (to_entry.focus_set(), 'break'), add='+')
    to_entry.bind('<Return>', lambda e: (_select_bills(), 'break'), add='+')
    print_btn.bind('<Return>', lambda e: (_select_bills(), 'break'), add='+')
    dlg.bind('<Escape>', lambda e: (_close(), 'break'), add='+')

    from core.voice.voice_dialog import register_print_all_period_dialog
    register_print_all_period_dialog(
        dlg,
        on_select_bills=_select_bills,
        on_cancel=_close,
        paper_var=paper_var,
    )

    _toggle_frames()
    _refresh_paper_hint()
    dlg.after(300, lambda: refresh_dialog_geometry(dlg))
    dlg.after(120, lambda: print_btn.focus_set())
    dlg.wait_window()
    if not result.get('items'):
        return None
    return result['items'], result.get('paper') or 'A6'