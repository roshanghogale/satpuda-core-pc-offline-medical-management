"""Dialog to add a medicine to a sale before it exists in stock."""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS
from core.layout_config import (
    get_configured_schedules,
    get_med_types,
    is_strip_count_type,
    load_layout,
    parse_tablets_per_stripe,
)
from core.quick_sale_medicine import build_quick_sale_row
from core.scroll_manager import open_dialog
from core.themed_messagebox import showerror, showwarning
from widgets.searchable_combo import SearchableCombo


def _default_pack_for_type(med_type: str, cfg: dict, sched_unit: dict) -> str:
    unit = sched_unit.get(med_type, cfg.get(f'unit_{med_type}', ''))
    if not is_strip_count_type(med_type, unit):
        return '1'
    if (med_type or '').lower() == 'bolus':
        return '1'
    default_qty = cfg.get(f'typeqty_{med_type}', 0)
    if default_qty:
        return str(int(default_qty))
    return '10'


def _load_inventory_names(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT name FROM medicines "
        "WHERE TRIM(COALESCE(name, '')) != '' "
        "ORDER BY name COLLATE NOCASE"
    )
    return [r[0] for r in cur.fetchall()]


def _fmt_rate(value: float) -> str:
    text = f'{float(value or 0):.4f}'.rstrip('0').rstrip('.')
    return text or '0'


