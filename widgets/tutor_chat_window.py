"""App Tutor — floating Satpuda AI chat with Instagram-style bubbles."""
from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
from typing import List, Optional, Tuple

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.gemini_tutor_config import (
    is_tutor_enabled, load_tutor_window_height, tutor_availability_message,
)
from core.gemini_tutor_service import TutorResult, ask_tutor
from core.tutor_context import collect_app_context, format_context_for_prompt
from core.voice.mic_devices import load_saved_device, resolve_device
from core.voice.recognizer import VoiceRecognitionError, WhisperRecognizer

HistoryItem = Tuple[str, str]

_PANEL_W = 460
_PANEL_H = 560
_PANEL_MIN_H = 420
_FAB_SIZE = 54
_MARGIN_X = 22
_MARGIN_Y_FAB = 28
_MARGIN_Y_PANEL = 20
_BUBBLE_WRAP = 330
_BUBBLE_RADIUS = 16
_BUBBLE_PAD_X = 14
_BUBBLE_PAD_Y = 10


def resolve_chat_palette(app) -> dict:
    """Match Satpuda AI colors to the active Satpuda Core theme."""
    from core.app_setup import load_theme
    from core.custom_themes import CUSTOM_THEMES

    name = getattr(app, "current_theme", None) or load_theme()
    theme = CUSTOM_THEMES.get(name, CUSTOM_THEMES["steel-dark"])
    c = theme["colors"]
    primary = c["primary"]
    is_light = theme.get("type") == "light"
    chat_bg = "#ffffff" if is_light else c.get("bg", "#1b2838")
    return {
        "primary": primary,
        "primary_dark": c.get("active", primary),
        "header": primary,
        "header_sub": "#e8f0fa" if is_light else "#d4e0ec",
        "chat_bg": chat_bg,
        "bubble_bg": primary,
        "bubble_fg": "#ffffff",
        "system_bg": c.get("active", "#dceaf8") if is_light else c.get("inputbg", "#243447"),
        "system_fg": c.get("fg", "#1a2530"),
        "shell": c.get("border", "#d8d8de"),
        "panel": "#ffffff" if is_light else c.get("inputbg", "#243447"),
        "footer": c.get("light", "#f0f4f8") if is_light else c.get("bg", "#1b2838"),
        "input_bg": c.get("inputbg", "#ffffff"),
        "input_fg": c.get("inputfg", "#1a2530"),
        "input_border": c.get("border", "#b8cce0"),
    }

def _round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, r: int, **kwargs) -> None:
    r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90, style="pieslice", **kwargs)
    canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90, style="pieslice", **kwargs)
    canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90, style="pieslice", **kwargs)
    canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90, style="pieslice", **kwargs)
    fill = kwargs.get("fill", "")
    outline = kwargs.get("outline", fill)
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=outline)
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=outline)


