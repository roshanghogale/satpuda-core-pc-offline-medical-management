"""Reorder — one notebook tab per supplier (like Sales multi-tab)."""
from __future__ import annotations

import re
import tkinter as tk
from datetime import date
from typing import Any, Dict, List, Optional

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT
from core.scroll_manager import open_dialog, pack_centered_buttons
from core.themed_messagebox import askyesno, showerror, showinfo, showwarning
from core.reorder_service import (
    _as_str,
    build_purchase_prefill,
    cancel_pending_order_group,
    current_stock_for_medicine,
    fetch_medicine_names_for_supplier,
    fetch_medicine_suppliers,
    fetch_pending_order_groups,
    fetch_pending_orders_by_group,
    fetch_supplier_choices,
    mark_order_group_received,
    min_stock_level,
    save_supplier_pending_orders,
    suggest_order_quantity,
)
from core.purchase_service import get_or_create_supplier
from widgets.searchable_combo import SearchableCombo
from widgets.scrollable_tab_notebook import ScrollableTabNotebook


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


def _empty_tab_state(label: str) -> Dict[str, Any]:
    return {
        "label": label,
        "supplier_id": None,
        "supplier_name": "",
        "phone": "",
        "offline": False,
        "offline_note": "",
        "delivery": "",
        "notes": "",
        "lines": [],
        "editing_group_id": None,
    }


