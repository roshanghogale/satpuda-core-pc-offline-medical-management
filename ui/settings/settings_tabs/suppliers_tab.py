"""
ui/settings/settings_tabs/suppliers_tab.py
───────────────────────────────────────────
Supplier list page — aligned with centralized accounting model.

Phase 2 compliance:
  - Displays suppliers.total_due / total_credit (single source of truth)
  - Status: Due / Credit / Cleared derived from those columns
  - No aggregation from purchases table
  - No last-bill snapshot usage
"""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
from core.font_config import *
from core.alert_colors import get_alert_color
from core.layout_config import SUPPLIERS_ROWS
from core.column_config import apply_column_visibility, all_column_names
from core.record_indicators import (
    extend_columns,
    indicator_column_widths,
    column_heading,
    prepare_tree_row,
    register_tree_tags,
    supplier_status,
)
from core.scroll_manager import make_scrollable, open_dialog
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno


class SuppliersTab:
    def __init__(self, parent, conn, embedded=False):
        self.conn = conn
        self.cursor = conn.cursor()
        if embedded:
            frame = ttk.Frame(parent)
            frame.pack(fill=tk.BOTH, expand=True)
        else:
            frame = make_scrollable(parent)
        self._build(frame)
        self.load()

    def _build(self, frame):
        # Summary bar at top
        sum_frame = ttk.LabelFrame(frame, text="Supplier Summary")
        sum_frame.pack(fill=tk.X, padx=10, pady=(10, 4))

        self.total_suppliers_var = tk.StringVar(value="0")
        self.total_due_var       = tk.StringVar(value="₹0.00")
        self.total_credit_var    = tk.StringVar(value="₹0.00")

        for col, (lbl, var, color) in enumerate([
            ("Total Suppliers:", self.total_suppliers_var, None),
            ("Total Due:",       self.total_due_var,       'danger'),
            ("Total Credit:",    self.total_credit_var,    'success'),
        ]):
            ttk.Label(sum_frame, text=lbl).grid(row=0, column=col * 2, padx=12, pady=6)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color:
                kw['foreground'] = get_alert_color(color)
            ttk.Label(sum_frame, textvariable=var, **kw).grid(
                row=0, column=col * 2 + 1, padx=12, pady=6)

        try:
            ttk.Button(sum_frame, text="↺ Recalculate All",
                       command=self._recalculate_all,
                       bootstyle="warning", width=18).grid(
                row=0, column=6, padx=12, pady=6)
        except Exception:
            ttk.Button(sum_frame, text="Recalculate All",
                       command=self._recalculate_all, width=18).grid(
                row=0, column=6, padx=12, pady=6)

        # Supplier list tree — Phase 2: includes Due / Credit / Status columns
        list_frame = ttk.LabelFrame(frame, text="Suppliers List")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._all_columns = tuple(all_column_names('suppliers'))
        self._tree_columns = extend_columns(self._all_columns)
        col_widths = {
            'Name': 180, 'Phone': 110, 'GSTIN': 140,
            'Address': 220, 'Total Due': 100, 'Credit': 90, 'Status': 80,
        }
        col_widths.update(indicator_column_widths())
        self.tree = ttk.Treeview(list_frame, columns=self._tree_columns, show='headings',
                                 height=SUPPLIERS_ROWS, style='Large.Treeview')
        for col in self._tree_columns:
            self.tree.heading(col, text=column_heading(col))
            self.tree.column(col, width=col_widths.get(col, 100))
        apply_column_visibility(self.tree, 'suppliers', self._tree_columns)

        register_tree_tags(self.tree)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        from core.tree_action_menu import setup_tree_actions
        self._action_menu = setup_tree_actions(
            list_frame,
            self.tree,
            [
                ("Edit Supplier", self.edit),
                ("Recalculate Balance", self._recalc_selected),
                "---",
                ("Delete Supplier", self.delete),
            ],
        )
        self._menu = self._action_menu.ctx_menu

    def _show_menu(self, event):
        if self.tree.selection():
            self._menu.post(event.x_root, event.y_root)

    def _tree_menu(self):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            bbox = self.tree.bbox(sel[0])
            if bbox:
                self._menu.post(
                    self.tree.winfo_rootx() + bbox[0],
                    self.tree.winfo_rooty() + bbox[1] + bbox[3])
        except Exception:
            pass

    def _fetch_online_suppliers(self):
        from core.online_catalog import suppliers

        rows = []
        for s in suppliers(force=True):
            try:
                sid = int(s.get("id") or s.get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            if sid <= 0:
                continue
            rows.append((
                sid,
                s.get("name") or "",
                s.get("phone") or "",
                s.get("gstin") or "",
                s.get("address") or "",
                float(s.get("total_due") or 0),
                float(s.get("total_credit") or 0),
            ))
        rows.sort(key=lambda r: str(r[1]).upper())
        return rows

    def _apply_supplier_rows(self, rows):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.suppliers_data = list(rows or [])
        total_due = 0.0
        total_credit = 0.0
        for row in self.suppliers_data:
            sid, name, phone, gstin, address, due, credit = row
            due = float(due)
            credit = float(credit)
            total_due += due
            total_credit += credit
            if due > 0:
                status = "Due"
            elif credit > 0:
                status = "Credit"
            else:
                status = "Cleared"
            vals = (
                name, phone or '', gstin, address,
                f"₹{due:.2f}" if due else '-',
                f"₹{credit:.2f}" if credit else '-',
                status,
            )
            values, tags = prepare_tree_row(
                vals, supplier_status(due, credit), badge_text=status.upper())
            self.tree.insert('', tk.END, iid=str(sid), values=values, tags=tags)
        self.total_suppliers_var.set(str(len(self.suppliers_data)))
        self.total_due_var.set(f"₹{total_due:.2f}")
        self.total_credit_var.set(f"₹{total_credit:.2f}")

    def load(self):
        """
        Phase 2: reads total_due / total_credit from suppliers table.
        No aggregation from purchases.
        Online: store catalog via online_catalog.suppliers.
        """
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.background_workers import run_in_thread
                self._load_gen = int(getattr(self, "_load_gen", 0) or 0) + 1
                gen = self._load_gen

                def _apply(rows):
                    if gen != getattr(self, "_load_gen", 0):
                        return
                    self._apply_supplier_rows(rows)

                def _err(exc):
                    if gen != getattr(self, "_load_gen", 0):
                        return
                    showerror("Error", f"Failed to load suppliers: {exc}")
                    self._apply_supplier_rows([])

                run_in_thread(
                    self._fetch_online_suppliers,
                    name="SuppliersLoad",
                    root=self.tree,
                    on_success=_apply,
                    on_error=_err,
                )
                return
        except Exception as e:
            showerror("Error", f"Failed to load suppliers: {e}")
            self._apply_supplier_rows([])
            return

        try:
            self.cursor.execute("""
                SELECT id, name, phone, COALESCE(gstin,''), COALESCE(address,''),
                       COALESCE(total_due,0), COALESCE(total_credit,0)
                FROM suppliers ORDER BY name
            """)
            self._apply_supplier_rows(self.cursor.fetchall())
        except Exception as e:
            showerror("Error", f"Failed to load suppliers: {e}")
            self._apply_supplier_rows([])

    def _recalc_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        supplier_id = int(sel[0])
        from core.purchase_service import recalculate_supplier_due
        recalculate_supplier_due(self.conn, supplier_id)
        try:
            from core.sync_coordinator import after_supplier_saved
            after_supplier_saved(self.conn, supplier_id)
        except Exception:
            pass
        self.load()

    def _recalculate_all(self):
        from core.purchase_service import recalculate_supplier_due
        from core.themed_messagebox import askyesno, showerror, showinfo

        if not askyesno(
            "Recalculate All",
            "Recalculate dues for all suppliers? This may take a moment.",
            parent=self.tree,
        ):
            return
        ids = [row[0] for row in self.suppliers_data]

        def _worker(put):
            for i, sid in enumerate(ids, 1):
                try:
                    recalculate_supplier_due(self.conn, sid)
                except Exception as e:
                    print(f"[RECALC] supplier {sid}: {e}")
                if i % 25 == 0 or i == len(ids):
                    put(f"Recalculated {i}/{len(ids)} suppliers…")
            return len(ids)

        def _done(count):
            self.load()
            showinfo(
                "Done",
                f"Recalculated dues for {count} supplier(s).",
                parent=self.tree,
            )

        from core.background_workers import run_with_progress

        run_with_progress(
            self.tree,
            "Recalculating Supplier Dues",
            _worker,
            on_complete=_done,
            on_error=lambda exc: showerror("Error", str(exc), parent=self.tree),
        )

    def edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        supplier_id = int(sel[0])
        values = self.tree.item(sel[0])['values']

        dlg = open_dialog(self.tree, "Edit Supplier", width=540, height=400, resizable=False)
        body = dlg.content
        body.grid_columnconfigure(1, weight=1)

        ttk.Label(body, text="Supplier Name:").grid(row=0, column=0, padx=12, pady=10, sticky=tk.W)
        name_e = ttk.Entry(body, width=36)
        name_e.grid(row=0, column=1, padx=12, pady=10, sticky=tk.EW)
        name_e.insert(0, values[0])

        ttk.Label(body, text="Phone:").grid(row=1, column=0, padx=12, pady=10, sticky=tk.W)
        phone_e = ttk.Entry(body, width=36)
        phone_e.grid(row=1, column=1, padx=12, pady=10, sticky=tk.EW)
        phone_e.insert(0, values[1])

        ttk.Label(body, text="GSTIN:").grid(row=2, column=0, padx=12, pady=10, sticky=tk.W)
        gstin_e = ttk.Entry(body, width=36)
        gstin_e.grid(row=2, column=1, padx=12, pady=10, sticky=tk.EW)
        gstin_e.insert(0, values[2])

        ttk.Label(body, text="Address:").grid(row=3, column=0, padx=12, pady=10, sticky=tk.W)
        addr_e = tk.Text(body, width=36, height=3)
        addr_e.grid(row=3, column=1, padx=12, pady=10, sticky=tk.EW)
        addr_e.insert(tk.END, values[3])

        def save():
            try:
                from core.sync_prefs import is_online_mode

                name = name_e.get().strip()
                phone = phone_e.get().strip()
                gstin = gstin_e.get().strip()
                address = addr_e.get(1.0, tk.END).strip()
                if is_online_mode():
                    from core.online_catalog import invalidate, suppliers
                    from core.server_crud import upsert_contact_online

                    # Only send a balance we actually found. Defaulting to 0.0
                    # meant that editing a supplier's phone number could push
                    # zeros over a real outstanding due and wipe it.
                    due = credit = None
                    for row in self.suppliers_data:
                        if int(row[0]) == supplier_id:
                            due = float(row[5] or 0)
                            credit = float(row[6] or 0)
                            break
                    else:
                        for s in suppliers():
                            try:
                                if int(s.get("id") or s.get("local_id") or 0) == supplier_id:
                                    due = float(s.get("total_due") or 0)
                                    credit = float(s.get("total_credit") or 0)
                                    break
                            except (TypeError, ValueError):
                                continue
                    payload = {
                        "id": supplier_id,
                        "local_id": supplier_id,
                        "name": name,
                        "phone": phone,
                        "gstin": gstin,
                        "address": address,
                    }
                    if due is not None:
                        payload["total_due"] = due
                        payload["total_credit"] = credit
                    upsert_contact_online("suppliers", payload)
                    invalidate("suppliers")
                else:
                    self.cursor.execute(
                        "UPDATE suppliers SET name=?,phone=?,gstin=?,address=? WHERE id=?",
                        (name, phone, gstin, address, supplier_id))
                    self.conn.commit()
                    from core.sync_coordinator import after_supplier_saved
                    after_supplier_saved(self.conn, supplier_id)
                showinfo("Success", "Supplier updated successfully!")
                dlg.destroy()
                self.load()
            except Exception as e:
                showerror("Error", f"Failed to update supplier: {e}")

        name_e.bind('<Return>',  lambda e: phone_e.focus())
        name_e.bind('<Down>',    lambda e: phone_e.focus())
        phone_e.bind('<Return>', lambda e: gstin_e.focus())
        phone_e.bind('<Down>',   lambda e: gstin_e.focus())
        phone_e.bind('<Up>',     lambda e: name_e.focus())
        gstin_e.bind('<Return>', lambda e: addr_e.focus())
        gstin_e.bind('<Down>',   lambda e: addr_e.focus())
        gstin_e.bind('<Up>',     lambda e: phone_e.focus())
        dlg.bind('<Escape>', lambda e: dlg.destroy())

        sb_btn = ttk.Button(dlg.footer, text="Save Changes", command=save)
        sb_btn.pack(side=tk.LEFT, padx=8)
        cb_btn = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
        cb_btn.pack(side=tk.LEFT, padx=8)
        sb_btn.bind('<Return>', lambda e: save())
        cb_btn.bind('<Return>', lambda e: dlg.destroy())
        name_e.focus()

    def delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        supplier_id = int(sel[0])
        name = self.tree.item(sel[0])['values'][0]
        if not askyesno("Confirm Delete", f"Delete supplier {name}?"):
            return
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_catalog import invalidate
                from core.server_crud import delete_contact_online
                from core import store_query_client as sq

                purchases = (sq.list_purchases(q=str(name), limit=50) or {}).get("rows") or []
                has_history = False
                for r in purchases:
                    if not isinstance(r, dict):
                        continue
                    sid = r.get("supplier_id")
                    try:
                        if sid is not None and int(sid) == supplier_id:
                            has_history = True
                            break
                    except (TypeError, ValueError):
                        pass
                    sn = str(r.get("supplier_name") or "").strip()
                    if sn and sn.upper() == str(name).strip().upper():
                        has_history = True
                        break
                if has_history:
                    showwarning(
                        "Cannot Delete",
                        "Supplier has purchase history and cannot be deleted.")
                    return
                delete_contact_online("suppliers", supplier_id)
                invalidate("suppliers")
            else:
                self.cursor.execute(
                    "SELECT COUNT(*) FROM purchases WHERE supplier_id=?", (supplier_id,))
                if self.cursor.fetchone()[0] > 0:
                    showwarning(
                        "Cannot Delete",
                        "Supplier has purchase history and cannot be deleted.")
                    return
                self.cursor.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
                self.conn.commit()
                from core.sync_coordinator import after_supplier_deleted
                after_supplier_deleted(self.conn, supplier_id)
            showinfo("Success", "Supplier deleted successfully!")
            self.load()
        except Exception as e:
            showerror("Error", f"Failed to delete supplier: {e}")
