"""Layout & Lists → Record Indicators settings panel."""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import (
    FONT_FAMILY, FONT_SIZE_LABELS, FONT_SIZE_SECTION_TITLE, FONT_SIZE_SUPPORTING_TEXT,
    FONT_SIZE_TABLES,
)
from core.record_indicators import (
    DISPLAY_STYLE_LABELS,
    DISPLAY_STYLES,
    STATUS_COLORS,
    STATUS_LABELS,
    STATUSES,
    indicator_column_widths,
    column_heading,
    load_record_indicator_prefs,
    preview_columns,
    preview_sample_rows,
    prepare_preview_row,
    register_preview_tags,
    save_record_indicator_prefs,
)

_BADGE_LABEL = {
    "due": "Due",
    "partial": "Partial",
    "cleared": "Paid",
}


class RecordIndicatorsPanel:
    def __init__(self, parent):
        self._preview_trees: dict[str, ttk.Treeview] = {}
        self._panel_bg = "#ffffff"
        self._build(parent)
        self._load()
        self._refresh_all_previews()

    def _resolve_panel_bg(self, frame) -> str:
        try:
            return str(ttk.Style().colors.bg)
        except Exception:
            pass
        try:
            return frame.winfo_toplevel().cget("bg")
        except Exception:
            return "#ffffff"

    def _canvas_swatch_row(self, parent, color: str, label: str, *, width: int = 240) -> tk.Canvas:
        """Canvas swatch — reliable color display on Windows/ttkbootstrap."""
        height = 30
        canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=self._panel_bg,
        )
        canvas.create_rectangle(2, 5, 56, 25, fill=color, outline="#333333", width=1)
        canvas.create_oval(62, 10, 74, 22, fill=color, outline=color)
        canvas.create_text(
            80, 16,
            text=label,
            fill=color,
            anchor="w",
            font=(FONT_FAMILY, FONT_SIZE_TABLES, "bold"),
        )
        return canvas

    def _build(self, frame):
        self._panel_bg = self._resolve_panel_bg(frame)
        ttk.Label(
            frame,
            text="Record Indicators",
            font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, "bold"),
        ).pack(anchor=tk.W, padx=10, pady=(10, 4))

        ttk.Label(
            frame,
            text="Each status uses a bold dark color in lists and in the previews below. "
                 "Full Row Background fills the entire row with that dark color and white text.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=720,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=10, pady=(0, 10))

        style_f = ttk.LabelFrame(frame, text="Selected Display Style")
        style_f.pack(fill=tk.X, padx=10, pady=5)
        style_row = tk.Frame(style_f, bg=self._panel_bg)
        style_row.pack(fill=tk.X, padx=10, pady=(8, 4))
        self._style_var = tk.StringVar(value="auto")
        labels = [DISPLAY_STYLE_LABELS[k] for k in DISPLAY_STYLES]
        self._style_combo = ttk.Combobox(
            style_row,
            textvariable=self._style_var,
            values=labels,
            state="readonly",
            width=36,
        )
        self._style_combo.pack(side=tk.LEFT, anchor=tk.W)
        self._style_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_selected_preview())

        chips = tk.Frame(style_f, bg=self._panel_bg)
        chips.pack(fill=tk.X, padx=10, pady=(0, 8))
        tk.Label(
            chips,
            text="Colors used:",
            bg=self._panel_bg,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(side=tk.LEFT, padx=(0, 8))
        for key in ("due", "partial", "cleared"):
            mini = tk.Canvas(chips, width=88, height=26, highlightthickness=0, bd=0, bg=self._panel_bg)
            color = STATUS_COLORS[key]
            mini.create_oval(4, 6, 18, 20, fill=color, outline=color)
            mini.create_text(
                24, 13,
                text=_BADGE_LABEL.get(key, key),
                fill=color,
                anchor="w",
                font=(FONT_FAMILY, FONT_SIZE_LABELS, "bold"),
            )
            mini.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(
            style_f,
            text="Applied to Sales, Purchase, Inventory, Customers, and Ledger lists.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=700,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=10, pady=(0, 8))

        legend = ttk.LabelFrame(frame, text="Status Colors")
        legend.pack(fill=tk.X, padx=10, pady=8)
        grid = tk.Frame(legend, bg=self._panel_bg)
        grid.pack(fill=tk.X, padx=8, pady=8)

        cols_per_row = 2
        for idx, key in enumerate(STATUSES):
            color = STATUS_COLORS[key]
            row = idx // cols_per_row
            col = idx % cols_per_row
            swatch = self._canvas_swatch_row(
                grid,
                color,
                STATUS_LABELS.get(key, key),
                width=320,
            )
            swatch.grid(row=row, column=col, sticky=tk.W, padx=8, pady=3)

        sel_prev = ttk.LabelFrame(frame, text="Live Preview — Selected Style")
        sel_prev.pack(fill=tk.X, padx=10, pady=6)
        self._selected_preview = self._make_preview_tree(sel_prev, height=5)
        self._selected_preview.pack(fill=tk.X, padx=8, pady=8)

        gallery = ttk.LabelFrame(frame, text="Compare All Display Styles")
        gallery.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(
            gallery,
            text="Each option shows Due, Partial, Paid, Low Stock, and Near Expiry samples.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=700,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=8, pady=(4, 8))

        for style_key in DISPLAY_STYLES:
            row = ttk.Frame(gallery)
            row.pack(fill=tk.X, padx=8, pady=4)
            ttk.Label(
                row,
                text=DISPLAY_STYLE_LABELS[style_key],
                font=(FONT_FAMILY, FONT_SIZE_LABELS, "bold"),
                width=28,
            ).pack(side=tk.LEFT, anchor=tk.N, padx=(0, 8))
            tree = self._make_preview_tree(row, height=5)
            tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._preview_trees[style_key] = tree

        ttk.Label(
            frame,
            text="Save Appearance & Restart (F10) applies indicator settings.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=10, pady=(4, 12))

    def _make_preview_tree(self, parent, *, height: int) -> ttk.Treeview:
        wrap = ttk.Frame(parent)
        cols = preview_columns("auto", self.collect_prefs())
        widths = {"Sample": 120, "Detail": 140, "Amount": 80}
        widths.update(indicator_column_widths())
        tree = ttk.Treeview(
            wrap,
            columns=cols,
            show="headings",
            height=height,
            style="Large.Treeview",
        )
        for col in cols:
            tree.heading(col, text=column_heading(col))
            tree.column(col, width=widths.get(col, 90), stretch=False)
        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        wrap._preview_tree = tree  # noqa: SLF001
        return wrap

    def _tree_widget(self, wrap) -> ttk.Treeview:
        return wrap._preview_tree

    def _label_to_key(self, label: str) -> str:
        for key, text in DISPLAY_STYLE_LABELS.items():
            if text == label:
                return key
        return "auto"

    def _key_to_label(self, key: str) -> str:
        return DISPLAY_STYLE_LABELS.get(key, DISPLAY_STYLE_LABELS["auto"])

    def _load(self):
        prefs = load_record_indicator_prefs(reload=True)
        self._style_var.set(self._key_to_label(prefs.get("display_style", "auto")))

    def _fill_preview_tree(self, wrap, style_key: str):
        tree = self._tree_widget(wrap)
        prefs = {"display_style": style_key}
        cols = preview_columns(style_key, prefs)

        for item in tree.get_children():
            tree.delete(item)

        try:
            tree.configure(columns=cols)
            widths = {"Sample": 120, "Detail": 140, "Amount": 80}
            widths.update(indicator_column_widths())
            for col in cols:
                tree.heading(col, text=column_heading(col))
                tree.column(col, width=widths.get(col, 90), stretch=False)
            tree.configure(displaycolumns=cols)
        except Exception:
            pass

        register_preview_tags(tree, style_key)
        for _label, status, base_vals in preview_sample_rows():
            vals, tags = prepare_preview_row(status, base_vals, style_key=style_key)
            try:
                tree.insert("", tk.END, values=vals, tags=tags)
            except Exception:
                pass

    def _refresh_selected_preview(self):
        self._fill_preview_tree(
            self._selected_preview,
            self._label_to_key(self._style_var.get()),
        )

    def _refresh_all_previews(self):
        for style_key, wrap in self._preview_trees.items():
            self._fill_preview_tree(wrap, style_key)
        self._refresh_selected_preview()

    def collect_prefs(self) -> dict:
        return {
            "display_style": self._label_to_key(self._style_var.get()),
        }

    def save(self):
        save_record_indicator_prefs(self.collect_prefs())
