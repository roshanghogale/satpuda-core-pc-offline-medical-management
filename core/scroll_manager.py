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
_LISTBOX_CLASSES = {'Listbox'}
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
    if cls in _TREEVIEW_CLASSES or cls in _LISTBOX_CLASSES:
        return False
    return True


def _canvas_needs_scroll(canvas, inner_frame):
    try:
        canvas.update_idletasks()
        inner_frame.update_idletasks()
        bbox = canvas.bbox('all')
        if bbox:
            content_h = bbox[3] - bbox[1]
            if content_h > max(canvas.winfo_height(), 1):
                return True
        return inner_frame.winfo_reqheight() > max(canvas.winfo_height(), 1)
    except Exception:
        return False


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
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)

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

    _vsb_shown = [False]
    _hsb_shown = [False]

    def _content_height():
        try:
            inner.update_idletasks()
            canvas.update_idletasks()
            bbox = canvas.bbox('all')
            if bbox:
                return bbox[3] - bbox[1]
            return inner.winfo_reqheight()
        except Exception:
            return 0

    def _update_scrollbars():
        ch = max(canvas.winfo_height(), 1)
        cw = max(canvas.winfo_width(), 1)
        content_h = _content_height()
        iw = inner.winfo_reqwidth()

        need_v = content_h > ch
        if need_v != _vsb_shown[0]:
            _vsb_shown[0] = need_v
            if need_v:
                vsb.grid(row=0, column=1, sticky='ns')
            else:
                vsb.grid_remove()
                canvas.yview_moveto(0)

        if hsb is not None:
            need_h = iw > cw
            if need_h != _hsb_shown[0]:
                _hsb_shown[0] = need_h
                if need_h:
                    hsb.grid(row=1, column=0, sticky='ew')
                else:
                    hsb.grid_remove()
                    canvas.xview_moveto(0)

    def _on_inner_configure(event):
        canvas.configure(scrollregion=canvas.bbox('all'))
        _update_scrollbars()

    def _on_canvas_configure(event):
        # Width only — setting height on the window item breaks scrolling on Windows.
        canvas.itemconfig(win_id, width=max(event.width, 1))
        _update_scrollbars()

    inner.bind("<Configure>", _on_inner_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    # ── Mouse-wheel handling ──────────────────────────────────────────────
    def _scroll_canvas(delta):
        """Scroll only when content is taller than viewport."""
        try:
            if not inner.winfo_exists():
                return
            if _canvas_needs_scroll(canvas, inner):
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

    inner._canvas = canvas
    canvas._inner_frame = inner
    inner._canvas_win_id = win_id
    inner._scroll_container = container
    inner._update_scrollbars = _update_scrollbars

    # Wheel on container catches events over any child in the scroll area.
    container.bind('<MouseWheel>', _on_mousewheel_win)
    container.bind('<Button-4>', _on_mousewheel_linux_up)
    container.bind('<Button-5>', _on_mousewheel_linux_down)

    canvas.grid(row=0, column=0, sticky='nsew')
    bind_scroll_descendants(inner, force=True)
    return inner


def refresh_scroll_region(inner_frame):
    """Recompute scrollregion and scrollbar visibility (e.g. after showing a panel)."""
    canvas = getattr(inner_frame, '_canvas', None)
    if canvas is None:
        return
    try:
        inner_frame.update_idletasks()
        bbox = canvas.bbox('all')
        if bbox:
            canvas.configure(scrollregion=bbox)
        updater = getattr(inner_frame, '_update_scrollbars', None)
        if updater:
            updater()
    except Exception:
        pass


def bind_scroll_descendants(inner_frame, force=False):
    """
    Mouse-wheel bind on descendants (for pages without GlobalInputController).
    Pass force=True after showing hidden panels (e.g. Appearance sections).
    """
    if getattr(inner_frame, '_scroll_descendants_bound', False) and not force:
        return
    canvas = getattr(inner_frame, '_canvas', None)
    if canvas is None:
        return
    inner_frame._scroll_descendants_bound = True

    def _scroll_canvas(delta):
        try:
            if not inner_frame.winfo_exists():
                return
            if _canvas_needs_scroll(canvas, inner_frame):
                canvas.yview_scroll(int(-1 * delta), 'units')
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

    def _walk(widget):
        try:
            if not widget.winfo_exists():
                return
            if _widget_class(widget) not in _TREEVIEW_CLASSES and _widget_class(widget) not in _LISTBOX_CLASSES:
                if not getattr(widget, '_page_scroll_wheel', False):
                    widget.bind('<MouseWheel>', _on_mousewheel_win, add='+')
                    widget.bind('<Button-4>', _on_mousewheel_linux_up, add='+')
                    widget.bind('<Button-5>', _on_mousewheel_linux_down, add='+')
                    widget._page_scroll_wheel = True
        except Exception:
            pass
        for child in widget.winfo_children():
            _walk(child)

    container = getattr(inner_frame, '_scroll_container', None)
    if container is not None:
        _walk(container)
    else:
        _walk(inner_frame)
    refresh_scroll_region(inner_frame)


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
    """Apply satpuda_logo to any Tk/Toplevel window (title bar + taskbar)."""
    try:
        from core.window_icon import apply_window_icon
        master = None
        try:
            master = window.winfo_toplevel()
        except Exception:
            pass
        apply_window_icon(window, master=master or window)
    except Exception:
        try:
            from core.license_manager import get_icon_path
            ico = get_icon_path()
            if os.path.exists(ico):
                window.iconbitmap(ico)
        except Exception:
            pass


def _apply_dialog_theme(dlg):
    """Apply ttkbootstrap theme background to a Toplevel dialog."""
    try:
        import ttkbootstrap as _ttk
        bg = _ttk.Style().colors.bg
        dlg.configure(bg=bg)
    except Exception:
        pass


def make_dialog_scrollable(parent):
    """
    Scrollable body for a dialog. Pack/grid widgets into the returned inner frame.
    Footer buttons should go in dlg.footer (see open_dialog), not inside this frame.
    """
    parent.grid_rowconfigure(0, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
    vsb = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    canvas.grid(row=0, column=0, sticky='nsew')
    vsb.grid(row=0, column=1, sticky='ns')

    inner = ttk.Frame(canvas, padding=2)
    win_id = canvas.create_window((0, 0), window=inner, anchor='nw')
    inner._dialog_canvas = canvas
    inner._dialog_win_id = win_id

    def _on_inner_configure(_event=None):
        try:
            canvas.configure(scrollregion=canvas.bbox('all'))
            ch = max(canvas.winfo_height(), 1)
            cw = max(canvas.winfo_width(), 1)
            canvas.itemconfig(win_id, width=cw)
            content_h = inner.winfo_reqheight()
            if content_h <= ch:
                vsb.grid_remove()
                canvas.yview_moveto(0)
            else:
                vsb.grid(row=0, column=1, sticky='ns')
        except Exception:
            pass

    def _on_canvas_configure(event):
        try:
            canvas.itemconfig(win_id, width=event.width)
        except Exception:
            pass

    inner.bind('<Configure>', _on_inner_configure)
    canvas.bind('<Configure>', _on_canvas_configure)

    def _wheel(event):
        try:
            if inner.winfo_reqheight() <= max(canvas.winfo_height(), 1):
                return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        except Exception:
            pass

    def _bind_wheel(w):
        try:
            w.bind('<MouseWheel>', _wheel, add='+')
            w.bind('<Button-4>', lambda e: canvas.yview_scroll(-1, 'units'), add='+')
            w.bind('<Button-5>', lambda e: canvas.yview_scroll(1, 'units'), add='+')
        except Exception:
            pass
        for ch in w.winfo_children():
            _bind_wheel(ch)

    inner.bind('<Map>', lambda e: (_on_inner_configure(), _bind_wheel(inner)))
    return inner


def finalize_dialog_geometry(dlg, width=None, height=None, resizable=True):
    """Size dialog to fit content + footer, never taller/wider than the screen."""
    from core.window_icon import center_window_on_screen, get_screen_work_area

    dlg.update_idletasks()
    max_w, max_h, _sw, _sh = get_screen_work_area(dlg)

    req_w = max(dlg.winfo_reqwidth(), 280)
    req_h = max(dlg.winfo_reqheight(), 120)

    if width:
        w = min(max(int(width), 280), max_w)
    else:
        w = min(req_w, max_w)

    if height:
        h = min(int(height), max_h)
    else:
        h = min(req_h, max_h)

    center_window_on_screen(dlg, w, h)
    if resizable:
        dlg.minsize(min(280, w), min(120, h))
        dlg.maxsize(max_w, max_h)
    else:
        dlg.minsize(w, h)
        dlg.maxsize(w, h)


def ensure_toplevel_fits_screen(
    win, width=None, height=None, resizable=None, footer_px=56,
):
    """Apply standard screen limits to any Toplevel (legacy / custom dialogs)."""
    win.update_idletasks()
    from core.window_icon import get_screen_work_area, center_window_on_screen

    max_w, max_h, _sw, _sh = get_screen_work_area(win)
    req_w = max(win.winfo_reqwidth(), 280)
    req_h = max(win.winfo_reqheight(), 120) + int(footer_px or 0)

    w = min(width if width else req_w, max_w)
    if height:
        h = min(int(height), max_h)
    else:
        h = min(req_h, max_h)

    center_window_on_screen(win, w, h)
    if resizable is None:
        try:
            resizable = bool(win.resizable()[0])
        except Exception:
            resizable = True
    if resizable:
        win.minsize(min(280, w), min(120, h))
        win.maxsize(max_w, max_h)
    else:
        win.minsize(w, h)
        win.maxsize(w, h)


def _show_dialog_modal(dlg, parent):
    try:
        from core.window_icon import show_modal_toplevel
        show_modal_toplevel(dlg, parent)
    except Exception:
        try:
            dlg.transient(parent.winfo_toplevel())
            dlg.lift()
            dlg.focus_force()
            dlg.grab_set()
        except Exception:
            pass


def open_dialog(parent, title, width=None, height=None, resizable=True):
    """
    Standard modal dialog: scrollable content (dlg.content) + fixed footer (dlg.footer).

    Place lists, inputs, and trees in dlg.content. Place action buttons in dlg.footer
    so they stay visible. Geometry accounts for both and never exceeds screen height.
    """
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.resizable(resizable, resizable)
    _apply_dialog_theme(dlg)
    _apply_icon(dlg)

    dlg.grid_rowconfigure(0, weight=1)
    dlg.grid_rowconfigure(1, weight=0)
    dlg.grid_columnconfigure(0, weight=1)

    body_wrap = ttk.Frame(dlg)
    body_wrap.grid(row=0, column=0, sticky='nsew')
    body_wrap.grid_rowconfigure(0, weight=1)
    body_wrap.grid_columnconfigure(0, weight=1)

    content = make_dialog_scrollable(body_wrap)

    footer_shell = ttk.Frame(dlg)
    footer_shell.grid(row=1, column=0, sticky='ew')
    ttk.Separator(footer_shell, orient='horizontal').pack(fill=tk.X)
    footer = ttk.Frame(footer_shell, padding=(10, 8))
    footer.pack(fill=tk.X)

    dlg.content = content
    dlg.body = content
    dlg.footer = footer
    dlg._dialog_width = width
    dlg._dialog_height = height
    dlg._dialog_resizable = resizable

    def _finalise():
        finalize_dialog_geometry(
            dlg, dlg._dialog_width, dlg._dialog_height, dlg._dialog_resizable,
        )
        _show_dialog_modal(dlg, parent)
        try:
            inner = dlg.content
            canvas = getattr(inner, '_dialog_canvas', None)
            if canvas:
                inner.event_generate('<Configure>')
        except Exception:
            pass

    dlg.after(1, _finalise)
    dlg.after(120, _finalise)

    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(dlg)
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
