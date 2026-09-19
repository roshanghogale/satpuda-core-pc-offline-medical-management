"""Cached main-window pages — show/hide instead of destroy/recreate."""

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


class MainPageCache:
    """One container frame per logical page; pack_forget when hidden."""

    def __init__(self, main_frame):
        self.main_frame = main_frame
        self._containers = {}
        self._visible_key = None

    def hide_all(self):
        if self._visible_key is None:
            return
        container = self._containers.get(self._visible_key)
        key = self._visible_key
        self._visible_key = None
        if container is None:
            return
        try:
            if container.winfo_exists():
                container.pack_forget()
        except tk.TclError:
            pass

    def invalidate_all(self):
        for container in list(self._containers.values()):
            try:
                if container.winfo_exists():
                    container.destroy()
            except tk.TclError:
                pass
        self._containers.clear()
        self._visible_key = None

    def _valid(self, key):
        container = self._containers.get(key)
        if container is None:
            return False
        try:
            return container.winfo_exists()
        except tk.TclError:
            return False

    def container(self, key):
        if self._valid(key):
            return self._containers[key]
        container = ttk.Frame(self.main_frame)
        self._containers[key] = container
        return container

    def show(self, key):
        """Hide the current page only, then pack the requested one."""
        if self._visible_key == key:
            return self.container(key)
        self.hide_all()
        container = self.container(key)
        container.pack(fill=tk.BOTH, expand=True)
        self._visible_key = key
        return container
