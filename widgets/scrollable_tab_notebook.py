"""Horizontal scrollable tab bar."""
from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

TAB_SHORTCUT_HINT = (
    "Ctrl+Shift+N new | Ctrl+Shift+W close | "
    "Ctrl+[ / Ctrl+PgUp prev | Ctrl+] / Ctrl+PgDn next"
)


class ScrollableTabNotebook:
    def __init__(
        self,
        parent,
        *,
        on_add=None,
        on_remove=None,
        on_select=None,
        add_label="+ Tab",
        hint="",
    ):
        self.outer = ttk.Frame(parent)
        self._on_add, self._on_remove = on_add, on_remove
        self._on_select = on_select
        self._tabs: List[dict] = []
        self._active = 0

        bar_row = ttk.Frame(self.outer)
        bar_row.pack(fill=tk.X)
        scroll_wrap = ttk.Frame(bar_row)
        scroll_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._canvas = tk.Canvas(scroll_wrap, height=36, highlightthickness=0, bd=0)
        self._hscroll = ttk.Scrollbar(scroll_wrap, orient=tk.HORIZONTAL, command=self._canvas.xview)
        self._canvas.configure(xscrollcommand=self._hscroll.set)
        self._canvas.pack(side=tk.TOP, fill=tk.X, expand=True)

        self._btn_frame = ttk.Frame(self._canvas)
        self._canvas.create_window((0, 0), window=self._btn_frame, anchor=tk.NW)
        self._btn_frame.bind("<Configure>", self._on_btn_frame_configure)
        for w in (self._canvas, self._btn_frame, scroll_wrap):
            w.bind("<Shift-MouseWheel>", self._on_wheel, add="+")
            w.bind("<MouseWheel>", self._on_wheel, add="+")

        if on_add:
            try:
                ttk.Button(
                    bar_row, text=add_label, command=on_add, bootstyle="success-outline",
                ).pack(side=tk.RIGHT, padx=(6, 0))
            except Exception:
                ttk.Button(bar_row, text=add_label, command=on_add).pack(side=tk.RIGHT, padx=(6, 0))

        if hint is None:
            hint = TAB_SHORTCUT_HINT
        if hint:
            ttk.Label(bar_row, text=hint, font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=6)

        self._content = ttk.Frame(self.outer)
        self._content.pack(fill=tk.BOTH, expand=True)

    def pack(self, **kwargs):
        self.outer.pack(**kwargs)

    def _on_btn_frame_configure(self, _e=None):
        self._canvas.update_idletasks()
        req, view = self._btn_frame.winfo_reqwidth(), self._canvas.winfo_width()
        if req > view + 2:
            self._hscroll.pack(side=tk.BOTTOM, fill=tk.X)
        else:
            self._hscroll.pack_forget()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_wheel(self, event):
        if self._btn_frame.winfo_reqwidth() <= self._canvas.winfo_width():
            return "break"
        self._canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def add(self, frame, label, tab_obj=None):
        frame.pack_forget()
        idx = len(self._tabs)
        self._tabs.append({"label": label, "frame": frame, "obj": tab_obj})
        self._refresh_buttons()
        self.select(idx)
        return idx

    def select(self, idx):
        if idx < 0 or idx >= len(self._tabs):
            return
        for t in self._tabs:
            t["frame"].pack_forget()
        self._active = idx
        self._tabs[idx]["frame"].pack(fill=tk.BOTH, expand=True)
        self._refresh_buttons()
        self._scroll_tab_into_view(idx)
        if self._on_select:
            self._on_select(idx)

    def _scroll_tab_into_view(self, idx):
        ch = self._btn_frame.winfo_children()
        if idx < 0 or idx >= len(ch):
            return
        btn = ch[idx]
        self._canvas.update_idletasks()
        bx, bw, cw = btn.winfo_x(), btn.winfo_width(), self._canvas.winfo_width()
        left, right = self._canvas.canvasx(0), self._canvas.canvasx(0) + cw
        total = max(self._btn_frame.winfo_reqwidth(), 1)
        if bx < left:
            self._canvas.xview_moveto(max(0, bx / total))
        elif bx + bw > right:
            self._canvas.xview_moveto((bx + bw - cw) / total)

    def remove(self, idx):
        if len(self._tabs) <= 1 or idx < 0 or idx >= len(self._tabs):
            return False
        self._tabs.pop(idx)["frame"].destroy()
        if self._on_remove:
            self._on_remove(idx)
        if self._active >= len(self._tabs):
            self._active = len(self._tabs) - 1
        elif self._active > idx:
            self._active -= 1
        self._refresh_buttons()
        if self._tabs:
            self.select(self._active)
        return True

    def remove_active(self):
        return self.remove(self._active)

    def update_label(self, idx, label):
        if 0 <= idx < len(self._tabs):
            self._tabs[idx]["label"] = label[:24]
            self._refresh_buttons()

    def clear(self):
        for t in self._tabs:
            t["frame"].destroy()
        self._tabs.clear()
        self._active = 0
        self._refresh_buttons()

    def _refresh_buttons(self):
        for w in self._btn_frame.winfo_children():
            w.destroy()
        for i, tab in enumerate(self._tabs):
            style = "primary" if i == self._active else "secondary"
            try:
                btn = ttk.Button(
                    self._btn_frame, text=tab["label"], bootstyle=style,
                    command=lambda idx=i: self.select(idx),
                )
            except Exception:
                btn = ttk.Button(
                    self._btn_frame, text=tab["label"],
                    command=lambda idx=i: self.select(idx),
                )
            btn.pack(side=tk.LEFT, padx=2, pady=2)
        self._btn_frame.after(10, self._on_btn_frame_configure)

    def prev_tab(self):
        if self._tabs:
            self.select((self._active - 1) % len(self._tabs))

    def next_tab(self):
        if self._tabs:
            self.select((self._active + 1) % len(self._tabs))

    def bind_shortcuts(self, widget):
        """Bind Ctrl+Shift+N/W and Ctrl+[ / ] / PgUp / PgDn on *widget* (usually the page root)."""
        if widget is None:
            return

        def _add(_event=None):
            if self._on_add:
                self._on_add()
            return "break"

        def _close(_event=None):
            if len(self._tabs) <= 1:
                return "break"
            self.remove_active()
            return "break"

        def _prev(_event=None):
            self.prev_tab()
            return "break"

        def _next(_event=None):
            self.next_tab()
            return "break"

        binds = (
            ("<Control-Shift-N>", _add),
            ("<Control-Shift-n>", _add),
            ("<Control-Shift-W>", _close),
            ("<Control-Shift-w>", _close),
            ("<Control-bracketleft>", _prev),
            ("<Control-Prior>", _prev),
            ("<Control-bracketright>", _next),
            ("<Control-Next>", _next),
        )
        for seq, handler in binds:
            try:
                widget.bind(seq, handler, add="+")
            except Exception:
                pass