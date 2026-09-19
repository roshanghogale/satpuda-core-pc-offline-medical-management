"""Full-screen loading overlay shown above the Startup Alerts dialog."""
from __future__ import annotations

import tkinter as tk
import time


class PostAlertLoadingOverlay:
    """Full-screen Toplevel that covers the alerts dialog until setup finishes."""

    _STATUS_MESSAGES = (
        (0, "Finishing setup…"),
        (35, "Preparing pages…"),
        (65, "Loading shortcuts…"),
        (85, "Almost ready…"),
    )

    def __init__(self, master: tk.Misc, *, duration_ms: int = 6000, on_done=None, alert_window=None):
        self._master = master
        self._alert_window = alert_window
        self._duration_ms = max(1000, int(duration_ms))
        self._on_done = on_done
        self._start_time = time.time()
        self._closed = False
        self._after_id = None

        self._win = tk.Toplevel(master)
        self._win.withdraw()
        try:
            self._win.overrideredirect(True)
        except Exception:
            pass
        try:
            self._win.configure(bg="#0d1117", cursor="watch")
        except Exception:
            pass

        self._overlay = tk.Frame(self._win, bg="#0d1117", cursor="watch")
        self._overlay.pack(fill=tk.BOTH, expand=True)

        card = tk.Frame(self._overlay, bg="#1b2838", padx=36, pady=30)
        card.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(
            card,
            text="Satpuda Core Private Limited",
            font=("Segoe UI", 14, "bold"),
            fg="#d4e0ec",
            bg="#1b2838",
        ).pack(pady=(0, 4))
        tk.Label(
            card,
            text="Medical Management Software",
            font=("Segoe UI", 9),
            fg="#9eb4c8",
            bg="#1b2838",
        ).pack(pady=(0, 10))

        self._status = tk.Label(
            card,
            text="Finishing setup…",
            font=("Segoe UI", 10),
            fg="#9eb4c8",
            bg="#1b2838",
            wraplength=340,
            justify=tk.CENTER,
        )
        self._status.pack(pady=(0, 14))

        self._progress = self._make_progressbar(card)
        self._progress.pack(pady=(0, 10))

        self._time_label = tk.Label(
            card,
            text="",
            font=("Segoe UI", 9),
            fg="#7a92a8",
            bg="#1b2838",
        )
        self._time_label.pack()

        for widget in (self._overlay, card):
            for seq in ("<Button>", "<ButtonPress>", "<Key>", "<MouseWheel>"):
                widget.bind(seq, lambda e: "break", add="+")

        self._position_fullscreen()
        try:
            self._win.deiconify()
        except Exception:
            pass
        self._raise_overlay()
        try:
            self._win.grab_set()
        except Exception:
            pass

        self._tick()

    def _position_fullscreen(self):
        try:
            self._master.update_idletasks()
            w = max(int(self._master.winfo_width()), 400)
            h = max(int(self._master.winfo_height()), 300)
            x = int(self._master.winfo_rootx())
            y = int(self._master.winfo_rooty())
            self._win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            try:
                sw = int(self._master.winfo_screenwidth())
                sh = int(self._master.winfo_screenheight())
                self._win.geometry(f"{sw}x{sh}+0+0")
            except Exception:
                pass

    def _raise_overlay(self):
        try:
            self._win.lift()
            self._win.attributes("-topmost", True)
            self._win.update_idletasks()
            self._win.attributes("-topmost", False)
            self._win.lift()
            self._win.focus_force()
        except Exception:
            pass

    def _restore_alert_focus(self):
        aw = self._alert_window
        if aw is None:
            return
        try:
            if not aw.winfo_exists():
                return
        except Exception:
            return
        try:
            aw.lift()
            aw.focus_force()
            aw.grab_set()
        except Exception:
            pass

    @staticmethod
    def _make_progressbar(parent):
        try:
            import ttkbootstrap as ttk
            return ttk.Progressbar(
                parent,
                length=340,
                mode="determinate",
                maximum=100,
                bootstyle="info-striped",
            )
        except Exception:
            pass
        try:
            from tkinter import ttk
            return ttk.Progressbar(
                parent,
                length=340,
                mode="determinate",
                maximum=100,
            )
        except Exception:
            bar = tk.Canvas(parent, width=340, height=18, bg="#2a3a4a", highlightthickness=0)
            bar._fill = bar.create_rectangle(0, 0, 0, 18, fill="#3b82f6", width=0)
            bar._set_pct = lambda pct: bar.coords(
                bar._fill, 0, 0, max(1, int(340 * pct / 100)), 18,
            )
            return bar

    def _set_progress(self, pct: float):
        try:
            if hasattr(self._progress, "configure"):
                self._progress["value"] = pct
            elif hasattr(self._progress, "_set_pct"):
                self._progress._set_pct(pct)
        except Exception:
            pass

    def _status_for_progress(self, pct: float) -> str:
        msg = self._STATUS_MESSAGES[0][1]
        for threshold, text in self._STATUS_MESSAGES:
            if pct >= threshold:
                msg = text
        return msg

    def _tick(self):
        if self._closed:
            return
        elapsed_ms = (time.time() - self._start_time) * 1000
        pct = min(100.0, (elapsed_ms / self._duration_ms) * 100.0)
        remaining_s = max(0.0, (self._duration_ms - elapsed_ms) / 1000.0)

        try:
            self._set_progress(pct)
            self._status.configure(text=self._status_for_progress(pct))
            self._time_label.configure(text=f"{remaining_s:.1f}s remaining")
            self._raise_overlay()
            self._win.update_idletasks()
        except Exception:
            pass

        if elapsed_ms >= self._duration_ms:
            self.destroy()
            return

        try:
            self._after_id = self._win.after(50, self._tick)
        except Exception:
            self.destroy()

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        cb = self._on_done
        try:
            if self._after_id is not None:
                self._win.after_cancel(self._after_id)
        except Exception:
            pass
        try:
            self._win.grab_release()
        except Exception:
            pass
        try:
            self._win.destroy()
        except Exception:
            pass
        self._restore_alert_focus()
        if cb:
            try:
                cb()
            except Exception:
                pass


def trigger_post_alert_loading(root: tk.Misc, alert_window=None) -> bool:
    """Show full-screen startup progress (with or without alerts dialog)."""
    app = getattr(root, "_main_app", None)
    if app is None:
        return False
    starter = getattr(app, "_start_post_alert_loading", None)
    if not callable(starter):
        return False
    starter(alert_window)
    return True


def stop_post_alert_loading(root: tk.Misc) -> None:
    """Hide overlay when alerts dialog closes early."""
    app = getattr(root, "_main_app", None)
    if app is None:
        return
    finisher = getattr(app, "_stop_post_alert_loading", None)
    if callable(finisher):
        finisher()