class _SupplierOrderTab:
    """One supplier = one notebook page with its own form."""

    def __init__(self, page: "ReorderPage", parent, state: Dict[str, Any]):
        self.page = page
        self.state = state
        self.frame = parent

        hdr = ttk.Frame(parent)
        hdr.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(hdr, text="Supplier:").pack(side=tk.LEFT)
        self.supplier_combo = SearchableCombo(hdr, width=32)
        self.supplier_combo.pack(side=tk.LEFT, padx=6)
        self.supplier_combo.configure(values=page._supplier_labels)
        self.supplier_combo.bind("<<ComboboxSelected>>", self._on_supplier_pick)

        self.manual_name = tk.StringVar(value=state.get("supplier_name", ""))
        self.manual_phone = tk.StringVar(value=state.get("phone", ""))
        self.offline_var = tk.BooleanVar(value=bool(state.get("offline")))
        self.offline_note = tk.StringVar(value=state.get("offline_note", ""))
        self.delivery_var = tk.StringVar(value=state.get("delivery", ""))

        ttk.Label(hdr, text="Manual").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(hdr, textvariable=self.manual_name, width=16).pack(side=tk.LEFT, padx=4)
        ttk.Label(hdr, text="Phone").pack(side=tk.LEFT)
        ttk.Entry(hdr, textvariable=self.manual_phone, width=10).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(hdr, text="Offline", variable=self.offline_var).pack(side=tk.LEFT, padx=6)

        list_frame = ttk.LabelFrame(parent, text="Medicines to order")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        cols = ("Medicine", "Pack", "Qty", "Rate")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (220, 70, 60, 70)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor=tk.W if c == "Medicine" else tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.tree.bind("<Double-1>", self._edit_qty)

        add_row = ttk.Frame(list_frame)
        add_row.pack(fill=tk.X, padx=4, pady=4)
        self.med_combo = SearchableCombo(add_row, width=28)
        self.med_combo.pack(side=tk.LEFT, padx=4)
        self.pack_var = tk.StringVar()
        self.qty_var = tk.StringVar(value="0")
        ttk.Entry(add_row, textvariable=self.pack_var, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Entry(add_row, textvariable=self.qty_var, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(add_row, text="Add", command=self._add_line).pack(side=tk.LEFT, padx=6)
        ttk.Button(add_row, text="Remove", command=self._remove_line).pack(side=tk.LEFT)

        opts = ttk.Frame(parent)
        opts.pack(fill=tk.X, padx=6)
        ttk.Label(opts, text="Delivery").pack(side=tk.LEFT)
        ttk.Entry(opts, textvariable=self.delivery_var, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Label(opts, text="Offline note").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(opts, textvariable=self.offline_note, width=18).pack(side=tk.LEFT, padx=4)

        ttk.Label(parent, text="Notes").pack(anchor=tk.W, padx=6)
        self.notes_text = tk.Text(parent, height=2, width=50)
        self.notes_text.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.notes_text.insert("1.0", state.get("notes", ""))

        act = ttk.Frame(parent)
        act.pack(fill=tk.X, padx=6, pady=8)
        pack_centered_buttons(act, [
            {"text": "Save Draft", "command": lambda: self._save("draft")},
            {"text": "Mark Ordered", "command": lambda: self._save("ordered")},
        ], pady=0)

        self._apply_state_to_form()

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

    def _apply_state_to_form(self):
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
            self.tree.insert("", tk.END, values=(
                line.get("medicine_name", ""),
                line.get("pack_size", ""),
                line.get("quantity", 0),
                f"{_safe_float(line.get('unit_price')):.2f}",
            ))

    def _lines_from_tree(self) -> List[Dict[str, Any]]:
        old_ids: Dict[tuple, Any] = {}
        for ln in self.state.get("lines") or []:
            key = (_as_str(ln.get("medicine_name")), _as_str(ln.get("pack_size")))
            if ln.get("id"):
                old_ids[key] = ln["id"]
        lines = []
        for iid in self.tree.get_children():
            v = self.tree.item(iid)["values"]
            key = (_as_str(v[0]), _as_str(v[1]))
            row = {
                "medicine_name": _as_str(v[0]),
                "pack_size": _as_str(v[1]),
                "quantity": _safe_float(v[2]),
                "unit_price": _safe_float(v[3]),
            }
            if key in old_ids:
                row["id"] = old_ids[key]
            lines.append(row)
        return lines

    def _build_line(self, name: str, pack: str = "", qty: float = 0) -> Optional[Dict[str, Any]]:
        return self.page._build_line_for_supplier(
            name, pack, qty,
            self.state.get("supplier_id"),
            self._supplier_index(),
        )

    def _add_line(self):
        if self._supplier_index() < 0 and not self.manual_name.get().strip():
            showwarning("Supplier", "Select a supplier on this tab first.", parent=self.page.parent)
            return
        name = (self.med_combo.get() or "").strip()
        if not name:
            return
        line = self._build_line(name, self.pack_var.get(), _safe_float(self.qty_var.get()))
        if not line:
            return
        lines = self._lines_from_tree()
        lines.append(line)
        self._fill_tree(lines)
        self.med_combo.set("")
        self.pack_var.set("")
        self.qty_var.set("0")

    def _remove_line(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)

    def _edit_qty(self, event):
        col = self.tree.identify_column(event.x)
        if self.tree.identify_region(event.x, event.y) != "cell" or col not in ("#3", "#4"):
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        vals = list(self.tree.item(row, "values"))
        is_qty = col == "#3"
        dlg = open_dialog(
            self.page.parent,
            "Edit quantity" if is_qty else "Edit rate",
            width=300, height=140, resizable=False,
        )
        ttk.Label(dlg.body, text="Quantity:" if is_qty else "Rate (Rs):").pack(pady=(12, 4))
        var = tk.StringVar(value=str(vals[2 if is_qty else 3]))
        ent = ttk.Entry(dlg.body, textvariable=var, width=14)
        ent.pack()
        ent.focus()
        ent.select_range(0, tk.END)

        def _ok():
            try:
                num = float(var.get())
            except ValueError:
                showwarning("Invalid", "Enter a number.", parent=dlg)
                return
            if is_qty:
                vals[2] = num
            else:
                vals[3] = f"{num:.2f}"
            self.tree.item(row, values=vals)
            dlg.destroy()

        ent.bind("<Return>", lambda e: _ok())
        ttk.Button(dlg.footer, text="OK", command=_ok).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT)

    def _sync_state_from_form(self):
        idx = self._supplier_index()
        self.state["supplier_id"] = (
            int(self.page._supplier_options[idx]["supplier_id"]) if idx >= 0 else None
        )
        self.state["supplier_name"] = self.manual_name.get().strip()
        self.state["phone"] = self.manual_phone.get().strip()
        self.state["offline"] = self.offline_var.get()
        self.state["offline_note"] = self.offline_note.get().strip()
        self.state["delivery"] = self.delivery_var.get().strip()
        self.state["notes"] = self.notes_text.get("1.0", tk.END).strip()
        self.state["lines"] = self._lines_from_tree()
        label = self.state["supplier_name"] or self.state.get("label", "Supplier")
        self.state["label"] = label[:24]

    def _resolve_supplier_id(self) -> Optional[int]:
        self._sync_state_from_form()
        if self.state.get("supplier_id"):
            return int(self.state["supplier_id"])
        manual = self.state.get("supplier_name") or ""
        if manual and not self.state.get("offline"):
            return get_or_create_supplier(
                self.page.conn, manual, "", self.state.get("phone", ""), "", "")
        return None

    def _save(self, status: str):
        self._sync_state_from_form()
        lines = self.state.get("lines") or []
        if not lines:
            showwarning("Save", "Add medicines on this supplier tab.", parent=self.page.parent)
            return
        supplier_id = self._resolve_supplier_id()
        manual = self.state.get("supplier_name") or ""
        if status == "ordered" and not supplier_id and not self.state.get("offline") and not manual:
            showerror("Supplier", "Set supplier or mark offline.", parent=self.page.parent)
            return
        built_lines: List[Dict[str, Any]] = []
        try:
            for line in lines:
                qty = _safe_float(line.get("quantity"))
                if qty <= 0:
                    continue
                full = self.page._build_line_for_supplier(
                    line.get("medicine_name", ""),
                    line.get("pack_size", ""),
                    qty,
                    supplier_id,
                    -1,
                ) or line
                full["unit_price"] = _safe_float(line.get("unit_price") or full.get("unit_price"))
                if line.get("id"):
                    full["id"] = line["id"]
                built_lines.append(full)
            if not built_lines:
                showwarning("Save", "Add medicines with quantity greater than zero.", parent=self.page.parent)
                return
            result = save_supplier_pending_orders(
                self.page.conn,
                {
                    "supplier_id": supplier_id,
                    "supplier_name_manual": manual if not supplier_id else "",
                    "supplier_phone": self.state.get("phone", ""),
                    "supplier_email": "",
                    "order_offline": self.state.get("offline"),
                    "offline_note": self.state.get("offline_note", ""),
                    "expected_delivery_date": self.state.get("delivery", ""),
                    "notes": self.state.get("notes", ""),
                    "order_date": str(date.today()),
                },
                built_lines,
                status=status,
                group_id=self.state.get("editing_group_id"),
            )
            showinfo(
                "Reorder",
                f"Saved supplier order ({result['count']} medicine(s)) for "
                f"{self.state.get('label', 'supplier')}.",
                parent=self.page.parent,
            )
            if status == "ordered":
                try:
                    from core.document_output import offer_reorder_document
                    gid = result.get("group_id")
                    if gid:
                        safe = re.sub(r"[^\w\-]+", "_", (self.state.get("label") or "reorder"))[:40]
                        offer_reorder_document(
                            self.page.parent, self.page.conn, gid, f"Reorder_{safe}",
                        )
                except Exception as exc:
                    showerror("PDF saved", f"Could not save PDF:\n{exc}", parent=self.page.parent)
            self._finish_after_save()
        except Exception as exc:
            self.page.conn.rollback()
            showerror("Reorder", str(exc), parent=self.page.parent)

    def _finish_after_save(self):
        self.page.load_pending()
        if len(self.page._tabs) > 1:
            self.page._tab_notebook.remove_active()
        else:
            self.state["editing_group_id"] = None
            self.state["lines"] = []
            self._fill_tree([])
            self.page.show_pending()


class ReorderPage:
    def __init__(self, parent, conn, prefill=None):
        self.parent = parent
        self.conn = conn
        self.cursor = conn.cursor()
        self._prefill = prefill or {}
        self._supplier_options: List[Dict[str, Any]] = []
        self._supplier_labels: List[str] = []
        self._tabs: List[_SupplierOrderTab] = []
        self._build_ui()
        self.parent.after(100, self._apply_prefill)

    def _default_tab_label(self, tab_number: int) -> str:
        return f"Supplier {int(tab_number)}"

    def _is_default_tab_label(self, label: str) -> bool:
        text = (label or "").strip()
        return text.startswith("Supplier ") and text[9:].isdigit()

    def _normalize_default_tab_labels(self):
        for idx, tab in enumerate(self._tabs, start=1):
            label = tab.state.get("label", "")
            if self._is_default_tab_label(label):
                new_label = self._default_tab_label(idx)
                tab.state["label"] = new_label
                self._tab_notebook.update_label(idx - 1, new_label)

    def get_keyboard_bindings(self):
        from core.keyboard_registry import PageBindings
        return PageBindings(
            page_id="reorder",
            on_f5=self.load_pending,
            on_ctrl_shift_n=self._tab_new_shortcut,
            on_ctrl_shift_w=self._tab_close_shortcut,
            on_ctrl_prior=self._tab_prev_shortcut,
            on_ctrl_next=self._tab_next_shortcut,
        )

    def _on_orders_view(self) -> bool:
        try:
            return bool(self._orders_view.winfo_ismapped())
        except Exception:
            return False

    def _tab_new_shortcut(self, event=None):
        if not self._on_orders_view():
            return "break"
        self._add_tab()
        return "break"

    def _tab_close_shortcut(self, event=None):
        if not self._on_orders_view():
            return "break"
        if len(self._tabs) <= 1:
            from core.themed_messagebox import showinfo
            showinfo("Tabs", "At least one supplier tab must remain open.", parent=self.parent)
            return "break"
        self._tab_notebook.remove_active()
        return "break"

    def _tab_prev_shortcut(self, event=None):
        if not self._on_orders_view() or not self._tab_notebook._tabs:
            return "break"
        self._tab_notebook.prev_tab()
        return "break"

    def _tab_next_shortcut(self, event=None):
        if not self._on_orders_view() or not self._tab_notebook._tabs:
            return "break"
        self._tab_notebook.next_tab()
        return "break"

    def _build_ui(self):
        self._root = ttk.Frame(self.parent)
        self._root.pack(fill=tk.BOTH, expand=True)

        self._orders_view = ttk.Frame(self._root)
        self._pending_view = ttk.Frame(self._root)

        from widgets.scrollable_tab_notebook import TAB_SHORTCUT_HINT
        self._tab_notebook = ScrollableTabNotebook(
            self._orders_view,
            on_add=self._add_tab,
            on_remove=self._on_tab_removed,
            add_label="+ Supplier",
            hint=TAB_SHORTCUT_HINT,
        )
        self._tab_notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        self._build_pending_view()
        self._reload_all_suppliers()
        self.show_pending()

    def _build_pending_view(self):
        top = ttk.Frame(self._pending_view)
        top.pack(fill=tk.X, padx=6, pady=6)
        self._status_var = tk.StringVar(value="active")
        ttk.Label(top, text="Show:").pack(side=tk.LEFT)
        for val, label in (("active", "Active"), ("received", "Received")):
            ttk.Radiobutton(
                top, text=label, variable=self._status_var, value=val,
                command=self.load_pending,
            ).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Refresh", command=self.load_pending).pack(side=tk.RIGHT)

        cols = ("Order No", "Supplier", "Medicines", "Items", "Status", "Date")
        self.pending_tree = ttk.Treeview(self._pending_view, columns=cols, show="headings", height=16)
        self.pending_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        col_w = {
            "Order No": 110, "Supplier": 160, "Medicines": 260,
            "Items": 55, "Status": 80, "Date": 95,
        }
        for c in cols:
            self.pending_tree.heading(c, text=c)
            self.pending_tree.column(c, width=col_w.get(c, 95))
        self.pending_tree.bind("<Double-1>", lambda e: self._edit_selected())

        pack_centered_buttons(self._pending_view, [
            {"text": "Edit", "command": self._edit_selected},
            {"text": "Receive", "command": self._receive_selected},
            {"text": "Cancel", "command": self._cancel_selected},
        ], pady=8)
        self.load_pending()

    def show_pending(self):
        self._orders_view.pack_forget()
        self._pending_view.pack(fill=tk.BOTH, expand=True)

    def show_orders(self):
        self._pending_view.pack_forget()
        self._orders_view.pack(fill=tk.BOTH, expand=True)
        if not self._tabs:
            self._add_tab(_empty_tab_state(self._default_tab_label(1)))

    def _reload_all_suppliers(self):
        self._supplier_options = fetch_supplier_choices(self.conn)
        self._supplier_labels = [
            f"{s['name']} | {s.get('phone') or ''}" for s in self._supplier_options
        ]

    def _add_tab(self, state: Optional[Dict[str, Any]] = None):
        if state is None:
            state = _empty_tab_state(self._default_tab_label(len(self._tabs) + 1))
        frame = ttk.Frame(self._tab_notebook._content)
        tab = _SupplierOrderTab(self, frame, state)
        self._tabs.append(tab)
        self._tab_notebook.add(frame, state.get("label", "Supplier")[:20], tab_obj=tab)

    def _on_tab_removed(self, idx: int):
        if 0 <= idx < len(self._tabs):
            del self._tabs[idx]
        self._normalize_default_tab_labels()

    def _clear_supplier_tabs(self):
        self._tab_notebook.clear()
        self._tabs.clear()

    def _build_line_for_supplier(
        self, name: str, pack: str, qty: float,
        supplier_id: Optional[int], supplier_index: int,
    ) -> Optional[Dict[str, Any]]:
        name = _as_str(name)
        if not name:
            return None
        online = False
        try:
            from core.sync_prefs import is_online_mode
            online = bool(is_online_mode())
        except Exception:
            online = False
        if online:
            from core.online_catalog import medicines_for_name
            med_type = "Others"
            rate = 0.0
            unit = ""
            for m in medicines_for_name(name):
                if m.get("is_hidden"):
                    continue
                unit = _as_str(m.get("unit")) or unit
                med_type = (m.get("type") or med_type) or "Others"
                rate = _safe_float(m.get("rate") or rate)
                break
            pack = _as_str(pack or unit)
            stock = current_stock_for_medicine(self.conn, name, pack)
            suggested = suggest_order_quantity(self.conn, name, med_type, stock, pack)
            qty_f = qty if qty > 0 else suggested
            return {
                "medicine_name": name,
                "pack_size": pack,
                "quantity": qty_f,
                "unit_price": rate,
                "current_stock": stock,
                "min_stock": min_stock_level(self.conn, med_type),
            }
        self.cursor.execute(
            "SELECT COALESCE(unit,''), COALESCE(type,'Others') FROM medicines "
            "WHERE name=? AND COALESCE(is_hidden,0)=0 ORDER BY id DESC LIMIT 1",
            (name,),
        )
        row = self.cursor.fetchone()
        pack = _as_str(pack or (row[0] if row else "") or "")
        med_type = (row[1] if row else "Others") or "Others"
        stock = current_stock_for_medicine(self.conn, name, pack)
        suggested = suggest_order_quantity(self.conn, name, med_type, stock, pack)
        qty_f = qty if qty > 0 else suggested
        rate = 0.0
        sid = supplier_id
        if sid is None and supplier_index >= 0:
            sid = int(self._supplier_options[supplier_index]["supplier_id"])
        for s in fetch_medicine_suppliers(self.conn, name):
            if sid and int(s.get("supplier_id") or 0) == int(sid):
                rate = _safe_float(s.get("last_rate"))
                break
        if rate <= 0:
            self.cursor.execute(
                "SELECT COALESCE(rate,0) FROM medicines WHERE name=? LIMIT 1", (name,))
            r = self.cursor.fetchone()
            rate = _safe_float(r[0] if r else 0)
        return {
            "medicine_name": name,
            "pack_size": pack,
            "quantity": qty_f,
            "unit_price": rate,
            "current_stock": stock,
            "min_stock": min_stock_level(self.conn, med_type),
        }

    def _load_bulk_tabs(self):
        from core.reorder_service import collect_reorder_candidates, _resolve_supplier_id
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in collect_reorder_candidates(self.conn):
            supplier = (item.get("supplier_name") or "").strip() or "(No Supplier)"
            groups.setdefault(supplier, []).append(item)
        if not groups:
            return
        self._clear_supplier_tabs()
        for label in sorted(groups.keys(), key=lambda x: x.lower()):
            sid = None if label == "(No Supplier)" else _resolve_supplier_id(self.conn, label)
            lines = []
            for item in groups[label]:
                lines.append({
                    "medicine_name": item["medicine_name"],
                    "pack_size": item.get("pack_size", ""),
                    "quantity": _safe_float(item.get("suggested_qty")),
                    "unit_price": _safe_float(item.get("unit_price")),
                })
            state = _empty_tab_state(label[:24])
            state["supplier_id"] = sid
            state["supplier_name"] = "" if label == "(No Supplier)" else label
            state["offline"] = label == "(No Supplier)"
            state["lines"] = lines
            self._add_tab(state)
        self.show_orders()

    def _apply_prefill(self):
        p = self._prefill
        if not p:
            return
        if p.get("bulk"):
            self._load_bulk_tabs()
            return
        self.show_orders()
        if not self._tabs:
            self._add_tab(_empty_tab_state(self._default_tab_label(1)))
        name = p.get("medicine_name", "")
        if not name:
            return
        line = self._build_line_for_supplier(
            name, str(p.get("pack_size") or p.get("unit") or ""),
            _safe_float(p.get("suggested_qty")), None, -1)
        if line and p.get("unit_price") is not None:
            line["unit_price"] = _safe_float(p.get("unit_price"))
        if line:
            self._tabs[0].state["lines"] = [line]
            self._tabs[0]._fill_tree([line])
        for s in fetch_medicine_suppliers(self.conn, name):
            self._tabs[0].state["supplier_id"] = int(s["supplier_id"])
            self._tabs[0].state["supplier_name"] = s.get("name") or ""
            self._tabs[0].state["label"] = (s.get("name") or "Supplier")[:24]
            self._tabs[0]._apply_state_to_form()
            self._tab_notebook.update_label(0, self._tabs[0].state["label"])
            break

    def load_pending(self):
        for i in self.pending_tree.get_children():
            self.pending_tree.delete(i)
        for row in fetch_pending_order_groups(self.conn):
            status = (row.get("status") or "").lower()
            if status == "cancelled":
                continue
            if self._status_var.get() == "active" and status == "received":
                continue
            if self._status_var.get() == "received" and status != "received":
                continue
            self.pending_tree.insert("", tk.END, iid=row["group_id"], values=(
                row.get("order_no"),
                row.get("supplier_name"),
                row.get("medicines_preview"),
                row.get("medicine_count"),
                row.get("status"),
                row.get("order_date"),
            ))

    def _selected_group_id(self):
        sel = self.pending_tree.selection()
        if not sel:
            showwarning("Select", "Select an order.", parent=self.parent)
            return None
        return sel[0]

    def _edit_selected(self):
        group_id = self._selected_group_id()
        if not group_id:
            return
        orders = fetch_pending_orders_by_group(self.conn, group_id)
        if not orders:
            showwarning("Edit", "Order not found.", parent=self.parent)
            return
        if (orders[0].get("status") or "").lower() == "received":
            showwarning("Edit", "Cannot edit a received order.", parent=self.parent)
            return
        self.show_orders()
        self._clear_supplier_tabs()
        first = orders[0]
        lines: List[Dict[str, Any]] = []
        for order in orders:
            line = self._build_line_for_supplier(
                order.get("medicine_name") or "",
                order.get("pack_size") or "",
                _safe_float(order.get("quantity")),
                order.get("supplier_id"),
                -1,
            )
            if line:
                line["unit_price"] = _safe_float(order.get("unit_price"))
                line["id"] = order.get("id")
                lines.append(line)
        supplier_label = (
            first.get("supplier_name")
            or first.get("supplier_name_manual")
            or "Supplier"
        )
        state = _empty_tab_state(str(supplier_label)[:24])
        real_group_id = first.get("order_group_id") or (
            group_id if not str(group_id).startswith("single:") else None
        )
        state.update({
            "supplier_id": first.get("supplier_id"),
            "supplier_name": first.get("supplier_name_manual") or supplier_label,
            "phone": first.get("supplier_phone") or "",
            "offline": bool(first.get("order_offline")),
            "offline_note": first.get("offline_note") or "",
            "delivery": first.get("expected_delivery_date") or "",
            "notes": first.get("notes") or "",
            "lines": lines,
            "editing_group_id": real_group_id,
            "label": str(supplier_label)[:24],
        })
        self._add_tab(state)

    def _receive_selected(self):
        group_id = self._selected_group_id()
        if not group_id:
            return
        orders = fetch_pending_orders_by_group(self.conn, group_id)
        if not orders or (orders[0].get("status") or "").lower() == "received":
            return
        first_id = int(orders[0]["id"])
        dlg = open_dialog(self.parent, "Receive Order", width=460, height=200)
        body = dlg.content
        ttk.Label(
            body,
            text=(
                "Add stock on the Purchase page (new batch, expiry, qty).\n"
                "Then click Mark Received to clear this pending order."
            ),
            wraplength=420,
        ).pack(anchor=tk.W, padx=12, pady=(12, 8))
        choice = {"mode": None}
        try:
            ttk.Button(dlg.footer, text="Mark Received", bootstyle="success",
                       command=lambda: (choice.update(mode="received"), dlg.destroy())).pack(side=tk.LEFT, padx=4)
            ttk.Button(dlg.footer, text="Open Purchase", bootstyle="info",
                       command=lambda: (choice.update(mode="purchase"), dlg.destroy())).pack(side=tk.LEFT, padx=4)
            ttk.Button(dlg.footer, text="Cancel", bootstyle="secondary",
                       command=dlg.destroy).pack(side=tk.RIGHT, padx=4)
        except Exception:
            ttk.Button(dlg.footer, text="Mark Received",
                       command=lambda: (choice.update(mode="received"), dlg.destroy())).pack(side=tk.LEFT, padx=4)
            ttk.Button(dlg.footer, text="Open Purchase",
                       command=lambda: (choice.update(mode="purchase"), dlg.destroy())).pack(side=tk.LEFT, padx=4)
            ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT, padx=4)
        dlg.wait_window()
        if choice["mode"] == "purchase":
            try:
                prefill = build_purchase_prefill(self.conn, first_id)
                app = getattr(self.parent.winfo_toplevel(), "_main_app", None)
                if app:
                    app.open_purchase()
                    page = getattr(app, "_purchase_page", None)
                    if page and hasattr(page, "apply_reorder_prefill"):
                        page.apply_reorder_prefill(prefill)
            except Exception as e:
                showerror("Purchase", str(e), parent=self.parent)
            return
        if choice["mode"] != "received":
            return
        try:
            mark_order_group_received(self.conn, group_id)
            showinfo("Received", "Supplier order marked received.", parent=self.parent)
            self.load_pending()
            try:
                from core.page_refresh import refresh_after_purchase
                refresh_after_purchase(self.parent)
            except Exception:
                pass
        except Exception as e:
            self.conn.rollback()
            showerror("Receive", str(e), parent=self.parent)

    def _cancel_selected(self):
        group_id = self._selected_group_id()
        if group_id and askyesno("Cancel", "Cancel this supplier order and all its medicines?", parent=self.parent):
            cancel_pending_order_group(self.conn, group_id)
            self.load_pending()
