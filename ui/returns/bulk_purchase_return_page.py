"""Bulk purchase return — one tab per purchase bill."""
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
    def __init__(self, page: "BulkPurchaseReturnPage", parent, group: Dict[str, Any]):
        self.page = page
        self.group = group
        self.conn = page.conn
        self.cursor = page.cursor
        self.parent = parent
        self.return_items: List[dict] = []
        self._purchase_id = int(group["purchase_id"])
        self._supplier_id: Optional[int] = None
        self._orig_items_data: List[dict] = []

        hdr = ttk.LabelFrame(parent, text="Purchase")
        hdr.pack(fill=tk.X, padx=6, pady=6)
        bill = group.get("bill_number") or f"#{self._purchase_id}"
        sup = group.get("supplier_name") or ""
        ttk.Label(
            hdr, text=f"{bill}  |  {sup}",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=8, pady=6)

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
        ttk.Label(
            row, textvariable=self.refund_var,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT, "bold"),
        ).pack(side=tk.LEFT, padx=4)

        pack_centered_buttons(parent, [
            {"text": "Save this purchase return", "command": self._save, "bootstyle": "danger"},
        ], pady=8)

        self._load_purchase()
        for line in group.get("lines", []):
            self._add_line(
                int(line["medicine_id"]),
                float(line.get("quantity") or 0),
                line.get("batch_no", ""),
            )
        self._refresh_tree()

    def _get_tps(self, unit_str, med_type):
        if not is_strip_count_type(med_type):
            return 1
        return parse_tablets_per_stripe(unit_str)

    def _already_returned(self, purchase_id, medicine_id):
        self.cursor.execute(
            """SELECT COALESCE(SUM(pri.qty), 0) FROM purchase_return_items pri
               JOIN purchase_returns pr ON pri.return_id = pr.id
               WHERE pr.purchase_id = ? AND pri.medicine_id = ?""",
            (purchase_id, medicine_id),
        )
        row = self.cursor.fetchone()
        return float(row[0]) if row else 0.0

    def _load_purchase(self):
        self.cursor.execute(
            """SELECT p.id, s.id FROM purchases p
               JOIN suppliers s ON p.supplier_id = s.id WHERE p.id = ?""",
            (self._purchase_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return
        self._supplier_id = int(row[1])
        self.cursor.execute(
            """SELECT m.name, pi.batch_no, pi.qty, pi.rate, COALESCE(pi.type,''),
                      pi.medicine_id, COALESCE(m.unit,'1')
               FROM purchase_items pi JOIN medicines m ON pi.medicine_id = m.id
               WHERE pi.purchase_id = ?""",
            (self._purchase_id,),
        )
        for med_name, batch, orig_qty, rate, med_type, med_id, unit in self.cursor.fetchall():
            orig_qty = float(orig_qty)
            tps = self._get_tps(unit, med_type)
            is_tablet = is_strip_count_type(med_type)
            already = self._already_returned(self._purchase_id, med_id)
            remaining = max(0.0, orig_qty - already)
            self._orig_items_data.append({
                "name": med_name, "batch": batch or "", "orig_qty": orig_qty,
                "rate": float(rate), "med_type": med_type, "med_id": med_id,
                "tps": tps, "remaining": remaining, "is_tablet": is_tablet,
            })

    def _add_line(self, med_id: int, qty: float, batch: str = ""):
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
            stock_deduction = use_qty * d["tps"] if d["is_tablet"] else use_qty
            self.return_items.append({
                "med_id": med_id, "name": d["name"], "batch": d.get("batch", ""),
                "qty": use_qty, "tps": d["tps"], "is_tablet": d["is_tablet"],
                "stock_deduction": stock_deduction, "rate": d["rate"],
                "amount": round(use_qty * d["rate"], 2),
            })
            return

    def _refresh_tree(self):
        for iid in self.ret_tree.get_children():
            self.ret_tree.delete(iid)
        for item in self.return_items:
            if item["is_tablet"]:
                ql = (
                    f"{item['qty']:.0f} strips x {item['tps']} "
                    f"= {item['stock_deduction']:.0f} tabs"
                )
            else:
                ql = f"{item['qty']:.0f} units"
            self.ret_tree.insert("", tk.END, values=(
                item["name"], item["batch"], ql,
                f"{item['rate']:.2f}", f"{item['amount']:.2f}",
            ))
        result = calc_return_refund(self.return_items, 0)
        self.refund_var.set(f"{result['refund_amount']:.2f}")

    def _save(self):
        if not self.return_items:
            showwarning("No items", "Nothing to return on this tab.", parent=self.parent)
            return
        bill = self.group.get("bill_number", "purchase")
        if not askyesno("Confirm", f"Save return for {bill}?", parent=self.parent):
            return
        reason = self.reason_var.get().strip()
        result = calc_return_refund(self.return_items, 0)
        refund = result["refund_amount"]
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.desktop_returns_service import save_purchase_return

                res = save_purchase_return(
                    self.conn,
                    {
                        "purchase_id": int(self._purchase_id or 0),
                        "supplier_id": int(self._supplier_id or 0),
                        "discount": 0,
                        "reason": reason,
                        "items": [
                            {
                                "medicine_id": int(item["med_id"]),
                                "qty": float(item["qty"]),
                                "rate": float(item["rate"]),
                            }
                            for item in self.return_items
                        ],
                    },
                )
                if not res.get("ok"):
                    showerror("Failed", res.get("error") or "Save failed", parent=self.parent)
                    return
                showinfo(
                    "Saved",
                    f"Return {res.get('return_no') or 'PENDING'} queued. "
                    f"Credit: {float(res.get('refund_amount') or refund):.2f}",
                    parent=self.parent,
                )
                self.return_items.clear()
                self._refresh_tree()
                self.page._mark_tab_done(self.group["purchase_id"])
                return
        except Exception as exc:
            showerror("Failed", str(exc), parent=self.parent)
            return

        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM purchase_returns")
        return_no = f"PR{self.cursor.fetchone()[0]}"
        try:
            self.cursor.execute(
                """INSERT INTO purchase_returns
                   (return_no, purchase_id, supplier_id, return_date, refund_amount, discount, reason)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (return_no, self._purchase_id, self._supplier_id,
                 datetime.now().date(), refund, reason),
            )
            return_id = self.cursor.lastrowid
            for item in self.return_items:
                self.cursor.execute(
                    """INSERT INTO purchase_return_items
                       (return_id, medicine_id, qty, rate, amount) VALUES (?, ?, ?, ?, ?)""",
                    (return_id, item["med_id"], item["qty"], item["rate"], item["amount"]),
                )
                self.cursor.execute(
                    "UPDATE medicines SET stock_qty = MAX(0, stock_qty - ?) WHERE id = ?",
                    (item["stock_deduction"], item["med_id"]),
                )
            self.conn.commit()
            from core.purchase_service import recalculate_supplier_due
            if self._supplier_id:
                recalculate_supplier_due(self.conn, self._supplier_id)
            showinfo("Saved", f"Return {return_no} saved. Credit: {refund:.2f}", parent=self.parent)
            self.return_items.clear()
            self._refresh_tree()
            self.page._mark_tab_done(self.group["purchase_id"])
        except Exception as exc:
            self.conn.rollback()
            showerror("Failed", str(exc), parent=self.parent)


class _WriteoffTab:
    def __init__(self, page: "BulkPurchaseReturnPage", parent, lines: List[Dict[str, Any]]):
        self.page = page
        self.conn = page.conn
        self.parent = parent
        self.lines = list(lines)
        self.reason_var = tk.StringVar(value="No purchase record")

        ttk.Label(
            parent,
            text="These medicines have no purchase bill — submit as write-off.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground="#666",
        ).pack(anchor=tk.W, padx=8, pady=6)

        cols = ("Medicine", "Batch", "Qty", "Reason")
        lf = ttk.LabelFrame(parent, text="Write-off items")
        lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (220, 90, 70, 160)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        for line in self.lines:
            self.tree.insert("", tk.END, values=(
                line.get("medicine_name", ""),
                line.get("batch_no", ""),
                line.get("quantity", 0),
                line.get("reason_tag", ""),
            ))
        ttk.Label(parent, text="Reason:").pack(anchor=tk.W, padx=8)
        ttk.Entry(parent, textvariable=self.reason_var, width=50).pack(fill=tk.X, padx=8, pady=4)
        pack_centered_buttons(parent, [
            {"text": "Submit write-offs", "command": self._submit, "bootstyle": "danger"},
        ], pady=8)

    def _submit(self):
        if not self.lines:
            return
        if not askyesno("Confirm", f"Write off {len(self.lines)} item(s)?", parent=self.parent):
            return
        reason = self.reason_var.get().strip() or "Write-off"
        try:
            for line in self.lines:
                submit_writeoff(
                    self.conn, int(line["medicine_id"]),
                    float(line.get("quantity") or 0),
                    reason, batch_no=line.get("batch_no", ""),
                    notes=line.get("reason_tag", ""),
                )
            showinfo("Done", f"{len(self.lines)} write-off(s) saved.", parent=self.parent)
            self.lines.clear()
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            from core.page_refresh import refresh_after_purchase
            refresh_after_purchase(self.parent)
        except Exception as exc:
            self.conn.rollback()
            showerror("Failed", str(exc), parent=self.parent)


class BulkPurchaseReturnPage:
    def __init__(self, parent, conn, prefill: dict):
        self.parent = parent
        self.conn = conn
        self.cursor = conn.cursor()
        self._prefill = prefill or {}
        self._tabs: List[Any] = []
        self._done: set = set()
        self._build_ui()

    def _build_ui(self):
        shell = ttk.Frame(self.parent)
        shell.pack(fill=tk.BOTH, expand=True)
        self._inner_frame = make_scrollable(shell)
        root = ttk.Frame(self._inner_frame)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            root,
            text="One tab per purchase — near-expiry and expired medicines pre-loaded.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground="#666",
        ).pack(anchor=tk.W, padx=8, pady=6)

        self._tab_notebook = ScrollableTabNotebook(root, hint="Scroll tabs")
        self._tab_notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self._tab_notebook.bind_shortcuts(self.parent)

        for group in self._prefill.get("purchase_groups") or []:
            label = (group.get("bill_number") or f"P{group['purchase_id']}")[:22]
            frame = ttk.Frame(self._tab_notebook._content)
            tab = _PurchaseReturnTab(self, frame, group)
            self._tabs.append(tab)
            self._tab_notebook.add(frame, label)

        writeoff = self._prefill.get("writeoff_lines") or []
        if writeoff:
            frame = ttk.Frame(self._tab_notebook._content)
            tab = _WriteoffTab(self, frame, writeoff)
            self._tabs.append(tab)
            self._tab_notebook.add(frame, "Write-off")

        if not (self._prefill.get("purchase_groups") or writeoff):
            ttk.Label(root, text="No return candidates found.").pack(padx=12, pady=20)

    def _mark_tab_done(self, purchase_id: int):
        self._done.add(int(purchase_id))
