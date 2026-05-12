import tkinter as tk
import os
try:
    import ttkbootstrap as ttk
    TTKBOOTSTRAP_AVAILABLE = True
except ImportError:
    from tkinter import ttk
    TTKBOOTSTRAP_AVAILABLE = False
from tkinter import messagebox
from core.font_config import *
from core.layout_config import (
    _DEFAULTS, _SCHEDULE_UNIT_DEFAULTS, _TYPE_QTY_DEFAULTS,
    _DEFAULT_SCHEDULES, _DEFAULT_MED_TYPES, save_layout, load_layout
)
from core.scroll_manager import make_scrollable


def _restart_app(root=None):
    import sys, subprocess
    if getattr(sys, 'frozen', False):
        args = [sys.executable]
    else:
        args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    if root is not None:
        try: root.quit()
        except Exception: pass
        try: root.destroy()
        except Exception: pass
    try:
        subprocess.Popen(args)
    except Exception:
        pass


def _theme_config_path():
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp', 'theme_config.txt')
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'config', 'theme_config.txt')


def _load_theme():
    try:
        path = _theme_config_path()
        if os.path.exists(path):
            t = open(path).read().strip()
            return t if t else 'superhero'
    except Exception:
        pass
    return 'superhero'


def _save_theme(theme):
    try:
        with open(_theme_config_path(), 'w') as f:
            f.write(theme)
    except Exception:
        pass


