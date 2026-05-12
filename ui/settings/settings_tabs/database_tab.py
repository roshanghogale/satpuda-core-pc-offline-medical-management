import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
import threading
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable, open_dialog


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
        ttk.Label(bf, text="Backup runs automatically. Use this to trigger a manual backup.").pack(pady=(8, 4))
        ttk.Label(bf, textvariable=self._backup_status_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(pady=(0, 4))
        ttk.Button(bf, text="Backup Now", command=self._manual_backup).pack(pady=(0, 10))

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

    def _manual_backup(self):
        self._backup_status_var.set("Backing up...")
        self._parent.update_idletasks()

        def _run():
            try:
                from core.backup_manager import run_backup_now
                run_backup_now()
                try:
                    from core.backup_manager import _log_path
                    lines = [l.strip() for l in open(_log_path(), encoding='utf-8') if l.strip()]
                    last = lines[-1] if lines else ""
                    if "Backup OK" in last:
                        msg = "Backup successful!"
                    elif "no internet" in last.lower():
                        msg = "No internet connection."
                    elif "missing" in last.lower():
                        msg = "Backup not configured."
                    else:
                        msg = "Backup failed. Check backup_log.txt."
                except Exception:
                    msg = "Done. Check backup_log.txt."
            except Exception as e:
                msg = f"Error: {e}"
            self._parent.after(0, lambda: self._backup_status_var.set(msg))

        threading.Thread(target=_run, daemon=True).start()

    def export_sales(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM sales")
            if self.cursor.fetchone()[0] == 0:
                messagebox.showinfo('Nothing to Export', 'No sales records found.')
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
            messagebox.showerror('Export Error', str(e))

    def export_purchases(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM purchases")
            if self.cursor.fetchone()[0] == 0:
                messagebox.showinfo('Nothing to Export', 'No purchase records found.')
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
            messagebox.showerror('Export Error', str(e))

    def export_inventory(self):
        try:
            self.cursor.execute("SELECT COUNT(*) FROM medicines")
            if self.cursor.fetchone()[0] == 0:
                messagebox.showinfo('Nothing to Export', 'No medicines found.')
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
            messagebox.showerror('Export Error', str(e))

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
            messagebox.showerror('Export Error', f'Sales: {e}'); return

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
            messagebox.showerror('Export Error', f'Purchases: {e}'); return

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
            messagebox.showerror('Export Error', f'Inventory: {e}'); return

        if not any(rows for _, _, rows in sections):
            messagebox.showinfo('Nothing to Export', 'No data found.')
            return
        export_all_combined(self._parent, sections)

    def delete_all_tables(self):
        dlg = open_dialog(self._parent, "Enter Password", width=380, height=170, resizable=False)
        ttk.Label(dlg, text="Password:").pack(pady=(18, 4))
        pwd_var = tk.StringVar()
        pwd_e = ttk.Entry(dlg, textvariable=pwd_var, show='*', width=30)
        pwd_e.pack(pady=4)
        pwd_e.focus()

        def _confirm():
            if pwd_var.get() != 'RoshanDeleteDatabase':
                messagebox.showerror("Wrong Password", "Incorrect password.", parent=dlg)
                pwd_e.delete(0, tk.END)
                pwd_e.focus()
                return
            dlg.destroy()
            if not messagebox.askyesno("Confirm Delete",
                                       "This will permanently delete ALL data. Are you sure?"):
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
            messagebox.showinfo("Success", "All data deleted. The application will now restart.")
            _restart_app(root)

        pwd_e.bind('<Return>', lambda e: _confirm())
        bf = ttk.Frame(dlg)
        bf.pack(pady=8)
        ttk.Button(bf, text="OK",     command=_confirm).pack(side=tk.LEFT, padx=6)
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)
