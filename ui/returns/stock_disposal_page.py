"""Return / write-off — one notebook tab per supplier (like Sales)."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Dict, List, Optional

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT
from core.scroll_manager import pack_centered_buttons, make_scrollable
from core.themed_messagebox import askyesno, showerror, showinfo, showwarning
from core.stock_disposal_service import (
    fetch_medicine_batch,
    lookup_batch_purchase,
    lookup_medicine_by_name_batch,
    submit_return,
    submit_writeoff,
)
from core.reorder_service import (
    fetch_medicine_names_for_supplier,
    fetch_supplier_choices,
    lookup_medicine_id,
)
from core.purchase_service import get_or_create_supplier
from widgets.searchable_combo import SearchableCombo

RETURN_REASONS = ("Expired", "Near Expiry", "Damaged", "Wrong Item", "Other")


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _build_line(conn, name: str, batch: str = "") -> Optional[Dict[str, Any]]:
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core.desktop_returns_service import lookup_disposal_medicine
            res = lookup_disposal_medicine(
                conn, {"name": name, "batch": batch},
            )
            if not res.get("ok"):
                return None
            return {
                "medicine_id": int(res["medicine_id"]),
                "medicine_name": res.get("name") or name,
                "batch_no": res.get("batch") or batch,
                "quantity": _safe_float(res.get("stock_qty")),
                "purchase_info": res.get("purchase") or {},
            }
    except Exception:
        pass
    med = lookup_medicine_by_name_batch(conn, name, batch)
    if not med:
        mid = lookup_medicine_id(conn, name, batch_no=batch)
        if mid:
            med = fetch_medicine_batch(conn, mid)
    if not med:
        return None
    pinfo = lookup_batch_purchase(conn, med["medicine_id"], med.get("batch_no", ""))
    return {
        "medicine_id": med["medicine_id"],
        "medicine_name": med["name"],
        "batch_no": med.get("batch_no", ""),
        "quantity": _safe_float(med.get("stock_qty")),
        "purchase_info": pinfo,
    }


def _empty_return_tab(label: str) -> Dict[str, Any]:
    return {
        "label": label,
        "supplier_id": None,
        "supplier_name": "",
        "phone": "",
        "reason": RETURN_REASONS[0],
        "notes": "",
        "lines": [],
    }


class _SupplierReturnTab:
    def __init__(self, page: "StockDisposalPage", parent, state: Dict[str, Any]):
        self.page = page
        self.state = state
        self.frame = parent

        hdr = ttk.Frame(parent)
        hdr.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(hdr, text="Supplier:").pack(side=tk.LEFT)
        self.supplier_combo = SearchableCombo(hdr, width=30)
        self.supplier_combo.pack(side=tk.LEFT, padx=6)
        self.supplier_combo.configure(values=page._supplier_labels)
        self.supplier_combo.bind("<<ComboboxSelected>>", self._on_supplier_pick)

        self.manual_name = tk.StringVar(value=state.get("supplier_name", ""))
        self.manual_phone = tk.StringVar(value=state.get("phone", ""))
        self.reason_var = tk.StringVar(value=state.get("reason", RETURN_REASONS[0]))
        ttk.Label(hdr, text="Manual").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(hdr, textvariable=self.manual_name, width=14).pack(side=tk.LEFT, padx=4)
        ttk.Entry(hdr, textvariable=self.manual_phone, width=10).pack(side=tk.LEFT, padx=4)
        ttk.Combobox(
            hdr, textvariable=self.reason_var, values=RETURN_REASONS,
            state="readonly", width=12,
        ).pack(side=tk.LEFT, padx=8)

        list_frame = ttk.LabelFrame(parent, text="Medicines to return")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        cols = ("Medicine", "Batch", "Qty", "Type", "Bill")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (200, 80, 55, 80, 110)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        add_row = ttk.Frame(list_frame)
        add_row.pack(fill=tk.X, padx=4, pady=4)
        self.med_combo = SearchableCombo(add_row, width=26)
        self.med_combo.pack(side=tk.LEFT, padx=4)
        self.batch_var = tk.StringVar()
        ttk.Entry(add_row, textvariable=self.batch_var, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(add_row, text="Add", command=self._add_line).pack(side=tk.LEFT, padx=6)
        ttk.Button(add_row, text="Remove", command=self._remove_line).pack(side=tk.LEFT)

        ttk.Label(parent, text="Notes").pack(anchor=tk.W, padx=6)
        self.notes_text = tk.Text(parent, height=2, width=50)
        self.notes_text.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.notes_text.insert("1.0", state.get("notes", ""))

        pack_centered_buttons(parent, [
            {"text": "Submit this supplier", "command": self._submit, "bootstyle": "danger"},
        ], pady=8)

        self._apply_state()

    def _supplier_index(self) -> int:
        sel = self.supplier_combo.get().strip()
        if not sel:
            return -1
        try:
            return self.page._supplier_labels.index(sel)
        except ValueError:
            pass
        for i, s in enumerate(self.page._supplier_options):
            name = (s.get("name") or "").strip()
            if sel == name or sel.startswith(name + " |"):
                return i
        return -1

    def _on_supplier_pick(self, _e=None):
        idx = self._supplier_index()
        if idx >= 0:
            s = self.page._supplier_options[idx]
            self.manual_name.set(s.get("name") or "")
            self.manual_phone.set(s.get("phone") or "")
            self.state["supplier_id"] = int(s["supplier_id"])
        self._reload_medicines()

    def _reload_medicines(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import search_medicine_names
                names = []
                seen = set()
                for r in search_medicine_names("", limit=500, show_zero=False) or []:
                    n = str(r.get("name") or "").strip()
                    key = n.lower()
                    if not n or key in seen:
                        continue
                    seen.add(key)
                    names.append(n)
                self.med_combo.configure(values=names)
                return
        except Exception:
            pass
        sid = self.state.get("supplier_id")
        if not sid:
            idx = self._supplier_index()
            if idx >= 0:
                sid = int(self.page._supplier_options[idx]["supplier_id"])
        if sid:
            self.med_combo.configure(
                values=fetch_medicine_names_for_supplier(self.page.conn, int(sid)))
        else:
            self.med_combo.configure(values=[])

    def _line_type(self, pinfo) -> str:
        return "Return" if pinfo and pinfo.get("purchase_id") else "Write-off"

    def _apply_state(self):
        sid = self.state.get("supplier_id")
        if sid:
            for i, s in enumerate(self.page._supplier_options):
                if int(s["supplier_id"]) == int(sid) and i < len(self.page._supplier_labels):
                    self.supplier_combo.set(self.page._supplier_labels[i])
                    break
        self._fill_tree(self.state.get("lines", []))
        self._reload_medicines()

    def _fill_tree(self, lines: List[Dict[str, Any]]):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for line in lines:
            pinfo = line.get("purchase_info")
            self.tree.insert("", tk.END, values=(
                line.get("medicine_name", ""),
                line.get("batch_no", ""),
                line.get("quantity", 0),
                self._line_type(pinfo),
                (pinfo or {}).get("bill_number", "") or "—",
            ))

    def _build_line(self, name: str, batch: str = "") -> Optional[Dict[str, Any]]:
        return _build_line(self.page.conn, name, batch)

    def _lines_from_tree(self) -> List[Dict[str, Any]]:
        stored = {(
            l.get("medicine_name"), l.get("batch_no")): l
            for l in self.state.get("lines", [])
        }
        out = []
        for iid in self.tree.get_children():
            v = self.tree.item(iid)["values"]
            base = stored.get((v[0], v[1]), {})
            out.append({
                **base,
                "medicine_name": v[0],
                "batch_no": v[1],
                "quantity": _safe_float(v[2]),
            })
        return out

    def _add_line(self):
        if self._supplier_index() < 0 and not self.manual_name.get().strip():
            showwarning("Supplier", "Select supplier on this tab.", parent=self.page.parent)
            return
        line = self._build_line(self.med_combo.get(), self.batch_var.get().strip())
        if not line:
            showwarning("Not found", "Medicine not in stock.", parent=self.page.parent)
            return
        lines = self._lines_from_tree()
        key = (line["medicine_name"], line.get("batch_no", ""))
        if any((l.get("medicine_name"), l.get("batch_no")) == key for l in lines):
            showwarning("Duplicate", "Already listed.", parent=self.page.parent)
            return
        lines.append(line)
        self.state["lines"] = lines
        self._fill_tree(lines)
        self.med_combo.set("")
        self.batch_var.set("")

    def _remove_line(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
        self.state["lines"] = self._lines_from_tree()

    def _sync_state(self):
        idx = self._supplier_index()
        self.state["supplier_id"] = (
            int(self.page._supplier_options[idx]["supplier_id"]) if idx >= 0 else None
        )
        self.state["supplier_name"] = self.manual_name.get().strip()
        self.state["phone"] = self.manual_phone.get().strip()
        self.state["reason"] = self.reason_var.get()
        self.state["notes"] = self.notes_text.get("1.0", tk.END).strip()
        self.state["lines"] = self._lines_from_tree()
        label = self.state["supplier_name"] or self.state.get("label", "Supplier")
        self.state["label"] = label[:24]

    def _resolve_supplier_id(self) -> Optional[int]:
        if self.state.get("supplier_id"):
            return int(self.state["supplier_id"])
        manual = self.state.get("supplier_name") or ""
        if manual:
            return get_or_create_supplier(
                self.page.conn, manual, "", self.state.get("phone", ""), "", "")
        return None

    def _submit(self):
        self._sync_state()
        lines = self.state.get("lines") or []
        if not lines:
            showwarning("Submit", "Add medicines first.", parent=self.page.parent)
            return
        if not askyesno(
            "Confirm",
            f"Submit {len(lines)} line(s) for {self.state.get('label', 'supplier')}?",
            parent=self.page.parent,
        ):
            return
        supplier_id = self._resolve_supplier_id()
        reason = self.state.get("reason", RETURN_REASONS[0])
        notes = self.state.get("notes", "")
        ret_n = wo_n = 0
        try:
            for line in lines:
                med_id = int(line["medicine_id"])
                batch = line.get("batch_no", "")
                qty = _safe_float(line.get("quantity"))
                pinfo = line.get("purchase_info") or lookup_batch_purchase(
                    self.page.conn, med_id, batch)
                if pinfo and pinfo.get("purchase_id"):
                    submit_return(
                        self.page.conn, med_id, qty, reason, batch_no=batch,
                        supplier_id=pinfo.get("supplier_id") or supplier_id,
                        purchase_id=pinfo.get("purchase_id"),
                        bill_number=pinfo.get("bill_number", ""),
                        original_purchase_qty=_safe_float(pinfo.get("original_qty")),
                        expected_credit_note=True, notes=notes,
                    )
                    ret_n += 1
                else:
                    submit_writeoff(
                        self.page.conn, med_id, qty, reason, batch_no=batch, notes=notes)
                    wo_n += 1
            showinfo(
                "Done",
                f"{ret_n} return(s), {wo_n} write-off(s) for {self.state.get('label', 'supplier')}.",
                parent=self.page.parent,
            )
            from core.page_refresh import refresh_after_purchase
            refresh_after_purchase(self.page.parent)
            self.state["lines"] = []
            self._fill_tree([])
        except Exception as exc:
            self.page.conn.rollback()
            showerror("Failed", str(exc), parent=self.page.parent)


class StockDisposalPage:
    def __init__(self, parent, conn, prefill: dict | None = None):
        self.parent = parent
        self.conn = conn
        self._prefill = prefill or {}
        self._supplier_options: List[Dict[str, Any]] = []
        self._supplier_labels: List[str] = []
        self._tabs: List[_SupplierReturnTab] = []
        self._build_ui()
        self.parent.after(100, self._apply_prefill)

    def _build_ui(self):
        shell = ttk.Frame(self.parent)
        shell.pack(fill=tk.BOTH, expand=True)
        self._inner_frame = make_scrollable(shell)
        root = ttk.Frame(self._inner_frame)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X, padx=8, pady=6)
        self._header_label = ttk.Label(
            top,
            text="Write-off medicines with no purchase record.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            foreground="#666",
        )
        self._header_label.pack(side=tk.LEFT)
        try:
            self._add_btn = ttk.Button(
                top, text="+ Supplier", command=self._add_tab, bootstyle="success-outline")
        except Exception:
            self._add_btn = ttk.Button(top, text="+ Supplier", command=self._add_tab)
        self._add_btn.pack(side=tk.RIGHT)

        from widgets.scrollable_tab_notebook import ScrollableTabNotebook
        self._tab_notebook = ScrollableTabNotebook(
            root,
            on_add=self._add_tab,
            on_remove=self._on_tab_removed,
            add_label="+ Supplier",
        )
        self._tab_notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self._tab_notebook.bind_shortcuts(self.parent)

        self._reload_all_suppliers()
        if not self._tabs:
            self._add_tab(_empty_return_tab("Write-off"))

    def _reload_all_suppliers(self):
        self._supplier_options = fetch_supplier_choices(self.conn)
        self._supplier_labels = [
            f"{s['name']} | {s.get('phone') or ''}" for s in self._supplier_options
        ]

    def _on_tab_removed(self, idx: int):
        if 0 <= idx < len(self._tabs):
            del self._tabs[idx]

    def _clear_tabs(self):
        self._tab_notebook.clear()
        self._tabs.clear()

    def _add_tab(self, state: Optional[Dict[str, Any]] = None):
        if state is None:
            state = _empty_return_tab(f"Supplier {len(self._tabs) + 1}")
        frame = ttk.Frame(self._tab_notebook._content)
        tab = _SupplierReturnTab(self, frame, state)
        self._tabs.append(tab)
        self._tab_notebook.add(frame, state.get("label", "Supplier")[:20])

    def _apply_prefill(self):
        p = self._prefill
        if not p:
            return
        if p.get("writeoff_only") and p.get("lines"):
            self._header_label.configure(
                text="These items have no purchase — submit as write-off.")
            self._add_btn.pack_forget()
            self._clear_tabs()
            lines = []
            for item in p["lines"]:
                line = _build_line(
                    self.conn, item.get("medicine_name", ""), item.get("batch_no", ""))
                if line:
                    line["quantity"] = _safe_float(item.get("quantity"))
                    lines.append(line)
            state = _empty_return_tab("Write-off")
            state["reason"] = p.get("reason", RETURN_REASONS[0])
            state["lines"] = lines
            self._add_tab(state)
            return
        if p.get("bulk") and p.get("supplier_groups"):
            self._clear_tabs()
            for group in p["supplier_groups"]:
                label = (group.get("supplier_name") or "Supplier")[:24]
                lines = []
                for item in group.get("lines", []):
                    line = _build_line(
                        self.conn, item.get("medicine_name", ""), item.get("batch_no", ""))
                    if line:
                        if item.get("purchase_info"):
                            line["purchase_info"] = item["purchase_info"]
                        line["quantity"] = _safe_float(item.get("quantity"))
                        lines.append(line)
                state = _empty_return_tab(label)
                state["supplier_id"] = group.get("supplier_id")
                state["supplier_name"] = (
                    "" if group.get("supplier_name") == "(No Supplier)"
                    else group.get("supplier_name", ""))
                state["lines"] = lines
                self._add_tab(state)
            return
        name = p.get("medicine_name", "")
        if not name:
                return
        line = None
        if self._tabs:
            line = self._tabs[0]._build_line(name, p.get("batch_no", ""))
        if line:
            line["quantity"] = _safe_float(p.get("available_qty")) or line["quantity"]
            self._tabs[0].state["lines"] = [line]
            pinfo = line.get("purchase_info")
            if pinfo and pinfo.get("supplier_name"):
                self._tabs[0].state["supplier_name"] = pinfo["supplier_name"]
                self._tabs[0].state["label"] = pinfo["supplier_name"][:24]
            self._tabs[0]._apply_state()
            self._tab_notebook.update_label(0, self._tabs[0].state["label"])
