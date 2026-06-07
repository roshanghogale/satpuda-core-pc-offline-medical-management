import tkinter as tk
from tkinter import ttk
from core.font_config import *

class SearchableCombo(ttk.Frame):
    def __init__(self, master, values=None, width=35, listbox_height=12, *args, **kwargs):
        super().__init__(master, *args, **kwargs)

        self.values = list(values) if values else []
        self.var = tk.StringVar()
        self.selected_flag = False
        self.list_visible = False
        self.ignore_next_enter = False
        self._listbox_navigated = False
        self.apply_on_select = None

        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(fill=tk.X)

        self.listbox = tk.Listbox(self.winfo_toplevel(), width=width+20, height=listbox_height,
                                  relief='solid', borderwidth=1, font=(FONT_FAMILY, FONT_SIZE_DROPDOWNS))
        self.listbox.place_forget()

        self.var.trace_add("write", self.on_text_change)

        self.entry.bind("<KeyRelease>", self.on_key_release)
        self.entry.bind("<Down>", self.on_down_arrow)
        self.entry.bind("<Up>", self.on_up_arrow)
        self.entry.bind("<Return>", self.on_entry_return)
        self.entry.bind("<FocusIn>", self.on_focus_in)
        self.entry.bind("<FocusOut>", self.on_focus_out)
        self.entry.bind("<Escape>", self.on_escape)

        self.winfo_toplevel().bind("<Button-1>", self.on_click_outside, add="+")

        # Use super().bind() so these bind on the Frame, not the entry
        super().bind("<Unmap>", self._hide_on_page_change)
        super().bind("<Destroy>", self._hide_on_page_change)
        self.after(0, self._bind_ancestor_unmap)

        self.listbox.bind("<Return>", self.on_listbox_return)
        self.listbox.bind("<Double-Button-1>", self.on_listbox_double)
        self.listbox.bind("<ButtonRelease-1>", self.on_listbox_click)
        self.listbox.bind("<Escape>", lambda e: self.hide_list())
        self.listbox.bind("<FocusOut>", self.on_listbox_focus_out)
        self.listbox.bind("<Up>", self.on_listbox_up)
        self.listbox.bind("<Down>", self.on_listbox_down)

    # ── lifecycle ──────────────────────────────────────────────────────────

    def bind_apply_on_select(self, callback):
        """Call ``callback`` after a value is chosen (Enter or click in list)."""
        self.apply_on_select = callback

    def _fire_apply(self):
        if callable(self.apply_on_select):
            try:
                self.apply_on_select()
            except Exception:
                pass

    def _hide_on_page_change(self, event=None):
        self.hide_list()

    def _bind_ancestor_unmap(self):
        """Bind <Unmap> on every ancestor so the listbox hides on page navigation."""
        try:
            w = self.master
            toplevel = self.winfo_toplevel()
            while w and w is not toplevel:
                tk.Widget.bind(w, "<Unmap>", self._hide_on_page_change, add="+")
                w = w.master
        except Exception:
            pass

    def _entry_exists(self):
        try:
            return self.entry.winfo_exists()
        except Exception:
            return False

    # ── entry events ───────────────────────────────────────────────────────

    def on_escape(self, event):
        if self.list_visible:
            self.hide_list()
            return "break"
        return None

    def on_focus_in(self, event):
        if getattr(self, '_suppress_focus_list', False):
            self.hide_list()
            return
        if self.values:
            self.after(10, self._show_all_on_focus)

    def _show_all_on_focus(self):
        """On focus, show full list filtered by current text (case-insensitive)."""
        typed = self.var.get().strip()
        if typed:
            # Re-run the normal filter so case-insensitive results show
            self.update_list()
        else:
            self.listbox.delete(0, tk.END)
            for item in self.values[:50]:
                self.listbox.insert(tk.END, item)
            if self.listbox.size() > 0:
                self.show_list()
                self.listbox.selection_clear(0, tk.END)
                self._listbox_navigated = False

    def on_focus_out(self, event):
        """Hide list when entry loses focus, UNLESS focus is going to the listbox."""
        self.after(50, self._hide_unless_listbox_focused)

    def _hide_unless_listbox_focused(self):
        """Hide the list if focus is not on the listbox."""
        try:
            focused = self.focus_get()
            if focused is not self.listbox:
                self.hide_list()
        except Exception:
            self.hide_list()

    def on_click_outside(self, event):
        w = event.widget
        if w is self.entry or w is self.listbox:
            return
        self.hide_list()

    def on_entry_return(self, event):
        """Enter keeps typed text unless user explicitly arrow-keyed to a list item."""
        if self.ignore_next_enter:
            self.ignore_next_enter = False
            return "break"

        typed = self.var.get()

        if self._listbox_navigated and self.list_visible and self.listbox.curselection():
            selected_item = self.listbox.get(self.listbox.curselection()[0])
            self.selected_flag = True
            self._listbox_navigated = False
            self.var.set(selected_item)
            self.hide_list()
            if self._entry_exists():
                self.entry.event_generate('<<ComboboxSelected>>')
            self._fire_apply()
        else:
            self.hide_list()
            if typed:
                typed_upper = typed.upper()
                match = next((v for v in self.values if v.upper() == typed_upper), None)
                if match:
                    self.selected_flag = True
                    self.var.set(match)
                    if self._entry_exists():
                        self.entry.event_generate('<<ComboboxSelected>>')
                    self._fire_apply()

        try:
            if hasattr(self, 'next_focus_widget') and self.next_focus_widget:
                self.next_focus_widget()
            else:
                event.widget.tk_focusNext().focus_set()
        except Exception:
            pass
        return "break"

    def on_key_release(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return
        self.selected_flag = False
        self._listbox_navigated = False
        self.update_list()

    def on_text_change(self, *args):
        if not self.selected_flag:
            self.update_list()

    # ── listbox events ─────────────────────────────────────────────────────

    def on_listbox_return(self, event):
        self.select_item(move_focus=False)
        if self._entry_exists():
            self.entry.event_generate('<<ComboboxSelected>>')
        self._fire_apply()
        try:
            if hasattr(self, 'next_focus_widget') and self.next_focus_widget:
                self.after(10, self.next_focus_widget)
            elif self._entry_exists():
                self.after(10, lambda: self.entry.tk_focusNext().focus_set())
        except Exception:
            pass
        return "break"

    def on_listbox_double(self, event):
        self.select_item(move_focus=False)
        if self._entry_exists():
            self.entry.event_generate('<<ComboboxSelected>>')
        self._fire_apply()
        try:
            if hasattr(self, 'next_focus_widget') and self.next_focus_widget:
                self.after(10, self.next_focus_widget)
            elif self._entry_exists():
                self.after(10, lambda: self.entry.tk_focusNext().focus_set())
        except Exception:
            pass

    def on_listbox_click(self, event):
        self.select_item(move_focus=False)
        if self._entry_exists():
            self.entry.event_generate('<<ComboboxSelected>>')
        self._fire_apply()

    def on_listbox_focus_out(self, event):
        """When listbox loses focus, hide unless focus went back to entry."""
        self.after(50, self._hide_unless_entry_focused)

    def _hide_unless_entry_focused(self):
        try:
            focused = self.focus_get()
            if focused is not self.entry:
                self.hide_list()
        except Exception:
            self.hide_list()

    def on_listbox_up(self, event):
        current = self.listbox.curselection()
        if current and current[0] == 0:
            if self._entry_exists():
                self.entry.focus_set()
            return "break"
        return None

    def on_listbox_down(self, event):
        return None

    # ── arrow navigation ───────────────────────────────────────────────────

    def on_down_arrow(self, event):
        if not self.list_visible and self.listbox.size() > 0:
            self.show_list()
        if self.list_visible and self.listbox.size() > 0:
            current = self.listbox.curselection()
            next_idx = min(current[0] + 1, self.listbox.size() - 1) if current else 0
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(next_idx)
            self.listbox.activate(next_idx)
            self.listbox.see(next_idx)
            self._listbox_navigated = True
        return "break"

    def on_up_arrow(self, event):
        if self.list_visible and self.listbox.size() > 0:
            current = self.listbox.curselection()
            if current:
                prev_idx = max(current[0] - 1, 0)
                if prev_idx == current[0] and current[0] == 0:
                    return "break"
            else:
                prev_idx = 0
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(prev_idx)
            self.listbox.activate(prev_idx)
            self.listbox.see(prev_idx)
            self._listbox_navigated = True
        return "break"

    # ── list management ────────────────────────────────────────────────────

    def update_list(self):
        search = self.var.get().lower()   # always compare lowercase
        if not search:
            matches = self.values[:50]
        else:
            starts   = [v for v in self.values if v.lower().startswith(search)]
            contains = [v for v in self.values
                        if search in v.lower() and not v.lower().startswith(search)]
            matches  = (starts + contains)[:50]

        self.listbox.delete(0, tk.END)
        for item in matches:
            self.listbox.insert(tk.END, item)

        if matches:
            self.show_list()
            self.listbox.selection_clear(0, tk.END)
            self._listbox_navigated = False
        else:
            self.hide_list()

    def show_list(self):
        if not self.list_visible and self.listbox.size() > 0:
            self.update_idletasks()
            try:
                x = self.entry.winfo_rootx() - self.winfo_toplevel().winfo_rootx()
                y = self.entry.winfo_rooty() - self.winfo_toplevel().winfo_rooty() + self.entry.winfo_height()
                self.listbox.place(x=x, y=y, width=max(self.entry.winfo_width() + 150, 500))
                self.listbox.tkraise()
                self.list_visible = True
            except tk.TclError:
                pass

    def hide_list(self):
        try:
            if self.listbox.winfo_exists():
                self.listbox.place_forget()
        except tk.TclError:
            pass
        self.list_visible = False

    def select_item(self, move_focus=True):
        sel = self.listbox.curselection()
        if sel:
            self.selected_flag = True
            self.var.set(self.listbox.get(sel[0]))
        self.hide_list()
        if move_focus and self._entry_exists():
            try:
                self.entry.tk_focusNext().focus_set()
            except Exception:
                pass

    # ── public API ─────────────────────────────────────────────────────────

    def get(self):
        return self.var.get()

    def set(self, value):
        text = str(value or '')
        # Non-empty programmatic set must not open the dropdown via text trace.
        self.selected_flag = bool(text.strip())
        self.var.set(value)
        self.hide_list()

    def configure(self, **kwargs):
        if 'values' in kwargs:
            self.values = list(kwargs['values'])

    def bind(self, event, callback):
        self.entry.bind(event, callback)

    def focus(self, *, open_dropdown=False):
        try:
            if self.winfo_exists() and self.entry.winfo_exists():
                if not open_dropdown:
                    self._suppress_focus_list = True
                    self.after(80, lambda: setattr(self, '_suppress_focus_list', False))
                    self.hide_list()
                self.entry.focus_set()
        except tk.TclError:
            pass

    def destroy(self):
        self.hide_list()
        super().destroy()

    def _has_matches(self):
        return self.listbox.size() > 0