class _BubbleFactory:
    def __init__(self) -> None:
        self._font = tkfont.Font(family="Segoe UI", size=10)

    @staticmethod
    def _parent_bg(parent: tk.Misc) -> str:
        try:
            return str(parent.cget("bg"))
        except tk.TclError:
            return "#ffffff"

    def _measure_wrapped(self, parent: tk.Misc, text: str, wrap_px: int) -> Tuple[int, int]:
        probe = tk.Canvas(parent, width=1, height=1, highlightthickness=0, bd=0)
        tid = probe.create_text(
            0, 0, text=text.strip(), anchor="nw", font=self._font, width=wrap_px,
        )
        bbox = probe.bbox(tid)
        probe.destroy()
        if not bbox:
            return wrap_px, self._font.metrics("linespace")
        return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])

    def create(
        self,
        parent: tk.Misc,
        text: str,
        *,
        fill: str,
        fg: str,
        align: str = "left",
        max_w: int = _BUBBLE_WRAP,
    ) -> tk.Frame:
        bg = self._parent_bg(parent)
        wrap_px = max_w
        tw, th = self._measure_wrapped(parent, text, wrap_px)
        w = tw + _BUBBLE_PAD_X * 2
        h = th + _BUBBLE_PAD_Y * 2
        side = tk.RIGHT if align == "right" else tk.LEFT

        outer = tk.Frame(parent, bg=bg)
        holder = tk.Frame(outer, bg=bg)
        holder.pack(side=side, anchor="e" if align == "right" else "w")

        canvas = tk.Canvas(holder, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        canvas.pack()
        _round_rect(canvas, 0, 0, w, h, _BUBBLE_RADIUS, fill=fill, outline=fill)
        canvas.create_text(
            _BUBBLE_PAD_X,
            _BUBBLE_PAD_Y,
            text=text.strip(),
            anchor="nw",
            fill=fg,
            font=self._font,
            width=wrap_px,
        )
        return outer


class _ChatMessageList:
    """Scrollable column of rounded message bubbles."""

    def __init__(self, parent: tk.Misc, palette: dict):
        self._palette = palette
        self._bg = palette["chat_bg"]
        self._bubbles = _BubbleFactory()
        self._auto_scroll = True

        outer = tk.Frame(parent, bg=self._bg)
        outer.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(outer, bg=self._bg, highlightthickness=0, borderwidth=0)
        self.scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self._scrollbar_cmd)
        self.canvas.configure(yscrollcommand=self._yscroll_callback)
        self.scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner = tk.Frame(self.canvas, bg=self._bg)
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._outer = outer
        for w in (outer, self.canvas, self.inner):
            w.bind("<Enter>", self._on_chat_enter, add="+")
            w.bind("<Leave>", self._on_chat_leave, add="+")
        self._wheel_bound = False

    def _on_chat_enter(self, _event=None) -> None:
        if self._wheel_bound:
            return
        self._wheel_bound = True
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", lambda e: self._wheel_scroll(-3))
        self.canvas.bind_all("<Button-5>", lambda e: self._wheel_scroll(3))

    def _on_chat_leave(self, _event=None) -> None:
        try:
            w = self.canvas.winfo_containing(self.canvas.winfo_pointerx(), self.canvas.winfo_pointery())
            cur = w
            while cur is not None:
                if cur in (self._outer, self.canvas, self.inner):
                    return
                cur = getattr(cur, "master", None)
        except tk.TclError:
            pass
        self._wheel_bound = False
        try:
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")
        except tk.TclError:
            pass

    def _scrollbar_cmd(self, *args) -> None:
        self.canvas.yview(*args)
        if not args:
            return
        if args[0] == "moveto":
            try:
                if float(args[1]) < 0.98:
                    self._auto_scroll = False
            except (ValueError, IndexError, TypeError):
                pass
        elif args[0] == "scroll":
            self._auto_scroll = False

    def _yscroll_callback(self, first, last) -> None:
        self.scroll.set(first, last)
        try:
            if float(last) < 0.98:
                self._auto_scroll = False
        except (TypeError, ValueError):
            pass

    def _wheel_scroll(self, units: int) -> None:
        try:
            self.canvas.yview_scroll(units, "units")
            self._auto_scroll = False
        except tk.TclError:
            pass

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self._auto_scroll:
            self.canvas.yview_moveto(1.0)

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._win_id, width=event.width)

    def _on_mousewheel(self, event) -> None:
        try:
            delta = int(-1 * (event.delta / 120)) if event.delta else 0
            if delta:
                self._wheel_scroll(delta)
        except Exception:
            pass

    def scroll_to_end(self) -> None:
        self._auto_scroll = True

        def _do() -> None:
            try:
                self.inner.update_idletasks()
                self.canvas.update_idletasks()
                bbox = self.canvas.bbox("all")
                if bbox:
                    self.canvas.configure(scrollregion=bbox)
                self.canvas.yview_moveto(1.0)
            except tk.TclError:
                pass

        _do()
        self.canvas.after_idle(_do)
        self.canvas.after(40, _do)
        self.canvas.after(150, _do)

    def clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self._auto_scroll = True

    def add_system(self, text: str) -> None:
        row = tk.Frame(self.inner, bg=self._bg)
        row.pack(fill=tk.X, pady=(10, 4), padx=16)
        lbl = tk.Label(
            row, text=text.strip(),
            bg=self._palette["system_bg"], fg=self._palette["system_fg"],
            font=("Segoe UI", 9), wraplength=_BUBBLE_WRAP + 48,
            padx=14, pady=8, justify=tk.CENTER,
        )
        lbl.pack()

    def add_user(self, text: str) -> None:
        row = tk.Frame(self.inner, bg=self._bg)
        row.pack(fill=tk.X, pady=5, padx=12)
        bubble = self._bubbles.create(
            row, text,
            fill=self._palette["bubble_bg"], fg=self._palette["bubble_fg"],
            align="right",
        )
        bubble.pack(side=tk.RIGHT, anchor="e")
        self.scroll_to_end()

    def add_bot(self, text: str) -> None:
        body = text.strip()
        if not body:
            return
        row = tk.Frame(self.inner, bg=self._bg)
        row.pack(fill=tk.X, pady=5, padx=12)
        bubble = self._bubbles.create(
            row, body,
            fill=self._palette["bubble_bg"], fg=self._palette["bubble_fg"],
            align="left",
        )
        bubble.pack(side=tk.LEFT, anchor="w")
        self.scroll_to_end()

    def add_typing(self) -> tk.Frame:
        row = tk.Frame(self.inner, bg=self._bg)
        row.pack(fill=tk.X, pady=5, padx=12)
        chip = tk.Frame(
            row, bg=self._palette["system_bg"],
            highlightbackground=self._palette["input_border"],
            highlightthickness=1, padx=14, pady=10,
        )
        chip.pack(side=tk.LEFT, anchor="w")
        inner = tk.Frame(chip, bg=self._palette["system_bg"])
        inner.pack()
        tk.Label(
            inner, text="AI is thinking",
            bg=self._palette["system_bg"], fg=self._palette["system_fg"],
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT)
        dots = tk.Label(
            inner, text="",
            bg=self._palette["system_bg"], fg=self._palette["primary"],
            font=("Segoe UI", 10, "bold"), width=4, anchor="w",
        )
        dots.pack(side=tk.LEFT)
        row._typing_dots = dots  # type: ignore[attr-defined]
        self.scroll_to_end()
        return row


