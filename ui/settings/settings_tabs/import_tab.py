"""Settings → Import with section navigation."""
import os
import sys
import shutil
import webbrowser
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from tkinter import messagebox
from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS, FONT_SIZE_SUPPORTING_TEXT
from core.alert_colors import get_muted_color
from ui.settings.settings_tabs.appearance_scroll import AppearanceScrollPane
from core.settings_section_nav import wire_settings_section_nav, bindings_for_sectioned_tab


_NAV_SECTIONS = [
    ('purchase_bill', 'Purchase Bill'),
    ('web',           'Web Entry'),
    ('file_import',   'Import Data'),
    ('mobile',        'Mobile Import'),
]


class ImportTab:
    TAB_NAME = 'Import'

    def __init__(self, notebook, conn, parent_widget):
        self.conn = conn
        self._parent = parent_widget
        self._panels = {}
        self._nav_buttons = {}

        outer = ttk.Frame(notebook)
        self.outer = outer
        notebook.add(outer, text=self.TAB_NAME)

        shell = ttk.Frame(outer)
        shell.pack(fill=tk.BOTH, expand=True)

        nav_outer = ttk.LabelFrame(shell, text="Sections")
        nav_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)
        nav_scroll = ttk.Frame(nav_outer)
        nav_scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        for section_id, label in _NAV_SECTIONS:
            btn = ttk.Button(
                nav_scroll, text=label, width=22,
                command=lambda k=section_id: self._show_section(k),
            )
            btn.pack(fill=tk.X, pady=2)
            self._nav_buttons[section_id] = btn

        right_col = ttk.Frame(shell)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)
        self._scroller = AppearanceScrollPane(right_col)
        self._content_host = self._scroller.frame

        self._build_all_panels()
        self._active_section = 'purchase_bill'
        self._show_section('purchase_bill')
        wire_settings_section_nav(
            self, self._nav_buttons, [s[0] for s in _NAV_SECTIONS], self._show_section)

    def get_keyboard_bindings(self):
        return bindings_for_sectioned_tab(self)

    def _panel(self, section_id):
        wrapper = ttk.Frame(self._content_host)
        self._panels[section_id] = wrapper
        return wrapper

    def show_section(self, section_id: str):
        self._show_section(section_id)

    def _show_section(self, section_id):
        if section_id not in self._panels:
            return
        self._active_section = section_id
        for frame in self._panels.values():
            frame.pack_forget()
        panel = self._panels[section_id]
        panel.pack(side=tk.TOP, fill=tk.X, anchor='n')

        def _after_show():
            self._scroller.bind_wheel_recursive()
            self._scroller.refresh()
            self._scroller.scroll_to_top()

        panel.after_idle(_after_show)
        for key, btn in self._nav_buttons.items():
            try:
                btn.configure(bootstyle='primary' if key == section_id else 'secondary')
            except Exception:
                pass

    def _build_all_panels(self):
        self._build_purchase_bill_panel()
        self._build_web_panel()
        self._build_file_import_panel()
        self._build_mobile_panel()

    def _build_purchase_bill_panel(self):
        frame = self._panel('purchase_bill')
        bill_bar = ttk.LabelFrame(frame, text="Import Purchase Bill")
        bill_bar.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(
            bill_bar,
            text="Import Purchase Bill",
            command=self._open_import_purchase_bill,
        ).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(
            bill_bar,
            text="Pick a PDF, CSV or Excel bill — the Purchase page opens with rows "
                 "filled in for you to verify and save.",
            foreground='gray',
            wraplength=520,
        ).pack(side=tk.LEFT, padx=8, pady=8)

    def _build_web_panel(self):
        frame = self._panel('web')
        web_lf = ttk.LabelFrame(frame, text="Web Purchase Entry")
        web_lf.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            web_lf, text="Open the browser purchase form linked to this store's inventory.",
            wraplength=520, justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(8, 4))
        ttk.Button(
            web_lf, text="Open Web Purchase Entry",
            command=self._open_web_purchase,
        ).pack(anchor=tk.W, padx=12, pady=(0, 12))

    def _build_file_import_panel(self):
        frame = self._panel('file_import')
        from ui.shared.import_purchases import ImportPurchasesPage
        lf = ttk.LabelFrame(frame, text="Import Purchase Data")
        lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ImportPurchasesPage(lf, self.conn)

    def _build_mobile_panel(self):
        frame = self._panel('mobile')
        from ui.shared.import_from_mobile import ImportFromMobilePage
        mobile_lf = ttk.LabelFrame(frame, text="Import from Mobile")
        mobile_lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ImportFromMobilePage(mobile_lf, self.conn)

    def _open_import_purchase_bill(self):
        root = self._parent.winfo_toplevel()
        app = getattr(root, '_main_app', None)
        purchase_page = None
        if app is not None and hasattr(app, 'open_purchase'):
            try:
                if hasattr(app, 'nav_click') and 'Purchase' in getattr(app, 'nav_buttons', {}):
                    app.nav_click(app.open_purchase, 'Purchase')
                else:
                    app.open_purchase()
                purchase_page = getattr(app, '_purchase_page', None)
            except Exception as e:
                messagebox.showerror(
                    "Import Purchase Bill",
                    f"Could not open the Purchase page:\n{e}",
                    parent=root,
                )
                return

        if purchase_page is None:
            messagebox.showerror(
                "Import Purchase Bill",
                "Purchase page is not available. Open Purchase once, then try again.",
                parent=root,
            )
            return

        from core.purchase_import_flow import import_purchase_bill_direct
        import_purchase_bill_direct(root, purchase_page)

    def _open_web_purchase(self):
        from core.web_purchase_server import (
            start_web_purchase_server, stop_web_purchase_server,
            get_api_base_url, write_runtime_catalog,
        )

        web_root = None
        if getattr(sys, 'frozen', False):
            dst_dir = os.path.join(
                os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                'VeterinaryApp', 'web_app',
            )
            os.makedirs(dst_dir, exist_ok=True)
            src = os.path.join(sys._MEIPASS, 'web_app', 'index.html')
            dst = os.path.join(dst_dir, 'index.html')
            try:
                shutil.copy2(src, dst)
                src_meds = os.path.join(sys._MEIPASS, 'web_app', 'medicines.json')
                if os.path.isfile(src_meds):
                    shutil.copy2(src_meds, os.path.join(dst_dir, 'medicines.json'))
                src_assets = os.path.join(sys._MEIPASS, 'web_app', 'assets')
                dst_assets = os.path.join(dst_dir, 'assets')
                if os.path.isdir(src_assets):
                    if os.path.exists(dst_assets):
                        shutil.rmtree(dst_assets)
                    shutil.copytree(src_assets, dst_assets)
            except Exception as e:
                messagebox.showerror('Web App Error', f'Could not extract web app:\n{e}')
                return
            web_root = dst_dir
        else:
            web_root = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                'web_app',
            )

        index = os.path.join(web_root, 'index.html')
        if not os.path.isfile(index):
            messagebox.showerror('Not Found', f'Web app not found at:\n{index}')
            return

        try:
            write_runtime_catalog(web_root, self.conn)
            stop_web_purchase_server()
            start_web_purchase_server(self.conn, web_root=web_root)
            url = get_api_base_url() + '/'
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror('Web App Error', f'Could not start web purchase server:\n{e}')
