"""
Dedicated scroll pane for Settings → Appearance.

Uses a tk.Frame inside a Canvas (not ttk.Frame) with the standard
scrollregion pattern. Never sets height on the canvas window item — that
is what caused content to be clipped to viewport height with no scrolling.
"""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk


class AppearanceScrollPane:
    """Right-hand scrollable area for Appearance section panels."""

    def __init__(self, parent):
        self._outer = ttk.Frame(parent)
        self._outer.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            self._outer, highlightthickness=0, borderwidth=0,
        )
        self.vsb = ttk.Scrollbar(
            self._outer, orient=tk.VERTICAL, command=self.canvas.yview,
        )
        self.canvas.configure(yscrollcommand=self.vsb.set)

        # tk.Frame — sizes to children; ttk.Frame inside canvas often stretches wrong on Windows.
        self.frame = tk.Frame(self.canvas)
        self._win_id = self.canvas.create_window((0, 0), window=self.frame, anchor='nw')

        self.frame.bind('<Configure>', self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_configure)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self._bind_wheel(self.canvas)
        self._bind_wheel(self.frame)
        self._bind_wheel(self._outer)

    def _on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _on_canvas_configure(self, event):
        # Match content width to viewport only — never set height on the window item.
        self.canvas.itemconfig(self._win_id, width=event.width)

    def _scroll(self, delta):
        try:
            self.canvas.yview_scroll(int(-1 * delta), 'units')
        except Exception:
            pass

    def _on_mousewheel(self, event):
        delta = getattr(event, 'delta', 0) or 0
        if delta:
            self._scroll(delta / 120)
        return 'break'

    def _on_linux_up(self, event):
        self._scroll(1)
        return 'break'

    def _on_linux_down(self, event):
        self._scroll(-1)
        return 'break'

    def _bind_wheel(self, widget):
        widget.bind('<MouseWheel>', self._on_mousewheel, add='+')
        widget.bind('<Button-4>', self._on_linux_up, add='+')
        widget.bind('<Button-5>', self._on_linux_down, add='+')

    def bind_wheel_recursive(self):
        """Call after building or showing a section so wheel works over all children."""
        seen = set()

        def walk(w):
            wid = str(w)
            if wid in seen:
                return
            seen.add(wid)
            self._bind_wheel(w)
            try:
                for child in w.winfo_children():
                    walk(child)
            except Exception:
                pass

        walk(self.frame)

    def refresh(self):
        self.frame.update_idletasks()
        bbox = self.canvas.bbox('all')
        if bbox:
            self.canvas.configure(scrollregion=bbox)
        self.canvas.update_idletasks()

    def scroll_to_top(self):
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass
