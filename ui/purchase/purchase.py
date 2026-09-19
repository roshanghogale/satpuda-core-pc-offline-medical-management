import tkinter as tk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import datetime
import sqlite3
from core.app_setup import load_app_mode
from core.master_medicine_service import search_master_names, upsert_master_medicine

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.layout_config import (
    load_layout, _DEFAULT_SCHEDULES, is_strip_count_type, get_med_types,
    parse_tablets_per_stripe,
)
from core.purchase_calculator import PurchaseCalculator
from core.purchase_service import (
    get_or_create_supplier, get_or_create_medicine,
    get_supplier_due, save_purchase as svc_save_purchase,
    update_purchase, finalize_autosave_purchase,
    refresh_inventory_if_loaded,
    lookup_medicine_details, lookup_supplier_last_purchase_rate,
    expiry_to_display,
)
try:
    from core.sync_v3.repositories.purchase_repository import (
        maybe_save_purchase,
        maybe_finalize_autosave,
    )
except Exception:
    maybe_save_purchase = svc_save_purchase
    maybe_finalize_autosave = finalize_autosave_purchase
from core.keyboard_registry import KeyboardRegistry, PageBindings
from ui.purchase.purchase_nav  import PurchaseNavMixin
from ui.purchase.purchase_form import PurchaseFormMixin
from ui.purchase.purchase_session import PurchaseSessionMixin


