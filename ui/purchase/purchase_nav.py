"""
ui/purchase_nav.py
──────────────────
Arrow-key / keyboard navigation mixin for PurchasePage.
Mixed into PurchasePage — no UI building here, only key bindings.
"""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.scroll_manager import scroll_to_widget


class PurchaseNavMixin:

    # ── public entry point ────────────────────────────────────────────────

    def _setup_arrow_nav(self):
        if getattr(self, '_arrow_nav_ready', False):
            return
        def _e(w):
            return w.entry if hasattr(w, 'entry') else w

        self._nav_static = [
            _e(self.supplier_name),   # 0
            self.supplier_address,    # 1
            self.supplier_phone,      # 2
            self.supplier_gstin,      # 3
            self.supplier_dl,         # 4
            self.purchase_date,       # 5
            self.bill_number,         # 6
            _e(self.medicine_name),   # 7
            _e(self.medicine_type),   # 8
            self.hsn_code,            # 9
            self.gst_value,           # 10
            self.mrp,                 # 11
            self.rate,                # 12
            self.manufacturer,        # 13
            self.batch_no,            # 14
            self.expiry_date,         # 15
            _e(self.schedule),        # 16
            self.content_drug,        # 17
            self.item_discount,       # 18
            self.add_btn,             # 19
            self.overall_discount_pct, # 20
            self.overall_discount,    # 21
            self.rounding_entry,      # 22
            self.expenditure_entry,   # 23
            self.cash_paid,           # 24
            self.online_paid,         # 25
            self.clear_btn,           # 26
            self.save_btn,            # 27
            self.gst_slab_btn,        # 28
            self.recalculate_btn,     # 29 (edit purchase only, when packed)
        ]
        self._qty_attrs = ('stripes', 'tablets_per_stripe', 'free_stripes',
                           'quantity', 'units', 'free_items')
        self._medicine_type_entry = _e(self.medicine_type)

        plain_static = {1,2,3,4,5,6,7,8,11,12,13,14,15,16,17,19,20,21,22,23,24,25,26,27,28,29,30}
        self._bind_nav_on_list(self._nav_static, plain_static)
        for w in (getattr(self, 'tablet_mrp', None), getattr(self, 'tablet_rate', None)):
            if w is not None:
                self._bind_nav_on_list([w], {0})

        self.parent.winfo_toplevel().bind(
            '<FocusIn>', lambda e: self._scroll_to_widget(e.widget), add='+')

        self._bind_end_to_payment()
        self._arrow_nav_ready = True

    def _focus_medicine_combo(self, event=None):
        try:
            self.medicine_name.hide_list()
            self.medicine_type.hide_list()
            self.medicine_name.focus(open_dropdown=False)
            entry = self.medicine_name.entry
            self._scroll_to_widget(entry)
            canvas = getattr(self._inner_frame, '_canvas', None)
            if canvas is not None:
                canvas.update_idletasks()
                wy = entry.winfo_rooty() - canvas.winfo_rooty()
                eh = max(entry.winfo_height(), 1)
                ch = canvas.winfo_height()
                if wy < 0 or wy + eh > ch:
                    canvas.yview_moveto(0)
                    self._scroll_to_widget(entry)
        except Exception:
            pass
        return 'break'

    def _focus_overall_discount(self, event=None):
        try:
            self.overall_discount_pct.focus_set()
            self.overall_discount_pct.select_range(0, tk.END)
            self._scroll_to_widget(self.overall_discount_pct)
        except Exception:
            pass
        return 'break'

    def _focus_payment_field(self, event=None):
        try:
            self.cash_paid.focus_set()
            self.cash_paid.select_range(0, tk.END)
            self._scroll_to_widget(self.cash_paid)
        except Exception:
            pass
        return 'break'

    def _bind_end_to_payment(self):
        skip = {self.cash_paid, self.online_paid}

        def handler(event):
            return self._focus_payment_field()

        for w in self._get_full_nav():
            if w in skip:
                continue
            try:
                if w.winfo_exists():
                    w.bind('<End>', handler, add='+')
            except Exception:
                pass

        for attr in ('overall_discount_pct',):
            if hasattr(self, attr):
                try:
                    w = getattr(self, attr)
                    if w.winfo_exists():
                        w.bind('<End>', handler, add='+')
                except Exception:
                    pass

    # ── full nav list (includes dynamic qty fields) ───────────────────────

    def _get_full_nav(self):
        mt  = self._medicine_type_entry
        qty = []
        for attr in self._qty_attrs:
            if hasattr(self, attr):
                try:
                    w = getattr(self, attr)
                    if w.winfo_exists():
                        qty.append(w)
                except Exception:
                    pass
        result = []
        for w in self._nav_static:
            if w is getattr(self, 'recalculate_btn', None):
                try:
                    if not w.winfo_ismapped():
                        continue
                except tk.TclError:
                    continue
            result.append(w)
            if w is mt and qty:
                result.extend(qty)
            if w is getattr(self, 'mrp', None):
                tm = getattr(self, 'tablet_mrp', None)
                try:
                    if tm is not None and tm.winfo_exists() and tm.winfo_ismapped():
                        result.append(tm)
                except tk.TclError:
                    pass
            if w is getattr(self, 'rate', None):
                tr = getattr(self, 'tablet_rate', None)
                try:
                    if tr is not None and tr.winfo_exists() and tr.winfo_ismapped():
                        result.append(tr)
                except tk.TclError:
                    pass
        return result

    # ── bind helpers ──────────────────────────────────────────────────────

    def _bind_nav_on_list(self, nav_list, plain_indices_set):
        def make_next(w):
            def handler(event):
                if event.keysym == 'Right':
                    try:
                        if event.widget.index(tk.INSERT) < len(event.widget.get()):
                            return None
                    except Exception:
                        pass
                full = self._get_full_nav()
                try:
                    i = full.index(w)
                    t = full[(i + 1) % len(full)]
                    t.focus(); self._scroll_to_widget(t)
                except (ValueError, AttributeError):
                    pass
                return 'break'
            return handler

        def make_prev(w):
            def handler(event):
                if event.keysym == 'Left':
                    try:
                        if event.widget.index(tk.INSERT) > 0:
                            return None
                    except Exception:
                        pass
                full = self._get_full_nav()
                try:
                    i = full.index(w)
                    t = full[(i - 1) % len(full)]
                    t.focus(); self._scroll_to_widget(t)
                except (ValueError, AttributeError):
                    pass
                return 'break'
            return handler

        for i, w in enumerate(nav_list):
            if i in plain_indices_set or isinstance(w, ttk.Button):
                w.bind('<Up>',   make_prev(w), add='+')
                w.bind('<Down>', make_next(w), add='+')
            w.bind('<Left>',  make_prev(w), add='+')
            w.bind('<Right>', make_next(w), add='+')

    def _make_qty_next(self, w):
        def handler(event):
            if event.keysym == 'Right':
                try:
                    sel_all = (event.widget.index('sel.first') == 0 and
                               event.widget.index('sel.last') == len(event.widget.get()))
                except Exception:
                    sel_all = False
                if not sel_all:
                    try:
                        if event.widget.index(tk.INSERT) < len(event.widget.get()):
                            return None
                    except Exception:
                        pass
            full = self._get_full_nav()
            try:
                i = full.index(w)
                t = full[(i + 1) % len(full)]
                t.focus(); self._scroll_to_widget(t)
            except (ValueError, AttributeError):
                pass
            return 'break'
        return handler

    def _make_qty_prev(self, w):
        def handler(event):
            if event.keysym == 'Left':
                try:
                    sel_all = (event.widget.index('sel.first') == 0 and
                               event.widget.index('sel.last') == len(event.widget.get()))
                except Exception:
                    sel_all = False
                if not sel_all:
                    try:
                        if event.widget.index(tk.INSERT) > 0:
                            return None
                    except Exception:
                        pass
            full = self._get_full_nav()
            try:
                i = full.index(w)
                t = full[(i - 1) % len(full)]
                t.focus(); self._scroll_to_widget(t)
            except (ValueError, AttributeError):
                pass
            return 'break'
        return handler

    def _bind_qty_nav(self):
        attrs = getattr(self, '_qty_attrs',
                        ('stripes', 'tablets_per_stripe', 'free_stripes',
                         'quantity', 'units', 'free_items'))
        for attr in attrs:
            if hasattr(self, attr):
                try:
                    e = getattr(self, attr)
                    if e.winfo_exists():
                        e.bind('<Up>',    self._make_qty_prev(e), add='+')
                        e.bind('<Down>',  self._make_qty_next(e), add='+')
                        e.bind('<Left>',  self._make_qty_prev(e), add='+')
                        e.bind('<Right>', self._make_qty_next(e), add='+')
                except Exception:
                    pass

    def _scroll_to_widget(self, widget):
        if hasattr(self, '_inner_frame'):
            scroll_to_widget(self._inner_frame, widget)
