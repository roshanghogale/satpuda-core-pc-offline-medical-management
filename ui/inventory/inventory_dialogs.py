"""
ui/inventory_dialogs.py
────────────────────────
Edit medicine, view details, and delete medicine dialogs.
Called by InventoryPage — no tree/filter UI here.
"""
import tkinter as tk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.scroll_manager import open_dialog
from widgets.searchable_combo import SearchableCombo


def _expiry_to_display(expiry_date):
    if expiry_date and '-' in str(expiry_date):
        parts = str(expiry_date).split('-')
        if len(parts) >= 2:
            return f"{parts[1]}/{parts[0][2:]}"
    return expiry_date or ''


def _load_medicine_row(cursor, medicine_id):
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import medicine_by_id
            from core.server_crud import get_doc

            mp = medicine_by_id(medicine_id) or get_doc("medicines", int(medicine_id)) or {}
            if mp:
                return (
                    mp.get("name") or "",
                    mp.get("type") or "",
                    mp.get("batch_no") or "",
                    mp.get("expiry_date") or "",
                    float(mp.get("stock_qty") or 0),
                    mp.get("unit") or "1",
                    float(mp.get("mrp") or 0),
                    float(mp.get("rate") or 0),
                    mp.get("manufacturer") or "",
                    mp.get("schedule") or "",
                    mp.get("content_drug") or "",
                    mp.get("hsn_code") or "",
                )
    except Exception:
        pass

    cursor.execute("PRAGMA table_info(medicines)")
    cols = {c[1] for c in cursor.fetchall()}
    has_content = 'content_drug' in cols
    has_hsn = 'hsn_code' in cols
    if has_content and has_hsn:
        cursor.execute("""
            SELECT name, type, batch_no, expiry_date, stock_qty,
                   unit, mrp, rate, manufacturer, schedule,
                   COALESCE(content_drug, ''), COALESCE(hsn_code, '')
            FROM medicines WHERE id=?
        """, (medicine_id,))
    elif has_content:
        cursor.execute("""
            SELECT name, type, batch_no, expiry_date, stock_qty,
                   unit, mrp, rate, manufacturer, schedule,
                   COALESCE(content_drug, ''), ''
            FROM medicines WHERE id=?
        """, (medicine_id,))
    elif has_hsn:
        cursor.execute("""
            SELECT name, type, batch_no, expiry_date, stock_qty,
                   unit, mrp, rate, manufacturer, schedule,
                   '', COALESCE(hsn_code, '')
            FROM medicines WHERE id=?
        """, (medicine_id,))
    else:
        cursor.execute("""
            SELECT name, type, batch_no, expiry_date, stock_qty,
                   unit, mrp, rate, manufacturer, schedule, '', ''
            FROM medicines WHERE id=?
        """, (medicine_id,))
    return cursor.fetchone()


def _field_set(widget, label, value):
    """Set widget value for edit dialog fields (Entry, Combo, or multiline Text)."""
    v = value or ''
    if label == 'Content/Drug' and isinstance(widget, tk.Text):
        widget.delete('1.0', tk.END)
        widget.insert('1.0', v)
    elif hasattr(widget, 'set'):
        widget.set(v)
    else:
        widget.delete(0, tk.END)
        widget.insert(0, v)


def _field_get(widget, label):
    """Read widget value for edit dialog fields."""
    if label == 'Content/Drug' and isinstance(widget, tk.Text):
        return widget.get('1.0', tk.END).strip()
    if hasattr(widget, 'get'):
        return widget.get().strip()
    return ''