class PurchasePage(PurchaseSessionMixin, PurchaseNavMixin, PurchaseFormMixin):

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.purchase_items = []
        self._import_bill_mode = False
        self._import_invoice_summary = None
        self._editing_purchase_id = None
        self._edit_payment_snapshot = None
        self._suppress_purchase_calc = False
        self.editing_item_index = None
        self._last_calc = None
        self._master_ready = True
        self.gst_calc_method_var = tk.StringVar(master=parent, value="discount_before_gst")

        self._reload_layout_config()

        self._init_purchase_session()
        self._build_interface()
        self._register_keyboard()
        self._init_master_dropdown_state()
        try:
            from core.store_live_refresh import subscribe as live_sub

            def _on_live(_evt):
                try:
                    if not self._page_is_visible():
                        return
                    name = (self.supplier_name.get() or "").strip()
                    if name and not self._editing_purchase_id:
                        self.parent.after(250, self.load_supplier_details)
                except Exception:
                    pass

            self._live_token = live_sub(
                {"suppliers", "supplier_payments"},
                _on_live,
            )
        except Exception:
            self._live_token = None
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(150, self._focus_supplier_name)

    def _reload_layout_config(self):
        cfg = load_layout()
        self._med_types  = get_med_types()
        self._schedules  = cfg.get('schedules', list(_DEFAULT_SCHEDULES))
        self._type_qty   = {t: cfg.get(f'typeqty_{t}', 0) for t in self._med_types}
        self._sched_unit = {t: cfg.get(f'unit_{t}', '') for t in self._med_types}

    def refresh_layout_dropdowns(self):
        """Reload medicine types/units from disk (after Appearance settings change)."""
        self._reload_layout_config()
        if hasattr(self, 'medicine_type'):
            self.medicine_type.configure(values=self._med_types)
        if hasattr(self, 'schedule'):
            self.schedule.configure(values=self._schedules)

    def _page_is_visible(self):
        try:
            return bool(self.parent.winfo_ismapped())
        except tk.TclError:
            return False

    def _scroll_form_to_top(self):
        try:
            canvas = getattr(getattr(self, '_inner_frame', None), '_canvas', None)
            if canvas is not None:
                canvas.yview_moveto(0)
        except Exception:
            pass

    def _focus_supplier_name(self):
        if not self._page_is_visible():
            return
        self._scroll_form_to_top()
        try:
            if self.supplier_name.winfo_exists():
                self.supplier_name.focus()
        except tk.TclError:
            pass

    # ── shortcuts ─────────────────────────────────────────────────────────

    def _f2_import_bill(self, event=None):
        self.open_import_purchase_bill()
        return 'break'

    def _register_keyboard(self):
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(100, self._setup_arrow_nav)
        bindings = KeyboardRegistry.make_bindings(
            page_id='purchase',
            first_focus=self._focus_supplier_name,
            on_f5=self._save_purchase_shortcut,
            on_f6=self._focus_overall_discount,
            on_end=self._focus_payment_field,
            on_ctrl_shift_c=self._clear_form_shortcut,
            on_f10=self._open_recent_picker_shortcut,
            on_f11=self._open_last_record_shortcut,
            on_ctrl_shift_n=self._new_tab_shortcut,
            on_ctrl_shift_w=self._close_tab_shortcut,
            on_ctrl_prior=self._prev_tab_shortcut,
            on_ctrl_next=self._next_tab_shortcut,
            f2_target=self.items_tree,
            on_shift_f2=self._f2_import_bill,
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)
        self._bind_last_bill_shortcut()

    def _bind_last_bill_shortcut(self):
        seqs = ('<F11>', '<KeyPress-F11>')
        for seq in seqs:
            try:
                self._inner_frame.bind(seq, self._open_last_record_shortcut, add='+')
            except tk.TclError:
                pass

    def open_import_purchase_bill(self):
        from core.purchase_import_flow import import_purchase_bill_direct
        import_purchase_bill_direct(self.parent.winfo_toplevel(), self)

    def _rebind_mousewheel(self):
        pass

    # ── supplier loading ──────────────────────────────────────────────────

    def load_suppliers(self):
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_catalog import supplier_names

                self.supplier_name.configure(values=supplier_names())
                return
        except Exception:
            pass
        try:
            self.cursor.execute("SELECT name FROM suppliers ORDER BY name")
            self.supplier_name.configure(values=[r[0] for r in self.cursor.fetchall()])
        except sqlite3.Error:
            self.supplier_name.configure(values=[])

    def apply_reorder_prefill(self, prefill: dict | None = None):
        """Prefill supplier and medicine line from a pending reorder."""
        prefill = prefill or {}
        self._reorder_pending_order_id = int(prefill.get("order_id") or 0) or None
        supplier = (prefill.get("supplier_name") or "").strip()
        if supplier:
            self.load_suppliers()
            self.supplier_name.set(supplier)
            self.load_supplier_details()
        med = (prefill.get("medicine_name") or "").strip()
        if med:
            self.medicine_name.set(med)
            self.on_medicine_selected()
        rate = float(prefill.get("rate") or 0)
        qty = float(prefill.get("quantity") or 0)
        if rate > 0:
            self.rate.delete(0, tk.END)
            self.rate.insert(0, f"{rate:.2f}")
            try:
                self._tablet_price_from_strip()
            except Exception:
                pass
        if qty > 0:
            for attr in ("qty", "stripes", "quantity", "tablets"):
                field = getattr(self, attr, None)
                if field is not None and hasattr(field, "delete"):
                    try:
                        field.delete(0, tk.END)
                        field.insert(0, str(int(qty) if qty == int(qty) else qty))
                        break
                    except Exception:
                        pass
        pack = (prefill.get("pack_size") or "").strip()
        unit_field = getattr(self, "unit", None) or getattr(self, "pack_size", None)
        if pack and unit_field is not None and hasattr(unit_field, "set"):
            try:
                unit_field.set(pack)
            except Exception:
                pass

    def _on_supplier_name_key(self, event=None):
        from core.name_utils import purchase_tab_label
        if not getattr(self, '_doc_tabs', None):
            return
        default = f'Purchase {self._active_tab_idx + 1}'
        self._doc_tabs[self._active_tab_idx]['label'] = purchase_tab_label(
            self.supplier_name.get(), default,
        )
        if hasattr(self, '_refresh_tab_bar'):
            self._refresh_tab_bar()

    def load_supplier_details(self, event=None):
        from core.name_utils import storage_name_from_entry, purchase_tab_label

        name = self.supplier_name.get()
        if not name:
            return
        short = storage_name_from_entry(name)
        if short and short != name.strip().upper():
            self.supplier_name.set(short)
            name = short
        if getattr(self, '_doc_tabs', None):
            default = f'Purchase {self._active_tab_idx + 1}'
            self._doc_tabs[self._active_tab_idx]['label'] = purchase_tab_label(name, default)
            if hasattr(self, '_refresh_tab_bar'):
                self._refresh_tab_bar()

        seq = int(getattr(self, "_supplier_due_seq", 0) or 0) + 1
        self._supplier_due_seq = seq
        from core.background_workers import db_path_from_conn, run_in_thread

        db_path = db_path_from_conn(self.conn)
        editing = bool(self._editing_purchase_id)
        if not editing:
            try:
                self.previous_due_var.set("0.00")
            except Exception:
                pass

        def _work():
            try:
                from core.sync_prefs import is_online_mode
                if is_online_mode():
                    from core.online_catalog import find_supplier_by_name
                    from core.purchase_service import get_supplier_due

                    cached = find_supplier_by_name(name, force=False)
                    fresh = find_supplier_by_name(name, force=True)
                    row_doc = fresh or cached
                    due = credit = 0.0
                    if row_doc and not editing:
                        due, credit = get_supplier_due(self.conn, name)
                    return ("online", (row_doc, due, credit))
            except Exception:
                pass
            if not db_path:
                return ("offline", None)
            try:
                from core.db_utils import open_store_db
                from core.purchase_service import get_supplier_due

                conn = open_store_db(db_path, readonly=True, timeout=15.0)
                try:
                    row = conn.execute(
                        "SELECT address,phone,gstin,dl_numbers FROM suppliers WHERE name=?",
                        (name,),
                    ).fetchone()
                    due = credit = 0.0
                    if row and not editing:
                        due, credit = get_supplier_due(conn, name)
                    return ("offline", (row, due, credit))
                finally:
                    conn.close()
            except Exception:
                return ("offline", None)

        def _apply(payload):
            if seq != getattr(self, "_supplier_due_seq", 0):
                return
            if self.supplier_name.get().strip() != name:
                return
            kind, data = payload if payload else (None, None)
            try:
                if kind == "online":
                    row_doc, due, credit = (data if isinstance(data, tuple) else (data, 0.0, 0.0))
                    if row_doc:
                        vals = [
                            row_doc.get("address") or "",
                            row_doc.get("phone") or "",
                            row_doc.get("gstin") or "",
                            row_doc.get("dl_numbers") or "",
                        ]
                        for entry, val in zip(
                            [
                                self.supplier_address,
                                self.supplier_phone,
                                self.supplier_gstin,
                                self.supplier_dl,
                            ],
                            vals,
                        ):
                            entry.delete(0, tk.END)
                            entry.insert(0, val or "")
                        if not editing:
                            self.previous_due_var.set(f"{float(due):.2f}")
                            self.previous_credit_var.set(f"{float(credit):.2f}")
                            self.calculate_total()
                    else:
                        self.previous_due_var.set("0.00")
                        self.previous_credit_var.set("0.00")
                        self.calculate_total()
                    return
                row, due, credit = data if data else (None, 0.0, 0.0)
                if row:
                    for entry, val in zip(
                        [
                            self.supplier_address,
                            self.supplier_phone,
                            self.supplier_gstin,
                            self.supplier_dl,
                        ],
                        row,
                    ):
                        entry.delete(0, tk.END)
                        entry.insert(0, val or "")
                    if not editing:
                        self.previous_due_var.set(f"{float(due):.2f}")
                        self.previous_credit_var.set(f"{float(credit):.2f}")
                        self.calculate_total()
                else:
                    self.previous_due_var.set("0.00")
                    self.previous_credit_var.set("0.00")
                    self.calculate_total()
            except Exception:
                pass

        # Instant: apply cached contact fields; due comes from live FIFO in the worker.
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import find_supplier_by_name
                cached = find_supplier_by_name(name, force=False)
                if cached:
                    vals = [
                        cached.get("address") or "",
                        cached.get("phone") or "",
                        cached.get("gstin") or "",
                        cached.get("dl_numbers") or "",
                    ]
                    for entry, val in zip(
                        [
                            self.supplier_address,
                            self.supplier_phone,
                            self.supplier_gstin,
                            self.supplier_dl,
                        ],
                        vals,
                    ):
                        entry.delete(0, tk.END)
                        entry.insert(0, val or "")
        except Exception:
            pass

        run_in_thread(
            _work,
            name="PurchaseSupplierDue",
            root=self.parent,
            on_success=_apply,
        )

    def _load_supplier_due(self, supplier_name):
        try:
            total_due, credit = get_supplier_due(self.conn, supplier_name)
            self.previous_due_var.set(f"{total_due:.2f}")
            self.previous_credit_var.set(f"{credit:.2f}")
            self.calculate_total()
        except Exception:
            self.previous_due_var.set("0.00")
            self.previous_credit_var.set("0.00")

    # ── medicine name search ──────────────────────────────────────────────

    def _main_app_root(self):
        """Main Tk root (not a nested Toplevel such as edit purchase)."""
        w = self.parent
        while w is not None:
            try:
                top = w.winfo_toplevel()
                if getattr(top, "_main_app", None) is not None:
                    return top
            except Exception:
                pass
            try:
                w = w.master
            except tk.TclError:
                break
        return self.parent.winfo_toplevel()

    def _init_master_dropdown_state(self):
        main_root = self._main_app_root()
        mode = load_app_mode()
        self._master_ready = bool(getattr(main_root, "_master_ready", mode != "medical"))
        if mode == "medical" and not self._master_ready:
            self._set_medicine_dropdown_enabled(False, "Loading medicines...")
        else:
            self._set_medicine_dropdown_enabled(True)
        main_root.bind("<<MasterMedicineReady>>", self._on_master_ready, add="+")

    def _on_master_ready(self, event=None):
        self._master_ready = True
        self._set_medicine_dropdown_enabled(True)
        self._master_search()

    def _set_medicine_dropdown_enabled(self, enabled: bool, placeholder: str = ""):
        try:
            self.medicine_name.entry.configure(state="normal")
        except Exception:
            pass
        if hasattr(self, "medicine_master_status_var"):
            if enabled:
                self.medicine_master_status_var.set("")
            else:
                self.medicine_master_status_var.set(placeholder or "Preparing master medicines...")
        if not enabled:
            self.medicine_name.values = []
            if not self.medicine_name.get().strip() or self.medicine_name.get() == placeholder:
                self.medicine_name.set("")
            try:
                self.medicine_name.hide_list()
            except Exception:
                pass
        else:
            if placeholder and self.medicine_name.get() == placeholder:
                self.medicine_name.set("")
            if not self.medicine_name.values:
                self._master_search()
        if self._page_is_visible() and enabled:
            try:
                if self.medicine_name.entry.focus_get() == self.medicine_name.entry:
                    self.medicine_name.update_list()
            except Exception:
                pass

    def _reload_medicine_names(self):
        if getattr(self.medicine_name, '_suppress_focus_list', False):
            return
        self.medicine_name.entry.bind('<KeyRelease>', self._master_search, add='+')
        if getattr(self, '_med_search_pending', None):
            try:
                self.medicine_name.entry.after_cancel(self._med_search_pending)
            except Exception:
                pass
        self._master_search()

    def _master_search(self, event=None):
        if event and event.keysym in ('Up','Down','Left','Right','Return','Escape','Tab'):
            return
        if getattr(self, '_med_search_pending', None):
            try:
                self.medicine_name.entry.after_cancel(self._med_search_pending)
            except Exception:
                pass
        self._med_search_pending = self.medicine_name.entry.after(
            0 if event is None else 80, self._run_master_search)

    def _run_master_search(self):
        self._med_search_pending = None
        typed = self.medicine_name.entry.get().strip()
        gen = getattr(self, '_med_search_gen', 0) + 1
        self._med_search_gen = gen

        def _worker():
            mode = load_app_mode()
            cursor = self.conn.cursor()
            if mode == 'veterinary':
                if not typed:
                    cursor.execute(
                        "SELECT DISTINCT name FROM medicines ORDER BY name COLLATE NOCASE LIMIT 50")
                else:
                    cursor.execute(
                        "SELECT DISTINCT name FROM medicines WHERE name LIKE ? COLLATE NOCASE "
                        "ORDER BY name COLLATE NOCASE LIMIT 50", (f"%{typed}%",))
                return mode, True, [r[0] for r in cursor.fetchall()]

            if not self._master_ready:
                return mode, False, []

            master_names = []
            try:
                from core.sync_prefs import is_online_mode
                if is_online_mode() and typed:
                    from core.master_medicine_cloud import search_master_remote
                    remote = search_master_remote(typed, limit=50) or []
                    master_names = [
                        str(r.get("name") or "").strip()
                        for r in remote
                        if str(r.get("name") or "").strip()
                    ]
            except Exception:
                master_names = []
            if not master_names:
                master_names = search_master_names(typed, limit=50)
            if not typed:
                cursor.execute(
                    "SELECT DISTINCT name FROM medicines ORDER BY name COLLATE NOCASE LIMIT 50"
                )
            else:
                cursor.execute(
                    "SELECT DISTINCT name FROM medicines WHERE name LIKE ? COLLATE NOCASE "
                    "ORDER BY name COLLATE NOCASE LIMIT 50",
                    (f"%{typed}%",),
                )
            local_names = [r[0] for r in cursor.fetchall()]
            seen = set()
            names = []
            for n in master_names + local_names:
                key = (n or "").strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                names.append(n)
                if len(names) >= 50:
                    break
            return mode, True, names

        def _apply(result):
            if gen != getattr(self, '_med_search_gen', 0):
                return
            mode, ready, names = result
            try:
                if mode != 'veterinary' and not ready:
                    self._set_medicine_dropdown_enabled(False, "Loading medicines...")
                    return
                self.medicine_name.values = names
                if getattr(self.medicine_name, '_suppress_focus_list', False):
                    self.medicine_name.hide_list()
                else:
                    self.medicine_name.update_list()
            except Exception:
                pass

        from core.background_workers import run_in_thread
        run_in_thread(
            _worker,
            name='PurchaseMasterSearch',
            root=self.parent,
            on_success=_apply,
        )

    def on_medicine_selected(self, event=None):
        name = self.medicine_name.get().strip()
        if not name:
            return
        d = lookup_medicine_details(self.conn, name)
        if not d:
            return
        self.medicine_type.set(d.get('type', ''))
        numeric_keys = {'gst_percent', 'mrp', 'rate', 'discount_pct'}
        for attr, key in [
            ('manufacturer', 'manufacturer'),
            ('hsn_code', 'hsn_code'),
            ('gst_value', 'gst_percent'),
            ('mrp', 'mrp'),
            ('rate', 'rate'),
            ('content_drug', 'content_drug'),
            ('batch_no', 'batch_no'),
        ]:
            getattr(self, attr).delete(0, tk.END)
            value = d.get(key, '')
            if key in numeric_keys:
                if value not in ('', None) and float(value or 0) > 0:
                    text = "{:.2f}".format(float(value))
                else:
                    text = ''
            else:
                text = str(value or '')
            getattr(self, attr).insert(0, text)
        self.expiry_date.delete(0, tk.END)
        self.expiry_date.insert(0, expiry_to_display(d.get('expiry_date', '') or ''))
        self.schedule.set(d.get('schedule', ''))
        disc = float(d.get('discount_pct', 0) or 0)
        self.item_discount.delete(0, tk.END)
        self.item_discount.insert(0, f"{disc:.2f}" if disc else "0")
        if d.get('type'):
            self.on_type_change()
            self._fill_qty_from_unit(d.get('unit', ''), d.get('type', ''))
        try:
            self._tablet_price_from_strip()
        except Exception:
            pass

    def _fill_qty_from_unit(self, unit: str, med_type: str):
        unit = (unit or '').strip()
        if not unit:
            return
        sched_unit = self._sched_unit.get(med_type, '')
        if is_strip_count_type(med_type, sched_unit):
            if hasattr(self, 'tablets_per_stripe'):
                try:
                    tps = parse_tablets_per_stripe(unit)
                except (ValueError, TypeError):
                    tps = 1
                self.tablets_per_stripe.delete(0, tk.END)
                self.tablets_per_stripe.insert(0, str(max(1, int(tps))))
        elif hasattr(self, 'quantity'):
            self.quantity.delete(0, tk.END)
            self.quantity.insert(0, unit)

    def _on_rate_enter(self, event=None):
        try:
            new_rate = float(self.rate.get() or 0)
        except ValueError:
            self.manufacturer.focus_set()
            return 'break'
        name = self.medicine_name.get().strip()
        supplier = self.supplier_name.get().strip()
        if name and supplier and new_rate > 0:
            info = lookup_supplier_last_purchase_rate(self.conn, name, supplier)
            prev = float(info.get('rate', 0) or 0) if info else 0.0
            if prev > 0 and new_rate > prev + 0.001:
                bill = info.get('bill_number', '') or '—'
                pdate = info.get('purchase_date', '') or '—'
                showwarning(
                    "Higher Purchase Rate",
                    f"You purchased {name} from {supplier} at ₹{prev:.2f} last time "
                    f"(Bill {bill}, {pdate}).\n\n"
                    f"Current rate ₹{new_rate:.2f} is higher.",
                    parent=self.parent,
                )
        self.manufacturer.focus_set()
        return 'break'

    # ── add / edit / remove items ─────────────────────────────────────────

    def add_medicine(self):
        self._import_bill_mode = False
        if not self._validate_medicine_fields():
            return
        try:
            qty_data = self._read_qty_data()
            pricing  = self._read_pricing_data()
            if qty_data is None or pricing is None:
                return

            medicine_id = get_or_create_medicine(
                self.conn,
                self.medicine_name.get().strip(),
                self.medicine_type.get(),
                self.batch_no.get().strip(),
                self.expiry_date.get().strip(),
                pricing['gst_pct'], pricing['mrp'], pricing['rate'],
                self.manufacturer.get(),
                self.hsn_code.get(),
                self.schedule.get(),
                self.content_drug.get().strip(),
            )
            if not medicine_id:
                return

            if load_app_mode() == 'medical':
                qty_value = qty_data.get('quantity_value', '') or qty_data.get('tablets_per_stripe', '')
                upsert_master_medicine(
                    name=self.medicine_name.get().strip(),
                    manufacturer=self.manufacturer.get(),
                    mrp=pricing['mrp'],
                    content_drug=self.content_drug.get().strip(),
                    med_type=self.medicine_type.get(),
                    pack_size=str(qty_value),
                )

            # Build item with canonical keys used by PurchaseCalculator
            item = {
                'medicine_id':   medicine_id,
                'name':          self.medicine_name.get(),
                'type':          qty_data['type'],
                'batch':         self.batch_no.get(),
                'expiry':        self.expiry_date.get(),
                'qty':           qty_data['qty'],
                'free_qty':      qty_data['free_qty'],
                'rate':          pricing['rate'],
                'discount_pct':  pricing['discount_pct'],
                'gst_pct':       pricing['gst_pct'],
                'mrp':           pricing['mrp'],
                'hsn_code':      self.hsn_code.get(),
                'manufacturer':  self.manufacturer.get(),
                'schedule':      self.schedule.get(),
                'content_drug':  self.content_drug.get().strip(),
            }
            if is_strip_count_type(qty_data['type'], self._sched_unit.get(qty_data['type'], '')):
                item.update({
                    'tablets_per_stripe': qty_data['tablets_per_stripe'],
                    'total_tablets':      qty_data['total_tablets'],
                    'free_tablets':       qty_data['free_tablets'],
                })
            else:
                item['quantity_value'] = qty_data.get('quantity_value', '1')
                item['auto_unit']      = qty_data.get('auto_unit', '')

            if self.editing_item_index is not None:
                self.purchase_items[self.editing_item_index] = item
                self.editing_item_index = None
                self.add_btn.config(text="Add Medicine")
            else:
                self.purchase_items.append(item)

            self.calculate_total()
            self.update_items_tree()
            if load_app_mode() == 'medical':
                self._master_search()
            self._clear_medicine_fields()
            self.medicine_name.focus()

        except Exception as e:
            showerror("Error", f"Failed to add medicine: {e}")

    def _read_qty_data(self):
        med_type = self.medicine_type.get()
        try:
            if is_strip_count_type(med_type, self._sched_unit.get(med_type, '')):
                stripes = float(self.stripes.get() or 0)
                tps     = int(self.tablets_per_stripe.get() or 1)
                free    = float(self.free_stripes.get() or 0)
                return {'type': med_type, 'qty': stripes, 'free_qty': free,
                        'tablets_per_stripe': tps,
                        'total_tablets': stripes * tps,
                        'free_tablets':  free * tps}
            else:
                units = float(self.units.get() or 0)
                free  = float(self.free_items.get() or 0)
                return {'type': med_type, 'qty': units, 'free_qty': free,
                        'quantity_value': self.quantity.get() or '1',
                        'auto_unit': self._get_auto_unit()}
        except ValueError:
            showerror("Invalid Input", "Please enter valid quantities.")
            return None

    @staticmethod
    def _parse_number(raw, *, default=None, allow_empty=False):
        """Parse UI amount fields that may include ₹, %, commas, or spaces."""
        s = str(raw or "").strip()
        if not s:
            if allow_empty:
                return 0.0 if default is None else float(default)
            if default is not None:
                return float(default)
            raise ValueError("empty")
        s = (
            s.replace("₹", "")
            .replace(",", "")
            .replace("%", "")
            .replace(" ", "")
        )
        # Disc display like "5.0pct" should not appear; still strip trailing junk
        if s.endswith(("pct", "PCT")):
            s = s[:-3]
        return float(s)

    def _read_pricing_data(self):
        try:
            # Ensure tablet helper edits are applied to strip fields before save.
            try:
                self._strip_price_from_tablet()
            except Exception:
                pass
            rate = self._parse_number(self.rate.get())
            mrp = self._parse_number(self.mrp.get())
            gst_pct = self._parse_number(self.gst_value.get(), allow_empty=True)
            # Edit mode may show "5.0%" / "₹10.00" from format_discount_display
            discount_pct = self._parse_number(
                self.item_discount.get(), allow_empty=True
            )
            return {
                "rate": rate,
                "mrp": mrp,
                "gst_pct": gst_pct,
                "discount_pct": discount_pct,
            }
        except ValueError:
            showerror(
                "Invalid Input",
                "Please enter valid rate, MRP, GST and discount "
                "(numbers only; % / ₹ are OK).",
            )
            return None

    def remove_selected_item(self, event=None):
        sel = self.items_tree.selection()
        if not sel:
            return 'break'
        idx = self.items_tree.index(sel[0])
        if 0 <= idx < len(self.purchase_items):
            removed = self.purchase_items.pop(idx)
            self.update_items_tree()
            self.calculate_total()
            showinfo("Removed", f"Removed {removed['name']} from list.")
        return 'break'

    def edit_selected_item(self, event=None):
        sel = self.items_tree.selection()
        if not sel:
            return 'break'
        idx = self.items_tree.index(sel[0])
        if not (0 <= idx < len(self.purchase_items)):
            return 'break'
        item = self.purchase_items[idx]
        self.editing_item_index = idx

        self.medicine_name.set(item['name'])
        self.medicine_type.set(item['type'])
        # Show strip+tablet MRP/Rate slots for tablet/bolus/capsule before filling values.
        try:
            self.on_type_change()
        except Exception:
            pass
        for attr, key in [('hsn_code','hsn_code'), ('gst_value','gst_pct'),
                           ('mrp','mrp'), ('rate','rate'),
                           ('manufacturer','manufacturer'), ('batch_no','batch'),
                           ('expiry_date','expiry'), ('content_drug','content_drug')]:
            getattr(self, attr).delete(0, tk.END)
            val = item.get(key, '')
            if val is None:
                val = ''
            elif key in ('gst_pct', 'mrp', 'rate') and val != '':
                try:
                    val = f"{float(val):g}"
                except (TypeError, ValueError):
                    val = str(val)
            else:
                val = str(val)
            getattr(self, attr).insert(0, val)
        self.schedule.set(item.get('schedule', ''))
        self.item_discount.delete(0, tk.END)
        # Keep editable numeric % in the form (not "5.0%" / "₹…" display strings)
        try:
            disc_val = float(item.get("discount_pct") or 0)
        except (TypeError, ValueError):
            disc_val = 0.0
        self.item_discount.insert(0, f"{disc_val:g}" if disc_val else "0")

        if is_strip_count_type(item['type'], self._sched_unit.get(item['type'], '')):
            if not (hasattr(self, 'stripes') and self.stripes.winfo_exists()):
                self._create_tablet_qty_fields()
            self.stripes.delete(0, tk.END)
            self.stripes.insert(0, str(item['qty']))
            self.tablets_per_stripe.delete(0, tk.END)
            self.tablets_per_stripe.insert(0, str(item.get('tablets_per_stripe', 1)))
            self.free_stripes.delete(0, tk.END)
            self.free_stripes.insert(0, str(item['free_qty']))
            try:
                self._refresh_price_labels()
            except Exception:
                pass
        else:
            if not (hasattr(self, 'quantity') and self.quantity.winfo_exists()):
                self._create_other_qty_fields()
            self.quantity.delete(0, tk.END)
            self.quantity.insert(0, item.get('quantity_value', '1'))
            self.units.delete(0, tk.END)
            self.units.insert(0, str(item['qty']))
            self.free_items.delete(0, tk.END)
            self.free_items.insert(0, str(item['free_qty']))
            if item['type'].lower() == 'vaccine' and hasattr(self, 'vaccine_unit_var'):
                self.vaccine_unit_var.set(item.get('auto_unit', 'ml'))
        try:
            self._tablet_price_from_strip()
        except Exception:
            pass

        self.add_btn.config(text="Update Medicine")
        self.medicine_name.hide_list()
        self.medicine_type.hide_list()
        self._focus_medicine_combo()
        return 'break'

    # ── calculate — single source of truth via PurchaseCalculator ─────────

    def show_gst_slab_breakdown(self, event=None):
        """Open dialog with GST slab table (same layout as supplier bill)."""
        if not self.purchase_items:
            showwarning("GST Slab Table", "Add purchase items first.", parent=self.parent)
            return 'break' if event else None
        self.recalculate_purchase_totals()
        calc = self._last_calc or {}
        from widgets.purchase_gst_slab_dialog import show_purchase_gst_slab_dialog
        show_purchase_gst_slab_dialog(
            self.parent.winfo_toplevel(),
            calc,
            self.get_gst_calc_method(),
            self._import_invoice_summary if self._import_bill_mode else None,
        )
        return 'break' if event else None

    def _import_discount_rates(self) -> tuple:
        """(discount_base, cash_pct, product_pct) for Seema-style import bills."""
        inv = self._import_invoice_summary or {}
        return (
            float(inv.get('discount_base') or 0),
            float(inv.get('cash_discount_pct') or 0),
            float(inv.get('product_discount_pct') or 0),
        )

    def get_gst_calc_method(self) -> str:
        var = getattr(self, 'gst_calc_method_var', None)
        method = (var.get() if var else 'discount_before_gst') or 'discount_before_gst'
        if method not in ('discount_before_gst', 'discount_after_gst'):
            return 'discount_before_gst'
        return method

    def set_gst_calc_method(self, method: str) -> None:
        """Set tax/discount mode (auto on import; manual entry defaults to After GST)."""
        if method not in ('discount_before_gst', 'discount_after_gst'):
            method = 'discount_before_gst'
        var = getattr(self, 'gst_calc_method_var', None)
        if var is not None:
            var.set(method)

    def _discount_field_focused(self) -> bool:
        try:
            focused = self.parent.winfo_toplevel().focus_get()
        except Exception:
            return False
        return focused in (
            str(self.overall_discount),
            str(self.overall_discount_pct),
            str(self.expenditure_entry),
        )

    def _overall_discount_base(self) -> float:
        """Medicine gross (before overall discount) — base for % ↔ ₹ sync."""
        pre = PurchaseCalculator(
            items=self.purchase_items,
            overall_discount=0,
            rounding=0,
            previous_due=0,
            previous_credit=0,
            amount_paid=0,
            gst_calc_method=self.get_gst_calc_method(),
        ).calculate()
        return float(pre.get('gross_subtotal', 0) or pre.get('gross_total', 0) or 0)

    def _discount_pct_to_rupees(self, pct: float, discount_base: float) -> float:
        if not discount_base or pct <= 0:
            return 0.0
        return round(discount_base * pct / 100, 2)

    def _discount_rupees_to_pct(self, rs: float, discount_base: float) -> float:
        if not discount_base or rs <= 0:
            return 0.0
        return round(rs * 100 / discount_base, 2)

    def sync_overall_discount_fields(self, source='rupees', quiet=False):
        """Keep overall disc ₹ and % in sync."""
        discount_base = self._overall_discount_base()

        def _f(widget):
            try:
                return float(widget.get() or 0)
            except (ValueError, tk.TclError):
                return 0.0

        if source == 'pct':
            pct = _f(self.overall_discount_pct)
            rs = self._discount_pct_to_rupees(pct, discount_base)
            if quiet:
                self._set_entry_quiet(self.overall_discount, rs, lambda v: f"{v:.2f}")
            else:
                self.overall_discount.delete(0, tk.END)
                self.overall_discount.insert(0, f"{rs:.2f}")
        else:
            rs = _f(self.overall_discount)
            pct = self._discount_rupees_to_pct(rs, discount_base)
            if quiet:
                self._set_entry_quiet(self.overall_discount_pct, pct, lambda v: f"{v:.2f}")
            else:
                self.overall_discount_pct.delete(0, tk.END)
                self.overall_discount_pct.insert(0, f"{pct:.2f}")

    def _set_entry_quiet(self, entry, value, fmt=None):
        """Update an entry without triggering KeyRelease recalc handlers."""
        prev_suppress = self._suppress_purchase_calc
        self._suppress_purchase_calc = True
        try:
            entry.delete(0, tk.END)
            text = fmt(value) if fmt else str(value)
            entry.insert(0, text)
        finally:
            self._suppress_purchase_calc = prev_suppress

    def _read_payment_fields(self):
        """Read cash/online entries; empty fields count as zero."""
        def _f(entry):
            try:
                raw = (entry.get() or '').strip()
                return float(raw) if raw else 0.0
            except (ValueError, tk.TclError):
                return 0.0
        cash = _f(self.cash_paid)
        online = _f(self.online_paid)
        return cash, online, round(cash + online, 2)

    def _edit_payment_context(self):
        snap = getattr(self, '_edit_payment_snapshot', None) or {}
        def _f(var_or_widget):
            try:
                v = var_or_widget.get() if hasattr(var_or_widget, 'get') else var_or_widget
                return float(v or 0)
            except (ValueError, tk.TclError):
                return 0.0
        cash, online, total = self._read_payment_fields()
        if snap.get('cash_paid') is not None or snap.get('online_paid') is not None:
            cash = float(snap.get('cash_paid', cash) or 0)
            online = float(snap.get('online_paid', online) or 0)
            total = round(cash + online, 2)
        return {
            'previous_due': float(snap.get('previous_due', _f(self.previous_due_var)) or 0),
            'previous_credit': float(snap.get('previous_credit', _f(self.previous_credit_var)) or 0),
            'cash_paid': cash,
            'online_paid': online,
            'amount_paid': total,
            '_f': _f,
        }

    def _apply_calc_result(self, result: dict):
        self._last_calc = result
        try:
            rounding_focused = (
                self.rounding_entry == self.rounding_entry.winfo_toplevel().focus_get()
            )
        except Exception:
            rounding_focused = False
        if not rounding_focused:
            self._set_entry_quiet(
                self.rounding_entry, result.get('rounding', 0), lambda v: f"{v:.2f}",
            )
        if not self._discount_field_focused():
            self._set_entry_quiet(
                self.overall_discount, result.get('overall_discount', 0), lambda v: f"{v:.2f}",
            )
            self.sync_overall_discount_fields('rupees', quiet=True)

        gross = float(result.get('gross_subtotal') or result.get('gross_total') or 0)
        if hasattr(self, 'gross_var'):
            self.gross_var.set(f"₹{gross:.2f}")
        self.subtotal_var.set(f"₹{result['subtotal']:.2f}")
        self.cgst_var.set(f"₹{result['cgst']:.2f}")
        self.sgst_var.set(f"₹{result['sgst']:.2f}")
        self.total_amount_var.set(f"₹{result['total_amount']:.2f}")
        self.need_to_pay_var.set(f"₹{result['need_to_pay']:.2f}")
        self.final_amount_var.set(f"₹{result['final_amount']:.2f}")
        self.current_credit_var.set(f"₹{result['current_credit']:.2f}")
        if result['total_due'] > 0.01:
            self.total_due_var.set(f"Due: ₹{result['total_due']:.2f}")
            self.due_label.config(foreground='red')
        else:
            self.total_due_var.set("₹0.00")
            self.due_label.config(foreground='green')
        total_paid = float(result.get('amount_paid') or 0)
        if hasattr(self, 'amount_paid_var'):
            self.amount_paid_var.set(f"₹{total_paid:.2f}" if total_paid else "")
        self.update_items_tree()
        try:
            self.parent.update_idletasks()
        except Exception:
            pass

    def apply_stored_edit_totals(self, header: dict):
        """Show saved purchase totals when opening edit — no recalc until user changes lines."""
        self._last_calc = dict(header)
        gross = float(header.get('gross_subtotal') or 0)
        if hasattr(self, 'gross_var'):
            self.gross_var.set(f"₹{gross:.2f}")
        self.subtotal_var.set(f"₹{float(header.get('subtotal') or 0):.2f}")
        self.cgst_var.set(f"₹{float(header.get('cgst') or 0):.2f}")
        self.sgst_var.set(f"₹{float(header.get('sgst') or 0):.2f}")
        self.total_amount_var.set(f"₹{float(header.get('total_amount') or 0):.2f}")
        self.need_to_pay_var.set(f"₹{float(header.get('need_to_pay') or 0):.2f}")
        self.final_amount_var.set(f"₹{float(header.get('final_amount') or 0):.2f}")
        self.current_credit_var.set(f"₹{float(header.get('current_credit') or 0):.2f}")
        entry_due = float(header.get('due_amount', header.get('due', 0)) or 0)
        if entry_due > 0.01:
            self.total_due_var.set(f"Due: ₹{entry_due:.2f}")
            self.due_label.config(foreground='red')
        else:
            self.total_due_var.set("₹0.00")
            self.due_label.config(foreground='green')
        self.update_items_tree()

    def _run_purchase_calc(self, force_auto_round=False):
        """Slab-based purchase calc — shared by calculate_total and Recalculate."""
        def _f(var_or_widget):
            try:
                v = var_or_widget.get() if hasattr(var_or_widget, 'get') else var_or_widget
                return float(v or 0)
            except (ValueError, tk.TclError):
                return 0.0

        ctx = self._edit_payment_context() if self._editing_purchase_id else {
            'previous_due': _f(self.previous_due_var),
            'previous_credit': _f(self.previous_credit_var),
            **dict(zip(
                ('cash_paid', 'online_paid', 'amount_paid'),
                self._read_payment_fields(),
            )),
            '_f': _f,
        }
        _f = ctx['_f']

        overall_disc = _f(self.overall_discount)

        if self._import_bill_mode:
            inv = self._import_invoice_summary or {}
            inv_disc = float(inv.get('parsed_total_discount') or 0)
            if not inv_disc:
                cash = float(inv.get('cash_discount') or 0)
                prod = float(inv.get('product_discount') or 0)
                if cash > 0 and prod > 0 and abs(cash - prod) <= 0.05:
                    inv_disc = cash
                else:
                    inv_disc = round(cash + prod, 2)
            if inv_disc and not overall_disc and not self._discount_field_focused():
                overall_disc = inv_disc
                self._set_entry_quiet(self.overall_discount, inv_disc, lambda v: f"{v:.2f}")
                self.sync_overall_discount_fields('rupees')
                overall_disc = _f(self.overall_discount)

        try:
            rounding_focused = (
                not force_auto_round
                and self.rounding_entry == self.rounding_entry.winfo_toplevel().focus_get()
            )
        except Exception:
            rounding_focused = False

        rounding = _f(self.rounding_entry) if (
            rounding_focused or (self._editing_purchase_id and not force_auto_round)
        ) else 0.0
        expenditure = _f(self.expenditure_entry)

        calc_method = self.get_gst_calc_method()
        if self._import_bill_mode:
            inv = self._import_invoice_summary or {}
            from core.purchase_invoice_engine import reconcile_purchase_items_to_footer_slabs
            reconcile_purchase_items_to_footer_slabs(self.purchase_items, inv)
            import_method = inv.get('gst_calc_method')
            if import_method in ('discount_before_gst', 'discount_after_gst'):
                calc_method = import_method

        result = PurchaseCalculator(
            items=self.purchase_items,
            overall_discount=overall_disc,
            rounding=rounding,
            previous_due=ctx['previous_due'],
            previous_credit=ctx['previous_credit'],
            cash_paid=ctx['cash_paid'],
            online_paid=ctx['online_paid'],
            expenditure=expenditure,
            gst_calc_method=calc_method,
        ).calculate()

        if self._import_bill_mode:
            inv = self._import_invoice_summary or {}
            use_footer = bool(
                inv.get('use_footer_totals')
                or (
                    inv.get('footer_gst_authoritative')
                    and float(inv.get('invoice_total') or 0) > 0
                    and float(inv.get('total_cgst') or 0) + float(inv.get('total_sgst') or 0) > 0
                )
            )
            from core.purchase_invoice_engine import reconcile_import_slab_breakdown
            if use_footer or inv.get('footer_gst_slabs'):
                if use_footer:
                    footer_cgst = round(float(inv.get('total_cgst') or 0), 2)
                    footer_sgst = round(float(inv.get('total_sgst') or 0), 2)
                    inv_net = round(float(inv.get('invoice_total') or 0), 2)
                    inv_round = round(float(inv.get('round_off') or 0), 2)
                    footer_gst = round(footer_cgst + footer_sgst, 2)
                    if inv_net > 0 and footer_gst > 0:
                        supplier_gross = float(inv.get('gross_amount') or 0)
                        result['cgst'] = footer_cgst
                        result['sgst'] = footer_sgst
                        result['total_gst'] = footer_gst
                        result['pre_round_total'] = round(inv_net - inv_round, 2)
                        result['total_amount'] = inv_net
                        result['rounding'] = inv_round if inv_round else round(
                            inv_net - result['pre_round_total'], 2,
                        )
                        if supplier_gross > float(result.get('gross_subtotal') or 0):
                            result['supplier_gross'] = supplier_gross
                        result['use_footer_totals'] = True
                        final_amount = round(inv_net + expenditure, 2)
                        need_to_pay = round(
                            final_amount + ctx['previous_due'] - ctx['previous_credit'], 2,
                        )
                        due = round(max(0.0, need_to_pay - ctx['amount_paid']), 2)
                        if due < 0.01:
                            due = 0.0
                        # Overpay past bill+previous due only (not leftover prev credit).
                        overpay = round(
                            ctx['amount_paid'] - (
                                final_amount + ctx['previous_due']
                            ),
                            2,
                        )
                        current_credit = overpay if overpay > 0.01 else 0.0
                        result['final_amount'] = final_amount
                        result['need_to_pay'] = need_to_pay
                        result['due'] = due
                        result['current_credit'] = current_credit
                        result['total_due'] = due
                        result['due_amount'] = due
                        result['credit_amount'] = current_credit
                        result['bill_cleared'] = 1 if due == 0 else 0
                        result['account_cleared'] = 1 if due == 0 else 0
                result = reconcile_import_slab_breakdown(result, inv)

        return result

    def recalculate_purchase_totals(self, event=None):
        """Full recalc: GST slabs, discount, subtotal, GST, rounding, payment totals."""
        if not self.purchase_items:
            showwarning("Recalculate", "No purchase items to calculate.", parent=self.parent)
            return 'break' if event else None

        prev = dict(self._last_calc) if self._last_calc else None
        self._calc_event = event
        try:
            result = self._run_purchase_calc(force_auto_round=True)
        finally:
            self._calc_event = None

        self._apply_calc_result(result)

        if event is not None and prev:
            same = (
                abs(float(prev.get('cgst', 0)) - float(result.get('cgst', 0))) < 0.02
                and abs(float(prev.get('total_amount', 0)) - float(result.get('total_amount', 0))) < 0.02
            )
            if same:
                showinfo(
                    "Recalculate",
                    "Totals unchanged.\n\n"
                    "If GST still looks wrong, check GST% on each line "
                    "(exempt items should be 0%), then press Recalculate again.",
                    parent=self.parent,
                )
        return 'break' if event else None

    def calculate_total(self, event=None):
        if self._suppress_purchase_calc:
            return
        if not self.purchase_items:
            return

        self._calc_event = event
        try:
            result = self._run_purchase_calc(force_auto_round=bool(self._editing_purchase_id))
        finally:
            self._calc_event = None

        self._apply_calc_result(result)

    # ── save ──────────────────────────────────────────────────────────────

    def _save_purchase_shortcut(self, event=None):
        self.save_purchase()
        return 'break'

    def save_purchase(self):
        if getattr(self, "_save_busy", False):
            return
        if not self.supplier_name.get().strip():
            showwarning("Missing", "Please enter supplier name.")
            return
        if not self.purchase_items:
            showwarning("No Items", "Please add items to the purchase.")
            return

        self.recalculate_purchase_totals()
        result = self._last_calc
        if not result:
            showerror("Error", "Calculation failed.")
            return

        from core.background_workers import run_on_ui_with_busy

        # Saved as typed; what looks mistyped is only pointed out afterwards. The same
        # supplier's bill number is looked up before the save, which would find itself.
        try:
            from core.save_warnings import (
                duplicate_supplier_bill_warnings,
                medicine_type_lookup,
                purchase_line_warnings,
            )

            warnings = duplicate_supplier_bill_warnings(
                self.conn,
                supplier_name=self.supplier_name.get().strip(),
                bill_number=self.bill_number.get().strip(),
                exclude_purchase_id=self._editing_purchase_id or self._autosave_purchase_id or 0,
            ) + purchase_line_warnings(
                self.purchase_items,
                self.purchase_date.get().strip(),
                medicine_type=medicine_type_lookup(self.conn),
            )
        except Exception:
            warnings = []

        self._save_busy = True

        def _do_save():
            supplier_id = get_or_create_supplier(
                self.conn,
                self.supplier_name.get().strip(),
                self.supplier_address.get(),
                self.supplier_phone.get(),
                self.supplier_gstin.get(),
                self.supplier_dl.get(),
            )
            if self._editing_purchase_id and not self._autosave_purchase_id:
                update_purchase(
                    self.conn,
                    self._editing_purchase_id,
                    supplier_id,
                    self.bill_number.get().strip(),
                    self.purchase_date.get().strip(),
                    result,
                    self.purchase_items,
                )
                purchase_no = str(self._editing_purchase_id)
                try:
                    from core.sync_prefs import is_online_mode
                    if is_online_mode():
                        from core.server_crud import get_doc
                        doc = get_doc("purchases", int(self._editing_purchase_id)) or {}
                        purchase_no = str(
                            doc.get("purchase_no") or doc.get("bill_number") or purchase_no
                        )
                    else:
                        cur = self.conn.cursor()
                        cur.execute(
                            "SELECT purchase_no FROM purchases WHERE id=?",
                            (self._editing_purchase_id,),
                        )
                        row = cur.fetchone()
                        if row and row[0]:
                            purchase_no = row[0]
                except Exception:
                    pass
                return purchase_no
            if self._autosave_purchase_id:
                return maybe_finalize_autosave(
                    self.conn,
                    self._autosave_purchase_id,
                    supplier_id,
                    self.bill_number.get().strip(),
                    self.purchase_date.get().strip(),
                    result,
                    self.purchase_items,
                )
            return maybe_save_purchase(
                self.conn,
                supplier_id,
                self.purchase_date.get().strip(),
                self.bill_number.get().strip(),
                result,
                self.purchase_items,
            )

        try:
            purchase_no = run_on_ui_with_busy(
                self.parent,
                "Saving Purchase",
                _do_save,
                message="Saving purchase… please wait.",
            )
        except Exception as exc:
            self._save_busy = False
            showerror("Error", f"Failed to save purchase:\n{exc}")
            return
        self._save_busy = False

        was_editing = bool(self._editing_purchase_id)
        pdate = (self.purchase_date.get() or "").strip()
        # Clear before dialog so OK returns focus to supplier (not during dialog).
        if not was_editing:
            self.clear_form(focus_supplier=False)
            if hasattr(self, '_doc_tabs') and self._doc_tabs:
                n = self._active_tab_idx + 1
                self._doc_tabs[self._active_tab_idx] = self._empty_tab_state(
                    self._default_tab_label(n),
                )
                self._normalize_default_tab_labels()
                self._refresh_tab_bar()

        showinfo(
            "Success",
            f"Purchase {purchase_no} saved successfully!\n"
            f"Date: {pdate or '(today)'}\n"
            f"Open Purchase History and check this date "
            f"(image bills often use the invoice date, not today)."
            + ("\n\nPlease check:\n• " + "\n• ".join(warnings) if warnings else ""),
            parent=self.parent,
            focus_after=(None if was_editing else self._focus_supplier_name),
        )
        reorder_oid = getattr(self, "_reorder_pending_order_id", None)
        if reorder_oid:
            try:
                from core.reorder_service import complete_pending_order_from_purchase
                complete_pending_order_from_purchase(self.conn, int(reorder_oid))
            except Exception:
                pass
            self._reorder_pending_order_id = None
        try:
            from core.page_refresh import refresh_after_purchase
            refresh_after_purchase(self.parent)
        except Exception:
            pass
        try:
            from core.sync_v3.data_change_bus import emit
            emit("purchases", local=True)
        except Exception:
            pass
        # Force purchase history reload even if page is cached/stale.
        try:
            app = getattr(self.parent, "_app_instance", None) or getattr(
                self.parent.winfo_toplevel(), "_app_instance", None
            )
            hist = getattr(app, "_purchase_history_page", None) if app else None
            if hist is not None:
                # Clear sticky search filters that can hide the new row.
                for attr in (
                    "serial_filter",
                    "medicine_filter",
                    "batch_filter",
                    "supplier_filter",
                ):
                    w = getattr(hist, attr, None)
                    if w is None:
                        continue
                    try:
                        if hasattr(w, "set"):
                            w.set("")
                        elif hasattr(w, "entry"):
                            w.entry.delete(0, "end")
                        else:
                            w.delete(0, "end")
                    except Exception:
                        pass
                try:
                    if hasattr(hist, "due_filter") and hist.due_filter:
                        hist.due_filter.set("All")
                except Exception:
                    pass
                # Ensure FY/all scope includes the saved bill date.
                try:
                    from core.history_prefs import current_fy_bounds, load_history_scope

                    if load_history_scope() == "current_fy" and pdate:
                        fd, td = current_fy_bounds()
                        if hasattr(hist, "_set_date_filter"):
                            hist._set_date_filter(hist.from_date, fd)
                            hist._set_date_filter(hist.to_date, td)
                except Exception:
                    pass
                if hasattr(hist, "load_purchase_history"):
                    hist.load_purchase_history()
                elif hasattr(hist, "queue_sync_refresh"):
                    hist.queue_sync_refresh()
        except Exception:
            pass
        if was_editing:
            self.clear_form(focus_supplier=False)
            self._editing_purchase_id = None

    # ── validation ────────────────────────────────────────────────────────

    def _validate_medicine_fields(self):
        from core.focus_chain import safe_focus

        def _warn(msg, widget):
            showwarning("Missing", msg, parent=self.parent,
                        focus_after=lambda w=widget: safe_focus(w))
            return False

        if not self.medicine_name.get().strip():
            return _warn("Please enter medicine name.", self.medicine_name)
        if not self.medicine_type.get():
            return _warn("Please select medicine type.", self.medicine_type)
        if not self.batch_no.get().strip():
            return _warn("Please enter batch number.", self.batch_no)
        expiry = self.expiry_date.get().strip()
        if not expiry:
            return _warn("Please enter expiry date (MM/YY).", self.expiry_date)
        try:
            if '/' not in expiry:
                raise ValueError
            month, year = expiry.split('/')
            if len(month) != 2 or len(year) != 2 or not month.isdigit() or not year.isdigit():
                raise ValueError
            if not (1 <= int(month) <= 12):
                raise ValueError
        except ValueError:
            showwarning(
                "Invalid Format", "Expiry must be MM/YY (e.g. 12/26).",
                parent=self.parent,
                focus_after=lambda: safe_focus(self.expiry_date),
            )
            return False
        return True

    # ── clear ─────────────────────────────────────────────────────────────

    def _clear_medicine_fields(self):
        self.medicine_name.set('')
        self.medicine_type.set('')
        for attr in ('hsn_code','gst_value','mrp','rate','manufacturer',
                     'batch_no','expiry_date','content_drug'):
            getattr(self, attr).delete(0, tk.END)
        for attr in ('tablet_mrp', 'tablet_rate'):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.delete(0, tk.END)
                except tk.TclError:
                    pass
        self.schedule.set('')
        self.item_discount.delete(0, tk.END)
        self.item_discount.insert(0, "0")
        self.editing_item_index = None
        self.add_btn.config(text="Add Medicine")
        try:
            for w in self.qty_frame.winfo_children():
                if isinstance(w, ttk.Entry):
                    w.delete(0, tk.END)
                    if 'free' in str(w):
                        w.insert(0, "0")
        except tk.TclError:
            pass
        try:
            self._refresh_price_labels()
        except Exception:
            pass

    def _clear_form_shortcut(self, event=None):
        self.clear_form()
        return 'break'

    def _discard_autosave_draft(self):
        purchase_id = getattr(self, '_autosave_purchase_id', None)
        if not purchase_id:
            return
        try:
            from core.purchase_service import delete_autosave_purchase
            delete_autosave_purchase(self.conn, purchase_id)
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass

    def clear_form(self, keep_tab=False, focus_supplier=True):
        self._discard_autosave_draft()
        self._reorder_pending_order_id = None
        for attr in ('supplier_address','supplier_phone','supplier_gstin',
                     'supplier_dl','bill_number'):
            getattr(self, attr).delete(0, tk.END)
        self.supplier_name.set('')
        self.purchase_date.delete(0, tk.END)
        self.purchase_date.insert(0, datetime.now().strftime('%Y-%m-%d'))

        self._clear_medicine_fields()
        self.purchase_items.clear()
        self.update_items_tree()

        for var in (self.gross_var, self.subtotal_var, self.cgst_var, self.sgst_var,
                    self.total_amount_var, self.need_to_pay_var,
                    self.final_amount_var, self.current_credit_var, self.total_due_var):
            var.set("0.00")
        self.previous_due_var.set("0.00")
        self.previous_credit_var.set("0.00")

        for attr, default in [('overall_discount_pct', '0'),
                               ('overall_discount', '0'),
                               ('rounding_entry', '0.00'),
                               ('expenditure_entry', '0.00'),
                               ('cash_paid', ''),
                               ('online_paid', '')]:
            e = getattr(self, attr)
            e.delete(0, tk.END)
            if default:
                e.insert(0, default)
        if hasattr(self, 'amount_paid_var'):
            self.amount_paid_var.set('')

        self._last_calc = None
        self._import_bill_mode = False
        self._import_invoice_summary = None
        self._editing_purchase_id = None
        self._autosave_purchase_id = None
        self._edit_payment_snapshot = None
        if hasattr(self, 'gst_calc_method_var'):
            self.set_gst_calc_method('discount_before_gst')
        if not keep_tab and hasattr(self, '_doc_tabs') and self._doc_tabs:
            n = self._active_tab_idx + 1
            self._doc_tabs[self._active_tab_idx] = self._empty_tab_state(
                self._default_tab_label(n),
            )
            self._normalize_default_tab_labels()
            self._refresh_tab_bar()
        if focus_supplier:
            self._focus_supplier_name()
        self._reset_persistent()

    def _reset_persistent(self):
        try:
            root = self.parent.winfo_toplevel()
            for child in root.winfo_children():
                if hasattr(child, '_purchase_page') and child._purchase_page is self:
                    child._purchase_page = None
                    break
        except Exception:
            pass
