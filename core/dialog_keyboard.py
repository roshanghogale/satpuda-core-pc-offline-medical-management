"""
Arrow-key navigation and Enter activation for modal dialogs.
"""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.focus_chain import safe_focus, wire_focus_ring

_BUTTON_CLASSES = frozenset({'TButton', 'Button'})


def focus_dialog_widget(dlg, widget):
    """Focus a widget after the dialog is shown (handles grab/timing)."""

    def _go():
        safe_focus(widget)

    try:
        dlg.after_idle(_go)
        dlg.after(150, _go)
    except Exception:
        _go()


def _wire_button_activate(widget):
    if widget.winfo_class() not in _BUTTON_CLASSES:
        return

    def _invoke(event=None):
        try:
            widget.invoke()
        except Exception:
            pass
        return 'break'

    widget.bind('<Return>', _invoke, add='+')
    widget.bind('<KP_Enter>', _invoke, add='+')


def wire_dialog_arrow_nav(widgets, dlg=None, *, initial_focus=None):
    """
    Bind Up/Down/Left/Right to cycle focus through widgets.
    Return on buttons invokes them.
    """
    widgets = [w for w in widgets if w is not None]
    if not widgets:
        return []

    nav = wire_focus_ring(widgets)
    for w in nav:
        _wire_button_activate(w)

    target = initial_focus if initial_focus is not None else nav[0]
    if dlg is not None and target is not None:
        focus_dialog_widget(dlg, target)
    return nav


def wire_radiobutton_values(radiobuttons, variable, values):
    """Selecting focus updates the linked variable (for arrow-key navigation)."""
    for rb, val in zip(radiobuttons, values):
        rb.bind('<FocusIn>', lambda e, v=val: variable.set(v), add='+')


def wire_export_option_listbox(dlg, lb, on_select):
    """
    Export report picker: ↑↓ on list, Enter / double-click to choose.
    """
    def _run(event=None):
        try:
            on_select()
        except Exception:
            pass
        return 'break'

    lb.bind('<Return>', _run, add='+')
    lb.bind('<KP_Enter>', _run, add='+')
    lb.bind('<Double-Button-1>', lambda e: on_select(), add='+')
    focus_dialog_widget(dlg, lb)


def wire_export_format_dialog(
    dlg,
    radiobuttons,
    fmt_var,
    fmt_values,
    export_btn,
    cancel_btn,
):
    """Format chooser: ↑↓ across radios + Export/Cancel, Return activates buttons."""
    wire_radiobutton_values(radiobuttons, fmt_var, fmt_values)
    wire_dialog_arrow_nav(
        list(radiobuttons) + [export_btn, cancel_btn],
        dlg,
        initial_focus=radiobuttons[0] if radiobuttons else export_btn,
    )