class LayoutTab:
    def __init__(self, notebook, parent_root):
        self._root = parent_root
        outer = ttk.Frame(notebook)
        notebook.add(outer, text="My Layout")
        layout_frame, canvas = make_scrollable(outer), None
        # make_scrollable returns inner frame; grab canvas from it
        canvas = getattr(layout_frame, '_canvas', None)
        self._build(layout_frame, canvas)

    def _build(self, frame, canvas):
        saved_font = 10
        try:
            from core.font_config import _get_font_size_path
            path = _get_font_size_path()
            if os.path.exists(path):
                saved_font = int(open(path).read().strip())
        except Exception:
            pass

        saved = load_layout()

        # ── Theme ─────────────────────────────────────────────────────────
        if TTKBOOTSTRAP_AVAILABLE:
            themes = {
                'superhero': 'Dark Blue',  'darkly': 'Dark Gray',
                'solar': 'Dark Orange',    'vapor': 'Dark Purple',
                'cosmo': 'Light Blue',     'minty': 'Light Green',
                'journal': 'Light Classic','sandstone': 'Light Warm',
            }
            current = _load_theme()
            tf = ttk.LabelFrame(frame, text="\U0001f3a8 Theme")
            tf.pack(fill=tk.X, padx=20, pady=(15, 8))
            ttk.Label(tf, text=f"Active theme: {themes.get(current, current)}",
                      font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(padx=10, pady=(8, 4))
            br = ttk.Frame(tf)
            br.pack(padx=10, pady=(0, 8))
            for key, label in themes.items():
                ttk.Button(br, text=label, width=13,
                           command=lambda t=key: self._change_theme(t)).pack(
                    side=tk.LEFT, padx=3, pady=2)

        # ── Font Size ──────────────────────────────────────────────────────
        ff = ttk.LabelFrame(frame, text="Font Size")
        ff.pack(fill=tk.X, padx=20, pady=(15, 8))
        ttk.Label(ff, text="Adjust the base font size. Changes take effect after restart.",
                  justify=tk.LEFT).pack(padx=10, pady=(8, 4))
        sr = ttk.Frame(ff)
        sr.pack(padx=10, pady=6)
        self.font_size_var = tk.IntVar(value=saved_font)
        ttk.Label(sr, text="Base Font Size:").pack(side=tk.LEFT, padx=5)
        ttk.Button(sr, text="\u2212", width=3,
                   command=lambda: self._adjust_font(-1)).pack(side=tk.LEFT, padx=2)
        self.font_spin = ttk.Spinbox(sr, from_=7, to=20,
                                     textvariable=self.font_size_var, width=5, state='readonly')
        self.font_spin.pack(side=tk.LEFT, padx=4)
        ttk.Button(sr, text="+", width=3,
                   command=lambda: self._adjust_font(1)).pack(side=tk.LEFT, padx=2)
        ttk.Label(sr, text="(7=smallest, 20=largest, default=10)").pack(side=tk.LEFT, padx=10)
        self.font_preview_var = tk.StringVar(value=f"Preview: Aa Bb Cc 123 — size {saved_font}")
        self.font_preview_lbl = ttk.Label(ff, textvariable=self.font_preview_var,
                                          font=('Segoe UI', saved_font))
        self.font_preview_lbl.pack(padx=10, pady=(0, 6))
        ttk.Button(frame, text="Save & Restart", command=self._save).pack(pady=(4, 10))

        # ── Row Counts ────────────────────────────────────────────────────
        rf = ttk.LabelFrame(frame, text="Visible Row Count per Page")
        rf.pack(fill=tk.X, padx=20, pady=8)
        ttk.Label(rf, text="Set how many rows are visible in each page's list.",
                  justify=tk.LEFT).pack(padx=10, pady=(8, 6))
        self._row_vars = {}
        pages = [
            ('billing_rows',          'Billing — Selected Medicines',     4, 30),
            ('inventory_rows',        'Inventory — Medicine List',         5, 50),
            ('sales_history_rows',    'Sales History — Bills List',        5, 50),
            ('purchase_history_rows', 'Purchase History — Purchases List', 5, 50),
            ('purchase_rows',         'Purchase — Items List',             2, 20),
            ('customers_rows',        'Customers — Customer List',         5, 50),
            ('doctors_rows',          'Settings — Doctors List',           2, 20),
            ('suppliers_rows',        'Settings — Suppliers List',         2, 20),
        ]
        rg = ttk.Frame(rf)
        rg.pack(padx=10, pady=(0, 10))
        row_spinboxes = []
        for i, (key, label, mn, mx) in enumerate(pages):
            ttk.Label(rg, text=label + ":").grid(row=i, column=0, sticky=tk.W, padx=5, pady=4)
            var = tk.IntVar(value=saved.get(key, _DEFAULTS.get(key, 8)))
            self._row_vars[key] = var
            sb = ttk.Spinbox(rg, from_=mn, to=mx, textvariable=var, width=5, state='readonly')
            sb.grid(row=i, column=1, padx=8, pady=4)
            ttk.Label(rg, text=f"rows  (default: {_DEFAULTS.get(key, '?')})").grid(
                row=i, column=2, sticky=tk.W, padx=4)
            row_spinboxes.append(sb)
        ttk.Button(frame, text="Save & Restart", command=self._save).pack(pady=(4, 10))

        # ── Unit / Measure per Type ───────────────────────────────────────
        uf = ttk.LabelFrame(frame, text="Unit / Measure per Medicine Type")
        uf.pack(fill=tk.X, padx=20, pady=8)
        ttk.Label(uf, text="Label shown next to quantity in Billing.",
                  justify=tk.LEFT).pack(padx=10, pady=(8, 4))
        self._unit_vars = {}
        self._typeqty_vars = {}
        ug = ttk.Frame(uf)
        ug.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Label(ug, text="Type", width=18, anchor='w',
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=0, column=0, padx=5, pady=2)
        ttk.Label(ug, text="Unit / Measure", width=14, anchor='w',
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=0, column=1, padx=8, pady=2)
        ttk.Label(ug, text="Default Qty (0=empty)", width=20, anchor='w',
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=0, column=2, padx=8, pady=2)
        unit_entries = []
        typeqty_entries = []
        unit_med_types = saved.get('med_types', list(_SCHEDULE_UNIT_DEFAULTS.keys()))
        for i, mt in enumerate(unit_med_types):
            r = i + 1
            ttk.Label(ug, text=f"{mt}:", width=18, anchor='w').grid(
                row=r, column=0, sticky=tk.W, padx=5, pady=3)
            uvar = tk.StringVar(value=saved.get(f'unit_{mt}', _SCHEDULE_UNIT_DEFAULTS.get(mt, '')))
            self._unit_vars[mt] = uvar
            ue = ttk.Entry(ug, textvariable=uvar, width=14)
            ue.grid(row=r, column=1, padx=8, pady=3, sticky=tk.W)
            unit_entries.append(ue)
            raw = saved.get(f'typeqty_{mt}', _TYPE_QTY_DEFAULTS.get(mt, 0))
            qvar = tk.StringVar(value=str(raw) if raw != 0 else '0')
            self._typeqty_vars[mt] = qvar
            qe = ttk.Entry(ug, textvariable=qvar, width=10)
            qe.grid(row=r, column=2, padx=8, pady=3, sticky=tk.W)
            typeqty_entries.append(qe)
        ttk.Button(frame, text="Save & Restart", command=self._save).pack(pady=(4, 10))

        # ── Schedules ─────────────────────────────────────────────────────
        sf = ttk.LabelFrame(frame, text="Schedule List (add / remove)")
        sf.pack(fill=tk.X, padx=20, pady=8)
        sb_body = ttk.Frame(sf)
        sb_body.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._sch_listbox = tk.Listbox(sb_body, height=6, selectmode=tk.SINGLE, exportselection=False)
        sch_sb = ttk.Scrollbar(sb_body, orient=tk.VERTICAL, command=self._sch_listbox.yview)
        self._sch_listbox.configure(yscrollcommand=sch_sb.set)
        self._sch_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sch_sb.pack(side=tk.LEFT, fill=tk.Y)
        for s in saved.get('schedules', list(_DEFAULT_SCHEDULES)):
            self._sch_listbox.insert(tk.END, s if s else '(blank)')
        sc = ttk.Frame(sf)
        sc.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._sch_new = ttk.Entry(sc, width=10)
        self._sch_new.pack(side=tk.LEFT, padx=(0, 4))
        self._sch_new.bind('<Return>', lambda e: self._sch_add())
        ttk.Button(sc, text="Add",
                   command=lambda: [self._sch_add(), self._save(silent=True)]).pack(side=tk.LEFT, padx=2)
        ttk.Button(sc, text="Remove Selected",
                   command=lambda: [
                       self._sch_listbox.delete(self._sch_listbox.curselection()[0])
                       if self._sch_listbox.curselection() else None,
                       self._save(silent=True)
                   ]).pack(side=tk.LEFT, padx=2)
        ttk.Button(frame, text="Save & Restart", command=self._save).pack(pady=(4, 10))

        # ── Medicine Types ────────────────────────────────────────────────
        tf2 = ttk.LabelFrame(frame, text="Medicine Type List (add / remove)")
        tf2.pack(fill=tk.X, padx=20, pady=8)
        tb_body = ttk.Frame(tf2)
        tb_body.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._typ_listbox = tk.Listbox(tb_body, height=8, selectmode=tk.SINGLE, exportselection=False)
        typ_sb = ttk.Scrollbar(tb_body, orient=tk.VERTICAL, command=self._typ_listbox.yview)
        self._typ_listbox.configure(yscrollcommand=typ_sb.set)
        self._typ_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        typ_sb.pack(side=tk.LEFT, fill=tk.Y)
        for t in saved.get('med_types', list(_DEFAULT_MED_TYPES)):
            self._typ_listbox.insert(tk.END, t)
        tc = ttk.Frame(tf2)
        tc.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._typ_new = ttk.Entry(tc, width=20)
        self._typ_new.pack(side=tk.LEFT, padx=(0, 4))
        self._typ_new.bind('<Return>', lambda e: self._typ_add())
        ttk.Button(tc, text="Add",
                   command=lambda: [self._typ_add(), self._save(silent=True)]).pack(side=tk.LEFT, padx=2)
        ttk.Button(tc, text="Remove Selected",
                   command=lambda: [
                       self._typ_listbox.delete(self._typ_listbox.curselection()[0])
                       if self._typ_listbox.curselection() else None,
                       self._save(silent=True)
                   ]).pack(side=tk.LEFT, padx=2)

        save_btn = ttk.Button(frame, text="Save Layout & Restart", command=self._save)
        save_btn.pack(pady=12)

        # Arrow nav across spinboxes + entries
        all_nav = row_spinboxes + unit_entries + typeqty_entries + [save_btn]
        for i, w in enumerate(all_nav):
            prv = all_nav[(i - 1) % len(all_nav)]
            nxt = all_nav[(i + 1) % len(all_nav)]
            w.bind('<Up>',   lambda e, p=prv: p.focus(), add='+')
            w.bind('<Down>', lambda e, n=nxt: n.focus(), add='+')
            if not isinstance(w, ttk.Button):
                w.bind('<Return>', lambda e, n=nxt: n.focus(), add='+')
                w.bind('<FocusIn>',
                       lambda e: e.widget.select_range(0, tk.END)
                       if hasattr(e.widget, 'select_range') else None, add='+')

        if canvas:
            frame.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))

    def _adjust_font(self, delta):
        new_val = max(7, min(20, self.font_size_var.get() + delta))
        self.font_size_var.set(new_val)
        self.font_preview_var.set(f"Preview: Aa Bb Cc 123 — size {new_val}")
        try:
            self.font_preview_lbl.configure(font=('Segoe UI', new_val))
        except Exception:
            pass

    def _change_theme(self, theme_name):
        if TTKBOOTSTRAP_AVAILABLE:
            try:
                _save_theme(theme_name)
                messagebox.showinfo("Theme Changed", "Application will restart with the new theme.")
                _restart_app(self._root)
            except Exception as e:
                print(f"Error changing theme: {e}")

    def _sch_add(self):
        val = self._sch_new.get().strip()
        if val and val not in self._sch_listbox.get(0, tk.END):
            self._sch_listbox.insert(tk.END, val)
            self._sch_new.delete(0, tk.END)

    def _typ_add(self):
        val = self._typ_new.get().strip()
        if val and val not in self._typ_listbox.get(0, tk.END):
            self._typ_listbox.insert(tk.END, val)
            self._typ_new.delete(0, tk.END)

    def _save(self, silent=False):
        size = self.font_size_var.get()
        try:
            from core.font_config import _get_font_size_path
            with open(_get_font_size_path(), 'w') as f:
                f.write(str(size))
        except Exception as e:
            messagebox.showerror("Error", f"Could not save font size: {e}")
            return
        try:
            data = {k: v.get() for k, v in self._row_vars.items()}
            for mt, var in self._unit_vars.items():
                data[f'unit_{mt}'] = var.get()
            for mt, var in self._typeqty_vars.items():
                raw = var.get().strip()
                try:
                    data[f'typeqty_{mt}'] = int(raw)
                except ValueError:
                    data[f'typeqty_{mt}'] = raw if raw else 0
            raw_sch = list(self._sch_listbox.get(0, tk.END))
            data['schedules'] = ['' if s == '(blank)' else s for s in raw_sch]
            new_types = list(self._typ_listbox.get(0, tk.END))
            data['med_types'] = new_types
            for t in new_types:
                if f'unit_{t}' not in data:
                    data[f'unit_{t}'] = _SCHEDULE_UNIT_DEFAULTS.get(t, '')
                if f'typeqty_{t}' not in data:
                    data[f'typeqty_{t}'] = _TYPE_QTY_DEFAULTS.get(t, 0)
            save_layout(data)
        except Exception as e:
            messagebox.showerror("Error", f"Could not save layout: {e}")
            return
        if not silent:
            messagebox.showinfo("Layout Saved", "Layout saved. The application will now restart.")
            _restart_app(self._root)
