import tkinter as tk
from tkinter import ttk
from core.font_config import *

class SearchableCombo(ttk.Frame):
    def __init__(self, master, values=None, width=35, listbox_height=12, list_min_width=None, *args, **kwargs):
        super().__init__(master, *args, **kwargs)

        self.values = list(values) if values else []
        self._search_rows: list[tuple[str, str]] = []
        self._rebuild_search_index()
        self.list_min_width = 100 if list_min_width is None and listbox_height <= 4 else (list_min_width or 500)
        self._listbox_height = listbox_height
        self._entry_width = width
        self.var = tk.StringVar(master=self)
        self.selected_flag = False
        self._suppress_trace = False
        self.list_visible = False
        self.ignore_next_enter = False
        self._listbox_navigated = False
        self.apply_on_select = None
        self.apply_on_return = True
        self._list_popup = None
        self.listbox = None
        self._update_after_id = None
        self._last_filter_key = None
        self._viewable_cache = (0.0, False)

        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(fill=tk.X)

        self.var.trace_add("write", self.on_text_change)

        self.entry.bind("<KeyRelease>", self.on_key_release)
        self.entry.bind("<Down>", self.on_down_arrow)
        self.entry.bind("<Up>", self.on_up_arrow)
        self.entry.bind("<Return>", self.on_entry_return)
        self.entry.bind("<FocusIn>", self.on_focus_in)
        self.entry.bind("<FocusOut>", self.on_focus_out)
        self.entry.bind("<Escape>", self.on_escape)
        self.entry.bind("<KeyPress-Escape>", self.on_escape)

        self.winfo_toplevel().bind("<Button-1>", self.on_click_outside, add="+")

        # Use super().bind() so these bind on the Frame, not the entry
        super().bind("<Unmap>", self._hide_on_page_change)
        super().bind("<Destroy>", self._hide_on_page_change)
        self.after(0, self._bind_ancestor_unmap)
        self.after(0, self._init_list_popup)

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

    def _ensure_list_popup(self):
        if self.listbox is not None:
            try:
                if self._list_popup and self._list_popup.winfo_exists():
                    return
            except tk.TclError:
                pass
        self._init_list_popup()

    def _init_list_popup(self):
        """Floating Toplevel so the list is not hidden behind canvas layers."""
        try:
            if self._list_popup is not None and self._list_popup.winfo_exists():
                return
        except tk.TclError:
            pass
        top = self.winfo_toplevel()
        self._list_popup = tk.Toplevel(top)
        self._list_popup.withdraw()
        self._list_popup.overrideredirect(True)
        try:
            self._list_popup.transient(top)
            self._list_popup.attributes('-topmost', True)
        except tk.TclError:
            pass
        self.listbox = tk.Listbox(
            self._list_popup,
            width=self._entry_width + 20,
            height=self._listbox_height,
            relief='solid',
            borderwidth=1,
            font=(FONT_FAMILY, FONT_SIZE_DROPDOWNS),
        )
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox._searchable_combo = self
        self.listbox.bind("<Return>", self.on_listbox_return)
        self.listbox.bind("<Double-Button-1>", self.on_listbox_double)
        self.listbox.bind("<ButtonRelease-1>", self.on_listbox_click)
        self.listbox.bind("<Escape>", self._on_listbox_escape)
        self.listbox.bind("<KeyPress-Escape>", self._on_listbox_escape)
        self.listbox.bind("<FocusOut>", self.on_listbox_focus_out)
        self.listbox.bind("<Up>", self.on_listbox_up)
        self.listbox.bind("<Down>", self.on_listbox_down)

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

    def _widget_is_or_inside(self, widget, container):
        w = widget
        while w is not None:
            try:
                if w is container:
                    return True
                w = w.master
            except tk.TclError:
                break
        return False

    def _rebuild_search_index(self):
        self._search_rows = [(v, str(v).lower()) for v in self.values]

    def _schedule_update_list(self):
        if self._update_after_id is not None:
            try:
                self.after_cancel(self._update_after_id)
            except Exception:
                pass
        self._update_after_id = self.after(60, self._run_update_list)

    def _run_update_list(self):
        self._update_after_id = None
        self.update_list()

    def _is_viewable(self):
        """True when the entry is on-screen (works inside scrollable canvas pages)."""
        import time

        now = time.time()
        cached_at, cached = self._viewable_cache
        if now - cached_at < 0.12:
            return cached
        try:
            if not self.winfo_exists() or not self._entry_exists():
                ok = False
            elif not self.entry.winfo_ismapped():
                ok = False
            elif self.entry.winfo_width() <= 1 or self.entry.winfo_height() <= 1:
                ok = False
            else:
                ok = True
        except tk.TclError:
            ok = False
        self._viewable_cache = (now, ok)
        return ok

    # ── entry events ───────────────────────────────────────────────────────

    def _release_focus(self):
        """Leave the field so page shortcuts (0–7, F-keys) work again."""
        try:
            from core.keyboard_registry import KeyboardRegistry
            KeyboardRegistry.blur_to_nav()
        except Exception:
            try:
                self.winfo_toplevel().focus_set()
            except Exception:
                pass

    def dismiss(self, *, blur=True):
        """Hide the dropdown and optionally release keyboard focus."""
        self.hide_list()
        if blur:
            self._release_focus()
        return 'break'

    def on_escape(self, event):
        return self.dismiss(blur=True)

    def _on_listbox_escape(self, event):
        return self.dismiss(blur=True)

    def on_focus_in(self, event):
        if getattr(self, '_suppress_focus_list', False):
            self.hide_list()
            return
        self.after(10, self._show_all_on_focus)

    def _show_all_on_focus(self):
        """On focus, show full list filtered by current text (case-insensitive)."""
        if not self._is_viewable():
            self.hide_list()
            return
        self._ensure_list_popup()
        typed = self.var.get().strip()
        if typed:
            self._last_filter_key = None
            self.update_list()
        elif len(self._search_rows) > 400:
            self.hide_list()
        else:
            self.listbox.delete(0, tk.END)
            preview = [v for v, _ in self._search_rows[:50]]
            if preview:
                self.listbox.insert(0, *preview)
            if self.listbox.size() > 0:
                self.show_list()
                self.listbox.selection_clear(0, tk.END)
                self._listbox_navigated = False
            else:
                self.hide_list()

    def on_focus_out(self, event):
        """Hide list when entry loses focus, UNLESS focus is going to the listbox."""
        self.after(50, self._hide_unless_listbox_focused)

    def _hide_unless_listbox_focused(self):
        """Hide the list if focus is not on the listbox."""
        try:
            focused = self.focus_get()
            if self.listbox and focused is self.listbox:
                return
            if getattr(self, '_list_popup', None) and self._widget_is_or_inside(focused, self._list_popup):
                return
            self.hide_list()
        except Exception:
            self.hide_list()

    def on_click_outside(self, event):
        w = event.widget
        if w is self.entry:
            return
        if self.listbox and self._widget_is_or_inside(w, self.listbox):
            return
        if getattr(self, '_list_popup', None) and self._widget_is_or_inside(w, self._list_popup):
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
            if self.apply_on_return:
                self._fire_apply()
        else:
            self.hide_list()
            if typed:
                typed_upper = typed.strip().upper()
                visible = [self.listbox.get(i) for i in range(self.listbox.size())]
                match = None
                if self.list_visible and self.listbox.curselection():
                    match = self.listbox.get(self.listbox.curselection()[0])
                if not match:
                    match = next((v for v in self.values if v.upper() == typed_upper), None)
                if not match:
                    match = next((v for v in visible if v.upper().startswith(typed_upper)), None)
                if not match:
                    match = next((v for v in self.values if v.upper().startswith(typed_upper)), None)
                if match:
                    self.selected_flag = True
                    self.var.set(match)
                    if self._entry_exists():
                        self.entry.event_generate('<<ComboboxSelected>>')
                    if self.apply_on_return:
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
        # Text trace already schedules filtering — avoid duplicate work per keystroke.

    def on_text_change(self, *args):
        if self._suppress_trace:
            return
        try:
            if self._entry_exists() and self.entry.focus_get() == self.entry:
                self.selected_flag = False
                self._listbox_navigated = False
        except Exception:
            pass
        self._schedule_update_list()

    # ── listbox events ─────────────────────────────────────────────────────

    def on_listbox_return(self, event):
        self.select_item(move_focus=False)
        if self._entry_exists():
            self.entry.event_generate('<<ComboboxSelected>>')
        if self.apply_on_return:
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
        self._ensure_list_popup()
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
        self._ensure_list_popup()
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
        self._ensure_list_popup()
        search = self.var.get().lower().strip()
        cache_key = (search, len(self._search_rows))
        if cache_key == self._last_filter_key and self.list_visible:
            return
        self._last_filter_key = cache_key

        limit = 50
        if not search:
            if len(self._search_rows) > 400:
                # Huge lists: don't paint thousands of names on focus — wait for typing.
                matches = []
            else:
                matches = [v for v, _ in self._search_rows[:limit]]
        else:
            starts: list[str] = []
            contains: list[str] = []
            for display, low in self._search_rows:
                if low.startswith(search):
                    starts.append(display)
                    if len(starts) >= limit:
                        break
            if len(starts) < limit:
                for display, low in self._search_rows:
                    if search in low and not low.startswith(search):
                        contains.append(display)
                        if len(starts) + len(contains) >= limit:
                            break
            matches = (starts + contains)[:limit]

        self.listbox.delete(0, tk.END)
        if matches:
            self.listbox.insert(0, *matches)

        if matches:
            if self._is_viewable():
                self.show_list()
            else:
                self.hide_list()
            self.listbox.selection_clear(0, tk.END)
            self._listbox_navigated = False
        else:
            self.hide_list()

    def show_list(self):
        if not self._is_viewable():
            self.hide_list()
            return
        self._ensure_list_popup()
        if not self.list_visible and self.listbox.size() > 0:
            self.update_idletasks()
            try:
                x = self.entry.winfo_rootx()
                y = self.entry.winfo_rooty() + self.entry.winfo_height()
                w = max(self.entry.winfo_width() + 20, self.list_min_width)
                h = self._listbox_height * 22 + 4
                self._list_popup.geometry(f"{w}x{h}+{x}+{y}")
                self._list_popup.deiconify()
                self._list_popup.lift()
                try:
                    self._list_popup.attributes('-topmost', True)
                except tk.TclError:
                    pass
                self.list_visible = True
            except tk.TclError:
                pass

    def hide_list(self):
        try:
            if getattr(self, '_list_popup', None) and self._list_popup.winfo_exists():
                self._list_popup.withdraw()
        except tk.TclError:
            pass
        self.list_visible = False

    def select_item(self, move_focus=True):
        self._ensure_list_popup()
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
        self._suppress_trace = True
        try:
            self.selected_flag = bool(text.strip())
            self._listbox_navigated = False
            # Always store a real string — None breaks later .get().strip() callers.
            self.var.set(text)
            self.hide_list()
        finally:
            self._suppress_trace = False

    def configure(self, **kwargs):
        if 'values' in kwargs:
            self.values = list(kwargs['values'])
            self._rebuild_search_index()
            self._last_filter_key = None
            # Refresh open dropdown silently — do not reopen while user is typing.
            try:
                if self._entry_exists() and self.entry.focus_get() == self.entry:
                    self._schedule_update_list()
            except Exception:
                pass

    def bind(self, event, callback):
        self.entry.bind(event, callback)

    def focus(self, *, open_dropdown=False):
        try:
            if not self._is_viewable():
                self.hide_list()
                return
            if self.winfo_exists() and self.entry.winfo_exists():
                if open_dropdown:
                    self.after(10, self._show_all_on_focus)
                else:
                    self._suppress_focus_list = True
                    self.after(80, lambda: setattr(self, '_suppress_focus_list', False))
                    self.hide_list()
                self.entry.focus_set()
        except tk.TclError:
            pass

    def destroy(self):
        self.hide_list()
        try:
            if getattr(self, '_list_popup', None) and self._list_popup.winfo_exists():
                self._list_popup.destroy()
        except tk.TclError:
            pass
        super().destroy()

    def _has_matches(self):
        return self.listbox.size() > 0