def show_quick_sale_medicine_dialog(parent, conn, on_add):
    dlg = open_dialog(
        parent,
        'Add Medicine (No Stock)',
        width=520,
        height=500,
        resizable=False,
    )
    body = dlg.content
    body.columnconfigure(1, weight=1)

    med_types = get_med_types()
    cfg = load_layout()
    sched_unit = {t: cfg.get(f'unit_{t}', '') for t in med_types}
    inventory_names = _load_inventory_names(conn)

    ttk.Label(body, text='Medicine Name *').grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
    name_combo = SearchableCombo(body, values=inventory_names, width=34, listbox_height=10)
    name_combo.grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=6)

    ttk.Label(body, text='Type *').grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
    type_combo = SearchableCombo(body, values=med_types, width=34, listbox_height=10)
    type_combo.grid(row=1, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=6)
    type_combo.set('')

    ttk.Label(body, text='Batch No *').grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
    batch_var = tk.StringVar()
    batch_entry = ttk.Entry(body, textvariable=batch_var, width=36)
    batch_entry.grid(row=2, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=6)

    pack_lbl = ttk.Label(body, text='Tablets per strip *')
    pack_lbl.grid(row=3, column=0, sticky=tk.W, padx=8, pady=6)
    pack_var = tk.StringVar(value='10')
    pack_entry = ttk.Entry(body, textvariable=pack_var, width=12)
    pack_entry.grid(row=3, column=1, sticky=tk.W, padx=8, pady=6)

    qty_lbl = ttk.Label(body, text='Total tablets *')
    qty_lbl.grid(row=4, column=0, sticky=tk.W, padx=8, pady=6)
    qty_var = tk.StringVar(value='1')
    qty_entry = ttk.Entry(body, textvariable=qty_var, width=12)
    qty_entry.grid(row=4, column=1, sticky=tk.W, padx=8, pady=6)

    rate_lbl = ttk.Label(body, text='Rate (₹ per strip) *')
    rate_lbl.grid(row=5, column=0, sticky=tk.W, padx=8, pady=6)
    rate_var = tk.StringVar(value='0')
    rate_entry = ttk.Entry(body, textvariable=rate_var, width=12)
    rate_entry.grid(row=5, column=1, sticky=tk.W, padx=8, pady=6)

    mrp_lbl = ttk.Label(body, text='MRP (₹ per strip)')
    mrp_lbl.grid(row=6, column=0, sticky=tk.W, padx=8, pady=6)
    mrp_var = tk.StringVar(value='')
    mrp_entry = ttk.Entry(body, textvariable=mrp_var, width=12)
    mrp_entry.grid(row=6, column=1, sticky=tk.W, padx=8, pady=6)

    ttk.Label(body, text='Schedule').grid(row=7, column=0, sticky=tk.W, padx=8, pady=6)
    schedule_combo = SearchableCombo(
        body, values=get_configured_schedules(), width=34, listbox_height=8,
    )
    schedule_combo.grid(row=7, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=6)
    schedule_combo.set('')

    hint = ttk.Label(
        body,
        text=(
            'Pick from inventory or type a new name. For tablets: qty = total tablets sold; '
            'rate & MRP are per strip (÷ tablets per strip for per-tablet sale). '
            'Schedule is optional; if set, doctor is required on the bill. Enter = next field.'
        ),
        font=(FONT_FAMILY, FONT_SIZE_LABELS - 1),
        foreground='#555',
        wraplength=460,
    )
    hint.grid(row=8, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(8, 4))

    def _refresh_type_fields(*_):
        mt = type_combo.get().strip()
        unit = sched_unit.get(mt, cfg.get(f'unit_{mt}', ''))
        if mt and is_strip_count_type(mt, unit):
            pack_lbl.configure(text='Tablets per strip *')
            pack_entry.configure(state='normal')
            qty_lbl.configure(text='Total tablets *')
            rate_lbl.configure(text='Rate (₹ per strip) *')
            mrp_lbl.configure(text='MRP (₹ per strip)')
            if not pack_var.get().strip() or pack_var.get().strip() in ('0', '1', '10'):
                pack_var.set(_default_pack_for_type(mt, cfg, sched_unit))
        else:
            pack_lbl.configure(text='Pack / unit (optional)')
            pack_entry.configure(state='normal')
            qty_lbl.configure(text='Quantity *')
            rate_lbl.configure(text='Rate (₹) *')
            mrp_lbl.configure(text='MRP (₹)')

    def _fill_from_inventory(name: str):
        name = (name or '').strip()
        if not name:
            return
        cur = conn.cursor()
        cur.execute(
            """
            SELECT type, batch_no, COALESCE(unit, '1'), COALESCE(mrp, 0), COALESCE(rate, 0),
                   COALESCE(schedule, '')
            FROM medicines
            WHERE UPPER(name) = UPPER(?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (name,),
        )
        row = cur.fetchone()
        if not row:
            return
        med_type, batch, unit, mrp, db_rate, schedule = row
        if med_type:
            type_combo.set(med_type)
        if batch:
            batch_var.set(batch)
        if schedule:
            schedule_combo.set(schedule)
        else:
            schedule_combo.set('')
        _refresh_type_fields()
        unit = str(unit or '1')
        strip_type = is_strip_count_type(med_type or '', unit)
        if strip_type:
            tps = max(1, parse_tablets_per_stripe(unit))
            pack_var.set(str(tps))
            strip_mrp = float(mrp or 0)
            purchase_rate = float(db_rate or 0)
            if purchase_rate > 0:
                rate_var.set(_fmt_rate(purchase_rate / tps))
            elif strip_mrp > 0:
                rate_var.set(_fmt_rate(strip_mrp / tps))
            if strip_mrp > 0:
                mrp_var.set(f'{strip_mrp:.2f}')
        else:
            pack_var.set(unit or '1')
            if float(mrp or 0) > 0:
                rate_var.set(_fmt_rate(mrp))
                mrp_var.set(f'{float(mrp):.2f}')
            elif float(db_rate or 0) > 0:
                rate_var.set(_fmt_rate(db_rate))

    type_combo.bind('<<ComboboxSelected>>', _refresh_type_fields)
    type_combo.bind_apply_on_select(_refresh_type_fields)
    name_combo.bind('<<ComboboxSelected>>', lambda _e: _fill_from_inventory(name_combo.get()))
    name_combo.bind_apply_on_select(lambda: _fill_from_inventory(name_combo.get()))

    def _submit():
        name = name_combo.get().strip()
        batch = batch_var.get().strip()
        med_type = type_combo.get().strip()
        if not name or not batch or not med_type:
            showwarning('Missing Fields', 'Name, type, and batch are required.', parent=dlg)
            return
        if med_type not in med_types:
            showwarning(
                'Invalid Type',
                'Please select a medicine type from the list (same as Purchase).',
                parent=dlg,
            )
            type_combo.focus(open_dropdown=True)
            return
        try:
            qty = int(qty_var.get().strip() or '0')
        except ValueError:
            showerror('Invalid Quantity', 'Enter a whole number for quantity.', parent=dlg)
            return
        if qty <= 0:
            showwarning('Quantity', 'Quantity must be greater than zero.', parent=dlg)
            return
        try:
            rate = float(rate_var.get().strip() or '0')
        except ValueError:
            showerror('Invalid Rate', 'Enter a valid selling rate.', parent=dlg)
            return
        if rate <= 0:
            showwarning('Rate Required', 'Enter the selling rate for this line.', parent=dlg)
            return
        mrp_raw = mrp_var.get().strip()
        mrp = 0.0
        if mrp_raw:
            try:
                mrp = float(mrp_raw)
            except ValueError:
                showerror('Invalid MRP', 'Enter a valid MRP or leave blank.', parent=dlg)
                return
            if mrp < 0:
                showwarning('MRP', 'MRP cannot be negative.', parent=dlg)
                return

        pack = pack_var.get().strip() or '1'
        unit = sched_unit.get(med_type, cfg.get(f'unit_{med_type}', ''))
        strip_type = is_strip_count_type(med_type, unit)
        if strip_type:
            try:
                tps = max(1, int(float(pack)))
            except ValueError:
                showwarning('Pack Size', 'Enter tablets per strip (e.g. 10).', parent=dlg)
                return
            sale_rate = round(rate / tps, 4)
            strip_mrp = mrp if mrp > 0 else round(rate, 2)
        else:
            sale_rate = round(rate, 4)
            strip_mrp = mrp if mrp > 0 else rate

        row = build_quick_sale_row(
            name, med_type, batch, qty, pack, strip_mrp, rate=sale_rate,
            schedule=schedule_combo.get().strip(),
        )
        on_add(row)
        dlg.destroy()

    def _focus_pack(_e=None):
        pack_entry.focus_set()
        return 'break'

    def _focus_qty(_e=None):
        qty_entry.focus_set()
        return 'break'

    def _focus_rate(_e=None):
        rate_entry.focus_set()
        return 'break'

    def _focus_mrp(_e=None):
        mrp_entry.focus_set()
        return 'break'

    def _focus_schedule(_e=None):
        schedule_combo.focus()
        return 'break'

    def _submit_enter(_e=None):
        _submit()
        return 'break'

    # Enter key flow through fields
    name_combo.next_focus_widget = lambda: type_combo.focus()
    type_combo.next_focus_widget = lambda: batch_entry.focus_set()
    batch_entry.bind('<Return>', _focus_pack)
    batch_entry.bind('<KP_Enter>', _focus_pack)
    pack_entry.bind('<Return>', _focus_qty)
    pack_entry.bind('<KP_Enter>', _focus_qty)
    qty_entry.bind('<Return>', _focus_rate)
    qty_entry.bind('<KP_Enter>', _focus_rate)
    rate_entry.bind('<Return>', _focus_mrp)
    rate_entry.bind('<KP_Enter>', _focus_mrp)
    mrp_entry.bind('<Return>', _focus_schedule)
    mrp_entry.bind('<KP_Enter>', _focus_schedule)
    schedule_combo.next_focus_widget = _submit
    schedule_combo.bind('<Return>', _submit_enter)
    schedule_combo.bind('<KP_Enter>', _submit_enter)

    ttk.Button(dlg.footer, text='Add to Bill', command=_submit).pack(side=tk.RIGHT, padx=6)
    ttk.Button(dlg.footer, text='Cancel', command=dlg.destroy).pack(side=tk.RIGHT, padx=6)

    dlg._name_focus_done = False

    def _focus_name_once(*_):
        if dlg._name_focus_done:
            return
        dlg._name_focus_done = True
        try:
            name_combo.entry.focus_set()
            name_combo.entry.icursor(tk.END)
            name_combo.focus(open_dropdown=True)
        except tk.TclError:
            pass

    # Once after modal dialog is shown (open_dialog finalises ~120ms); do not re-run on Map/delays
    dlg.after(150, _focus_name_once)
