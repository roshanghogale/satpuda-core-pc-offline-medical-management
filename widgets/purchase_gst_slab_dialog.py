"""GST slab breakdown dialog — matches supplier bill GST summary table."""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS, FONT_SIZE_TABLES
from core.themed_messagebox import showinfo


def show_purchase_gst_slab_dialog(
    parent,
    calc: dict,
    gst_calc_method: str = "discount_after_gst",
    import_summary: dict | None = None,
) -> None:
    """Show modal GST slab table (same layout as printed pharmacy supplier bills)."""
    slabs = list(calc.get("slab_breakdown") or [])
    if not slabs:
        showinfo(
            "GST Slab Table",
            "No GST slab data yet.\nAdd purchase items and apply discount first.",
            parent=parent,
        )
        return

    win = tk.Toplevel(parent)
    win.title("GST Slab Calculation")
    win.transient(parent)
    win.grab_set()
    try:
        from core.window_icon import apply_window_icon
        apply_window_icon(win, master=parent, is_root=False)
    except Exception:
        pass

    pad = {"padx": 12, "pady": 6}
    outer = ttk.Frame(win, padding=12)
    outer.pack(fill=tk.BOTH, expand=True)

    method_label = (
        "Discount applied before GST (per slab)"
        if gst_calc_method == "discount_before_gst"
        else "Discount applied after GST (inclusive)"
    )
    gross = float(calc.get("gross_subtotal") or calc.get("gross_total") or 0)
    disc = float(calc.get("discount_amount") or calc.get("overall_discount") or 0)

    hdr = ttk.Frame(outer)
    hdr.pack(fill=tk.X, **pad)
    ttk.Label(
        hdr,
        text=method_label,
        font=(FONT_FAMILY, FONT_SIZE_LABELS, "bold"),
    ).pack(anchor=tk.W)
    ttk.Label(
        hdr,
        text=(
            f"Medicine gross ₹{gross:.2f}  ·  Overall discount ₹{disc:.2f}  ·  "
            f"Taxable subtotal ₹{float(calc.get('subtotal') or 0):.2f}"
        ),
        font=(FONT_FAMILY, FONT_SIZE_LABELS),
    ).pack(anchor=tk.W, pady=(4, 0))

    cols = (
        "class",
        "tot_amt",
        "disc",
        "taxable",
        "cgst",
        "sgst",
        "tot_gst",
    )
    tree_frame = ttk.Frame(outer)
    tree_frame.pack(fill=tk.BOTH, expand=True, **pad)

    tree = ttk.Treeview(
        tree_frame,
        columns=cols,
        show="headings",
        height=min(max(len(slabs) + 2, 8), 16),
    )
    headings = {
        "class": "CLASS (GST %)",
        "tot_amt": "TOT. AMT.",
        "disc": "DISC.",
        "taxable": "TAXABLE",
        "cgst": "CGST",
        "sgst": "SGST",
        "tot_gst": "TOT. GST",
    }
    widths = {
        "class": 130,
        "tot_amt": 115,
        "disc": 95,
        "taxable": 115,
        "cgst": 95,
        "sgst": 95,
        "tot_gst": 100,
    }
    for col in cols:
        tree.heading(col, text=headings[col])
        tree.column(col, width=widths[col], anchor=tk.E if col != "class" else tk.W)

    sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _class_label(pct: float) -> str:
        if abs(pct - round(pct)) < 0.001:
            return f"GST {int(round(pct))}%"
        return f"GST {pct:g}%"

    tot_gross = tot_disc = tot_taxable = tot_cgst = tot_sgst = tot_gst = 0.0
    for slab in sorted(slabs, key=lambda s: float(s.get("gst_pct") or 0)):
        pct = float(slab.get("gst_pct") or 0)
        g = float(slab.get("gross") or 0)
        d = float(slab.get("discount") or 0)
        t = float(slab.get("taxable") or 0)
        c = float(slab.get("cgst") or 0)
        s = float(slab.get("sgst") or 0)
        gt = float(slab.get("total_gst") or 0)
        tot_gross += g
        tot_disc += d
        tot_taxable += t
        tot_cgst += c
        tot_sgst += s
        tot_gst += gt
        tree.insert(
            "",
            tk.END,
            values=(
                _class_label(pct),
                f"₹{g:.2f}",
                f"₹{d:.2f}",
                f"₹{t:.2f}",
                f"₹{c:.2f}",
                f"₹{s:.2f}",
                f"₹{gt:.2f}",
            ),
        )

    tree.insert(
        "",
        tk.END,
        values=(
            "TOTAL",
            f"₹{tot_gross:.2f}",
            f"₹{tot_disc:.2f}",
            f"₹{tot_taxable:.2f}",
            f"₹{tot_cgst:.2f}",
            f"₹{tot_sgst:.2f}",
            f"₹{tot_gst:.2f}",
        ),
        tags=("total",),
    )
    tree.tag_configure("total", font=(FONT_FAMILY, FONT_SIZE_TABLES, "bold"))

    sum_frame = ttk.LabelFrame(outer, text="Bill totals")
    sum_frame.pack(fill=tk.X, **pad)

    pre_round = float(calc.get("pre_round_total") or 0)
    rounding = float(calc.get("rounding") or 0)
    total_amt = float(calc.get("total_amount") or 0)
    cgst = float(calc.get("cgst") or 0)
    sgst = float(calc.get("sgst") or 0)

    lines = [
        f"Subtotal (after discount): ₹{float(calc.get('subtotal') or 0):.2f}",
        f"Total GST: CGST ₹{cgst:.2f} + SGST ₹{sgst:.2f} = ₹{cgst + sgst:.2f}",
        f"Before rounding: ₹{pre_round:.2f}",
    ]
    if abs(rounding) > 0.001:
        lines.append(f"Round off: ₹{rounding:.2f}")
    lines.append(f"Bill total: ₹{total_amt:.2f}")
    expenditure = float(calc.get("expenditure") or 0)
    if abs(expenditure) > 0.001:
        lines.append(f"Delivery charges: ₹{expenditure:.2f}")
        lines.append(f"Final amount: ₹{float(calc.get('final_amount') or total_amt + expenditure):.2f}")
    else:
        lines.append(f"Payable amount: ₹{total_amt:.2f}")

    if import_summary:
        supplier_net = float(import_summary.get("invoice_total") or 0)
        if supplier_net > 0:
            diff = round(total_amt - supplier_net, 2)
            if abs(diff) <= 0.02:
                lines.append(f"Supplier bill net: ₹{supplier_net:.2f} (matches)")
            else:
                lines.append(
                    f"Supplier bill net: ₹{supplier_net:.2f} (difference ₹{diff:.2f})"
                )

    for line in lines:
        ttk.Label(
            sum_frame,
            text=line,
            font=(FONT_FAMILY, FONT_SIZE_LABELS),
        ).pack(anchor=tk.W, padx=8, pady=2)

    ttk.Label(
        outer,
        text=(
            "Items with the same GST % are grouped; discount is split per slab; "
            "CGST/SGST are computed on each slab taxable (rounded up per component)."
        ),
        font=(FONT_FAMILY, FONT_SIZE_LABELS - 1),
        foreground="#666",
        wraplength=860,
        justify=tk.LEFT,
    ).pack(fill=tk.X, padx=12, pady=(0, 4))

    btn_row = ttk.Frame(outer)
    btn_row.pack(fill=tk.X, pady=(8, 0))
    close_btn = ttk.Button(btn_row, text="Close", command=win.destroy, width=14)
    close_btn.pack(side=tk.RIGHT)
    win.bind("<Escape>", lambda e: win.destroy())
    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(win, on_close=win.destroy)

    from core.scroll_manager import finalize_dialog_geometry
    finalize_dialog_geometry(win, width=920, height=680, resizable=True)
    close_btn.focus_set()
