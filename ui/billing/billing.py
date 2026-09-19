from __future__ import annotations

import os
import threading
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno

from core.calc_engine import calc_bill_summary, calc_payment_result, auto_round
from core.customer_service import (
    get_or_create_customer, get_customer_names, COUNTER_SALE, is_counter_sale_name,
)
from core.billing_service import (
    save_new_bill as _legacy_save_new_bill, append_counter_sale_today, update_existing_bill,
    finalize_autosave_bill,
)
try:
    from core.sync_v3.repositories.sale_repository import maybe_save_new_bill as save_new_bill
except Exception:
    save_new_bill = _legacy_save_new_bill
from core.margin_utils import (
    format_total_margin_display,
    total_net_margin,
    validate_bill_discounts,
)
from core.bill_output import save_bill_pdf_only, print_bill_with_slot, print_bill_silent_with_slot
from core.bill_config import get_print_slot_key, load_bill_print_settings
from core.keyboard_registry import KeyboardRegistry, PageBindings
from ui.billing.billing_nav  import BillingNavMixin
from ui.billing.billing_form import BillingFormMixin
from ui.billing.billing_session import BillingSessionMixin


class BillingPage(BillingSessionMixin, BillingNavMixin, BillingFormMixin):

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent

        self.selected_medicines = []
        self.previous_due    = 0
        self.previous_credit = 0
        self._last_bill_no = None
        self._last_sale_id = None
        self._customer_id  = None
        self._pdf_busy = False
        # Once the user edits Round, keep it (do not overwrite on Cash/Online focus).
        self._rounding_touched = False

        self._init_billing_session()
        self._build_interface()
        self.refresh_print_button_labels()
        self._register_keyboard()
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(0, self.reload_villages)
            self.parent.after(150, self._focus_first_field)
            # An autosaved bill is a REAL bill. If the app was killed mid-sale,
            # that money is already on a customer and this form is empty -- ask
            # the engine what was in progress before the counter starts typing
            # the same sale a second time. After the fields exist, and after
            # focus, so the prompt is the first thing seen and not a surprise
            # halfway through a line.
            self.parent.after(700, self.offer_autosave_recovery)

    def _page_is_visible(self):
        try:
            return bool(self.parent.winfo_ismapped())
        except tk.TclError:
            return False

    def _scroll_form_to_top(self):
        try:
            canvas = getattr(getattr(self, '_inner_frame', None), '_canvas', None)
            if canvas is not None:
                canvas.yview_moveto(0)
        except Exception:
            pass

    def _focus_first_field(self):
        if not self._page_is_visible():
            return
        self._scroll_form_to_top()
        if self._sales_payment_mode_enabled() and not self._sales_payment_after_bill_date():
            self._focus_payment_mode_field()
        else:
            self._focus_customer_name()

    def _focus_payment_mode(self):
        self._focus_payment_mode_field()

    def _focus_customer_name(self):
        try:
            if self.customer_name.winfo_exists():
                self.customer_name.focus()
        except tk.TclError:
            pass

    def _register_keyboard(self):
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(100, self._setup_arrow_nav)
        bindings = KeyboardRegistry.make_bindings(
            page_id='billing',
            first_focus=self._focus_first_field,
            on_f5=self._save_sales_shortcut,
            on_f6=self._focus_overall_discount,
            on_f7=self._print_sales_1_shortcut,
            on_f8=self._print_sales_2_shortcut,
            on_f9=self._silent_reprint_shortcut,
            on_end=self._focus_payment_field,
            on_ctrl_p=self._print_last_bill,
            on_ctrl_shift_c=self._clear_form_shortcut,
            on_f10=self._open_recent_picker_shortcut,
            on_f11=self._open_last_record_shortcut,
            on_ctrl_shift_n=self._new_tab_shortcut,
            on_ctrl_shift_w=self._close_tab_shortcut,
            on_ctrl_shift_m=self._quick_sale_medicine_shortcut,
            on_ctrl_prior=self._prev_tab_shortcut,
            on_ctrl_next=self._next_tab_shortcut,
            f2_target=self.medicine_tree,
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)
        self._bind_last_bill_shortcut()

    def _bind_last_bill_shortcut(self):
        """Extra bind so F11 works from Entry fields on Windows."""
        seqs = ('<F11>', '<KeyPress-F11>')
        for seq in seqs:
            try:
                self._inner_frame.bind(seq, self._open_last_record_shortcut, add='+')
            except tk.TclError:
                pass

    def _rebind_mousewheel(self):
        pass  # scroll_manager handles this

    def _print_last_bill(self):
        if self._last_sale_id:
            self._print_sales_slot(1)
        else:
            showinfo("No Bill", "No sale saved yet in this session.", parent=self.parent)

    def _has_scheduled_medicine(self) -> bool:
        return any((m.get('schedule') or '').strip() for m in self.selected_medicines)

    def _sale_requires_doctor(self) -> bool:
        try:
            from core.billing_layout_prefs import sale_requires_doctor
            return sale_requires_doctor(self.selected_medicines)
        except Exception:
            return self._has_scheduled_medicine()

    def _resolve_customer_name(self, *, touch_ui: bool = False) -> str | None:
        from core.name_utils import storage_name_from_entry

        name = self.customer_name.get().strip()
        if not name:
            if self._has_scheduled_medicine():
                showwarning(
                    "Missing Information",
                    "Scheduled medicine requires a customer name.",
                    parent=self.parent,
                )
                return None
            return COUNTER_SALE
        if is_counter_sale_name(name):
            if touch_ui and name.strip().upper() != COUNTER_SALE:
                self.customer_name.set(COUNTER_SALE)
            return COUNTER_SALE
        resolved = storage_name_from_entry(name) or name.upper()
        if touch_ui and resolved != name:
            self.customer_name.set(resolved)
            self._update_sale_tab_label()
        return resolved

    def _quick_sale_medicine_shortcut(self, event=None):
        self.open_quick_sale_medicine_dialog()
        return 'break'

    def _print_sales_1_shortcut(self, event=None):
        self._print_sales_slot(1)
        return 'break'

    def _print_sales_2_shortcut(self, event=None):
        self._print_sales_slot(2)
        return 'break'

    def _silent_reprint_shortcut(self, event=None):
        """F9 — silently reprint last saved sale (Print Sales 1 preset, no save)."""
        if self._pdf_busy:
            return 'break'
        if not self._last_sale_id:
            showinfo("No Bill", "No sale saved yet in this session.", parent=self.parent)
            return 'break'
        self._silent_reprint_slot(self._last_sale_id, 1)
        return 'break'

    def _silent_reprint_slot(self, sale_id: int, slot: int):
        if self._pdf_busy:
            return
        self._pdf_busy = True
        bill_no = self._last_bill_no or str(sale_id)
        db_path = self._bill_db_path()

        def _work():
            try:
                print_bill_silent_with_slot(self.conn, sale_id, slot, db_path=db_path)
            except Exception as exc:
                self.parent.after(
                    0,
                    lambda e=str(exc): showerror(
                        "Print Bill", f"Silent print failed for bill {bill_no}:\n{e}",
                        parent=self.parent,
                    ),
                )
            finally:
                self.parent.after(0, lambda: setattr(self, '_pdf_busy', False))

        threading.Thread(
            target=_work, daemon=True, name=f"BillSilent{slot}",
        ).start()

    def _validate_scheduled_requirements(self) -> bool:
        if self._has_scheduled_medicine() and not self.customer_name.get().strip():
            showwarning(
                "Missing Information",
                "Scheduled medicine requires a customer name.",
                parent=self.parent,
            )
            try:
                self.customer_name.focus()
            except Exception:
                pass
            return False
        if self._sale_requires_doctor() and not self.doctor_name.get().strip():
            showwarning(
                "Doctor Required",
                "H1 / X (and other schedules if enabled in Settings) require a doctor name.",
                parent=self.parent,
            )
            try:
                self.doctor_name.entry.focus_set()
            except Exception:
                pass
            return False
        return True

    def _bill_db_path(self) -> str:
        """Absolute path of the live store DB used for this billing page."""
        import os
        for obj in (self.parent, getattr(self.parent, "winfo_toplevel", lambda: None)()):
            if obj is None:
                continue
            path = getattr(obj, "db_path", None)
            if path:
                return os.path.abspath(path)
        try:
            from core.bill_output import resolve_bill_db_path
            return resolve_bill_db_path(self.conn)
        except Exception:
            return ""

    def _prepare_bill_pdf_context(self, sale_id: int) -> str:
        """Flush WAL and confirm the sale is readable before background PDF/print."""
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                # Online bills are on the server — local PDF export is skipped.
                return ""
        except Exception:
            pass
        db_path = self._bill_db_path()
        try:
            self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass
        try:
            self.conn.commit()
        except Exception:
            pass
        row = self.conn.execute(
            "SELECT id FROM sales WHERE id=?",
            (int(sale_id),),
        ).fetchone()
        if not row:
            raise ValueError(f"Sale id {sale_id} not found")
        return db_path

    def _persist_sale(self) -> tuple[str, int] | None:
        """Validate and save the current bill; returns (bill_no, sale_id) or None."""
        try:
            from core.pharmacy_profile_io import has_pharmacy_profile

            if not has_pharmacy_profile(self.conn):
                showwarning(
                    "Setup Required",
                    "Please set up pharmacy profile in Settings first.",
                    parent=self.parent,
                )
                return None
        except Exception:
            self.cursor.execute("SELECT * FROM pharmacy_profile LIMIT 1")
            if not self.cursor.fetchone():
                showwarning(
                    "Setup Required",
                    "Please set up pharmacy profile in Settings first.",
                    parent=self.parent,
                )
                return None
        customer_name = self._resolve_customer_name(touch_ui=True)
        if not customer_name:
            return None
        if not self.selected_medicines:
            showwarning(
                "No Medicines",
                "Please add medicines to the bill.",
                parent=self.parent,
            )
            return None
        if not self._validate_scheduled_requirements():
            return None
        if not self._validate_payment_mode():
            return None
        if not self._validate_cash_payment():
            return None
        if not (self._editing_sale_id and not self._autosave_sale_id):
            # The picker checks a line when it is added; the Bill Date can change after.
            from core.sale_availability import lines_unavailable_on

            problems = lines_unavailable_on(
                self.conn, self.selected_medicines, self.get_bill_date_value()
            )
            if problems:
                showerror(
                    "Not Available On This Date", "\n".join(problems), parent=self.parent
                )
                return None

        try:
            disc_rs = float(self.discount.get() or 0)
        except ValueError:
            disc_rs = 0.0
        if not validate_bill_discounts(self.parent, self.selected_medicines, disc_rs):
            return None

        # Block concurrent autosave so it cannot delete/rewrite this bill mid-save.
        self._autosave_busy = True
        try:
            customer_id = get_or_create_customer(
                self.conn,
                customer_name,
                self.customer_phone.get().strip(),
                self.customer_address.get().strip(),
            )
            disc_pct = float(self.discount_pct.get() or 0)
            rounding = float(self.rounding.get() or 0)
            if getattr(self, '_is_due_payment', lambda: False)():
                cash = 0.0
                online = 0.0
            else:
                cash = float(self.cash_paid.get() or 0)
                online = float(self.online_paid.get() or 0)

            bill_date = self.get_bill_date_value()

            # Saved as typed. Paid more than the bill and the old due together is only
            # pointed out afterwards (store 4 SCB1061: Rs 210 cash and Rs 210 online).
            try:
                from core.save_warnings import sale_warnings

                if self._editing_sale_id and not self._autosave_sale_id:
                    old_due = float(
                        (self._edit_payment_snapshot or {}).get('previous_due', self.previous_due)
                        or 0
                    )
                else:
                    old_due = float(self.previous_due or 0)
                self._save_warnings = sale_warnings(
                    total=calc_bill_summary(
                        self.selected_medicines, disc_pct, rounding, discount_rs=disc_rs
                    )['total_amount'],
                    cash_paid=cash,
                    online_paid=online,
                    previous_due=old_due,
                )
            except Exception:
                self._save_warnings = []

            if self._editing_sale_id and not self._autosave_sale_id:
                snap = self._edit_payment_snapshot or {}
                prev_due = float(snap.get('previous_due', self.previous_due))
                bill_no = str(self._editing_sale_id)
                try:
                    from core.sync_prefs import is_online_mode
                    if is_online_mode():
                        from core.server_crud import get_doc
                        doc = get_doc("sales", int(self._editing_sale_id)) or {}
                        bill_no = str(doc.get("bill_no") or bill_no)
                    else:
                        cur = self.conn.cursor()
                        cur.execute(
                            "SELECT bill_no FROM sales WHERE id=?",
                            (self._editing_sale_id,),
                        )
                        row = cur.fetchone()
                        if row and row[0]:
                            bill_no = row[0]
                except Exception:
                    bill_no = str(self._editing_sale_id)
                update_existing_bill(
                    self.conn,
                    self._editing_sale_id,
                    self.selected_medicines,
                    discount_pct=disc_pct,
                    rounding=rounding,
                    cash_paid=cash,
                    online_paid=online,
                    customer_name=customer_name,
                    customer_phone=self.customer_phone.get().strip(),
                    doctor_name=self.doctor_name.get(),
                    previous_due=prev_due,
                    discount_rs=disc_rs,
                    bill_date=bill_date,
                )
                sale_id = self._editing_sale_id
            elif self._autosave_sale_id or getattr(self, '_autosave_token', None):
                from core.autosave_bill import write_autosave_bill
                from core.autosave_session import own_session

                token = getattr(self, '_autosave_token', None) or ''
                live = own_session(token, self._autosave_sale_id or 0)
                # A token always finishes through the engine: a stale one must
                # not reach finalize_autosave_bill (it rewrites a real bill id
                # -- the day's counter bill -- with this form's lines).
                if live is not None or token:
                    # Autosave already made this a REAL bill. Saving is the last
                    # update of that same bill: no second number, and a counter
                    # sale is not added to the day bill twice.
                    res = write_autosave_bill(
                        self.conn,
                        token=token or str(live.get('token') or ''),
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
                        previous_due=self.previous_due,
                        bill_date=bill_date,
                        final=True,
                    )
                    bill_no = res.get('bill_no') or ''
                    sale_id = int(res.get('sale_id') or 0)
                else:
                    bill_no, sale_id = finalize_autosave_bill(
                        self.conn,
                        self._autosave_sale_id,
                        customer_id,
                        self.selected_medicines,
                        disc_pct,
                        rounding,
                        cash,
                        online,
                        self.doctor_name.get(),
                        self.doctor_phone.get().strip(),
                        customer_name,
                        self.customer_phone.get().strip(),
                        self.previous_due,
                        discount_rs=disc_rs,
                        bill_date=bill_date,
                    )
            else:
                merged = append_counter_sale_today(
                    conn=self.conn,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    medicines=self.selected_medicines,
                    discount_pct=disc_pct,
                    discount_rs=disc_rs,
                    rounding=rounding,
                    cash_paid=cash,
                    online_paid=online,
                    doctor_name=self.doctor_name.get(),
                    doctor_phone=self.doctor_phone.get().strip(),
                    previous_due=self.previous_due,
                    bill_date=bill_date,
                )
                if merged:
                    bill_no, sale_id = merged
                else:
                    bill_no, sale_id = save_new_bill(
                        conn=self.conn,
                        customer_id=customer_id,
                        medicines=self.selected_medicines,
                        discount_pct=disc_pct,
                        discount_rs=disc_rs,
                        rounding=rounding,
                        cash_paid=cash,
                        online_paid=online,
                        doctor_name=self.doctor_name.get(),
                        doctor_phone=self.doctor_phone.get().strip(),
                        previous_due=self.previous_due,
                        bill_date=bill_date,
                        customer_name=customer_name,
                        customer_phone=self.customer_phone.get().strip(),
                    )
            # Refresh dropdown values without blocking save (Online catalog can be large).
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    cur_vals = list(self.customer_name.cget("values") or ())
                    nm = (customer_name or "").strip()
                    if nm and nm not in cur_vals:
                        self.customer_name.configure(values=sorted(set(cur_vals) | {nm}, key=str.upper))
                    # Soft background refresh
                    try:
                        self._refresh_customer_names(show_list=False)
                    except Exception:
                        pass
                else:
                    self.customer_name.configure(values=get_customer_names(self.conn))
            except Exception:
                try:
                    self.customer_name.configure(values=get_customer_names(self.conn))
                except Exception:
                    pass
            self._last_bill_no = bill_no
            self._last_sale_id = sale_id
            self._editing_sale_id = None
            self._autosave_sale_id = None
            self._autosave_token = None
            self._edit_payment_snapshot = None
            # Offline: confirm local SQLite write. Online: sale lives on the server only.
            try:
                from core.sync_prefs import is_online_mode

                online = bool(is_online_mode())
            except Exception:
                online = False
            if not online:
                row = self.conn.execute(
                    "SELECT id FROM sales WHERE id=?",
                    (int(sale_id),),
                ).fetchone()
                if not row:
                    raise ValueError(
                        f"Sale id {sale_id} was not written to the database — try Save again."
                    )
            return bill_no, int(sale_id)
        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to save sale: {e}", parent=self.parent)
            return None
        finally:
            self._autosave_busy = False

    def _please_check(self) -> str:
        """The last save's warnings as a note under its message ("" when there are none)."""
        found = list(getattr(self, '_save_warnings', None) or [])
        if not found:
            return ""
        return "\n\nPlease check:\n• " + "\n• ".join(found)

    def _run_sales_enter_action(self, setting_key: str):
        """Run the configured Enter action (save or print slot) from bill print settings."""
        from core.bill_config import load_bill_print_settings, get_sales_enter_action
        action = get_sales_enter_action(load_bill_print_settings(), setting_key)
        if action == 'print_slot_1':
            self._print_sales_slot(1)
        elif action == 'print_slot_2':
            self._print_sales_slot(2)
        else:
            self.save_sales()

    def _print_sales_slot(self, slot: int):
        if self._pdf_busy:
            return
        saved = self._persist_sale()
        if not saved:
            return
        bill_no, sale_id = saved
        try:
            from core.sync_prefs import is_online_mode

            online = bool(is_online_mode())
        except Exception:
            online = False
        if online:
            from core.page_refresh import refresh_after_sale
            refresh_after_sale(self.parent)
            self.clear_form(focus_customer=False)
            showinfo(
                "Saved",
                f"Bill {bill_no} saved on server.\n"
                "(Print from Sales History after the list refreshes.)"
                + self._please_check(),
                parent=self.parent,
                focus_after=self._focus_customer_name,
            )
            return
        try:
            db_path = self._prepare_bill_pdf_context(sale_id)
        except Exception as exc:
            showerror(
                "Print Bill",
                f"Bill {bill_no} saved but print failed:\n{exc}",
                parent=self.parent,
            )
            return
        self.clear_form(focus_customer=True)
        note = self._please_check()
        if note:
            # The bill is saved and goes to the printer regardless; this only points it out.
            showwarning(
                "Please Check",
                f"Bill {bill_no} was saved and is printing.{note}",
                parent=self.parent,
            )
        self._pdf_busy = True
        hwnd = int(self.parent.winfo_id()) if self.parent else 0

        def _work():
            try:
                print_bill_with_slot(
                    self.conn, sale_id, slot, hwnd_owner=hwnd, db_path=db_path,
                )
            except Exception as exc:
                self.parent.after(
                    0,
                    lambda e=str(exc): (
                        showerror(
                            "Print Bill", f"Bill {bill_no} saved but print failed:\n{e}",
                            parent=self.parent,
                        ),
                        self._focus_customer_name(),
                    ),
                )
            finally:
                self.parent.after(
                    0,
                    lambda: (
                        setattr(self, '_pdf_busy', False),
                        self._focus_customer_name(),
                    ),
                )

        threading.Thread(target=_work, daemon=True, name=f"BillPrint{slot}").start()

    # ── Calculate ─────────────────────────────────────────────────────────

    def calculate_total(self, event=None):
        gst_pcts = [m.get('gst_percent', 0) for m in self.selected_medicines
                    if (m.get('gst_percent') or 0) > 0]
        if gst_pcts:
            unique = list(set(gst_pcts))
            self.gst_percent_var.set(
                f"{unique[0]}% (Included in MRP)" if len(unique) == 1
                else "Mixed GST (Included in MRP)")
        else:
            self.gst_percent_var.set("No GST")

        try:
            disc_rs = float(self.discount.get() or 0)
        except ValueError:
            disc_rs = 0
        try:
            rounding = float(self.rounding.get() or 0)
        except ValueError:
            rounding = 0

        summary = calc_bill_summary(self.selected_medicines, rounding=0, discount_rs=disc_rs)
        # Keep manual rounding after the user edits the Round field (Mac2 desktop parity).
        # Focus-only auto-round was wiping +0.12 when moving to Cash/Online, which then
        # saved a lower total and showed ₹0.12 credit after a full payment.
        if not getattr(self, '_rounding_touched', False):
            rounding = auto_round(summary['pre_round_total'])
            self.rounding.delete(0, tk.END)
            self.rounding.insert(0, f"{rounding:.2f}")

        summary = calc_bill_summary(self.selected_medicines, rounding=rounding, discount_rs=disc_rs)
        self.subtotal_var.set(f"{summary['subtotal']:.2f}")
        self.total_amount_var.set(f"{summary['total_amount']:.2f}")
        if hasattr(self, 'total_margin_var'):
            self.total_margin_var.set(
                format_total_margin_display(self.selected_medicines, disc_rs))

        try:
            if getattr(self, '_is_due_payment', lambda: False)():
                cash = 0.0
                online = 0.0
            else:
                cash   = float(self.cash_paid.get() or 0)
                online = float(self.online_paid.get() or 0)
        except ValueError:
            cash   = 0
            online = 0

        pay = calc_payment_result(summary['total_amount'], cash, online,
                                   self.previous_due, self.previous_credit)
        self.amount_paid_var.set(f"{pay['amount_paid']:.2f}")
        self.due_amount_var.set(f"{pay['due_amount']:.2f}")
        self.total_due_var.set(f"{pay['total_due']:.2f}")

    # ── Generate bill ─────────────────────────────────────────────────────

    def _save_sales_shortcut(self, event=None):
        self.save_sales()
        return 'break'

    def _persist_sale_edit(self) -> tuple[str, int] | None:
        """Save when editing in fullscreen edit window."""
        saved = self._persist_sale()
        return saved

    def save_sales(self):
        if self._pdf_busy:
            return
        if getattr(self, "_save_busy", False):
            return
        self._save_busy = True
        from core.background_workers import run_on_ui_with_busy

        try:
            saved = run_on_ui_with_busy(
                self.parent,
                "Saving Sale",
                self._persist_sale,
                message="Saving bill… please wait.",
            )
        finally:
            self._save_busy = False
        if not saved:
            return
        bill_no, sale_id = saved
        try:
            from core.sync_prefs import is_online_mode

            online = bool(is_online_mode())
        except Exception:
            online = False

        from core.page_refresh import refresh_after_sale
        refresh_after_sale(self.parent)
        if not getattr(self, '_in_edit_window', False):
            self.clear_form(focus_customer=False)
            if hasattr(self, '_doc_tabs') and self._doc_tabs:
                n = self._active_tab_idx + 1
                self._doc_tabs[self._active_tab_idx] = self._empty_tab_state(
                    self._default_tab_label(n),
                )
                self._normalize_default_tab_labels()
                self._refresh_tab_bar()

        if online:
            showinfo(
                "Saved",
                f"Bill {bill_no} saved on server." + self._please_check(),
                parent=self.parent,
                focus_after=self._focus_customer_name,
            )
            return

        try:
            db_path = self._prepare_bill_pdf_context(sale_id)
        except Exception as exc:
            showerror(
                "Save Bill",
                f"Bill saved but PDF failed:\n{exc}",
                parent=self.parent,
            )
            return

        self._pdf_busy = True
        note = self._please_check()

        def _pdf_work():
            try:
                _, pdf_path = save_bill_pdf_only(self.conn, sale_id, db_path=db_path)
                save_dir = os.path.dirname(pdf_path) if pdf_path else ""
                def _notify():
                    if pdf_path:
                        showinfo(
                            "Saved",
                            f"Bill {bill_no} saved.\n\nPDF:\n{pdf_path}" + note,
                            parent=self.parent,
                            focus_after=self._focus_customer_name,
                        )
                    else:
                        showinfo(
                            "Saved",
                            f"Bill {bill_no} saved.\n(PDF could not be created — HTML saved"
                            f"{f' in {save_dir}' if save_dir else ''}. "
                            "Install Edge, Chrome, or Brave for PDF export.)" + note,
                            parent=self.parent,
                            focus_after=self._focus_customer_name,
                        )
                self.parent.after(0, _notify)
            except Exception as exc:
                self.parent.after(
                    0,
                    lambda e=str(exc): (
                        showerror(
                            "Save Bill", f"Bill saved but PDF failed:\n{e}", parent=self.parent,
                        ),
                        self._focus_customer_name(),
                    ),
                )
            finally:
                self.parent.after(0, lambda: setattr(self, '_pdf_busy', False))

        threading.Thread(target=_pdf_work, daemon=True, name="BillPdfSave").start()

    def generate_bill(self):
        """Alias for keyboard registry / legacy callers."""
        return self.save_sales()

    def refresh_print_button_labels(self):
        settings = load_bill_print_settings()
        k1 = get_print_slot_key(settings, 1)
        k2 = get_print_slot_key(settings, 2)
        s1 = settings.get('print_slot_1') or {}
        s2 = settings.get('print_slot_2') or {}
        l1 = s1.get('label') or 'Print Sales 1'
        l2 = s2.get('label') or 'Print Sales 2'
        p1 = (s1.get('paper_size') or 'A5').upper()
        p2 = (s2.get('paper_size') or 'A4').upper()
        c1 = int(s1.get('copies') or 2)
        c2 = int(s2.get('copies') or 1)
        from core.bill_config import BILL_SIZE_DOT_MATRIX, get_bill_size_mode, get_print_slot_settings
        suffix1 = ' · DM' if get_bill_size_mode(get_print_slot_settings(settings, 1)) == BILL_SIZE_DOT_MATRIX else ''
        suffix2 = ' · DM' if get_bill_size_mode(get_print_slot_settings(settings, 2)) == BILL_SIZE_DOT_MATRIX else ''
        if hasattr(self, 'print_sales_1_btn'):
            self.print_sales_1_btn.configure(text=f"{l1} ({k1}) — {p1}×{c1}{suffix1}")
        if hasattr(self, 'print_sales_2_btn'):
            self.print_sales_2_btn.configure(text=f"{l2} ({k2}) — {p2}×{c2}{suffix2}")

    # ── Clear ─────────────────────────────────────────────────────────────

    def _clear_form_shortcut(self, event=None):
        self.clear_form()
        return 'break'

    def _discard_autosave_draft(self):
        sale_id = getattr(self, '_autosave_sale_id', None)
        token = getattr(self, '_autosave_token', None)
        if not sale_id and not token:
            return
        try:
            # Autosave holds a REAL bill now. A counter sale gives back only
            # this form's lines; the day's other counter sales stay put.
            from core.autosave_bill import discard_autosave_bill
            discard_autosave_bill(
                self.conn, token=token or '', sale_id=int(sale_id or 0),
            )
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass

    def clear_form(self, keep_tab=False, *, focus_customer=False):
        self._discard_autosave_draft()
        self.customer_name.set('')
        self.customer_name.configure(values=get_customer_names(self.conn))
        self._customer_id = None
        self.customer_phone.delete(0, tk.END)
        self.customer_address.set('')
        self.doctor_name.set('')
        self.doctor_phone.delete(0, tk.END)
        self._reset_bill_date_today()
        self.clear_medicine_fields()

        self.discount_pct.delete(0, tk.END); self.discount_pct.insert(0, "0")
        self.discount.delete(0, tk.END);    self.discount.insert(0, "0")
        self.rounding.delete(0, tk.END);    self.rounding.insert(0, "0.00")
        self._rounding_touched = False
        self.cash_paid.delete(0, tk.END)
        self.online_paid.delete(0, tk.END)
        self.payment_mode.set('')
        self._on_payment_mode_change()

        self.selected_medicines.clear()
        self._sync_medicine_reserved_stock()
        self.update_medicine_tree()
        self.previous_due    = 0
        self.previous_credit = 0
        self.previous_due_var.set("0.00")
        self.subtotal_var.set("0.00")
        self.gst_percent_var.set("Included in MRP")
        self.total_amount_var.set("0.00")
        self.due_amount_var.set("0.00")
        self.total_due_var.set("0.00")

        # After Save/Print, always land on Customer for the next bill.
        if focus_customer:
            self._focus_customer_name()
        else:
            self._focus_first_field()
        self._editing_sale_id = None
        self._autosave_sale_id = None
        self._autosave_token = None
        self._edit_payment_snapshot = None
        if not keep_tab and hasattr(self, '_doc_tabs') and self._doc_tabs:
            n = self._active_tab_idx + 1
            self._doc_tabs[self._active_tab_idx] = self._empty_tab_state(
                self._default_tab_label(n),
            )
            self._normalize_default_tab_labels()
            self._refresh_tab_bar()
        self._reset_persistent()

    def _reset_persistent(self):
        try:
            root = self.parent.winfo_toplevel()
            for child in root.winfo_children():
                if hasattr(child, '_billing_page') and child._billing_page is self:
                    child._billing_page = None
                    break
        except Exception:
            pass