class TutorChatWindow:
    """Floating Satpuda AI — FAB + chat panel."""

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._palette = resolve_chat_palette(app)
        self._history: List[HistoryItem] = []
        self._busy = False
        self._recognizer: Optional[WhisperRecognizer] = None
        self._listen_thread: Optional[threading.Thread] = None
        self._panel_visible = False
        self._fab_allowed = False
        self._typing_job: Optional[str] = None
        self._typing_row: Optional[tk.Frame] = None
        self._typing_phase = 0
        pal = self._palette

        self._fab = tk.Frame(self.root, bg=pal["shell"], padx=2, pady=2, cursor="hand2")
        self._fab.place_forget()
        self._fab_canvas = tk.Canvas(
            self._fab, width=_FAB_SIZE, height=_FAB_SIZE,
            bg=pal["primary"], highlightthickness=0, cursor="hand2", bd=0,
        )
        self._fab_canvas.pack()
        pad = 2
        self._fab_canvas.create_oval(
            pad, pad, _FAB_SIZE - pad, _FAB_SIZE - pad,
            fill=pal["primary"], outline=pal["primary_dark"], width=1,
        )
        self._fab_canvas.create_text(
            _FAB_SIZE // 2, _FAB_SIZE // 2, text="\u2753",
            fill="#ffffff", font=("Segoe UI", 17, "bold"),
        )
        for w in (self._fab, self._fab_canvas):
            w.bind("<Button-1>", lambda _e: self.show())

        self.win = tk.Frame(self.root, width=_PANEL_W, height=self._resolved_panel_height())
        self.win.pack_propagate(False)
        self.win.place_forget()

        shell = tk.Frame(self.win, bg=pal["shell"], padx=2, pady=2)
        shell.pack(fill=tk.BOTH, expand=True)
        outer = tk.Frame(shell, bg=pal["panel"])
        outer.pack(fill=tk.BOTH, expand=True)

        self._header = tk.Frame(outer, bg=pal["header"], padx=14, pady=11)
        self._header.pack(fill=tk.X)
        title_col = tk.Frame(self._header, bg=pal["header"])
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._title_lbl = tk.Label(
            title_col, text="Satpuda AI", font=("Segoe UI", 13, "bold"),
            fg="#ffffff", bg=pal["header"], anchor="w",
        )
        self._title_lbl.pack(anchor="w")
        self._ctx_var = tk.StringVar(value="")
        self._ctx_lbl = tk.Label(
            title_col, textvariable=self._ctx_var, font=("Segoe UI", 9),
            fg=pal["header_sub"], bg=pal["header"], anchor="w",
        )
        self._ctx_lbl.pack(anchor="w", pady=(1, 0))

        hdr_btns = tk.Frame(self._header, bg=pal["header"])
        hdr_btns.pack(side=tk.RIGHT)
        self._hdr_link_labels: List[tk.Label] = []
        for text, cmd in (
            ("Clear", self._clear_chat),
            ("Satpuda", self._open_satpuda),
            ("\u2715", self.hide),
        ):
            lbl = tk.Label(
                hdr_btns, text=text, font=("Segoe UI", 10),
                fg=pal["header_sub"], bg=pal["header"], cursor="hand2", padx=8,
            )
            lbl.pack(side=tk.LEFT)
            lbl.bind("<Button-1>", lambda _e, c=cmd: c())
            self._hdr_link_labels.append(lbl)

        self._messages = _ChatMessageList(outer, pal)

        footer = tk.Frame(outer, bg=pal["footer"], padx=10, pady=10)
        footer.pack(fill=tk.X)
        input_shell = tk.Frame(footer, bg=pal["input_border"], padx=1, pady=1)
        input_shell.pack(fill=tk.X)
        input_row = tk.Frame(input_shell, bg=pal["input_bg"], padx=6, pady=5)
        input_row.pack(fill=tk.X)

        self._question_var = tk.StringVar()
        self._entry = tk.Entry(
            input_row,
            textvariable=self._question_var,
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            bg=pal["input_bg"],
            fg=pal["input_fg"],
            insertbackground=pal["input_fg"],
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(6, 8))
        self._entry.bind("<Return>", lambda _e: self._on_send())
        self._entry.bind("<FocusIn>", self._on_entry_focus_in, add="+")
        self._entry.bind("<FocusOut>", self._on_entry_focus_out, add="+")

        self._mic_btn = tk.Button(
            input_row, text="\U0001f3a4", font=("Segoe UI", 11),
            bg=pal["primary"], fg="#ffffff", relief=tk.FLAT, width=2,
            cursor="hand2", activebackground=pal["primary_dark"],
            activeforeground="#ffffff", command=self._on_mic,
        )
        self._mic_btn.pack(side=tk.RIGHT, padx=(0, 2))
        self._send_btn = tk.Button(
            input_row, text="\u27a4", font=("Segoe UI", 11),
            bg=pal["primary"], fg="#ffffff", relief=tk.FLAT, width=3,
            cursor="hand2", activebackground=pal["primary_dark"],
            activeforeground="#ffffff", command=self._on_send,
        )
        self._send_btn.pack(side=tk.RIGHT, padx=(0, 4))

        avail = tutor_availability_message()
        if avail:
            self._messages.add_system(avail)
        elif is_tutor_enabled():
            from core.offline_tutor import offline_entry_count
            n = offline_entry_count()
            if n:
                self._messages.add_system(
                    f"Satpuda AI ready. Online answers use Gemini when internet is on. "
                    f"Offline help ({n} topics) works without internet."
                )
        self.refresh_context()
        self._sync_visibility()
        self.root.bind("<Configure>", self._on_root_configure, add="+")

    def _resolved_panel_height(self) -> int:
        height = load_tutor_window_height()
        try:
            from core.window_icon import get_screen_work_area
            _max_w, max_h, _sw, _sh = get_screen_work_area(self.root)
            visible_limit = max(_PANEL_MIN_H, int(max_h) - (_MARGIN_Y_PANEL + 24))
            return max(_PANEL_MIN_H, min(int(height), visible_limit))
        except Exception:
            return max(_PANEL_MIN_H, int(height))

    def _apply_panel_size(self) -> None:
        try:
            self.win.configure(width=_PANEL_W, height=self._resolved_panel_height())
            self.win.pack_propagate(False)
        except tk.TclError:
            pass
        if self._panel_visible:
            self._place_panel()

    def set_fab_allowed(self, allowed: bool = True) -> None:
        self._fab_allowed = bool(allowed)
        self._sync_visibility()

    def _clear_chat(self) -> None:
        if self._busy:
            return
        self._history.clear()
        self._messages.clear()
        self._messages.add_system("Chat cleared. Ask me about this screen or any workflow.")
        self._messages.scroll_to_end()

    def _on_entry_focus_in(self, _event=None) -> None:
        self.app._tutor_chat_input_active = True
        try:
            self._entry.icursor(tk.END)
        except Exception:
            pass

    def _on_entry_focus_out(self, _event=None) -> None:
        self.root.after(80, self._sync_input_active_flag)

    def _sync_input_active_flag(self) -> None:
        if not self._panel_visible:
            self.app._tutor_chat_input_active = False
            return
        try:
            w = self.root.focus_get()
        except Exception:
            self.app._tutor_chat_input_active = False
            return
        self.app._tutor_chat_input_active = self._widget_in_chat(w)

    def _widget_in_chat(self, widget) -> bool:
        if widget is None:
            return False
        cur = widget
        while cur is not None:
            if cur == self.win:
                return True
            try:
                cur = cur.master
            except Exception:
                break
        return False

    def _open_satpuda(self) -> None:
        if hasattr(self.app, "_open_voice_dialog"):
            self.app._open_voice_dialog()

    def _on_root_configure(self, _event=None) -> None:
        if self._panel_visible:
            self._place_panel()
        elif self._fab_allowed and is_tutor_enabled():
            self._place_fab()

    def _fab_visible(self) -> bool:
        try:
            return bool(self._fab.winfo_ismapped())
        except tk.TclError:
            return False

    def _place_fab(self) -> None:
        try:
            self._fab.place(relx=1.0, rely=1.0, anchor="se", x=-_MARGIN_X, y=-_MARGIN_Y_FAB)
            self._fab.lift()
        except tk.TclError:
            pass

    def _place_panel(self) -> None:
        try:
            self.win.place(relx=1.0, rely=1.0, anchor="se", x=-_MARGIN_X, y=-_MARGIN_Y_PANEL)
            self.win.lift()
        except tk.TclError:
            pass

    def _sync_visibility(self) -> None:
        if not is_tutor_enabled():
            self._panel_visible = False
            self._hide_typing()
            self.app._tutor_chat_input_active = False
            try:
                self.win.place_forget()
                self._fab.place_forget()
            except tk.TclError:
                pass
            return
        if self._panel_visible:
            try:
                self._fab.place_forget()
                self._place_panel()
            except tk.TclError:
                pass
        else:
            try:
                self.win.place_forget()
                self.app._tutor_chat_input_active = False
                if self._fab_allowed:
                    self._place_fab()
                else:
                    self._fab.place_forget()
            except tk.TclError:
                pass

    def refresh_context(self) -> None:
        try:
            ctx = collect_app_context(self.app)
            self._ctx_var.set(format_context_for_prompt(ctx))
        except Exception:
            self._ctx_var.set("")

    def show(self) -> None:
        if not is_tutor_enabled():
            self._messages.add_system("Satpuda AI is off. Enable it in Settings \u2192 My Assist.")
            return
        self._panel_visible = True
        self.refresh_context()
        self._sync_visibility()
        self.app._tutor_chat_input_active = True
        try:
            self._entry.focus_force()
        except tk.TclError:
            pass

    def hide(self) -> None:
        self._panel_visible = False
        self.app._tutor_chat_input_active = False
        self._hide_typing()
        self._sync_visibility()

    def is_visible(self) -> bool:
        return bool(self._panel_visible)

    def apply_settings(self) -> None:
        self._apply_panel_size()
        if not is_tutor_enabled():
            self._panel_visible = False
        self._sync_visibility()

    def _show_typing(self) -> None:
        self._hide_typing()
        self._typing_phase = 0
        self._typing_row = self._messages.add_typing()
        self._animate_typing()

    def _animate_typing(self) -> None:
        if not self._busy or self._typing_row is None:
            return
        dot_frames = ("", ".", "..", "...")
        phase = self._typing_phase % len(dot_frames)
        try:
            dots = getattr(self._typing_row, "_typing_dots", None)
            if dots is not None and dots.winfo_exists():
                dots.configure(text=dot_frames[phase])
        except tk.TclError:
            pass
        self._typing_phase += 1
        self._typing_job = self.root.after(420, self._animate_typing)

    def _hide_typing(self) -> None:
        if self._typing_job is not None:
            try:
                self.root.after_cancel(self._typing_job)
            except Exception:
                pass
            self._typing_job = None
        if self._typing_row is not None:
            try:
                self._typing_row.destroy()
            except tk.TclError:
                pass
        self._typing_row = None

    def _set_busy(self, busy: bool, *, typing: bool = True) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        try:
            self._send_btn.configure(state=state)
            self._mic_btn.configure(state=state)
            self._entry.configure(state=state)
        except tk.TclError:
            pass
        if busy and typing:
            self._show_typing()
        else:
            self._hide_typing()

    def _on_send(self) -> None:
        if self._busy:
            return
        q = (self._question_var.get() or "").strip()
        if not q:
            return
        self._question_var.set("")
        self._ask(q)

    def _ask(self, question: str) -> None:
        self._messages.add_user(question)
        self._set_busy(True, typing=True)

        def worker():
            err: Optional[Exception] = None
            result: Optional[TutorResult] = None
            try:
                ctx = collect_app_context(self.app)
                result = ask_tutor(
                    question,
                    context=ctx,
                    history=self._history,
                )
            except Exception as exc:
                err = exc

            def done():
                self._hide_typing()
                self._set_busy(False, typing=False)
                if err is not None:
                    self._messages.add_bot(f"Sorry \u2014 {err}")
                elif result is not None:
                    answer = result.text
                    self._history.append(("user", question))
                    self._history.append(("assistant", answer))
                    if len(self._history) > 16:
                        self._history = self._history[-16:]
                    self._messages.add_bot(answer)
                self.refresh_context()
                self._messages.scroll_to_end()

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_mic(self) -> None:
        if self._busy:
            return
        if self._listen_thread and self._listen_thread.is_alive():
            return
        self._set_busy(True, typing=False)
        self._messages.add_system("Listening\u2026")
        self._listen_thread = threading.Thread(target=self._listen_worker, daemon=True)
        self._listen_thread.start()

    def _listen_worker(self) -> None:
        heard = ""
        err_msg = ""
        try:
            dev = resolve_device(load_saved_device())
            if self._recognizer is None:
                self._recognizer = WhisperRecognizer(input_device=dev)
            heard = (self._recognizer.listen_once(max_seconds=12.0, silence_seconds=0.9) or "").strip()
        except VoiceRecognitionError as exc:
            err_msg = str(exc)
        except Exception as exc:
            err_msg = str(exc)

        def done():
            self._set_busy(False)
            if err_msg:
                self._messages.add_system(f"Couldn't hear that \u2014 {err_msg}")
            elif not heard:
                self._messages.add_system("No speech detected. Try again.")
            else:
                self._question_var.set(heard)
                self._ask(heard)
            self._messages.scroll_to_end()

        self.root.after(0, done)


def open_tutor_chat(app) -> TutorChatWindow:
    existing = getattr(app, "_tutor_chat_window", None)
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.show()
                return existing
        except tk.TclError:
            pass
    win = TutorChatWindow(app)
    app._tutor_chat_window = win
    win.set_fab_allowed(True)
    return win