def open_edit_dialog(parent, conn, medicine_id, refresh_callback):
    cursor = conn.cursor()
    row = _load_medicine_row(cursor, medicine_id)
    if not row:
        return
    (name, med_type, batch_no, expiry_date, stock_qty, unit, mrp, rate,
     manufacturer, schedule, content_drug, hsn_code) = row
    expiry_display = _expiry_to_display(expiry_date)

    dlg = open_dialog(parent, "Edit Medicine", width=500, height=760, resizable=False)
    body = dlg.content
    body.grid_columnconfigure(1, weight=1)

    from core.layout_config import load_layout, _DEFAULT_SCHEDULES, get_med_types, is_strip_count_type, parse_tablets_per_stripe
    from core.stock_utils import decompose_strip_stock, inventory_save_stock_qty
    layout = load_layout()
    med_types = get_med_types()
    schedules = [s for s in layout.get('schedules', list(_DEFAULT_SCHEDULES)) if s]

    # Per-tablet MRP/Rate are UI helpers only — save still writes strip MRP/Rate.
    labels = ['Name','Type','Batch No','Expiry Date (MM/YY)','Stock Qty',
              'Unit','Extra Tablets','MRP','Rate','Per-tablet MRP','Per-tablet Rate',
              'Manufacturer','Schedule','Content/Drug','HSN Code']
    label_widgets = {}
    fields = {}
    for i, label in enumerate(labels):
        lbl_w = ttk.Label(body, text=f"{label}:")
        lbl_w.grid(row=i, column=0, sticky=tk.W, padx=12, pady=5)
        label_widgets[label] = lbl_w
        if label == 'Type':
            fields[label] = SearchableCombo(body, values=med_types, width=30)
        elif label == 'Schedule':
            fields[label] = SearchableCombo(body, values=schedules, width=30)
        elif label == 'Content/Drug':
            fields[label] = tk.Text(
                body, height=2, width=34, wrap=tk.WORD,
                font=(FONT_FAMILY, FONT_SIZE_LABELS),
            )
        else:
            fields[label] = ttk.Entry(body, width=34)
        fields[label].grid(row=i, column=1, padx=12, pady=5, sticky=tk.EW)

    # Stock display: strip types show strips + extra loose tablets
    tps = parse_tablets_per_stripe(unit) if is_strip_count_type(med_type or '', unit) else 1
    strip_mrp = float(mrp or 0)
    strip_rate = float(rate or 0)
    tab_mrp = round(strip_mrp / tps, 4) if tps else strip_mrp
    tab_rate = round(strip_rate / tps, 4) if tps else strip_rate
    if is_strip_count_type(med_type or '', unit):
        strips, extra_loose = decompose_strip_stock(int(stock_qty or 0), tps)
        db_values = [name, med_type or '', batch_no or '', expiry_display,
                     str(strips), unit or '', str(extra_loose), str(strip_mrp), str(strip_rate),
                     str(tab_mrp), str(tab_rate),
                     manufacturer or '', schedule or '', content_drug or '', hsn_code or '']
    else:
        db_values = [name, med_type or '', batch_no or '', expiry_display,
                     str(stock_qty or 0), unit or '', '0', str(strip_mrp), str(strip_rate),
                     '', '',
                     manufacturer or '', schedule or '', content_drug or '', hsn_code or '']

    extra_field = fields['Extra Tablets']
    extra_field.grid_remove()
    tab_mrp_field = fields['Per-tablet MRP']
    tab_rate_field = fields['Per-tablet Rate']
    _syncing_price = {"on": False}

    def _current_tps():
        try:
            return max(1, parse_tablets_per_stripe(fields['Unit'].get()))
        except Exception:
            return 1

    def _fmt_price(val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ""
        if abs(v - round(v, 2)) < 1e-9:
            return f"{v:.2f}"
        return f"{v:.4f}".rstrip("0").rstrip(".")

    def _set_entry(entry, value):
        entry.delete(0, tk.END)
        entry.insert(0, value)

    def _strip_from_tablet():
        if _syncing_price["on"]:
            return
        if not is_strip_count_type(fields['Type'].get(), fields['Unit'].get()):
            return
        _syncing_price["on"] = True
        try:
            tps_now = _current_tps()
            try:
                tm = float(tab_mrp_field.get() or 0)
                _set_entry(fields['MRP'], _fmt_price(tm * tps_now))
            except (TypeError, ValueError):
                pass
            try:
                tr = float(tab_rate_field.get() or 0)
                _set_entry(fields['Rate'], _fmt_price(tr * tps_now))
            except (TypeError, ValueError):
                pass
        finally:
            _syncing_price["on"] = False

    def _tablet_from_strip():
        if _syncing_price["on"]:
            return
        if not is_strip_count_type(fields['Type'].get(), fields['Unit'].get()):
            return
        _syncing_price["on"] = True
        try:
            tps_now = _current_tps()
            try:
                sm = float(fields['MRP'].get() or 0)
                _set_entry(tab_mrp_field, _fmt_price(sm / tps_now))
            except (TypeError, ValueError):
                pass
            try:
                sr = float(fields['Rate'].get() or 0)
                _set_entry(tab_rate_field, _fmt_price(sr / tps_now))
            except (TypeError, ValueError):
                pass
        finally:
            _syncing_price["on"] = False

    def _refresh_stock_labels(*_):
        strip = is_strip_count_type(fields['Type'].get(), fields['Unit'].get())
        if strip:
            fields['Stock Qty'].config(state='normal')
            label_widgets['Stock Qty'].config(text='Strips:')
            label_widgets['Unit'].config(text='Tablets/Strip:')
            label_widgets['MRP'].config(text='MRP (per strip):')
            label_widgets['Rate'].config(text='Rate (per strip):')
            label_widgets['Per-tablet MRP'].config(text='MRP (per tablet):')
            label_widgets['Per-tablet Rate'].config(text='Rate (per tablet):')
            extra_field.grid()
            tab_mrp_field.grid()
            tab_rate_field.grid()
            label_widgets['Per-tablet MRP'].grid()
            label_widgets['Per-tablet Rate'].grid()
            _tablet_from_strip()
        else:
            label_widgets['Stock Qty'].config(text='Stock Qty:')
            label_widgets['Unit'].config(text='Unit:')
            label_widgets['MRP'].config(text='MRP:')
            label_widgets['Rate'].config(text='Rate:')
            extra_field.grid_remove()
            extra_field.delete(0, tk.END)
            extra_field.insert(0, '0')
            tab_mrp_field.grid_remove()
            tab_rate_field.grid_remove()
            label_widgets['Per-tablet MRP'].grid_remove()
            label_widgets['Per-tablet Rate'].grid_remove()

    for i, label in enumerate(labels):
        _field_set(fields[label], label, db_values[i])
    fields['Unit'].bind('<FocusOut>', lambda e: (_refresh_stock_labels(), _tablet_from_strip(), _rebind_plain_nav()))
    fields['Unit'].bind('<KeyRelease>', lambda e: _tablet_from_strip())
    fields['MRP'].bind('<KeyRelease>', lambda e: _tablet_from_strip())
    fields['MRP'].bind('<FocusOut>', lambda e: _tablet_from_strip())
    fields['Rate'].bind('<KeyRelease>', lambda e: _tablet_from_strip())
    fields['Rate'].bind('<FocusOut>', lambda e: _tablet_from_strip())
    tab_mrp_field.bind('<KeyRelease>', lambda e: _strip_from_tablet())
    tab_mrp_field.bind('<FocusOut>', lambda e: _strip_from_tablet())
    tab_rate_field.bind('<KeyRelease>', lambda e: _strip_from_tablet())
    tab_rate_field.bind('<FocusOut>', lambda e: _strip_from_tablet())
    # Type select + focus handlers bound after _rebind_plain_nav is defined below.

    def save():
        try:
            try:
                from core.online_guard import ensure_can_mutate, OnlineUnavailableError, show_mutate_error
                ensure_can_mutate()
            except OnlineUnavailableError as exc:
                show_mutate_error(exc, parent=dlg)
                return
            exp_input = fields['Expiry Date (MM/YY)'].get().strip()
            if '/' in exp_input:
                parts = exp_input.split('/')
                year = parts[1] if len(parts[1]) == 4 else '20' + parts[1]
                db_expiry = f"{year}-{parts[0]}-01"
            else:
                db_expiry = exp_input
            med_type_val = fields['Type'].get()
            unit_val = fields['Unit'].get()
            strips_or_qty = int(fields['Stock Qty'].get() or 0)
            extra_loose = int(fields['Extra Tablets'].get() or 0)
            stock_saved = inventory_save_stock_qty(
                med_type_val, unit_val, strips_or_qty, extra_loose,
            )
            name_val = fields['Name'].get()
            batch_val = fields['Batch No'].get()
            mrp_val = float(fields['MRP'].get() or 0)
            rate_val = float(fields['Rate'].get() or 0)
            mfr_val = fields['Manufacturer'].get()
            sch_val = fields['Schedule'].get()
            content_val = _field_get(fields['Content/Drug'], 'Content/Drug')
            hsn_val = fields['HSN Code'].get().strip()

            try:
                from core.sync_prefs import is_online_mode
                if is_online_mode():
                    from core.server_crud import upsert_medicine_online, get_doc
                    from core.online_catalog import medicine_by_id, invalidate

                    existing = medicine_by_id(medicine_id) or get_doc("medicines", int(medicine_id)) or {}
                    doc = dict(existing)
                    doc.update({
                        "id": int(medicine_id),
                        "local_id": int(medicine_id),
                        "name": name_val,
                        "type": med_type_val,
                        "batch_no": batch_val,
                        "expiry_date": db_expiry,
                        "stock_qty": stock_saved,
                        "unit": unit_val,
                        "mrp": mrp_val,
                        "rate": rate_val,
                        "manufacturer": mfr_val,
                        "schedule": sch_val,
                        "content_drug": content_val,
                        "hsn_code": hsn_val,
                    })
                    upsert_medicine_online(doc)
                    invalidate("medicines")
                    showinfo("Success", "Medicine updated successfully!", parent=dlg)
                    dlg.destroy()
                    refresh_callback()
                    return
            except Exception as e:
                showerror("Error", f"Failed to update medicine: {e}", parent=dlg)
                return

            try:
                cursor.execute("ALTER TABLE medicines ADD COLUMN content_drug TEXT")
                conn.commit()
            except Exception:
                pass
            cursor.execute("""
                UPDATE medicines SET name=?,type=?,batch_no=?,expiry_date=?,stock_qty=?,
                unit=?,mrp=?,rate=?,manufacturer=?,schedule=?,content_drug=?,hsn_code=?,
                synced_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id=?
            """, (name_val, med_type_val, batch_val,
                  db_expiry, stock_saved, unit_val,
                  mrp_val, rate_val,
                  mfr_val, sch_val,
                  content_val, hsn_val,
                  medicine_id))
            conn.commit()
            from core.sync_coordinator import after_medicine_saved
            after_medicine_saved(conn, medicine_id)
            showinfo("Success", "Medicine updated successfully!", parent=dlg)
            dlg.destroy()
            refresh_callback()
        except Exception as e:
            showerror("Error", f"Failed to update medicine: {e}", parent=dlg)

    plain = [fields[l] for l in labels if l not in (
        'Type', 'Schedule', 'Content/Drug', 'HSN Code', 'Extra Tablets',
        'Per-tablet MRP', 'Per-tablet Rate',
    )]
    # When strip type, insert per-tablet helpers into tab order after Rate.
    def _plain_nav_list():
        base = [fields[l] for l in (
            'Name', 'Batch No', 'Expiry Date (MM/YY)', 'Stock Qty', 'Unit',
            'MRP', 'Rate',
        )]
        if is_strip_count_type(fields['Type'].get(), fields['Unit'].get()):
            base.extend([tab_mrp_field, tab_rate_field])
        base.append(fields['Manufacturer'])
        return base

    content_field = fields['Content/Drug']
    hsn_field = fields['HSN Code']

    def _rebind_plain_nav(*_):
        chain = _plain_nav_list()
        for idx, w in enumerate(chain):
            if idx < len(chain) - 1:
                nxt = chain[idx + 1]
                w.bind('<Return>', lambda e, n=nxt: n.focus())
                w.bind('<Down>',   lambda e, n=nxt: n.focus())
            if idx > 0:
                prev = chain[idx - 1]
                w.bind('<Up>', lambda e, p=prev: p.focus())
        chain[-1].bind('<Return>', lambda e: fields['Schedule'].focus())
        chain[-1].bind('<Down>', lambda e: fields['Schedule'].focus())

    _rebind_plain_nav()
    fields['Type'].bind_apply_on_select(lambda: (_refresh_stock_labels(), _rebind_plain_nav()))
    fields['Type'].entry.bind('<FocusOut>', lambda e: (_refresh_stock_labels(), _rebind_plain_nav()))
    _refresh_stock_labels()
    fields['Manufacturer'].bind('<Return>', lambda e: fields['Schedule'].focus())
    fields['Manufacturer'].bind('<Down>', lambda e: fields['Schedule'].focus())
    fields['Schedule'].bind('<Down>', lambda e: content_field.focus())
    fields['Schedule'].bind('<Return>', lambda e: content_field.focus())
    content_field.bind('<Up>', lambda e: fields['Schedule'].focus())
    content_field.bind('<Return>', lambda e: hsn_field.focus())
    hsn_field.bind('<Up>', lambda e: content_field.focus())
    hsn_field.bind('<Return>', lambda e: save())
    dlg.bind('<Escape>', lambda e: dlg.destroy())

    sb = ttk.Button(dlg.footer, text="Save Changes", command=save)
    sb.pack(side=tk.LEFT, padx=8)
    cb = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
    cb.pack(side=tk.LEFT, padx=8)
    sb.bind('<Return>', lambda e: save())
    cb.bind('<Return>', lambda e: dlg.destroy())
    fields[labels[0]].focus()


def _norm_med_key(name: str) -> str:
    """Normalize medicine name for matching near-duplicates (TR-WELL vs TR WELL)."""
    return ''.join(ch for ch in (name or '').upper() if ch.isalnum())


def _make_scrolled_treeview(parent, columns, *, height=6, style='Large.Treeview'):
    """Create Treeview + scrollbars inside parent (safe for packed LabelFrames)."""
    wrap = ttk.Frame(parent)
    wrap.pack(fill=tk.BOTH, expand=True)
    wrap.grid_rowconfigure(0, weight=1)
    wrap.grid_columnconfigure(0, weight=1)
    tree = ttk.Treeview(
        wrap, columns=columns, show='headings', style=style, height=height,
    )
    ysb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=tree.yview)
    xsb = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=tree.xview)
    tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    tree.grid(row=0, column=0, sticky='nsew')
    ysb.grid(row=0, column=1, sticky='ns')
    xsb.grid(row=1, column=0, sticky='ew')
    return tree


