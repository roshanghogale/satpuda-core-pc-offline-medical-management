"""Embed HTML preview on Windows using IE WebBrowser control (Win7–11)."""
from __future__ import annotations

import os
import sys
import tkinter as tk


def try_embed_html(parent: tk.Widget, html_path: str):
    """
    Try to show HTML inside a Tk frame. Returns the frame widget or None.
    Falls back gracefully when IE control is unavailable.
    """
    if sys.platform != 'win32':
        return None
    path = os.path.abspath(html_path)
    if not os.path.isfile(path):
        return None
    try:
        import pythoncom
        import win32gui
        import win32con
        from win32com.client import DispatchEx

        class BrowserHost(tk.Frame):
            def __init__(self, master, file_path):
                super().__init__(master, bg='white')
                self._file_path = file_path
                self._browser = None
                self.bind('<Configure>', self._on_resize)
                self.after(150, self._create)

            def _create(self):
                try:
                    pythoncom.CoInitialize()
                    self._browser = DispatchEx('Shell.Explorer.2')
                    self._browser.Navigate2(self._file_path)
                    self.after(300, self._reparent)
                except Exception:
                    pass

            def _reparent(self):
                try:
                    if not self._browser:
                        return
                    hwnd = self.winfo_id()
                    child = win32gui.FindWindowEx(hwnd, 0, 'Shell Embedding', None)
                    if not child:
                        return
                    win32gui.SetParent(child, hwnd)
                    win32gui.SetWindowPos(
                        child, None, 0, 0,
                        max(10, self.winfo_width()), max(10, self.winfo_height()),
                        win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
                    )
                except Exception:
                    pass

            def _on_resize(self, _event=None):
                try:
                    hwnd = self.winfo_id()
                    child = win32gui.FindWindowEx(hwnd, 0, 'Shell Embedding', None)
                    if child:
                        win32gui.SetWindowPos(
                            child, None, 0, 0,
                            max(10, self.winfo_width()), max(10, self.winfo_height()),
                            win32con.SWP_NOZORDER,
                        )
                except Exception:
                    pass

        return BrowserHost(parent, path)
    except Exception:
        return None
