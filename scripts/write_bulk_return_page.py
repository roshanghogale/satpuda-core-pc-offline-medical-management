# Generator script - run once
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / "ui" / "returns" / "bulk_purchase_return_page.py"

DEST.write_text('''"""Bulk purchase return — one tab per purchase bill."""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT
from core.scroll_manager import make_scrollable, pack_centered_buttons
from core.themed_messagebox import askyesno, showerror, showinfo, showwarning
from core.calc_engine import calc_return_refund
from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
from core.stock_disposal_service import submit_writeoff
from widgets.scrollable_tab_notebook import ScrollableTabNotebook


class _PurchaseReturnTab:
    def __init__(self, page, parent, group):
        self.page = page
        self.group = group
        self.conn = page.conn
        self.cursor = page.cursor
        self.parent = parent
        self.return_items = []
        self._purchase_id = int(group["purchase_id"])
        self._supplier_id = None
        self._orig_items_data = []

        hdr = ttk.LabelFrame(parent, text="Purchase")
        hdr.pack(fill=tk.X, padx=6, pady=6)
        bill = group.get("bill_number") or ("#%s" % self._purchase_id)
        sup = group.get("supplier_name") or ""
        ttk.Label(hdr, text="%s  |  %s" % (bill, sup),
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT)).pack(anchor=tk.W, padx=8, pady=6)

        cols = ("Medicine", "Batch", "Qty", "Rate", "Amount")
        lf = ttk.LabelFrame(parent, text="Items to return")
        lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.ret_tree = ttk.Treeview(lf, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (200, 80, 120, 70, 80)):
            self.ret_tree.heading(c, text=c)
            self.ret_tree.column(c, width=w)
        self.ret_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="Reason:").pack(side=tk.LEFT)
        self.reason_var = tk.StringVar(value=group.get("reason") or "Near expiry / Expired")
        ttk.Entry(row, textvariable=self.reason_var, width=40).pack(side=tk.LEFT, padx=6)
        self.refund_var = tk.StringVar(value="0.00")
        ttk.Label(row, text="Credit:").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(row, textvariable=self.refund_var,
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT, "bold")).pack(side=tk.LEFT, padx=4)

        pack_centered_buttons(parent, [
            {"text": "Save this purchase return", "command": self._save, "bootstyle": "danger"},
        ], pady=8)

        self._load_purchase()
        for line in group.get("lines", []):
            self._add_line(int(line["medicine_id"]), float(line.get("quantity") or 0), line.get("batch_no", ""))
        self._refresh_tree()

    def _get_tps(self, unit_str, med_type):
        if not is_strip_count_type(med_type):
            return 1
        return parse_tablets_per_stripe(unit_str)

    def _already_returned(self, purchase_id, medicine_id):
        self.cursor.execute(
            "SELECT COALESCE(SUM(pri.qty), 0) FROM purchase_return_items pri "
            "JOIN purchase_returns pr ON pri.return_id = pr.id "
            "WHERE pr.purchase_id = ? AND pri.medicine_id = ?", (purchase_id, medicine_id))
        row = self.cursor.fetchone()
        return float(row[0]) if row else 0.0

    def _load_purchase(self):
        self.cursor.execute(
            "SELECT p.id, s.id FROM purchases p JOIN suppliers s ON p.supplier_id = s.id WHERE p.id = ?",
            (self._purchase_id,))
        row = self.cursor.fetchone()
        if not row:
            return
        self._supplier_id = int(row[1])
        self.cursor.execute(
            "SELECT m.name, pi.batch_no, pi.qty, pi.rate, COALESCE(pi.type,''), "
            "pi.medicine_id, COALESCE(m.unit,'1') FROM purchase_items pi "
            "JOIN medicines m ON pi.medicine_id = m.id WHERE pi.purchase_id = ?",
            (self._purchase_id,))
        for med_name, batch, orig_qty, rate, med_type, med_id, unit in self.cursor.fetchall():
            orig_qty = float(orig_qty)
            tps = self._get_tps(unit, med_type)
            is_tablet = is_strip_count_type(med_type)
            remaining = max(0.0, orig_qty - self._already_returned(self._purchase_id, med_id))
            self._orig_items_data.append({
                "name": med_name, "batch": batch or "", "rate": float(rate),
                "med_id": med_id, "tps": tps, "remaining": remaining, "is_tablet": is_tablet,
            })

    def _add_line(self, med_id, qty, batch=""):
        batch = (batch or "").strip()
        for d in self._orig_items_data:
            if int(d["med_id"]) != int(med_id):
                continue
            if batch and (d.get("batch") or "") != batch:
                continue
            if any(item["med_id"] == med_id for item in self.return_items):
                return
            remaining = float(d.get("remaining") or 0)
            if remaining <= 0:
                return
            use_qty = min(qty, remaining) if qty > 0 else remaining
            sd = use_qty * d["tps"] if d["is_tablet"] else use_qty
            self.return_items.append({
                "med_id": med_id, "name": d["name"], "batch": d.get("batch", ""),
                "qty": use_qty, "tps": d["tps"], "is_tablet": d["is_tablet"],
                "stock_deduction": sd, "rate": d["rate"], "amount": round(use_qty * d["rate"], 2),
            })
            return

    def _refresh_tree(self):
        for iid in self.ret_tree.get_children():
            self.ret_tree.delete(iid)
        for item in self.return_items:
            ql = ("%s strips" % int(item["qty"])) if not item["is_tablet"] else (
                "%s strips x %s" % (int(item["qty"]), int(item["tps"])))
            self.ret_tree.insert("", tk.END, values=(
                item["name"], item["batch"], ql, "%.2f" % item["rate"], "%.2f" % item["amount"]))
        self.refund_var.set("%.2f" % calc_return_refund(self.return_items, 0)["refund_amount"])

    def _save(self):
        if not self.return_items:
            showwarning("No items", "Nothing to return.", parent=self.parent)
            return
        if not askyesno("Confirm", "Save this purchase return?", parent=self.parent):
            return
        reason = self.reason_var.get().strip()
        refund = calc_return_refund(self.return_items, 0)["refund_amount"]
        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM purchase_returns")
        return_no = "PR%s" % self.cursor.fetchone()[0]
        try:
            self.cursor.execute(
                "INSERT INTO purchase_returns (return_no, purchase_id, supplier_id, return_date, "
                "refund_amount, discount, reason) VALUES (?, ?, ?, ?, ?, 0, ?)",
                (return_no, self._purchase_id, self._supplier_id, datetime.now().date(), refund, reason))
            rid = self.cursor.lastrowid
            for item in self.return_items:
                self.cursor.execute(
                    "INSERT INTO purchase_return_items (return_id, medicine_id, qty, rate, amount) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (rid, item["med_id"], item["qty"], item["rate"], item["amount"]))
                self.cursor.execute(
                    "UPDATE medicines SET stock_qty = MAX(0, stock_qty - ?) WHERE id = ?",
                    (item["stock_deduction"], item["med_id"]))
            self.conn.commit()
            from core.purchase_service import recalculate_supplier_due
            if self._supplier_id:
                recalculate_supplier_due(self.conn, self._supplier_id)
            showinfo("Saved", "Return %s saved." % return_no, parent=self.parent)
            self.return_items.clear()
            self._refresh_tree()
        except Exception as exc:
            self.conn.rollback()
            showerror("Failed", str(exc), parent=self.parent)


class _WriteoffTab:
    def __init__(self, page, parent, lines):
        self.conn = page.conn
        self.parent = parent
        self.lines = list(lines)
        self.reason_var = tk.StringVar(value="No purchase record")
        ttk.Label(parent, text="No purchase bill — write-off these items.",
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground="#666").pack(
            anchor=tk.W, padx=8, pady=6)
        cols = ("Medicine", "Batch", "Qty", "Tag")
        lf = ttk.LabelFrame(parent, text="Write-off")
        lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (220, 90, 70, 160)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        for line in self.lines:
            self.tree.insert("", tk.END, values=(
                line.get("medicine_name"), line.get("batch_no"),
                line.get("quantity"), line.get("reason_tag")))
        ttk.Entry(parent, textvariable=self.reason_var, width=50).pack(fill=tk.X, padx=8, pady=4)
        pack_centered_buttons(parent, [
            {"text": "Submit write-offs", "command": self._submit, "bootstyle": "danger"},
        ], pady=8)

    def _submit(self):
        if not self.lines or not askyesno("Confirm", "Submit write-offs?", parent=self.parent):
            return
        reason = self.reason_var.get().strip() or "Write-off"
        try:
            for line in self.lines:
                submit_writeoff(self.conn, int(line["medicine_id"]), float(line.get("quantity") or 0),
                                reason, batch_no=line.get("batch_no", ""), notes=line.get("reason_tag", ""))
            showinfo("Done", "Write-offs saved.", parent=self.parent)
            self.lines.clear()
            for iid in self.tree.get_children():
                self.tree.delete(iid)
        except Exception as exc:
            self.conn.rollback()
            showerror("Failed", str(exc), parent=self.parent)


class BulkPurchaseReturnPage:
    def __init__(self, parent, conn, prefill):
        self.parent = parent
        self.conn = conn
        self.cursor = conn.cursor()
        self._prefill = prefill or {}
        self._build_ui()

    def _build_ui(self):
        shell = ttk.Frame(self.parent)
        shell.pack(fill=tk.BOTH, expand=True)
        self._inner_frame = make_scrollable(shell)
        root = ttk.Frame(self._inner_frame)
        root.pack(fill=tk.BOTH, expand=True)
        ttk.Label(root, text="One tab per purchase bill.",
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground="#666").pack(
            anchor=tk.W, padx=8, pady=6)
        nb = ScrollableTabNotebook(root, hint="Scroll tabs")
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        nb.bind_shortcuts(self.parent)
        for group in self._prefill.get("purchase_groups") or []:
            label = (group.get("bill_number") or ("P%s" % group["purchase_id"]))[:22]
            frame = ttk.Frame(nb._content)
            _PurchaseReturnTab(self, frame, group)
            nb.add(frame, label)
        wo = self._prefill.get("writeoff_lines") or []
        if wo:
            frame = ttk.Frame(nb._content)
            _WriteoffTab(self, frame, wo)
            nb.add(frame, "Write-off")
''', encoding='utf-8')
print("Wrote", DEST)
