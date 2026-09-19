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
from widgets.searchable_combo import SearchableCombo
from core.record_indicators import (
    extend_columns,
    indicator_column_widths,
    column_heading,
    ledger_status,
    prepare_tree_row,
    register_tree_tags,
)


def _ledger_insert(tree, ledger_tag, values, *, return_kind='sales_return'):
    if ledger_tag == 'return':
        status = return_kind
    else:
        status = ledger_status(ledger_tag)
    row_vals, tags = prepare_tree_row(values, status)
    tree.insert('', tk.END, values=row_vals, tags=tags)


# -- Shared running-balance engine ---------------------------------------------

def _calc_due_credit(running):
    if running > 0:  return round(running, 2), 0.0
    elif running < 0: return 0.0, round(abs(running), 2)
    return 0.0, 0.0


def _build_ledger_tree(parent, cols, col_widths):
    lf = ttk.LabelFrame(parent, text="")
    lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))
    ext_cols = extend_columns(cols)
    widths = dict(col_widths)
    widths.update(indicator_column_widths())
    tree = ttk.Treeview(lf, columns=ext_cols, show='headings',
                        height=12, style='Large.Treeview')
    for c in ext_cols:
        heading = column_heading(c)
        tree.heading(c, text=heading)
        tree.column(c, width=widths.get(c, 90), anchor='e')
    for c in ('Date', 'Type', 'Reference', 'Details', 'Bill No'):
        if c in ext_cols:
            tree.column(c, anchor='center' if c == 'Date' else 'w')
    vsb = ttk.Scrollbar(lf, orient=tk.VERTICAL,   command=tree.yview)
    hsb = ttk.Scrollbar(lf, orient=tk.HORIZONTAL, command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky='nsew')
    vsb.grid(row=0, column=1, sticky='ns')
    hsb.grid(row=1, column=0, sticky='ew')
    lf.grid_rowconfigure(0, weight=1)
    lf.grid_columnconfigure(0, weight=1)
    register_tree_tags(tree)
    return lf, tree


def _tag_for_running(running):
    if running > 0:  return 'due'
    if running < 0:  return 'credit'
    return 'clear'


def _fnum(v, default=0.0):
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


def _in_range(d, fd, td):
    ds = str(d or "")
    if fd and ds < fd:
        return False
    if td and ds and ds > td:
        return False
    return True


