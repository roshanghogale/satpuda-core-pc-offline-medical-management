"""Centered Satpuda voice assistant dialog — opens from the nav bar mic button."""

import threading
import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_DEFAULT, FONT_SIZE_LABELS
from core.voice.assistant import (
    STATE_AWAITING_WAKE,
    STATE_COMMAND_LISTENING,
    STATE_LISTENING,
    STATE_LOADING,
    STATE_OFF,
    STATE_PROCESSING,
    STATE_READY,
    STATE_STANDBY,
)
from core.voice.assistant_config import (
    get_assistant_display_name,
    get_voice_listening_example,
    load_assistant_name,
)
from core.voice.mic_devices import load_saved_device
from core.voice.recognizer import VoiceRecognitionError, WhisperRecognizer
from core.voice.voice_log import voice_log


def open_mic_picker_dialog(parent, *, assistant=None, on_selected=None):
    """Show a scrollable list of microphones; saves choice to config."""
    from core.voice.mic_devices import invalidate_device_cache, list_input_devices

    if assistant is not None and assistant.enabled:
        from core.themed_messagebox import showinfo
        showinfo(
            "Satpuda",
            "Turn off the microphone before changing the input device.",
            parent=parent,
        )
        return

    invalidate_device_cache()
    devices = list_input_devices(timeout=12.0)
    if not devices:
        from core.themed_messagebox import showwarning
        showwarning(
            "Satpuda",
            "No microphone detected.\n\n"
            "1. Connect the Bluetooth / USB mic and wait until Windows shows it\n"
            "2. Windows Settings → System → Sound → Input — confirm it appears\n"
            "3. Privacy → Microphone — allow desktop apps\n"
            "4. Open this picker again (Refresh rescans WASAPI devices)",
            parent=parent,
        )
        return

    picker = tk.Toplevel(parent)
    picker.title("Select microphone")
    picker.resizable(True, True)
    picker.transient(parent)
    picker.grab_set()

    ttk.Label(
        picker,
        text="Same microphones as Windows Settings → Sound → Input. "
             "Pick the one your PC shows (headphones name / External Microphone / etc.):",
        font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        wraplength=500,
    ).pack(anchor="w", padx=12, pady=(12, 6))

    frame = ttk.Frame(picker)
    frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
    scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL)
    lb = tk.Listbox(
        frame,
        height=min(14, max(6, len(devices))),
        width=68,
        font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        yscrollcommand=scroll.set,
        selectmode=tk.SINGLE,
        activestyle="dotbox",
    )
    scroll.config(command=lb.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    from core.voice.mic_devices import load_saved_device, resolve_device
    device_list = {"items": list(devices)}

    def _fill_list(devs):
        lb.delete(0, tk.END)
        device_list["items"] = list(devs or [])
        current = resolve_device(load_saved_device())
        selected_idx = 0
        for idx, dev in enumerate(device_list["items"]):
            lb.insert(tk.END, dev.get("label") or dev.get("name") or f"Mic {idx}")
            if current and dev.get("index") == current.get("index") and dev.get("backend") == current.get("backend"):
                selected_idx = idx
            elif dev.get("is_default") and current is None:
                selected_idx = idx
        if device_list["items"]:
            lb.selection_set(selected_idx)
            lb.see(selected_idx)

    _fill_list(devices)

    btn_row = ttk.Frame(picker)
    btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))

    def _refresh():
        invalidate_device_cache()
        fresh = list_input_devices(timeout=12.0)
        if not fresh:
            from core.themed_messagebox import showwarning
            showwarning(
                "Satpuda",
                "Still no microphones found. Connect the device in Windows Sound settings, then Refresh.",
                parent=picker,
            )
            return
        _fill_list(fresh)

    def _choose():
        sel = lb.curselection()
        if not sel:
            return
        items = device_list["items"]
        if not items or sel[0] >= len(items):
            return
        dev = items[sel[0]]
        if assistant is not None:
            assistant.set_input_device(dev)
        if callable(on_selected):
            on_selected(dev)
        picker.destroy()

    try:
        ttk.Button(btn_row, text="Use this mic", bootstyle="success", command=_choose).pack(
            side=tk.RIGHT, padx=(6, 0),
        )
        ttk.Button(btn_row, text="Refresh", bootstyle="info", command=_refresh).pack(
            side=tk.RIGHT, padx=(6, 0),
        )
        ttk.Button(btn_row, text="Cancel", bootstyle="secondary", command=picker.destroy).pack(
            side=tk.RIGHT,
        )
    except Exception:
        ttk.Button(btn_row, text="Use this mic", command=_choose).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Refresh", command=_refresh).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Cancel", command=picker.destroy).pack(side=tk.RIGHT)

    lb.bind("<Double-Button-1>", lambda _e: _choose())
    picker.bind("<Return>", lambda _e: _choose())
    picker.bind("<Escape>", lambda _e: picker.destroy())

    try:
        from core.scroll_manager import finalize_dialog_geometry
        finalize_dialog_geometry(picker, width=560, height=380, resizable=True)
    except Exception:
        picker.geometry("560x380")
    picker.lift()
    picker.focus_force()