def _item_matches_medicine(it: dict, medicine_id: int, name: str, batch: str) -> bool:
    try:
        mid = int(it.get("medicine_id") or it.get("local_id") or 0)
    except (TypeError, ValueError):
        mid = 0
    if mid and mid == int(medicine_id):
        return True
    it_name = str(it.get("name") or "").strip().upper()
    want_name = (name or "").strip().upper()
    if want_name and it_name == want_name:
        it_batch = str(it.get("batch_no") or it.get("batch") or "").strip()
        want_batch = str(batch or "").strip()
        if not want_batch or it_batch == want_batch:
            return True
    return False


def _fetch_medicine_history_rows(conn, medicine_id, name, batch, *, strip_type: bool, tps: int):
    """Purchase/sales lines for Medicine Details.

    Online: server bills (wide date range) + line items for this medicine.
    Offline: local purchase_items / sales_items.
    """
    purch: list[tuple] = []
    sales: list[tuple] = []
    online = False
    try:
        from core.sync_prefs import is_online_mode
        online = bool(is_online_mode())
    except Exception:
        online = False

    if online:
        try:
            from core import store_query_client as sq

            # Explicit wide range so old stock is not limited to current FY.
            common = {
                "from_date": "2000-04-01",
                "to_date": "2099-03-31",
                "medicine": (name or "").strip(),
                "batch": (batch or "").strip(),
                "limit": 300,
            }
            purch_bills = list((sq.list_purchases(**common) or {}).get("rows") or [])
            sales_bills = list((sq.list_sales(**common) or {}).get("rows") or [])

            for b in purch_bills[:80]:
                try:
                    detail = sq.get_purchase(int(b.get("id") or 0)) or {}
                except Exception:
                    continue
                supplier = (
                    detail.get("supplier_name")
                    or b.get("supplier_name")
                    or "(no supplier)"
                )
                pdate = detail.get("purchase_date") or b.get("purchase_date") or ""
                bill = detail.get("bill_number") or b.get("bill_number") or ""
                for it in detail.get("items") or []:
                    if not isinstance(it, dict):
                        continue
                    if it.get("deleted"):
                        continue
                    if not _item_matches_medicine(it, medicine_id, name, batch):
                        continue
                    qty_s = float(it.get("qty") or 0)
                    free_s = float(it.get("free_qty") or 0)
                    rate = it.get("rate")
                    amount = it.get("item_amount")
                    if amount is None:
                        amount = it.get("amount") or 0
                    purch.append((pdate, bill, supplier, qty_s, free_s, rate, amount))

            for b in sales_bills[:80]:
                try:
                    detail = sq.get_sale(int(b.get("id") or 0)) or {}
                except Exception:
                    continue
                if detail.get("is_autosave") or detail.get("deleted"):
                    continue
                customer = (
                    detail.get("customer_name")
                    or b.get("customer_name")
                    or "(no customer)"
                )
                sdate = detail.get("bill_date") or b.get("bill_date") or ""
                bill = detail.get("bill_no") or b.get("bill_no") or ""
                for it in detail.get("items") or []:
                    if not isinstance(it, dict):
                        continue
                    if it.get("deleted"):
                        continue
                    if not _item_matches_medicine(it, medicine_id, name, batch):
                        continue
                    sales.append(
                        (
                            sdate,
                            bill,
                            customer,
                            float(it.get("qty") or 0),
                            it.get("rate"),
                            it.get("amount") or 0,
                        )
                    )
            return purch, sales, "online"
        except Exception:
            pass

    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.purchase_date, COALESCE(p.bill_number, ''),
               COALESCE(s.name, '(no supplier)'),
               COALESCE(pi.qty,0), COALESCE(pi.free_qty,0), pi.rate,
               COALESCE(pi.item_amount, pi.amount, 0)
        FROM purchase_items pi
        JOIN purchases p ON pi.purchase_id=p.id
        LEFT JOIN suppliers s ON p.supplier_id=s.id
        WHERE pi.medicine_id=?
          AND COALESCE(pi.deleted,0)=0 AND COALESCE(p.deleted,0)=0
        ORDER BY p.purchase_date DESC
        """,
        (medicine_id,),
    )
    purch = list(cur.fetchall() or [])
    cur.execute(
        """
        SELECT s.bill_date, COALESCE(s.bill_no, ''),
               COALESCE(c.name, '(no customer)'), si.qty, si.rate, si.amount
        FROM sales_items si
        JOIN sales s ON si.sale_id=s.id
        LEFT JOIN customers c ON s.customer_id=c.id
        WHERE si.medicine_id=?
          AND COALESCE(si.deleted,0)=0 AND COALESCE(s.deleted,0)=0
          AND COALESCE(s.is_autosave,0)=0
        ORDER BY s.bill_date DESC, s.id DESC
        """,
        (medicine_id,),
    )
    sales = list(cur.fetchall() or [])
    return purch, sales, "offline"


def open_view_dialog(parent, conn, medicine_id):
    cursor = conn.cursor()
    row = _load_medicine_row(cursor, medicine_id)
    if not row:
        return
    (name, med_type, batch_no, expiry_date, stock_qty, unit, mrp, rate,
     manufacturer, schedule, content_drug, hsn_code) = row
    expiry_display = _expiry_to_display(expiry_date)

    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.stock_rebuild import expected_stock_qty
    from core.stock_utils import decompose_strip_stock

    strip_type = is_strip_count_type(med_type or '', unit)
    tps = parse_tablets_per_stripe(unit) if strip_type else 1
    if tps <= 0:
        tps = 1
    ledger_stock = max(0, int(round(expected_stock_qty(
        cursor, int(medicine_id), med_type or '', unit or '',
    ))))
    if strip_type:
        strips, extra = decompose_strip_stock(int(stock_qty or 0), tps)
        stock_label = f"{strips} strip(s)"
        if extra:
            stock_label += f" + {extra} tab"
        stock_label += f"  ({int(stock_qty or 0)} tablets, {tps}/strip)"
        unit_label = f"{unit or ''}  ({tps} tablets/strip)"
        sales_qty_hdr = 'Tablets'
    else:
        stock_label = str(stock_qty or 0)
        unit_label = unit or ''
        sales_qty_hdr = 'Qty'

    db_values = [name, med_type or '', batch_no or '', expiry_display,
                 stock_label, unit_label, str(mrp or 0), str(rate or 0),
                 manufacturer or '', schedule or '', content_drug or '', hsn_code or '']

    dlg = open_dialog(parent, f"Medicine Details - {name}", width=780, height=780, resizable=True)
    body = dlg.content

    info_frame = ttk.LabelFrame(body, text="Medicine Information")
    info_frame.pack(fill=tk.X, padx=10, pady=5)
    info_frame.grid_columnconfigure(1, weight=1)
    info_frame.grid_columnconfigure(3, weight=1)

    info_labels = ['Name', 'Type', 'Batch No', 'Expiry Date', 'Current Stock',
                   'Unit', 'MRP', 'Rate', 'Manufacturer', 'Schedule', 'Content/Drug', 'HSN Code']
    pair_labels = info_labels[:10]
    for i, label in enumerate(pair_labels):
        row_i = i // 2
        col_lbl = (i % 2) * 2
        col_val = col_lbl + 1
        ttk.Label(info_frame, text=f"{label}:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=row_i, column=col_lbl, sticky=tk.W, padx=(5, 2), pady=2)
        ttk.Label(info_frame, text=db_values[i], wraplength=300, justify=tk.LEFT).grid(
            row=row_i, column=col_val, sticky=tk.W, padx=(0, 8), pady=2)

    content_row = (len(pair_labels) + 1) // 2
    ttk.Label(info_frame, text="Content/Drug:",
              font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
        row=content_row, column=0, columnspan=4, sticky=tk.W, padx=5, pady=(8, 0))
    ttk.Label(info_frame, text=db_values[10] or '—', wraplength=720, justify=tk.LEFT).grid(
        row=content_row + 1, column=0, columnspan=4, sticky=tk.W, padx=5, pady=(0, 4))

    hsn_row = content_row + 2
    ttk.Label(info_frame, text="HSN Code:",
              font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
        row=hsn_row, column=0, sticky=tk.W, padx=5, pady=2)
    ttk.Label(info_frame, text=db_values[11] or '—').grid(
        row=hsn_row, column=1, columnspan=3, sticky=tk.W, padx=5, pady=2)

    # Purchase history — dedicated qty columns (long supplier names used to hide Qty)
    pf = ttk.LabelFrame(body, text="Purchase History")
    pf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    pt = _make_scrolled_treeview(
        pf,
        ('Date', 'Bill', 'Supplier', 'Qty', 'Free', 'Total', 'Rate', 'Amount'),
        height=5,
    )
    purch_cols = (
        ('Date', 'Date', 90),
        ('Bill', 'Bill', 80),
        ('Supplier', 'Supplier', 160),
        ('Qty', 'Qty', 70),
        ('Free', 'Free', 55),
        ('Total', 'Total' + (' tabs' if strip_type else ''), 90),
        ('Rate', 'Rate', 70),
        ('Amount', 'Amount', 80),
    )
    for col, hdr, w in purch_cols:
        pt.heading(col, text=hdr)
        pt.column(col, width=w, minwidth=50, stretch=(col == 'Supplier'))
    pf.configure(text='Purchase History — loading…')

    sf = ttk.LabelFrame(body, text='Sales History — loading…')
    sf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    st = _make_scrolled_treeview(
        sf,
        ('Date', 'Bill', 'Customer', 'Qty', 'Rate', 'Amount'),
        height=7,
    )
    for col, hdr, w in (
        ('Date', 'Date', 90),
        ('Bill', 'Bill', 80),
        ('Customer', 'Customer', 160),
        ('Qty', sales_qty_hdr, 110),
        ('Rate', 'Rate', 70),
        ('Amount', 'Amount', 80),
    ):
        st.heading(col, text=hdr)
        st.column(col, width=w, minwidth=50, stretch=(col == 'Customer'))

    note_var = tk.StringVar(value='Loading purchase/sales history for this batch…')
    ttk.Label(body, textvariable=note_var, wraplength=740, justify=tk.LEFT).pack(
        fill=tk.X, padx=12, pady=(0, 6),
    )

    name_key = _norm_med_key(name)
    related = []
    if name_key:
        try:
            for r in cursor.execute(
                """
                SELECT id, name, batch_no, COALESCE(stock_qty,0), COALESCE(unit,''), type
                FROM medicines
                WHERE COALESCE(deleted,0)=0 AND id<>?
                ORDER BY name, batch_no
                """,
                (medicine_id,),
            ):
                if _norm_med_key(r[1]) == name_key:
                    related.append(r)
        except Exception:
            related = []
    if related:
        rf = ttk.LabelFrame(
            body,
            text='Same medicine — other name/batch rows (stock is per batch)',
        )
        rf.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        rt = _make_scrolled_treeview(
            rf,
            ('Name', 'Batch', 'Stock', 'PurchTabs', 'Sold'),
            height=4,
        )
        for col, w in (
            ('Name', 200), ('Batch', 100), ('Stock', 80),
            ('PurchTabs', 90), ('Sold', 70),
        ):
            rt.heading(col, text=col)
            rt.column(col, width=w)
        for rid, rname, rbatch, rstock, runit, rtype in related:
            rtps = parse_tablets_per_stripe(runit) if is_strip_count_type(rtype or '', runit) else 1
            if rtps <= 0:
                rtps = 1
            try:
                rp = float(cursor.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(pi.qty,0)+COALESCE(pi.free_qty,0)),0)
                    FROM purchase_items pi JOIN purchases p ON p.id=pi.purchase_id
                    WHERE pi.medicine_id=? AND COALESCE(pi.deleted,0)=0 AND COALESCE(p.deleted,0)=0
                    """,
                    (rid,),
                ).fetchone()[0]) * rtps
                rs = float(cursor.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(si.qty,0)),0)
                    FROM sales_items si JOIN sales s ON s.id=si.sale_id
                    WHERE si.medicine_id=? AND COALESCE(si.deleted,0)=0
                      AND COALESCE(s.deleted,0)=0 AND COALESCE(s.is_autosave,0)=0
                    """,
                    (rid,),
                ).fetchone()[0])
            except Exception:
                rp, rs = 0.0, 0.0
            rt.insert(
                '', tk.END,
                values=(rname, rbatch or '', int(rstock or 0), int(round(rp)), int(round(rs))),
            )

    ttk.Button(dlg.footer, text='Close', command=dlg.destroy).pack(side=tk.RIGHT, padx=8)

    from core.background_workers import run_in_thread

    def _work():
        return _fetch_medicine_history_rows(
            conn, medicine_id, name, batch_no or '',
            strip_type=strip_type, tps=tps,
        )

    def _apply(payload):
        try:
            if not dlg.winfo_exists():
                return
        except Exception:
            return
        purch_rows_data, sales_rows_data, source = payload if payload else ([], [], 'offline')
        purch_tabs = 0.0
        purch_rows = 0
        for r in purch_rows_data:
            qty_s = float(r[3] or 0)
            free_s = float(r[4] or 0)
            total_s = qty_s + free_s
            tabs = total_s * tps if strip_type else total_s
            purch_tabs += tabs
            purch_rows += 1
            if strip_type:
                total_disp = f'{total_s:g} strip = {int(round(tabs))} tab'
            else:
                total_disp = f'{total_s:g}'
            pt.insert(
                '', tk.END,
                values=(
                    r[0], r[1], r[2],
                    f'{qty_s:g}',
                    f'{free_s:g}' if free_s else '0',
                    total_disp, r[5], r[6],
                ),
            )
        if strip_type:
            pf.configure(
                text=(
                    f'Purchase History — {purch_rows} bill(s), '
                    f'{purch_tabs:g} tablets ({purch_tabs / tps:g} strips)'
                )
            )
        else:
            pf.configure(text=f'Purchase History — {purch_rows} bill(s), qty {purch_tabs:g}')

        sold_tabs = 0.0
        sales_rows = 0
        for r in sales_rows_data:
            q = float(r[3] or 0)
            sold_tabs += q
            sales_rows += 1
            qty_disp = f'{q:g}'
            if strip_type and tps > 1:
                qty_disp += f' ({q / tps:g} strip)'
            from core.fy_serial import display_sales_bill_no
            bill_label = display_sales_bill_no(str(r[1] or ''))
            st.insert('', tk.END, values=(r[0], bill_label, r[2], qty_disp, r[4], r[5]))
        remain = max(0, int(round(purch_tabs - sold_tabs)))
        if strip_type:
            sf.configure(
                text=(
                    f'Sales History — {sales_rows} bill(s), '
                    f'sold {sold_tabs:g} tablets ({sold_tabs / tps:g} strips)  |  '
                    f'remain {remain} tablets ({remain / tps:g} strips)'
                )
            )
        else:
            sf.configure(
                text=(
                    f'Sales History — {sales_rows} bill(s), '
                    f'sold {sold_tabs:g}  |  remain {remain}'
                )
            )

        stock_n = float(stock_qty or 0)
        if purch_rows == 0 and sales_rows == 0 and stock_n > 0:
            note_var.set(
                'No purchase or sale bills found for this batch. '
                'Opening / imported / adjusted stock often has Current Stock '
                'without purchase history — that is expected. '
                f'(Source: {source}; shown stock {stock_n:g}.)'
            )
        elif purch_rows == 0 and stock_n > 0:
            note_var.set(
                'No purchase bills for this batch (opening/imported stock). '
                f'Sales found: {sales_rows}. Shown stock {stock_n:g}.'
            )
        else:
            note_var.set(
                f'This batch ledger: purchased {purch_tabs:g}, sold {sold_tabs:g}, '
                f'remain ~{remain} (shown {int(stock_n)}). History source: {source}.'
            )

    run_in_thread(
        _work,
        name='MedicineDetailsHistory',
        root=parent,
        on_success=_apply,
        on_error=lambda exc: note_var.set(f'Could not load history: {exc}'),
    )


def delete_medicine(conn, medicine_id, medicine_name, batch_no, refresh_callback):
    """Hide medicine from inventory lists (history is preserved)."""
    if not askyesno(
        "Confirm Delete",
        f"Delete {medicine_name} (Batch: {batch_no}) from inventory?\n\n"
        "Sales and purchase history will be kept.",
    ):
        return
    try:
        from core.medicine_visibility import hide_medicine
        hide_medicine(conn, int(medicine_id))
        from core.sync_coordinator import after_medicines_hidden
        after_medicines_hidden(conn, [int(medicine_id)])
        showinfo("Success", "Medicine deleted successfully!")
        refresh_callback()
        from core.page_refresh import refresh_open_pages
        refresh_open_pages(alert_monitoring=True, home=True)
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to delete medicine: {e}")


def delete_zero_stock_medicines(conn, refresh_callback, parent=None):
    """Hide all visible zero-stock / out-of-stock medicine batches."""
    from core.medicine_visibility import hide_all_out_of_stock_medicines

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM medicines m
        WHERE COALESCE(m.is_hidden, 0) = 0
          AND UPPER(TRIM(m.name)) IN (
              SELECT UPPER(TRIM(name))
              FROM medicines
              WHERE COALESCE(is_hidden, 0) = 0
              GROUP BY UPPER(TRIM(name))
              HAVING SUM(COALESCE(stock_qty, 0)) <= 0
          )
        """
    )
    count = int(cursor.fetchone()[0] or 0)
    if count <= 0:
        showinfo("Nothing to Remove", "No out-of-stock medicines to hide.", parent=parent)
        return

    if not askyesno(
        "Confirm Remove Out of Stock",
        f"Remove {count} out-of-stock medicine batch(es) from inventory?\n\n"
        "Fully out-of-stock medicines (all batches zero) will be hidden. "
        "Sales and purchase history are kept.",
        parent=parent,
    ):
        return

    try:
        from core.alert_monitoring_service import medicine_names_fully_out_of_stock
        cursor.execute(
            """
            SELECT id FROM medicines
            WHERE COALESCE(is_hidden, 0) = 0
            """
        )
        sync_ids = [r[0] for r in cursor.fetchall()]
        hidden = hide_all_out_of_stock_medicines(conn)
        from core.sync_coordinator import after_medicines_hidden
        after_medicines_hidden(conn, sync_ids)
        showinfo(
            "Done",
            f"Removed {hidden} out-of-stock medicine batch(es) from inventory.",
            parent=parent,
        )
        refresh_callback()
        from core.page_refresh import refresh_open_pages
        refresh_open_pages(alert_monitoring=True, home=True)
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to hide out-of-stock medicines: {e}", parent=parent)


