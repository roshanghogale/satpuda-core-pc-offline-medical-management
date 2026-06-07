"""Keyboard-navigable action menu for Treeview rows (Enter / arrows / Esc)."""
from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional, Sequence, Tuple, Union

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

ActionItem = Union[
    Tuple[str, Callable[[], None]],
    Tuple[str, Callable],
    None,
    str,
]

RIGHT_CLICK_BINDINGS = ("<Button-3>", "<Button-2>", "<Control-Button-1>")


class TreeActionMenu:
    """Popup list menu: ↑↓ move, Enter run, Escape close."""

    def __init__(self, parent, tree):
        self.parent = parent
        self.tree = tree
        self._entries: List[Tuple[str, Optional[Callable]]] = []
        self._actions_factory: Optional[Callable[[], Sequence[ActionItem]]] = None
        self._popup = None
        self._listbox = None
        self.ctx_menu: Optional[tk.Menu] = None

    def clear(self):
        self._entries = []

    def set_actions_factory(self, factory: Callable[[], Sequence[ActionItem]]):
        self._actions_factory = factory

    def load_actions(self, actions: Sequence[ActionItem]):
        self.clear()
        for item in actions:
            if item is None or item == "---":
                self.add_separator()
            else:
                label, command = item
                self.add_command(label, command)

    def refresh_from_factory(self):
        if self._actions_factory:
            self.load_actions(self._actions_factory())

    def add_command(self, label, command):
        self._entries.append((label, command))

    def add_separator(self):
        self._entries.append(("---", None))

    def bind_tree(self, *, on_double=None):
        """Double-click only; use wire_tree_list(on_return=...) for Enter."""
        if on_double:
            def _dbl(event=None):
                on_double()
                return "break"

            self.tree.bind("<Double-1>", _dbl, add="+")

    def _actions(self):
        return [(lbl, fn) for lbl, fn in self._entries if fn is not None]

    def on_enter_key(self, event=None):
        if getattr(self, "_opening", False):
            return "break"
        self._opening = True
        try:
            if not self.tree.selection():
                children = self.tree.get_children()
                if children:
                    self.tree.selection_set(children[0])
                    self.tree.focus(children[0])
                else:
                    return "break"
            self.refresh_from_factory()
            self.show(event)
            return "break"
        finally:
            self._opening = False

    def show(self, event=None):
        actions = self._actions()
        if not actions:
            return
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()

        self._popup = tk.Toplevel(self.parent)
        self._popup.title("Actions")
        self._popup.transient(self.parent.winfo_toplevel())
        self._popup.resizable(False, False)
        self._popup.overrideredirect(False)

        frm = ttk.Frame(self._popup, padding=4)
        frm.pack(fill=tk.BOTH, expand=True)
        self._listbox = tk.Listbox(
            frm, height=min(len(actions), 8), width=28, exportselection=False
        )
        self._listbox.pack(fill=tk.BOTH, expand=True)
        for lbl, _ in actions:
            self._listbox.insert(tk.END, lbl)
        self._listbox.selection_set(0)
        self._listbox.focus_set()

        def _run():
            sel = self._listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            if 0 <= idx < len(actions):
                self._close()
                try:
                    actions[idx][1]()
                except Exception:
                    pass

        def _on_key(event):
            if event.keysym in ("Return", "KP_Enter"):
                _run()
                return "break"
            if event.keysym == "Escape":
                self._close()
                try:
                    self.tree.focus_set()
                except Exception:
                    pass
                return "break"
            if event.keysym == "Up" and self._listbox.curselection():
                i = self._listbox.curselection()[0]
                if i > 0:
                    self._listbox.selection_clear(i)
                    self._listbox.selection_set(i - 1)
                    self._listbox.activate(i - 1)
                return "break"
            if event.keysym == "Down" and self._listbox.curselection():
                i = self._listbox.curselection()[0]
                if i < self._listbox.size() - 1:
                    self._listbox.selection_clear(i)
                    self._listbox.selection_set(i + 1)
                    self._listbox.activate(i + 1)
                return "break"
            return None

        self._listbox.bind("<Return>", _on_key, add="+")
        self._listbox.bind("<Double-Button-1>", lambda e: _run(), add="+")
        self._popup.bind("<Escape>", _on_key, add="+")
        self._listbox.bind("<KeyPress>", _on_key, add="+")
        self._popup.protocol("WM_DELETE_WINDOW", self._close)
        try:
            from core.dialog_escape import bind_escape_to_close

            def _close_popup():
                self._close()
                try:
                    self.tree.focus_set()
                except Exception:
                    pass

            bind_escape_to_close(self._popup, on_close=_close_popup)
        except Exception:
            pass
        try:
            self._popup.grab_set()
        except Exception:
            pass

        try:
            sel = self.tree.selection()
            if sel:
                bbox = self.tree.bbox(sel[0])
                if bbox:
                    x = self.tree.winfo_rootx() + bbox[0]
                    y = self.tree.winfo_rooty() + bbox[1] + bbox[3]
                    self._popup.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _close(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None


def _sync_ctx_menu(ctx: tk.Menu, menu: TreeActionMenu):
    ctx.delete(0, tk.END)
    for label, fn in menu._entries:
        if fn is None:
            ctx.add_separator()
        else:
            ctx.add_command(label=label, command=fn)


def setup_tree_actions(
    parent,
    tree,
    actions: Sequence[ActionItem],
    *,
    on_double=None,
    escape_to=None,
    on_delete=None,
    actions_factory: Optional[Callable[[], Sequence[ActionItem]]] = None,
):
    """
    Wire TreeActionMenu + right-click menu + Enter/Delete/Escape on a tree.

    Returns the TreeActionMenu instance (.ctx_menu is the tk.Menu for right-click).
    """
    action_menu = TreeActionMenu(parent, tree)
    if actions_factory is not None:
        action_menu.set_actions_factory(actions_factory)
    else:
        action_menu.load_actions(actions)

    ctx = tk.Menu(parent, tearoff=0)
    action_menu.ctx_menu = ctx

    def _show_ctx(event):
        row = tree.identify_row(event.y)
        if row:
            tree.selection_set(row)
            tree.focus(row)
        action_menu.refresh_from_factory()
        if not action_menu._actions():
            return
        _sync_ctx_menu(ctx, action_menu)
        if tree.selection():
            ctx.post(event.x_root, event.y_root)

    for ev in RIGHT_CLICK_BINDINGS:
        tree.bind(ev, _show_ctx, add="+")

    if on_double:
        action_menu.bind_tree(on_double=on_double)

    from core.focus_chain import wire_tree_list

    wire_tree_list(
        tree,
        on_return=action_menu.on_enter_key,
        on_delete=on_delete,
        escape_to=escape_to,
    )
    return action_menu
