"""
ScrollManager — single reusable scrollable frame factory.

Usage (inside any page):
    from core.scroll_manager import make_scrollable
    inner = make_scrollable(self.parent)
    # build your UI inside `inner`

The returned `inner` frame fills the parent.
A vertical scrollbar appears only when content height > viewport height.
Mouse-wheel scrolling works even when the cursor is over Entry, Combobox,
Treeview, or any other child widget.
"""

import os
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

# Widget classes that should NOT be intercepted for page-level scrolling
# (they handle their own internal scroll)
_TREEVIEW_CLASSES = {'Treeview'}
_INPUT_CLASSES    = {'Entry', 'TEntry', 'TCombobox', 'Text', 'Spinbox', 'TSpinbox'}


def _widget_class(widget):
    try:
        return widget.winfo_class()
    except Exception:
        return ''


def _should_scroll_page(widget):
    """Return True if a mousewheel event on `widget` should scroll the page canvas."""
    try:
        cls = widget.winfo_class()
    except Exception:
        return True
    # Treeview scrolls its own rows — let it handle the event
    if cls in _TREEVIEW_CLASSES:
        return False
    return True


def make_scrollable(parent, horizontal=False):
    """
    Create a scrollable frame inside `parent`.

    Returns the inner ttk.Frame where you should place all child widgets.
    The canvas reference is stored as `inner._canvas` for programmatic access
    (e.g. scroll-to-widget).

    Parameters
    ----------
    parent      : tk widget — the container (e.g. self.parent in a page class)
    horizontal  : bool — also add a horizontal scrollbar (default False)
    """
    container = ttk.Frame(parent)
    container.pack(fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(container, highlightthickness=0)
    vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)

    if horizontal:
        hsb = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hsb.set)
    else:
        hsb = None

    inner = ttk.Frame(canvas)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    # Track scrollbar visibility to avoid redundant pack/forget calls
    _vsb_shown = [False]
    _hsb_shown = [False]

    def _update_scrollbars():
        ch = canvas.winfo_height()
        cw = canvas.winfo_width()
        ih = inner.winfo_reqheight()
        iw = inner.winfo_reqwidth()

        need_v = ih > ch
        if need_v != _vsb_shown[0]:
            _vsb_shown[0] = need_v
            if need_v:
                vsb.pack(side="right", fill="y")
            else:
                vsb.pack_forget()
                canvas.yview_moveto(0)  # reset scroll position when not needed

        if hsb is not None:
            need_h = iw > cw
            if need_h != _hsb_shown[0]:
                _hsb_shown[0] = need_h
                if need_h:
                    hsb.pack(side="bottom", fill="x")
                else:
                    hsb.pack_forget()
                    canvas.xview_moveto(0)

    def _on_inner_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
        _update_scrollbars()

    def _on_canvas_configure(event):
        # Make inner frame fill canvas width (never height — that breaks scrolling)
        canvas.itemconfig(win_id, width=event.width)
        _update_scrollbars()

    inner.bind("<Configure>", _on_inner_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    # ── Mouse-wheel handling ──────────────────────────────────────────────
    def _scroll_canvas(delta):
        """Scroll only when content is taller than viewport."""
        try:
            if not inner.winfo_exists():
                return
            if inner.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(int(-1 * delta), "units")
        except Exception:
            pass

    def _on_mousewheel_win(event):
        if _should_scroll_page(event.widget):
            _scroll_canvas(event.delta / 120)

    def _on_mousewheel_linux_up(event):
        if _should_scroll_page(event.widget):
            _scroll_canvas(1)

    def _on_mousewheel_linux_down(event):
        if _should_scroll_page(event.widget):
            _scroll_canvas(-1)

    # Bind to canvas and inner frame
    canvas.bind("<MouseWheel>", _on_mousewheel_win)
    canvas.bind("<Button-4>",   _on_mousewheel_linux_up)
    canvas.bind("<Button-5>",   _on_mousewheel_linux_down)
    inner.bind("<MouseWheel>", _on_mousewheel_win)
    inner.bind("<Button-4>",   _on_mousewheel_linux_up)
    inner.bind("<Button-5>",   _on_mousewheel_linux_down)

    # Bind scroll on every child widget as it is mapped, so scrolling works
    # regardless of which child the cursor is over.
    def _bind_child_scroll(widget):
        try:
            if _widget_class(widget) not in _TREEVIEW_CLASSES:
                widget.bind("<MouseWheel>", _on_mousewheel_win,        add='+')
                widget.bind("<Button-4>",   _on_mousewheel_linux_up,   add='+')
                widget.bind("<Button-5>",   _on_mousewheel_linux_down, add='+')
        except Exception:
            pass

    def _is_descendant(w):
        """Return True if widget w is a descendant of inner."""
        try:
            parent = w
            inner_id = str(inner)
            while parent is not None:
                if str(parent) == inner_id:
                    return True
                try:
                    parent = parent.nametowidget(parent.winfo_parent())
                except Exception:
                    break
        except Exception:
            pass
        return False

    def _on_map(event):
        try:
            if not inner.winfo_exists():
                return
            w = event.widget
            if _is_descendant(w):
                _bind_child_scroll(w)
        except Exception:
            pass

    inner.bind('<Map>', _on_map, add='+')

    # Store canvas reference on inner for scroll_to_widget helper
    inner._canvas = canvas

    canvas.pack(side="left", fill="both", expand=True)
    return inner


def scroll_to_widget(inner_frame, widget):
    """
    Scroll the canvas that owns `inner_frame` so that `widget` is visible.

    Call this after focus changes (e.g. arrow-key navigation).
    """
    canvas = getattr(inner_frame, '_canvas', None)
    if canvas is None:
        return
    try:
        canvas.update_idletasks()
        # widget Y relative to canvas viewport top
        wy = widget.winfo_rooty() - canvas.winfo_rooty()
        wh = widget.winfo_height()
        ch = canvas.winfo_height()
        bbox = canvas.bbox('all')
        if not bbox:
            return
        total_h = bbox[3]
        if total_h <= ch:
            return  # no scrolling needed

        top_frac = canvas.yview()[0]
        top_px   = top_frac * total_h
        w_top    = wy + top_px
        w_bot    = w_top + wh

        if w_top < top_px:
            canvas.yview_moveto(max(0, w_top - 10) / total_h)
        elif w_bot > top_px + ch:
            canvas.yview_moveto(min(1, (w_bot - ch + 10) / total_h))
    except Exception:
        pass


def _apply_icon(window):
    """Apply satpuda_logo.ico to any Tk/Toplevel window (title bar + taskbar)."""
    try:
        from core.license_manager import get_icon_path
        ico = get_icon_path()
        if os.path.exists(ico):
            window.iconbitmap(ico)
    except Exception:
        pass


def open_dialog(parent, title, width=None, height=None, resizable=True):
    """
    Create a standard modal Toplevel dialog.
    Every dialog gets the Satpuda Core icon in the top-left corner.
    """
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.resizable(resizable, resizable)

    # Set window icon
    _apply_icon(dlg)

    def _finalise():
        dlg.update_idletasks()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        max_w = int(sw * 0.90)
        max_h = int(sh * 0.90)

        w = min(width  if width  else dlg.winfo_reqwidth(),  max_w)
        h = min(height if height else dlg.winfo_reqheight(), max_h)

        x = (sw - w) // 2
        y = (sh - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

        if not resizable:
            dlg.minsize(w, h)
            dlg.maxsize(w, h)

        try:
            dlg.grab_set()
        except Exception:
            pass
        try:
            dlg.focus_force()
        except Exception:
            pass

    dlg.after(10, _finalise)
    dlg.bind('<Escape>', lambda e: dlg.destroy())
    return dlg


def bind_mousewheel_to_widget(widget, inner_frame):
    """
    Explicitly forward mousewheel events from a specific widget (e.g. a
    Treeview that has its own internal scroll) to the page canvas AS WELL.

    Use this when you want BOTH the Treeview to scroll its rows AND the page
    to scroll when the Treeview has no more rows to scroll.

    Note: This is optional — by default Treeview consumes the event.
    Only call this if you want pass-through behaviour.
    """
    canvas = getattr(inner_frame, '_canvas', None)
    if canvas is None:
        return

    def _fwd_win(event):
        if inner_frame.winfo_reqheight() > canvas.winfo_height():
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _fwd_up(event):
        if inner_frame.winfo_reqheight() > canvas.winfo_height():
            canvas.yview_scroll(-1, "units")

    def _fwd_down(event):
        if inner_frame.winfo_reqheight() > canvas.winfo_height():
            canvas.yview_scroll(1, "units")

    widget.bind("<MouseWheel>", _fwd_win,  add='+')
    widget.bind("<Button-4>",   _fwd_up,   add='+')
    widget.bind("<Button-5>",   _fwd_down, add='+')
