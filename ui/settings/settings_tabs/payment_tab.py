import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import date
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable
from widgets.searchable_combo import SearchableCombo


class PaymentTab:
    def __init__(self, conn, notebook=None, parent=None):
        self.conn = conn
        self.cursor = conn.cursor()
        self._last_supplier_names: list[str] = []
        self._reload_busy = False
        self._history_load_gen = 0
        self._history_job = None
        self._due_job = None
        self._last_repair_mono = 0.0
        self._ensure_table()
        if parent is not None:
            self.outer = parent
            frame = make_scrollable(parent)
        else:
            self.outer = ttk.Frame(notebook)
            notebook.add(self.outer, text="💳 Payment")
            frame = make_scrollable(self.outer)
        self._build(frame, self.outer)

    def _ensure_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS supplier_payments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_no   TEXT UNIQUE,
                supplier_id  INTEGER,
                payment_date DATE,
                amount       REAL DEFAULT 0,
                mode         TEXT DEFAULT 'Cash',
                reference    TEXT,
                due_before   REAL DEFAULT 0,
                due_after    REAL DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
            )
        """)
        self.conn.commit()

    def _build(self, frame, outer):
        form = ttk.LabelFrame(frame, text="Record Supplier Payment")
        form.pack(fill=tk.X, padx=10, pady=(10, 6))

        ttk.Label(form, text="Supplier:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        self.pay_supplier = SearchableCombo(form, width=28)
        self.pay_supplier.grid(row=0, column=1, padx=8, pady=6, sticky=tk.W)

        self.pay_due_var = tk.StringVar(value="Outstanding Due: ₹0.00")
        ttk.Label(form, textvariable=self.pay_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('danger')).grid(
            row=0, column=2, columnspan=2, padx=12, pady=6, sticky=tk.W)

        ttk.Label(form, text="Amount Paid (₹):").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        self.pay_amount = ttk.Entry(form, width=16)
        self.pay_amount.grid(row=1, column=1, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Payment Mode:").grid(row=1, column=2, sticky=tk.W, padx=8, pady=6)
        self.pay_mode = SearchableCombo(form, values=['Cash','Online','Cheque','NEFT','RTGS','UPI'], width=14)
        self.pay_mode.set('')
        self.pay_mode.grid(row=1, column=3, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Payment Date:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
        self.pay_date = ttk.Entry(form, width=16)
        self.pay_date.insert(0, date.today().strftime('%Y-%m-%d'))
        self.pay_date.grid(row=2, column=1, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Reference / Note:").grid(row=2, column=2, sticky=tk.W, padx=8, pady=6)
        self.pay_note = ttk.Entry(form, width=28)
        self.pay_note.grid(row=2, column=3, padx=8, pady=6, sticky=tk.W)

        btn_row = ttk.Frame(form)
        btn_row.grid(row=3, column=0, columnspan=4, pady=8, padx=8, sticky=tk.W)
        try:
            ttk.Button(btn_row, text="✔ Save Payment  [F5]",
                       command=self._save, bootstyle="success", width=22).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="↺ Clear",
                       command=self._clear, bootstyle="secondary", width=12).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="🗑 Delete Selected",
                       command=self._delete, bootstyle="danger", width=18).pack(side=tk.LEFT, padx=6)
        except Exception:
            ttk.Button(btn_row, text="Save Payment", command=self._save, width=18).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Clear", command=self._clear).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Delete Selected", command=self._delete).pack(side=tk.LEFT, padx=6)

        # History
        hf = ttk.LabelFrame(frame, text="Payment History")
        hf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        hist_cols = ('Payment No','Date','Supplier','Amount','Mode','Reference','Due Before','Due After')
        self.hist_tree = ttk.Treeview(hf, columns=hist_cols, show='headings',
                                      height=12, style='Large.Treeview')
        hw = {'Payment No':110,'Date':100,'Supplier':160,'Amount':100,
              'Mode':90,'Reference':160,'Due Before':110,'Due After':110}
        for c in hist_cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=hw.get(c, 100))
        self.hist_tree.column('Amount', anchor='e')
        self.hist_tree.column('Due Before', anchor='e')
        self.hist_tree.column('Due After', anchor='e')
        sb = ttk.Scrollbar(hf, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        from core.tree_action_menu import setup_tree_actions
        setup_tree_actions(
            hf,
            self.hist_tree,
            [("Delete Selected Payment", self._delete)],
            on_delete=lambda e: self._delete(),
            escape_to=self.pay_supplier.entry,
        )

        summary = ttk.Frame(frame)
        summary.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.total_paid_var = tk.StringVar(value="Total Paid: ₹0.00")
        ttk.Label(summary, textvariable=self.total_paid_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('success')).pack(side=tk.LEFT, padx=15)

        # Bindings
        self.pay_supplier.entry.bind('<FocusIn>', lambda e: self._reload_suppliers(), add='+')
        self.pay_supplier.bind('<<ComboboxSelected>>', self._on_supplier_select)
        self.pay_supplier.next_focus_widget = lambda: self._on_supplier_select()
        self.pay_amount.bind('<FocusIn>', lambda e: self.pay_amount.select_range(0, tk.END), add='+')
        self.pay_amount.bind('<Return>', lambda e: self.pay_mode.focus())
        self.pay_date.bind('<FocusIn>', lambda e: self.pay_date.select_range(0, tk.END), add='+')
        self.pay_date.bind('<Return>', lambda e: self.pay_note.focus())
        self.pay_note.bind('<Return>', lambda e: self._save())
        nav = [self.pay_supplier.entry, self.pay_amount, self.pay_mode.entry, self.pay_date, self.pay_note]
        for i, w in enumerate(nav):
            w.bind('<Down>', lambda e, n=nav[(i+1)%len(nav)]: n.focus(), add='+')
            w.bind('<Up>',   lambda e, p=nav[(i-1)%len(nav)]: p.focus(), add='+')
        outer.bind('<F5>', lambda e: self._save(), add='+')

        self._prefetch_and_reload_suppliers()
        self._load_history()
        try:
            from core.store_live_refresh import subscribe as live_sub

            def _on_live(_evt):
                # Debounce — never run repair/API on the Tk main thread.
                try:
                    if self._history_job is not None:
                        self.outer.after_cancel(self._history_job)
                except Exception:
                    pass
                try:
                    self._history_job = self.outer.after(350, self._load_history)
                except Exception:
                    self._load_history()
                try:
                    if self._due_job is not None:
                        self.outer.after_cancel(self._due_job)
                except Exception:
                    pass
                try:
                    self._due_job = self.outer.after(400, self._refresh_due_label)
                except Exception:
                    pass

            self._live_token = live_sub(
                {"supplier_payments", "suppliers", "purchases"},
                _on_live,
            )
        except Exception:
            self._live_token = None

    def _apply_supplier_names(self, names):
        names = list(names or [])
        if names:
            self._last_supplier_names = names
        try:
            self.pay_supplier.configure(values=self._last_supplier_names or names)
        except Exception:
            pass

    def _prefetch_and_reload_suppliers(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import prefetch_hot
                prefetch_hot()
        except Exception:
            pass
        self._reload_suppliers()

    def _reload_suppliers(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                # Keep last successful names; never fall back to empty local SQLite.
                if self._last_supplier_names:
                    try:
                        self.pay_supplier.configure(values=self._last_supplier_names)
                    except Exception:
                        pass
                if self._reload_busy:
                    return
                self._reload_busy = True
                from core.background_workers import run_in_thread

                def _load():
                    from core.online_catalog import supplier_names
                    return list(supplier_names() or [])

                def _done(names):
                    self._reload_busy = False
                    self._apply_supplier_names(names)

                def _err(_exc):
                    self._reload_busy = False
                    if self._last_supplier_names:
                        try:
                            self.pay_supplier.configure(values=self._last_supplier_names)
                        except Exception:
                            pass

                run_in_thread(
                    _load,
                    name="SuppPaySuppliersReload",
                    root=self.outer,
                    on_success=_done,
                    on_error=_err,
                )
                return
        except Exception:
            try:
                from core.sync_prefs import is_online_mode as _online
                if _online():
                    if self._last_supplier_names:
                        self.pay_supplier.configure(values=self._last_supplier_names)
                    return
            except Exception:
                pass
        self.cursor.execute("SELECT name FROM suppliers ORDER BY name")
        self.pay_supplier.configure(values=[r[0] for r in self.cursor.fetchall()])

    def _on_supplier_select(self, event=None):
        name = self.pay_supplier.get().strip()
        if not name:
            return
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.background_workers import run_in_thread

                def _work():
                    return self._supplier_outstanding_due(name)

                def _ok(due):
                    try:
                        if self.pay_supplier.get().strip() != name:
                            return
                        if due is None:
                            return
                        due = float(due)
                        self.pay_due_var.set(f"Outstanding Due: ₹{due:.2f}")
                        self.pay_amount.delete(0, tk.END)
                        if due > 0:
                            self.pay_amount.insert(0, f"{due:.2f}")
                        self.pay_amount.focus()
                    except Exception:
                        pass

                run_in_thread(
                    _work,
                    name="PaySupplierSelectDue",
                    on_success=_ok,
                    root=self.outer,
                )
                return
        except Exception:
            pass
        due = self._supplier_outstanding_due(name)
        if due is None:
            return
        self.pay_due_var.set(f"Outstanding Due: ₹{due:.2f}")
        self.pay_amount.delete(0, tk.END)
        if due > 0:
            self.pay_amount.insert(0, f"{due:.2f}")
        self.pay_amount.focus()

    def _supplier_outstanding_due(self, name):
        name = (name or "").strip()
        if not name:
            return None
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import find_supplier_by_name, patch_supplier_cache
                from core.desktop_settings_service import online_supplier_remaining_due

                s = find_supplier_by_name(name) or {}
                try:
                    sid = int(s.get("id") or s.get("local_id") or 0)
                except (TypeError, ValueError):
                    sid = 0
                live = online_supplier_remaining_due(sid, name)
                if live is not None:
                    due = float(live)
                    if s:
                        merged = dict(s)
                        merged["total_due"] = due
                        patch_supplier_cache(merged)
                    return due
                return float(s.get("total_due") or 0)
        except Exception:
            pass
        self.cursor.execute("SELECT id FROM suppliers WHERE name=? LIMIT 1", (name,))
        row = self.cursor.fetchone()
        if not row:
            return None
        supplier_id = int(row[0])
        from core.purchase_service import get_supplier_due, recalculate_supplier_due
        recalculate_supplier_due(self.conn, supplier_id)
        due, _ = get_supplier_due(self.conn, name)
        return due

    def _refresh_due_label(self):
        self._due_job = None
        name = self.pay_supplier.get().strip()
        if not name:
            return
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.background_workers import run_in_thread

                def _work():
                    return self._supplier_outstanding_due(name)

                def _ok(due):
                    if due is None:
                        return
                    try:
                        if self.pay_supplier.get().strip() != name:
                            return
                        self.pay_due_var.set(f"Outstanding Due: ₹{float(due):.2f}")
                    except Exception:
                        pass

                run_in_thread(
                    _work,
                    name="PayDueRefresh",
                    on_success=_ok,
                    root=self.outer,
                )
                return
        except Exception:
            pass
        due = self._supplier_outstanding_due(name)
        if due is None:
            return
        self.pay_due_var.set(f"Outstanding Due: ₹{due:.2f}")

    def _paint_history_rows(self, rows, total: float):
        """Apply payment history rows on the UI thread (diffed when possible)."""
        from core.ui_tree_loader import sync_tree_by_iid, populate_tree_batched, save_tree_yview, restore_tree_yview

        painted = []
        for r in rows:
            pay_no = str(r.get("payment_no") or r.get("id") or "")
            if not pay_no:
                continue
            amt = float(r.get("amount") or 0)
            values = (
                pay_no,
                r.get("date") or "",
                r.get("party") or "",
                f"₹{amt:.2f}",
                r.get("mode") or "",
                r.get("reference") or "",
                f"₹{float(r.get('due_before') or 0):.2f}",
                f"₹{float(r.get('due_after') or 0):.2f}",
            )
            painted.append((pay_no, values, ()))

        mode = sync_tree_by_iid(self.hist_tree, painted)
        if mode == "rebuild":
            yview = save_tree_yview(self.hist_tree)
            kids = self.hist_tree.get_children()
            if kids:
                self.hist_tree.delete(*kids)

            def _insert(row):
                iid, values, _tags = row
                self.hist_tree.insert("", tk.END, iid=iid, values=values)

            populate_tree_batched(
                self.hist_tree,
                painted,
                _insert,
                self.outer,
                batch_size=80,
                on_done=lambda: restore_tree_yview(self.hist_tree, yview),
            )
        self.total_paid_var.set(f"Total Paid (all): ₹{total:.2f}")

    def _load_history(self):
        self._history_job = None
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.background_workers import run_in_thread
                import time as _time

                self._history_load_gen += 1
                gen = self._history_load_gen
                do_repair = False
                now = _time.monotonic()
                # Throttle expensive repair; payments list still refreshes every time.
                if now - float(self._last_repair_mono or 0) >= 30.0:
                    do_repair = True
                    self._last_repair_mono = now

                def _work():
                    from core.desktop_settings_service import get_payments, repair_supplier_dues_online
                    from core import sync_timing

                    with sync_timing.span("payment_tab.online_history"):
                        if do_repair:
                            with sync_timing.span("payment_tab.repair_dues"):
                                try:
                                    repair_supplier_dues_online()
                                except Exception:
                                    pass
                        with sync_timing.span("payment_tab.get_payments"):
                            data = get_payments(self.conn, "supplier") or {}
                    history = list(data.get("history") or [])
                    total = 0.0
                    rows = []
                    for r in history:
                        amt = float(r.get("amount") or 0)
                        total += amt
                        rows.append({
                            "payment_no": r.get("payment_no") or "",
                            "date": r.get("date") or "",
                            "party": r.get("party") or "",
                            "amount": amt,
                            "mode": r.get("mode") or "",
                            "reference": r.get("reference") or "",
                            "due_before": float(r.get("due_before") or 0),
                            "due_after": float(r.get("due_after") or 0),
                        })
                    return {"rows": rows, "total": total, "repaired": do_repair}

                def _ok(payload):
                    if gen != self._history_load_gen:
                        return
                    try:
                        self._paint_history_rows(payload.get("rows") or [], float(payload.get("total") or 0))
                    except Exception:
                        pass

                def _err(_exc):
                    if gen != self._history_load_gen:
                        return

                run_in_thread(
                    _work,
                    name="PayHistoryOnline",
                    on_success=_ok,
                    on_error=_err,
                    root=self.outer,
                )
                return
        except Exception:
            pass

        # Offline / SQLite path — local query is cheap; still avoid full wipe when possible.
        try:
            self.cursor.execute("""
                SELECT sp.payment_no, sp.payment_date, s.name,
                       sp.amount, sp.mode, COALESCE(sp.reference,''),
                       sp.due_before, sp.due_after
                FROM supplier_payments sp
                JOIN suppliers s ON sp.supplier_id=s.id
                ORDER BY sp.id DESC LIMIT 300
            """)
            rows = []
            total = 0.0
            for r in self.cursor.fetchall():
                amt = float(r[3] or 0)
                total += amt
                rows.append({
                    "payment_no": r[0],
                    "date": r[1],
                    "party": r[2],
                    "amount": amt,
                    "mode": r[4],
                    "reference": r[5],
                    "due_before": float(r[6] or 0),
                    "due_after": float(r[7] or 0),
                })
            self._paint_history_rows(rows, total)
        except Exception:
            for item in self.hist_tree.get_children():
                self.hist_tree.delete(item)
            self.total_paid_var.set("Total Paid (all): ₹0.00")

    def _save(self):
        # Online Hybrid A: allow enqueue during short drops (no ensure_can_mutate).
        name = self.pay_supplier.get().strip()
        if not name:
            showwarning("Missing", "Please select a supplier.")
            return
        try:
            amount = float(self.pay_amount.get() or 0)
        except ValueError:
            showerror("Invalid", "Enter a valid payment amount.")
            return
        if amount <= 0:
            showwarning("Invalid", "Amount must be greater than zero.")
            return
        mode = self.pay_mode.get().strip()
        if not mode:
            showwarning("Missing", "Please select a payment mode.")
            return
        pdate     = self.pay_date.get().strip() or date.today().strftime('%Y-%m-%d')
        reference = self.pay_note.get().strip()
        # Saved as typed; a date after today is only pointed out.
        try:
            from core.save_warnings import payment_warnings

            date_note = "".join(f"\n\nPlease check: {w}" for w in payment_warnings(pdate))
        except Exception:
            date_note = ""

        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_settings_service import save_payment
                data = save_payment(self.conn, {
                    "kind": "supplier",
                    "party": name,
                    "amount": amount,
                    "mode": mode,
                    "date": pdate,
                    "reference": reference,
                    "note": reference,
                })
                due = None
                if (data or {}).get("due_after") is not None:
                    due = float(data.get("due_after") or 0)
                else:
                    due = 0.0
                    for p in (data or {}).get("parties") or []:
                        if str(p.get("name") or "").strip().upper() == name.upper():
                            due = float(p.get("due") or 0)
                            break
                # Payment is already saved — never treat UI/dialog refresh errors as save failures.
                try:
                    self.pay_due_var.set(f"Outstanding Due: ₹{due:.2f}")
                    self.pay_amount.delete(0, tk.END)
                    self.pay_note.delete(0, tk.END)
                except Exception:
                    pass
                try:
                    self._load_history()
                except Exception:
                    pass
                try:
                    showinfo(
                        "Payment Saved",
                        f"Payment recorded.\n"
                        f"Amount: ₹{amount:.2f}  |  Mode: {mode}\n"
                        f"Outstanding Due: ₹{due:.2f}" + date_note,
                        parent=getattr(self, "outer", None),
                    )
                except Exception:
                    pass
                return
        except Exception as e:
            showerror("Error", f"Failed to save payment: {e}", parent=getattr(self, "outer", None))
            return

        self.cursor.execute("SELECT id FROM suppliers WHERE name=? LIMIT 1", (name,))
        sup_row = self.cursor.fetchone()
        if not sup_row:
            showerror("Not Found", f"Supplier '{name}' not found.")
            return
        supplier_id = sup_row[0]

        from core.purchase_service import get_supplier_due, recalculate_supplier_due
        due_before, _ = get_supplier_due(self.conn, name)
        net        = round(due_before - amount, 2)
        due_after  = max(0.0, net)

        # PHASE 1: collision-safe payment number using MAX(id)+1
        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM supplier_payments")
        pay_no = f"PAY{self.cursor.fetchone()[0]:04d}"

        try:
            self.cursor.execute("""
                INSERT INTO supplier_payments
                    (payment_no,supplier_id,payment_date,amount,mode,reference,due_before,due_after)
                VALUES (?,?,?,?,?,?,?,?)
            """, (pay_no, supplier_id, pdate, amount, mode, reference, due_before, due_after))
            payment_id = self.cursor.lastrowid

            # PHASE 3.3: do NOT mutate purchases.amount_paid.
            self.conn.commit()
            recalculate_supplier_due(self.conn, supplier_id)
            from core.sync_coordinator import after_supplier_payment_saved
            after_supplier_payment_saved(self.conn, int(payment_id), supplier_id)

            showinfo("Payment Saved",
                f"Payment {pay_no} recorded.\n"
                f"Amount: ₹{amount:.2f}  |  Mode: {mode}\n"
                f"Due Before: ₹{due_before:.2f}  →  Due After: ₹{due_after:.2f}\n\n"
                f"Payment applied to oldest purchase bills first." + date_note)
            self.pay_due_var.set(f"Outstanding Due: ₹{due_after:.2f}")
            self.pay_amount.delete(0, tk.END)
            self.pay_note.delete(0, tk.END)
            self._load_history()
        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to save payment: {e}")

    def _clear(self):
        self.pay_supplier.set('')
        self.pay_amount.delete(0, tk.END)
        self.pay_mode.set('')
        self.pay_note.delete(0, tk.END)
        self.pay_due_var.set("Outstanding Due: ₹0.00")
        self.pay_supplier.focus()

    def _delete(self):
        sel = self.hist_tree.selection()
        if not sel:
            showinfo("No Selection", "Select a payment row to delete.")
            return
        pay_no = self.hist_tree.item(sel[0])['values'][0]
        vals = self.hist_tree.item(sel[0])['values']
        party = vals[2] if len(vals) > 2 else ""
        if not askyesno("Confirm Delete",
                                   f"Delete payment {pay_no}?\nThis will reverse the due adjustment."):
            return
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_settings_service import delete_payment
                from core.online_catalog import invalidate
                delete_payment(self.conn, {
                    "kind": "supplier",
                    "payment_no": str(pay_no),
                    "party": party,
                })
                invalidate("suppliers")
                showinfo("Deleted", f"Payment {pay_no} deleted and due recalculated.")
                self._load_history()
                self._on_supplier_select()
                return
        except Exception as e:
            showerror("Error", f"Failed to delete payment: {e}")
            return
        try:
            self.cursor.execute(
                "SELECT id, supplier_id FROM supplier_payments WHERE payment_no=?", (pay_no,))
            row = self.cursor.fetchone()
            if not row:
                showerror("Not Found", "Payment record not found.")
                return
            payment_id, supplier_id = row[0], row[1]

            # PHASE 7.3: just delete the row, then recalculate.
            # No manual rebuild of purchases.amount_paid needed.
            self.cursor.execute("DELETE FROM supplier_payments WHERE payment_no=?", (pay_no,))
            self.conn.commit()

            from core.purchase_service import recalculate_supplier_due
            recalculate_supplier_due(self.conn, supplier_id)

            from core.sync_coordinator import after_supplier_payment_deleted
            after_supplier_payment_deleted(self.conn, int(payment_id), int(supplier_id))

            showinfo("Deleted", f"Payment {pay_no} deleted and due recalculated.")
            self._load_history()
            self._on_supplier_select()
        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to delete payment: {e}")
