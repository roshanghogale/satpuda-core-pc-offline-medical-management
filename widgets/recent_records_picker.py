"""
Dialog to pick one of the recent sales or purchases for quick edit.
"""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS
from core.scroll_manager import open_dialog
from core.dialog_escape import bind_escape_to_close


def show_recent_records_picker(parent, title: str, rows: list, on_select):
    """
    Show a list of recent records. rows: list of (id, no, date, party, amount).
    on_select(record_id) called when user presses Enter on a row.
    """
    if not rows:
        from core.themed_messagebox import showinfo
        showinfo('No Records', 'No saved records found yet.', parent=parent)
        return

    dlg = open_dialog(parent, title, width=720, height=420, resizable=True)
    body = dlg.content

    cols = ('No', 'Date', 'Party', 'Amount')
    tree = ttk.Treeview(
        body, columns=cols, show='headings', height=8,
        style='Large.Treeview', selectmode='browse', takefocus=True,
    )
    for col, w in zip(cols, (140, 110, 260, 100)):
        tree.heading(col, text=col)
        tree.column(col, width=w)
    tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    for row in rows:
        rid, no, dt, party, amount = row
        dt_str = str(dt or '')
        if ' ' in dt_str:
            dt_str = dt_str.split(' ')[0]
        tree.insert('', tk.END, iid=str(rid), values=(
            no or rid, dt_str, party or '', f'₹{float(amount or 0):.2f}',
        ))

    hint = ttk.Label(
        body,
        text='↑↓ to move • Enter to load into form • Esc to close',
        font=(FONT_FAMILY, FONT_SIZE_LABELS - 1),
    )
    hint.pack(anchor=tk.W, padx=10, pady=(0, 6))

    def _children():
        return list(tree.get_children())

    def _select_index(idx: int):
        children = _children()
        if not children:
            return
        idx = max(0, min(len(children) - 1, idx))
        iid = children[idx]
        tree.selection_set(iid)
        tree.focus(iid)
        tree.see(iid)

    def _choose(event=None):
        sel = tree.selection()
        if not sel:
            children = _children()
            if not children:
                return 'break'
            _select_index(0)
            sel = tree.selection()
        on_select(int(sel[0]))
        dlg.destroy()
        return 'break'

    def _move_selection(delta, event=None):
        children = _children()
        if not children:
            return 'break'
        sel = tree.selection()
        idx = children.index(sel[0]) if sel else 0
        _select_index(idx + int(delta))
        return 'break'

    def _focus_tree(*_):
        try:
            tree.focus_set()
            children = _children()
            if children and not tree.selection():
                _select_index(0)
            elif tree.selection():
                tree.focus(tree.selection()[0])
        except tk.TclError:
            pass

    tree.bind('<Up>', lambda e: _move_selection(-1, e))
    tree.bind('<Down>', lambda e: _move_selection(1, e))
    tree.bind('<KeyPress-Up>', lambda e: _move_selection(-1, e))
    tree.bind('<KeyPress-Down>', lambda e: _move_selection(1, e))
    tree.bind('<KP_Up>', lambda e: _move_selection(-1, e))
    tree.bind('<KP_Down>', lambda e: _move_selection(1, e))
    tree.bind('<Return>', _choose)
    tree.bind('<KP_Enter>', _choose)
    tree.bind('<Double-1>', _choose)

    # Route keys on the dialog too so arrows work once the list is focused.
    for seq, delta in (
        ('<Up>', -1), ('<Down>', 1), ('<KeyPress-Up>', -1), ('<KeyPress-Down>', 1),
        ('<KP_Up>', -1), ('<KP_Down>', 1),
    ):
        dlg.bind(seq, lambda e, d=delta: _move_selection(d, e), add='+')
    dlg.bind('<Return>', _choose, add='+')
    dlg.bind('<KP_Enter>', _choose, add='+')

    _select_index(0)
    dlg.bind('<Map>', lambda _e: dlg.after(80, _focus_tree), add='+')
    dlg.after(220, _focus_tree)
    bind_escape_to_close(dlg, on_close=dlg.destroy)

    ttk.Button(dlg.footer, text='Load Selected (Enter)', command=_choose).pack(side=tk.RIGHT, padx=6)
    ttk.Button(dlg.footer, text='Cancel', command=dlg.destroy).pack(side=tk.RIGHT, padx=6)
