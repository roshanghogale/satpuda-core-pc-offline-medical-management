import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from core.font_config import *
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from ui.settings.settings_tabs.appearance_scroll import AppearanceScrollPane


class ThresholdsTab:
    """Low-stock / near-expiry thresholds per medicine type (embedded panel)."""

    def __init__(self, parent, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self._build(parent)
        self._load()

    def _build(self, frame):
        from core.layout_config import load_layout, _DEFAULT_MED_TYPES
        medicine_types = load_layout().get('med_types', list(_DEFAULT_MED_TYPES))
        self.low_stock_entries = {}
        self.near_expiry_entries = {}
        low_widgets = []
        near_widgets = []

        lf = ttk.LabelFrame(frame, text="Low Stock Thresholds")
        lf.pack(fill=tk.X, padx=10, pady=5)
        for i, mt in enumerate(medicine_types):
            ttk.Label(lf, text=f"{mt}:").grid(row=i // 3, column=(i % 3) * 2, sticky=tk.W, padx=5, pady=5)
            e = ttk.Entry(lf, width=10)
            e.grid(row=i // 3, column=(i % 3) * 2 + 1, padx=5, pady=5)
            e.insert(0, "10")
            self.low_stock_entries[mt.lower()] = e
            low_widgets.append(e)

        nf = ttk.LabelFrame(frame, text="Near Expiry Thresholds (Months)")
        nf.pack(fill=tk.X, padx=10, pady=5)
        for i, mt in enumerate(medicine_types):
            ttk.Label(nf, text=f"{mt}:").grid(row=i // 3, column=(i % 3) * 2, sticky=tk.W, padx=5, pady=5)
            e = ttk.Entry(nf, width=10)
            e.grid(row=i // 3, column=(i % 3) * 2 + 1, padx=5, pady=5)
            e.insert(0, "3")
            self.near_expiry_entries[mt.lower()] = e
            near_widgets.append(e)

        try:
            save_btn = ttk.Button(frame, text="Save Thresholds", command=self.save, style='Large.TButton')
        except Exception:
            save_btn = ttk.Button(frame, text="Save Thresholds", command=self.save)
        save_btn.pack(pady=20)
        save_btn.bind('<Return>', lambda e: self.save())

        all_w = low_widgets + near_widgets
        for idx, w in enumerate(all_w):
            if idx < len(all_w) - 1:
                nxt = all_w[idx + 1]
                w.bind('<Return>', lambda e, n=nxt: n.focus())
                w.bind('<Down>', lambda e, n=nxt: n.focus())
            else:
                w.bind('<Return>', lambda e: self.save())
                w.bind('<Down>', lambda e: save_btn.focus())
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
            showinfo("Success", "Thresholds saved successfully!")
        except Exception as e:
            showerror("Error", f"Failed to save thresholds: {e}")


class AppModeTab:
    """Application mode panel (embedded in Appearance → App Mode section)."""

    def __init__(self, parent, conn=None):
        from core.app_setup import load_app_mode, save_app_mode
        self._save_app_mode = save_app_mode

        ttk.Label(parent, text="Application Mode",
                  font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold')).pack(pady=(10, 6))

        self._mode_var = tk.StringVar(value=load_app_mode())

        desc = {
            'medical': ("💊 Medical",
                        "Medicine names are loaded from the bundled master database.\n"
                        "New medicines are also saved to the master list for suggestions."),
            'veterinary': ("🐾 Veterinary",
                           "No master medicine database. Purchase shows only inventory medicines.\n"
                           "You can type any new name — it is added when you save the purchase."),
        }

        for mode, (label, detail) in desc.items():
            rf = ttk.LabelFrame(parent, text=label)
            rf.pack(fill=tk.X, padx=20, pady=8)
            ttk.Radiobutton(rf, text=label, variable=self._mode_var, value=mode,
                            command=self._on_change).pack(anchor=tk.W, padx=10, pady=(6, 2))
            ttk.Label(rf, text=detail,
                      font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                      justify=tk.LEFT).pack(anchor=tk.W, padx=28, pady=(0, 8))

        self._status = ttk.Label(parent, text="",
                                 font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'))
        self._status.pack(pady=10)

    def _on_change(self):
        mode = self._mode_var.get()
        self._save_app_mode(mode)
        label = "Medical 💊" if mode == 'medical' else "Veterinary 🐾"
        self._status.config(text=f"✔ Mode set to {label}. Restart the app to apply.",
                            foreground='green')


class ShortcutsTab:
    def __init__(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="⌨ Shortcuts")
        self._scroller = AppearanceScrollPane(frame)
        self._build(self._scroller.frame)
        frame.after_idle(lambda: (self._scroller.bind_wheel_recursive(), self._scroller.refresh()))

    def _build(self, inner):
        sections = [
            ("Global Navigation", [
                ("` or 0", "Home (press Escape first if typing in a field)"),
                ("1", "Sales (Billing)"),
                ("2", "Purchase"),
                ("3", "Inventory"),
                ("4", "Sales History"),
                ("5", "Purchase History"),
                ("6", "Returns — S = Sales Return, P = Purchase Return"),
                ("7", "Settings — Ctrl+1…9 for tabs (see below)"),
            ]),
            ("Global Actions", [
                ("Alt", "Focus first input on the current page"),
                ("F2", "Focus primary list / table"),
                ("F3", "Focus secondary list (Returns sub-pages)"),
                ("Shift+F2", "Import Purchase bill (Purchase page)"),
                ("F5 / Ctrl+G", "Save Sales / Save Purchase / Save Return / Save Payment"),
                ("F6", "Overall discount % (Sales & Purchase) · Clear (Returns)"),
                ("End", "Cash Paid (Sales) · Amount Paid (Purchase)"),
                ("Ctrl+P", "Print last saved sale (Sales page)"),
                ("Ctrl+E", "Export (page-specific)"),
                ("Ctrl+F", "Focus filter fields"),
                ("Ctrl+Enter", "Apply filters"),
                ("Ctrl+Shift+C", "Clear / refresh filters"),
                ("Escape", "Close dropdown · exit list · release focus (number keys work again)"),
                ("↑ / ↓ (no input)", "Scroll page"),
            ]),
            ("Arrow / Enter Navigation", [
                ("← → ↑ ↓", "Move between fields on the current page"),
                ("Enter", "Next field · apply filter on dropdowns · add medicine · save"),
                ("Tab / Shift+Tab", "Standard focus order"),
            ]),
            ("Home", [
                ("B", "New Bill (Sales)"),
                ("P", "New Purchase"),
                ("I", "Inventory"),
                ("E", "Export menu dialog"),
                ("Ctrl+E", "Export menu"),
                ("F2", "Focus quick actions · list dialogs: F2 → table, Enter → close, Esc → close"),
            ]),
            ("Sales / Billing", [
                ("F5 / Ctrl+G", "Save Sales — saves, opens browser print (A5), clears form"),
                ("F6", "Overall discount %"),
                ("End", "Cash Paid"),
                ("Ctrl+P", "Reprint last sale"),
                ("F2", "Medicine items list"),
                ("Enter on list", "Edit qty/disc · Delete removes row"),
                ("Enter on medicine", "Add medicine → back to medicine search"),
            ]),
            ("Purchase", [
                ("Shift+F2", "Import Purchase"),
                ("F2", "Purchase items list"),
                ("F5 / Ctrl+G", "Save Purchase"),
                ("F6", "Overall discount %"),
                ("End", "Amount Paid"),
            ]),
            ("Inventory / History", [
                ("Ctrl+F", "Focus search / customer / supplier filter"),
                ("Ctrl+Enter", "Apply filters"),
                ("Enter on filter dropdown", "Apply filter after selecting value"),
                ("Ctrl+Shift+C", "Clear filters (Inventory: refresh + clear)"),
                ("Ctrl+E", "Export"),
                ("F2", "Data table"),
                ("Enter on table", "Keyboard action menu (↑↓ pick, Enter run, Esc close)"),
                ("Delete", "Delete row (Inventory / Sales History)"),
            ]),
            ("Returns", [
                ("S / P", "Sales Return / Purchase Return (when Returns hub focused)"),
                ("F2", "Original bill items"),
                ("F3", "Return items list"),
                ("F5", "Save return"),
                ("F6", "Clear form"),
            ]),
            ("Settings", [
                ("Ctrl+1", "Pharmacy Profile"),
                ("Ctrl+2", "Contacts"),
                ("Ctrl+3", "Shelf Management"),
                ("Ctrl+4", "Appearance"),
                ("Ctrl+5", "Import"),
                ("Ctrl+6", "Management"),
                ("Ctrl+7", "Payment — S = Supplier, C = Customer payment"),
                ("Ctrl+8", "Ledger — S = Supplier, C = Customer ledger"),
                ("Ctrl+9", "This Shortcuts page"),
                ("Ctrl+Tab", "Next settings tab"),
                ("Ctrl+Shift+Tab", "Previous settings tab"),
                ("F4", "Focus section sidebar (Pharmacy, Contacts, Import, Management, Appearance)"),
                ("↑ ↓ Enter", "Move and select section buttons when sidebar focused"),
                ("Alt+1…4", "Jump to section (on sectioned tabs)"),
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
                          anchor='w', wraplength=720).pack(side=tk.LEFT, fill=tk.X, expand=True)
