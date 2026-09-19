"""
Multi-tab sessions, autosave, and quick-edit shortcuts for PurchasePage.
"""
from __future__ import annotations

import copy
import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.themed_messagebox import askyesno, showinfo, showwarning
from core.autosave_prefs import load_autosave_enabled, load_autosave_interval_seconds
from core.purchase_service import (
    save_autosave_purchase, update_autosave_purchase,
    get_or_create_supplier, fetch_recent_purchases, fetch_last_purchase_id,
)
from widgets.recent_records_picker import show_recent_records_picker


class PurchaseSessionMixin:

    def _init_purchase_session(self):
        self._autosave_purchase_id = None
        self._doc_tabs = []
        self._active_tab_idx = 0
        self._autosave_timer_id = None
        self._autosave_busy = False
        self._tab_bar_frame = None
        self._ensure_initial_tab()
        self._schedule_autosave()

    def _default_tab_label(self, tab_number: int) -> str:
        return f"Purchase {int(tab_number)}"

    def _is_default_tab_label(self, label: str) -> bool:
        text = (label or '').strip()
        return text.startswith('Purchase ') and text[9:].isdigit()

    def _normalize_default_tab_labels(self):
        for idx, tab in enumerate(self._doc_tabs, start=1):
            if self._is_default_tab_label(tab.get('label', '')):
                tab['label'] = self._default_tab_label(idx)

    def _ensure_initial_tab(self):
        if not self._doc_tabs:
            self._doc_tabs.append(self._empty_tab_state(self._default_tab_label(1)))

    def _empty_tab_state(self, label: str) -> dict:
        return {
            'label': label,
            'editing_purchase_id': None,
            'autosave_purchase_id': None,
            'edit_payment_snapshot': None,
            'import_bill_mode': False,
            'import_invoice_summary': None,
            'gst_calc_method': self.gst_calc_method_var.get(),
            'supplier_name': '',
            'supplier_address': '',
            'supplier_phone': '',
            'supplier_gstin': '',
            'supplier_dl': '',
            'purchase_date': '',
            'bill_number': '',
            'overall_discount': '0',
            'overall_discount_pct': '0',
            'rounding': '0.00',
            'expenditure': '0.00',
            'cash_paid': '',
            'online_paid': '',
            'previous_due': '0.00',
            'previous_credit': '0.00',
            'purchase_items': [],
            'last_calc': None,
        }

    def _build_tab_bar(self, parent):
        if self._editing_purchase_id:
            return
        self._tab_bar_frame = ttk.Frame(parent)
        self._tab_bar_frame.pack(fill=tk.X, pady=(0, 6))
        self._refresh_tab_bar()

    def _refresh_tab_bar(self):
        if not self._tab_bar_frame:
            return
        self._normalize_default_tab_labels()
        for w in self._tab_bar_frame.winfo_children():
            w.destroy()
        for i, tab in enumerate(self._doc_tabs):
            style = 'primary' if i == self._active_tab_idx else 'secondary'
            try:
                btn = ttk.Button(
                    self._tab_bar_frame,
                    text=tab.get('label', f'Purchase {i + 1}'),
                    bootstyle=style,
                    command=lambda idx=i: self._switch_to_tab(idx),
                )
            except Exception:
                btn = ttk.Button(
                    self._tab_bar_frame,
                    text=tab.get('label', f'Purchase {i + 1}'),
                    command=lambda idx=i: self._switch_to_tab(idx),
                )
            btn.pack(side=tk.LEFT, padx=2)
        try:
            ttk.Button(
                self._tab_bar_frame,
                text='Last Bill (F11)',
                bootstyle='info-outline',
                command=self._open_last_record_shortcut,
            ).pack(side=tk.RIGHT, padx=2)
            ttk.Button(
                self._tab_bar_frame,
                text='Recent (F10)',
                bootstyle='info-outline',
                command=self._open_recent_picker_shortcut,
            ).pack(side=tk.RIGHT, padx=2)
        except Exception:
            ttk.Button(
                self._tab_bar_frame,
                text='Last Bill (F11)',
                command=self._open_last_record_shortcut,
            ).pack(side=tk.RIGHT, padx=2)
            ttk.Button(
                self._tab_bar_frame,
                text='Recent (F10)',
                command=self._open_recent_picker_shortcut,
            ).pack(side=tk.RIGHT, padx=2)
        hint = ttk.Label(
            self._tab_bar_frame,
            text="Ctrl+Shift+N new • Ctrl+Shift+W close • Ctrl+[ / Ctrl+PgUp prev • "
                 "Ctrl+] / Ctrl+PgDn next • F10 recent • F11 last",
            font=('Segoe UI', 8),
        )
        hint.pack(side=tk.RIGHT, padx=6)

    def _capture_tab_state(self) -> dict:
        return {
            'label': self._doc_tabs[self._active_tab_idx].get('label', f'Purchase {self._active_tab_idx + 1}'),
            'editing_purchase_id': self._editing_purchase_id,
            'autosave_purchase_id': self._autosave_purchase_id,
            'edit_payment_snapshot': copy.deepcopy(self._edit_payment_snapshot),
            'import_bill_mode': self._import_bill_mode,
            'import_invoice_summary': copy.deepcopy(self._import_invoice_summary),
            'gst_calc_method': self.gst_calc_method_var.get(),
            'supplier_name': self.supplier_name.get(),
            'supplier_address': self.supplier_address.get(),
            'supplier_phone': self.supplier_phone.get(),
            'supplier_gstin': self.supplier_gstin.get(),
            'supplier_dl': self.supplier_dl.get(),
            'purchase_date': self.purchase_date.get(),
            'bill_number': self.bill_number.get(),
            'overall_discount': self.overall_discount.get(),
            'overall_discount_pct': self.overall_discount_pct.get(),
            'rounding': self.rounding_entry.get(),
            'expenditure': self.expenditure_entry.get() if hasattr(self, 'expenditure_entry') else '0.00',
            'cash_paid': self.cash_paid.get(),
            'online_paid': self.online_paid.get(),
            'previous_due': self.previous_due_var.get(),
            'previous_credit': self.previous_credit_var.get(),
            'purchase_items': copy.deepcopy(self.purchase_items),
            'last_calc': copy.deepcopy(self._last_calc) if self._last_calc else None,
        }

    def _restore_tab_state(self, state: dict):
        self._editing_purchase_id = state.get('editing_purchase_id')
        self._autosave_purchase_id = state.get('autosave_purchase_id')
        self._edit_payment_snapshot = copy.deepcopy(state.get('edit_payment_snapshot'))
        self._import_bill_mode = bool(state.get('import_bill_mode'))
        self._import_invoice_summary = copy.deepcopy(state.get('import_invoice_summary'))
        self.gst_calc_method_var.set(state.get('gst_calc_method', 'discount_before_gst'))

        self.supplier_name.set(state.get('supplier_name', ''))
        for entry, key in (
            (self.supplier_address, 'supplier_address'),
            (self.supplier_phone, 'supplier_phone'),
            (self.supplier_gstin, 'supplier_gstin'),
            (self.supplier_dl, 'supplier_dl'),
            (self.purchase_date, 'purchase_date'),
            (self.bill_number, 'bill_number'),
            (self.overall_discount, 'overall_discount'),
            (self.overall_discount_pct, 'overall_discount_pct'),
            (self.rounding_entry, 'rounding'),
            (self.cash_paid, 'cash_paid'),
            (self.online_paid, 'online_paid'),
        ):
            entry.delete(0, tk.END)
            val = state.get(key, '')
            if not val and key == 'cash_paid' and state.get('amount_paid'):
                val = state.get('amount_paid', '')
            entry.insert(0, val)
        if hasattr(self, 'expenditure_entry'):
            self.expenditure_entry.delete(0, tk.END)
            self.expenditure_entry.insert(0, state.get('expenditure', '0.00'))

        self.previous_due_var.set(state.get('previous_due', '0.00'))
        self.previous_credit_var.set(state.get('previous_credit', '0.00'))
        self.purchase_items = copy.deepcopy(state.get('purchase_items', []))
        self._last_calc = copy.deepcopy(state.get('last_calc')) if state.get('last_calc') else None
        self.update_items_tree()
        if self._last_calc:
            self.apply_stored_edit_totals(self._last_calc)
        else:
            self.calculate_total()

    def _tab_has_data(self) -> bool:
        if self.purchase_items:
            return True
        if (self.supplier_name.get() or '').strip():
            return True
        if self._editing_purchase_id or self._autosave_purchase_id:
            return True
        return False

    def _save_active_tab(self):
        if self._doc_tabs:
            self._doc_tabs[self._active_tab_idx] = self._capture_tab_state()

    def _switch_to_tab(self, idx: int):
        if idx == self._active_tab_idx or idx < 0 or idx >= len(self._doc_tabs):
            return 'break'
        self._save_active_tab()
        self._active_tab_idx = idx
        self._restore_tab_state(self._doc_tabs[idx])
        self._refresh_tab_bar()
        return 'break'

    def _new_tab_shortcut(self, event=None):
        if self._editing_purchase_id:
            return 'break'
        # Same as sales: do not clear_form() here — it would discard the previous
        # tab's autosave draft while that tab state still references it.
        self._save_active_tab()
        n = len(self._doc_tabs) + 1
        self._doc_tabs.append(self._empty_tab_state(self._default_tab_label(n)))
        self._active_tab_idx = len(self._doc_tabs) - 1
        self._restore_tab_state(self._doc_tabs[self._active_tab_idx])
        self._refresh_tab_bar()
        return 'break'

    def _close_tab_shortcut(self, event=None):
        if self._editing_purchase_id:
            return 'break'
        if len(self._doc_tabs) <= 1:
            showinfo("Tabs", "At least one purchase tab must remain open.", parent=self.parent)
            return 'break'
        if self._tab_has_data():
            if not askyesno(
                "Close Tab",
                "Close this purchase tab? Unsaved form data on this tab will be lost.",
                parent=self.parent,
            ):
                return 'break'
        autosave_id = getattr(self, '_autosave_purchase_id', None)
        del self._doc_tabs[self._active_tab_idx]
        if autosave_id:
            try:
                from core.purchase_service import delete_autosave_purchase
                delete_autosave_purchase(self.conn, autosave_id)
            except Exception:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
        self._normalize_default_tab_labels()
        self._active_tab_idx = min(self._active_tab_idx, len(self._doc_tabs) - 1)
        self._restore_tab_state(self._doc_tabs[self._active_tab_idx])
        self._refresh_tab_bar()
        return 'break'

    def _prev_tab_shortcut(self, event=None):
        if self._editing_purchase_id:
            return 'break'
        idx = (self._active_tab_idx - 1) % len(self._doc_tabs)
        return self._switch_to_tab(idx)

    def _next_tab_shortcut(self, event=None):
        if self._editing_purchase_id:
            return 'break'
        idx = (self._active_tab_idx + 1) % len(self._doc_tabs)
        return self._switch_to_tab(idx)

    def _load_purchase_for_edit(self, purchase_id: int, *, purchase_no=None) -> bool:
        """Clear current tab and load a saved purchase from history."""
        from ui.purchase.purchase_history_edit import _load_for_edit

        self._save_active_tab()
        self.clear_form(keep_tab=True)
        _load_for_edit(self.conn, self, purchase_id)
        if not self.purchase_items:
            showwarning(
                'Load Failed',
                f'Could not load purchase {purchase_no or purchase_id} from history.',
                parent=self.parent,
            )
            return False
        self._apply_loaded_purchase_ids(purchase_id)
        self._doc_tabs[self._active_tab_idx] = self._capture_tab_state()
        label = purchase_no or str(purchase_id)
        self._doc_tabs[self._active_tab_idx]['label'] = f'Edit {label}'
        self._refresh_tab_bar()
        return True

    def _open_recent_picker_shortcut(self, event=None):
        rows = fetch_recent_purchases(self.conn, limit=5)

        def _load(rid):
            match = next((r for r in rows if r[0] == rid), None)
            self._load_purchase_for_edit(rid, purchase_no=match[1] if match else None)

        show_recent_records_picker(
            self.parent.winfo_toplevel(),
            'Recent Purchases — last 5 saved bills',
            rows,
            _load,
        )
        return 'break'

    def _open_last_record_shortcut(self, event=None):
        rid = fetch_last_purchase_id(self.conn)
        if not rid:
            showwarning('No Purchases', 'No saved purchases found in history yet.', parent=self.parent)
            return 'break'
        cur = self.conn.cursor()
        cur.execute('SELECT purchase_no FROM purchases WHERE id=?', (rid,))
        row = cur.fetchone()
        self._load_purchase_for_edit(rid, purchase_no=row[0] if row else None)
        return 'break'

    def _schedule_autosave(self):
        if self._autosave_timer_id:
            try:
                self.parent.after_cancel(self._autosave_timer_id)
            except Exception:
                pass
            self._autosave_timer_id = None
        if not load_autosave_enabled():
            return
        secs = load_autosave_interval_seconds()
        self._autosave_timer_id = self.parent.after(secs * 1000, self._autosave_tick)

    def _autosave_tick(self):
        self._autosave_timer_id = None
        self._run_autosave()
        self._schedule_autosave()

    def _run_autosave(self):
        if self._autosave_busy:
            return
        if not load_autosave_enabled():
            return
        if not self.purchase_items:
            return
        if not self._page_is_visible():
            return
        if not (self.supplier_name.get() or '').strip():
            return
        self._autosave_busy = True
        try:
            self._persist_autosave()
        finally:
            self._autosave_busy = False

    def _apply_loaded_purchase_ids(self, purchase_id: int):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COALESCE(is_autosave,0) FROM purchases WHERE id=?",
            (purchase_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            self._autosave_purchase_id = purchase_id
            self._editing_purchase_id = None
        else:
            self._editing_purchase_id = purchase_id
            self._autosave_purchase_id = None

    def _persist_autosave(self):
        self.recalculate_purchase_totals()
        result = self._last_calc
        if not result:
            return
        try:
            supplier_id = get_or_create_supplier(
                self.conn,
                self.supplier_name.get().strip(),
                self.supplier_address.get(),
                self.supplier_phone.get(),
                self.supplier_gstin.get(),
                self.supplier_dl.get(),
            )
            purchase_id = self._autosave_purchase_id or self._editing_purchase_id
            bill_number = self.bill_number.get().strip()
            purchase_date = self.purchase_date.get().strip()

            if purchase_id:
                update_autosave_purchase(
                    self.conn,
                    purchase_id,
                    supplier_id,
                    bill_number,
                    purchase_date,
                    result,
                    self.purchase_items,
                )
            else:
                purchase_no, purchase_id = save_autosave_purchase(
                    self.conn,
                    supplier_id,
                    purchase_date,
                    bill_number,
                    result,
                    self.purchase_items,
                )
                self._autosave_purchase_id = purchase_id
                self._doc_tabs[self._active_tab_idx]['label'] = f"Draft {purchase_no}"
                self._refresh_tab_bar()
            self._save_active_tab()
        except Exception:
            self.conn.rollback()
