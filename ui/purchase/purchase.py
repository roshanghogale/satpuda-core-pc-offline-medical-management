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

from core.layout_config import load_layout, _DEFAULT_MED_TYPES, _DEFAULT_SCHEDULES, is_strip_count_type
from core.purchase_calculator import PurchaseCalculator
from core.calc_engine import auto_round
from core.purchase_invoice_engine import compute_import_bill_totals
from core.purchase_service import (
    get_or_create_supplier, get_or_create_medicine,
    get_supplier_due, save_purchase as svc_save_purchase,
    lookup_medicine_details,
)
from core.keyboard_registry import KeyboardRegistry, PageBindings
from ui.purchase.purchase_nav  import PurchaseNavMixin
from ui.purchase.purchase_form import PurchaseFormMixin


class PurchasePage(PurchaseNavMixin, PurchaseFormMixin):

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.purchase_items = []
        self._import_bill_mode = False
        self._import_invoice_summary = None
        self._editing_purchase_id = None
        self._edit_payment_snapshot = None
        self.editing_item_index = None
        self._last_calc = None
        self._master_ready = True

        cfg = load_layout()
        self._med_types  = cfg.get('med_types',  list(_DEFAULT_MED_TYPES))
        self._schedules  = cfg.get('schedules',  list(_DEFAULT_SCHEDULES))
        self._type_qty   = {t: cfg.get(f'typeqty_{t}', 0) for t in self._med_types}
        self._sched_unit = {t: cfg.get(f'unit_{t}', '')  for t in self._med_types}

        self._build_interface()
        self._register_keyboard()
        self._init_master_dropdown_state()
        self.parent.after(150, self._focus_supplier_name)

    def _focus_supplier_name(self):
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
        self.parent.after(100, self._setup_arrow_nav)
        bindings = KeyboardRegistry.make_bindings(
            page_id='purchase',
            first_focus=self._focus_supplier_name,
            on_f5=self._save_purchase_shortcut,
            on_f6=self._focus_overall_discount,
            on_end=self._focus_payment_field,
            on_ctrl_shift_c=self._clear_form_shortcut,
            f2_target=self.items_tree,
            on_shift_f2=self._f2_import_bill,
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

    def open_import_purchase_bill(self):
        from core.purchase_import_flow import import_purchase_bill_direct
        import_purchase_bill_direct(self.parent.winfo_toplevel(), self)

    def _rebind_mousewheel(self):
        pass

    # ── supplier loading ──────────────────────────────────────────────────

    def load_suppliers(self):
        try:
            self.cursor.execute("SELECT name FROM suppliers ORDER BY name")
            self.supplier_name.configure(values=[r[0] for r in self.cursor.fetchall()])
        except sqlite3.Error:
            self.supplier_name.configure(values=[])

    def load_supplier_details(self, event=None):
        name = self.supplier_name.get()
        if not name:
            return
        try:
            self.cursor.execute(
                "SELECT address,phone,gstin,dl_numbers FROM suppliers WHERE name=?", (name,))
            row = self.cursor.fetchone()
            if row:
                # Existing supplier — fill details and load their due
                for entry, val in zip(
                    [self.supplier_address, self.supplier_phone,
                     self.supplier_gstin, self.supplier_dl], row):
                    entry.delete(0, tk.END)
                    entry.insert(0, val or '')
                if not self._editing_purchase_id:
                    self._load_supplier_due(name)
            else:
                # New supplier — clear previous due/credit so it starts at zero
                self.previous_due_var.set("0.00")
                self.previous_credit_var.set("0.00")
                self.calculate_total()
        except sqlite3.Error:
            showerror("Database Error", "Failed to load supplier details.")

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
        state = "normal" if enabled else "disabled"
        try:
            self.medicine_name.entry.configure(state=state)
        except Exception:
            pass
        if hasattr(self, "medicine_master_status_var"):
            if enabled:
                self.medicine_master_status_var.set("")
            else:
                self.medicine_master_status_var.set(placeholder or "Preparing master medicines...")
        if not enabled:
            self.medicine_name.values = [placeholder] if placeholder else []
            self.medicine_name.set(placeholder)
        else:
            if placeholder and self.medicine_name.get() == placeholder:
                self.medicine_name.set("")
            self.medicine_name.values = []
        self.medicine_name.update_list()

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
        try:
            mode = load_app_mode()

            if mode == 'veterinary':
                # Veterinary: search only medicines already in inventory
                if not typed:
                    self.cursor.execute(
                        "SELECT DISTINCT name FROM medicines ORDER BY name COLLATE NOCASE LIMIT 50")
                else:
                    self.cursor.execute(
                        "SELECT DISTINCT name FROM medicines WHERE name LIKE ? COLLATE NOCASE "
                        "ORDER BY name COLLATE NOCASE LIMIT 50", (f"%{typed}%",))
                names = [r[0] for r in self.cursor.fetchall()]
            else:
                # Medical: use dedicated master_medicine.db when ready.
                if not self._master_ready:
                    self._set_medicine_dropdown_enabled(False, "Loading medicines...")
                    return
                master_names = search_master_names(typed, limit=50)
                # Safety merge: include local inventory medicines too.
                if not typed:
                    self.cursor.execute(
                        "SELECT DISTINCT name FROM medicines ORDER BY name COLLATE NOCASE LIMIT 50"
                    )
                else:
                    self.cursor.execute(
                        "SELECT DISTINCT name FROM medicines WHERE name LIKE ? COLLATE NOCASE "
                        "ORDER BY name COLLATE NOCASE LIMIT 50",
                        (f"%{typed}%",),
                    )
                local_names = [r[0] for r in self.cursor.fetchall()]
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

            self.medicine_name.values = names
            if getattr(self.medicine_name, '_suppress_focus_list', False):
                self.medicine_name.hide_list()
            else:
                self.medicine_name.update_list()
        except Exception:
            pass

    def on_medicine_selected(self, event=None):
        name = self.medicine_name.get().strip()
        if not name:
            return
        d = lookup_medicine_details(self.conn, name)
        if not d:
            return
        self.medicine_type.set(d.get('type', ''))
        for attr, key in [('manufacturer','manufacturer'), ('hsn_code','hsn_code'),
                           ('gst_value','gst_percent'), ('mrp','mrp'), ('rate','rate'),
                           ('content_drug','content_drug')]:
            getattr(self, attr).delete(0, tk.END)
            value = d.get(key, '')
            if key in ('gst_percent', 'mrp', 'rate') and value not in ('', None):
                text = "{:.2f}".format(float(value)) if float(value or 0) else "0"
            else:
                text = str(value or '')
            getattr(self, attr).insert(0, text)
        self.schedule.set(d.get('schedule', ''))
        if d.get('type'):
            self.on_type_change()

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

    def _read_pricing_data(self):
        try:
            return {
                'rate':         float(self.rate.get()),
                'mrp':          float(self.mrp.get()),
                'gst_pct':      float(self.gst_value.get() or 0),
                'discount_pct': float(self.item_discount.get() or 0),
            }
        except ValueError:
            showerror("Invalid Input", "Please enter valid rate, MRP and GST.")
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
        for attr, key in [('hsn_code','hsn_code'), ('gst_value','gst_pct'),
                           ('mrp','mrp'), ('rate','rate'),
                           ('manufacturer','manufacturer'), ('batch_no','batch'),
                           ('expiry_date','expiry'), ('content_drug','content_drug')]:
            getattr(self, attr).delete(0, tk.END)
            getattr(self, attr).insert(0, str(item.get(key, '')))
        self.schedule.set(item.get('schedule', ''))
        self.item_discount.delete(0, tk.END)
        disc_show = item.get("discount_display")
        if not disc_show:
            from core.purchase_invoice_engine import format_discount_display
            disc_show = format_discount_display(
                item.get("discount_pct", 0),
                item.get("disc_column_value", 0),
                item.get("disc_column_type", ""),
            )
        self.item_discount.insert(0, disc_show)

        if is_strip_count_type(item['type'], self._sched_unit.get(item['type'], '')):
            self._create_tablet_qty_fields()
            self.stripes.insert(0, str(item['qty']))
            self.tablets_per_stripe.insert(0, str(item.get('tablets_per_stripe', 1)))
            self.free_stripes.insert(0, str(item['free_qty']))
        else:
            self._create_other_qty_fields()
            self.quantity.insert(0, item.get('quantity_value', '1'))
            self.units.insert(0, str(item['qty']))
            self.free_items.insert(0, str(item['free_qty']))
            if item['type'].lower() == 'vaccine' and hasattr(self, 'vaccine_unit_var'):
                self.vaccine_unit_var.set(item.get('auto_unit', 'ml'))

        self.add_btn.config(text="Update Medicine")
        self.medicine_name.hide_list()
        self.medicine_type.hide_list()
        self._focus_medicine_combo()
        return 'break'

    # ── calculate — single source of truth via PurchaseCalculator ─────────

    def _update_import_bill_note(self, bill: dict) -> None:
        """Explain supplier footer figures below the purchase summary."""
        note_var = getattr(self, 'import_bill_info_var', None)
        if note_var is None:
            return
        if not self._import_bill_mode:
            note_var.set('')
            return
        inv = self._import_invoice_summary or {}
        supplier_gross = float(bill.get('supplier_gross') or inv.get('gross_amount') or 0)
        supplier_net = float(inv.get('invoice_total') or 0)
        payable = float(bill.get('total_amount') or supplier_net or 0)
        parsed_disc = float(inv.get('parsed_total_discount') or 0) or (
            float(inv.get('cash_discount') or 0) + float(inv.get('product_discount') or 0)
        )
        user_disc = float(bill.get('overall_discount') or 0)
        cash = float(inv.get('cash_discount') or 0)
        prod = float(inv.get('product_discount') or 0)
        disc_base = float(inv.get('discount_base') or 0)
        cash_pct = float(inv.get('cash_discount_pct') or 0)
        prod_pct = float(inv.get('product_discount_pct') or 0)
        edi_fmt = str(inv.get('edi_format') or '')
        item_disc = float(inv.get('item_discount_total') or 0)
        if supplier_net > 0:
            parts = [
                "Medicine total ₹{:.2f}".format(float(inv.get('line_gross') or 0)),
            ]
            if edi_fmt == 'marg' and item_disc > 0:
                parts.append(
                    "MARG item discount ₹{:.2f} (already in line rates, not subtracted again)".format(
                        item_disc,
                    )
                )
            if disc_base > 0 and cash_pct:
                parts.append(
                    "Seema CD {:.0f}% + PD {:.0f}% on base ₹{:.2f} "
                    "(F[24] cash ₹{:.2f} + F[25] product ₹{:.2f} — not CGST/SGST)".format(
                        cash_pct, prod_pct, disc_base, cash, prod,
                    )
                )
            cgst = float(bill.get('cgst') or inv.get('total_cgst') or 0)
            sgst = float(bill.get('sgst') or inv.get('total_sgst') or 0)
            if cgst or sgst:
                gst_source = {
                    'seema': 'GST from bill F[2]',
                    'seema_legacy': 'GST from bill F[4]+F[6]',
                    'marg': 'GST from bill F[9]',
                }.get(edi_fmt, 'GST from bill footer')
                parts.append(
                    "{}: CGST ₹{:.2f} + SGST ₹{:.2f} = ₹{:.2f} (matches supplier bill)".format(
                        gst_source, cgst, sgst, cgst + sgst,
                    )
                )
            if abs(user_disc - parsed_disc) > 0.005:
                parts.append(
                    "Supplier bill net ₹{:.2f}; payable now ₹{:.2f} "
                    "(discount changed from ₹{:.2f} to ₹{:.2f})".format(
                        supplier_net, payable, parsed_disc, user_disc,
                    )
                )
            else:
                parts.append("Supplier bill net ₹{:.2f}".format(supplier_net))
            bill_round = float(bill.get('rounding') or inv.get('round_off') or 0)
            if abs(bill_round) > 0.001:
                parts.append("Round off ₹{:.2f}".format(bill_round))
            if supplier_gross > 0 and abs(supplier_gross - payable) > 0.02 and edi_fmt == 'seema':
                parts.append(
                    "F[1] internal gross ₹{:.2f} (not subtotal − discount + tax)".format(
                        supplier_gross,
                    )
                )
            note_var.set("  |  ".join(parts))
        else:
            note_var.set('')

    def _import_discount_rates(self) -> tuple:
        """(discount_base, cash_pct, product_pct) for Seema-style import bills."""
        inv = self._import_invoice_summary or {}
        return (
            float(inv.get('discount_base') or 0),
            float(inv.get('cash_discount_pct') or 0),
            float(inv.get('product_discount_pct') or 0),
        )

    def _overall_discount_base(self) -> float:
        """Base for overall discount % ↔ ₹ sync."""
        if self._import_bill_mode:
            inv = self._import_invoice_summary or {}
            disc_base, _, _ = self._import_discount_rates()
            if disc_base > 0:
                return disc_base
            line_gross = float(inv.get('line_gross') or 0)
            if line_gross > 0:
                return line_gross
        return float(
            PurchaseCalculator(
                items=self.purchase_items,
                overall_discount=0,
                rounding=0,
                previous_due=0,
                previous_credit=0,
                amount_paid=0,
            ).calculate().get('gross_subtotal', 0) or 0
        )

    def _discount_sync_source(self, event=None):
        """Which overall-discount field the user is editing ('pct', 'rupees', or None)."""
        triggered = getattr(event, 'widget', None) if event else None
        if triggered == self.overall_discount_pct:
            return 'pct'
        if triggered == self.overall_discount:
            return 'rupees'
        try:
            focused = self.parent.winfo_toplevel().focus_get()
        except Exception:
            focused = None
        pct_path = str(self.overall_discount_pct)
        rs_path = str(self.overall_discount)
        if focused == pct_path:
            return 'pct'
        if focused == rs_path:
            return 'rupees'
        return None

    def _discount_pct_to_rupees(self, pct: float, discount_base: float) -> float:
        """Map displayed CD % to total cash+product discount rupees."""
        if not discount_base or pct <= 0:
            return 0.0
        if self._import_bill_mode:
            _, cash_pct, prod_pct = self._import_discount_rates()
            if cash_pct > 0 and prod_pct > 0:
                return round(discount_base * pct / 100 * (cash_pct + prod_pct) / cash_pct, 2)
        return round(discount_base * pct / 100, 2)

    def _discount_rupees_to_pct(self, rs: float, discount_base: float) -> float:
        """Map total discount rupees back to displayed CD %."""
        if not discount_base or rs <= 0:
            return 0.0
        if self._import_bill_mode:
            _, cash_pct, prod_pct = self._import_discount_rates()
            rate_sum = cash_pct + prod_pct
            if cash_pct > 0 and rate_sum > 0:
                return round(rs * cash_pct / (discount_base * rate_sum), 2)
        return round(rs * 100 / discount_base, 2)

    def sync_overall_discount_fields(self, source='rupees'):
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
            self.overall_discount.delete(0, tk.END)
            self.overall_discount.insert(0, f"{rs:.2f}")
        else:
            rs = _f(self.overall_discount)
            pct = self._discount_rupees_to_pct(rs, discount_base)
            self.overall_discount_pct.delete(0, tk.END)
            self.overall_discount_pct.insert(0, f"{pct:.2f}")

    def calculate_total(self, event=None):
        def _f(var_or_widget):
            try:
                v = var_or_widget.get() if hasattr(var_or_widget, 'get') else var_or_widget
                return float(v or 0)
            except (ValueError, tk.TclError):
                return 0.0

        discount_base = self._overall_discount_base()
        sync_source = self._discount_sync_source(event)
        if sync_source == 'pct':
            pct = _f(self.overall_discount_pct)
            rs = self._discount_pct_to_rupees(pct, discount_base)
            self.overall_discount.delete(0, tk.END)
            self.overall_discount.insert(0, f"{rs:.2f}")
        elif sync_source == 'rupees':
            rs = _f(self.overall_discount)
            pct = self._discount_rupees_to_pct(rs, discount_base)
            self.overall_discount_pct.delete(0, tk.END)
            self.overall_discount_pct.insert(0, f"{pct:.2f}")

        overall_disc = _f(self.overall_discount)

        if self._import_bill_mode:
            inv = self._import_invoice_summary or {}
            inv_disc = float(inv.get('parsed_total_discount') or 0) or (
                float(inv.get('product_discount') or 0) + float(inv.get('cash_discount') or 0)
            )
            # Only seed from import on first load — not while user is editing discount fields.
            if inv_disc and not overall_disc and sync_source is None:
                overall_disc = inv_disc
                self.overall_discount.delete(0, tk.END)
                self.overall_discount.insert(0, f"{inv_disc:.2f}")
                self.sync_overall_discount_fields('rupees')

        # Pass 1 — pre-round total before rounding adjustment
        pre_calc = PurchaseCalculator(
            items=self.purchase_items,
            overall_discount=0,
            rounding=0,
            previous_due=_f(self.previous_due_var),
            previous_credit=_f(self.previous_credit_var),
            amount_paid=_f(self.amount_paid),
        ).calculate()

        try:
            rounding_focused = (
                self.rounding_entry == self.rounding_entry.winfo_toplevel().focus_get()
            )
        except Exception:
            rounding_focused = False

        if self._import_bill_mode:
            inv = self._import_invoice_summary or {}
            bill = compute_import_bill_totals(
                inv,
                float(pre_calc.get('subtotal', 0) or 0),
                float(pre_calc.get('total_gst', 0) or 0),
                items=self.purchase_items,
                overall_discount=overall_disc,
            )
            # Apply slab-based GST breakdown back to line items unless footer totals are authoritative
            if not bill.get('use_footer_totals'):
                calc_items = bill.get('calc_items') or []
                for idx, pi in enumerate(self.purchase_items):
                    if idx < len(calc_items):
                        ci = calc_items[idx]
                        pi['taxable'] = ci.get('taxable', pi.get('taxable', 0))
                        pi['gst_amt'] = ci.get('gst_amt', 0)
                        pi['item_amount'] = ci.get('item_amount', pi.get('item_amount', 0))
                        pi['amount'] = pi['item_amount']
                        pi['import_gst_amt'] = pi['gst_amt']
                        pi['import_item_amount'] = pi['item_amount']
            inv_net = float(inv.get('invoice_total') or 0)
            if rounding_focused:
                rounding = _f(self.rounding_entry)
                bill['rounding'] = rounding
                bill['total_amount'] = round(bill['pre_round_total'] + rounding, 2)
            elif bill.get('use_footer_totals'):
                rounding = bill['rounding']
                if not rounding_focused:
                    self.rounding_entry.delete(0, tk.END)
                    self.rounding_entry.insert(0, f"{rounding:.2f}")
            elif inv_net > 0 and not rounding_focused:
                rounding = bill['rounding']
                self.rounding_entry.delete(0, tk.END)
                self.rounding_entry.insert(0, f"{rounding:.2f}")
            else:
                rounding = bill['rounding']
                pre_round = bill['pre_round_total']
                if not inv_net:
                    rounding = auto_round(pre_round)
                    bill['rounding'] = rounding
                    bill['total_amount'] = round(pre_round + rounding, 2)
                    self.rounding_entry.delete(0, tk.END)
                    self.rounding_entry.insert(0, f"{rounding:.2f}")

            result = PurchaseCalculator(
                items=self.purchase_items,
                overall_discount=0,
                rounding=bill['rounding'],
                previous_due=_f(self.previous_due_var),
                previous_credit=_f(self.previous_credit_var),
                amount_paid=_f(self.amount_paid),
            ).calculate()

            total_amount = bill['total_amount']
            prev_due = _f(self.previous_due_var)
            prev_credit = _f(self.previous_credit_var)
            amount_paid = _f(self.amount_paid)
            need_to_pay = round(total_amount + prev_due - prev_credit, 2)
            due = round(max(0.0, need_to_pay - amount_paid), 2)
            current_credit = round(max(0.0, amount_paid - need_to_pay), 2)

            self._update_import_bill_note(bill)

            result.update({
                'subtotal': bill.get('subtotal', bill.get('gross_total', 0)),
                'total_gst': bill['total_gst'],
                'cgst': bill['cgst'],
                'sgst': bill['sgst'],
                'discount_amount': bill['discount_amount'],
                'overall_discount': bill['overall_discount'],
                'pre_round_total': bill['pre_round_total'],
                'rounding': bill['rounding'],
                'total_amount': total_amount,
                'need_to_pay': need_to_pay,
                'final_amount': total_amount,
                'amount_paid': amount_paid,
                'previous_due': prev_due,
                'previous_credit': prev_credit,
                'due': due,
                'current_credit': current_credit,
                'total_due': due,
                'bill_cleared': 1 if due == 0 else 0,
                'account_cleared': 1 if due == 0 else 0,
                'due_amount': due,
                'credit_amount': current_credit,
            })
        else:
            pre_round = float(pre_calc.get('pre_round_total', 0) or 0)
            if not rounding_focused:
                rounding = auto_round(pre_round)
                self.rounding_entry.delete(0, tk.END)
                self.rounding_entry.insert(0, f"{rounding:.2f}")
            else:
                rounding = _f(self.rounding_entry)

            result = PurchaseCalculator(
                items=self.purchase_items,
                overall_discount=overall_disc,
                rounding=rounding,
                previous_due=_f(self.previous_due_var),
                previous_credit=_f(self.previous_credit_var),
                amount_paid=_f(self.amount_paid),
            ).calculate()

        self._last_calc = result

        self.subtotal_var.set(f"₹{result['subtotal']:.2f}")
        self.cgst_var.set(f"₹{result['cgst']:.2f}")
        self.sgst_var.set(f"₹{result['sgst']:.2f}")
        self.total_amount_var.set(f"₹{result['total_amount']:.2f}")
        self.need_to_pay_var.set(f"₹{result['need_to_pay']:.2f}")
        self.final_amount_var.set(f"₹{result['final_amount']:.2f}")
        self.current_credit_var.set(f"₹{result['current_credit']:.2f}")

        if result['total_due'] > 0:
            self.total_due_var.set(f"Due: ₹{result['total_due']:.2f}")
            self.due_label.config(foreground='red')
        else:
            self.total_due_var.set("₹0.00")
            self.due_label.config(foreground='green')

        self.update_items_tree()

    # ── save ──────────────────────────────────────────────────────────────

    def _save_purchase_shortcut(self, event=None):
        self.save_purchase()
        return 'break'

    def save_purchase(self):
        if not self.supplier_name.get().strip():
            showwarning("Missing", "Please enter supplier name.")
            return
        if not self.purchase_items:
            showwarning("No Items", "Please add items to the purchase.")
            return

        self.calculate_total()
        result = self._last_calc
        if not result:
            showerror("Error", "Calculation failed.")
            return

        try:
            supplier_id = get_or_create_supplier(
                self.conn,
                self.supplier_name.get().strip(),
                self.supplier_address.get(),
                self.supplier_phone.get(),
                self.supplier_gstin.get(),
                self.supplier_dl.get(),
            )
            purchase_no = svc_save_purchase(
                self.conn,
                supplier_id,
                self.purchase_date.get().strip(),
                self.bill_number.get().strip(),
                result,
                self.purchase_items,
            )
            showinfo("Success", f"Purchase {purchase_no} saved successfully!")
            self.clear_form()
        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to save purchase: {e}")

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

    def _clear_form_shortcut(self, event=None):
        self.clear_form()
        return 'break'

    def clear_form(self):
        for attr in ('supplier_address','supplier_phone','supplier_gstin',
                     'supplier_dl','bill_number'):
            getattr(self, attr).delete(0, tk.END)
        self.supplier_name.set('')
        self.purchase_date.delete(0, tk.END)
        self.purchase_date.insert(0, datetime.now().strftime('%Y-%m-%d'))

        self._clear_medicine_fields()
        self.purchase_items.clear()
        self.update_items_tree()

        for var in (self.subtotal_var, self.cgst_var, self.sgst_var,
                    self.total_amount_var, self.need_to_pay_var,
                    self.final_amount_var, self.current_credit_var, self.total_due_var):
            var.set("0.00")
        self.previous_due_var.set("0.00")
        self.previous_credit_var.set("0.00")

        for attr, default in [('overall_discount_pct','0'),
                               ('overall_discount','0'),
                               ('rounding_entry','0.00'),
                               ('amount_paid','0.00')]:
            e = getattr(self, attr)
            e.delete(0, tk.END)
            e.insert(0, default)

        self._last_calc = None
        self._import_bill_mode = False
        self._import_invoice_summary = None
        if hasattr(self, 'import_bill_info_var'):
            self.import_bill_info_var.set('')
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
