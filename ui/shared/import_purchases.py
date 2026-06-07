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
        from core.web_purchase_save import json_bill_to_internal
        return json_bill_to_internal(b)

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
        from core.dialog_escape import bind_escape_to_close
        bind_escape_to_close(win, on_close=self._on_toplevel_close)
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
        try:
            b['overall_discount_pct'] = float(p.overall_discount_pct.get() or 0)
        except (ValueError, AttributeError):
            b['overall_discount_pct'] = 0.0
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

        # Set overall discount and amount paid (₹ and % stay in sync)
        disc_pct = float(bill.get('overall_discount_pct', 0) or 0)
        disc_rs = float(bill.get('overall_discount', 0) or 0)
        page.overall_discount_pct.delete(0, tk.END)
        page.overall_discount_pct.insert(0, str(disc_pct) if disc_pct else '0')
        page.overall_discount.delete(0, tk.END)
        page.overall_discount.insert(0, str(disc_rs))
        page.amount_paid.delete(0, tk.END)
        page.amount_paid.insert(0, str(bill.get('amount_paid', 0)))

        page.update_items_tree()
        if disc_pct and not disc_rs:
            page.sync_overall_discount_fields('pct')
        else:
            page.sync_overall_discount_fields('rupees')
        page.calculate_total()

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
        from core.web_purchase_save import save_internal_bill
        save_internal_bill(self.conn, bill)

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
