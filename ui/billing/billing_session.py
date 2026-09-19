"""
Multi-tab sessions, autosave, and quick-edit shortcuts for BillingPage.
"""
from __future__ import annotations

import copy
import time
import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.themed_messagebox import ask_choice, askyesno, showinfo, showwarning
from core.autosave_prefs import (
    load_autosave_enabled, load_autosave_interval_seconds,
)
from core.autosave_bill import (
    discard_autosave_bill, list_recoverable, resume_autosave_bill,
    write_autosave_bill,
)
from core.billing_service import fetch_recent_sales, fetch_last_sale_id
from core.customer_service import get_or_create_customer, get_customer_names
from core.sales_form_io import load_sale_into_billing_page
from widgets.recent_records_picker import show_recent_records_picker


class BillingSessionMixin:

    def _init_billing_session(self):
        self._editing_sale_id = None
        self._autosave_sale_id = None
        self._autosave_token = None
        self._edit_payment_snapshot = None
        self._in_edit_window = False
        self._doc_tabs = []
        self._active_tab_idx = 0
        self._autosave_timer_id = None
        self._autosave_busy = False
        self._tab_bar_frame = None
        self._last_tab_shortcut_at = 0.0
        self._ensure_initial_tab()
        self._schedule_autosave()

    def _default_tab_label(self, tab_number: int) -> str:
        return f"Sale {int(tab_number)}"

    def _is_default_tab_label(self, label: str) -> bool:
        text = (label or '').strip()
        return text.startswith('Sale ') and text[5:].isdigit()

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
            'editing_sale_id': None,
            'autosave_sale_id': None,
            'autosave_token': None,
            'edit_payment_snapshot': None,
            'customer_name': '',
            'customer_phone': '',
            'customer_address': '',
            'doctor_name': '',
            'doctor_phone': '',
            'bill_date': '',
            'payment_mode': '',
            'discount_pct': '0',
            'discount': '0',
            'rounding': '0.00',
            'cash_paid': '',
            'online_paid': '',
            'previous_due': 0.0,
            'previous_credit': 0.0,
            'selected_medicines': [],
        }

    def _build_tab_bar(self, parent):
        if getattr(self, '_in_edit_window', False):
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
                    text=tab.get('label', f'Sale {i + 1}'),
                    bootstyle=style,
                    command=lambda idx=i: self._switch_to_tab(idx),
                )
            except Exception:
                btn = ttk.Button(
                    self._tab_bar_frame,
                    text=tab.get('label', f'Sale {i + 1}'),
                    command=lambda idx=i: self._switch_to_tab(idx),
                )
            btn.pack(side=tk.LEFT, padx=2)
        if not getattr(self, '_in_edit_window', False):
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
            'label': self._doc_tabs[self._active_tab_idx].get('label', f'Sale {self._active_tab_idx + 1}'),
            'editing_sale_id': self._editing_sale_id,
            'autosave_sale_id': self._autosave_sale_id,
            'autosave_token': getattr(self, '_autosave_token', None),
            'edit_payment_snapshot': copy.deepcopy(self._edit_payment_snapshot),
            'customer_name': self.customer_name.get(),
            'customer_phone': self.customer_phone.get(),
            'customer_address': self.customer_address.get(),
            'doctor_name': self.doctor_name.get(),
            'doctor_phone': self.doctor_phone.get(),
            'bill_date': str(self.get_bill_date_value()),
            'payment_mode': self.payment_mode.get(),
            'discount_pct': self.discount_pct.get(),
            'discount': self.discount.get(),
            'rounding': self.rounding.get(),
            'rounding_touched': bool(getattr(self, '_rounding_touched', False)),
            'cash_paid': self.cash_paid.get(),
            'online_paid': self.online_paid.get(),
            'previous_due': self.previous_due,
            'previous_credit': self.previous_credit,
            'selected_medicines': copy.deepcopy(self.selected_medicines),
        }

    def _restore_tab_state(self, state: dict):
        self._editing_sale_id = state.get('editing_sale_id')
        self._autosave_sale_id = state.get('autosave_sale_id')
        self._autosave_token = state.get('autosave_token')
        self._edit_payment_snapshot = copy.deepcopy(state.get('edit_payment_snapshot'))

        self.payment_mode.set(state.get('payment_mode', ''))
        self._on_payment_mode_change()
        self.customer_name.set(state.get('customer_name', ''))
        self.customer_phone.delete(0, tk.END)
        self.customer_phone.insert(0, state.get('customer_phone', ''))
        self.customer_address.set(state.get('customer_address', ''))
        self.doctor_name.set(state.get('doctor_name', ''))
        self.doctor_phone.delete(0, tk.END)
        self.doctor_phone.insert(0, state.get('doctor_phone', ''))
        self._set_bill_date_value(state.get('bill_date', ''))

        self.discount_pct.delete(0, tk.END)
        self.discount_pct.insert(0, state.get('discount_pct', '0'))
        self.discount.delete(0, tk.END)
        self.discount.insert(0, state.get('discount', '0'))
        self.rounding.delete(0, tk.END)
        self.rounding.insert(0, state.get('rounding', '0.00'))
        self._rounding_touched = bool(state.get('rounding_touched', False))
        self.cash_paid.delete(0, tk.END)
        self.cash_paid.insert(0, state.get('cash_paid', ''))
        self.online_paid.delete(0, tk.END)
        self.online_paid.insert(0, state.get('online_paid', ''))

        self.previous_due = float(state.get('previous_due', 0))
        self.previous_credit = float(state.get('previous_credit', 0))
        self.previous_due_var.set(f"{self.previous_due:.2f}")

        self.selected_medicines = copy.deepcopy(state.get('selected_medicines', []))
        self._sync_medicine_reserved_stock()
        self.update_medicine_tree()
        self.calculate_total()

    def _tab_has_data(self) -> bool:
        if self.selected_medicines:
            return True
        if (self.customer_name.get() or '').strip():
            return True
        if self._editing_sale_id or self._autosave_sale_id:
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

    def _tab_shortcut_debounce(self) -> bool:
        """Return True when the same tab shortcut fired twice in one keypress."""
        now = time.time()
        if now - self._last_tab_shortcut_at < 0.35:
            return True
        self._last_tab_shortcut_at = now
        return False

    def _new_tab_shortcut(self, event=None):
        if getattr(self, '_in_edit_window', False):
            return 'break'
        if self._tab_shortcut_debounce():
            return 'break'
        try:
            # Save current tab (including its autosave draft id) then restore a
            # blank tab. Do NOT call clear_form() here — that would discard the
            # previous tab's still-active _autosave_sale_id and delete the draft
            # from the DB while the tab state still points at it (counter-sale
            # all-day tab + second tab for other sales).
            self._save_active_tab()
            n = len(self._doc_tabs) + 1
            self._doc_tabs.append(self._empty_tab_state(self._default_tab_label(n)))
            self._active_tab_idx = len(self._doc_tabs) - 1
            self._restore_tab_state(self._doc_tabs[self._active_tab_idx])
            self._refresh_tab_bar()
            from core.keyboard_registry import KeyboardRegistry
            KeyboardRegistry._log_action(
                'billing:new_tab', True, event, source='billing_session',
            )
        except Exception as exc:
            from core.keyboard_registry import KeyboardRegistry
            KeyboardRegistry._log_action(
                'billing:new_tab', False, event,
                reason=str(exc), source='billing_session',
            )
            showwarning("New Sale Tab", f"Could not open a new sale tab:\n{exc}", parent=self.parent)
        return 'break'

    def _close_tab_shortcut(self, event=None):
        if getattr(self, '_in_edit_window', False):
            return 'break'
        if self._tab_shortcut_debounce():
            return 'break'
        if len(self._doc_tabs) <= 1:
            showinfo("Tabs", "At least one sale tab must remain open.", parent=self.parent)
            return 'break'
        if self._tab_has_data():
            if not askyesno(
                "Close Tab",
                "Close this sale tab? Unsaved form data on this tab will be lost.",
                parent=self.parent,
            ):
                return 'break'
        autosave_id = getattr(self, '_autosave_sale_id', None)
        autosave_token = getattr(self, '_autosave_token', None)
        del self._doc_tabs[self._active_tab_idx]
        if autosave_id or autosave_token:
            try:
                # Autosave holds a REAL bill now. Closing the tab gives back
                # only what this tab put in it -- a counter tab must not take
                # the rest of the day's counter sales with it.
                discard_autosave_bill(
                    self.conn,
                    token=autosave_token or '',
                    sale_id=int(autosave_id or 0),
                )
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
        if getattr(self, '_in_edit_window', False):
            return 'break'
        if self._tab_shortcut_debounce():
            return 'break'
        idx = (self._active_tab_idx - 1) % len(self._doc_tabs)
        return self._switch_to_tab(idx)

    def _next_tab_shortcut(self, event=None):
        if getattr(self, '_in_edit_window', False):
            return 'break'
        if self._tab_shortcut_debounce():
            return 'break'
        idx = (self._active_tab_idx + 1) % len(self._doc_tabs)
        return self._switch_to_tab(idx)

    def _load_sale_for_edit(self, sale_id: int, *, bill_no=None) -> bool:
        """Load a saved sale for edit — uses a new tab when the current one has data."""
        if getattr(self, '_in_edit_window', False):
            return False
        if self._tab_has_data():
            # Preserve counter / autosave work on the current tab.
            self._save_active_tab()
            n = len(self._doc_tabs) + 1
            self._doc_tabs.append(self._empty_tab_state(self._default_tab_label(n)))
            self._active_tab_idx = len(self._doc_tabs) - 1
            self._restore_tab_state(self._doc_tabs[self._active_tab_idx])
        else:
            self._autosave_sale_id = None
            self._editing_sale_id = None
            self._edit_payment_snapshot = None
            self.clear_form(keep_tab=True)
        if not load_sale_into_billing_page(self.conn, self, sale_id):
            showwarning(
                'Load Failed',
                f'Could not load sale {bill_no or sale_id} from history.',
                parent=self.parent,
            )
            return False
        self._doc_tabs[self._active_tab_idx] = self._capture_tab_state()
        label = bill_no or str(sale_id)
        self._doc_tabs[self._active_tab_idx]['label'] = f'Edit {label}'
        self._refresh_tab_bar()
        return True

    def _open_recent_picker_shortcut(self, event=None):
        if getattr(self, '_in_edit_window', False):
            return 'break'
        rows = fetch_recent_sales(self.conn, limit=5)

        def _load(rid):
            match = next((r for r in rows if r[0] == rid), None)
            self._load_sale_for_edit(rid, bill_no=match[1] if match else None)

        show_recent_records_picker(
            self.parent.winfo_toplevel(),
            'Recent Sales — last 5 saved bills',
            rows,
            _load,
        )
        return 'break'

    def _open_last_record_shortcut(self, event=None):
        if getattr(self, '_in_edit_window', False):
            return 'break'
        rid = fetch_last_sale_id(self.conn)
        if not rid:
            showwarning('No Sales', 'No saved sales found in history yet.', parent=self.parent)
            return 'break'
        cur = self.conn.cursor()
        cur.execute('SELECT bill_no FROM sales WHERE id=?', (rid,))
        row = cur.fetchone()
        self._load_sale_for_edit(rid, bill_no=row[0] if row else None)
        return 'break'

    def _schedule_autosave(self):
        if self._autosave_timer_id:
            try:
                self.parent.after_cancel(self._autosave_timer_id)
            except Exception:
                pass
            self._autosave_timer_id = None
        if not load_autosave_enabled() or getattr(self, '_in_edit_window', False):
            return
        secs = load_autosave_interval_seconds()
        self._autosave_timer_id = self.parent.after(secs * 1000, self._autosave_tick)

    def _autosave_tick(self):
        self._autosave_timer_id = None
        self._run_autosave()
        self._schedule_autosave()

    def _run_autosave(self):
        if self._autosave_busy or getattr(self, '_in_edit_window', False):
            return
        if not load_autosave_enabled():
            return
        if self._editing_sale_id and not self._autosave_sale_id:
            return
        if not self.selected_medicines:
            return
        if not self._page_is_visible():
            return
        self._autosave_busy = True
        try:
            self._persist_autosave()
        finally:
            self._autosave_busy = False

    def _persist_autosave(self):
        # Never rewrite a real F10/F11 edit as an autosave draft.
        if self._editing_sale_id and not self._autosave_sale_id:
            return
        customer_name = self._resolve_customer_name(touch_ui=False)
        if not customer_name:
            return
        if not self._validate_scheduled_requirements():
            return

        try:
            disc_rs = float(self.discount.get() or 0)
        except ValueError:
            disc_rs = 0.0
        try:
            disc_pct = float(self.discount_pct.get() or 0)
            rounding = float(self.rounding.get() or 0)
            if getattr(self, '_is_due_payment', lambda: False)():
                cash = online = 0.0
            else:
                cash = float(self.cash_paid.get() or 0)
                online = float(self.online_paid.get() or 0)
            customer_id = get_or_create_customer(
                self.conn,
                customer_name,
                self.customer_phone.get().strip(),
                self.customer_address.get().strip(),
            )
            bill_date = self.get_bill_date_value()
            previous_due = self._get_edit_previous_due()
            if not getattr(self, '_autosave_token', None):
                # Mint the token BEFORE the write and keep it on the tab. An
                # engine-minted token only reached the tab when the write
                # returned; a write that pinned the bill and then threw left
                # the tab with no token, and the next tick opened bill two.
                from core.autosave_session import new_token

                self._autosave_token = new_token()
                self._save_active_tab()
            res = write_autosave_bill(
                self.conn,
                token=getattr(self, '_autosave_token', None) or '',
                sale_id=int(self._autosave_sale_id or 0),
                customer_id=customer_id,
                customer_name=customer_name,
                customer_phone=self.customer_phone.get().strip(),
                medicines=self.selected_medicines,
                discount_pct=disc_pct,
                discount_rs=disc_rs,
                rounding=rounding,
                cash_paid=cash,
                online_paid=online,
                doctor_name=self.doctor_name.get(),
                doctor_phone=self.doctor_phone.get().strip(),
                previous_due=previous_due,
                bill_date=bill_date,
                # This tick runs on the Tk thread: never wait on the store for the bill
                # date's answer. Until it is in, the engine keeps the form and writes nothing.
                availability_wait=False,
            )
            self._autosave_sale_id = int(res.get('sale_id') or 0) or None
            self._autosave_token = res.get('token') or None
            bill_no = res.get('bill_no') or ''
            if res.get('created') and bill_no:
                self._doc_tabs[self._active_tab_idx]['label'] = str(bill_no)
                self._refresh_tab_bar()
            self._save_active_tab()
        except Exception:
            self.conn.rollback()

    # ── Unfinished sales left behind by a crash ──────────────────────────

    def offer_autosave_recovery(self):
        """Hand back any in-progress bill this device is still holding open.

        Autosave writes a REAL bill on the first tick, and which bill a tab owns
        lived in ``self._autosave_sale_id`` -- memory. A crash or a power cut
        left the bill on the customer's account and the form empty, so the sale
        was typed again and billed twice. The engine's session record survives
        the process, so ask it rather than remember.

        Deliberately not silent: by the time this runs the money and the stock
        have already moved, and a form that quietly refills itself does not tell
        anyone that. The operator is told the number and the amount, and picks.
        """
        if getattr(self, '_in_edit_window', False):
            return
        try:
            rows = list_recoverable()
        except Exception as exc:
            print(f"[autosave] recovery scan failed: {exc}")
            return
        held = {
            t.get('autosave_token')
            for t in self._doc_tabs
            if t.get('autosave_token')
        }
        rows = [r for r in rows if r.get('token') not in held]
        if not rows:
            return
        # Yesterday's leftovers first -- nobody is expecting those.
        rows.sort(key=lambda r: (not r.get('stale'), -float(r.get('updated_at') or 0)))
        for rec in rows:
            try:
                self._ask_about_recovered_sale(rec)
            except Exception as exc:
                print(f"[autosave] recovery prompt failed: {exc}")

    def _recovered_summary(self, rec: dict) -> str:
        if rec.get('held'):
            # No bill was written: a line on the form could not be sold on its Bill Date,
            # so only the form was kept.
            return "\n".join([
                f"A sale for {rec.get('customer_name') or 'a customer'} — "
                f"{int(rec.get('items') or 0)} item(s), ₹{float(rec.get('total') or 0):.2f}.",
                "",
                "It was NOT saved as a bill: a medicine on it had not come in, or had "
                "expired, by its Bill Date. Only the form was kept.",
                "",
                "Resume  — put the form back on screen to correct it and save.",
                "Discard — clear the kept form (no bill, stock or balance is touched).",
                "Not now — leave it; it is offered again next time.",
            ])
        who = (
            "today's counter bill"
            if rec.get('counter')
            else (rec.get('customer_name') or 'this customer')
        )
        lines = [
            f"Bill {rec.get('bill_no') or rec.get('sale_id')} — "
            f"{int(rec.get('items') or 0)} item(s), "
            f"₹{float(rec.get('total') or 0):.2f} for {who}.",
            "",
            "This bill is already saved: the stock and the balance have already "
            "moved. It was never finished.",
        ]
        if rec.get('stale'):
            lines.insert(1, f"Started on {rec.get('bill_date')} and never completed.")
        lines += [
            "",
            "Resume  — put it back on screen and finish it (no second bill).",
            "Discard — take it back off the books"
            + (
                " (only these lines leave the counter bill)."
                if rec.get('counter')
                else " and give the stock back."
            ),
            "Not now — leave it; it stays claimable and shows as unfinished.",
        ]
        return "\n".join(lines)

    def _ask_about_recovered_sale(self, rec: dict):
        choice = ask_choice(
            'Unfinished Sale Recovered',
            self._recovered_summary(rec),
            [
                ('Resume', 'resume', 'primary'),
                ('Discard', 'discard', 'danger'),
                ('Not now', 'later', 'secondary'),
            ],
            parent=self.parent,
        )
        if choice == 'resume':
            self._resume_recovered_sale(rec)
        elif choice == 'discard':
            self._discard_recovered_sale(rec)

    def _resume_recovered_sale(self, rec: dict):
        """Reopen the sale on a tab that still OWNS its bill.

        The token comes back with the form, which is the whole point: the next
        tick and the save are updates of the bill that already exists, and for a
        counter sale the token is what lets the engine take this form's own
        earlier lines out of the shared day bill before putting the current ones
        in. Resuming without it bills the day twice.
        """
        try:
            res = resume_autosave_bill(
                self.conn,
                token=str(rec.get('token') or ''),
                sale_id=int(rec.get('sale_id') or 0),
            )
        except Exception as exc:
            showwarning('Unfinished Sale', f'Could not reopen the sale:\n{exc}',
                        parent=self.parent)
            return
        if not res.get('ok'):
            showwarning(
                'Unfinished Sale',
                res.get('error') or 'That sale could not be reopened.',
                parent=self.parent,
            )
            return

        form = res.get('form') or {}
        label = str(res.get('bill_no') or rec.get('bill_no') or 'Recovered')
        state = self._empty_tab_state(label)
        state.update({
            'autosave_sale_id': int(res.get('sale_id') or 0) or None,
            'autosave_token': res.get('token') or None,
            'customer_name': form.get('customer_name') or '',
            'customer_phone': form.get('customer_phone') or '',
            'customer_address': form.get('customer_address') or '',
            'doctor_name': form.get('doctor_name') or '',
            'doctor_phone': form.get('doctor_phone') or '',
            'bill_date': form.get('bill_date') or '',
            'payment_mode': 'Cash',
            'discount_pct': str(form.get('discount_pct') or 0),
            'discount': str(form.get('discount_rs') or 0),
            'rounding': f"{float(form.get('rounding') or 0):.2f}",
            'rounding_touched': True,
            'cash_paid': str(form.get('cash_paid') or 0),
            'online_paid': str(form.get('online_paid') or 0),
            'previous_due': float(form.get('previous_due') or 0),
            'selected_medicines': copy.deepcopy(list(form.get('medicines') or [])),
        })

        # Never overwrite a tab that already has work on it.
        self._save_active_tab()
        if self._tab_has_data():
            self._doc_tabs.append(state)
            self._active_tab_idx = len(self._doc_tabs) - 1
        else:
            self._doc_tabs[self._active_tab_idx] = state
        self._restore_tab_state(self._doc_tabs[self._active_tab_idx])
        self._normalize_default_tab_labels()
        self._refresh_tab_bar()

    def _discard_recovered_sale(self, rec: dict):
        question = (
            f"Clear the kept form (₹{float(rec.get('total') or 0):.2f})? "
            "No bill was saved, so no stock or balance moves."
            if rec.get('held')
            else f"Take bill {rec.get('bill_no') or rec.get('sale_id')} "
            f"(₹{float(rec.get('total') or 0):.2f}) back off the books?"
        )
        if not askyesno(
            'Discard Unfinished Sale',
            question,
            parent=self.parent,
        ):
            return
        try:
            out = discard_autosave_bill(
                self.conn,
                token=str(rec.get('token') or ''),
                sale_id=int(rec.get('sale_id') or 0),
            )
        except Exception as exc:
            try:
                self.conn.rollback()
            except Exception:
                pass
            out = {'ok': False, 'error': str(exc)}
        if not out.get('ok'):
            showwarning(
                'Discard Unfinished Sale',
                (out.get('error') or 'The bill could not be reversed.')
                + '\n\nNothing was changed — the sale is still recorded.',
                parent=self.parent,
            )

    def _get_edit_previous_due(self) -> float:
        snap = self._edit_payment_snapshot
        if snap:
            return float(snap.get('previous_due', 0))
        return float(self.previous_due or 0)
