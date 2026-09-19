"""Compact voice indicator with live heard text above the status pill."""

import tkinter as tk

from core.voice.assistant import (
    STATE_AWAITING_WAKE,
    STATE_COMMAND_LISTENING,
    STATE_LOADING,
    STATE_OFF,
    STATE_PROCESSING,
    STATE_READY,
    STATE_STANDBY,
)
from core.voice.assistant_config import get_assistant_display_name, get_voice_listening_example
from core.voice.voice_sounds import play_error_sound

_PILL_W = 300
_PILL_H = 52
_BAR_W = 120
_HEARD_WRAP = 300


class SatpudaFloatOverlay:
    """Live heard caption + minimal draggable status pill."""

    def __init__(self, root):
        self.root = root
        self._visible = False
        self._error_latched = False
        self._working_latched = False
        self._working_job = None
        self._error_job = None
        self._pulse_job = None
        self._pulse_on = False
        self._level = 0.0
        self._state = STATE_OFF
        self._drag = (0, 0)
        self._command_var = tk.StringVar(value="")
        self._heard_var = tk.StringVar(value="")
        self._assistant_label = get_assistant_display_name()

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.96)
        except Exception:
            pass

        outer = tk.Frame(self.win, bg="#0f172a", padx=1, pady=1)
        outer.pack()
        self._outer = outer

        self._heard_shell = tk.Frame(outer, bg="#ffffff", padx=10, pady=8)
        self._heard_shell.pack(fill=tk.X)
        self._heard_shell.bind("<ButtonPress-1>", self._start_drag)
        self._heard_shell.bind("<B1-Motion>", self._on_drag)

        tk.Label(
            self._heard_shell,
            text="Heard",
            font=("Segoe UI", 8, "bold"),
            fg="#64748b",
            bg="#ffffff",
            anchor="w",
        ).pack(fill=tk.X)
        self._heard_label = tk.Label(
            self._heard_shell,
            textvariable=self._heard_var,
            font=("Segoe UI", 11, "bold"),
            fg="#0f172a",
            bg="#ffffff",
            wraplength=_HEARD_WRAP,
            justify=tk.LEFT,
            anchor="w",
        )
        self._heard_label.pack(fill=tk.X)
        self._heard_label.bind("<ButtonPress-1>", self._start_drag)
        self._heard_label.bind("<B1-Motion>", self._on_drag)

        self._shell = tk.Frame(outer, bg="#475569", padx=1, pady=1)
        self._shell.pack(fill=tk.X)
        self._inner = tk.Frame(self._shell, bg="#475569")
        self._inner.pack(fill=tk.BOTH, expand=True)
        self._inner.bind("<ButtonPress-1>", self._start_drag)
        self._inner.bind("<B1-Motion>", self._on_drag)

        row = tk.Frame(self._inner, bg="#475569", padx=12, pady=8)
        row.pack(fill=tk.BOTH, expand=True)
        row.bind("<ButtonPress-1>", self._start_drag)
        row.bind("<B1-Motion>", self._on_drag)
        self._row = row

        self._dot = tk.Canvas(row, width=14, height=14, bg="#475569", highlightthickness=0)
        self._dot.pack(side=tk.LEFT, padx=(0, 10))

        text_col = tk.Frame(row, bg="#475569")
        text_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._text_col = text_col

        self._status_var = tk.StringVar(value=self._wake_prompt())
        self._status = tk.Label(
            text_col,
            textvariable=self._status_var,
            font=("Segoe UI", 10, "bold"),
            fg="#f8fafc",
            bg="#475569",
            anchor="w",
        )
        self._status.pack(fill=tk.X)

        self._command = tk.Label(
            text_col,
            textvariable=self._command_var,
            font=("Segoe UI", 9),
            fg="#cbd5e1",
            bg="#475569",
            anchor="w",
        )
        self._command.pack(fill=tk.X)

        self._bar_bg = tk.Canvas(row, width=_BAR_W, height=6, bg="#475569", highlightthickness=0)
        self._bar_bg.pack(side=tk.RIGHT, padx=(8, 0))

        self._apply_palette(STATE_AWAITING_WAKE)

    def _wake_prompt(self) -> str:
        return f'Say "{get_voice_listening_example()}"'

    def _standby_prompt(self) -> str:
        return f'Say "Hey {self._assistant_label}"'

    def _command_prompt(self) -> str:
        return self._wake_prompt()

    def refresh_assistant_name(self):
        self._assistant_label = get_assistant_display_name()
        if self._error_latched:
            return
        if self._state == STATE_STANDBY:
            self._status_var.set(self._standby_prompt())
        elif self._state in (STATE_AWAITING_WAKE, STATE_READY, STATE_OFF):
            self._status_var.set(self._wake_prompt())

    def _start_drag(self, event):
        self._drag = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _on_drag(self, event):
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self.win.geometry(f"+{x}+{y}")

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _cancel_jobs(self):
        for job in (self._pulse_job, self._error_job, self._working_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._pulse_job = None
        self._error_job = None
        self._working_job = None

    def _apply_working_palette(self, bg: str):
        self._shell.configure(bg=bg)
        self._inner.configure(bg=bg)
        for widget in (self._inner, self._row, self._text_col, self._status, self._command):
            try:
                widget.configure(bg=bg)
            except Exception:
                pass
        self._status.configure(fg="#ffffff", bg=bg)
        self._command.configure(bg=bg, fg="#fef3c7")
        self._dot.configure(bg=bg)
        self._bar_bg.configure(bg=bg)
        self._draw_dot(bg, "#ffffff")
        self._draw_bar(bg)

    def _stop_working_pulse(self):
        if self._working_job is not None:
            try:
                self.root.after_cancel(self._working_job)
            except Exception:
                pass
            self._working_job = None

    def _pulse_working(self):
        if not self._visible or not self._working_latched:
            return
        bg = "#ea580c" if self._pulse_on else "#d97706"
        self._pulse_on = not self._pulse_on
        self._apply_working_palette(bg)
        self._working_job = self.root.after(500, self._pulse_working)

    def _palette_for(self, state: str):
        labels = {
            STATE_OFF: ("#64748b", "#f8fafc", "Voice off"),
            STATE_LOADING: ("#2563eb", "#ffffff", "Loading…"),
            STATE_READY: ("#0369a1", "#ffffff", self._wake_prompt()),
            STATE_STANDBY: ("#b45309", "#ffffff", self._standby_prompt()),
            STATE_AWAITING_WAKE: ("#475569", "#f8fafc", self._wake_prompt()),
            STATE_COMMAND_LISTENING: ("#059669", "#ffffff", self._command_prompt()),
            STATE_PROCESSING: ("#d97706", "#ffffff", "Processing…"),
        }
        return labels.get(state, labels[STATE_AWAITING_WAKE])

    def _apply_palette(self, state: str):
        bg, fg, label = self._palette_for(state)
        if self._error_latched:
            bg, fg = "#b91c1c", "#ffffff"
        self._shell.configure(bg=bg)
        self._inner.configure(bg=bg)
        for widget in (self._inner, self._row, self._text_col, self._status, self._command):
            try:
                widget.configure(bg=bg)
            except Exception:
                pass
        self._status.configure(fg=fg, bg=bg)
        self._command.configure(bg=bg)
        self._dot.configure(bg=bg)
        self._bar_bg.configure(bg=bg)
        if not self._error_latched:
            self._status_var.set(label)
        self._draw_dot(bg, fg)
        self._draw_bar(bg)

    def _draw_dot(self, bg: str, fg: str):
        c = self._dot
        c.delete("all")
        c.configure(bg=bg)
        fill = "#86efac" if self._state == STATE_COMMAND_LISTENING and self._pulse_on else fg
        c.create_oval(2, 2, 12, 12, fill=fill, outline=fill)

    def _draw_bar(self, bg: str):
        c = self._bar_bg
        c.delete("all")
        c.configure(bg=bg)
        c.create_rectangle(0, 1, _BAR_W, 5, fill="#334155", outline="")
        level = max(0.0, min(1.0, self._level))
        w = max(0, int(_BAR_W * level))
        if w > 0:
            color = "#86efac" if self._state == STATE_COMMAND_LISTENING else "#38bdf8"
            c.create_rectangle(0, 1, w, 5, fill=color, outline="")

    def _position_default(self):
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        w = max(self.win.winfo_reqwidth(), _PILL_W + 8)
        h = max(self.win.winfo_reqheight(), _PILL_H + 70)
        self.win.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 64}")

    def show(self):
        if not self._alive():
            return
        self._visible = True
        self._error_latched = False
        self._command_var.set("")
        self._heard_var.set("")
        self.refresh_assistant_name()
        self.win.deiconify()
        self._position_default()
        self.win.lift()
        self._apply_palette(self._state if self._state != STATE_OFF else STATE_AWAITING_WAKE)

    def hide(self):
        if not self._alive():
            return
        self._visible = False
        self._error_latched = False
        self._level = 0.0
        self._state = STATE_OFF
        self._command_var.set("")
        self._heard_var.set("")
        self._cancel_jobs()
        self.win.withdraw()

    def _start_pulse(self):
        self._cancel_jobs()
        self._pulse_indicator()

    def _stop_pulse(self):
        if self._pulse_job is not None:
            try:
                self.root.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None

    def _pulse_indicator(self):
        if not self._visible or self._state != STATE_COMMAND_LISTENING:
            return
        self._pulse_on = not self._pulse_on
        self._apply_palette(STATE_COMMAND_LISTENING)
        self._pulse_job = self.root.after(500, self._pulse_indicator)

    def on_live_heard(self, text: str):
        if not self._visible or not self._alive():
            return
        if text and text.strip():
            display = text.strip()
            if len(display) > 120:
                display = display[:117] + "…"
            self._heard_var.set(display)
        elif self._state in (STATE_AWAITING_WAKE, STATE_COMMAND_LISTENING, STATE_PROCESSING):
            self._heard_var.set("Listening…")
        self._position_default()

    def on_state(self, state: str, _detail: str = ""):
        if not self._visible or not self._alive():
            return
        self._state = state
        if self._working_latched:
            if state == STATE_PROCESSING:
                if not self._heard_var.get() or self._heard_var.get() == "Listening…":
                    self._heard_var.set("…")
            return
        self._stop_pulse()
        if state == STATE_COMMAND_LISTENING:
            self._error_latched = False
            self._start_pulse()
        elif state == STATE_AWAITING_WAKE:
            self._level = 0.0
            if not self._error_latched and not self._heard_var.get():
                self._heard_var.set("")
        elif state == STATE_PROCESSING:
            if not self._heard_var.get() or self._heard_var.get() == "Listening…":
                self._heard_var.set("…")
        self._apply_palette(state)

    def on_level(self, level: float):
        if not self._visible or not self._alive():
            return
        self._level = float(level)
        bg = self._palette_for(self._state)[0]
        if self._error_latched:
            bg = "#b91c1c"
        self._draw_bar(bg)

    def on_heard(self, text: str):
        self.on_live_heard(text)
        if not self._visible or not self._alive():
            return
        if text and text.strip():
            display = text.strip()
            if len(display) > 48:
                display = display[:45] + "…"
            self._command_var.set(display)

    def on_working(self, recognized: str, message: str):
        if not self._visible or not self._alive():
            return
        self._working_latched = True
        self._error_latched = False
        self._stop_pulse()
        self._stop_working_pulse()
        if recognized:
            self.on_heard(recognized)
        msg = (message or "Working…").strip()
        self._status_var.set(f"⏳ {msg[:42]}")
        self._command_var.set(msg[:48])
        self._apply_working_palette("#d97706")
        self._pulse_on = False
        self._pulse_working()

    def on_success(self, recognized: str, action: str):
        if not self._visible or not self._alive():
            return
        self._working_latched = False
        self._stop_working_pulse()
        self._error_latched = False
        self._stop_pulse()
        self._status_var.set(f"✓ {action}")
        if recognized:
            self.on_heard(recognized)
        self._apply_palette(STATE_READY)
        self._shell.configure(bg="#059669")
        self._inner.configure(bg="#059669")
        self._status.configure(bg="#059669", fg="#ffffff")
        self._command.configure(bg="#059669")

    def on_error(self, recognized: str, message: str):
        if not self._visible or not self._alive():
            return
        self._working_latched = False
        self._stop_working_pulse()
        self._error_latched = True
        self._level = 0.0
        self._stop_pulse()
        if recognized:
            self.on_heard(recognized)
        self._status_var.set("Unknown command")
        self._command_var.set((message or "")[:48])
        self._apply_palette(STATE_AWAITING_WAKE)
        self._shell.configure(bg="#b91c1c")
        self._inner.configure(bg="#b91c1c")
        self._status.configure(bg="#b91c1c", fg="#ffffff")
        self._command.configure(bg="#b91c1c", fg="#fecaca")
        play_error_sound()

        def _clear_error():
            if not self._visible:
                return
            self._error_latched = False
            self._command_var.set("")
            self._heard_var.set("")
            self.on_state(STATE_AWAITING_WAKE)

        if self._error_job is not None:
            try:
                self.root.after_cancel(self._error_job)
            except Exception:
                pass
        self._error_job = self.root.after(2500, _clear_error)

    def on_status(self, message: str):
        pass
