"""Dialog window for reorder thresholds and default order quantity."""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT
from core.scroll_manager import DIALOG_SIZE_MEDIUM, dialog_root, dialog_section, open_dialog
from core.themed_messagebox import showinfo, showwarning


def open_reorder_defaults_dialog(parent, conn):
    w, h = DIALOG_SIZE_MEDIUM
    dlg = open_dialog(parent, "Reorder defaults", width=w, height=h, resizable=True)
    body = dialog_root(dlg.body)

    ttk.Label(
        body,
        text="Minimum stock triggers a reorder. Default qty is how many to order each time.",
        font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        foreground="#666",
        wraplength=w - 60,
    ).pack(anchor=tk.W, pady=(0, 4))

    from core.layout_config import get_med_types
    from core.alert_thresholds import load_thresholds

    low_thr, _ = load_thresholds(conn)
    cur = conn.cursor()
    low_entries = {}

    lf, grid = dialog_section(body, "Minimum stock (reorder when below)")
    lf.pack(fill=tk.X, pady=6)
    for i, mt in enumerate(get_med_types()):
        ttk.Label(grid, text=f"{mt}:").grid(
            row=i // 2, column=(i % 2) * 2, sticky=tk.W, padx=4, pady=2)
        e = ttk.Entry(grid, width=8)
        e.grid(row=i // 2, column=(i % 2) * 2 + 1, padx=4, pady=2)
        e.insert(0, str(low_thr.get(mt.lower(), low_thr.get("others", 10))))
        low_entries[mt.lower()] = e

    rf, row = dialog_section(body, "Default order quantity")
    rf.pack(fill=tk.X, pady=6)
    ttk.Label(row, text="Qty to order (each line):").pack(side=tk.LEFT)
    default_qty = ttk.Entry(row, width=10)
    default_qty.pack(side=tk.LEFT, padx=8)
    cur.execute("SELECT value FROM settings WHERE name='reorder_default_qty'")
    r = cur.fetchone()
    default_qty.insert(0, (r[0] if r and r[0] else "10"))
    ttk.Label(
        rf,
        text="Used for every medicine on Load by Supplier — not the gap to minimum.",
        font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        foreground="#666",
        wraplength=w - 60,
    ).pack(anchor=tk.W, pady=(0, 4))

    def _save():
        try:
            for mt, e in low_entries.items():
                cur.execute(
                    "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                    (f"low_stock_{mt}", e.get().strip() or "10"),
                )
            cur.execute(
                "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
                ("reorder_default_qty", default_qty.get().strip() or "10"),
            )
            conn.commit()
            showinfo("Saved", "Reorder defaults updated.", parent=dlg)
            dlg.destroy()
        except Exception as exc:
            conn.rollback()
            showwarning("Save failed", str(exc), parent=dlg)

    try:
        ttk.Button(dlg.footer, text="Save", command=_save, bootstyle="primary").pack(
            side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT)
    except Exception:
        ttk.Button(dlg.footer, text="Save", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT)

    dlg.wait_window()
