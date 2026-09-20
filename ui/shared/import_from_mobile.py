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
        ttk.Button(btn_frame, text="📶 Receive from Phone (WiFi)",
                   command=self._start_wifi_receive).pack(side=tk.LEFT, padx=4)
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
            '"extra_medicine": 4, '
            '"mrp": 60.0, "rate": 45.0, '
            '"supplier": {"name": "...", "address": "...", "phone": "...", "gstin": "...", "dl_numbers": "..."}, ... } ] }\n'
            'Supplier field in each medicine is optional — saved to suppliers table if present.'
        )
        ttk.Label(guide_frame, text=guide, justify=tk.LEFT,
                  font=(FONT_FAMILY, FONT_SIZE_TABLES)).pack(
            padx=10, pady=6, anchor='w')

        self._qr_window = None

    def _start_wifi_receive(self):
        from core.mobile_import_server import (
            start_mobile_import_server,
            stop_mobile_import_server,
            is_running,
        )
        if is_running():
            self._show_qr_window()
            return

        root = self.parent.winfo_toplevel()

        def on_receive(raw, data):
            def apply():
                self._text.delete('1.0', tk.END)
                self._text.insert('1.0', raw)
                device = data.get('device_name', 'Phone')
                export_type = data.get('export_type', 'data')
                self._status_var.set(f"Received {export_type} from {device} — click Parse & Import")
                messagebox.showinfo(
                    "Received from Phone",
                    f"JSON received from {device}.\n\nClick 'Parse & Import' to import.",
                )
            root.after(0, apply)

        try:
            url, port = start_mobile_import_server(on_receive)
        except Exception as exc:
            messagebox.showerror("WiFi Receive", f"Could not start receiver:\n{exc}")
            return

        self._status_var.set(f"Waiting for phone on {url}")
        self._show_qr_window(url, port)

    def _show_qr_window(self, url=None, port=None):
        from core.mobile_import_server import (
            get_receive_url,
            is_running,
            stop_mobile_import_server,
            current_port,
        )
        if self._qr_window and self._qr_window.winfo_exists():
            self._qr_window.lift()
            return

        if url is None:
            if not is_running():
                messagebox.showinfo("WiFi Receive", "Receiver is not running. Click the button again.")
                return
            url = get_receive_url(current_port())
            port = current_port()

        top = tk.Toplevel(self.parent.winfo_toplevel())
        self._qr_window = top
        top.title("Mobile Import — this PC's address")
        top.resizable(False, False)

        # There is no Export tab and no scanner in the phone app: the address
        # is typed in Satpuda → Settings → Mobile Import. The QR only carries
        # the same address, for a general scanner app.
        ttk.Label(
            top,
            text="On your phone: Satpuda → Settings → Mobile Import → type the "
                 "address below. The QR carries the same address.",
            wraplength=360,
        ).pack(padx=16, pady=(14, 8))

        qr_frame = ttk.Frame(top)
        qr_frame.pack(padx=16, pady=4)
        qr_shown = False
        qr_error = ""
        try:
            import qrcode
            from PIL import ImageTk
            qr = qrcode.QRCode(version=1, box_size=6, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            photo = ImageTk.PhotoImage(img)
            # tk.Label (not ttk) — ttk.Label often fails to show PhotoImage
            lbl = tk.Label(qr_frame, image=photo, borderwidth=0)
            lbl.pack()
            top._qr_photo = photo
            qr_shown = True
        except Exception as exc:
            qr_error = str(exc)

        if not qr_shown:
            ttk.Label(
                qr_frame,
                text="QR image could not be loaded.\n"
                     "Run: pip install \"qrcode[pil]\"\n"
                     "You can still type/copy the URL below on the phone.",
                foreground='gray',
                justify=tk.CENTER,
            ).pack()
            if qr_error:
                ttk.Label(
                    qr_frame,
                    text=qr_error,
                    foreground='red',
                    wraplength=340,
                ).pack(pady=4)

        url_lf = ttk.LabelFrame(top, text="PC address (same network as phone)")
        url_lf.pack(fill=tk.X, padx=16, pady=8)
        url_entry = ttk.Entry(url_lf, width=48)
        url_entry.pack(padx=10, pady=8, fill=tk.X)
        url_entry.insert(0, url)

        def copy_url():
            top.clipboard_clear()
            top.clipboard_append(url)
            top.update_idletasks()
            messagebox.showinfo(
                "Copy URL",
                "PC address copied.\nType or paste it in Settings → Mobile Import on the phone.",
            )

        url_btn_row = ttk.Frame(url_lf)
        url_btn_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        ttk.Button(url_btn_row, text="Copy URL for Phone", command=copy_url).pack(side=tk.LEFT)

        ttk.Label(
            top,
            text="Same network required: home WiFi, shop router, OR turn on phone hotspot\n"
                 "and connect this PC to that hotspot. Allow Windows Firewall if asked.\n"
                 "No internet needed. Works on Windows 7–11.",
            wraplength=360,
            foreground='gray',
        ).pack(padx=16, pady=(0, 8))

        def stop():
            stop_mobile_import_server()
            self._status_var.set("WiFi receiver stopped.")
            if self._qr_window and self._qr_window.winfo_exists():
                self._qr_window.destroy()
            self._qr_window = None

        btn_row = ttk.Frame(top)
        btn_row.pack(pady=(0, 14))
        ttk.Button(btn_row, text="Stop Receiver", command=stop).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Close", command=top.destroy).pack(side=tk.LEFT, padx=6)
        top.protocol("WM_DELETE_WINDOW", stop)

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
                "Existing medicines with same name+batch will have stock updated "
                "and un-hidden so they show in Inventory.\n"
                "New medicines will be inserted."):
            return

        from core.mobile_import_apply import apply_mobile_data

        result = apply_mobile_data(self.conn, data)
        if not result.get("ok"):
            messagebox.showerror("Import Failed", result.get("error") or "Unknown error")
            self._status_var.set("Medicine import failed.")
            return

        inserted = int(result.get("inserted") or 0)
        updated = int(result.get("updated") or 0)
        skipped = int(result.get("skipped") or 0)
        errors = list(result.get("errors") or [])
        msg = (
            f"Medicines imported successfully!\n\n"
            f"Inserted: {inserted}\nUpdated: {updated}"
            + (f"\nSkipped: {skipped}" if skipped else "")
            + "\n\nOpen Inventory and refresh to see them."
        )
        if result.get("sync_warning"):
            msg += f"\n\nOnline sync warning:\n{result['sync_warning']}"
        if errors:
            msg += f"\n\nNotes ({len(errors)}):\n" + "\n".join(errors[:10])
            messagebox.showwarning("Import Complete with Notes", msg)
        else:
            messagebox.showinfo("Import Complete", msg)
            self._clear()

        self._status_var.set(
            f"Done: {inserted} inserted, {updated} updated, "
            f"{skipped} skipped, {len(errors)} notes."
        )

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
            from core.layout_config import is_strip_count_type
            is_tb    = is_strip_count_type(med_type)
            qty      = float(it.get('qty', 0))
            free_qty = float(it.get('free_qty', 0))
            exp_raw  = it.get('expiry_date', '')
            # normalise expiry to MM/YY
            if '/' in exp_raw:
                parts = exp_raw.split('/')
                expiry = f"{parts[0].zfill(2)}/{parts[1][-2:]}"
            else:
                expiry = exp_raw

            item = {
                # Android: name / gst_pct · older web: medicine_name / gst_percent
                'name':          str(it.get('name') or it.get('medicine_name') or '').strip(),
                'type':          med_type,
                'batch':         str(it.get('batch_no') or it.get('batch') or '').strip() or '-',
                'expiry':        expiry,
                'qty':           qty,
                'free_qty':      free_qty,
                'rate':          float(it.get('rate', 0)),
                'mrp':           float(it.get('mrp', 0)),
                'discount_pct':  float(it.get('item_discount', it.get('discount_pct', 0)) or 0),
                'gst_pct':       float(it.get('gst_pct', it.get('gst_percent', 0)) or 0),
                'hsn_code':      it.get('hsn_code', ''),
                'manufacturer':  it.get('manufacturer', ''),
                'schedule':      it.get('schedule', ''),
                'content_drug':  it.get('content_drug', ''),
                'auto_unit':     '',
            }
            if not item['name']:
                raise ValueError("Purchase item missing medicine name")
            item['medicine_id'] = get_or_create_medicine(
                self.conn,
                item['name'], item['type'], item['batch'], item['expiry'],
                item['gst_pct'], item['mrp'], item['rate'],
                item['manufacturer'], item['hsn_code'],
                item['schedule'], item['content_drug'],
            )
            # The line's own pack when the export carries one, else the catalogue's -- never an
            # invented 1 (core.mobile_import_apply.import_line_pack).
            from core.mobile_import_apply import import_line_pack
            item.update(import_line_pack(self.conn, it, item['medicine_id'], med_type))
            tps = int(item.get('tablets_per_stripe') or 1)
            item['total_tablets'] = qty * tps if is_tb else 0
            item['free_tablets'] = free_qty * tps if is_tb else 0
            items.append(item)

        prev_due, prev_credit = get_supplier_due(self.conn, supplier_name)

        cash = float(purchase.get('cash_paid', purchase.get('amount_paid', 0)) or 0)
        online = float(purchase.get('online_paid', 0) or 0)
        if cash <= 0 and online <= 0 and float(purchase.get('amount_paid', 0) or 0) > 0:
            cash = float(purchase.get('amount_paid', 0) or 0)

        result = PurchaseCalculator(
            items=items,
            overall_discount=float(purchase.get('overall_discount', 0)),
            rounding=0.0,
            previous_due=prev_due,
            previous_credit=prev_credit,
            cash_paid=cash,
            online_paid=online,
            gst_calc_method=(purchase.get('gst_calc_method') or 'discount_after_gst').strip(),
        ).calculate()

        svc_save_purchase(
            self.conn, supplier_id,
            purchase.get('purchase_date', datetime.now().strftime('%Y-%m-%d')),
            purchase.get('bill_number', ''),
            result, items,
        )

    # ── Expiry date parser ─────────────────────────────────────────────────

    def _parse_expiry_to_db(self, raw: str) -> str:
        """Convert any expiry a phone can send to YYYY-MM-01. See core/expiry_text.py."""
        from core.expiry_text import expiry_to_db

        return expiry_to_db(raw)
