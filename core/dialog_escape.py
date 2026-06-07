"""
Unified Escape-to-close for modal dialogs and alert boxes.

Global page shortcuts (main.py, input_controller) must defer to an open dialog
so Escape closes alerts instead of blurring inputs or exiting tree focus.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

_closing_dialog = False


def _main_toplevel(root: tk.Misc):
    try:
        return root.winfo_toplevel()
    except Exception:
        return root


def focus_toplevel(root: tk.Misc, main_window: Optional[tk.Misc] = None) -> Optional[tk.Toplevel]:
    """Return the Toplevel under focus, if it is not the main application window."""
    main = main_window or _main_toplevel(root)
    try:
        w = root.focus_get()
        while w:
            if isinstance(w, tk.Toplevel):
                try:
                    if not w.winfo_exists():
                        return None
                    if w.state() == 'withdrawn':
                        return None
                except Exception:
                    return None
                if w is not main:
                    return w
            try:
                w = w.master
            except Exception:
                break
    except Exception:
        pass
    return None


def grabbed_toplevel(root: tk.Misc) -> Optional[tk.Toplevel]:
    try:
        g = root.grab_current()
        if g is not None and g.winfo_exists():
            return g
    except Exception:
        pass
    return None


def active_dialog(root: tk.Misc, main_window: Optional[tk.Misc] = None) -> Optional[tk.Toplevel]:
    return grabbed_toplevel(root) or focus_toplevel(root, main_window)


def should_defer_global_escape(root: tk.Misc, main_window: Optional[tk.Misc] = None) -> bool:
    return active_dialog(root, main_window) is not None


def is_modal_open(root: tk.Misc, main_window: Optional[tk.Misc] = None) -> bool:
    return active_dialog(root, main_window) is not None


def _destroy_dialog(dlg: tk.Misc) -> None:
    try:
        if not dlg.winfo_exists():
            return
    except tk.TclError:
        return
    try:
        dlg.grab_release()
    except Exception:
        pass
    try:
        dlg.destroy()
    except tk.TclError:
        pass


def _run_dialog_close(dlg: tk.Misc) -> None:
    """Invoke the dialog's registered close handler once (no synthetic key events)."""
    if getattr(dlg, '_dialog_escape_closed', False):
        return
    close_fn = getattr(dlg, '_dialog_escape_close', None)
    if callable(close_fn):
        close_fn()
        return
    dlg._dialog_escape_closed = True
    _destroy_dialog(dlg)


_SKIP_ENTER_PROPAGATE = frozenset({
    'TButton', 'Button', 'Entry', 'TEntry', 'Text', 'Spinbox', 'TSpinbox',
})


def bind_enter_to_confirm(
    window: tk.Misc,
    on_confirm: Optional[Callable] = None,
    *,
    bind_children: bool = True,
):
    """
    Bind Return / KP_Enter on ``window`` and descendants to confirm (OK/Save).
    Skips buttons and text inputs (they handle Return themselves).
    Returns the confirm handler.
    """

    def confirm(event=None):
        try:
            if callable(on_confirm):
                on_confirm()
        except Exception:
            pass
        return 'break'

    window._dialog_enter_confirm = confirm

    for seq in ('<Return>', '<KP_Enter>'):
        window.bind(seq, confirm, add='+')

    if bind_children:
        _walking = [False]

        def walk(w):
            if _walking[0]:
                return
            _walking[0] = True
            try:
                cls = w.winfo_class()
                if cls not in _SKIP_ENTER_PROPAGATE:
                    w.bind('<Return>', confirm, add='+')
                    w.bind('<KP_Enter>', confirm, add='+')
                for ch in w.winfo_children():
                    walk(ch)
            except Exception:
                pass
            finally:
                _walking[0] = False

        walk(window)

        def _on_map(_event=None):
            walk(window)

        window.bind('<Map>', _on_map, add='+')

    return confirm


def bind_escape_to_close(
    window: tk.Misc,
    on_close: Optional[Callable] = None,
    *,
    bind_children: bool = True,
):
    """
    Bind Escape on ``window`` and descendants to close/cancel the dialog.
    ``on_close`` may be provided; default releases grab and destroys the window.
    Returns the close handler.
    """
    if on_close is None:
        on_close = getattr(window, '_dialog_escape_close', None)

    def close(event=None):
        if getattr(window, '_dialog_escape_closed', False):
            return 'break'
        window._dialog_escape_closed = True
        try:
            if callable(on_close):
                on_close()
            else:
                _destroy_dialog(window)
        except Exception:
            try:
                _destroy_dialog(window)
            except Exception:
                pass
        return 'break'

    window._dialog_escape_close = close

    window.bind('<Escape>', close, add='+')

    if bind_children:
        _walking = [False]

        def walk(w):
            if _walking[0]:
                return
            _walking[0] = True
            try:
                w.bind('<Escape>', close, add='+')
                for ch in w.winfo_children():
                    walk(ch)
            except Exception:
                pass
            finally:
                _walking[0] = False

        walk(window)

        def _on_map(_event=None):
            walk(window)

        window.bind('<Map>', _on_map, add='+')

    return close


def confirm_active_dialog(root: tk.Misc) -> bool:
    """Press Enter on the grabbed modal dialog (fallback for global Return)."""
    dlg = grabbed_toplevel(root)
    if dlg is None:
        return False
    confirm_fn = getattr(dlg, '_dialog_enter_confirm', None)
    if callable(confirm_fn):
        try:
            confirm_fn()
            return True
        except Exception:
            return False
    try:
        if not dlg.winfo_exists():
            return False
    except tk.TclError:
        return False
    return False


def close_active_dialog(root: tk.Misc, main_window: Optional[tk.Misc] = None) -> bool:
    """
    Close the topmost modal/non-main Toplevel (Escape fallback for global handlers).

    Does not use event_generate('<Escape>') — that re-enters bind_all handlers and
    causes RecursionError. Calls the dialog's registered close callback instead.
    """
    global _closing_dialog
    if _closing_dialog:
        return False

    dlg = active_dialog(root, main_window)
    if dlg is None:
        return False

    _closing_dialog = True
    try:
        try:
            if not dlg.winfo_exists():
                return True
        except tk.TclError:
            return True
        _run_dialog_close(dlg)
        return True
    except Exception:
        return False
    finally:
        _closing_dialog = False
