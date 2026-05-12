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


class ImportFromMobilePage:
    """
    Import from Mobile page.
    Handles two JSON formats exported from the Android app:
      1. export_type = "purchases"  -> purchases with supplier info
      2. export_type = "medicines"  -> medicines only, no supplier/purchase
    """

    def __init__(self, parent, conn):
        self.conn = conn
        self.parent = parent
        self._build_ui()

    def _build_ui(self):
        from core.scroll_manager import make_scrollable
        inner = make_scrollable(self.parent)

        # Header
        ttk.Label(inner,
                  text="Import from Mobile (Android App)",
                  font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold')
                  ).pack(padx=10, pady=(10, 2), anchor='w')
        ttk.Label(inner,
                  text="Paste or load the JSON exported from the Android app. "
                       "Supports both Purchases and Medicines-only formats.",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS),
                  foreground='gray'
                  ).pack(padx=10, pady=(0, 8), anchor='w')

        # Buttons row
        btn_frame = ttk.Frame(inner)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 4))

        ttk.Button(btn_frame, text="📂 Load JSON File",
                   command=self._load_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="▶ Parse & Import",
                   command=self._parse_and_import).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="✖ Clear",
                   command=self._clear).pack(side=tk.LEFT, padx=4)

        self._status_var = tk.StringVar(value="Paste JSON below or load a file.")
        ttk.Label(btn_frame, textvariable=self._status_var,
                  foreground='gray').pack(side=tk.LEFT, padx=12)

        # Text area
        text_frame = ttk.Frame(inner)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self._text = tk.Text(text_frame, height=14, wrap=tk.NONE,
                             font=(FONT_FAMILY, FONT_SIZE_TABLES))
        sb_y = ttk.Scrollbar(text_frame, orient=tk.VERTICAL,
                              command=self._text.yview)
        sb_x = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL,
                              command=self._text.xview)
        self._text.configure(xscrollcommand=sb_x.set, yscrollcommand=sb_y.set)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Format guide
        guide_frame = ttk.LabelFrame(inner, text="Supported JSON Formats")
        guide_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        guide = (
            'Format 1 — Purchases:  { "export_type": "purchases", "suppliers": [...], '
            '"purchases": [ { "supplier_name": "...", "bill_number": "...", '
            '"purchase_date": "YYYY-MM-DD", "items": [...] } ] }\n'
            'Format 2 — Medicines:  { "export_type": "medicines", '
            '"medicines": [ { "name": "...", "type": "...", "batch_no": "...", '
            '"expiry_date": "MM/YY or YYYY-MM-DD", "stock_qty": 10, "unit": "10", '
            '"mrp": 60.0, "rate": 45.0, '
            '"supplier": {"name": "...", "address": "...", "phone": "...", "gstin": "...", "dl_numbers": "..."}, ... } ] }\n'
            'Supplier field in each medicine is optional — saved to suppliers table if present.'
        )
        ttk.Label(guide_frame, text=guide, justify=tk.LEFT,
                  font=(FONT_FAMILY, FONT_SIZE_TABLES)).pack(
            padx=10, pady=6, anchor='w')

    # ── File loading ───────────────────────────────────────────────────────

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
            self._status_var.set(f"Loaded: {path}")
        except Exception as e:
            messagebox.showerror("File Error", str(e))

    def _clear(self):
        self._text.delete('1.0', tk.END)
        self._status_var.set("Paste JSON below or load a file.")

    # ── Parse and route ────────────────────────────────────────────────────

    def _parse_and_import(self):
        raw = self._text.get('1.0', tk.END).strip()
        if not raw:
            messagebox.showwarning("Empty", "Nothing to parse.")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON Error", f"Invalid JSON:\n{e}")
            return

        export_type = data.get('export_type', '').lower()

        if export_type == 'medicines':
            self._import_medicines(data)
        elif export_type == 'purchases':
            self._import_purchases(data)
        else:
            # Try to auto-detect
            if 'medicines' in data:
                self._import_medicines(data)
            elif 'purchases' in data or 'bills' in data:
                self._import_purchases(data)
            else:
                messagebox.showerror(
                    "Unknown Format",
                    "Could not detect format.\n"
                    "JSON must contain 'export_type': 'medicines' or 'purchases'.")

    # ── Medicines-only import ──────────────────────────────────────────────

    def _import_medicines(self, data):
        medicines = data.get('medicines', [])
        if not medicines:
            messagebox.showwarning("Empty", "No medicines found in JSON.")
            return

        device = data.get('device_name', 'Unknown')
        export_date = data.get('export_date', '')

        if not messagebox.askyesno(
                "Confirm Import",
                f"Import {len(medicines)} medicine(s) from device '{device}' "
                f"(exported {export_date})?\n\n"
                "Existing medicines with same name+batch will have stock updated.\n"
                "New medicines will be inserted."):
            return

        cursor = self.conn.cursor()
        inserted = 0
        updated = 0
        errors = []

        for med in medicines:
            try:
                name     = med.get('name', '').strip()
                batch    = med.get('batch_no', '').strip()
                exp_raw  = med.get('expiry_date', '')
                stock    = int(med.get('stock_qty', 0))
                unit     = str(med.get('unit', ''))
                mrp      = float(med.get('mrp', 0))
                rate     = float(med.get('rate', 0))
                gst      = float(med.get('gst_percent', 0))
                hsn      = med.get('hsn_code', '')
                mfg      = med.get('manufacturer', '')
                schedule = med.get('schedule', '')
                med_type = med.get('type', '')
                content  = med.get('content_drug', '')

                if not name or not batch:
                    errors.append(f"Skipped: missing name or batch_no")
                    continue

                # Save supplier if present
                sup = med.get('supplier')
                if isinstance(sup, dict) and sup.get('name', '').strip():
                    from core.purchase_service import get_or_create_supplier
                    get_or_create_supplier(
                        self.conn,
                        sup.get('name', '').strip(),
                        sup.get('address', ''),
                        sup.get('phone', ''),
                        sup.get('gstin', ''),
                        sup.get('dl_numbers', ''),
                    )

                # expiry_date: handles MM/YY or YYYY-MM-DD
                db_expiry = self._parse_expiry_to_db(exp_raw)

                # For Tablet/Bolus: stock_qty from Android = number of strips
                # unit = tablets per strip (e.g. "10")
                # Desktop stores total tablets = strips * tablets_per_strip
                is_tablet_bolus = med_type.lower() in ['tablet', 'bolus']
                if is_tablet_bolus:
                    try:
                        tps = int(float(unit))  # unit field = tablets per strip
                        stock = stock * tps
                    except (ValueError, ZeroDivisionError):
                        pass  # unit not numeric, use stock_qty as-is

                # Check if medicine exists (name + batch + expiry)
                cursor.execute(
                    "SELECT id, stock_qty FROM medicines "
                    "WHERE name=? AND batch_no=? AND expiry_date=?",
                    (name, batch, db_expiry))
                existing = cursor.fetchone()

                if existing:
                    # Replace stock with imported value (not additive — prevents double-import)
                    cursor.execute(
                        "UPDATE medicines SET stock_qty=?, unit=?, "
                        "mrp=?, rate=?, gst_percent=?, hsn_code=?, "
                        "manufacturer=?, schedule=?, type=?, content_drug=? "
                        "WHERE id=?",
                        (stock, unit, mrp, rate, gst, hsn,
                         mfg, schedule, med_type, content, existing[0]))
                    updated += 1
                else:
                    cursor.execute(
                        "INSERT INTO medicines "
                        "(name, type, batch_no, expiry_date, stock_qty, unit, "
                        "mrp, rate, gst_percent, hsn_code, manufacturer, "
                        "schedule, content_drug, location) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'')",
                        (name, med_type, batch, db_expiry, stock, unit,
                         mrp, rate, gst, hsn, mfg, schedule, content))
                    inserted += 1

            except Exception as e:
                errors.append(f"{med.get('name', '?')}: {e}")

        self.conn.commit()

        msg = f"Medicines imported successfully!\n\nInserted: {inserted}\nUpdated: {updated}"
        if errors:
            msg += f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors[:10])
            messagebox.showwarning("Import Complete with Errors", msg)
        else:
            messagebox.showinfo("Import Complete", msg)
            self._clear()

        self._status_var.set(
            f"Done: {inserted} inserted, {updated} updated, {len(errors)} errors.")

    # ── Purchases import ───────────────────────────────────────────────────

    def _import_purchases(self, data):
        # Support both Android format (purchases array) and web format (bills array)
        purchases = data.get('purchases') or data.get('bills', [])
        suppliers_list = data.get('suppliers', [])

        if not purchases:
            messagebox.showwarning("Empty", "No purchases found in JSON.")
            return

        device = data.get('device_name', 'Unknown')
        export_date = data.get('export_date', '')

        # Build supplier lookup from suppliers array
        supplier_lookup = {}
        for s in suppliers_list:
            supplier_lookup[s.get('name', '').strip()] = s

        if not messagebox.askyesno(
                "Confirm Import",
                f"Import {len(purchases)} purchase(s) from device '{device}' "
                f"(exported {export_date})?\n\n"
                "Suppliers will be created if they don't exist.\n"
                "Stock will be updated for all items."):
            return

        cursor = self.conn.cursor()
        saved = 0
        errors = []

        for i, purchase in enumerate(purchases):
            try:
                self._save_purchase(cursor, purchase, supplier_lookup)
                saved += 1
            except Exception as e:
                bill_no = purchase.get('bill_number', f'#{i+1}')
                errors.append(f"Bill {bill_no}: {e}")

        self.conn.commit()

        msg = f"Purchases imported!\n\nSaved: {saved}/{len(purchases)}"
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors[:10])
            messagebox.showwarning("Import Complete with Errors", msg)
        else:
            messagebox.showinfo("Import Complete", msg)
            self._clear()

        self._status_var.set(
            f"Done: {saved} saved, {len(errors)} errors.")

    def _save_purchase(self, cursor, purchase, supplier_lookup):
        from core.purchase_calculator import PurchaseCalculator
        from core.purchase_service import (
            get_or_create_supplier, get_or_create_medicine,
            save_purchase as svc_save_purchase, get_supplier_due,
        )

        supplier_name = (
            purchase.get('supplier_name') or
            purchase.get('supplier', {}).get('name', '')
        ).strip()
        if not supplier_name:
            raise ValueError("Missing supplier name")

        sup_data = supplier_lookup.get(supplier_name, {})
        if not sup_data and isinstance(purchase.get('supplier'), dict):
            sup_data = purchase['supplier']

        supplier_id = get_or_create_supplier(
            self.conn,
            supplier_name,
            sup_data.get('address', ''), sup_data.get('phone', ''),
            sup_data.get('gstin', ''), sup_data.get('dl_numbers', ''),
        )

        raw_items = purchase.get('items', [])
        if not raw_items:
            raise ValueError("No items in purchase")

        items = []
        for it in raw_items:
            med_type = it.get('type', '')
            is_tb    = med_type.lower() in ('tablet', 'bolus')
            qty      = float(it.get('qty', 0))
            free_qty = float(it.get('free_qty', 0))
            tps      = int(it.get('tablets_per_stripe', 1))
            exp_raw  = it.get('expiry_date', '')
            # normalise expiry to MM/YY
            if '/' in exp_raw:
                parts = exp_raw.split('/')
                expiry = f"{parts[0].zfill(2)}/{parts[1][-2:]}"
            else:
                expiry = exp_raw

            item = {
                'name':          it.get('medicine_name', '').strip(),
                'type':          med_type,
                'batch':         it.get('batch_no', '').strip(),
                'expiry':        expiry,
                'qty':           qty,
                'free_qty':      free_qty,
                'rate':          float(it.get('rate', 0)),
                'mrp':           float(it.get('mrp', 0)),
                'discount_pct':  float(it.get('item_discount', 0)),
                'gst_pct':       float(it.get('gst_percent', 0)),
                'hsn_code':      it.get('hsn_code', ''),
                'manufacturer':  it.get('manufacturer', ''),
                'schedule':      it.get('schedule', ''),
                'content_drug':  it.get('content_drug', ''),
                'tablets_per_stripe': tps,
                'total_tablets': qty * tps if is_tb else 0,
                'free_tablets':  free_qty * tps if is_tb else 0,
                'quantity_value': str(it.get('quantity_value', '1')),
                'auto_unit':     '',
            }
            item['medicine_id'] = get_or_create_medicine(
                self.conn,
                item['name'], item['type'], item['batch'], item['expiry'],
                item['gst_pct'], item['mrp'], item['rate'],
                item['manufacturer'], item['hsn_code'],
                item['schedule'], item['content_drug'],
            )
            items.append(item)

        prev_due, prev_credit = get_supplier_due(self.conn, supplier_name)

        result = PurchaseCalculator(
            items=items,
            overall_discount=float(purchase.get('overall_discount', 0)),
            rounding=0.0,
            previous_due=prev_due,
            previous_credit=prev_credit,
            amount_paid=float(purchase.get('amount_paid', 0)),
        ).calculate()

        svc_save_purchase(
            self.conn, supplier_id,
            purchase.get('purchase_date', datetime.now().strftime('%Y-%m-%d')),
            purchase.get('bill_number', ''),
            result, items,
        )

    # ── Expiry date parser ─────────────────────────────────────────────────

    def _parse_expiry_to_db(self, raw: str) -> str:
        """Convert any expiry format to YYYY-MM-01 for DB storage.
        Handles: MM/YY, MM/YYYY, YYYY-MM-DD, YYYY-MM-01
        """
        raw = str(raw).strip()
        if not raw:
            return ''
        # Already YYYY-MM-DD or YYYY-MM-01
        if len(raw) == 10 and raw[4] == '-':
            parts = raw.split('-')
            return f"{parts[0]}-{parts[1].zfill(2)}-01"
        # MM/YY or MM/YYYY
        if '/' in raw:
            parts = raw.split('/')
            mm = parts[0].zfill(2)
            yy = parts[1]
            year = '20' + yy if len(yy) == 2 else yy
            return f"{year}-{mm}-01"
        return raw