def _online_supplier_ledger(name, fd, td):
    """Assemble supplier ledger from store list + payment endpoints."""
    from core import store_query_client as sq
    from core.online_catalog import find_supplier_by_name, supplier_names
    from core.due_fifo import purchase_entry_paid

    found = find_supplier_by_name(name)
    try:
        sid = int((found or {}).get("id") or (found or {}).get("local_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    name_u = (name or "").strip().upper()

    purchases_raw = (sq.list_purchases(q=name, limit=5000) or {}).get("rows") or []
    payments_raw = (sq.list_supplier_payments(limit=5000) or {}).get("rows") or []
    returns_raw = []
    try:
        returns_raw = (sq.list_purchase_returns(limit=5000) or {}).get("rows") or []
    except Exception:
        returns_raw = []

    def _match_sup(r):
        if not isinstance(r, dict):
            return False
        try:
            if sid and int(r.get("supplier_id") or 0) == sid:
                return True
        except (TypeError, ValueError):
            pass
        sn = str(r.get("supplier_name") or r.get("party") or "").strip().upper()
        return bool(name_u) and sn == name_u

    all_txns = []
    opening = 0.0
    for r in purchases_raw:
        if not _match_sup(r):
            continue
        d = str(r.get("purchase_date") or "")
        amt = _fnum(r.get("final_amount")) or _fnum(r.get("total_amount"))
        paid = purchase_entry_paid(r)
        ref = str(r.get("purchase_no") or "")
        details = str(r.get("bill_number") or "") or ref
        if fd and d and d < fd:
            opening = round(opening + amt - paid, 2)
            continue
        if not _in_range(d, fd, td):
            continue
        all_txns.append((d, ref, details, amt, paid, "purchase"))

    for r in payments_raw:
        if not _match_sup(r):
            continue
        d = str(r.get("payment_date") or r.get("date") or "")
        amt = _fnum(r.get("amount"))
        ref = str(r.get("payment_no") or r.get("id") or "")
        details = str(r.get("mode") or "Cash")
        if fd and d and d < fd:
            opening = round(opening - amt, 2)
            continue
        if not _in_range(d, fd, td):
            continue
        all_txns.append((d, ref, details, amt, 0.0, "payment"))

    for r in returns_raw:
        if not _match_sup(r):
            continue
        d = str(r.get("return_date") or "")
        amt = _fnum(r.get("refund_amount"))
        ref = str(r.get("return_no") or "")
        details = str(r.get("bill_number") or r.get("purchase_no") or "")
        if fd and d and d < fd:
            opening = round(opening - amt, 2)
            continue
        if not _in_range(d, fd, td):
            continue
        all_txns.append((d, ref, details, amt, 0.0, "return"))

    all_txns.sort(key=lambda x: x[0] or "")
    return sid, opening, all_txns, supplier_names()


def _online_customer_ledger(name, fd, td):
    """Assemble customer ledger from store list + payment endpoints."""
    from core import store_query_client as sq
    from core.online_catalog import customer_names, find_customer_by_name

    found = find_customer_by_name(name)
    try:
        cid = int((found or {}).get("id") or 0)
    except (TypeError, ValueError):
        cid = 0
    name_u = (name or "").strip().upper()

    sales_raw = (sq.list_sales(q=name, limit=5000) or {}).get("rows") or []
    payments_raw = (sq.list_customer_payments(limit=5000) or {}).get("rows") or []
    returns_raw = []
    try:
        returns_raw = (sq.list_sales_returns(limit=5000) or {}).get("rows") or []
    except Exception:
        returns_raw = []

    def _match_cust(r):
        if not isinstance(r, dict):
            return False
        try:
            if cid and int(r.get("customer_id") or 0) == cid:
                return True
        except (TypeError, ValueError):
            pass
        cn = str(r.get("customer_name") or r.get("party") or "").strip().upper()
        return bool(name_u) and cn == name_u

    all_txns = []
    opening = 0.0
    for r in sales_raw:
        if not _match_cust(r):
            continue
        d = str(r.get("bill_date") or "")
        amt = _fnum(r.get("total_amount"))
        paid = _fnum(r.get("amount_paid"))
        ref = str(r.get("bill_no") or "")
        details = str(r.get("doctor_name") or "")
        if fd and d and d < fd:
            opening = round(opening + amt - paid, 2)
            continue
        if not _in_range(d, fd, td):
            continue
        all_txns.append((d, ref, details, amt, paid, "sale"))

    for r in returns_raw:
        if not _match_cust(r):
            continue
        d = str(r.get("return_date") or "")
        amt = _fnum(r.get("refund_amount"))
        ref = str(r.get("return_no") or "")
        details = str(r.get("bill_no") or "")
        if fd and d and d < fd:
            opening = round(opening - amt, 2)
            continue
        if not _in_range(d, fd, td):
            continue
        all_txns.append((d, ref, details, amt, 0.0, "return"))

    for r in payments_raw:
        if not _match_cust(r):
            continue
        d = str(r.get("payment_date") or r.get("date") or "")
        amt = _fnum(r.get("amount"))
        if amt == 0.0:
            amt = _fnum(r.get("cash_amount")) + _fnum(r.get("online_amount"))
        ref = f"PAY-{r.get('id')}" if r.get("id") is not None else "PAY"
        details = str(r.get("payment_mode") or r.get("mode") or "cash")
        if fd and d and d < fd:
            opening = round(opening - amt, 2)
            continue
        if not _in_range(d, fd, td):
            continue
        all_txns.append((d, ref, details, amt, 0.0, "payment"))

    all_txns.sort(key=lambda x: x[0] or "")
    return cid, opening, all_txns, customer_names(include_from_sales=False)


# -- Main class ----------------------------------------------------------------

class LedgerTab:
    TAB_NAME = 'Ledger'

    def __init__(self, notebook, conn, host=None):
        self.conn   = conn
        self.cursor = conn.cursor()
        self._kind = 'supplier'
        outer = host if host is not None else ttk.Frame(notebook)
        self.outer = outer
        if host is None and notebook is not None:
            notebook.add(outer, text=self.TAB_NAME)
        self._build(outer)

    def get_keyboard_bindings(self):
        from core.keyboard_registry import PageBindings
        return PageBindings(
            page_id='ledger',
            sub_keys={'s': lambda: self._show('supplier'), 'c': lambda: self._show('customer')},
            f2_target=getattr(self, '_ledger_tree', None),
        )

    def _build(self, outer):
        btn_bar = ttk.Frame(outer)
        btn_bar.pack(fill=tk.X, padx=10, pady=10)
        try:
            self._btn_supplier = ttk.Button(
                btn_bar, text="Supplier Ledger",
                command=lambda: self._show('supplier'),
                bootstyle="primary", width=22)
            self._btn_customer = ttk.Button(
                btn_bar, text="Customer Ledger",
                command=lambda: self._show('customer'),
                bootstyle="success", width=22)
        except Exception:
            self._btn_supplier = ttk.Button(
                btn_bar, text="Supplier Ledger",
                command=lambda: self._show('supplier'), width=22)
            self._btn_customer = ttk.Button(
                btn_bar, text="Customer Ledger",
                command=lambda: self._show('customer'), width=22)
        self._btn_supplier.pack(side=tk.LEFT, padx=8)
        self._btn_customer.pack(side=tk.LEFT, padx=8)
        self._container = ttk.Frame(outer)
        self._container.pack(fill=tk.BOTH, expand=True)
        self._show('supplier')

    def _show(self, kind):
        self._kind = kind
        for w in self._container.winfo_children():
            w.destroy()
        if kind == 'supplier':
            self._build_supplier(self._container)
            try:
                self._btn_supplier.configure(bootstyle='primary')
                self._btn_customer.configure(bootstyle='secondary')
            except Exception:
                pass
        else:
            self._build_customer(self._container)
            try:
                self._btn_customer.configure(bootstyle='success')
                self._btn_supplier.configure(bootstyle='secondary')
            except Exception:
                pass
        refresh = getattr(self, '_keyboard_refresh', None)
        if callable(refresh):
            refresh()

    # -- Supplier Ledger -------------------------------------------------------

    def _build_supplier(self, parent):
        sf = ttk.LabelFrame(parent, text="Supplier Ledger - Bank Statement")
        sf.pack(fill=tk.X, padx=10, pady=(8, 4))

        ttk.Label(sf, text="Supplier ID:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=5)
        sl_id_var = tk.StringVar()
        ttk.Label(sf, textvariable=sl_id_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'), width=12).grid(
            row=0, column=1, sticky=tk.W, padx=8, pady=5)

        ttk.Label(sf, text="Supplier Name:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=5)
        sl_name = SearchableCombo(sf, width=28)
        sl_name.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=8, pady=5)

        fy_start = (date.today().replace(month=4, day=1) if date.today().month >= 4
                    else date(date.today().year - 1, 4, 1))

        ttk.Label(sf, text="From:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=5)
        sl_from = ttk.Entry(sf, width=14)
        sl_from.grid(row=2, column=1, sticky=tk.W, padx=8, pady=5)
        sl_from.insert(0, fy_start.strftime('%Y-%m-%d'))

        ttk.Label(sf, text="To:").grid(row=2, column=2, sticky=tk.W, padx=8, pady=5)
        sl_to = ttk.Entry(sf, width=14)
        sl_to.grid(row=2, column=3, sticky=tk.W, padx=8, pady=5)
        sl_to.insert(0, date.today().strftime('%Y-%m-%d'))

        btn_f = ttk.Frame(sf)
        btn_f.grid(row=3, column=0, columnspan=5, pady=6, padx=8, sticky=tk.W)

        cols = ('Date', 'Type', 'Reference', 'Details',
                'Amount', 'Paid', 'Due', 'Credit', 'Running Balance')
        cw   = {'Date': 95, 'Type': 90, 'Reference': 120, 'Details': 110,
                'Amount': 110, 'Paid': 90, 'Due': 90, 'Credit': 90,
                'Running Balance': 120}
        _, tree = _build_ledger_tree(parent, cols, cw)
        self._ledger_tree = tree

        summary = ttk.Frame(parent)
        summary.pack(fill=tk.X, padx=10, pady=(0, 6))
        total_pur_var    = tk.StringVar(value="Total Purchase: Rs.0.00")
        total_paid_var   = tk.StringVar(value="Total Paid: Rs.0.00")
        total_return_var = tk.StringVar(value="Total Returns: Rs.0.00")
        balance_var      = tk.StringVar(value="Balance Due: Rs.0.00")
        bal_label = None
        for var, color in [
            (total_pur_var,    None),
            (total_paid_var,   get_alert_color('success')),
            (total_return_var, get_alert_color('info')),
            (balance_var,      get_alert_color('danger')),
        ]:
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color: kw['foreground'] = color
            lbl = ttk.Label(summary, textvariable=var, **kw)
            lbl.pack(side=tk.LEFT, padx=15)
            if var is balance_var:
                bal_label = lbl

        def reload():
            last = getattr(self, "_last_ledger_supplier_names", None) or []
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    if last:
                        try:
                            sl_name.configure(values=last)
                        except Exception:
                            pass
                    if getattr(self, "_ledger_sup_reload_busy", False):
                        return
                    self._ledger_sup_reload_busy = True
                    from core.background_workers import run_in_thread
                    try:
                        from core.online_catalog import prefetch_hot
                        prefetch_hot()
                    except Exception:
                        pass

                    def _load():
                        from core.online_catalog import supplier_names
                        return list(supplier_names() or [])

                    def _done(names):
                        self._ledger_sup_reload_busy = False
                        names = list(names or [])
                        if names:
                            self._last_ledger_supplier_names = names
                        try:
                            sl_name.configure(
                                values=getattr(self, "_last_ledger_supplier_names", None) or names
                            )
                        except Exception:
                            pass

                    def _err(_exc):
                        self._ledger_sup_reload_busy = False
                        kept = getattr(self, "_last_ledger_supplier_names", None) or []
                        if kept:
                            try:
                                sl_name.configure(values=kept)
                            except Exception:
                                pass

                    run_in_thread(
                        _load,
                        name="LedgerSuppliersReload",
                        root=parent,
                        on_success=_done,
                        on_error=_err,
                    )
                    return
            except Exception:
                try:
                    from core.sync_prefs import is_online_mode as _online
                    if _online():
                        if last:
                            sl_name.configure(values=last)
                        return
                except Exception:
                    pass
            self.cursor.execute("SELECT name FROM suppliers ORDER BY name")
            sl_name.configure(values=[r[0] for r in self.cursor.fetchall()])

        def on_select(event=None):
            name = sl_name.get().strip()
            if not name: return
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    from core.online_catalog import find_supplier_by_name

                    found = find_supplier_by_name(name)
                    if found:
                        try:
                            sid = int(found.get("id") or found.get("local_id") or 0)
                            if sid:
                                sl_id_var.set(f"S-{sid:04d}")
                        except (TypeError, ValueError):
                            pass
                    sl_to.focus()
                    return
            except Exception:
                pass
            self.cursor.execute("SELECT id FROM suppliers WHERE name=? LIMIT 1", (name,))
            row = self.cursor.fetchone()
            if row: sl_id_var.set(f"S-{row[0]:04d}")
            sl_to.focus()

        def view():
            name = sl_name.get().strip()
            if not name:
                showwarning("No Supplier", "Please select a supplier.")
                return
            for item in tree.get_children(): tree.delete(item)
            fd, td = sl_from.get().strip(), sl_to.get().strip()

            online = False
            try:
                from core.sync_prefs import is_online_mode
                online = is_online_mode()
            except Exception:
                online = False

            if online:
                try:
                    sid, opening, all_txns, _names = _online_supplier_ledger(name, fd, td)
                    if sid:
                        sl_id_var.set(f"S-{sid:04d}")
                except Exception as e:
                    showerror("Error", f"Failed to load supplier ledger: {e}")
                    return
            else:
                self.cursor.execute("""
                    SELECT p.purchase_date, p.purchase_no,
                           COALESCE(p.bill_number, ''),
                           p.total_amount,
                           COALESCE(p.amount_paid_at_entry, p.amount_paid, 0),
                           'purchase'
                    FROM purchases p JOIN suppliers s ON p.supplier_id = s.id
                    WHERE s.name = ?
                      AND (? = '' OR p.purchase_date >= ?)
                      AND (? = '' OR p.purchase_date <= ?)
                    ORDER BY p.purchase_date ASC, p.id ASC
                """, (name, fd, fd, td, td))
                purchases = self.cursor.fetchall()

                self.cursor.execute("""
                    SELECT sp.payment_date, sp.payment_no,
                           COALESCE(sp.mode, 'Cash'), sp.amount, 'payment'
                    FROM supplier_payments sp JOIN suppliers s ON sp.supplier_id = s.id
                    WHERE s.name = ?
                      AND (? = '' OR sp.payment_date >= ?)
                      AND (? = '' OR sp.payment_date <= ?)
                    ORDER BY sp.payment_date ASC, sp.id ASC
                """, (name, fd, fd, td, td))
                payments = self.cursor.fetchall()

                returns = []
                try:
                    self.cursor.execute("""
                        SELECT pr.return_date, pr.return_no,
                               COALESCE(p.bill_number, p.purchase_no, ''),
                               pr.refund_amount, 'return'
                        FROM purchase_returns pr
                        JOIN purchases p ON pr.purchase_id = p.id
                        JOIN suppliers s ON p.supplier_id  = s.id
                        WHERE s.name = ?
                          AND (? = '' OR pr.return_date >= ?)
                          AND (? = '' OR pr.return_date <= ?)
                        ORDER BY pr.return_date ASC, pr.id ASC
                    """, (name, fd, fd, td, td))
                    returns = self.cursor.fetchall()
                except Exception:
                    pass

                # PHASE 5.2: compute opening balance from transactions BEFORE fd
                opening = 0.0
                if fd:
                    self.cursor.execute("""
                        SELECT COALESCE(SUM(p.total_amount),0),
                               COALESCE(SUM(COALESCE(p.amount_paid_at_entry,p.amount_paid,0)),0)
                        FROM purchases p JOIN suppliers s ON p.supplier_id=s.id
                        WHERE s.name=? AND p.purchase_date < ?
                    """, (name, fd))
                    r = self.cursor.fetchone()
                    opening = round(float(r[0] or 0) - float(r[1] or 0), 2)
                    try:
                        self.cursor.execute("""
                            SELECT COALESCE(SUM(sp.amount),0)
                            FROM supplier_payments sp JOIN suppliers s ON sp.supplier_id=s.id
                            WHERE s.name=? AND sp.payment_date < ?
                        """, (name, fd))
                        opening = round(opening - float(self.cursor.fetchone()[0] or 0), 2)
                        self.cursor.execute("""
                            SELECT COALESCE(SUM(pr.refund_amount),0)
                            FROM purchase_returns pr
                            JOIN purchases p ON pr.purchase_id=p.id
                            JOIN suppliers s ON p.supplier_id=s.id
                            WHERE s.name=? AND pr.return_date < ?
                        """, (name, fd))
                        opening = round(opening - float(self.cursor.fetchone()[0] or 0), 2)
                    except Exception:
                        pass

                all_txns = []
                for r in purchases:
                    all_txns.append((r[0], r[1], r[2], float(r[3] or 0), float(r[4] or 0), 'purchase'))
                for r in payments:
                    all_txns.append((r[0], r[1], r[2], float(r[3] or 0), 0.0, 'payment'))
                for r in returns:
                    all_txns.append((r[0], r[1], r[2], float(r[3] or 0), 0.0, 'return'))
                all_txns.sort(key=lambda x: x[0])

            running = opening
            total_purchase = total_paid = total_returns = 0.0

            if opening != 0.0:
                due_ob, credit_ob = _calc_due_credit(opening)
                _ledger_insert(tree, _tag_for_running(opening), (
                    fd or '-', 'Opening Balance', '', '',
                    '-', '-',
                    f"Rs.{due_ob:.2f}" if due_ob else '-',
                    f"Rs.{credit_ob:.2f}" if credit_ob else '-',
                    f"Rs.{opening:.2f}",
                ))

            for txn_date, ref, details, amt, paid_at_txn, txn_type in all_txns:
                if txn_type == 'purchase':
                    running = round(running + amt - paid_at_txn, 2)
                    due, credit = _calc_due_credit(running)
                    total_purchase += amt
                    total_paid     += paid_at_txn
                    _ledger_insert(tree, _tag_for_running(running), (
                        txn_date, 'Purchase', ref, details,
                        f"Rs.{amt:.2f}", f"Rs.{paid_at_txn:.2f}" if paid_at_txn else "-",
                        f"Rs.{due:.2f}" if due else "-",
                        f"Rs.{credit:.2f}" if credit else "-",
                        f"Rs.{running:.2f}",
                    ))
                elif txn_type == 'payment':
                    running = round(running - amt, 2)
                    due, credit = _calc_due_credit(running)
                    total_paid += amt
                    _ledger_insert(tree, 'payment', (
                        txn_date, 'Payment', ref, details,
                        "-", f"Rs.{amt:.2f}",
                        f"Rs.{due:.2f}" if due else "-",
                        f"Rs.{credit:.2f}" if credit else "-",
                        f"Rs.{running:.2f}",
                    ))
                elif txn_type == 'return':
                    running = round(running - amt, 2)
                    due, credit = _calc_due_credit(running)
                    total_returns += amt
                    _ledger_insert(tree, 'return', (
                        txn_date, 'Return', ref, details,
                        f"Rs.{amt:.2f}", "-",
                        f"Rs.{due:.2f}" if due else "-",
                        f"Rs.{credit:.2f}" if credit else "-",
                        f"Rs.{running:.2f}",
                    ), return_kind='purchase_return')

            expected = round(opening + total_purchase - total_paid - total_returns, 2)
            if abs(running - expected) > 0.01:
                print(f"[LEDGER WARN] Supplier '{name}': running={running} expected={expected}")

            total_pur_var.set(f"Total Purchase: Rs.{total_purchase:.2f}")
            total_paid_var.set(f"Total Paid: Rs.{total_paid:.2f}")
            total_return_var.set(f"Total Returns: Rs.{total_returns:.2f}")
            balance_var.set(f"Balance Due: Rs.{running:.2f}")
            if bal_label:
                try:
                    bal_label.configure(
                        foreground=get_alert_color('danger') if running > 0
                        else get_alert_color('success'))
                except Exception:
                    pass

        def reset():
            sl_name.set(''); sl_id_var.set('')
            sl_from.delete(0, tk.END); sl_from.insert(0, fy_start.strftime('%Y-%m-%d'))
            sl_to.delete(0, tk.END);   sl_to.insert(0, date.today().strftime('%Y-%m-%d'))
            for item in tree.get_children(): tree.delete(item)
            total_pur_var.set("Total Purchase: Rs.0.00")
            total_paid_var.set("Total Paid: Rs.0.00")
            total_return_var.set("Total Returns: Rs.0.00")
            balance_var.set("Balance Due: Rs.0.00")
            sl_name.focus()

        sl_name.entry.bind('<FocusIn>', lambda e: reload(), add='+')
        sl_name.bind('<<ComboboxSelected>>', on_select)
        sl_name.next_focus_widget = lambda: on_select()
        sl_from.bind('<Return>', lambda e: sl_to.focus())
        sl_to.bind('<Return>',   lambda e: view())
        try:
            ttk.Button(btn_f, text="View Report", command=view,
                       bootstyle="primary").pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_f, text="Reset", command=reset,
                       bootstyle="secondary").pack(side=tk.LEFT, padx=6)
        except Exception:
            ttk.Button(btn_f, text="View Report", command=view).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_f, text="Reset",        command=reset).pack(side=tk.LEFT, padx=6)
        reload()

    # -- Customer Ledger -------------------------------------------------------

    def _build_customer(self, parent):
        sf = ttk.LabelFrame(parent, text="Customer Ledger - Bank Statement")
        sf.pack(fill=tk.X, padx=10, pady=(8, 4))

        ttk.Label(sf, text="Customer ID:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=5)
        cl_id_var = tk.StringVar()
        ttk.Label(sf, textvariable=cl_id_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'), width=12).grid(
            row=0, column=1, sticky=tk.W, padx=8, pady=5)

        ttk.Label(sf, text="Customer Name:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=5)
        cl_name = SearchableCombo(sf, width=28)
        cl_name.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=8, pady=5)

        fy_start = (date.today().replace(month=4, day=1) if date.today().month >= 4
                    else date(date.today().year - 1, 4, 1))

        ttk.Label(sf, text="From:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=5)
        cl_from = ttk.Entry(sf, width=14)
        cl_from.grid(row=2, column=1, sticky=tk.W, padx=8, pady=5)
        cl_from.insert(0, fy_start.strftime('%Y-%m-%d'))

        ttk.Label(sf, text="To:").grid(row=2, column=2, sticky=tk.W, padx=8, pady=5)
        cl_to = ttk.Entry(sf, width=14)
        cl_to.grid(row=2, column=3, sticky=tk.W, padx=8, pady=5)
        cl_to.insert(0, date.today().strftime('%Y-%m-%d'))

        btn_f = ttk.Frame(sf)
        btn_f.grid(row=3, column=0, columnspan=5, pady=6, padx=8, sticky=tk.W)

        cols = ('Date', 'Type', 'Reference', 'Details',
                'Amount', 'Paid', 'Due', 'Credit', 'Running Balance')
        cw   = {'Date': 95, 'Type': 90, 'Reference': 120, 'Details': 110,
                'Amount': 110, 'Paid': 90, 'Due': 90, 'Credit': 90,
                'Running Balance': 120}
        _, tree = _build_ledger_tree(parent, cols, cw)
        self._ledger_tree = tree

        summary = ttk.Frame(parent)
        summary.pack(fill=tk.X, padx=10, pady=(0, 6))
        total_bill_var   = tk.StringVar(value="Total Billed: Rs.0.00")
        total_paid_var   = tk.StringVar(value="Total Paid: Rs.0.00")
        total_return_var = tk.StringVar(value="Total Returns: Rs.0.00")
        balance_var      = tk.StringVar(value="Balance Due: Rs.0.00")
        bal_label = None
        for var, color in [
            (total_bill_var,   None),
            (total_paid_var,   get_alert_color('success')),
            (total_return_var, get_alert_color('info')),
            (balance_var,      get_alert_color('danger')),
        ]:
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color: kw['foreground'] = color
            lbl = ttk.Label(summary, textvariable=var, **kw)
            lbl.pack(side=tk.LEFT, padx=15)
            if var is balance_var:
                bal_label = lbl

        def reload():
            last = getattr(self, "_last_ledger_customer_names", None) or []
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    if last:
                        try:
                            cl_name.configure(values=last)
                        except Exception:
                            pass
                    if getattr(self, "_ledger_cust_reload_busy", False):
                        return
                    self._ledger_cust_reload_busy = True
                    from core.background_workers import run_in_thread
                    try:
                        from core.online_catalog import prefetch_hot
                        prefetch_hot()
                    except Exception:
                        pass

                    def _load():
                        from core.online_catalog import customer_names
                        return list(customer_names(include_from_sales=False) or [])

                    def _done(names):
                        self._ledger_cust_reload_busy = False
                        names = list(names or [])
                        if names:
                            self._last_ledger_customer_names = names
                        try:
                            cl_name.configure(
                                values=getattr(self, "_last_ledger_customer_names", None) or names
                            )
                        except Exception:
                            pass

                    def _err(_exc):
                        self._ledger_cust_reload_busy = False
                        kept = getattr(self, "_last_ledger_customer_names", None) or []
                        if kept:
                            try:
                                cl_name.configure(values=kept)
                            except Exception:
                                pass

                    run_in_thread(
                        _load,
                        name="LedgerCustomersReload",
                        root=parent,
                        on_success=_done,
                        on_error=_err,
                    )
                    return
            except Exception:
                try:
                    from core.sync_prefs import is_online_mode as _online
                    if _online():
                        if last:
                            cl_name.configure(values=last)
                        return
                except Exception:
                    pass
            self.cursor.execute("SELECT name FROM customers ORDER BY name")
            cl_name.configure(values=[r[0] for r in self.cursor.fetchall()])

        def on_select(event=None):
            name = cl_name.get().strip()
            if not name: return
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    from core.online_catalog import find_customer_by_name

                    found = find_customer_by_name(name)
                    if found:
                        try:
                            cid = int(found.get("id") or 0)
                            if cid:
                                cl_id_var.set(f"C-{cid:04d}")
                        except (TypeError, ValueError):
                            pass
                    cl_to.focus()
                    return
            except Exception:
                pass
            self.cursor.execute(
                "SELECT id FROM customers WHERE UPPER(name)=UPPER(?) LIMIT 1", (name,))
            row = self.cursor.fetchone()
            if row: cl_id_var.set(f"C-{row[0]:04d}")
            cl_to.focus()

        def view():
            name = cl_name.get().strip()
            if not name:
                showwarning("No Customer", "Please select a customer.")
                return
            for item in tree.get_children(): tree.delete(item)
            fd, td = cl_from.get().strip(), cl_to.get().strip()

            online = False
            try:
                from core.sync_prefs import is_online_mode
                online = is_online_mode()
            except Exception:
                online = False

            if online:
                try:
                    cid, opening, all_txns, _names = _online_customer_ledger(name, fd, td)
                    if cid:
                        cl_id_var.set(f"C-{cid:04d}")
                except Exception as e:
                    showerror("Error", f"Failed to load customer ledger: {e}")
                    return
            else:
                # 1. Sales
                self.cursor.execute("""
                    SELECT s.bill_date, s.bill_no,
                           COALESCE(s.doctor_name, ''),
                           s.total_amount, COALESCE(s.amount_paid, 0), 'sale'
                    FROM sales s JOIN customers c ON s.customer_id = c.id
                    WHERE UPPER(c.name) = UPPER(?)
                      AND (? = '' OR s.bill_date >= ?)
                      AND (? = '' OR s.bill_date <= ?)
                    ORDER BY s.bill_date ASC, s.id ASC
                """, (name, fd, fd, td, td))
                sales = self.cursor.fetchall()

                # 2. Sales Returns
                returns = []
                try:
                    self.cursor.execute("""
                        SELECT sr.return_date, sr.return_no,
                               COALESCE(s.bill_no, ''),
                               sr.refund_amount, 'return'
                        FROM sales_returns sr
                        JOIN sales s     ON sr.sale_id     = s.id
                        JOIN customers c ON sr.customer_id = c.id
                        WHERE UPPER(c.name) = UPPER(?)
                          AND (? = '' OR sr.return_date >= ?)
                          AND (? = '' OR sr.return_date <= ?)
                        ORDER BY sr.return_date ASC, sr.id ASC
                    """, (name, fd, fd, td, td))
                    returns = self.cursor.fetchall()
                except Exception:
                    pass

                # 3. Standalone customer payments
                cust_payments = []
                try:
                    self.cursor.execute("""
                        SELECT cp.payment_date,
                               'PAY-' || cp.id,
                               COALESCE(cp.payment_mode, 'cash'),
                               cp.amount, 'payment'
                        FROM customer_payments cp
                        JOIN customers c ON cp.customer_id = c.id
                        WHERE UPPER(c.name) = UPPER(?)
                          AND (? = '' OR cp.payment_date >= ?)
                          AND (? = '' OR cp.payment_date <= ?)
                        ORDER BY cp.payment_date ASC, cp.id ASC
                    """, (name, fd, fd, td, td))
                    cust_payments = self.cursor.fetchall()
                except Exception:
                    pass

                # PHASE 5.2: compute opening balance from transactions BEFORE fd
                opening = 0.0
                if fd:
                    self.cursor.execute("""
                        SELECT COALESCE(SUM(s.total_amount),0),
                               COALESCE(SUM(s.amount_paid),0)
                        FROM sales s JOIN customers c ON s.customer_id=c.id
                        WHERE UPPER(c.name)=UPPER(?) AND s.bill_date < ?
                    """, (name, fd))
                    r = self.cursor.fetchone()
                    opening = round(float(r[0] or 0) - float(r[1] or 0), 2)
                    try:
                        self.cursor.execute("""
                            SELECT COALESCE(SUM(sr.refund_amount),0)
                            FROM sales_returns sr
                            JOIN customers c ON sr.customer_id=c.id
                            WHERE UPPER(c.name)=UPPER(?) AND sr.return_date < ?
                        """, (name, fd))
                        opening = round(opening - float(self.cursor.fetchone()[0] or 0), 2)
                        self.cursor.execute("""
                            SELECT COALESCE(SUM(cp.amount),0)
                            FROM customer_payments cp
                            JOIN customers c ON cp.customer_id=c.id
                            WHERE UPPER(c.name)=UPPER(?) AND cp.payment_date < ?
                        """, (name, fd))
                        opening = round(opening - float(self.cursor.fetchone()[0] or 0), 2)
                    except Exception:
                        pass

                # Merge and sort by date ASC
                all_txns = []
                for r in sales:
                    all_txns.append((r[0], r[1], r[2], float(r[3] or 0), float(r[4] or 0), 'sale'))
                for r in returns:
                    all_txns.append((r[0], r[1], r[2], float(r[3] or 0), 0.0, 'return'))
                for r in cust_payments:
                    all_txns.append((r[0], r[1], r[2], float(r[3] or 0), 0.0, 'payment'))
                all_txns.sort(key=lambda x: x[0])

            # Bank-statement loop
            running = opening
            total_billed = total_paid = total_returns = total_payments = 0.0

            if opening != 0.0:
                due_ob, credit_ob = _calc_due_credit(opening)
                _ledger_insert(tree, _tag_for_running(opening), (
                    fd or '-', 'Opening Balance', '', '',
                    '-', '-',
                    f"Rs.{due_ob:.2f}" if due_ob else '-',
                    f"Rs.{credit_ob:.2f}" if credit_ob else '-',
                    f"Rs.{opening:.2f}",
                ))

            for txn_date, ref, details, amt, paid_at_txn, txn_type in all_txns:
                if txn_type == 'sale':
                    running = round(running + amt - paid_at_txn, 2)
                    due, credit = _calc_due_credit(running)
                    total_billed += amt
                    total_paid   += paid_at_txn
                    _ledger_insert(tree, _tag_for_running(running), (
                        txn_date, 'Sale', ref, details,
                        f"Rs.{amt:.2f}",
                        f"Rs.{paid_at_txn:.2f}" if paid_at_txn else "-",
                        f"Rs.{due:.2f}" if due else "-",
                        f"Rs.{credit:.2f}" if credit else "-",
                        f"Rs.{running:.2f}",
                    ))
                elif txn_type == 'return':
                    running = round(running - amt, 2)
                    due, credit = _calc_due_credit(running)
                    total_returns += amt
                    _ledger_insert(tree, 'return', (
                        txn_date, 'Return', ref, details,
                        f"Rs.{amt:.2f}", "-",
                        f"Rs.{due:.2f}" if due else "-",
                        f"Rs.{credit:.2f}" if credit else "-",
                        f"Rs.{running:.2f}",
                    ), return_kind='sales_return')
                elif txn_type == 'payment':
                    running = round(running - amt, 2)
                    due, credit = _calc_due_credit(running)
                    total_payments += amt
                    total_paid     += amt
                    _ledger_insert(tree, 'payment', (
                        txn_date, 'Payment', ref, details,
                        "-", f"Rs.{amt:.2f}",
                        f"Rs.{due:.2f}" if due else "-",
                        f"Rs.{credit:.2f}" if credit else "-",
                        f"Rs.{running:.2f}",
                    ))

            # Integrity check
            expected = round(opening + total_billed - total_paid - total_returns, 2)
            if abs(running - expected) > 0.01:
                print(f"[LEDGER WARN] Customer '{name}': "
                      f"running={running} expected={expected} diff={running - expected}")

            total_bill_var.set(f"Total Billed: Rs.{total_billed:.2f}")
            total_paid_var.set(f"Total Paid: Rs.{total_paid:.2f}")
            total_return_var.set(f"Total Returns: Rs.{total_returns:.2f}")
            balance_var.set(f"Balance Due: Rs.{running:.2f}")
            if bal_label:
                try:
                    bal_label.configure(
                        foreground=get_alert_color('danger') if running > 0
                        else get_alert_color('success'))
                except Exception:
                    pass

        def reset():
            cl_name.set(''); cl_id_var.set('')
            cl_from.delete(0, tk.END); cl_from.insert(0, fy_start.strftime('%Y-%m-%d'))
            cl_to.delete(0, tk.END);   cl_to.insert(0, date.today().strftime('%Y-%m-%d'))
            for item in tree.get_children(): tree.delete(item)
            total_bill_var.set("Total Billed: Rs.0.00")
            total_paid_var.set("Total Paid: Rs.0.00")
            total_return_var.set("Total Returns: Rs.0.00")
            balance_var.set("Balance Due: Rs.0.00")
            cl_name.focus()

        cl_name.entry.bind('<FocusIn>', lambda e: reload(), add='+')
        cl_name.bind('<<ComboboxSelected>>', on_select)
        cl_name.next_focus_widget = lambda: on_select()
        cl_from.bind('<Return>', lambda e: cl_to.focus())
        cl_to.bind('<Return>',   lambda e: view())
        try:
            ttk.Button(btn_f, text="View Report", command=view,
                       bootstyle="primary").pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_f, text="Reset", command=reset,
                       bootstyle="secondary").pack(side=tk.LEFT, padx=6)
        except Exception:
            ttk.Button(btn_f, text="View Report", command=view).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_f, text="Reset",        command=reset).pack(side=tk.LEFT, padx=6)
        reload()
