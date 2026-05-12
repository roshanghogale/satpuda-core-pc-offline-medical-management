import tkinter as tk
from tkinter import messagebox, filedialog
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    from tkinter import ttk
import json
from datetime import datetime
import sqlite3
from core.font_config import *
from ui.purchase import PurchasePage


class ImportPurchasesPage:
    """
    Settings tab: paste/load JSON → review each bill in a full PurchasePage UI
    with Prev / Next (→ Submit on last) buttons.
    All edits are kept in memory; nothing is saved until Submit.
    """

    @property
    def TYPES(self):
        from core.layout_config import load_layout, _DEFAULT_MED_TYPES
        return load_layout().get('med_types', list(_DEFAULT_MED_TYPES))

    def __init__(self, parent, conn):
        self.conn = conn
        self.parent = parent
        self._bills = []          # parsed bill dicts (editable in-memory)
        self._index = 0           # current bill index
        self._page = None         # current PurchasePage instance
        self._toplevel = None     # fullscreen Toplevel window
        self._build_ui()

    # ── top-level layout ──────────────────────────────────────────────────

    def _build_ui(self):
        from core.scroll_manager import make_scrollable
        inner = make_scrollable(self.parent)

        # ── Input area ───────────────────────────────────────────────────
        input_frame = ttk.LabelFrame(inner, text="Import JSON")
        input_frame.pack(fill=tk.X, padx=10, pady=(8, 4))

        btn_row = ttk.Frame(input_frame)
        btn_row.pack(fill=tk.X, padx=8, pady=(6, 2))

        ttk.Button(btn_row, text="📂 Load from File",
                   command=self._load_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="▶ Parse Pasted JSON",
                   command=self._parse_pasted).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="✖ Clear",
                   command=self._clear_all).pack(side=tk.LEFT, padx=4)

        self._status_var = tk.StringVar(value="Paste JSON below or load a file.")
        ttk.Label(btn_row, textvariable=self._status_var,
                  foreground='gray').pack(side=tk.LEFT, padx=12)

        # Text + scrollbars
        text_frame = ttk.Frame(input_frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 6))

        self._text = tk.Text(text_frame, height=7, wrap=tk.NONE,
                             font=(FONT_FAMILY, FONT_SIZE_TABLES))
        sb_y = ttk.Scrollbar(text_frame, orient=tk.VERTICAL,
                              command=self._text.yview)
        sb_x = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL,
                              command=self._text.xview)
        self._text.configure(xscrollcommand=sb_x.set,
                             yscrollcommand=sb_y.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Sample format section ────────────────────────────────────────
        info_frame = ttk.LabelFrame(inner, text="JSON Format Guide")
        info_frame.pack(fill=tk.X, padx=10, pady=(4, 8))
        guide = (
            "Required structure:  { \"bills\": [ { \"supplier\": {...}, \""
            "purchase_date\": \"YYYY-MM-DD\", \"bill_number\": \"...\", "
            "\"amount_paid\": 0, \"items\": [...] } ] }\n"
            "Each item needs: medicine_name, type, batch_no, expiry_date (MM/YY), "
            "qty, tablets_per_stripe, free_qty, rate, mrp, gst_percent, "
            "hsn_code, manufacturer, schedule, content_drug, item_discount\n"
            "purchase_no is auto-generated. bill_number is the supplier's invoice number."
        )
        ttk.Label(info_frame, text=guide, justify=tk.LEFT,
                  font=(FONT_FAMILY, FONT_SIZE_TABLES)).pack(
            padx=10, pady=6, anchor='w')

    # ── JSON loading ──────────────────────────────────────────────────────

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="Select JSON file",
            filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            self._text.delete('1.0', tk.END)
            self._text.insert('1.0', content)
            self._parse_pasted()
        except Exception as e:
            messagebox.showerror("File Error", str(e))

    def _parse_pasted(self):
        raw = self._text.get('1.0', tk.END).strip()
        if not raw:
            messagebox.showwarning("Empty", "Nothing to parse.")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON Error", f"Invalid JSON:\n{e}")
            return

        bills = data.get('bills') if isinstance(data, dict) else data
        if not isinstance(bills, list) or not bills:
            messagebox.showerror("Format Error",
                                 "Expected {\"bills\": [...]} or a list of bills.")
            return

        # Convert JSON items → internal purchase_items format (same as PurchasePage)
        self._bills = []
        for b in bills:
            self._bills.append(self._json_bill_to_internal(b))

        self._index = 0
        self._status_var.set(
            f"✔ {len(self._bills)} bill(s) loaded. Review each and click Submit.")
        self._open_toplevel()
        self._load_bill(self._index)

    def _json_bill_to_internal(self, b):
        """Convert one JSON bill dict to the internal format used by PurchasePage."""
        items = []
        for it in b.get('items', []):
            med_type = it.get('type', '')
            is_tablet_bolus = med_type.lower() in ['tablet', 'bolus']
            qty = float(it.get('qty', 0))
            free_qty = float(it.get('free_qty', 0))
            tps = int(it.get('tablets_per_stripe', 1))
            rate = float(it.get('rate', 0))
            gst = float(it.get('gst_percent', 0))
            item_disc = float(it.get('item_discount', 0))

            base = qty * rate
            if item_disc:
                base = base * (1 - item_disc / 100)
            # Accept pre-calculated gst_amount from JSON (web app sends it)
            if 'gst_amount' in it:
                gst_amt = float(it['gst_amount'])
            else:
                gst_amt = base * gst / 100
            amount = round(base + gst_amt, 2)

            schedule = it.get('schedule', '')
            if schedule == 'Non-Scheduled':
                schedule = ''

            expiry_raw = it.get('expiry_date', '')
            # normalise to MM/YY
            if '/' in expiry_raw:
                parts = expiry_raw.split('/')
                mm = parts[0].zfill(2)
                yy = parts[1][-2:]   # take last 2 digits
                expiry = f"{mm}/{yy}"
            else:
                expiry = expiry_raw

            item = {
                'medicine_id': None,   # resolved on save
                'name': it.get('medicine_name', ''),
                'type': med_type,
                'batch': it.get('batch_no', ''),
                'expiry': expiry,
                'qty': qty,
                'free_qty': free_qty,
                'rate': rate,
                # canonical keys for PurchaseCalculator
                'discount_pct':      item_disc,
                'gst_pct':           gst,
                'mrp':               float(it.get('mrp', 0)),
                'manufacturer':      it.get('manufacturer', ''),
                'schedule':          schedule,
                'content_drug':      it.get('content_drug', ''),
                'hsn_code':          it.get('hsn_code', ''),
                'tablets_per_stripe': tps,
                'total_tablets':     qty * tps if is_tablet_bolus else 0,
                'free_tablets':      free_qty * tps if is_tablet_bolus else 0,
                'quantity_value':    it.get('quantity_value', '1'),
                'auto_unit':         '',
            }
            items.append(item)

        return {
            'supplier': b.get('supplier', {}),
            'purchase_date': b.get('purchase_date',
                                   datetime.now().strftime('%Y-%m-%d')),
            'bill_number': b.get('bill_number', ''),
            'gst_calc_method': b.get('gst_calc_method', 'discount_before_gst'),
            'overall_discount': float(b.get('overall_discount', 0)),
            'amount_paid': float(b.get('amount_paid', 0)),
            'items': items,
        }

    # ── navigation ────────────────────────────────────────────────────────

    def _open_toplevel(self):
        """Create or reuse the fullscreen Toplevel for reviewing bills."""
        if self._toplevel and self._toplevel.winfo_exists():
            return
        root = self.parent.winfo_toplevel()
        win = tk.Toplevel(root)
        win.title("Import Purchases — Review")
        win.state('zoomed')          # maximised on Windows
        win.protocol('WM_DELETE_WINDOW', self._on_toplevel_close)
        self._toplevel = win

        # ── nav bar at top of Toplevel ────────────────────────────────────
        nav = ttk.Frame(win)
        nav.pack(fill=tk.X, padx=10, pady=(6, 2))

        self._nav_label = tk.StringVar(value="")
        ttk.Label(nav, textvariable=self._nav_label,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(
            side=tk.LEFT, padx=10)

        self._prev_btn = ttk.Button(nav, text="◀ Previous", command=self._go_prev)
        self._prev_btn.pack(side=tk.LEFT, padx=4)

        self._next_btn = ttk.Button(nav, text="Next ▶", command=self._go_next)
        self._next_btn.pack(side=tk.LEFT, padx=4)

        ttk.Button(nav, text="✖ Cancel Import",
                   command=self._on_toplevel_close).pack(side=tk.RIGHT, padx=8)

        # ── content area for PurchasePage ─────────────────────────────────
        self._page_outer = ttk.Frame(win)
        self._page_outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _on_toplevel_close(self):
        if messagebox.askyesno("Cancel Import",
                               "Close without saving? All unsaved changes will be lost.",
                               parent=self._toplevel):
            self._clear_all()

    def _update_nav_label(self):
        n = len(self._bills)
        self._nav_label.set(f"Bill {self._index + 1} of {n}")
        self._prev_btn.config(state=tk.NORMAL if self._index > 0 else tk.DISABLED)
        if self._index == n - 1:
            self._next_btn.config(text="✔ Submit All")
            try:
                self._next_btn.config(bootstyle="success")
            except Exception:
                pass
        else:
            self._next_btn.config(text="Next ▶")
            try:
                self._next_btn.config(bootstyle="primary")
            except Exception:
                pass

    def _go_prev(self):
        self._sync_current()
        self._index -= 1
        self._load_bill(self._index)

    def _go_next(self):
        self._sync_current()
        if self._index == len(self._bills) - 1:
            self._submit_all()
        else:
            self._index += 1
            self._load_bill(self._index)

    # ── sync edits back to _bills before navigating ───────────────────────

    def _sync_current(self):
        """Read current PurchasePage state back into self._bills[self._index]."""
        if self._page is None:
            return
        p = self._page
        b = self._bills[self._index]
        sup = b['supplier']
        sup['name']       = p.supplier_name.get().strip()
        sup['address']    = p.supplier_address.get().strip()
        sup['phone']      = p.supplier_phone.get().strip()
        sup['gstin']      = p.supplier_gstin.get().strip()
        sup['dl_numbers'] = p.supplier_dl.get().strip()
        b['purchase_date']    = p.purchase_date.get().strip()
        b['bill_number']      = p.bill_number.get().strip()
        b['overall_discount'] = float(p.overall_discount.get() or 0)
        b['amount_paid']      = float(p.amount_paid.get() or 0)
        b['items'] = list(p.purchase_items)

    # ── load a bill into the PurchasePage ─────────────────────────────────

    def _load_bill(self, idx):
        # Destroy previous page frame
        for child in self._page_outer.winfo_children():
            child.destroy()
        self._page = None

        bill = self._bills[idx]

        page_frame = ttk.Frame(self._page_outer)
        page_frame.pack(fill=tk.BOTH, expand=True)

        page = PurchasePage(page_frame, self.conn)
        self._page = page

        # Register this page's canvas with the global input controller
        # so scroll, Alt (focus-first), F2 (tree focus) and arrow-scroll
        # all work inside the Toplevel.
        root = self.parent.winfo_toplevel()
        ctrl = getattr(root, '_input_ctrl', None)
        if ctrl is not None:
            ctrl.set_active_canvas(getattr(page._inner_frame, '_canvas', None))
            ctrl.set_active_frame(page._inner_frame)

        # Populate supplier fields
        sup = bill['supplier']
        page.supplier_name.set(sup.get('name', ''))
        page.supplier_address.delete(0, tk.END)
        page.supplier_address.insert(0, sup.get('address', ''))
        page.supplier_phone.delete(0, tk.END)
        page.supplier_phone.insert(0, sup.get('phone', ''))
        page.supplier_gstin.delete(0, tk.END)
        page.supplier_gstin.insert(0, sup.get('gstin', ''))
        page.supplier_dl.delete(0, tk.END)
        page.supplier_dl.insert(0, sup.get('dl_numbers', ''))

        # Populate purchase header
        page.purchase_date.delete(0, tk.END)
        page.purchase_date.insert(0, bill.get('purchase_date', ''))
        page.bill_number.delete(0, tk.END)
        page.bill_number.insert(0, bill.get('bill_number', ''))

        # Load previous due for this supplier
        page.load_supplier_details()

        # Populate items
        page.purchase_items = list(bill['items'])
        page.update_items_tree()

        # Set overall discount and amount paid
        page.overall_discount.delete(0, tk.END)
        page.overall_discount.insert(0, str(bill.get('overall_discount', 0)))
        page.amount_paid.delete(0, tk.END)
        page.amount_paid.insert(0, str(bill.get('amount_paid', 0)))

        # Recalculate totals
        page.calculate_total()
        page.calculate_payment_due()

        # Disable individual save — use nav buttons only
        page.save_btn.config(text="Use Next/Submit to save", state=tk.DISABLED)
        page.clear_btn.config(command=page.clear_form)

        self._update_nav_label()

    # ── submit all ────────────────────────────────────────────────────────

    def _submit_all(self):
        self._sync_current()
        parent_win = self._toplevel if (self._toplevel and self._toplevel.winfo_exists()) else None
        if not messagebox.askyesno(
                "Submit All",
                f"Save all {len(self._bills)} purchase(s) to the database?",
                parent=parent_win):
            return

        saved = 0
        errors = []
        for i, bill in enumerate(self._bills):
            try:
                self._save_bill(bill)
                self.conn.commit()   # commit each bill individually
                saved += 1
            except Exception as e:
                self.conn.rollback()
                errors.append(f"Bill {i+1} ({bill.get('bill_number', '?')}): {e}")

        if errors:
            messagebox.showerror(
                "Partial Save",
                f"Saved {saved}/{len(self._bills)} bills.\n\nErrors:\n" +
                "\n".join(errors),
                parent=parent_win)
        else:
            messagebox.showinfo(
                "Success",
                f"All {saved} purchase(s) saved successfully!",
                parent=parent_win)
            self._clear_all()

    def _save_bill(self, bill):
        """Save one bill using PurchaseCalculator + purchase_service."""
        from core.purchase_calculator import PurchaseCalculator
        from core.purchase_service import (
            get_or_create_supplier, get_or_create_medicine,
            save_purchase as svc_save_purchase, get_supplier_due,
        )
        sup = bill['supplier']
        items = bill['items']

        # Normalise item keys to canonical format expected by PurchaseCalculator
        for item in items:
            item.setdefault('discount_pct', item.get('item_discount', 0))
            item.setdefault('gst_pct',      item.get('gst_value', 0))

        # Resolve medicine IDs
        for item in items:
            if not item.get('medicine_id'):
                item['medicine_id'] = get_or_create_medicine(
                    self.conn,
                    item['name'], item['type'],
                    item['batch'], item['expiry'],
                    item['gst_pct'], item.get('mrp', 0), item['rate'],
                    item.get('manufacturer', ''), item.get('hsn_code', ''),
                    item.get('schedule', ''), item.get('content_drug', ''),
                )

        supplier_id = get_or_create_supplier(
            self.conn,
            sup.get('name', ''), sup.get('address', ''),
            sup.get('phone', ''), sup.get('gstin', ''),
            sup.get('dl_numbers', ''),
        )

        prev_due, prev_credit = get_supplier_due(self.conn, sup.get('name', ''))

        result = PurchaseCalculator(
            items=items,
            overall_discount=float(bill.get('overall_discount', 0)),
            rounding=0.0,
            previous_due=prev_due,
            previous_credit=prev_credit,
            amount_paid=float(bill.get('amount_paid', 0)),
        ).calculate()

        svc_save_purchase(
            self.conn, supplier_id,
            bill.get('purchase_date', datetime.now().strftime('%Y-%m-%d')),
            bill.get('bill_number', ''),
            result, items,
        )

    # ── clear ─────────────────────────────────────────────────────────────

    def _clear_all(self):
        self._bills = []
        self._index = 0
        self._page = None
        if self._toplevel and self._toplevel.winfo_exists():
            self._toplevel.destroy()
        self._toplevel = None
        self._text.delete('1.0', tk.END)
        self._status_var.set("Paste JSON below or load a file.")
