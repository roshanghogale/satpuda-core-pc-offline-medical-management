"""Reusable focus-ring and tree keyboard helpers."""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


def _widget_entry(w):
    return w.entry if hasattr(w, 'entry') else w


def _cursor_at_start(widget, keysym):
    if keysym != 'Left':
        return False
    try:
        return widget.index(tk.INSERT) <= 0
    except Exception:
        return True


def _cursor_at_end(widget, keysym):
    if keysym != 'Right':
        return False
    try:
        return widget.index(tk.INSERT) >= len(widget.get())
    except Exception:
        return True


def wire_focus_ring(
    widgets,
    *,
    scroll_to=None,
    updown_indices=None,
    horizontal_only=None,
):
    """Bind Up/Down/Left/Right to cycle through widgets in order."""
    nav = [_widget_entry(w) for w in widgets]
    n = len(nav)
    updown = set(updown_indices or range(n))
    horizontal_only = set(horizontal_only or [])

    def make_next(i):
        def handler(event):
            if event.keysym in ('Left', 'Right') and not _cursor_at_end(nav[i], event.keysym):
                return None
            target = nav[(i + 1) % n]
            target.focus_set()
            if scroll_to:
                scroll_to(target)
            return 'break'
        return handler

    def make_prev(i):
        def handler(event):
            if event.keysym in ('Left', 'Right') and not _cursor_at_start(nav[i], event.keysym):
                return None
            target = nav[(i - 1) % n]
            target.focus_set()
            if scroll_to:
                scroll_to(target)
            return 'break'
        return handler

    for i, w in enumerate(nav):
        if i in updown and i not in horizontal_only:
            w.bind('<Up>', make_prev(i), add='+')
            w.bind('<Down>', make_next(i), add='+')
        if i not in horizontal_only or i in updown:
            w.bind('<Left>', make_prev(i), add='+')
            w.bind('<Right>', make_next(i), add='+')
    return nav


def wire_return_chain(widgets, actions=None):
    """
    widgets: list of widgets
    actions: optional list same length — callable or next index; last may be save fn
    """
    actions = actions or []
    entries = [_widget_entry(w) for w in widgets]
    for i, w in enumerate(entries):
        nxt = actions[i] if i < len(actions) else (entries[i + 1] if i < len(entries) - 1 else None)

        def bind_return(idx, target):
            def handler(event=None):
                if callable(target):
                    target()
                elif target is not None:
                    target.focus_set()
                return 'break'
            entries[idx].bind('<Return>', handler, add='+')
            entries[idx].bind('<KP_Enter>', handler, add='+')

        bind_return(i, nxt)


def wire_tree_list(
    tree,
    *,
    on_return=None,
    on_delete=None,
    escape_to=None,
    on_double=None,
):
    def _tree_handler(fn):
        def _wrapped(e):
            try:
                r = fn(e)
            except TypeError:
                r = fn()
            return r if r else "break"
        return _wrapped

    if on_double or on_return:
        tree.bind('<Double-1>', _tree_handler(on_double or on_return), add='+')
    if on_return:
        tree.bind('<Return>', _tree_handler(on_return), add='+')
    if on_delete:
        tree.bind('<Delete>', _tree_handler(on_delete), add='+')
    if escape_to:
        def _esc(e):
            try:
                tree.selection_remove(tree.selection())
            except Exception:
                pass
            tgt = escape_to() if callable(escape_to) else escape_to
            if tgt is not None:
                try:
                    tgt.focus_set()
                except Exception:
                    pass
            return 'break'
        tree.bind('<Escape>', _esc, add='+')


def focus_tree(tree):
    try:
        children = tree.get_children()
        if not children:
            return False
        sel = tree.selection()
        row = sel[0] if sel else children[0]
        tree.selection_set(row)
        tree.focus(row)
        tree.focus_set()
        tree.see(row)
        return True
    except Exception:
        return False


def wire_combo_filter_chain(*combos):
    """Enter on each SearchableCombo applies filter and moves to the next combo."""
    items = list(combos)
    for i, combo in enumerate(items):
        nxt = items[i + 1] if i < len(items) - 1 else None
        if nxt is not None:
            combo.next_focus_widget = nxt.focus


def wire_entry_filter_chain(*entries, last_action=None):
    """Return key moves through plain Entry widgets; last runs optional action."""

    def _bind_go(entry, action):
        def handler(event=None):
            action()
            return 'break'
        entry.bind('<Return>', handler, add='+')
        entry.bind('<KP_Enter>', handler, add='+')

    for i, entry in enumerate(entries):
        if i < len(entries) - 1:
            nxt = entries[i + 1]
            _bind_go(entry, lambda n=nxt: n.focus_set())
        elif callable(last_action):
            _bind_go(entry, last_action)


def safe_focus(widget):
    if widget is None:
        return False
    try:
        w = _widget_entry(widget)
        if w is not None and w.winfo_exists():
            w.focus_set()
            return True
    except tk.TclError:
        pass
    return False
