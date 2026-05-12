import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
from core.font_config import *
from core.layout_config import load_layout, _DEFAULT_MED_TYPES
from core.scroll_manager import make_scrollable


class ThresholdsTab:
    def __init__(self, notebook, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        outer = ttk.Frame(notebook)
        notebook.add(outer, text="Thresholds")
        frame = make_scrollable(outer)
        self._build(frame)
        self._load()

    def _build(self, frame):
        medicine_types = load_layout().get('med_types', list(_DEFAULT_MED_TYPES))
        self.low_stock_entries = {}
        self.near_expiry_entries = {}
        low_widgets = []
        near_widgets = []

        lf = ttk.LabelFrame(frame, text="Low Stock Thresholds")
        lf.pack(fill=tk.X, padx=10, pady=5)
        for i, mt in enumerate(medicine_types):
            ttk.Label(lf, text=f"{mt}:").grid(row=i//3, column=(i%3)*2, sticky=tk.W, padx=5, pady=5)
            e = ttk.Entry(lf, width=10)
            e.grid(row=i//3, column=(i%3)*2+1, padx=5, pady=5)
            e.insert(0, "10")
            self.low_stock_entries[mt.lower()] = e
            low_widgets.append(e)

        nf = ttk.LabelFrame(frame, text="Near Expiry Thresholds (Months)")
        nf.pack(fill=tk.X, padx=10, pady=5)
        for i, mt in enumerate(medicine_types):
            ttk.Label(nf, text=f"{mt}:").grid(row=i//3, column=(i%3)*2, sticky=tk.W, padx=5, pady=5)
            e = ttk.Entry(nf, width=10)
            e.grid(row=i//3, column=(i%3)*2+1, padx=5, pady=5)
            e.insert(0, "3")
            self.near_expiry_entries[mt.lower()] = e
            near_widgets.append(e)

        try:
            save_btn = ttk.Button(frame, text="Save Thresholds",
                                  command=self.save, style='Large.TButton')
        except Exception:
            save_btn = ttk.Button(frame, text="Save Thresholds", command=self.save)
        save_btn.pack(pady=20)
        save_btn.bind('<Return>', lambda e: self.save())

        all_w = low_widgets + near_widgets
        for idx, w in enumerate(all_w):
            if idx < len(all_w) - 1:
                nxt = all_w[idx + 1]
                w.bind('<Return>', lambda e, n=nxt: n.focus())
                w.bind('<Down>',   lambda e, n=nxt: n.focus())
            else:
                w.bind('<Return>', lambda e: self.save())
                w.bind('<Down>',   lambda e: save_btn.focus())
            if idx > 0:
                prv = all_w[idx - 1]
                w.bind('<Up>', lambda e, p=prv: p.focus())

    def _load(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE, value TEXT
            )
        """)
        for mt in self.low_stock_entries:
            self.cursor.execute("SELECT value FROM settings WHERE name=?", (f'low_stock_{mt}',))
            r = self.cursor.fetchone()
            if r:
                self.low_stock_entries[mt].delete(0, tk.END)
                self.low_stock_entries[mt].insert(0, r[0])
        for mt in self.near_expiry_entries:
            self.cursor.execute("SELECT value FROM settings WHERE name=?", (f'near_expiry_{mt}',))
            r = self.cursor.fetchone()
            if r:
                self.near_expiry_entries[mt].delete(0, tk.END)
                self.near_expiry_entries[mt].insert(0, r[0])

    def save(self):
        try:
            for mt, e in self.low_stock_entries.items():
                self.cursor.execute(
                    "INSERT OR REPLACE INTO settings (name,value) VALUES (?,?)",
                    (f'low_stock_{mt}', e.get() or "10"))
            for mt, e in self.near_expiry_entries.items():
                self.cursor.execute(
                    "INSERT OR REPLACE INTO settings (name,value) VALUES (?,?)",
                    (f'near_expiry_{mt}', e.get() or "3"))
            self.conn.commit()
            messagebox.showinfo("Success", "Thresholds saved successfully!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save thresholds: {e}")


class ShortcutsTab:
    def __init__(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="⌨ Shortcuts")
        inner = make_scrollable(frame)
        self._build(inner)

    def _build(self, inner):
        sections = [
            ("Global (work on every page)", [
                ("1 – 7",          "Navigate to page: 1=Sales, 2=Purchase, 3=Inventory, 4=Sales History, 5=Purchase History, 6=Customers, 7=Settings"),
                ("Alt",            "Jump focus to the first input field on the current page"),
                ("F2",             "Jump focus into the list/table on the current page"),
                ("F5 / Ctrl+G",    "Generate Bill (Billing) / Save Purchase (Purchase)"),
                ("F6",             "Jump to Cash Paid (Billing) / Amount Paid (Purchase)"),
                ("Ctrl+P",         "Print last generated bill (Billing page only)"),
                ("Tab",            "Move focus to the next input field"),
                ("Shift+Tab",      "Move focus to the previous input field"),
                ("Enter",          "Confirm / move to next field"),
                ("Escape",         "Close dropdown / exit list / release focus to nav bar"),
                ("Mouse Wheel",    "Scroll the page up or down"),
                ("↑ / ↓ (no focus)", "Scroll the page up or down when no input is focused"),
            ]),
            ("Arrow Keys — Field Navigation", [
                ("↑ Up",   "Move to the previous input field"),
                ("↓ Down", "Move to the next input field"),
                ("← Left", "Move to the previous field (or cursor left inside text)"),
                ("→ Right","Move to the next field (or cursor right inside text)"),
            ]),
            ("Arrow Keys — Inside a List (after F2)", [
                ("↑ Up",   "Select the row above"),
                ("↓ Down", "Select the row below"),
                ("Enter",  "Open action menu or edit the selected row"),
                ("Delete", "Remove selected item (Purchase & Billing item lists)"),
                ("Escape", "Leave the list, return focus to the first input field"),
            ]),
            ("Billing Page", [
                ("F2",            "Jump into the Selected Medicines list"),
                ("F5 / Ctrl+G",  "Generate Bill"),
                ("F6",           "Jump to Cash Paid field"),
                ("Ctrl+P",       "Print last bill (opens browser)"),
                ("Enter (list)", "Open Edit Quantity/Discount dialog for selected medicine"),
                ("Delete (list)","Remove selected medicine from bill"),
                ("Escape (list)","Return focus to Medicine combo"),
                ("Double-click", "Edit quantity / discount of a medicine in the list"),
            ]),
            ("Purchase Page", [
                ("F2",            "Jump into the Purchase Items list"),
                ("F5 / Ctrl+G",  "Save Purchase"),
                ("F6",           "Jump to Amount Paid field"),
                ("Enter (list)", "Load selected item back into the form for editing"),
                ("Delete (list)","Remove selected item from purchase"),
                ("Escape (list)","Return focus to Medicine Name field"),
                ("Double-click", "Edit selected purchase item"),
            ]),
            ("Settings Page", [
                ("Ctrl+Tab",       "Switch to the next Settings tab"),
                ("Ctrl+Shift+Tab", "Switch to the previous Settings tab"),
                ("F2",             "Jump into the list on the active tab (Doctors / Suppliers)"),
                ("Enter (list)",  "Show action menu: Edit / Delete"),
                ("Escape (list)", "Return focus to the add form"),
                ("Enter (form)",  "Move to next field in add/edit forms"),
                ("Up / Down",     "Navigate between fields in forms and My Layout spinboxes"),
            ]),
        ]
        for section_title, rows in sections:
            sec = ttk.LabelFrame(inner, text=section_title)
            sec.pack(fill=tk.X, padx=15, pady=(10, 4))
            for key, desc in rows:
                row_frame = ttk.Frame(sec)
                row_frame.pack(fill=tk.X, padx=6, pady=1)
                ttk.Label(row_frame, text=key,
                          font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                          width=22, anchor='w').pack(side=tk.LEFT, padx=(4, 8))
                ttk.Label(row_frame, text=desc,
                          font=(FONT_FAMILY, FONT_SIZE_LABELS),
                          anchor='w').pack(side=tk.LEFT, fill=tk.X, expand=True)
