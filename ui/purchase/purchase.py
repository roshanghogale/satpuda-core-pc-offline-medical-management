import tkinter as tk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import datetime
import sqlite3

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.layout_config import load_layout, _DEFAULT_MED_TYPES, _DEFAULT_SCHEDULES
from core.purchase_calculator import PurchaseCalculator
from core.purchase_service import (
    get_or_create_supplier, get_or_create_medicine,
    get_supplier_due, save_purchase as svc_save_purchase,
    lookup_medicine_details,
)
from ui.purchase.purchase_nav  import PurchaseNavMixin
from ui.purchase.purchase_form import PurchaseFormMixin


class PurchasePage(PurchaseNavMixin, PurchaseFormMixin):

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.purchase_items = []
        self.editing_item_index = None
        self._last_calc = None

        cfg = load_layout()
        self._med_types  = cfg.get('med_types',  list(_DEFAULT_MED_TYPES))
        self._schedules  = cfg.get('schedules',  list(_DEFAULT_SCHEDULES))
        self._type_qty   = {t: cfg.get(f'typeqty_{t}', 0) for t in self._med_types}
        self._sched_unit = {t: cfg.get(f'unit_{t}', '')  for t in self._med_types}

        self._build_interface()
        self._bind_shortcuts()
        self.parent.after(150, lambda: self.supplier_name.focus())

    # ── shortcuts ─────────────────────────────────────────────────────────

    def _bind_shortcuts(self):
        root = self.parent.winfo_toplevel()
        root.bind('<F5>',        lambda e: self.save_purchase())
        root.bind('<Control-g>', lambda e: self.save_purchase())
        root.bind('<Control-G>', lambda e: self.save_purchase())
        root.bind('<F6>',        lambda e: self.amount_paid.focus())
        self.parent.after(100, self._setup_arrow_nav)

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

    def _reload_medicine_names(self):
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
            self.cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='medicines_master'")
            if not self.cursor.fetchone():
                return
            if not typed:
                self.cursor.execute(
                    "SELECT name FROM medicines_master ORDER BY name COLLATE NOCASE LIMIT 50")
                names = [r[0] for r in self.cursor.fetchall()]
            else:
                self.cursor.execute(
                    "SELECT name FROM medicines_master WHERE name LIKE ? COLLATE NOCASE "
                    "ORDER BY name COLLATE NOCASE LIMIT 50", (f"{typed}%",))
                prefix = [r[0] for r in self.cursor.fetchall()]
                contains = []
                if len(prefix) < 50:
                    ps = {n.lower() for n in prefix}
                    self.cursor.execute(
                        "SELECT name FROM medicines_master WHERE name LIKE ? COLLATE NOCASE "
                        "AND name NOT LIKE ? COLLATE NOCASE "
                        "ORDER BY name COLLATE NOCASE LIMIT ?",
                        (f"%{typed}%", f"{typed}%", 50 - len(prefix)))
                    contains = [r[0] for r in self.cursor.fetchall() if r[0].lower() not in ps]
                names = prefix + contains
            self.medicine_name.values = names
            self.medicine_name.update_list()
        except sqlite3.Error:
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
            getattr(self, attr).insert(0, str(d.get(key, '') or ''))
        self.schedule.set(d.get('schedule', ''))
        if d.get('type'):
            self.on_type_change()

    # ── add / edit / remove items ─────────────────────────────────────────

    def add_medicine(self):
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
            if qty_data['type'].lower() in ('tablet', 'bolus'):
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
            self._clear_medicine_fields()
            self.medicine_name.focus()

        except Exception as e:
            showerror("Error", f"Failed to add medicine: {e}")

    def _read_qty_data(self):
        med_type = self.medicine_type.get()
        try:
            if med_type.lower() in ('tablet', 'bolus'):
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

    def remove_selected_item(self):
        sel = self.items_tree.selection()
        if not sel:
            return
        idx = self.items_tree.index(sel[0])
        if 0 <= idx < len(self.purchase_items):
            removed = self.purchase_items.pop(idx)
            self.update_items_tree()
            self.calculate_total()
            showinfo("Removed", f"Removed {removed['name']} from list.")

    def edit_selected_item(self, event=None):
        sel = self.items_tree.selection()
        if not sel:
            return
        idx = self.items_tree.index(sel[0])
        if not (0 <= idx < len(self.purchase_items)):
            return
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
        self.item_discount.insert(0, str(item.get('discount_pct', 0)))

        if item['type'].lower() in ('tablet', 'bolus'):
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

    # ── calculate — single source of truth via PurchaseCalculator ─────────

    def calculate_total(self, event=None):
        def _f(var_or_widget):
            try:
                v = var_or_widget.get() if hasattr(var_or_widget, 'get') else var_or_widget
                return float(v or 0)
            except (ValueError, tk.TclError):
                return 0.0

        # Two-way sync: disc% ↔ disc₹ based on which field triggered the event
        need_to_pay_base = PurchaseCalculator(
            items=self.purchase_items,
            overall_discount=0,
            rounding=0,
            previous_due=_f(self.previous_due_var),
            previous_credit=_f(self.previous_credit_var),
            amount_paid=0,
        ).calculate()['need_to_pay']

        triggered = getattr(event, 'widget', None)
        if triggered is self.overall_discount_pct:
            pct = _f(self.overall_discount_pct)
            rs  = round(need_to_pay_base * pct / 100, 2)
            self.overall_discount.delete(0, tk.END)
            self.overall_discount.insert(0, f"{rs:.2f}")
        elif triggered is self.overall_discount:
            rs  = _f(self.overall_discount)
            pct = round(rs * 100 / need_to_pay_base, 4) if need_to_pay_base else 0
            self.overall_discount_pct.delete(0, tk.END)
            self.overall_discount_pct.insert(0, f"{pct:.2f}")

        result = PurchaseCalculator(
            items=self.purchase_items,
            overall_discount=_f(self.overall_discount),
            rounding=_f(self.rounding_entry),
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
        if not self.medicine_name.get().strip():
            showwarning("Missing", "Please enter medicine name."); return False
        if not self.medicine_type.get():
            showwarning("Missing", "Please select medicine type."); return False
        if not self.batch_no.get().strip():
            showwarning("Missing", "Please enter batch number."); return False
        expiry = self.expiry_date.get().strip()
        if not expiry:
            showwarning("Missing", "Please enter expiry date (MM/YY)."); return False
        try:
            if '/' not in expiry:
                raise ValueError
            month, year = expiry.split('/')
            if len(month) != 2 or len(year) != 2 or not month.isdigit() or not year.isdigit():
                raise ValueError
            if not (1 <= int(month) <= 12):
                raise ValueError
        except ValueError:
            showwarning("Invalid Format", "Expiry must be MM/YY (e.g. 12/26).")
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
