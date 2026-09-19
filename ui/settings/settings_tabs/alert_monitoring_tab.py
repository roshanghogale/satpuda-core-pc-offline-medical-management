"""Settings → Alert & Monitoring dashboard."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS, FONT_SIZE_SUPPORTING_TEXT
from core.themed_messagebox import showinfo, showerror, askyesno


_SECTIONS = (
    {
        "key": "low_stock",
        "label": "Low Stock",
        "title": "Low Stock Alerts",
        "columns": (
            "Medicine Name", "Current Stock", "Unit", "Supplier",
        ),
        "widths": {
            "Medicine Name": 240, "Current Stock": 130, "Unit": 100, "Supplier": 200,
        },
        "numeric_cols": {"Current Stock"},
        "center_cols": {"Current Stock", "Unit"},
        "export_name": "low_stock_alerts",
        "action_label": "Reorder",
    },
    {
        "key": "out_of_stock",
        "label": "Out of Stock",
        "title": "Out of Stock Medicines",
        "columns": (
            "Medicine Name", "Pack Size", "Selling Rate", "Purchase Rate",
            "Medicine Type", "Supplier",
        ),
        "widths": {
            "Medicine Name": 180, "Pack Size": 90, "Selling Rate": 100,
            "Purchase Rate": 100, "Medicine Type": 110, "Supplier": 150,
        },
        "numeric_cols": {"Selling Rate", "Purchase Rate"},
        "export_name": "out_of_stock_medicines",
        "action_label": "Reorder",
    },
    {
        "key": "expired",
        "label": "Expired",
        "title": "Expired Medicines",
        "columns": (
            "Medicine Name", "Batch Number", "Expiry Date", "Quantity Expired",
            "Supplier Name", "Bill Number",
        ),
        "widths": {
            "Medicine Name": 180, "Batch Number": 90, "Expiry Date": 100,
            "Quantity Expired": 110, "Supplier Name": 140, "Bill Number": 110,
        },
        "numeric_cols": {"Quantity Expired"},
        "export_name": "expired_medicines",
        "action_label": "Return",
    },
    {
        "key": "near_expiry",
        "label": "Near Expiry",
        "title": "Near Expiry Medicines",
        "columns": (
            "Medicine Name", "Batch Number", "Expiry Date", "Remaining Days",
            "Available Qty", "Supplier Name", "Bill Number",
        ),
        "widths": {
            "Medicine Name": 170, "Batch Number": 90, "Expiry Date": 100,
            "Remaining Days": 110, "Available Qty": 100, "Supplier Name": 130,
            "Bill Number": 100,
        },
        "numeric_cols": {"Remaining Days", "Available Qty"},
        "export_name": "near_expiry_medicines",
        "action_label": "Return",
    },
    {
        "key": "customer_due",
        "label": "Customer Dues",
        "title": "Outstanding Customer Dues",
        "columns": (
            "Customer Name", "Mobile Number", "Bill Number", "Bill Date",
            "Total Amount", "Paid Amount", "Due Amount", "Due Days",
        ),
        "widths": {
            "Customer Name": 160, "Mobile Number": 110, "Bill Number": 100,
            "Bill Date": 95, "Total Amount": 95, "Paid Amount": 95,
            "Due Amount": 95, "Due Days": 80,
        },
        "numeric_cols": {"Total Amount", "Paid Amount", "Due Amount", "Due Days"},
        "export_name": "customer_due_bills",
    },
)


class _AlertSection:
    def __init__(self, parent, spec: dict, on_export, on_action=None,
                 on_bulk_reorder=None, on_bulk_return=None,
                 on_dismiss=None, on_bulk_dismiss=None, on_bulk_dismiss_oos=None):
        self.spec = spec
        self._on_export = on_export
        self._on_action = on_action
        self._on_bulk_reorder = on_bulk_reorder
        self._on_bulk_return = on_bulk_return
        self._on_dismiss = on_dismiss
        self._on_bulk_dismiss = on_bulk_dismiss
        self._on_bulk_dismiss_oos = on_bulk_dismiss_oos
        self._all_rows: List[Tuple[Any, ...]] = []
        self._sort_col: Optional[str] = None
        self._sort_reverse = False

        self.frame = ttk.Frame(parent)
        top = ttk.Frame(self.frame)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(top, text="Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        search_entry = ttk.Entry(top, textvariable=self.search_var, width=28)
        search_entry.pack(side=tk.LEFT, padx=(6, 12))

        ttk.Button(top, text="Export", command=self._export).pack(side=tk.RIGHT, padx=(4, 0))
        self.count_var = tk.StringVar(value="0 records")
        ttk.Label(
            top, textvariable=self.count_var,
            foreground="#666",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(side=tk.RIGHT, padx=8)

        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        cols = spec["columns"]
        self.tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings", height=16, style="Large.Treeview",
        )
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        widths = spec.get("widths", {})
        numeric_cols = spec.get("numeric_cols", set())
        center_cols = spec.get("center_cols", set()) | numeric_cols
        for col in cols:
            self.tree.heading(
                col, text=col,
                command=lambda c=col: self._sort_by(c),
            )
            if col in center_cols:
                anchor = "center"
            else:
                anchor = "w"
            self.tree.column(col, width=widths.get(col, 100), anchor=anchor, stretch=True)

        if spec.get("action_label") and on_action:
            act = ttk.Frame(self.frame)
            act.pack(fill=tk.X, padx=8, pady=(0, 4))
            btn_specs = [{
                "text": spec["action_label"],
                "command": self._run_row_action,
                "bootstyle": (
                    "warning-outline"
                    if spec.get("key") in ("expired", "near_expiry")
                    else "primary-outline"
                ),
            }]
            key = spec.get("key", "")
            if key in ("expired", "near_expiry") and getattr(self, "_on_bulk_return", None):
                btn_specs.append({
                    "text": "Return by Purchase",
                    "command": self._on_bulk_return,
                    "bootstyle": "success-outline",
                })
            elif key in ("low_stock", "out_of_stock") and getattr(self, "_on_bulk_reorder", None):
                btn_specs.append({
                    "text": "Reorder by Supplier",
                    "command": self._on_bulk_reorder,
                    "bootstyle": "success-outline",
                })
            if getattr(self, "_on_dismiss", None) and key != "customer_due":
                btn_specs.append({
                    "text": "Remove from List",
                    "command": self._run_dismiss,
                    "bootstyle": "secondary-outline",
                })
            if key == "expired" and getattr(self, "_on_bulk_dismiss", None):
                btn_specs.append({
                    "text": "Remove All Expired",
                    "command": self._on_bulk_dismiss,
                    "bootstyle": "danger-outline",
                })
            elif key == "out_of_stock" and getattr(self, "_on_bulk_dismiss_oos", None):
                btn_specs.append({
                    "text": "Remove All Out of Stock",
                    "command": self._on_bulk_dismiss_oos,
                    "bootstyle": "danger-outline",
                })
            from core.scroll_manager import pack_centered_buttons
            pack_centered_buttons(act, btn_specs, pady=0)
            self.tree.bind("<Double-1>", lambda e: self._run_row_action())

    def _run_dismiss(self):
        if not self._on_dismiss:
            return
        sel = self.tree.selection()
        if not sel:
            showinfo(
                "Remove from List",
                "Select a row first, then click Remove from List.",
                parent=self.frame,
            )
            return
        values = self.tree.item(sel[0]).get("values") or ()
        self._on_dismiss(self.spec.get("key", ""), tuple(values))

    def _run_row_action(self):
        if not self._on_action:
            return
        sel = self.tree.selection()
        if not sel:
            showinfo(
                "Action",
                "Select a row first, then click the action button.",
                parent=self.frame,
            )
            return
        values = self.tree.item(sel[0]).get("values") or ()
        self._on_action(self.spec.get("key", ""), tuple(values))

    def set_rows(self, rows: List[Tuple[Any, ...]]):
        self._all_rows = list(rows)
        self._apply_filter()

    def _apply_filter(self):
        q = (self.search_var.get() or "").strip().lower()
        rows = self._all_rows
        if q:
            rows = [
                r for r in rows
                if any(q in str(v).lower() for v in r)
            ]
        if self._sort_col:
            rows = self._sorted(rows)
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in rows:
            self.tree.insert("", tk.END, values=tuple("" if v is None else v for v in row))
        self.count_var.set(f"{len(rows):,} record{'s' if len(rows) != 1 else ''}")

    def _sorted(self, rows: List[Tuple[Any, ...]]) -> List[Tuple[Any, ...]]:
        col = self._sort_col
        if not col:
            return rows
        idx = self.spec["columns"].index(col)
        numeric = col in self.spec.get("numeric_cols", set())

        def key_fn(row):
            val = row[idx] if idx < len(row) else ""
            if numeric:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0
            return str(val).lower()

        return sorted(rows, key=key_fn, reverse=self._sort_reverse)

    def _sort_by(self, col: str):
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = False
        self._apply_filter()

    def _export(self):
        rows = []
        for item in self.tree.get_children():
            rows.append(self.tree.item(item)["values"])
        if not rows:
            showinfo("Export", "No rows to export.", parent=self.frame)
            return
        self._on_export(self.spec["title"], list(self.spec["columns"]), rows, self.spec["export_name"])


class AlertMonitoringTab:
    TAB_NAME = "Alert & Monitoring"

    def __init__(self, notebook, conn, parent_widget=None, host=None):
        self.conn = conn
        self._parent = parent_widget
        self._sections: Dict[str, _AlertSection] = {}
        self._loaded = False
        self._loading = False

        outer = host if host is not None else ttk.Frame(notebook)
        self.outer = outer
        if host is None and notebook is not None:
            notebook.add(outer, text=self.TAB_NAME)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Label(
            header,
            text="Central dashboard for stock, expiry, and customer due alerts.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            foreground="#666",
        ).pack(side=tk.LEFT)
        ttk.Button(header, text="Refresh All", command=self.refresh).pack(side=tk.RIGHT, padx=(4, 0))

        def _bulk_reorder():
            app = self._resolve_app()
            if app and hasattr(app, "open_reorder"):
                app.open_reorder(bulk=True)
            else:
                from core.reorder_service import collect_reorder_candidates
                from core.themed_messagebox import showinfo
                if not collect_reorder_candidates(self.conn):
                    showinfo("Reorder", "No low or out-of-stock medicines to reorder.", parent=self.outer)

        def _bulk_return():
            app = self._resolve_app()
            if app and hasattr(app, "open_stock_disposal"):
                app.open_stock_disposal(bulk=True, include_expired=True, include_near_expiry=True)
            else:
                from core.stock_disposal_service import run_bulk_return_by_supplier
                run_bulk_return_by_supplier(self.conn, parent=self.outer)

        self._bulk_reorder = _bulk_reorder
        self._bulk_return = _bulk_return

        btn_bar = ttk.Frame(header)
        btn_bar.pack(side=tk.RIGHT)
        try:
            ttk.Button(btn_bar, text="Return by Purchase", command=_bulk_return,
                       bootstyle="warning-outline").pack(side=tk.RIGHT, padx=4)
            ttk.Button(btn_bar, text="Reorder by Supplier", command=_bulk_reorder,
                       bootstyle="success-outline").pack(side=tk.RIGHT, padx=4)
        except Exception:
            ttk.Button(btn_bar, text="Return by Purchase", command=_bulk_return).pack(side=tk.RIGHT, padx=4)
            ttk.Button(btn_bar, text="Reorder by Supplier", command=_bulk_reorder).pack(side=tk.RIGHT, padx=4)

        self._status_var = tk.StringVar(value="")
        ttk.Label(
            outer, textvariable=self._status_var,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            foreground="#888",
        ).pack(anchor=tk.W, padx=10)

        self._notebook = ttk.Notebook(outer)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        for spec in _SECTIONS:
            section = _AlertSection(
                self._notebook, spec, self._export_section, self._on_section_action,
                on_bulk_reorder=self._bulk_reorder,
                on_bulk_return=self._bulk_return,
                on_dismiss=self._dismiss_row,
                on_bulk_dismiss=self._dismiss_all_expired,
                on_bulk_dismiss_oos=self._dismiss_all_out_of_stock,
            )
            self._sections[spec["key"]] = section
            self._notebook.add(section.frame, text=f"{spec['label']} (0)")

        self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        outer.bind("<Visibility>", lambda e: self._ensure_loaded())
        parent_widget.after(300, self._ensure_loaded)

    def _resolve_app(self):
        try:
            if self._parent is not None:
                root = self._parent.winfo_toplevel()
                return getattr(root, "_main_app", None) or getattr(root, "_app_instance", None)
        except Exception:
            pass
        return None

    def _on_section_action(self, section_key: str, values: Tuple[Any, ...]):
        app = self._resolve_app()
        if app is None:
            showerror("Action", "Could not open page — restart the app.", parent=self.outer)
            return
        if section_key in ("low_stock", "out_of_stock"):
            prefill = self._prefill_reorder(section_key, values)
            if hasattr(app, "open_reorder"):
                app.open_reorder(prefill)
        elif section_key in ("expired", "near_expiry"):
            from core.stock_disposal_service import (
                build_bulk_return_by_purchase, collect_return_candidates,
            )
            name = str(values[0] or "").strip()
            batch = str(values[1] or "").strip() if len(values) > 1 else ""
            items = [
                i for i in collect_return_candidates(
                    self.conn, include_expired=True, include_near_expiry=True)
                if (i.get("medicine_name") or "").strip() == name
                and (not batch or (i.get("batch_no") or "").strip() == batch)
            ]
            data = build_bulk_return_by_purchase(self.conn, items=items)
            if hasattr(app, "open_returns") and (
                data.get("purchase_groups") or data.get("writeoff_lines")
            ):
                app.open_returns(kind="purchase", prefill=data)
            elif hasattr(app, "open_stock_disposal"):
                app.open_stock_disposal(self._prefill_disposal(section_key, values))

    def _prefill_reorder(self, section_key: str, values: Tuple[Any, ...]) -> dict:
        from core.reorder_service import current_stock_for_medicine, min_stock_level, suggest_order_quantity
        if section_key == "low_stock":
            name = str(values[0] or "")
            stock = float(values[1] or 0)
            pack = str(values[2] or "")
            med_type = "Others"
            return {
                "medicine_name": name,
                "pack_size": pack,
                "med_type": med_type,
                "current_stock": stock,
                "suggested_qty": suggest_order_quantity(
                    self.conn, name, med_type, stock, pack),
            }
        name = str(values[0] or "")
        pack = str(values[1] or "")
        rate = float(values[3] or 0)
        med_type = str(values[4] or "Others")
        stock = current_stock_for_medicine(self.conn, name, pack)
        return {
            "medicine_name": name,
            "pack_size": pack,
            "med_type": med_type,
            "current_stock": stock,
            "unit_price": rate,
            "min_stock": min_stock_level(self.conn, med_type),
            "suggested_qty": suggest_order_quantity(
                self.conn, name, med_type, stock, pack),
        }

    def _prefill_disposal(self, section_key: str, values: Tuple[Any, ...]) -> dict:
        if section_key == "expired":
            name, batch, expiry, qty = values[0], values[1], values[2], values[3]
        else:
            name, batch, expiry, qty = values[0], values[1], values[2], values[4]
        return {
            "from_alert": True,
            "medicine_name": str(name or ""),
            "batch_no": str(batch or ""),
            "expiry_date": str(expiry or ""),
            "available_qty": float(qty or 0),
        }

    def _medicine_ids_for_dismiss(self, section_key: str, values: Tuple[Any, ...]) -> list:
        cur = self.conn.cursor()
        name = str(values[0] or "").strip()
        if not name:
            return []
        if section_key == "low_stock":
            cur.execute(
                "SELECT id FROM medicines "
                "WHERE TRIM(name)=TRIM(?) AND COALESCE(is_hidden, 0) = 0",
                (name,),
            )
        elif section_key == "out_of_stock":
            pack = str(values[1] or "").strip()
            cur.execute(
                "SELECT id FROM medicines "
                "WHERE TRIM(name)=TRIM(?) AND TRIM(COALESCE(unit, ''))=TRIM(?) "
                "AND COALESCE(is_hidden, 0) = 0",
                (name, pack),
            )
        elif section_key in ("expired", "near_expiry"):
            batch = str(values[1] or "").strip()
            cur.execute(
                "SELECT id FROM medicines "
                "WHERE TRIM(name)=TRIM(?) AND TRIM(COALESCE(batch_no, ''))=TRIM(?) "
                "AND COALESCE(is_hidden, 0) = 0",
                (name, batch),
            )
        else:
            return []
        return [int(r[0]) for r in cur.fetchall()]

    def _dismiss_row(self, section_key: str, values: Tuple[Any, ...]) -> None:
        from core.medicine_visibility import (
            hide_medicines_by_name,
            hide_medicines_by_name_and_batch,
            hide_medicines_by_name_and_pack,
        )
        from core.page_refresh import refresh_open_pages
        from core.sync_coordinator import after_medicines_hidden

        sync_ids = self._medicine_ids_for_dismiss(section_key, values)

        if section_key == "low_stock":
            name = str(values[0] or "").strip()
            if not name:
                return
            if not askyesno(
                "Remove from List",
                f"Remove all batches of {name} from inventory lists and alerts?\n\n"
                "Sales and purchase history are kept.",
                parent=self.outer,
            ):
                return
            hide_medicines_by_name(self.conn, name)
        elif section_key == "out_of_stock":
            name = str(values[0] or "").strip()
            pack = str(values[1] or "").strip()
            if not name:
                return
            if not askyesno(
                "Remove from List",
                f"Remove {name} ({pack or 'no pack'}) from inventory lists and alerts?\n\n"
                "Sales and purchase history are kept.",
                parent=self.outer,
            ):
                return
            hide_medicines_by_name_and_pack(self.conn, name, pack)
        elif section_key in ("expired", "near_expiry"):
            name = str(values[0] or "").strip()
            batch = str(values[1] or "").strip()
            if not name:
                return
            if not askyesno(
                "Remove from List",
                f"Remove {name} batch {batch or '—'} from inventory lists and alerts?\n\n"
                "Sales and purchase history are kept.",
                parent=self.outer,
            ):
                return
            hide_medicines_by_name_and_batch(self.conn, name, batch)
        else:
            return

        after_medicines_hidden(self.conn, sync_ids)
        refresh_open_pages(self.outer, inventory=True, alert_monitoring=True, home=True)
        self.refresh()

    def _dismiss_all_expired(self) -> None:
        from core.medicine_visibility import hide_all_expired_medicines
        from core.page_refresh import refresh_open_pages
        from core.sync_coordinator import after_medicines_hidden
        from core.alert_thresholds import parse_expiry
        from datetime import date

        if not askyesno(
            "Remove All Expired",
            "Hide every expired medicine batch from inventory lists and alerts?\n\n"
            "This does not delete sales or purchase history.",
            parent=self.outer,
        ):
            return
        today = date.today()
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, COALESCE(expiry_date, '') FROM medicines "
            "WHERE COALESCE(is_hidden, 0) = 0 AND COALESCE(stock_qty, 0) > 0"
        )
        sync_ids = [
            int(med_id) for med_id, expiry_raw in cur.fetchall()
            if (expiry_dt := parse_expiry(expiry_raw)) and expiry_dt < today
        ]
        count = hide_all_expired_medicines(self.conn)
        after_medicines_hidden(self.conn, sync_ids)
        showinfo(
            "Done",
            f"Removed {count} expired batch(es) from lists.",
            parent=self.outer,
        )
        refresh_open_pages(self.outer, inventory=True, alert_monitoring=True, home=True)
        self.refresh()

    def _dismiss_all_out_of_stock(self) -> None:
        from core.medicine_visibility import hide_all_out_of_stock_medicines
        from core.page_refresh import refresh_open_pages
        from core.sync_coordinator import after_medicines_hidden

        if not askyesno(
            "Remove All Out of Stock",
            "Hide every out-of-stock medicine from inventory lists and alerts?\n\n"
            "This does not delete sales or purchase history.",
            parent=self.outer,
        ):
            return
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id FROM medicines "
            "WHERE COALESCE(stock_qty, 0) <= 0 AND COALESCE(is_hidden, 0) = 0"
        )
        sync_ids = [int(r[0]) for r in cur.fetchall()]
        count = hide_all_out_of_stock_medicines(self.conn)
        after_medicines_hidden(self.conn, sync_ids)
        showinfo(
            "Done",
            f"Removed {count} out-of-stock batch(es) from lists.",
            parent=self.outer,
        )
        refresh_open_pages(self.outer, inventory=True, alert_monitoring=True, home=True)
        self.refresh()

    def get_keyboard_bindings(self):
        from core.keyboard_registry import PageBindings
        return PageBindings(page_id="alert_monitoring", on_f5=self.refresh)

    def show_section(self, section_key: str) -> bool:
        """Select an alert notebook tab by key (voice / open_settings)."""
        key = (section_key or "").strip()
        if not key or key not in self._sections:
            return False
        try:
            self._notebook.select(self._sections[key].frame)
            self._ensure_loaded()
            return True
        except Exception:
            return False

    def _on_tab_changed(self, _event=None):
        self._ensure_loaded()

    def _ensure_loaded(self):
        if self._loaded or self._loading:
            return
        self.refresh()

    def refresh(self):
        if self._loading:
            return
        self._loading = True
        self._status_var.set("Loading alerts…")
        from core.background_workers import run_in_thread

        run_in_thread(
            self._fetch_data,
            name="AlertMonitoringLoad",
            root=self._parent or self.outer,
            on_success=self._apply_data,
            on_error=self._on_load_error,
        )

    def _fetch_data(self):
        from core.alert_monitoring_service import fetch_all_monitoring_sections
        return fetch_all_monitoring_sections(self.conn)

    def _apply_data(self, data: Dict[str, List[Tuple[Any, ...]]]):
        self._loading = False
        self._loaded = True
        total = 0
        for i, spec in enumerate(_SECTIONS):
            key = spec["key"]
            rows = data.get(key, [])
            total += len(rows)
            self._sections[key].set_rows(rows)
            self._notebook.tab(i, text=f"{spec['label']} ({len(rows)})")
        self._status_var.set(f"Last refreshed — {total:,} total alert records")

    def _on_load_error(self, exc: Exception):
        self._loading = False
        self._status_var.set(f"Load failed: {exc}")
        showerror("Alert & Monitoring", f"Could not load alerts:\n{exc}", parent=self.outer)

    def _export_section(self, title: str, headers: List[str], rows, default_name: str):
        from core.export_manager import export_data
        try:
            export_data(self.outer, title, headers, rows, default_name)
        except Exception as e:
            showerror("Export", f"Export failed:\n{e}", parent=self.outer)
