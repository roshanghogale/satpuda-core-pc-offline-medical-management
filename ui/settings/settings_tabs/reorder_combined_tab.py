"""Settings -> Reorder."""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT
from core.themed_messagebox import showinfo
from core.keyboard_registry import PageBindings


class ReorderCombinedTab:
    TAB_NAME = "Reorder"

    def __init__(self, notebook, conn, parent_widget=None, host=None):
        self.conn = conn
        self._parent = parent_widget
        self._page = None
        self._pending_prefill = None

        outer = host if host is not None else ttk.Frame(notebook)
        self.outer = outer
        if host is None and notebook is not None:
            notebook.add(outer, text=self.TAB_NAME)

        btn_bar = ttk.Frame(outer)
        btn_bar.pack(fill=tk.X, padx=10, pady=(10, 0))
        try:
            self._btn_pending = ttk.Button(
                btn_bar, text="Pending Orders", command=lambda: self._show("pending"),
                bootstyle="primary", width=20)
            self._btn_new = ttk.Button(
                btn_bar, text="New Order", command=lambda: self._show("new"),
                bootstyle="outline-secondary", width=16)
            self._btn_bulk = ttk.Button(
                btn_bar, text="Load by Supplier", command=self._bulk_load,
                bootstyle="success-outline", width=22)
            self._btn_defaults = ttk.Button(
                btn_bar, text="Reorder Defaults…", command=self._open_defaults,
                bootstyle="info-outline", width=18)
        except Exception:
            self._btn_pending = ttk.Button(
                btn_bar, text="Pending Orders", command=lambda: self._show("pending"), width=20)
            self._btn_new = ttk.Button(
                btn_bar, text="New Order", command=lambda: self._show("new"), width=16)
            self._btn_bulk = ttk.Button(
                btn_bar, text="Load by Supplier", command=self._bulk_load, width=22)
            self._btn_defaults = ttk.Button(
                btn_bar, text="Reorder Defaults…", command=self._open_defaults, width=18)

        self._btn_pending.pack(side=tk.LEFT, padx=6)
        self._btn_new.pack(side=tk.LEFT, padx=6)
        self._btn_bulk.pack(side=tk.LEFT, padx=6)
        self._btn_defaults.pack(side=tk.RIGHT, padx=6)
        ttk.Label(
            btn_bar,
            text="Scroll supplier tabs horizontally • Ctrl+Shift+N/W • Ctrl+[ / ]",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            foreground="#666",
        ).pack(side=tk.LEFT, padx=12)

        self._host = ttk.Frame(outer)
        self._host.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._show("pending")

    def _open_defaults(self):
        from ui.settings.settings_tabs.reorder_defaults_dialog import open_reorder_defaults_dialog
        open_reorder_defaults_dialog(self.outer, self.conn)

    def get_keyboard_bindings(self):
        if self._page is not None:
            return self._page.get_keyboard_bindings()
        return PageBindings(page_id="reorder_settings", on_f5=self.refresh_pending)

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from ui.reorder_page import ReorderPage
        self._page = ReorderPage(self._host, self.conn, prefill=self._pending_prefill or {})
        self._pending_prefill = None
        return self._page

    def _style_buttons(self, active: str):
        for name, btn in (("pending", self._btn_pending), ("new", self._btn_new)):
            try:
                btn.configure(bootstyle="primary" if name == active else "outline-secondary")
            except Exception:
                pass

    def _show(self, which: str):
        page = self._ensure_page()
        self._style_buttons(which)
        if which == "pending":
            page.show_pending()
        else:
            page.show_orders()

    def refresh_pending(self, _event=None):
        if self._page:
            self._page.load_pending()

    def open_with_prefill(self, prefill: dict):
        self._pending_prefill = prefill or {}
        if self._page is not None:
            for child in self._host.winfo_children():
                child.destroy()
            self._page = None
        self._show("new")
        page = self._ensure_page()
        page.parent.after(100, page._apply_prefill)

    def _bulk_load(self):
        from core.reorder_service import collect_reorder_candidates
        if not collect_reorder_candidates(self.conn):
            showinfo("Reorder", "No low or out-of-stock medicines to reorder.", parent=self.outer)
            return
        self.open_with_prefill({"bulk": True})
