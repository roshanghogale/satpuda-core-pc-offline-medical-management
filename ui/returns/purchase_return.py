import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    from tkinter import ttk
from datetime import datetime
import copy
import re
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable, open_dialog, scroll_to_widget, pack_centered_buttons
from core.calc_engine import calc_return_refund
from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
from widgets.searchable_combo import SearchableCombo


class PurchaseReturnPage:
    def __init__(self, parent, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.return_items = []
        self._purchase_id = None
        self._supplier_id = None
        self._orig_items_data = []
        self._bulk_sequence = None
        self._bulk_group = None
        self._doc_tabs = []
        self._active_tab_idx = 0
        self._tab_switching = False
        self._ensure_table()
        self._build_ui()
        self._setup_nav()
        self.parent.after(150, self.purchase_search.focus)

    def _default_return_tab_label(self, tab_number: int) -> str:
        return f"Return {int(tab_number)}"

    def _is_default_return_tab_label(self, label: str) -> bool:
        text = (label or "").strip()
        return text.startswith("Return ") and text[7:].isdigit()

    def _normalize_default_return_tab_labels(self):
        for idx, state in enumerate(self._doc_tabs, start=1):
            if self._is_default_return_tab_label(state.get("label", "")):
                label = self._default_return_tab_label(idx)
                state["label"] = label
                try:
                    self._tab_notebook.update_label(idx - 1, label)
                except Exception:
                    pass

    # ── DB ────────────────────────────────────────────────────────────────

    def _ensure_table(self):
        """
        Simple schema — qty stores strips (for tablet/bolus) or units (for others).
        No extra columns needed. Works even if all data is deleted.
        """
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_returns (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                return_no     TEXT UNIQUE,
                purchase_id   INTEGER,
                supplier_id   INTEGER,
                return_date   DATE,
                refund_amount REAL DEFAULT 0,
                discount      REAL DEFAULT 0,
                reason        TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (purchase_id) REFERENCES purchases(id),
                FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_return_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id   INTEGER,
                medicine_id INTEGER,
                qty         REAL DEFAULT 0,
                rate        REAL DEFAULT 0,
                amount      REAL DEFAULT 0,
                FOREIGN KEY (return_id)   REFERENCES purchase_returns(id),
                FOREIGN KEY (medicine_id) REFERENCES medicines(id)
            )
        """)
        self.conn.commit()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        inner = make_scrollable(self.parent)
        self._inner_frame = inner
        inner.configure(padding=(12, 12))

        from widgets.scrollable_tab_notebook import ScrollableTabNotebook, TAB_SHORTCUT_HINT
        self._tab_notebook = ScrollableTabNotebook(
            inner,
            on_add=self._new_return_tab,
            on_remove=self._on_return_tab_removed,
            on_select=self._switch_return_tab,
            add_label="+ Return",
            hint=TAB_SHORTCUT_HINT,
        )
        self._tab_notebook.pack(fill=tk.X, pady=(0, 8))
        self._tab_notebook._content.pack_forget()
        self._init_return_tabs()

        # ── Row 1: Search bar ─────────────────────────────────────────────
        search_frame = ttk.LabelFrame(inner, text="Step 1 — Find Original Purchase")
        search_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_frame, text="Purchase No / Supplier:").grid(
            row=0, column=0, padx=6, pady=6, sticky=tk.W)
        self.purchase_search = SearchableCombo(search_frame, width=32)
        self.purchase_search.grid(row=0, column=1, padx=6, pady=6)
        self.purchase_search.entry.bind('<FocusIn>', lambda e: self._reload_purchases(), add='+')
        self.purchase_search.bind('<<ComboboxSelected>>', self._on_purchase_select)
        self.purchase_search.next_focus_widget = lambda: self.load_btn.focus()

        try:
            self.load_btn = ttk.Button(search_frame, text="Load Purchase  [Enter]",
                                       command=self._on_purchase_select,
                                       bootstyle="info", width=20)
        except Exception:
            self.load_btn = ttk.Button(search_frame, text="Load Purchase  [Enter]",
                                       command=self._on_purchase_select, width=20)
        self.load_btn.grid(row=0, column=2, padx=8, pady=6)

        self.purchase_info_var = tk.StringVar(value="No purchase loaded — type purchase no or supplier name above")
        ttk.Label(search_frame, textvariable=self.purchase_info_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS),
                  foreground=get_alert_color('info')).grid(
            row=0, column=3, padx=12, pady=6, sticky=tk.W)

        # ── Row 2: Original purchase items ────────────────────────────────
        orig_frame = ttk.LabelFrame(
            inner, text="Step 2 — Select Item to Return  [F2 = focus list  |  Enter = add selected]")
        orig_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        orig_cols = ('Medicine', 'Batch', 'Purchased', 'Returnable', 'Rate', 'Type')
        self.orig_tree = ttk.Treeview(orig_frame, columns=orig_cols,
                                      show='headings', height=4, style='Large.Treeview')
        col_w = {'Medicine': 170, 'Batch': 80, 'Purchased': 110,
                 'Returnable': 120, 'Rate': 80, 'Type': 70}
        for c in orig_cols:
            self.orig_tree.heading(c, text=c)
            self.orig_tree.column(c, width=col_w.get(c, 90))
        sb1 = ttk.Scrollbar(orig_frame, orient=tk.VERTICAL, command=self.orig_tree.yview)
        self.orig_tree.configure(yscrollcommand=sb1.set)
        self.orig_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        from core.tree_action_menu import setup_tree_actions
        setup_tree_actions(
            orig_frame,
            self.orig_tree,
            [("Add to Return", self._add_return_item_dialog)],
            on_double=self._add_return_item_dialog,
            escape_to=self.purchase_search.entry,
        )

        # Add button below orig tree
        orig_btn_row = ttk.Frame(inner)
        orig_btn_row.pack(fill=tk.X, pady=(0, 6))
        try:
            self.add_btn = ttk.Button(orig_btn_row,
                                      text="➕ Add Selected to Return  [Enter on row]",
                                      command=self._add_return_item_dialog,
                                      bootstyle="success", width=38)
        except Exception:
            self.add_btn = ttk.Button(orig_btn_row,
                                      text="➕ Add Selected to Return  [Enter on row]",
                                      command=self._add_return_item_dialog, width=38)
        self.add_btn.pack(side=tk.LEFT, padx=4)

        # ── Row 3: Return items ───────────────────────────────────────────
        ret_frame = ttk.LabelFrame(
            inner, text="Step 3 — Items to Return  [F3 = focus list  |  Delete / Remove btn = remove]")
        ret_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        ret_cols = ('Medicine', 'Batch', 'Return Qty', 'Rate', 'Amount')
        self.ret_tree = ttk.Treeview(ret_frame, columns=ret_cols,
                                     show='headings', height=3, style='Large.Treeview')
        col_w2 = {'Medicine': 170, 'Batch': 80, 'Return Qty': 140, 'Rate': 80, 'Amount': 90}
        for c in ret_cols:
            self.ret_tree.heading(c, text=c)
            self.ret_tree.column(c, width=col_w2.get(c, 90))
        sb2 = ttk.Scrollbar(ret_frame, orient=tk.VERTICAL, command=self.ret_tree.yview)
        self.ret_tree.configure(yscrollcommand=sb2.set)
        self.ret_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        setup_tree_actions(
            ret_frame,
            self.ret_tree,
            [("Remove from Return", self._remove_return_item)],
            on_delete=lambda e: self._remove_return_item(),
            escape_to=self.add_btn,
        )

        # Remove button below ret tree
        ret_btn_row = ttk.Frame(inner)
        ret_btn_row.pack(fill=tk.X, pady=(0, 6))
        try:
            self.remove_btn = ttk.Button(ret_btn_row,
                                         text="➖ Remove Selected  [Delete key]",
                                         command=self._remove_return_item,
                                         bootstyle="warning", width=30)
        except Exception:
            self.remove_btn = ttk.Button(ret_btn_row,
                                         text="➖ Remove Selected  [Delete key]",
                                         command=self._remove_return_item, width=30)
        self.remove_btn.pack(side=tk.LEFT, padx=4)

        # ── Row 4: Summary ────────────────────────────────────────────────
        summary_frame = ttk.LabelFrame(inner, text="Step 4 — Confirm & Save")
        summary_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(summary_frame, text="Reason:").grid(
            row=0, column=0, padx=6, pady=6, sticky=tk.W)
        self.reason_entry = ttk.Entry(summary_frame, width=28)
        self.reason_entry.grid(row=0, column=1, padx=6, pady=6, sticky=tk.W)

        ttk.Label(summary_frame, text="Discount %:").grid(
            row=0, column=2, padx=6, pady=6, sticky=tk.W)
        self.discount_entry = ttk.Entry(summary_frame, width=8)
        self.discount_entry.insert(0, "0")
        self.discount_entry.grid(row=0, column=3, padx=6, pady=6)
        self.discount_entry.bind('<KeyRelease>', self._update_summary)

        ttk.Label(summary_frame, text="Credit to Supplier:").grid(
            row=0, column=4, padx=10, pady=6, sticky=tk.W)
        self.refund_var = tk.StringVar(value="0.00")
        ttk.Label(summary_frame, textvariable=self.refund_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('success')).grid(
            row=0, column=5, padx=6, pady=6)

        try:
            self.save_btn = ttk.Button(summary_frame, text="✔ Save Return  [F5]",
                                       command=self._save_return,
                                       bootstyle="danger", width=18)
            self.clear_btn = ttk.Button(summary_frame, text="✖ Clear  [F6]",
                                        command=self._clear,
                                        bootstyle="secondary", width=14)
        except Exception:
            self.save_btn = ttk.Button(summary_frame, text="✔ Save Return  [F5]",
                                       command=self._save_return, width=18)
            self.clear_btn = ttk.Button(summary_frame, text="✖ Clear  [F6]",
                                        command=self._clear, width=14)
        self.save_btn.grid(row=0, column=6, padx=10, pady=6)
        self.clear_btn.grid(row=0, column=7, padx=4, pady=6)

        # ── Row 5: History ────────────────────────────────────────────────
        hist_frame = ttk.LabelFrame(inner, text="Return History — saved purchase returns")
        hist_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        hist_cols = ('Return No', 'Date', 'Purchase No', 'Supplier', 'Credit', 'Reason')
        self.hist_tree = ttk.Treeview(hist_frame, columns=hist_cols,
                                      show='headings', height=6, style='Large.Treeview')
        hw = {'Return No': 110, 'Date': 100, 'Purchase No': 120,
              'Supplier': 160, 'Credit': 90, 'Reason': 200}
        for c in hist_cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=hw.get(c, 100))
        sb3 = ttk.Scrollbar(hist_frame, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb3.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)
        setup_tree_actions(
            hist_frame,
            self.hist_tree,
            [
                ("View Details", self._view_history_return),
                ("Save PDF", self._save_history_return_pdf),
                ("Load Purchase", self._load_history_purchase),
                ("Delete Return", self._delete_history_return),
            ],
            on_double=self._view_history_return,
            on_delete=lambda e: self._delete_history_return(),
        )

        self._reload_purchases()
        self._load_history()
        self.parent.after(300, self._scroll_to_history)

    def _empty_return_tab_state(self, label: str) -> dict:
        return {"label": label}

    def _init_return_tabs(self):
        self._doc_tabs = [self._empty_return_tab_state(self._default_return_tab_label(1))]
        self._active_tab_idx = 0
        frame = ttk.Frame(self._tab_notebook._content)
        self._tab_notebook.add(frame, self._default_return_tab_label(1))

    def _capture_return_tab_state(self) -> dict:
        label = self._doc_tabs[self._active_tab_idx].get("label", "Return")
        if self._purchase_id and self.purchase_search.get():
            label = (self.purchase_search.get().split(" — ")[0].strip() or label)[:22]
        return {
            "label": label,
            "purchase_id": self._purchase_id,
            "supplier_id": self._supplier_id,
            "purchase_search": self.purchase_search.get(),
            "purchase_info": self.purchase_info_var.get(),
            "reason": self.reason_entry.get(),
            "discount": self.discount_entry.get(),
            "refund": self.refund_var.get(),
            "return_items": copy.deepcopy(self.return_items),
            "orig_items_data": copy.deepcopy(self._orig_items_data),
        }

    def _restore_return_tab_state(self, state: dict):
        self._purchase_id = state.get("purchase_id")
        self._supplier_id = state.get("supplier_id")
        self.return_items = copy.deepcopy(state.get("return_items") or [])
        self._orig_items_data = copy.deepcopy(state.get("orig_items_data") or [])
        self.purchase_search.set(state.get("purchase_search") or "")
        self.purchase_info_var.set(
            state.get("purchase_info") or "No purchase loaded — type purchase no or supplier name above")
        self.reason_entry.delete(0, tk.END)
        self.reason_entry.insert(0, state.get("reason") or "")
        self.discount_entry.delete(0, tk.END)
        self.discount_entry.insert(0, state.get("discount") or "0")
        self.refund_var.set(state.get("refund") or "0.00")
        for t in (self.orig_tree, self.ret_tree):
            for item in t.get_children():
                t.delete(item)
        self._populate_orig_tree()
        self._refresh_ret_tree()
        self._update_summary()

    def _populate_orig_tree(self):
        for d in self._orig_items_data:
            is_tablet = is_strip_count_type(d.get("med_type") or "")
            unit_word = "strips" if is_tablet else "units"
            self.orig_tree.insert("", tk.END, values=(
                d["name"],
                d.get("batch", ""),
                f"{d.get('orig_qty', 0):.0f} {unit_word}",
                f"{d.get('remaining', 0):.0f} {unit_word}",
                f"{d.get('rate', 0):.2f}",
                d.get("med_type", ""),
            ))

    def _sync_return_tab_label(self):
        if not self._doc_tabs:
            return
        label = self.purchase_search.get().split(" — ")[0].strip()
        if not label:
            return
        label = label[:22]
        self._doc_tabs[self._active_tab_idx]["label"] = label
        self._tab_notebook.update_label(self._active_tab_idx, label)

    def _save_active_return_tab(self):
        if not self._doc_tabs:
            return
        self._doc_tabs[self._active_tab_idx] = self._capture_return_tab_state()

    def _switch_return_tab(self, idx: int):
        if self._tab_switching or idx == self._active_tab_idx:
            return
        self._tab_switching = True
        try:
            self._save_active_return_tab()
            self._active_tab_idx = idx
            self._restore_return_tab_state(self._doc_tabs[idx])
        finally:
            self._tab_switching = False

    def _new_return_tab(self):
        self._save_active_return_tab()
        n = len(self._doc_tabs) + 1
        label = self._default_return_tab_label(n)
        self._doc_tabs.append(self._empty_return_tab_state(label))
        frame = ttk.Frame(self._tab_notebook._content)
        idx = self._tab_notebook.add(frame, label)
        self._tab_switching = True
        try:
            self._active_tab_idx = idx
            self._clear(skip_advance=True)
        finally:
            self._tab_switching = False

    def _on_return_tab_removed(self, idx: int):
        if 0 <= idx < len(self._doc_tabs):
            del self._doc_tabs[idx]
        self._normalize_default_return_tab_labels()
        if self._active_tab_idx >= len(self._doc_tabs):
            self._active_tab_idx = max(0, len(self._doc_tabs) - 1)
        if self._doc_tabs:
            self._tab_switching = True
            try:
                self._restore_return_tab_state(self._doc_tabs[self._active_tab_idx])
            finally:
                self._tab_switching = False

    def _return_tab_has_data(self) -> bool:
        return bool(
            self._purchase_id or self.return_items
            or self.reason_entry.get().strip()
            or self.purchase_search.get().strip()
        )

    def _new_return_tab_shortcut(self, event=None):
        self._new_return_tab()
        return "break"

    def _close_return_tab_shortcut(self, event=None):
        if len(self._doc_tabs) <= 1:
            showinfo("Tabs", "At least one return tab must remain open.", parent=self.parent)
            return "break"
        if self._return_tab_has_data():
            if not askyesno(
                "Close Tab",
                "Close this return tab? Unsaved form data on this tab will be lost.",
                parent=self.parent,
            ):
                return "break"
        self._tab_notebook.remove_active()
        return "break"

    def _prev_return_tab_shortcut(self, event=None):
        if not self._doc_tabs:
            return "break"
        idx = (self._active_tab_idx - 1) % len(self._doc_tabs)
        self._tab_notebook.select(idx)
        return "break"

    def _next_return_tab_shortcut(self, event=None):
        if not self._doc_tabs:
            return "break"
        idx = (self._active_tab_idx + 1) % len(self._doc_tabs)
        self._tab_notebook.select(idx)
        return "break"

    def get_keyboard_bindings(self):
        from core.keyboard_registry import PageBindings
        return PageBindings(
            page_id="purchase_return",
            first_focus=lambda: self.purchase_search.entry.focus_set(),
            on_f5=self._save_return,
            on_f6=self._clear,
            on_ctrl_shift_n=self._new_return_tab_shortcut,
            on_ctrl_shift_w=self._close_return_tab_shortcut,
            on_ctrl_prior=self._prev_return_tab_shortcut,
            on_ctrl_next=self._next_return_tab_shortcut,
            f2_target=lambda: self._focus_tree(self.orig_tree),
            f3_target=lambda: self._focus_tree(self.ret_tree),
        )

    # ── Keyboard navigation ───────────────────────────────────────────────

    def _setup_nav(self):
        from core.keyboard_registry import KeyboardRegistry
        bindings = self.get_keyboard_bindings()
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

        # Linear nav: search → load → add → remove → reason → discount → save → clear
        nav = [
            self.purchase_search.entry,
            self.load_btn,
            self.add_btn,
            self.remove_btn,
            self.reason_entry,
            self.discount_entry,
            self.save_btn,
            self.clear_btn,
        ]
        n = len(nav)

        def _next(i):
            def h(e):
                nav[(i + 1) % n].focus()
                return 'break'
            return h

        def _prev(i):
            def h(e):
                nav[(i - 1) % n].focus()
                return 'break'
            return h

        for i, w in enumerate(nav):
            w.bind('<Down>',  _next(i), add='+')
            w.bind('<Up>',    _prev(i), add='+')
            w.bind('<Tab>',   _next(i), add='+')

        # Enter on buttons
        self.load_btn.bind('<Return>',   lambda e: self._on_purchase_select())
        self.add_btn.bind('<Return>',    lambda e: self._add_return_item_dialog())
        self.remove_btn.bind('<Return>', lambda e: self._remove_return_item())
        self.save_btn.bind('<Return>',   lambda e: self._save_return())
        self.clear_btn.bind('<Return>',  lambda e: self._clear())

        # Enter on entries
        self.reason_entry.bind('<Return>', lambda e: self.discount_entry.focus())
        self.discount_entry.bind('<Return>', lambda e: self.save_btn.focus())

        # Escape from trees
        self.orig_tree.bind('<Escape>', lambda e: self.purchase_search.focus())
        self.ret_tree.bind('<Escape>',  lambda e: self.reason_entry.focus())

        # Tab from trees → action buttons
        self.orig_tree.bind('<Tab>', lambda e: (self.add_btn.focus(), 'break'))
        self.ret_tree.bind('<Tab>',  lambda e: (self.remove_btn.focus(), 'break'))

        # FocusIn: select all
        for w in (self.reason_entry, self.discount_entry):
            w.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END), add='+')

    def _focus_tree(self, tree):
        items = tree.get_children()
        if not items:
            return
        sel = tree.selection()
        target = sel[0] if sel else items[0]
        tree.selection_set(target)
        tree.focus(target)
        tree.focus()
        tree.see(target)

    # ── Core helpers ──────────────────────────────────────────────────────

    def _scroll_to_history(self):
        try:
            scroll_to_widget(self._inner_frame, self.hist_tree)
        except Exception:
            pass

    def _selected_history_id(self):
        sel = self.hist_tree.selection()
        if not sel:
            showwarning("Select", "Select a saved return from the history list.", parent=self.parent)
            return None
        try:
            return int(sel[0])
        except ValueError:
            return None

    def _fetch_return_details(self, return_id: int):
        self.cursor.execute(
            """
            SELECT pr.return_no, pr.return_date, COALESCE(p.bill_number, p.purchase_no),
                   s.name, pr.refund_amount, COALESCE(pr.reason, ''), pr.purchase_id
            FROM purchase_returns pr
            JOIN purchases p ON pr.purchase_id = p.id
            JOIN suppliers s ON pr.supplier_id = s.id
            WHERE pr.id = ?
            """,
            (int(return_id),),
        )
        header = self.cursor.fetchone()
        if not header:
            return None, []
        self.cursor.execute(
            """
            SELECT m.name, COALESCE(m.batch_no, ''), pri.qty, pri.rate, pri.amount
            FROM purchase_return_items pri
            JOIN medicines m ON pri.medicine_id = m.id
            WHERE pri.return_id = ?
            ORDER BY pri.id
            """,
            (int(return_id),),
        )
        return header, self.cursor.fetchall()

    def _view_history_return(self):
        rid = self._selected_history_id()
        if not rid:
            return
        header, lines = self._fetch_return_details(rid)
        if not header:
            showwarning("Not Found", "Return record not found.", parent=self.parent)
            return
        dlg = open_dialog(
            self.parent,
            f"Return {header[0]}",
            width=520,
            height=420,
            resizable=True,
        )
        body = dlg.content
        ttk.Label(
            body,
            text=(
                f"Date: {header[1]}  |  Purchase: {header[2]}  |  Supplier: {header[3]}\n"
                f"Credit: ₹{float(header[4] or 0):.2f}  |  Reason: {header[5] or '—'}"
            ),
            wraplength=480,
            font=(FONT_FAMILY, FONT_SIZE_LABELS),
        ).pack(anchor=tk.W, padx=12, pady=(12, 8))
        cols = ("Medicine", "Batch", "Qty", "Rate", "Amount")
        tree = ttk.Treeview(body, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (220, 80, 70, 70, 80)):
            tree.heading(c, text=c)
            tree.column(c, width=w)
        tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        for name, batch, qty, rate, amount in lines:
            tree.insert("", tk.END, values=(
                name, batch, f"{float(qty):.2f}",
                f"{float(rate):.2f}", f"{float(amount):.2f}",
            ))
        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.RIGHT, padx=6)
        dlg.wait_window()

    def _load_history_purchase(self):
        rid = self._selected_history_id()
        if not rid:
            return
        header, lines = self._fetch_return_details(rid)
        if not header:
            showwarning("Not Found", "Return record not found.", parent=self.parent)
            return
        purchase_id = header[6]
        self._clear(skip_advance=True)
        self._load_purchase_by_id(int(purchase_id))
        self.reason_entry.delete(0, tk.END)
        self.reason_entry.insert(0, header[5] or "")
        self.refund_var.set(f"{float(header[4] or 0):.2f}")
        self.return_items.clear()
        for name, batch, qty, rate, amount in lines:
            med_id = None
            for d in self._orig_items_data:
                if d["name"] == name and (not batch or d.get("batch") == batch):
                    med_id = d["med_id"]
                    break
            if med_id is None:
                continue
            is_tablet = any(
                d.get("med_type") and is_strip_count_type(d["med_type"])
                for d in self._orig_items_data if d["med_id"] == med_id
            )
            tps = next((d.get("tps") or 1 for d in self._orig_items_data if d["med_id"] == med_id), 1)
            qty_f = float(qty)
            stock_deduction = qty_f * tps if is_tablet else qty_f
            self.return_items.append({
                "med_id": med_id,
                "name": name,
                "batch": batch or "",
                "qty": qty_f,
                "tps": tps,
                "is_tablet": is_tablet,
                "stock_deduction": stock_deduction,
                "rate": float(rate),
                "amount": float(amount),
            })
        self._refresh_ret_tree()
        self._update_summary()
        showinfo(
            "Loaded",
            f"Purchase {header[2]} loaded with return {header[0]} items for review.\n"
            "Saved returns cannot be changed — use this to verify what was returned.",
            parent=self.parent,
        )

    def _finish_after_save(self):
        self._load_history()
        if len(self._doc_tabs) > 1:
            self._tab_notebook.remove_active()
        else:
            self._clear(skip_advance=True)
        self.parent.after(150, self._scroll_to_history)

    def _focus_purchase_search_at_top(self):
        """After a dialog closes: scroll page to top and focus purchase/supplier search."""
        try:
            self.purchase_search.hide_list()
        except Exception:
            pass
        canvas = getattr(self._inner_frame, '_canvas', None)
        if canvas is not None:
            try:
                canvas.update_idletasks()
                canvas.yview_moveto(0)
            except Exception:
                pass
        try:
            self.purchase_search.focus(open_dropdown=False)
            scroll_to_widget(self._inner_frame, self.purchase_search.entry)
        except Exception:
            pass

    def _reload_purchases(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import search_purchases_for_return
                res = search_purchases_for_return(self.conn, "")
                labels = [
                    f"{p.get('bill_label')} — {p.get('supplier')}"
                    for p in (res.get("purchases") or [])
                ]
                self.purchase_search.configure(values=labels)
                try:
                    self.purchase_search.update_list()
                except Exception:
                    pass
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT COALESCE(p.bill_number, p.purchase_no) || ' — ' || s.name
            FROM purchases p JOIN suppliers s ON p.supplier_id = s.id
            ORDER BY p.id DESC LIMIT 300
        """)
        self.purchase_search.configure(values=[r[0] for r in self.cursor.fetchall()])
        try:
            self.purchase_search.update_list()
        except Exception:
            pass

    def _on_purchase_select(self, event=None):
        val = self.purchase_search.get().strip()
        if not val:
            return
        bill_no = val.split(' — ')[0].strip()
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import search_purchases_for_return
                res = search_purchases_for_return(self.conn, bill_no)
                purchases = res.get("purchases") or []
                match = None
                for p in purchases:
                    label = str(p.get("bill_label") or "")
                    if label == bill_no or label.startswith(bill_no):
                        match = p
                        break
                if match is None and purchases:
                    match = purchases[0]
                if not match:
                    showwarning(
                        "Not Found", f"Purchase '{bill_no}' not found.",
                        parent=self.parent,
                        focus_after=self._focus_purchase_search_at_top,
                    )
                    return
                self._load_purchase_by_id(int(match["purchase_id"]))
                self._sync_return_tab_label()
                self.parent.after(100, lambda: self._focus_tree(self.orig_tree))
                return
        except Exception as exc:
            showwarning("Online", str(exc) or "Purchase search failed.", parent=self.parent)
            return

        self.cursor.execute("""
            SELECT p.id, COALESCE(p.bill_number, p.purchase_no),
                   p.purchase_date, s.name, s.id
            FROM purchases p JOIN suppliers s ON p.supplier_id = s.id
            WHERE p.bill_number = ? OR p.purchase_no = ?
            ORDER BY p.id DESC LIMIT 1
        """, (bill_no, bill_no))
        row = self.cursor.fetchone()
        if not row:
            showwarning(
                "Not Found", f"Purchase '{bill_no}' not found.",
                parent=self.parent,
                focus_after=self._focus_purchase_search_at_top,
            )
            return
        self._purchase_id = row[0]
        self._supplier_id = row[4]
        self._set_purchase_info_line(row)
        self._load_orig_items()
        self._sync_return_tab_label()
        # Auto-focus orig_tree after loading
        self.parent.after(100, lambda: self._focus_tree(self.orig_tree))

    def _set_purchase_info_from_payload(self, res: dict):
        due_bits = (
            f"  |  Bill: ₹{float(res.get('bill_total') or 0):.2f}"
            f"  |  Paid: ₹{float(res.get('bill_paid') or 0):.2f}"
            f"  |  Bill due: ₹{float(res.get('bill_due') or 0):.2f}"
            f"  |  Supplier due: ₹{float(res.get('previous_due') or 0):.2f}"
        )
        if float(res.get("previous_credit") or 0):
            due_bits += f"  |  Credit: ₹{float(res.get('previous_credit') or 0):.2f}"
        self.purchase_info_var.set(
            f"Purchase: {res.get('bill_label')}  |  Date: {res.get('purchase_date')}  "
            f"|  Supplier: {res.get('supplier')}{due_bits}"
        )

    def _apply_loaded_purchase_items(self, items):
        for item in self.orig_tree.get_children():
            self.orig_tree.delete(item)
        self._orig_items_data = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            med_type = str(it.get("type") or "")
            is_tablet = bool(it.get("is_tablet")) if "is_tablet" in it else is_strip_count_type(med_type)
            tps = int(it.get("tablets_per_stripe") or 1) or 1
            orig_qty = float(it.get("orig_qty") or 0)
            remaining = float(
                it.get("remaining_qty")
                if it.get("remaining_qty") is not None
                else it.get("remaining")
                or 0
            )
            unit_word = "strips" if is_tablet else "units"
            self._orig_items_data.append({
                'name': str(it.get("name") or ""),
                'batch': str(it.get("batch") or ""),
                'orig_qty': orig_qty,
                'rate': float(it.get("rate") or 0),
                'med_type': med_type,
                'med_id': int(it.get("medicine_id") or 0),
                'tps': tps,
                'remaining': remaining,
            })
            self.orig_tree.insert('', tk.END, values=(
                str(it.get("name") or ""),
                str(it.get("batch") or ""),
                f"{orig_qty:.0f} {unit_word}",
                f"{remaining:.0f} {unit_word}",
                f"{float(it.get('rate') or 0):.2f}",
                med_type,
            ))

    def _set_purchase_info_line(self, row):
        """row: id, bill_label, date, supplier_name, supplier_id"""
        try:
            from core.purchase_service import get_supplier_due
            supp_due, supp_credit = get_supplier_due(self.conn, str(row[3] or ""))
        except Exception:
            supp_due, supp_credit = 0.0, 0.0
        self.cursor.execute(
            "SELECT COALESCE(final_amount, total_amount, 0), "
            "COALESCE(amount_paid_at_entry, amount_paid, 0), "
            "COALESCE(due_amount, due, 0) FROM purchases WHERE id=?",
            (row[0],),
        )
        pay = self.cursor.fetchone() or (0, 0, 0)
        due_bits = (
            f"  |  Bill: ₹{float(pay[0]):.2f}"
            f"  |  Paid: ₹{float(pay[1]):.2f}"
            f"  |  Bill due: ₹{float(pay[2]):.2f}"
            f"  |  Supplier due: ₹{float(supp_due):.2f}"
        )
        if supp_credit:
            due_bits += f"  |  Credit: ₹{float(supp_credit):.2f}"
        self.purchase_info_var.set(
            f"Purchase: {row[1]}  |  Date: {row[2]}  |  Supplier: {row[3]}{due_bits}"
        )

    @staticmethod
    def _get_tps(unit_str, med_type):
        """
        Return tablets-per-strip (int >= 1) from medicines.unit.
        medicines.unit is set by purchase.py as plain int string e.g. '10'.
        For non-tablet types always returns 1.
        """
        if not is_strip_count_type(med_type):
            return 1
        return parse_tablets_per_stripe(unit_str)

    def _already_returned(self, purchase_id, medicine_id):
        """
        Sum of qty (strips/units) already returned for this exact
        purchase_id + medicine_id combination across all past returns.
        """
        self.cursor.execute("""
            SELECT COALESCE(SUM(pri.qty), 0)
            FROM purchase_return_items pri
            JOIN purchase_returns pr ON pri.return_id = pr.id
            WHERE pr.purchase_id = ?
              AND pri.medicine_id = ?
        """, (purchase_id, medicine_id))
        row = self.cursor.fetchone()
        return float(row[0]) if row else 0.0

    def _load_orig_items(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode() and self._purchase_id:
                from core.desktop_returns_service import load_purchase_for_return
                res = load_purchase_for_return(self.conn, int(self._purchase_id))
                if res.get("ok"):
                    self._apply_loaded_purchase_items(res.get("items") or [])
                    return
        except Exception:
            pass
        for item in self.orig_tree.get_children():
            self.orig_tree.delete(item)
        self._orig_items_data = []

        self.cursor.execute("""
            SELECT m.name, pi.batch_no, pi.qty, pi.rate,
                   COALESCE(pi.type, ''), pi.medicine_id,
                   COALESCE(m.unit, '1')
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            WHERE pi.purchase_id = ?
        """, (self._purchase_id,))

        for row in self.cursor.fetchall():
            med_name, batch, orig_qty, rate, med_type, med_id, unit = row
            orig_qty  = float(orig_qty)
            tps       = self._get_tps(unit, med_type)
            is_tablet = is_strip_count_type(med_type)

            # How many strips/units already returned for THIS purchase + medicine
            already   = self._already_returned(self._purchase_id, med_id)
            remaining = max(0.0, orig_qty - already)

            unit_word = "strips" if is_tablet else "units"

            self._orig_items_data.append({
                'name':      med_name,
                'batch':     batch or '',
                'orig_qty':  orig_qty,   # strips (tablet) or units (others)
                'rate':      float(rate),
                'med_type':  med_type,
                'med_id':    med_id,
                'tps':       tps,        # tablets per strip; 1 for non-tablet
                'remaining': remaining,  # strips/units still returnable
            })

            self.orig_tree.insert('', tk.END, values=(
                med_name,
                batch or '',
                f"{orig_qty:.0f} {unit_word}",
                f"{remaining:.0f} {unit_word}",
                f"{rate:.2f}",
                med_type,
            ))

    def _add_return_item_dialog(self, event=None):
        sel = self.orig_tree.selection()
        if not sel:
            showinfo("No Selection",
                                "Select a medicine row first (use ↑↓ arrow keys).")
            return
        idx = self.orig_tree.index(sel[0])
        d   = self._orig_items_data[idx]

        is_tablet  = is_strip_count_type(d['med_type'])
        unit_label = "strips" if is_tablet else "units"
        remaining  = d['remaining']
        tps        = d['tps']

        if remaining <= 0:
            showwarning(
                "Fully Returned",
                f"All {d['orig_qty']:.0f} {unit_label} of {d['name']} "
                f"have already been returned.")
            return

        # Block duplicate in current session
        for item in self.return_items:
            if item['med_id'] == d['med_id']:
                showwarning(
                    "Already Added",
                    f"{d['name']} is already in the return list.\n"
                    f"Remove it first to change the quantity.")
                return

        dlg = open_dialog(self.parent, f"Return — {d['name']}",
                          width=360, height=175, resizable=False)
        body = dlg.content

        # Show clear info to user
        if is_tablet:
            info = f"Returnable: {remaining:.0f} strips  (×{tps} = {int(remaining * tps)} tablets in stock)"
        else:
            info = f"Returnable: {remaining:.0f} {unit_label}"

        ttk.Label(body, text=info,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(pady=(12, 4))

        qty_entry = ttk.Entry(body, width=14)
        qty_entry.pack(pady=4)
        qty_entry.insert(0, str(int(remaining)))
        qty_entry.select_range(0, tk.END)
        qty_entry.focus()

        def _confirm():
            try:
                qty = float(qty_entry.get())
            except ValueError:
                showerror("Invalid",
                                     f"Enter a valid number of {unit_label}.",
                                     parent=dlg)
                return

            # Hard cap: cannot exceed remaining strips/units
            if qty <= 0 or qty > remaining:
                already_done = d['orig_qty'] - remaining
                showerror(
                    "Invalid",
                    f"Must be 1 – {remaining:.0f} {unit_label}.\n"
                    f"Purchased: {d['orig_qty']:.0f}  |  Already returned: {already_done:.0f}",
                    parent=dlg)
                return

            # stock_deduction:
            #   tablet/bolus → strips × tps  (removes tablets from stock_qty)
            #   others       → qty           (removes units from stock_qty)
            stock_deduction = qty * tps if is_tablet else qty

            self.return_items.append({
                'med_id':          d['med_id'],
                'name':            d['name'],
                'batch':           d['batch'],
                'qty':             qty,             # strips or units — saved to DB
                'tps':             tps,
                'is_tablet':       is_tablet,
                'stock_deduction': stock_deduction, # tablets or units — used for stock update
                'rate':            d['rate'],
                'amount':          round(qty * d['rate'], 2),
            })
            self._refresh_ret_tree()
            self._update_summary()
            dlg.destroy()
            # Auto-focus ret_tree after adding
            self.parent.after(80, lambda: self._focus_tree(self.ret_tree))

        qty_entry.bind('<Return>', lambda e: _confirm())
        dlg.bind('<Escape>', lambda e: dlg.destroy())
        ok_btn = ttk.Button(dlg.footer, text="Add", command=_confirm)
        ok_btn.pack(side=tk.LEFT, padx=6)
        ca_btn = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
        ca_btn.pack(side=tk.LEFT, padx=6)
        ok_btn.bind('<Return>', lambda e: _confirm())
        ca_btn.bind('<Return>', lambda e: dlg.destroy())
        ok_btn.bind('<Tab>', lambda e: (ca_btn.focus(), 'break'))
        ca_btn.bind('<Tab>', lambda e: (ok_btn.focus(), 'break'))

    def _remove_return_item(self):
        sel = self.ret_tree.selection()
        if not sel:
            return
        idx = self.ret_tree.index(sel[0])
        del self.return_items[idx]
        self._refresh_ret_tree()
        self._update_summary()

    def _refresh_ret_tree(self):
        for item in self.ret_tree.get_children():
            self.ret_tree.delete(item)
        for item in self.return_items:
            if item['is_tablet']:
                qty_label = (f"{item['qty']:.0f} strips "
                             f"× {item['tps']} = {item['stock_deduction']:.0f} tablets")
            else:
                qty_label = f"{item['qty']:.0f} units"
            self.ret_tree.insert('', tk.END, values=(
                item['name'],
                item['batch'],
                qty_label,
                f"{item['rate']:.2f}",
                f"{item['amount']:.2f}",
            ))

    def _update_summary(self, event=None):
        try:
            disc = float(self.discount_entry.get() or 0)
        except ValueError:
            disc = 0
        result = calc_return_refund(self.return_items, disc)
        self.refund_var.set(f"{result['refund_amount']:.2f}")

    def _save_return(self):
        # Online Hybrid A: allow enqueue during short drops (no ensure_can_mutate).
        if not self._purchase_id:
            showwarning(
                "No Purchase", "Please load a purchase first.",
                parent=self.parent,
                focus_after=self._focus_purchase_search_at_top,
            )
            return
        if not self.return_items:
            showwarning("No Items", "Please add items to return.")
            return

        try:
            disc = float(self.discount_entry.get() or 0)
        except ValueError:
            disc = 0

        result  = calc_return_refund(self.return_items, disc)
        refund  = result['refund_amount']
        reason  = self.reason_entry.get().strip()

        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import save_purchase_return
                body = {
                    "purchase_id": int(self._purchase_id),
                    "supplier_id": int(self._supplier_id or 0),
                    "supplier_name": "",
                    "purchase_no": "",
                    "discount": disc,
                    "reason": reason,
                    "items": [
                        {
                            "medicine_id": int(it["med_id"]),
                            "qty": float(it["qty"]),
                            "rate": float(it.get("rate") or 0),
                            "type": "",
                            "unit": str(it.get("tps") or 1),
                        }
                        for it in self.return_items
                    ],
                }
                info = self.purchase_info_var.get() or ""
                if "Supplier:" in info:
                    try:
                        body["supplier_name"] = info.split("Supplier:")[-1].split("|")[0].strip()
                    except Exception:
                        pass
                if "Purchase:" in info:
                    try:
                        body["purchase_no"] = info.split("Purchase:")[-1].split("|")[0].strip()
                    except Exception:
                        pass
                res = save_purchase_return(self.conn, body)
                if not res.get("ok"):
                    showerror("Error", res.get("error") or "Failed to save return.", parent=self.parent)
                    return
                return_no = res.get("return_no") or ""
                showinfo(
                    "Success",
                    f"Return {return_no} saved.\n"
                    f"Credit to supplier: ₹{float(res.get('refund_amount') or refund):.2f}\n"
                    f"Stock reduced for {len(self.return_items)} item(s).",
                    parent=self.parent,
                )
                self._finish_after_save()
                self._advance_bulk_sequence()
                return
        except Exception as e:
            showerror("Error", f"Failed to save return: {e}", parent=self.parent)
            return

        # PHASE 1: collision-safe return number
        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM purchase_returns")
        return_no = f"PR{self.cursor.fetchone()[0]}"

        try:
            self.cursor.execute("""
                INSERT INTO purchase_returns
                    (return_no, purchase_id, supplier_id, return_date,
                     refund_amount, discount, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (return_no, self._purchase_id, self._supplier_id,
                  datetime.now().date(), refund, disc, reason))
            return_id = self.cursor.lastrowid

            for item in self.return_items:
                # Record how much stock the line actually moved, so deleting
                # this return hands back the same amount instead of the raw
                # strip count.
                self.cursor.execute("""
                    INSERT INTO purchase_return_items
                        (return_id, medicine_id, qty, rate, amount, stock_units)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (return_id, item['med_id'],
                      item['qty'], item['rate'], item['amount'],
                      item['stock_deduction']))
                # stock_deduction = strips×tps for tablets, units for others.
                # No MAX(0, ...): sending 10 back while the row shows 3 must
                # leave -7, or the difference is silently swallowed.
                self.cursor.execute("""
                    UPDATE medicines SET stock_qty = stock_qty - ? WHERE id = ?
                """, (item['stock_deduction'], item['med_id']))

            from core.medicine_visibility import hide_medicines_after_return
            hide_medicines_after_return(
                self.conn,
                [item['med_id'] for item in self.return_items],
                reason=reason,
            )

            # PHASE 3.4 / 6.2: do NOT patch purchases.total_due directly.
            # Supplier balance is maintained exclusively by recalculate_supplier_due().
            self.conn.commit()

            from core.purchase_service import recalculate_supplier_due
            recalculate_supplier_due(self.conn, self._supplier_id)
            try:
                from core.sync_coordinator import after_purchase_return_saved
                after_purchase_return_saved(self.conn, int(return_id), self._supplier_id)
            except Exception:
                pass

            showinfo(
                "Success",
                f"Return {return_no} saved.\n"
                f"Credit to supplier: ₹{refund:.2f}\n"
                f"Stock reduced for {len(self.return_items)} item(s).",
                parent=self.parent,
            )
            try:
                from core.document_output import offer_purchase_return_document
                offer_purchase_return_document(self.parent, self.conn, int(return_id))
            except Exception as exc:
                showerror("PDF saved", f"Could not save PDF:\n{exc}", parent=self.parent)
            self._finish_after_save()
            self._advance_bulk_sequence()

        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to save return: {e}")

    def _load_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                data = sq.list_purchase_returns(limit=200) or {}
                for r in data.get("rows") or []:
                    if not isinstance(r, dict):
                        continue
                    rid = r.get("id") or r.get("local_id") or ""
                    self.hist_tree.insert(
                        '', tk.END, iid=str(rid),
                        values=(
                            r.get("return_no") or "",
                            str(r.get("return_date") or "")[:10],
                            r.get("purchase_no") or "",
                            r.get("supplier_name") or "",
                            f"₹{float(r.get('refund_amount') or 0):.2f}",
                            r.get("reason") or "",
                        ),
                    )
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT pr.id, pr.return_no, pr.return_date,
                   COALESCE(p.bill_number, p.purchase_no),
                   s.name, pr.refund_amount, COALESCE(pr.reason, '')
            FROM purchase_returns pr
            JOIN purchases p ON pr.purchase_id = p.id
            JOIN suppliers s ON pr.supplier_id = s.id
            WHERE COALESCE(pr.deleted, 0) = 0
            ORDER BY pr.id DESC LIMIT 200
        """)
        for r in self.cursor.fetchall():
            self.hist_tree.insert('', tk.END, iid=str(r[0]),
                                  values=(r[1], r[2], r[3], r[4],
                                          f"₹{r[5]:.2f}", r[6]))

    def _save_history_return_pdf(self):
        sel = self.hist_tree.selection()
        if not sel:
            showwarning("Save PDF", "Select a return in history first.", parent=self.parent)
            return
        return_id = int(sel[0])
        try:
            from core.document_output import offer_purchase_return_document
            offer_purchase_return_document(self.parent, self.conn, return_id)
        except Exception as exc:
            showerror("Save PDF", f"Could not save PDF:\n{exc}", parent=self.parent)

    def _delete_history_return(self):
        """Delete a saved purchase return and push soft-delete + related sync."""
        sel = self.hist_tree.selection()
        if not sel:
            showerror("Delete Return", "Select a return in history first.")
            return
        return_id = int(sel[0])
        vals = self.hist_tree.item(sel[0], 'values')
        return_no = vals[0] if vals else str(return_id)
        if not askyesno(
            "Confirm Delete",
            f"Delete purchase return {return_no}?\n"
            "Stock will be increased again and the return will sync as deleted.",
        ):
            return
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_mutation_queue import enqueue

                enqueue(
                    collection="purchase_returns",
                    op="delete",
                    payload={"id": int(return_id)},
                    local_id=int(return_id),
                )
                showinfo("Success", "Return delete queued — will sync.")
                self._load_history()
                return
        except Exception as exc:
            showerror("Error", f"Failed to delete return: {exc}")
            return
        try:
            self.cursor.execute(
                "SELECT supplier_id FROM purchase_returns WHERE id=?",
                (return_id,),
            )
            row = self.cursor.fetchone()
            supplier_id = int(row[0]) if row and row[0] else None
            self.cursor.execute(
                """
                SELECT pri.medicine_id, pri.qty, pri.stock_units,
                       COALESCE(m.type,''), COALESCE(m.unit,'')
                FROM purchase_return_items pri
                LEFT JOIN medicines m ON m.id = pri.medicine_id
                WHERE pri.return_id=?
                """,
                (return_id,),
            )
            lines = self.cursor.fetchall()
            medicine_ids = [int(r[0]) for r in lines if r[0]]
            # Purchase return reduced stock on save — restore the SAME amount on
            # delete. The line is in strips for tablet medicines while stock is
            # kept in tablets, so handing back the raw strip count left the shelf
            # short by (strip size - 1) times the quantity.
            from core.desktop_returns_service import purchase_return_stock_units

            for med_id, qty, stock_units, m_type, m_unit in lines:
                units = purchase_return_stock_units(
                    {"qty": qty, "stock_units": stock_units},
                    medicine={"type": m_type, "unit": m_unit},
                )
                self.cursor.execute(
                    "UPDATE medicines SET stock_qty = stock_qty + ? WHERE id=?",
                    (float(units or 0), med_id),
                )
            self.cursor.execute(
                """
                UPDATE purchase_returns
                SET deleted=1,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    version=COALESCE(version,1)+1,
                    sync_status='pending'
                WHERE id=?
                """,
                (return_id,),
            )
            self.conn.commit()
            if supplier_id:
                try:
                    from core.purchase_service import recalculate_supplier_due
                    recalculate_supplier_due(self.conn, supplier_id)
                except Exception:
                    pass
            from core.sync_coordinator import after_purchase_return_deleted
            after_purchase_return_deleted(
                self.conn, return_id, supplier_id, medicine_ids,
            )
            showinfo("Success", f"Return {return_no} deleted.")
            self._load_history()
        except Exception as exc:
            self.conn.rollback()
            showerror("Error", f"Failed to delete return: {exc}")

    # ── Bulk return sequence (grouped by purchase) ───────────────────────

    def set_bulk_sequence(self, sequence, group: dict):
        self._bulk_sequence = sequence
        self._bulk_group = group
        self.parent.after(80, lambda: self.apply_bulk_return_prefill(group))

    def apply_bulk_return_prefill(self, group: dict):
        purchase_id = int(group["purchase_id"])
        self._clear(skip_advance=True)
        self._load_purchase_by_id(purchase_id)
        reason = group.get("reason") or "Near expiry / Expired stock"
        self.reason_entry.delete(0, tk.END)
        self.reason_entry.insert(0, reason)
        for line in group.get("lines", []):
            self._add_return_item_direct(
                int(line["medicine_id"]),
                float(line.get("quantity") or 0),
                line.get("batch_no", ""),
            )
        if self.return_items:
            self._refresh_ret_tree()
            self._update_summary()

    def _load_purchase_by_id(self, purchase_id: int):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import load_purchase_for_return
                res = load_purchase_for_return(self.conn, int(purchase_id))
                if not res.get("ok"):
                    showwarning(
                        "Not Found",
                        res.get("error") or f"Purchase #{purchase_id} not found.",
                        parent=self.parent,
                    )
                    return
                self._purchase_id = int(res["purchase_id"])
                self._supplier_id = int(res.get("supplier_id") or 0)
                label = f"{res.get('bill_label')} — {res.get('supplier')}"
                self.purchase_search.set(label)
                self._set_purchase_info_from_payload(res)
                self._apply_loaded_purchase_items(res.get("items") or [])
                return
        except Exception as exc:
            showwarning("Online", str(exc) or "Purchase load failed.", parent=self.parent)
            return

        self.cursor.execute("""
            SELECT p.id, COALESCE(p.bill_number, p.purchase_no),
                   p.purchase_date, s.name, s.id
            FROM purchases p JOIN suppliers s ON p.supplier_id = s.id
            WHERE p.id = ?
        """, (int(purchase_id),))
        row = self.cursor.fetchone()
        if not row:
            showwarning("Not Found", f"Purchase #{purchase_id} not found.", parent=self.parent)
            return
        self._purchase_id = row[0]
        self._supplier_id = row[4]
        label = f"{row[1]} — {row[3]}"
        self.purchase_search.set(label)
        self._set_purchase_info_line(row)
        self._load_orig_items()

    def _add_return_item_direct(self, med_id: int, qty: float, batch: str = ""):
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
            is_tablet = bool(d.get("med_type") and is_strip_count_type(d["med_type"]))
            tps = d.get("tps") or 1
            stock_deduction = use_qty * tps if is_tablet else use_qty
            self.return_items.append({
                "med_id": med_id,
                "name": d["name"],
                "batch": d.get("batch", ""),
                "qty": use_qty,
                "tps": tps,
                "is_tablet": is_tablet,
                "stock_deduction": stock_deduction,
                "rate": d["rate"],
                "amount": round(use_qty * d["rate"], 2),
            })
            return

    def _advance_bulk_sequence(self):
        seq = getattr(self, "_bulk_sequence", None)
        if seq:
            self._bulk_sequence = None
            self._bulk_group = None
            seq.advance()

    def _clear(self, skip_advance=False):
        self._purchase_id = None
        self._supplier_id = None
        self._orig_items_data = []
        self.return_items.clear()
        self.purchase_search.set('')
        self.purchase_info_var.set("No purchase loaded")
        self.reason_entry.delete(0, tk.END)
        self.discount_entry.delete(0, tk.END)
        self.discount_entry.insert(0, "0")
        self.refund_var.set("0.00")
        for t in (self.orig_tree, self.ret_tree):
            for item in t.get_children():
                t.delete(item)
        advancing = not skip_advance and bool(getattr(self, "_bulk_sequence", None))
        self.purchase_search.focus()
        if advancing:
            self._advance_bulk_sequence()


class BulkPurchaseReturnPage:
    """One scrollable tab per purchase bill; write-off tab at end if needed."""

    def __init__(self, parent, conn, prefill: dict):
        from widgets.scrollable_tab_notebook import ScrollableTabNotebook
        from core.stock_disposal_service import submit_writeoff

        self.parent = parent
        self.conn = conn
        self.cursor = conn.cursor()
        self._prefill = prefill or {}

        shell = ttk.Frame(parent)
        shell.pack(fill=tk.BOTH, expand=True)
        self._inner_frame = make_scrollable(shell)
        root = ttk.Frame(self._inner_frame)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            root,
            text="One tab per purchase — expired and near-expiry items pre-loaded to return.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground="#666",
        ).pack(anchor=tk.W, padx=8, pady=6)

        from widgets.scrollable_tab_notebook import ScrollableTabNotebook, TAB_SHORTCUT_HINT

        self._tab_notebook = ScrollableTabNotebook(root, hint=TAB_SHORTCUT_HINT)
        self._tab_notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        for group in self._prefill.get("purchase_groups") or []:
            frame = ttk.Frame(self._tab_notebook._content)
            self._build_purchase_tab(frame, group)
            label = (group.get("bill_number") or ("P%s" % group["purchase_id"]))[:22]
            self._tab_notebook.add(frame, label)

        writeoff = self._prefill.get("writeoff_lines") or []
        if writeoff:
            frame = ttk.Frame(self._tab_notebook._content)
            self._build_writeoff_tab(frame, writeoff, submit_writeoff)
            self._tab_notebook.add(frame, "Write-off")

        self._register_keyboard()

    def get_keyboard_bindings(self):
        from core.keyboard_registry import PageBindings
        return PageBindings(
            page_id="purchase_return_bulk",
            on_ctrl_shift_w=self._bulk_tab_close_shortcut,
            on_ctrl_prior=self._bulk_tab_prev_shortcut,
            on_ctrl_next=self._bulk_tab_next_shortcut,
        )

    def _register_keyboard(self):
        from core.keyboard_registry import KeyboardRegistry
        bindings = self.get_keyboard_bindings()
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

    def _bulk_tab_close_shortcut(self, event=None):
        nb = self._tab_notebook
        if len(nb._tabs) <= 1:
            showinfo("Tabs", "At least one tab must remain open.", parent=self.parent)
            return "break"
        if nb._tabs[nb._active].get("label") == "Write-off":
            showinfo("Tabs", "Write-off tab cannot be closed.", parent=self.parent)
            return "break"
        nb.remove_active()
        return "break"

    def _bulk_tab_prev_shortcut(self, event=None):
        if self._tab_notebook._tabs:
            self._tab_notebook.prev_tab()
        return "break"

    def _bulk_tab_next_shortcut(self, event=None):
        if self._tab_notebook._tabs:
            self._tab_notebook.next_tab()
        return "break"

    def _build_purchase_tab(self, parent, group):
        purchase_id = int(group["purchase_id"])
        return_items = []
        supplier_id = [None]
        orig_data = []

        hdr = ttk.LabelFrame(parent, text="Purchase")
        hdr.pack(fill=tk.X, padx=6, pady=6)
        bill = group.get("bill_number") or ("#%s" % purchase_id)
        ttk.Label(hdr, text="%s | %s" % (bill, group.get("supplier_name") or ""),
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT)).pack(anchor=tk.W, padx=8, pady=4)

        cols = ("Medicine", "Batch", "Qty", "Rate", "Amount")
        lf = ttk.LabelFrame(parent, text="Items to return")
        lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        tree = ttk.Treeview(lf, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (200, 80, 120, 70, 80)):
            tree.heading(c, text=c)
            tree.column(c, width=w)
        tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        reason_var = tk.StringVar(value=group.get("reason") or "Near expiry / Expired")
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="Reason:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=reason_var, width=40).pack(side=tk.LEFT, padx=6)

        def _already_ret(pid, mid):
            self.cursor.execute(
                "SELECT COALESCE(SUM(pri.qty),0) FROM purchase_return_items pri "
                "JOIN purchase_returns pr ON pri.return_id=pr.id "
                "WHERE pr.purchase_id=? AND pri.medicine_id=?", (pid, mid))
            r = self.cursor.fetchone()
            return float(r[0] or 0)

        self.cursor.execute(
            "SELECT p.id, s.id FROM purchases p JOIN suppliers s ON p.supplier_id=s.id WHERE p.id=?",
            (purchase_id,))
        prow = self.cursor.fetchone()
        if prow:
            supplier_id[0] = int(prow[1])
        self.cursor.execute(
            "SELECT m.name, pi.batch_no, pi.qty, pi.rate, COALESCE(pi.type,''), "
            "pi.medicine_id, COALESCE(m.unit,'1') FROM purchase_items pi "
            "JOIN medicines m ON pi.medicine_id=m.id WHERE pi.purchase_id=?",
            (purchase_id,))
        for med_name, batch, oq, rate, med_type, med_id, unit in self.cursor.fetchall():
            tps = self._get_tps(unit, med_type)
            is_tab = is_strip_count_type(med_type)
            rem = max(0.0, float(oq) - _already_ret(purchase_id, med_id))
            orig_data.append({
                "name": med_name, "batch": batch or "", "rate": float(rate),
                "med_id": med_id, "tps": tps, "is_tablet": is_tab, "remaining": rem,
            })

        def _add_line(mid, qty, batch=""):
            batch = (batch or "").strip()
            for d in orig_data:
                if int(d["med_id"]) != int(mid):
                    continue
                if batch and d["batch"] != batch:
                    continue
                if any(x["med_id"] == mid for x in return_items):
                    return
                if d["remaining"] <= 0:
                    return
                uq = min(float(qty), d["remaining"]) if qty else d["remaining"]
                sd = uq * d["tps"] if d["is_tablet"] else uq
                return_items.append({
                    "med_id": mid, "name": d["name"], "batch": d["batch"],
                    "qty": uq, "tps": d["tps"], "is_tablet": d["is_tablet"],
                    "stock_deduction": sd, "rate": d["rate"],
                    "amount": round(uq * d["rate"], 2),
                })
                return

        def _fill_tree():
            for iid in tree.get_children():
                tree.delete(iid)
            for item in return_items:
                ql = "%.0f strips" % item["qty"] if not item["is_tablet"] else (
                    "%.0f x %d" % (item["qty"], item["tps"]))
                tree.insert("", tk.END, values=(
                    item["name"], item["batch"], ql,
                    "%.2f" % item["rate"], "%.2f" % item["amount"]))

        for line in group.get("lines", []):
            _add_line(int(line["medicine_id"]), float(line.get("quantity") or 0),
                      line.get("batch_no", ""))
        _fill_tree()

        def _save():
            if not return_items:
                showwarning("No items", "Nothing to return.", parent=parent)
                return
            if not askyesno("Confirm", "Save return for %s?" % bill, parent=parent):
                return
            refund = calc_return_refund(return_items, 0)["refund_amount"]
            self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM purchase_returns")
            rno = "PR%s" % self.cursor.fetchone()[0]
            try:
                self.cursor.execute(
                    "INSERT INTO purchase_returns (return_no,purchase_id,supplier_id,return_date,"
                    "refund_amount,discount,reason) VALUES (?,?,?,?,?,0,?)",
                    (rno, purchase_id, supplier_id[0], datetime.now().date(),
                     refund, reason_var.get().strip()))
                rid = self.cursor.lastrowid
                for item in return_items:
                    self.cursor.execute(
                        "INSERT INTO purchase_return_items (return_id,medicine_id,qty,rate,amount) "
                        "VALUES (?,?,?,?,?)",
                        (rid, item["med_id"], item["qty"], item["rate"], item["amount"]))
                    self.cursor.execute(
                        "UPDATE medicines SET stock_qty=MAX(0,stock_qty-?) WHERE id=?",
                        (item["stock_deduction"], item["med_id"]))
                from core.medicine_visibility import hide_medicines_after_return
                hide_medicines_after_return(
                    self.conn,
                    [item["med_id"] for item in return_items],
                    reason=reason_var.get().strip(),
                )
                self.conn.commit()
                from core.purchase_service import recalculate_supplier_due
                if supplier_id[0]:
                    recalculate_supplier_due(self.conn, supplier_id[0])
                showinfo("Saved", "Return %s saved." % rno, parent=parent)
                try:
                    from core.document_output import offer_purchase_return_document
                    offer_purchase_return_document(parent, self.conn, int(rid))
                except Exception as exc:
                    showerror("PDF saved", f"Could not save PDF:\n{exc}", parent=parent)
                return_items.clear()
                _fill_tree()
                if len(self._tab_notebook._tabs) > 1:
                    self._tab_notebook.remove_active()
            except Exception as exc:
                self.conn.rollback()
                showerror("Failed", str(exc), parent=parent)

        pack_centered_buttons(parent, [
            {"text": "Save this purchase return", "command": _save, "bootstyle": "danger"},
        ], pady=8)

    def _get_tps(self, unit_str, med_type):
        if not is_strip_count_type(med_type):
            return 1
        return parse_tablets_per_stripe(unit_str)

    def _build_writeoff_tab(self, parent, lines, submit_writeoff):
        reason_var = tk.StringVar(value="No purchase record")
        ttk.Label(parent, text="Medicines without a purchase bill — write-off.",
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground="#666").pack(
            anchor=tk.W, padx=8, pady=6)
        cols = ("Medicine", "Batch", "Qty", "Tag")
        lf = ttk.LabelFrame(parent, text="Write-off items")
        lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        tree = ttk.Treeview(lf, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (220, 90, 70, 160)):
            tree.heading(c, text=c)
            tree.column(c, width=w)
        tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        store = list(lines)
        for line in store:
            tree.insert("", tk.END, values=(
                line.get("medicine_name"), line.get("batch_no"),
                line.get("quantity"), line.get("reason_tag")))

        def _submit():
            if not store or not askyesno("Confirm", "Submit write-offs?", parent=parent):
                return
            reason = reason_var.get().strip() or "Write-off"
            try:
                for line in store:
                    submit_writeoff(
                        self.conn, int(line["medicine_id"]),
                        float(line.get("quantity") or 0), reason,
                        batch_no=line.get("batch_no", ""), notes=line.get("reason_tag", ""))
                showinfo("Done", "Write-offs saved.", parent=parent)
                store.clear()
                for iid in tree.get_children():
                    tree.delete(iid)
            except Exception as exc:
                self.conn.rollback()
                showerror("Failed", str(exc), parent=parent)

        ttk.Entry(parent, textvariable=reason_var, width=50).pack(fill=tk.X, padx=8, pady=4)
        pack_centered_buttons(parent, [
            {"text": "Submit write-offs", "command": _submit, "bootstyle": "danger"},
        ], pady=8)