def delete_expired_medicines(conn, refresh_callback, parent=None):
    """Hide all visible expired medicine batches."""
    from core.medicine_visibility import hide_all_expired_medicines

    cursor = conn.cursor()
    from core.alert_thresholds import parse_expiry
    from datetime import date

    today = date.today()
    cursor.execute(
        "SELECT id, COALESCE(expiry_date, '') FROM medicines "
        "WHERE COALESCE(is_hidden, 0) = 0 AND COALESCE(stock_qty, 0) > 0"
    )
    expired_rows = cursor.fetchall()
    sync_ids = [
        med_id for med_id, expiry_raw in expired_rows
        if (expiry_dt := parse_expiry(expiry_raw)) and expiry_dt < today
    ]
    count = len(sync_ids)
    if count <= 0:
        showinfo("Nothing to Remove", "No expired medicines to hide.", parent=parent)
        return

    if not askyesno(
        "Confirm Remove Expired",
        f"Remove {count} expired medicine batch(es) from inventory?\n\n"
        "They will be hidden from lists. Sales, purchase, and return history are kept.",
        parent=parent,
    ):
        return

    try:
        hidden = hide_all_expired_medicines(conn)
        from core.sync_coordinator import after_medicines_hidden
        after_medicines_hidden(conn, sync_ids)
        showinfo(
            "Done",
            f"Removed {hidden} expired medicine batch(es) from inventory.",
            parent=parent,
        )
        refresh_callback()
        from core.page_refresh import refresh_open_pages
        refresh_open_pages(alert_monitoring=True, home=True)
    except Exception as e:
        conn.rollback()
        showerror("Error", f"Failed to hide expired medicines: {e}", parent=parent)
