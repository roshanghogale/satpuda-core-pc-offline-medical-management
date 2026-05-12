import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno

from core.calc_engine import calc_bill_summary, calc_payment_result, auto_round
from core.customer_service import get_or_create_customer, get_customer_names
from core.billing_service import save_new_bill
from widgets.bill_preview import show_bill_preview
from ui.billing.billing_nav  import BillingNavMixin
from ui.billing.billing_form import BillingFormMixin


class BillingPage(BillingNavMixin, BillingFormMixin):

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

        self._build_interface()
        self._bind_shortcuts()
        self.parent.after(150, self.customer_name.focus)

    # ── Shortcuts ─────────────────────────────────────────────────────────

    def _bind_shortcuts(self):
        root = self.parent.winfo_toplevel()
        root.bind('<F5>',        lambda e: self.generate_bill())
        root.bind('<Control-g>', lambda e: self.generate_bill())
        root.bind('<Control-G>', lambda e: self.generate_bill())
        root.bind('<Control-p>', lambda e: self._print_last_bill())
        root.bind('<Control-P>', lambda e: self._print_last_bill())
        root.bind('<F6>',        lambda e: self.cash_paid.focus())
        self.parent.after(100, self._setup_arrow_nav)

    def _rebind_mousewheel(self):
        pass  # scroll_manager handles this

    def _print_last_bill(self):
        if self._last_bill_no and self._last_sale_id:
            show_bill_preview(self.parent, self.conn,
                              self._last_bill_no, self._last_sale_id)
        else:
            showinfo("No Bill", "No bill generated yet in this session.", parent=self.parent)

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

        # Auto-rounding when field not focused
        summary = calc_bill_summary(self.selected_medicines, rounding=0, discount_rs=disc_rs)
        try:
            rounding_focused = (self.rounding == self.rounding.winfo_toplevel().focus_get())
        except Exception:
            rounding_focused = False
        if not rounding_focused:
            rounding = auto_round(summary['pre_round_total'])
            self.rounding.delete(0, tk.END)
            self.rounding.insert(0, f"{rounding:.2f}")

        summary = calc_bill_summary(self.selected_medicines, rounding=rounding, discount_rs=disc_rs)
        self.subtotal_var.set(f"{summary['subtotal']:.2f}")
        self.total_amount_var.set(f"{summary['total_amount']:.2f}")

        try:
            cash   = float(self.cash_paid.get() or 0)
        except ValueError:
            cash   = 0
        try:
            online = float(self.online_paid.get() or 0)
        except ValueError:
            online = 0

        pay = calc_payment_result(summary['total_amount'], cash, online,
                                   self.previous_due, self.previous_credit)
        self.amount_paid_var.set(f"{pay['amount_paid']:.2f}")
        self.due_amount_var.set(f"{pay['due_amount']:.2f}")
        self.total_due_var.set(f"{pay['due_amount']:.2f}")

    # ── Generate bill ─────────────────────────────────────────────────────

    def generate_bill(self):
        self.cursor.execute("SELECT * FROM pharmacy_profile LIMIT 1")
        if not self.cursor.fetchone():
            showwarning("Setup Required",
                        "Please set up pharmacy profile in Settings first.", parent=self.parent)
            return
        if not self.customer_name.get().strip():
            showwarning("Missing Information", "Please enter customer name.", parent=self.parent)
            return
        if not self.selected_medicines:
            showwarning("No Medicines", "Please add medicines to the bill.", parent=self.parent)
            return

        try:
            customer_id = get_or_create_customer(
                self.conn,
                self.customer_name.get().strip(),
                self.customer_phone.get().strip(),
                self.customer_address.get().strip(),
            )
            disc_rs  = float(self.discount.get() or 0)
            disc_pct = float(self.discount_pct.get() or 0)
            rounding = float(self.rounding.get() or 0)
            cash     = float(self.cash_paid.get() or 0)
            online   = float(self.online_paid.get() or 0)

            bill_no, sale_id = save_new_bill(
                conn         = self.conn,
                customer_id  = customer_id,
                medicines    = self.selected_medicines,
                discount_pct = disc_pct,
                discount_rs  = disc_rs,
                rounding     = rounding,
                cash_paid    = cash,
                online_paid  = online,
                doctor_name  = self.doctor_name.get(),
                doctor_phone = self.doctor_phone.get().strip(),
                previous_due = self.previous_due,
            )

            self.customer_name.configure(values=get_customer_names(self.conn))
            self._last_bill_no = bill_no
            self._last_sale_id = sale_id
            showinfo("Success",
                     f"Bill {bill_no} generated!  [F5=New Bill | Ctrl+P=Print]",
                     parent=self.parent)
            show_bill_preview(self.parent, self.conn, bill_no, sale_id)
            self.clear_form()

        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to generate bill: {e}", parent=self.parent)

    # ── Clear ─────────────────────────────────────────────────────────────

    def clear_form(self):
        self.customer_name.set('')
        self._customer_id = None
        self.customer_phone.delete(0, tk.END)
        self.customer_address.delete(0, tk.END)
        self.doctor_name.set('')
        self.doctor_phone.delete(0, tk.END)
        self.clear_medicine_fields()

        self.discount_pct.delete(0, tk.END); self.discount_pct.insert(0, "0")
        self.discount.delete(0, tk.END);    self.discount.insert(0, "0")
        self.rounding.delete(0, tk.END);    self.rounding.insert(0, "0.00")
        self.cash_paid.delete(0, tk.END)
        self.online_paid.delete(0, tk.END)

        self.selected_medicines.clear()
        self.update_medicine_tree()
        self.previous_due    = 0
        self.previous_credit = 0
        self.previous_due_var.set("0.00")
        self.subtotal_var.set("0.00")
        self.gst_percent_var.set("Included in MRP")
        self.total_amount_var.set("0.00")
        self.due_amount_var.set("0.00")
        self.total_due_var.set("0.00")

        self.customer_name.focus()
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
