import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
import threading
import os
import json
from datetime import date
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable, open_dialog
from ui.settings.settings_tabs.updates_tab import UpdatesTab


def _restart_app(root=None):
    import sys, os, subprocess
    if getattr(sys, 'frozen', False):
        args = [sys.executable]
    else:
        args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    if root is not None:
        try: root.quit()
        except Exception: pass
        try: root.destroy()
        except Exception: pass
    try:
        subprocess.Popen(args)
    except Exception:
        pass


class DatabaseTab:
    def __init__(self, notebook, conn, parent_widget):
        self.conn = conn
        self.cursor = conn.cursor()
        self._parent = parent_widget
        outer = ttk.Frame(notebook)
        notebook.add(outer, text="Database")
        frame = make_scrollable(outer)
        self._build(frame)

    def _build(self, frame):
        mgmt = ttk.LabelFrame(frame, text="Management")
        mgmt.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            mgmt,
            text="Check for app updates from GitHub Releases. Install replaces only the EXE — "
                 "your database, activation, and backups stay on this PC.",
            wraplength=560,
            justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(8, 4))
        UpdatesTab.embed(mgmt, self._parent)

        # Export
        ef = ttk.LabelFrame(frame, text="Export Data")
        ef.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(ef, text="Export data to CSV files you can open in Excel.").pack(pady=(8, 4))
        br = ttk.Frame(ef)
        br.pack(pady=8)
        ttk.Button(br, text="Export Sales",     command=self.export_sales).pack(side=tk.LEFT, padx=8)
        ttk.Button(br, text="Export Purchases", command=self.export_purchases).pack(side=tk.LEFT, padx=8)
        ttk.Button(br, text="Export Inventory", command=self.export_inventory).pack(side=tk.LEFT, padx=8)
        ttk.Button(br, text="Export All",       command=self.export_all).pack(side=tk.LEFT, padx=8)

        # Backup
        bf = ttk.LabelFrame(frame, text="Google Drive Backup")
        bf.pack(fill=tk.X, padx=10, pady=10)
        self._backup_status_var = tk.StringVar(value="")
        ttk.Label(
            bf,
            text="Use the checkbox to control automatic backup on open/close. "
                 "Backup Now always runs a manual backup.",
        ).pack(pady=(8, 4))
        try:
            from core.backup_manager import is_auto_backup_enabled
            auto_on = is_auto_backup_enabled()
        except Exception:
            auto_on = False
        self._auto_backup_var = tk.BooleanVar(value=auto_on)
        ttk.Checkbutton(
            bf,
            text="Automatic backup on open, close, and every hour",
            variable=self._auto_backup_var,
            command=self._save_auto_backup_pref,
        ).pack(anchor=tk.W, padx=10, pady=(0, 6))
        ttk.Label(bf, textvariable=self._backup_status_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(pady=(0, 4))
        ttk.Button(bf, text="Backup Now", command=self._manual_backup).pack(pady=(0, 8))

        self._backup_info_var = tk.StringVar(value="")
        ttk.Label(
            bf,
            textvariable=self._backup_info_var,
            justify=tk.LEFT,
            wraplength=560,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(padx=10, pady=(0, 10), anchor='w')
        self._refresh_backup_info()

        admin = ttk.LabelFrame(frame, text="Administrator")
        admin.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            admin,
            text="Restricted tools: Drive backup settings and expiry.dat editor.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=10, pady=(8, 4))
        ttk.Button(admin, text="Administrator Login", command=self._admin_login).pack(
            anchor=tk.W, padx=10, pady=(0, 10)
        )

        # Danger zone
        wf = ttk.LabelFrame(frame, text="Danger Zone")
        wf.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(wf, text="Delete All Tables",
                  font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold'),
                  foreground=get_alert_color('danger')).pack(pady=5)
        ttk.Label(wf, text="This will permanently delete ALL data from the database.").pack(pady=2)
        ttk.Label(wf, text="This action cannot be undone!",
                  foreground=get_alert_color('danger')).pack(pady=2)
        ttk.Button(wf, text="DELETE ALL TABLES", command=self.delete_all_tables).pack(pady=10)

    def _save_auto_backup_pref(self):
        try:
            from core.backup_manager import set_auto_backup_enabled
            set_auto_backup_enabled(bool(self._auto_backup_var.get()))
            self._refresh_backup_info()
        except Exception as e:
            showerror("Backup Settings", f"Could not save preference: {e}", parent=self._parent)

    def _manual_backup(self):
        self._backup_status_var.set("Backing up...")
        self._parent.update_idletasks()

        def _run():
            try:
                from core.backup_manager import run_backup_now
                run_backup_now(manual=True)
                try:
                    from core.backup_manager import _log_path
                    lines = [l.strip() for l in open(_log_path(), encoding='utf-8') if l.strip()]
                    last = lines[-1] if lines else ""
                    if "Backup OK" in last:
                        msg = "Backup successful!"
                    elif "no internet" in last.lower():
                        msg = "No internet connection."
                    elif "backup_config.dat missing" in last.lower():
                        msg = "Backup not configured."
                    elif "backup_creds.dat missing" in last.lower() or "invalid" in last.lower():
                        msg = "Backup credentials missing/invalid."
                    else:
                        msg = "Backup failed. Check backup_log.txt."
                except Exception:
                    msg = "Done. Check backup_log.txt."
            except Exception as e:
                msg = f"Error: {e}"
            self._parent.after(0, lambda: self._backup_status_var.set(msg))

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_backup_info(self):
        try:
            from core.backup_manager import get_backup_config_status, is_auto_backup_enabled
            auto_on = is_auto_backup_enabled()
            auto_line = (
                "Automatic backup is ON (open, hourly, close)."
                if auto_on else
                "Automatic backup is OFF — faster start/close. Use Backup Now when needed."
            )
            st = get_backup_config_status()
            if st.get('configured'):
                name = st.get('store_name', '')
                fid = st.get('folder_id', '')
                short_id = fid[:8] + '…' + fid[-4:] if len(fid) > 16 else fid
                self._backup_info_var.set(
                    f"Backup is configured for this installation.\n"
                    f"Store: {name}\n"
                    f"Drive folder: {short_id}\n\n"
                    "Backups upload to Store_<name> inside that Drive folder.\n"
                    "Use Administrator Login to change store name or folder ID.\n"
                    f"{auto_line}"
                )
            elif st.get('folder_id') and not st.get('creds_ok'):
                self._backup_info_var.set(
                    "Drive folder is set but OAuth credentials are missing or invalid.\n"
                    "Rebuild the EXE with valid config/backup_creds.dat, or delete\n"
                    "%LOCALAPPDATA%\\VeterinaryApp\\backup_creds.dat and restart the app."
                )
            else:
                self._backup_info_var.set(
                    "Backup is not configured on this PC.\n"
                    "Use Administrator Login to set the store name and Drive folder ID,\n"
                    "or embed settings before building the EXE (store_backup.build)."
                )
        except Exception:
            self._backup_info_var.set("Backup status unavailable.")

    def _admin_login(self):
        dlg = open_dialog(self._parent, "Administrator Login", width=360, height=220, resizable=False)
        body = dlg.content
        ttk.Label(body, text="Username").pack(pady=(18, 4))
        user_var = tk.StringVar()
        user_e = ttk.Entry(body, textvariable=user_var, width=32)
        user_e.pack()
        ttk.Label(body, text="Password").pack(pady=(10, 4))
        pass_var = tk.StringVar()
        pass_e = ttk.Entry(body, textvariable=pass_var, show='*', width=32)
        pass_e.pack()
        user_e.focus_set()

        def _submit():
            if user_var.get().strip() != "satpudacore" or pass_var.get() != "satpudacore":
                showerror("Administrator", "Invalid username or password.", parent=dlg)
                pass_e.delete(0, tk.END)
                pass_e.focus_set()
                return
            dlg.destroy()
            self._open_admin_panel()

        user_e.bind('<Return>', lambda e: pass_e.focus_set())
        pass_e.bind('<Return>', lambda e: _submit())
        ttk.Button(dlg.footer, text="Login", command=_submit).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_admin_panel(self):
        dlg = open_dialog(self._parent, "Administrator Tools", width=480, height=280, resizable=False)
        body = dlg.content
        ttk.Label(
            body,
            text="Choose a tool. Changes are saved on this PC and kept after app updates.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=420,
        ).pack(anchor=tk.W, padx=12, pady=(16, 12))

        btn_row = ttk.Frame(body)
        btn_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Button(
            btn_row,
            text="Drive Backup Settings",
            command=lambda: (dlg.destroy(), self._open_backup_config_editor()),
        ).pack(fill=tk.X, pady=4)
        ttk.Button(
            btn_row,
            text="Expiry File Editor",
            command=lambda: (dlg.destroy(), self._open_expiry_editor()),
        ).pack(fill=tk.X, pady=4)

        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_backup_config_editor(self):
        from core.backup_manager import get_backup_config_status, write_backup_config

        st = get_backup_config_status()
        dlg = open_dialog(
            self._parent, "Administrator - Drive Backup Settings",
            width=620, height=360, resizable=True,
        )
        top = dlg.content
        top.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        ttk.Label(
            top,
            text="Set which Google Drive folder receives this store's backups.\n"
                 "A subfolder Store_<store_name> is created automatically inside the folder ID.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 12))

        name_row = ttk.Frame(top)
        name_row.pack(fill=tk.X, pady=4)
        ttk.Label(name_row, text="Store Name:", width=16).pack(side=tk.LEFT)
        store_var = tk.StringVar(value=st.get('store_name', ''))
        ttk.Entry(name_row, textvariable=store_var, width=42).pack(side=tk.LEFT, fill=tk.X, expand=True)

        folder_row = ttk.Frame(top)
        folder_row.pack(fill=tk.X, pady=4)
        ttk.Label(folder_row, text="Drive Folder ID:", width=16).pack(side=tk.LEFT)
        folder_var = tk.StringVar(value=st.get('folder_id', ''))
        ttk.Entry(folder_row, textvariable=folder_var, width=42).pack(side=tk.LEFT, fill=tk.X, expand=True)

        creds_label = "OAuth credentials: OK" if st.get('creds_ok') else (
            "OAuth credentials: missing or invalid (rebuild EXE with backup_creds.dat)"
        )
        ttk.Label(
            top, text=creds_label,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            foreground='green' if st.get('creds_ok') else get_alert_color('warning'),
        ).pack(anchor=tk.W, pady=(12, 4))

        from core.license_manager import _appdata_dir
        path_label = os.path.join(_appdata_dir(), 'backup_config.dat')
        ttk.Label(
            top,
            text=f"Saved to:\n{path_label}",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 8))

        def _save():
            store_name = store_var.get().strip()
            folder_id = folder_var.get().strip()
            if not store_name:
                showerror("Backup Settings", "Store name is required.", parent=dlg)
                return
            if not folder_id:
                showerror("Backup Settings", "Drive folder ID is required.", parent=dlg)
                return
            try:
                write_backup_config(folder_id, store_name)
                import sys
                if not getattr(sys, 'frozen', False):
                    try:
                        from core.backup_manager import write_bundled_backup_config
                        write_bundled_backup_config(folder_id, store_name)
                    except Exception:
                        pass
                self._refresh_backup_info()
                showinfo(
                    "Backup Settings",
                    f"Backup settings saved.\n\n"
                    f"Store: {store_name}\n"
                    f"Drive subfolder: Store_{store_name.replace(' ', '_')}\n\n"
                    "These settings are kept after app updates.",
                    parent=dlg,
                )
            except Exception as e:
                showerror("Backup Settings", f"Failed to save backup settings:\n{e}", parent=dlg)

        ttk.Button(dlg.footer, text="Save", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _expiry_paths(self):
        from core.license_manager import _appdata_dir
        appdata_path = os.path.join(_appdata_dir(), 'expiry.dat')
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        config_path = os.path.join(repo_root, 'config', 'expiry.dat')
        paths = []
        for p in (appdata_path, config_path):
            if p not in paths:
                paths.append(p)
        return paths

    def _read_expiry_payload_from_path(self, path):
        try:
            if not os.path.exists(path):
                return {}
            from core.license_manager import _decrypt
            with open(path, 'rb') as f:
                return _decrypt(f.read()) or {}
        except Exception:
            return {}

    def _write_expiry_payload_to_path(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        from core.license_manager import _encrypt
        with open(path, 'wb') as f:
            f.write(_encrypt(payload))

    def _open_expiry_editor(self):
        paths = self._expiry_paths()
        payloads = [self._read_expiry_payload_from_path(p) for p in paths]
        merged = next((p for p in payloads if p), {})
        if not merged:
            merged = {'enabled': True, 'expiry_date': str(date.today())}

        dlg = open_dialog(self._parent, "Administrator - Expiry File Editor", width=620, height=420, resizable=True)
        top = dlg.content
        top.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        enabled_var = tk.BooleanVar(value=bool(merged.get('enabled', True)))
        ttk.Checkbutton(top, text="Expiry check enabled", variable=enabled_var).pack(anchor=tk.W, pady=(0, 8))

        row = ttk.Frame(top)
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="Expiry Date (YYYY-MM-DD):").pack(side=tk.LEFT)
        expiry_var = tk.StringVar(value=str(merged.get('expiry_date', '') or ''))
        ttk.Entry(row, textvariable=expiry_var, width=22).pack(side=tk.LEFT, padx=8)

        ttk.Label(top, text="Synced file locations:").pack(anchor=tk.W)
        path_text = tk.Text(top, height=4, wrap='word')
        path_text.pack(fill=tk.X, pady=(2, 8))
        path_text.insert('1.0', "\n".join(paths))
        path_text.configure(state='disabled')

        raw_text = tk.Text(top, height=8, wrap='word')
        raw_text.pack(fill=tk.BOTH, expand=True)
        raw_text.insert('1.0', json.dumps(merged, indent=2))

        def _save():
            payload = {
                'enabled': bool(enabled_var.get()),
                'expiry_date': (expiry_var.get() or '').strip(),
            }
            try:
                # validate date
                date.fromisoformat(payload['expiry_date'])
            except Exception:
                showerror("Expiry Editor", "Invalid date format. Use YYYY-MM-DD.", parent=dlg)
                return

            try:
                for p in paths:
                    self._write_expiry_payload_to_path(p, payload)
                raw_text.delete('1.0', tk.END)
                raw_text.insert('1.0', json.dumps(payload, indent=2))
                showinfo("Expiry Editor", "expiry.dat saved and synced to all locations.", parent=dlg)
            except Exception as e:
                showerror("Expiry Editor", f"Failed to save expiry file:\n{e}", parent=dlg)

        ttk.Button(dlg.footer, text="Save & Sync", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def export_sales(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM sales")
            if self.cursor.fetchone()[0] == 0:
                showinfo('Nothing to Export', 'No sales records found.', parent=self._parent)
                return
            self.cursor.execute("""
                SELECT s.bill_no, s.bill_date, c.name, COALESCE(c.phone,''),
                       COALESCE(s.doctor_name,''), m.name, COALESCE(m.type,''),
                       COALESCE(si.qty,0), COALESCE(si.rate,0), COALESCE(si.amount,0),
                       COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                       s.total_amount, COALESCE(s.discount,0),
                       COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                       COALESCE(s.amount_paid,0), COALESCE(s.due_amount,0),
                       COALESCE(s.total_due,0)
                FROM sales s
                JOIN customers c   ON s.customer_id  = c.id
                JOIN sales_items si ON si.sale_id     = s.id
                JOIN medicines m   ON si.medicine_id  = m.id
                ORDER BY s.bill_date DESC, s.bill_no, m.name
            """)
            rows = self.cursor.fetchall()
            headers = ['Bill No','Date','Customer','Phone','Doctor',
                       'Medicine','Type','Qty','Rate','Amount',
                       'Batch No','Expiry Date','Bill Total','Discount',
                       'Cash Paid','Online Paid','Amount Paid','Due Amount','Total Due']
            from core.export_manager import export_data
            export_data(self._parent, f'Sales Export ({len(rows)} rows)', headers, rows, 'sales_export')
        except Exception as e:
            showerror('Export Error', str(e), parent=self._parent)

    def export_purchases(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM purchases")
            if self.cursor.fetchone()[0] == 0:
                showinfo('Nothing to Export', 'No purchase records found.', parent=self._parent)
                return
            self.cursor.execute("""
                SELECT p.purchase_no, COALESCE(p.bill_number,''), p.purchase_date,
                       s.name, COALESCE(s.phone,''), m.name, COALESCE(pi.type,''),
                       COALESCE(pi.qty,0), COALESCE(pi.free_qty,0),
                       COALESCE(pi.rate,0), COALESCE(pi.mrp,0),
                       COALESCE(pi.batch_no,''), COALESCE(pi.expiry_date,''),
                       p.total_amount, COALESCE(p.amount_paid,0),
                       COALESCE(p.due_amount,0), COALESCE(p.total_due,0)
                FROM purchases p
                JOIN suppliers s       ON p.supplier_id   = s.id
                JOIN purchase_items pi ON pi.purchase_id  = p.id
                JOIN medicines m       ON pi.medicine_id  = m.id
                ORDER BY p.purchase_date DESC, p.purchase_no, m.name
            """)
            rows = self.cursor.fetchall()
            headers = ['Purchase No','Bill Number','Date','Supplier','Phone',
                       'Medicine','Type','Qty','Free Qty','Rate','MRP',
                       'Batch No','Expiry Date','Total Amount','Amount Paid',
                       'Due Amount','Total Due']
            from core.export_manager import export_data
            export_data(self._parent, f'Purchases Export ({len(rows)} rows)',
                        headers, rows, 'purchases_export')
        except Exception as e:
            showerror('Export Error', str(e), parent=self._parent)

    def export_inventory(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM medicines")
            if self.cursor.fetchone()[0] == 0:
                showinfo('Nothing to Export', 'No medicines found.', parent=self._parent)
                return
            self.cursor.execute("""
                SELECT m.name, m.type, COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                       COALESCE(m.stock_qty,0), COALESCE(m.unit,''),
                       COALESCE(m.mrp,0), COALESCE(m.rate,0), COALESCE(m.gst_percent,0),
                       COALESCE(m.hsn_code,''), COALESCE(m.manufacturer,''),
                       COALESCE(m.schedule,''), COALESCE(m.content_drug,''),
                       COALESCE(m.location,''), m.created_at
                FROM medicines m ORDER BY m.name, m.batch_no
            """)
            rows = self.cursor.fetchall()
            headers = ['Name','Type','Batch No','Expiry Date','Stock Qty','Unit',
                       'MRP','Rate','GST%','HSN Code','Manufacturer',
                       'Schedule','Content/Drug','Location','Created At']
            from core.export_manager import export_data
            export_data(self._parent, f'Inventory Export ({len(rows)} medicines)',
                        headers, rows, 'inventory_export')
        except Exception as e:
            showerror('Export Error', str(e), parent=self._parent)

    def export_all(self):
        from core.export_manager import export_all_combined
        sections = []
        try:
            self.cursor.execute("""
                SELECT s.bill_no, s.bill_date, c.name, COALESCE(c.phone,''),
                       COALESCE(s.doctor_name,''), m.name, COALESCE(m.type,''),
                       COALESCE(si.qty,0), COALESCE(si.rate,0), COALESCE(si.amount,0),
                       COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                       s.total_amount, COALESCE(s.discount,0),
                       COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                       COALESCE(s.amount_paid,0), COALESCE(s.due_amount,0),
                       COALESCE(s.total_due,0)
                FROM sales s
                JOIN customers c   ON s.customer_id  = c.id
                JOIN sales_items si ON si.sale_id     = s.id
                JOIN medicines m   ON si.medicine_id  = m.id
                ORDER BY s.bill_date DESC, s.bill_no, m.name
            """)
            sections.append(('Sales', ['Bill No','Date','Customer','Phone','Doctor',
                'Medicine','Type','Qty','Rate','Amount','Batch No','Expiry Date',
                'Bill Total','Discount','Cash Paid','Online Paid',
                'Amount Paid','Due Amount','Total Due'], self.cursor.fetchall()))
        except Exception as e:
            showerror('Export Error', f'Sales: {e}', parent=self._parent); return

        try:
            self.cursor.execute("""
                SELECT p.purchase_no, p.purchase_date, s.name,
                       COALESCE(p.bill_number,''), m.name, COALESCE(pi.type,''),
                       COALESCE(pi.qty,0), COALESCE(pi.free_qty,0),
                       COALESCE(pi.rate,0), COALESCE(pi.mrp,0),
                       COALESCE(pi.batch_no,''), COALESCE(pi.expiry_date,''),
                       p.total_amount, COALESCE(p.amount_paid,0),
                       COALESCE(p.due_amount,0), COALESCE(p.total_due,0)
                FROM purchases p
                JOIN suppliers s       ON p.supplier_id   = s.id
                JOIN purchase_items pi ON pi.purchase_id  = p.id
                JOIN medicines m       ON pi.medicine_id  = m.id
                ORDER BY p.purchase_date DESC, p.purchase_no, m.name
            """)
            sections.append(('Purchases', ['Purchase No','Date','Supplier','Bill Number',
                'Medicine','Type','Qty','Free Qty','Rate','MRP','Batch No','Expiry Date',
                'Total Amount','Amount Paid','Due Amount','Total Due'], self.cursor.fetchall()))
        except Exception as e:
            showerror('Export Error', f'Purchases: {e}', parent=self._parent); return

        try:
            self.cursor.execute("""
                SELECT m.name, m.type, COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                       COALESCE(m.stock_qty,0), COALESCE(m.mrp,0), COALESCE(m.rate,0),
                       COALESCE(m.manufacturer,''), COALESCE(m.schedule,''), COALESCE(m.hsn_code,'')
                FROM medicines m ORDER BY m.name, m.batch_no
            """)
            sections.append(('Inventory', ['Name','Type','Batch No','Expiry Date','Stock Qty',
                'MRP','Rate','Manufacturer','Schedule','HSN Code'], self.cursor.fetchall()))
        except Exception as e:
            showerror('Export Error', f'Inventory: {e}', parent=self._parent); return

        if not any(rows for _, _, rows in sections):
            showinfo('Nothing to Export', 'No data found.', parent=self._parent)
            return
        export_all_combined(self._parent, sections)

    def delete_all_tables(self):
        dlg = open_dialog(self._parent, "Enter Password", width=380, height=170, resizable=False)
        body = dlg.content
        ttk.Label(body, text="Password:").pack(pady=(18, 4))
        pwd_var = tk.StringVar()
        pwd_e = ttk.Entry(body, textvariable=pwd_var, show='*', width=30)
        pwd_e.pack(pady=4)
        pwd_e.focus()

        def _confirm():
            if pwd_var.get() != 'RoshanDeleteDatabase':
                showerror("Wrong Password", "Incorrect password.", parent=dlg)
                pwd_e.delete(0, tk.END)
                pwd_e.focus()
                return
            dlg.destroy()
            if not askyesno("Confirm Delete",
                            "This will permanently delete ALL data. Are you sure?",
                            parent=self._parent):
                return
            tables = ['sales_items','sales','purchase_items','purchases',
                      'medicine_shelf','medicines','customers','suppliers',
                      'doctors','shelves','pharmacy_profile','settings',
                      'racks','sections','boxes','shelf_settings']
            for t in tables:
                try: self.cursor.execute(f"DROP TABLE IF EXISTS {t}")
                except Exception: pass
            for obj_type, name in [
                ('TRIGGER','trg_purchases_after_insert'),('TRIGGER','trg_purchases_after_update'),
                ('TRIGGER','trg_sales_after_insert'),('TRIGGER','trg_sales_after_update'),
                ('VIEW','bills_cleared'),('VIEW','accounts_cleared'),('VIEW','supplier_due_status'),
            ]:
                try: self.cursor.execute(f"DROP {obj_type} IF EXISTS {name}")
                except Exception: pass
            self.conn.commit()
            root = self._parent.winfo_toplevel()
            main_app = getattr(root, '_main_app', None)
            if main_app and hasattr(main_app, 'create_tables'):
                main_app.create_tables()
                try:
                    from core.customer_service import migrate_schema
                    migrate_schema(self.conn)
                except Exception:
                    pass
            showinfo("Success", "All data deleted. The application will now restart.", parent=self._parent)
            _restart_app(root)

        pwd_e.bind('<Return>', lambda e: _confirm())
        ttk.Button(dlg.footer, text="OK", command=_confirm).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)