def open_satpuda_voice_dialog(app):
    """Open or focus the centered voice dialog."""
    existing = getattr(app, "_voice_dialog", None)
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.win.lift()
                existing.win.focus_force()
                return existing
        except Exception:
            pass
    dlg = SatpudaVoiceDialog(app)
    app._voice_dialog = dlg
    return dlg


class SatpudaVoiceDialog:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._pulse_on = False
        self._pulse_job = None
        self._device_map = {}
        self._device_list = []
        self._test_stop = threading.Event()
        self._test_thread = None
        self._test_recognizer = None
        self._mic_on = False
        self._closed = False

        self.win = tk.Toplevel(self.root)
        self.win.title("Satpuda Voice Assistant")
        self.win.resizable(False, False)
        self.win.grid_rowconfigure(0, weight=1)
        self.win.grid_rowconfigure(1, weight=0)
        self.win.grid_columnconfigure(0, weight=1)

        try:
            from core.scroll_manager import _apply_dialog_theme
            _apply_dialog_theme(self.win)
        except Exception:
            pass
        try:
            from core.window_icon import apply_window_icon
            apply_window_icon(self.win, master=self.root, is_root=False)
        except Exception:
            pass

        body = ttk.Frame(self.win)
        body.grid(row=0, column=0, sticky="nsew", padx=20, pady=(16, 8))

        header = ttk.Frame(body)
        header.pack(fill=tk.X, pady=(0, 10))
        try:
            ttk.Label(
                header, text="Voice", font=(FONT_FAMILY, 18, "bold"), bootstyle="primary",
            ).pack(side=tk.LEFT, padx=(0, 12))
        except Exception:
            ttk.Label(header, text="Voice", font=(FONT_FAMILY, 18, "bold")).pack(
                side=tk.LEFT, padx=(0, 12),
            )

        title_col = ttk.Frame(header)
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(
            title_col,
            text="Satpuda Voice Assistant",
            font=(FONT_FAMILY, FONT_SIZE_LABELS, "bold"),
        ).pack(anchor="w")
        self._step_var = tk.StringVar(value=self._step_instructions())
        ttk.Label(
            title_col,
            textvariable=self._step_var,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            title_col,
            text="Other speech is ignored. You can close this window while listening.",
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT - 1),
        ).pack(anchor="w", pady=(1, 0))

        # ── Speech model (loads when dialog opens) ────────────────────────
        self._model_frame = ttk.LabelFrame(body, text="Speech model")
        self._model_frame.pack(fill=tk.X, pady=(0, 10))
        model_inner = ttk.Frame(self._model_frame)
        model_inner.pack(fill=tk.X, padx=10, pady=8)
        self._model_status_var = tk.StringVar(
            value="Loading speech model — first open may take up to a minute…",
        )
        ttk.Label(
            model_inner,
            textvariable=self._model_status_var,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
            wraplength=480,
            justify=tk.LEFT,
        ).pack(fill=tk.X)
        try:
            self._model_progress = ttk.Progressbar(
                model_inner,
                mode="indeterminate",
                length=480,
                bootstyle="info-striped",
            )
        except Exception:
            self._model_progress = ttk.Progressbar(
                model_inner, mode="indeterminate", length=480,
            )
        self._model_progress.pack(fill=tk.X, pady=(8, 0))
        self._model_loaded = False

        # ── Mic ON / OFF banner ───────────────────────────────────────────
        self._banner_frame = tk.Frame(body, bg="#9ca3af", height=36)
        self._banner_frame.pack(fill=tk.X, pady=(0, 10))
        self._banner_frame.pack_propagate(False)
        self._banner_var = tk.StringVar(value="MIC IS OFF")
        self._banner_label = tk.Label(
            self._banner_frame,
            textvariable=self._banner_var,
            font=("Segoe UI", 12, "bold"),
            fg="white",
            bg="#9ca3af",
        )
        self._banner_label.pack(expand=True)

        # ── Microphone selection & testing ────────────────────────────────
        mic_frame = ttk.LabelFrame(body, text="Microphone")
        mic_frame.pack(fill=tk.X, pady=(0, 10))
        mic_inner = ttk.Frame(mic_frame)
        mic_inner.pack(fill=tk.X, padx=10, pady=10)

        mic_row = ttk.Frame(mic_inner)
        mic_row.pack(fill=tk.X)
        ttk.Label(mic_row, text="Input device:", font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack(
            side=tk.LEFT,
        )
        self._mic_var = tk.StringVar(value="Click Select to choose microphone")
        self._mic_entry = ttk.Entry(
            mic_row,
            textvariable=self._mic_var,
            state="readonly",
            width=34,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        )
        self._mic_entry.pack(side=tk.LEFT, padx=(8, 4), fill=tk.X, expand=True)
        try:
            self._select_mic_btn = ttk.Button(
                mic_row, text="Select…", width=8,
                bootstyle="primary-outline", command=self._open_mic_picker,
            )
            self._refresh_btn = ttk.Button(
                mic_row, text="Refresh", width=8,
                bootstyle="secondary-outline", command=self._refresh_mic_list,
            )
        except Exception:
            self._select_mic_btn = ttk.Button(
                mic_row, text="Select…", width=8, command=self._open_mic_picker,
            )
            self._refresh_btn = ttk.Button(
                mic_row, text="Refresh", width=8, command=self._refresh_mic_list,
            )
        self._select_mic_btn.pack(side=tk.LEFT, padx=(4, 0))
        self._refresh_btn.pack(side=tk.LEFT, padx=(4, 0))

        level_row = ttk.Frame(mic_inner)
        level_row.pack(fill=tk.X, pady=(8, 4))
        ttk.Label(level_row, text="Audio level:", font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack(
            side=tk.LEFT,
        )
        self._level_var = tk.DoubleVar(value=0.0)
        try:
            self._level_bar = ttk.Progressbar(
                level_row,
                variable=self._level_var,
                maximum=100,
                length=240,
                bootstyle="secondary",
            )
        except Exception:
            self._level_bar = ttk.Progressbar(
                level_row, variable=self._level_var, maximum=100, length=240,
            )
        self._level_bar.pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)

        test_row = ttk.Frame(mic_inner)
        test_row.pack(fill=tk.X, pady=(6, 4))
        try:
            self._test_level_btn = ttk.Button(
                test_row, text="Test Level (3s)", width=14,
                bootstyle="info-outline", command=self._run_level_test,
            )
            self._test_voice_btn = ttk.Button(
                test_row, text="Test Voice", width=14,
                bootstyle="primary-outline", command=self._run_voice_test,
            )
        except Exception:
            self._test_level_btn = ttk.Button(
                test_row, text="Test Level (3s)", width=14, command=self._run_level_test,
            )
            self._test_voice_btn = ttk.Button(
                test_row, text="Test Voice", width=14, command=self._run_voice_test,
            )
        self._test_level_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._test_voice_btn.pack(side=tk.LEFT)

        self._test_result_var = tk.StringVar(value="Use Test Level or Test Voice to check your mic.")
        ttk.Label(
            mic_inner,
            textvariable=self._test_result_var,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
            wraplength=480,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(4, 0))

        # ── Assistant listening status ────────────────────────────────────
        status_frame = ttk.LabelFrame(body, text="Voice Commands")
        status_frame.pack(fill=tk.X, pady=(0, 6))
        status_inner = ttk.Frame(status_frame)
        status_inner.pack(fill=tk.X, padx=10, pady=10)

        self._indicator_var = tk.StringVar(value="● Assistant off")
        self._indicator = tk.Label(
            status_inner,
            textvariable=self._indicator_var,
            font=("Segoe UI", 13, "bold"),
            fg="#6b7280",
        )
        self._indicator.pack(anchor="center", pady=(0, 6))

        self._status_var = tk.StringVar(
            value="Turn the mic on below to start voice navigation.",
        )
        ttk.Label(
            status_inner,
            textvariable=self._status_var,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
            wraplength=480,
            justify=tk.LEFT,
        ).pack(fill=tk.X)

        heard_row = tk.Frame(status_inner, bg="#ffffff", padx=10, pady=8)
        heard_row.pack(fill=tk.X, pady=(8, 0))
        tk.Label(
            heard_row,
            text="Heard now",
            font=("Segoe UI", 9),
            fg="#64748b",
            bg="#ffffff",
            anchor="w",
        ).pack(fill=tk.X)
        self._live_heard_var = tk.StringVar(value="—")
        tk.Label(
            heard_row,
            textvariable=self._live_heard_var,
            font=("Segoe UI", 11, "bold"),
            fg="#0f172a",
            bg="#ffffff",
            wraplength=460,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)

        last_row = tk.Frame(status_inner, bg="#f1f5f9", padx=10, pady=8)
        last_row.pack(fill=tk.X, pady=(8, 0))
        tk.Label(
            last_row,
            text="Last command",
            font=("Segoe UI", 9),
            fg="#64748b",
            bg="#f1f5f9",
            anchor="w",
        ).pack(fill=tk.X)
        self._heard_var = tk.StringVar(value="—")
        tk.Label(
            last_row,
            textvariable=self._heard_var,
            font=("Segoe UI", 10, "bold"),
            fg="#0f172a",
            bg="#f1f5f9",
            wraplength=460,
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)

        footer_shell = ttk.Frame(self.win)
        footer_shell.grid(row=1, column=0, sticky="ew")
        ttk.Separator(footer_shell, orient="horizontal").pack(fill=tk.X)
        footer = ttk.Frame(footer_shell)
        footer.pack(fill=tk.X, padx=12, pady=8)

        try:
            self._close_btn = ttk.Button(
                footer, text="Close", width=12,
                bootstyle="secondary", command=self.close,
            )
            self._mic_btn = ttk.Button(
                footer, text="Turn Mic On", width=14,
                bootstyle="success", command=self._toggle_mic,
            )
        except Exception:
            self._close_btn = ttk.Button(footer, text="Close", width=12, command=self.close)
            self._mic_btn = ttk.Button(
                footer, text="Turn Mic On", width=14, command=self._toggle_mic,
            )
        self._close_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._mic_btn.pack(side=tk.RIGHT)

        from core.dialog_escape import bind_escape_to_close
        bind_escape_to_close(self.win, on_close=self.close)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._refresh_mic_list(select_device=load_saved_device())
        self._sync_from_assistant()
        self._set_model_loading_ui(True)
        self._start_speech_model_load()
        self.win.after(1, self._present)

    def _set_model_loading_ui(self, loading: bool):
        """Disable voice controls until the Whisper model is ready."""
        state = "disabled" if loading else "normal"
        try:
            self._mic_btn.configure(state=state)
            self._test_level_btn.configure(state=state)
            self._test_voice_btn.configure(state=state)
            if loading:
                self._select_mic_btn.configure(state="disabled")
                self._refresh_btn.configure(state="disabled")
        except tk.TclError:
            pass
        if loading:
            try:
                self._model_progress.start(10)
            except tk.TclError:
                pass
        else:
            try:
                self._model_progress.stop()
            except tk.TclError:
                pass

    def _start_speech_model_load(self):
        """Load Whisper when the dialog opens; show progress until ready."""
        assistant = self._assistant()
        if assistant is None:
            self._model_status_var.set("Voice assistant is not available.")
            self._set_model_loading_ui(False)
            return
        if assistant.is_model_ready:
            self._on_model_ready()
            return

        def _on_progress(msg: str):
            self._ui(lambda m=msg: self._model_status_var.set(m))

        def _on_ready():
            self._ui(self._on_model_ready)

        assistant.preload_model(on_progress=_on_progress, on_ready=_on_ready)

    def _on_model_ready(self):
        assistant = self._assistant()
        if assistant is None or not assistant.is_model_ready:
            self._model_loaded = False
            self._set_model_loading_ui(False)
            err = ""
            if assistant is not None and assistant._recognizer is not None:
                err = assistant._recognizer.load_error or ""
            self._model_status_var.set(
                err or "Failed to load speech model. Close this window and try again.",
            )
            return

        self._model_loaded = True
        self._set_model_loading_ui(False)
        self._model_status_var.set("Speech model ready — turn the mic on when you are ready.")
        try:
            self._model_frame.configure(text="Speech model — ready")
        except Exception:
            pass
        self._refresh_mic_list(select_device=load_saved_device())
        self._warmup_tts()
        self._try_auto_start_mic()

    def _warmup_tts(self):
        def _work():
            try:
                from core.voice.tts import _ensure_worker, warmup_engine
                _ensure_worker()
                warmup_engine()
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True, name="SatpudaTTSWarmup").start()

    def _try_auto_start_mic(self):
        try:
            from core.voice.assistant_config import load_voice_auto_start_mic
            assistant = self._assistant()
            if not load_voice_auto_start_mic() or assistant is None or assistant.enabled:
                return
            if assistant.enable(start_standby=True):
                self.app._update_voice_nav_button()
                self._set_mic_button_on()
                self.on_state(assistant.state)
        except Exception:
            pass

    def _present(self):
        from core.scroll_manager import finalize_dialog_geometry
        from core.window_icon import get_screen_work_area

        max_w, _max_h, _sw, _sh = get_screen_work_area(self.win)
        finalize_dialog_geometry(
            self.win,
            width=min(560, max_w),
            height=min(640, _max_h),
            resizable=False,
        )
        try:
            self.win.transient(self.root)
            self.win.lift()
            self.win.focus_force()
        except Exception:
            pass

    def _step_instructions(self) -> str:
        return f'Say one phrase: "{get_voice_listening_example()}"'

    def refresh_assistant_name(self):
        if not self._alive():
            return
        self._step_var.set(self._step_instructions())

    def _assistant(self):
        return getattr(self.app, "_voice_assistant", None)

    def _selected_device_index(self):
        dev = self._device_map.get(self._mic_var.get())
        if isinstance(dev, dict):
            return dev.get("index")
        return dev

    def _selected_device(self):
        dev = self._device_map.get(self._mic_var.get())
        if isinstance(dev, dict):
            return dev
        if getattr(self, "_device_list", None):
            for item in self._device_list:
                if item.get("label") == self._mic_var.get():
                    return item
        return None

    def _set_mic_banner(self, on: bool):
        self._mic_on = on
        if on:
            self._banner_var.set("● MIC IS ON — LISTENING FOR COMMANDS")
            self._banner_frame.configure(bg="#16a34a")
            self._banner_label.configure(bg="#16a34a", fg="white")
        else:
            self._banner_var.set("MIC IS OFF")
            self._banner_frame.configure(bg="#9ca3af")
            self._banner_label.configure(bg="#9ca3af", fg="white")

    def _sync_from_assistant(self):
        assistant = self._assistant()
        if assistant is None:
            return
        if assistant.enabled:
            self._set_mic_button_on()
        else:
            self._set_mic_button_off()
        self.on_state(assistant.state)

    def _refresh_mic_list(self, select_index=None, select_backend=None, select_device=None):
        from core.voice.mic_devices import (
            invalidate_device_cache,
            list_input_devices,
            load_saved_device,
            resolve_device,
        )

        invalidate_device_cache()
        self._mic_var.set("Scanning microphones…")
        self._select_mic_btn.configure(state="disabled")
        self._refresh_btn.configure(state="disabled")

        preferred = select_device
        if preferred is None and select_index is not None:
            preferred = {"index": select_index, "backend": select_backend}
        elif preferred is None:
            preferred = load_saved_device()

        devices = list_input_devices(timeout=12.0)
        self._device_list = list(devices)
        self._device_map = {d["label"]: d for d in devices}

        assistant = self._assistant()
        enabled = assistant is not None and assistant.enabled

        if not devices:
            self._mic_var.set("No microphone found — open Windows Sound settings")
            self._test_level_btn.configure(state="disabled")
            self._test_voice_btn.configure(state="disabled")
        else:
            pick = resolve_device(preferred)
            if pick:
                self._mic_var.set(pick["label"])
                if assistant is not None and not enabled:
                    assistant.set_input_device(pick)
            else:
                self._mic_var.set(devices[0]["label"])
                if assistant is not None and not enabled:
                    assistant.set_input_device(devices[0])
            if enabled:
                self._test_level_btn.configure(state="disabled")
                self._test_voice_btn.configure(state="disabled")
            else:
                self._test_level_btn.configure(state="normal")
                self._test_voice_btn.configure(state="normal")

        self._select_mic_btn.configure(state="disabled" if enabled else "normal")
        self._refresh_btn.configure(state="disabled" if enabled else "normal")

    def _open_mic_picker(self):
        assistant = self._assistant()

        def _on_selected(dev):
            self._device_map = {dev["label"]: dev}
            self._device_list = list(self._device_map.values())
            self._mic_var.set(dev["label"])
            self._test_result_var.set("Selected: {}".format(dev["label"]))

        open_mic_picker_dialog(
            self.win,
            assistant=assistant,
            on_selected=_on_selected,
        )

    def _get_test_recognizer(self):
        assistant = self._assistant()
        dev = self._selected_device()
        if assistant is not None:
            assistant._ensure_recognizer()
            if dev is not None:
                assistant._recognizer.set_input_device(dev)
            return assistant._recognizer
        if self._test_recognizer is None:
            self._test_recognizer = WhisperRecognizer(input_device=dev)
        elif dev is not None:
            self._test_recognizer.set_input_device(dev)
        return self._test_recognizer

    def _ui(self, callback):
        """Run UI update on main thread only if dialog is still open."""
        if self._closed or not self._alive():
            return

        def _run():
            if self._closed or not self._alive():
                return
            try:
                callback()
            except tk.TclError:
                pass

        try:
            self.root.after(0, _run)
        except tk.TclError:
            pass

    def _set_testing(self, active: bool):
        if self._closed or not self._alive():
            return
        state = "disabled" if active else "normal"
        try:
            self._test_level_btn.configure(state=state)
            self._test_voice_btn.configure(state=state)
            self._refresh_btn.configure(state=state)
            if not active:
                assistant = self._assistant()
                if assistant is None or not assistant.enabled:
                    self._select_mic_btn.configure(state="normal")
        except tk.TclError:
            pass

    def _run_level_test(self):
        if not self._model_loaded:
            self._test_result_var.set("Wait until the speech model finishes loading.")
            return
        if self._test_thread and self._test_thread.is_alive():
            return
        dev = self._selected_device()
        if dev is None:
            self._test_result_var.set("No microphone selected — click Select…")
            return

        self._test_stop.clear()
        self._set_testing(True)
        self._test_result_var.set("Testing level — speak into the mic for 3 seconds...")
        voice_log("Level test started on {}".format(dev.get("label", "")))

        def _work():
            try:
                rec = self._get_test_recognizer()

                def _level(lv):
                    self._ui(lambda: self._level_var.set(max(0.0, min(100.0, lv * 100.0))))

                peak = rec.preview_levels(
                    seconds=3.0,
                    stop_event=self._test_stop,
                    on_audio_level=_level,
                    device=dev,
                )
                pct = int(peak * 100)
                if peak < 0.02:
                    msg = "No audio detected. Try another mic or speak louder."
                elif peak < 0.08:
                    msg = f"Low level ({pct}%). Mic works but signal is weak."
                else:
                    msg = f"Good level ({pct}%). Microphone is working."
                self._ui(lambda m=msg: self._test_result_var.set(m))
                voice_log(f"Level test result: {msg}")
            except VoiceRecognitionError as exc:
                self._ui(lambda e=str(exc): self._test_result_var.set(e))
            except Exception as exc:
                self._ui(lambda e=str(exc): self._test_result_var.set(f"Test failed: {e}"))
            finally:
                self._ui(lambda: self._set_testing(False))
                self._ui(lambda: self._level_var.set(0.0))

        self._test_thread = threading.Thread(target=_work, daemon=True)
        self._test_thread.start()

    def _run_voice_test(self):
        if not self._model_loaded:
            self._test_result_var.set("Wait until the speech model finishes loading.")
            return
        if self._test_thread and self._test_thread.is_alive():
            return
        dev = self._selected_device()
        if dev is None:
            self._test_result_var.set("No microphone selected — click Select…")
            return

        self._test_stop.clear()
        self._set_testing(True)
        self._test_result_var.set("Test Voice — speak a short phrase now...")
        voice_log("Voice test started on {}".format(dev.get("label", "")))

        def _work():
            try:
                rec = self._get_test_recognizer()

                def _level(lv):
                    self._ui(lambda: self._level_var.set(max(0.0, min(100.0, lv * 100.0))))

                def _progress(msg):
                    self._ui(lambda m=msg: self._test_result_var.set(m))

                text = rec.test_voice(
                    max_seconds=5.0,
                    stop_event=self._test_stop,
                    on_audio_level=_level,
                    on_progress=_progress,
                    device=dev,
                )
                if text:
                    msg = f'You said: "{text}"'
                else:
                    msg = "No speech recognized. Try speaking louder or pick another mic."
                self._ui(lambda m=msg: self._test_result_var.set(m))
            except VoiceRecognitionError as exc:
                self._ui(lambda e=str(exc): self._test_result_var.set(e))
            except Exception as exc:
                self._ui(lambda e=str(exc): self._test_result_var.set(f"Test failed: {e}"))
            finally:
                self._ui(lambda: self._set_testing(False))
                self._ui(lambda: self._level_var.set(0.0))

        self._test_thread = threading.Thread(target=_work, daemon=True)
        self._test_thread.start()

    def _set_mic_button_on(self):
        self._set_mic_banner(True)
        try:
            self._mic_btn.configure(text="Turn Mic Off", bootstyle="danger")
        except Exception:
            self._mic_btn.configure(text="Turn Mic Off")
        self._select_mic_btn.configure(state="disabled")
        self._mic_entry.configure(state="disabled")
        self._test_level_btn.configure(state="disabled")
        self._test_voice_btn.configure(state="disabled")
        self._refresh_btn.configure(state="disabled")

    def _set_mic_button_off(self):
        self._set_mic_banner(False)
        try:
            self._mic_btn.configure(text="Turn Mic On", bootstyle="success")
        except Exception:
            self._mic_btn.configure(text="Turn Mic On")
        assistant = self._assistant()
        if assistant is not None:
            self._refresh_mic_list(
                select_index=assistant.input_device,
                select_backend=getattr(assistant, "input_device_backend", None),
            )
        else:
            self._refresh_mic_list()

    def sync_mic_from_assistant(self):
        """Keep Turn Mic On/Off button in sync when voice commands change the mic."""
        if not self._alive():
            return
        assistant = self._assistant()
        if assistant is None or not assistant.enabled:
            self._stop_pulse()
            self._set_mic_button_off()
        else:
            self._set_mic_button_on()

    def _toggle_mic(self):
        assistant = self._assistant()
        if assistant is None:
            return
        if not self._model_loaded and not assistant.is_model_ready:
            self._test_result_var.set("Speech model is still loading — please wait.")
            return
        enabled = assistant.toggle()
        if enabled:
            self._set_mic_button_on()
        else:
            self._stop_pulse()
            self._set_mic_button_off()

    def _stop_pulse(self):
        if self._pulse_job is not None:
            try:
                self.root.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None

    def _start_pulse(self):
        self._stop_pulse()
        self._pulse_indicator()

    def _pulse_indicator(self):
        assistant = self._assistant()
        if assistant is None or not assistant.enabled:
            return
        if assistant.state not in (STATE_AWAITING_WAKE, STATE_LISTENING, STATE_COMMAND_LISTENING):
            return
        self._pulse_on = not self._pulse_on
        if self._mic_on:
            color = "#15803d" if self._pulse_on else "#16a34a"
            self._banner_frame.configure(bg=color)
            self._banner_label.configure(bg=color)
        self._indicator.configure(fg="#16a34a" if self._pulse_on else "#86efac")
        self._pulse_job = self.root.after(450, self._pulse_indicator)

    def on_live_heard(self, text: str):
        if not self._alive():
            return
        if text and text.strip():
            display = text.strip()
            if len(display) > 100:
                display = display[:97] + "…"
            self._live_heard_var.set(display)
        elif getattr(self, "_live_heard_var", None) is not None:
            assistant = self._assistant()
            if assistant is not None and assistant.enabled:
                self._live_heard_var.set("Listening…")

    def on_heard(self, text: str):
        if not self._alive():
            return
        if text and text.strip():
            display = text.strip()
            if len(display) > 100:
                display = display[:97] + "…"
            self._heard_var.set(display)
        elif not text:
            pass

    def on_status(self, message: str):
        if not self._alive():
            return
        self._status_var.set(message)

    def on_result(self, recognized: str, action: str, detail: str = "", spoken_source: str = ""):
        if not self._alive():
            return
        if action == "Working":
            line = f"Recognized: {recognized}\n{detail or 'Working…'}"
            self._status_var.set(line)
            return
        if action.startswith("Opened"):
            line = f"Recognized: {recognized}\nAction: {action}"
        elif detail:
            line = f"Recognized: {recognized}\nAction: {action} — {detail}"
        else:
            line = f"Recognized: {recognized}\nAction: {action}"
        self._status_var.set(line)

    def on_state(self, state: str, _detail: str = ""):
        if not self._alive():
            return
        self._stop_pulse()
        assistant = self._assistant()
        mic_enabled = assistant is not None and assistant.enabled
        self._set_mic_banner(mic_enabled)

        if state == STATE_OFF:
            self._indicator_var.set("● Assistant off")
            self._indicator.configure(fg="#6b7280")
            if not mic_enabled:
                self._level_var.set(0.0)
            try:
                self._level_bar.configure(bootstyle="secondary")
            except Exception:
                pass
        elif state == STATE_LOADING:
            self._indicator_var.set("● Loading speech model...")
            self._indicator.configure(fg="#d97706")
        elif state == STATE_STANDBY:
            name = get_assistant_display_name()
            self._indicator_var.set(f"● Standby — say Hey {name}")
            self._indicator.configure(fg="#b45309")
            if mic_enabled:
                self._banner_var.set(f'● MIC ON — say "Hey {name}" to start listening')
            try:
                self._level_bar.configure(bootstyle="warning-striped")
            except Exception:
                pass
        elif state in (STATE_AWAITING_WAKE, STATE_LISTENING):
            name = get_assistant_display_name()
            self._indicator_var.set(f"● Listening — {name} + command")
            self._indicator.configure(fg="#64748b")
            if mic_enabled:
                self._banner_var.set(f'● MIC ON — say "{get_voice_listening_example()}"')
            try:
                self._level_bar.configure(bootstyle="secondary-striped")
            except Exception:
                pass
        elif state == STATE_COMMAND_LISTENING:
            name = get_assistant_display_name()
            self._indicator_var.set(f"● {name} heard — say command")
            self._indicator.configure(fg="#16a34a")
            if mic_enabled:
                self._banner_var.set("● MIC ON — say your command")
            try:
                self._level_bar.configure(bootstyle="success-striped")
            except Exception:
                pass
            self._start_pulse()
        elif state == STATE_READY:
            self._indicator_var.set("● Ready")
            self._indicator.configure(fg="#2563eb")
            try:
                self._level_bar.configure(bootstyle="info-striped")
            except Exception:
                pass
        elif state == STATE_PROCESSING:
            self._indicator_var.set("● Processing speech...")
            self._indicator.configure(fg="#ca8a04")
            if mic_enabled:
                self._banner_var.set("● MIC IS ON — PROCESSING")
            try:
                self._level_bar.configure(bootstyle="warning-striped")
            except Exception:
                pass

    def on_audio_level(self, level: float):
        if not self._alive():
            return
        self._level_var.set(max(0.0, min(100.0, float(level) * 100.0)))

    def _alive(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def close(self):
        """Close the panel only — microphone stays on if it was enabled."""
        self._closed = True
        self._test_stop.set()
        self._stop_pulse()
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.app, "_voice_dialog", None) is self:
            self.app._voice_dialog = None
        if hasattr(self.app, "_update_voice_nav_button"):
            self.app._update_voice_nav_button()
