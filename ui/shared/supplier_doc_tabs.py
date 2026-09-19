"""Tab bar for multi-supplier documents (reorder / return) — like Sales tabs."""
from __future__ import annotations

from typing import Callable, List, Optional

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


class SupplierDocTabBar:
    """Horizontal supplier tabs with add button."""

    def __init__(
        self,
        parent,
        *,
        on_switch: Callable[[int], None],
        on_add: Optional[Callable[[], None]] = None,
        add_label: str = "+ Supplier",
    ):
        self._on_switch = on_switch
        self._on_add = on_add
        self._add_label = add_label
        self._labels: List[str] = []
        self._active = 0
        self.frame = ttk.Frame(parent)
        self._inner = ttk.Frame(self.frame)

    def pack(self, **kwargs):
        self.frame.pack(fill=tk.X, **kwargs)
        self._inner.pack(fill=tk.X, padx=2, pady=4)

    def set_tabs(self, labels: List[str], active: int = 0):
        self._labels = list(labels)
        self._active = max(0, min(active, len(self._labels) - 1)) if self._labels else 0
        self.refresh()

    def refresh(self):
        for w in self._inner.winfo_children():
            w.destroy()
        for i, label in enumerate(self._labels):
            text = label if len(label) <= 22 else label[:20] + "..."
            style = "primary" if i == self._active else "secondary"
            try:
                btn = ttk.Button(
                    self._inner, text=text, bootstyle=style,
                    command=lambda idx=i: self._on_switch(idx),
                )
            except Exception:
                btn = ttk.Button(self._inner, text=text, command=lambda idx=i: self._on_switch(idx))
            btn.pack(side=tk.LEFT, padx=2)
        if self._on_add:
            try:
                ttk.Button(
                    self._inner, text=self._add_label, bootstyle="success-outline",
                    command=self._on_add,
                ).pack(side=tk.LEFT, padx=(8, 2))
            except Exception:
                ttk.Button(self._inner, text=self._add_label, command=self._on_add).pack(
                    side=tk.LEFT, padx=(8, 2))
