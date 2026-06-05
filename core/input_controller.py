import tkinter as tk

# ── Widget classification ─────────────────────────────────────────────────────

_TREEVIEW_CLASSES = frozenset({'Treeview'})
_LISTBOX_CLASSES = frozenset({'Listbox'})

_INPUT_CLASSES = frozenset({
    'Entry', 'TEntry', 'TCombobox', 'Text', 'Spinbox', 'TSpinbox', 'Listbox'
})

_FOCUSABLE_CLASSES = frozenset({
    'Entry', 'TEntry', 'TCombobox', 'Text', 'Spinbox', 'TSpinbox',
    'Listbox', 'TButton', 'Button',
})


def _is_treeview(widget):
    return _widget_class(widget) in _TREEVIEW_CLASSES


def _widget_class(widget):
    try:
        return widget.winfo_class()
    except Exception:
        return ''


def _treeview_at_edge(treeview, direction):
    try:
        top, bottom = treeview.yview()
        if direction > 0:
            return bottom >= 1.0
        else:
            return top <= 0.0
    except Exception:
        return True


class GlobalInputController:
    """
    Installed ONCE on the root window.  Owns all global input events.

    Pages register their active canvas via set_active_canvas().
    Settings page (Notebook) registers per-tab canvases via set_active_canvas()
    on each <<NotebookTabChanged>> event — see open_settings() in main.py.
    """

    def __init__(self, root: tk.Misc, main_frame: tk.Misc):
        self._root       = root
        self._main_frame = main_frame
        self._canvas     = None
        self._f2_handler = None
        self._install_bindings()

    # ── Public API ────────────────────────────────────────────────────────

    def set_active_canvas(self, canvas):
        """Register the canvas that should receive page-level scroll events."""
        self._canvas = canvas

    def set_active_frame(self, frame):
        """
        Override the frame used by Shift-key focus search.
        Call this when navigating to a page whose content root differs from
        main_frame (e.g. a Notebook tab's inner frame).
        Pass None to revert to main_frame.
        """
        self._active_frame = frame

    def set_f2_handler(self, handler):
        """Optional page-specific F2 action (e.g. Purchase → Import Bill). Pass None for default."""
        self._f2_handler = handler

    # ── Binding installation ──────────────────────────────────────────────

    def _install_bindings(self):
        self._active_frame = None   # None → fall back to self._main_frame

        # bind_all used ONCE here — never in any page.
        self._root.bind_all('<MouseWheel>', self._on_mousewheel,        add='+')
        self._root.bind_all('<Button-4>',   self._on_scroll_up_linux,   add='+')
        self._root.bind_all('<Button-5>',   self._on_scroll_down_linux, add='+')
        self._root.bind_all('<Up>',         self._on_arrow_up,          add='+')
        self._root.bind_all('<Down>',       self._on_arrow_down,        add='+')
        self._root.bind_all('<Alt_L>',       self._on_shift,             add='+')
        self._root.bind_all('<Alt_R>',       self._on_shift,             add='+')
        self._root.bind_all('<F2>',         self._on_f2,                add='+')
        self._root.bind_all('<Escape>',     self._on_escape_treeview,   add='+')

    # ── Mouse-wheel handlers ──────────────────────────────────────────────

    def _on_mousewheel(self, event):
        self._handle_scroll(event.widget, event.delta / 120)

    def _on_scroll_up_linux(self, event):
        self._handle_scroll(event.widget, 1)

    def _on_scroll_down_linux(self, event):
        self._handle_scroll(event.widget, -1)

    def _handle_scroll(self, source_widget, delta):
        if self._canvas is None:
            return
        cls = _widget_class(source_widget)
        if cls in _TREEVIEW_CLASSES:
            if not _treeview_at_edge(source_widget, delta):
                return
        if cls in _LISTBOX_CLASSES:
            return
        self._scroll_canvas(delta)

    def _scroll_canvas(self, delta):
        canvas = self._canvas
        if canvas is None:
            return
        try:
            inner = getattr(canvas, '_inner_frame', None)
            if inner is not None:
                from core.scroll_manager import _canvas_needs_scroll
                if not _canvas_needs_scroll(canvas, inner):
                    return
            canvas.yview_scroll(int(-1 * delta), 'units')
        except Exception:
            pass

    # ── Arrow-key page scroll ─────────────────────────────────────────────

    def _on_arrow_up(self, event):
        w = self._focused_widget()
        if w is not None and _is_treeview(w):
            return  # let Treeview handle Up natively
        if not self._is_input_focused():
            self._scroll_canvas(3)

    def _on_arrow_down(self, event):
        w = self._focused_widget()
        if w is not None and _is_treeview(w):
            return  # let Treeview handle Down natively
        if not self._is_input_focused():
            self._scroll_canvas(-3)

    # ── F2 → focus first visible Treeview ────────────────────────────────

    def _on_f2(self, event):
        if self._f2_handler:
            try:
                result = self._f2_handler(event)
                return result if result else 'break'
            except Exception:
                return 'break'
        frame = self._resolve_active_frame()
        tree = self._find_first_treeview(frame)
        if tree is None:
            return
        items = tree.get_children()
        if not items:
            return
        sel = tree.selection()
        target = sel[0] if sel else items[0]
        tree.selection_set(target)
        tree.focus(target)
        tree.focus_set()
        tree.see(target)
        return 'break'

    def _find_first_treeview(self, widget):
        try:
            if not widget.winfo_exists() or not widget.winfo_ismapped():
                return None
        except Exception:
            return None
        if _widget_class(widget) in _TREEVIEW_CLASSES:
            return widget
        try:
            for child in widget.winfo_children():
                result = self._find_first_treeview(child)
                if result is not None:
                    return result
        except Exception:
            pass
        return None

    # ── Escape → exit Treeview focus ─────────────────────────────────────

    def _on_escape_treeview(self, event):
        w = self._focused_widget()
        if w is None or not _is_treeview(w):
            return  # not a Treeview — let main.py Escape handler run
        try:
            w.selection_remove(w.selection())
        except Exception:
            pass
        self._focus_first_widget(self._resolve_active_frame())
        return 'break'

    # ── Alt → focus first input ─────────────────────────────────────────

    def _on_shift(self, event):
        # Alt key: focus the first input of the current page.
        focused = self._focused_widget()
        if focused is not None:
            cls = _widget_class(focused)
            if cls == 'TCombobox':
                try:
                    popdown = focused.tk.call('ttk::combobox::PopdownWindow', focused)
                    if popdown and int(focused.tk.call('winfo', 'ismapped', popdown)):
                        return
                except Exception:
                    pass
        self._focus_first_widget(self._resolve_active_frame())

    def _resolve_active_frame(self):
        """
        Return the frame to search for the first focusable widget.

        Uses self._active_frame when it is set AND still alive.
        Falls back to self._main_frame otherwise.

        This is the core fix for the intermittent Shift bug: after page
        navigation, clear_main_frame() destroys the previous page's widgets.
        The stored _active_frame reference becomes a dead Tkinter widget whose
        winfo_children() raises TclError.  Checking winfo_exists() first
        prevents that silent failure.
        """
        frame = self._active_frame
        if frame is not None:
            try:
                if frame.winfo_exists():
                    return frame
            except Exception:
                pass
            # Frame is dead — clear the stale reference
            self._active_frame = None
        return self._main_frame

    def bind_text_nav(self, text_widget, prev_widget, next_widget):
        """
        Bind Up/Down on a tk.Text widget so that:
        - Up at the first line  → focus prev_widget
        - Down at the last line → focus next_widget
        - Otherwise normal cursor movement is preserved.
        """
        def _on_up(event):
            try:
                # cursor is at first line when row index == 1
                row = int(text_widget.index(tk.INSERT).split('.')[0])
                if row <= 1:
                    prev_widget.focus()
                    return 'break'
            except Exception:
                pass
            return None  # let Text handle it normally

        def _on_down(event):
            try:
                idx   = text_widget.index(tk.INSERT)
                last  = text_widget.index(tk.END + '-1c')
                row   = int(idx.split('.')[0])
                last_row = int(last.split('.')[0])
                if row >= last_row:
                    next_widget.focus()
                    return 'break'
            except Exception:
                pass
            return None

        text_widget.bind('<Up>',   _on_up,   add='+')
        text_widget.bind('<Down>', _on_down, add='+')

    # ── Helpers ───────────────────────────────────────────────────────────

    def _focused_widget(self):
        try:
            return self._root.focus_get()
        except Exception:
            return None

    def _is_input_focused(self):
        w = self._focused_widget()
        if w is None:
            return False
        return _widget_class(w) in _INPUT_CLASSES

    def _focus_first_widget(self, frame):
        """
        Recursively find and focus the first focusable input widget.
        Handles: Entry/Combobox, SearchableCombo (.entry), TwoStepMedicineCombo
        (.step1_entry), and any custom widget with a .focus_entry() method.
        Only visits widgets that are alive (winfo_exists) and mapped (winfo_ismapped).
        """
        try:
            if not frame.winfo_exists():
                return False
            for child in frame.winfo_children():
                # Skip destroyed or unmapped widgets
                try:
                    if not child.winfo_exists() or not child.winfo_ismapped():
                        continue
                except Exception:
                    continue

                if callable(getattr(child, 'focus_entry', None)):
                    child.focus_entry()
                    return True
                if hasattr(child, 'step1_entry'):
                    try:
                        child.step1_entry.focus()
                        return True
                    except Exception:
                        pass
                if hasattr(child, 'entry') and hasattr(child, 'list_visible'):
                    try:
                        child.entry.focus()
                        return True
                    except Exception:
                        pass
                if _widget_class(child) in ('Entry', 'TEntry', 'TCombobox', 'Text'):
                    try:
                        child.focus()
                        return True
                    except Exception:
                        pass
                if self._focus_first_widget(child):
                    return True
        except Exception:
            pass
        return False
