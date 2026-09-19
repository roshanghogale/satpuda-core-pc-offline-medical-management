"""Satpuda voice assistant — one listen; command runs only after Satpuda in the phrase."""
from __future__ import annotations

import threading
import time

from core.voice.assistant_config import get_assistant_display_name

from core.voice.command_parser import (
    CommandParseResult,
    get_assistant_name,
    is_incomplete_command,
    is_intentional_command_attempt,
    parse_command_only,
    parse_voice_command,
)
from core.voice.mic_devices import device_label, load_saved_device, resolve_device
from core.voice.navigator import open_screen
from core.voice.recognizer import VoiceRecognitionError, WhisperRecognizer
from core.voice.voice_config import (
    ACTIVE_LISTEN_IDLE_TIMEOUT_SECONDS,
    ACTIVE_LISTEN_SILENCE_SECONDS,
    FULL_LISTEN_MAX_SECONDS,
    FULL_LISTEN_SILENCE_SECONDS,
    LISTEN_IDLE_TIMEOUT_SECONDS,
    STANDBY_LISTEN_IDLE_TIMEOUT_SECONDS,
    STANDBY_LISTEN_SILENCE_SECONDS,
    TTS_WAIT_ACTIVE_SECONDS,
    TTS_WAIT_STANDBY_SECONDS,
)
from core.voice.voice_log import voice_log

STATE_OFF = "off"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_STANDBY = "standby"
STATE_AWAITING_WAKE = "awaiting_wake"
STATE_COMMAND_LISTENING = "command_listening"  # kept for overlay compat
STATE_LISTENING = STATE_AWAITING_WAKE
STATE_PROCESSING = "processing"

_MANAGEMENT_ACTIONS = frozenset({
    "check_updates",
    "install_update",
    "backup_now",
    "sync_from_drive",
})


class SatpudaVoiceAssistant:
    """Always listening; executes only when Satpuda is heard, using text after it."""

    def __init__(
        self,
        app,
        on_status=None,
        on_result=None,
        on_state=None,
        on_audio_level=None,
        on_mic_changed=None,
        on_heard=None,
        on_live_heard=None,
    ):
        self.app = app
        self.root = app.root
        self.on_status = on_status
        self.on_result = on_result
        self.on_state = on_state
        self.on_audio_level = on_audio_level
        self.on_mic_changed = on_mic_changed
        self.on_heard = on_heard
        self.on_live_heard = on_live_heard
        self._enabled = False
        self._stop_event = threading.Event()
        self._thread = None
        self._recognizer = None
        self._model_preload_started = False
        self._model_ready_callbacks: list = []
        self._model_preload_lock = threading.Lock()
        self._last_recognized = ""
        self._last_action = ""
        self._last_raw_heard = ""
        self._listening_active = False
        self._standby_cooldown_until = 0.0
        self._state = STATE_OFF
        self._selected_device = None  # full device dict when user picks a mic

    def _resolved_input_device(self):
        if self._selected_device is None:
            self._selected_device = resolve_device(load_saved_device())
        return self._selected_device

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def in_standby(self) -> bool:
        return self._enabled and not self._listening_active

    @property
    def state(self) -> str:
        return self._state

    @property
    def input_device(self):
        dev = self._resolved_input_device()
        return dev.get("index") if dev else None

    @property
    def input_device_backend(self):
        dev = self._resolved_input_device()
        return (dev or {}).get("backend", "winmm")

    @property
    def last_recognized(self) -> str:
        return self._last_recognized

    @property
    def last_action(self) -> str:
        return self._last_action

    def set_input_device(self, device_or_index):
        if isinstance(device_or_index, dict):
            self._selected_device = device_or_index
        else:
            self._selected_device = resolve_device(device_or_index)
        if self._recognizer is not None and self._selected_device:
            self._recognizer.set_input_device(self._selected_device)
        if self._selected_device:
            voice_log("Microphone set to: {}".format(self._selected_device.get("label", "")))

    def _listening_hint(self) -> str:
        from core.voice.assistant_config import get_voice_listening_example
        return f'Say "{get_voice_listening_example()}" (one phrase)'

    def _set_state(self, state: str, detail: str = ""):
        # Avoid log/UI spam when the listen loop retries the same state.
        if state == getattr(self, "_state", None) and not detail:
            return
        self._state = state
        if callable(self.on_state):
            self.root.after(0, lambda: self.on_state(state, detail))
        messages = {
            STATE_OFF: "Microphone OFF",
            STATE_STANDBY: "Say Hey Satpuda to listen",
            STATE_LOADING: "Loading speech model...",
            STATE_READY: "Ready",
            STATE_AWAITING_WAKE: self._listening_hint(),
            STATE_PROCESSING: "Processing...",
        }
        voice_log(messages.get(state, state) + (f" ({detail})" if detail else ""))

    def _emit_status(self, message: str):
        if callable(self.on_status):
            self.root.after(0, lambda: self.on_status(message))

    def _emit_result(self, recognized: str, action: str, detail: str = "", spoken_source: str = ""):
        self._last_recognized = recognized
        self._last_action = action
        if callable(self.on_result):
            src = spoken_source or recognized
            self.root.after(
                0,
                lambda r=recognized, a=action, d=detail, s=src: self.on_result(r, a, d, s),
            )

    def _emit_level(self, level: float):
        if callable(self.on_audio_level):
            self.root.after(0, lambda: self.on_audio_level(level))

    def _emit_heard(self, text: str):
        if callable(self.on_heard):
            self.root.after(0, lambda: self.on_heard(text))

    def _emit_live_heard(self, text: str):
        if callable(self.on_live_heard):
            self.root.after(0, lambda: self.on_live_heard(text))

    def _ensure_recognizer(self):
        device = self._resolved_input_device()
        if self._recognizer is None:
            self._recognizer = WhisperRecognizer(input_device=device)
        else:
            self._recognizer.set_input_device(device)

    @property
    def is_model_ready(self) -> bool:
        return self._recognizer is not None and self._recognizer.is_ready

    def _call_ui(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _wrap_model_progress(self, on_progress=None):
        def _emit(msg: str):
            self._emit_status(msg)
            if on_progress:
                self._call_ui(lambda m=msg: on_progress(m))
        return _emit

    def preload_model(self, on_ready=None, on_progress=None):
        """Load Whisper in a background thread (intended when voice dialog opens)."""
        if self.is_model_ready:
            if on_progress:
                self._call_ui(lambda: on_progress("Speech model ready"))
            if on_ready:
                self._call_ui(on_ready)
            return

        with self._model_preload_lock:
            if on_ready:
                self._model_ready_callbacks.append(on_ready)
            if self._model_preload_started:
                return
            self._model_preload_started = True

        def _work():
            try:
                self._ensure_recognizer()
                progress = self._wrap_model_progress(on_progress)
                if self._recognizer.ensure_model(on_progress=progress):
                    self._recognizer.warmup()
            except Exception as exc:
                voice_log(f"Speech preload skipped: {exc}")
            finally:
                with self._model_preload_lock:
                    callbacks = list(self._model_ready_callbacks)
                    self._model_ready_callbacks.clear()
                for cb in callbacks:
                    self._call_ui(cb)

        threading.Thread(target=_work, daemon=True, name="SatpudaWhisperPreload").start()

    def enable(self, *, start_standby: bool | None = None) -> bool:
        if self._enabled:
            return True
        try:
            self._ensure_recognizer()
        except VoiceRecognitionError as exc:
            self._emit_status(str(exc))
            voice_log(str(exc), level="error")
            self._set_state(STATE_OFF)
            return False

        if not self._recognizer.is_ready:
            self._set_state(STATE_LOADING)
            threading.Thread(
                target=lambda: self._recognizer.ensure_model(on_progress=self._emit_status),
                daemon=True,
            ).start()

        if start_standby is None:
            from core.voice.assistant_config import load_voice_wake_only
            start_standby = load_voice_wake_only()

        mic_name = device_label(self.input_device)
        voice_log(f"Microphone ON — using: {mic_name}")
        self._enabled = True
        self._listening_active = not start_standby
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        self._set_state(STATE_STANDBY if start_standby else STATE_AWAITING_WAKE, mic_name)
        self._notify_mic_changed()
        return True

    def activate(self) -> bool:
        """Exit standby — full command listening."""
        if not self._enabled:
            return False
        self._listening_active = True
        self._set_state(STATE_AWAITING_WAKE)
        self._notify_mic_changed()
        voice_log("Satpuda activated — listening for commands")
        return True

    def start_listening(self) -> bool:
        """Turn mic on (if needed) and listen for commands immediately."""
        if not self._enabled:
            if not self.enable(start_standby=False):
                return False
            self._notify_mic_changed()
            return True
        if not self._listening_active:
            ok = self.activate()
            self._notify_mic_changed()
            return ok
        return True

    def stop_listening(self):
        """Pause commands — mic stays on; Hey Satpuda starts again."""
        self.enter_standby()

    def turn_off_mic(self):
        """Fully stop the microphone."""
        self.disable()

    def _in_standby_cooldown(self) -> bool:
        return time.monotonic() < self._standby_cooldown_until

    def enter_standby(self):
        """Pause commands — mic stays on; only Hey Satpuda starts listening again."""
        if not self._enabled:
            return
        self._listening_active = False
        # Ignore wake phrases briefly so TTS / echo does not re-activate listening.
        self._standby_cooldown_until = time.monotonic() + 3.5
        self._notify_mic_changed()
        self._set_state(STATE_STANDBY)
        self._emit_live_heard("")
        voice_log("Satpuda standby — say Hey Satpuda to listen")

    def disable(self):
        if not self._enabled and self._state == STATE_OFF:
            return
        self._enabled = False
        self._listening_active = False
        self._stop_event.set()
        self._set_state(STATE_OFF)
        voice_log("Microphone OFF")
        self._notify_mic_changed()

    def _notify_mic_changed(self):
        if callable(self.on_mic_changed):
            self.root.after(0, self.on_mic_changed)

    def reload_assistant_name(self):
        from core.voice.command_parser import reload_voice_parser_config
        reload_voice_parser_config()
        if self._enabled:
            self._set_state(STATE_STANDBY if not self._listening_active else STATE_AWAITING_WAKE)

    def reload_voice_settings(self):
        """Reload wake word, language, and listening hints from disk."""
        self.reload_assistant_name()

    def toggle(self) -> bool:
        if self._enabled:
            self.disable()
            return False
        return self.enable()

    def _try_dialog_voice_on_ui(self, heard: str):
        """
        Run dialog voice handling on the Tk UI thread.
        Calling grab_current / destroy from the listen thread freezes voice.
        """
        import threading

        box: dict = {}
        done = threading.Event()

        def _run():
            try:
                from core.dialog_escape import active_dialog
                from core.voice.dialog_voice import try_dialog_voice

                if active_dialog(self.app.root) is None:
                    box["r"] = None
                else:
                    box["r"] = try_dialog_voice(
                        self.app, heard, allow_without_wake=True,
                    )
            except Exception as exc:
                voice_log(f"Dialog voice UI failed: {exc}", level="error")
                box["r"] = None
            finally:
                done.set()

        try:
            self.root.after(0, _run)
        except Exception:
            return None
        # Nested wait_window still runs after(); native filedialog may time out.
        if not done.wait(timeout=8.0):
            voice_log("Dialog voice UI marshal timed out (main thread busy)")
            return None
        return box.get("r")

    def _listen_once(
        self,
        *,
        whisper_prompt=None,
        max_seconds=None,
        silence_seconds=None,
        idle_timeout_seconds=None,
        listening_state=STATE_AWAITING_WAKE,
    ) -> str:
        from core.voice.voice_config import get_whisper_initial_prompt

        def _on_listen_start(_device=None):
            self._emit_live_heard("")
            self._set_state(listening_state)

        def _on_listen_stop():
            self._set_state(STATE_PROCESSING)

        def _on_processing():
            self._set_state(STATE_PROCESSING)

        def _on_transcribed(raw: str, normalized: str):
            self._last_raw_heard = (raw or "").strip()
            text = (raw or normalized or "").strip()
            if text:
                self._emit_live_heard(text)

        return self._recognizer.listen_once(
            stop_event=self._stop_event,
            on_audio_level=self._emit_level,
            on_listening_start=_on_listen_start,
            on_listening_stop=_on_listen_stop,
            on_processing=_on_processing,
            on_transcribed=_on_transcribed,
            whisper_prompt=whisper_prompt or get_whisper_initial_prompt(),
            max_seconds=max_seconds or FULL_LISTEN_MAX_SECONDS,
            silence_seconds=silence_seconds or FULL_LISTEN_SILENCE_SECONDS,
            idle_timeout_seconds=idle_timeout_seconds if idle_timeout_seconds is not None else LISTEN_IDLE_TIMEOUT_SECONDS,
        )

    def _listen_loop(self):
        try:
            self._recognizer.ensure_model(on_progress=self._emit_status)
        except Exception:
            pass

        while self._enabled and not self._stop_event.is_set():
            try:
                from core.voice.tts import wait_until_idle
                wait_until_idle(
                    TTS_WAIT_ACTIVE_SECONDS if self._listening_active else TTS_WAIT_STANDBY_SECONDS
                )

                listen_state = (
                    STATE_AWAITING_WAKE if self._listening_active else STATE_STANDBY
                )
                self._set_state(listen_state)
                self._emit_live_heard("")

                if self._listening_active:
                    heard = self._listen_once(
                        listening_state=listen_state,
                        silence_seconds=ACTIVE_LISTEN_SILENCE_SECONDS,
                        idle_timeout_seconds=ACTIVE_LISTEN_IDLE_TIMEOUT_SECONDS,
                    )
                else:
                    heard = self._listen_once(
                        listening_state=listen_state,
                        silence_seconds=STANDBY_LISTEN_SILENCE_SECONDS,
                        idle_timeout_seconds=STANDBY_LISTEN_IDLE_TIMEOUT_SECONDS,
                    )
                if not self._enabled or self._stop_event.is_set():
                    break
                if not heard:
                    continue

                from core.voice.tts import is_likely_tts_echo
                if is_likely_tts_echo(heard):
                    voice_log(f'TTS echo — ignored: "{heard}"')
                    self._emit_live_heard("")
                    continue

                voice_log(f'Heard: "{heard}"')

                from core.voice.voice_dialog import is_voice_modal_open

                dlg = None
                # Only poll the thread-safe modal depth flag from the listen
                # thread — never call grab_current / active_dialog here.
                if is_voice_modal_open():
                    dlg = self._try_dialog_voice_on_ui(heard)
                if dlg is not None:
                    from core.voice.tts import speak_action_result
                    speak_action_result(dlg.get("message", "Done."))
                    self._emit_heard(heard)
                    self._emit_result(heard, "Action", dlg.get("message", ""))
                    continue

                if not self._listening_active:
                    from core.voice.voice_control import parse_standby_wake
                    from core.voice.tts import speak

                    if self._in_standby_cooldown():
                        voice_log(f'Standby cooldown — ignored: "{heard}"')
                        continue

                    activated, remainder = parse_standby_wake(heard)
                    if activated:
                        self.start_listening()
                        if remainder:
                            wake = get_assistant_name()
                            self._dispatch_voice_command(
                                parse_voice_command(f"{wake} {remainder}"),
                                heard,
                            )
                        else:
                            from core.voice.assistant_config import get_assistant_display_name
                            speak(f"{get_assistant_display_name()} is listening.")
                            self._emit_heard(heard)
                            self._emit_result(heard, "Ready", "Microphone on. Listening for commands.")
                    else:
                        voice_log(f'Standby — ignored: "{heard}"')
                    continue

                result = parse_voice_command(heard)
                if not result.wake_found:
                    if not is_intentional_command_attempt(heard):
                        voice_log(f'Ambient/noise — ignored: "{result.recognized}"')
                        self._emit_live_heard("")
                        continue
                    wake_name = get_assistant_name()
                    voice_log(f'No wake word — ignored: "{result.recognized}"')
                    self._emit_heard(heard)
                    self._emit_result(
                        result.recognized or heard,
                        "No action",
                        f'Say "{wake_name}" before your command',
                    )
                    continue

                self._dispatch_voice_command(result, heard)

            except VoiceRecognitionError as exc:
                self._emit_status(str(exc))
                voice_log(str(exc), level="error")
                self.disable()
                break
            except Exception as exc:
                msg = str(exc)
                voice_log(f"Voice error: {msg}", level="error")
                # Mic open failures must not spin the UI (status flicker).
                is_mic = (
                    "Could not open microphone" in msg
                    or "Invalid sample rate" in msg
                    or "microphone busy" in msg.lower()
                )
                if is_mic:
                    self._emit_status(
                        "Microphone busy or unsupported — retrying in a moment…"
                    )
                    self._stop_event.wait(1.5)
                self._set_state(STATE_AWAITING_WAKE)

    def _dispatch_voice_command(self, result, heard: str = ""):
        if result.action is not None:
            action_id = (result.action or {}).get("id", "")
            if action_id == "stop_listening":
                self.enter_standby()
            if action_id == "start_listening" and self._listening_active:
                voice_log(f'Wake only while listening — ignored: "{result.recognized}"')
                self._emit_heard(result.recognized or heard)
                return

        if not (result.command_text or "").strip():
            voice_log(f'Wake only, no command — ignored: "{result.recognized}"')
            self._emit_heard(result.recognized or heard)
            return

        if result.action is not None:
            action_id = (result.action or {}).get("id", "")
            if action_id in _MANAGEMENT_ACTIONS:
                self._execute_action(result)
            else:
                self._execute_page_action(result)
            return

        if result.entry is None:
            completed = self._try_complete_command(result)
            if completed is not None:
                self._execute(completed)
                return
            if not is_intentional_command_attempt(result.recognized or heard):
                voice_log(f'Low-intent phrase — ignored: "{result.recognized}"')
                self._emit_live_heard("")
                return
            self._emit_heard(result.recognized)
            self._emit_result(result.recognized, "No action", result.reason)
            return

        self._execute(result)

    def _try_complete_command(self, result):
        """Re-listen when the user paused after 'open' / 'go to' before the screen name."""
        if not is_incomplete_command(result.command_text):
            return None
        if not self._enabled or self._stop_event.is_set():
            return None

        from core.voice.voice_config import (
            COMMAND_COMPLETION_MAX_SECONDS,
            COMMAND_COMPLETION_SILENCE_SECONDS,
            get_command_listen_prompt,
        )

        prefix = (result.command_text or "").strip()
        voice_log(f'Incomplete command "{prefix}" — listening for screen name...')
        self._set_state(STATE_COMMAND_LISTENING)

        more = self._listen_once(
            whisper_prompt=get_command_listen_prompt(),
            max_seconds=COMMAND_COMPLETION_MAX_SECONDS,
            silence_seconds=COMMAND_COMPLETION_SILENCE_SECONDS,
            idle_timeout_seconds=COMMAND_COMPLETION_SILENCE_SECONDS,
            listening_state=STATE_COMMAND_LISTENING,
        )
        if not self._enabled or self._stop_event.is_set():
            return None
        if not more:
            return None

        combined = f"{prefix} {more}".strip()
        voice_log(f'Completed phrase: "{combined}"')
        completed = parse_command_only(combined)
        if completed.entry is None:
            return None

        recognized = f"{result.recognized} {more}".strip()
        completed.recognized = recognized
        completed.wake_found = True
        completed.command_text = combined
        return completed

    def _speak_nav_feedback(self, result, *, screen_label: str, screen_id: str = ""):
        from core.voice.tts import speak_command_feedback

        spoken = getattr(self, "_last_raw_heard", "") or result.recognized
        speak_command_feedback(
            kind="nav",
            recognized=spoken,
            command=result.command_text or "",
            screen_label=screen_label,
            screen_id=screen_id,
        )

    def _execute_action(self, result):
        action = result.action or {}
        action_id = action.get("id", "")
        label = result.label or action.get("label", "Action")
        recognized = result.recognized

        from core.voice.tts import speak_command_feedback
        from core.voice.action_executor import run_voice_action

        spoken = getattr(self, "_last_raw_heard", "") or recognized
        progress_msgs = {
            "check_updates": "Checking for updates…",
            "install_update": "Downloading update…",
            "backup_now": "Backing up to Google Drive…",
            "sync_from_drive": "Syncing from Drive…",
        }
        progress = progress_msgs.get(action_id, f"Running {label}…")
        self._emit_heard(recognized)
        self._emit_result(recognized, "Working", progress, spoken)
        speak_command_feedback(kind="action", recognized=spoken, action_id=action_id)

        def _on_complete(ok: bool, summary: str):
            self._emit_heard(recognized)
            if ok:
                self._emit_result(recognized, f"Action {label}", summary, spoken)
                voice_log(f"Voice action done: {summary}")
            else:
                self._emit_result(recognized, "Failed", summary or f"Action failed: {label}", spoken)
                voice_log(f"Voice action failed: {summary}", level="error")

        def _run():
            if not run_voice_action(self.app, action_id, on_complete=_on_complete):
                self._emit_result(recognized, "Failed", f"Could not start: {label}", spoken)

        self.root.after(0, _run)

    def _execute_page_action(self, result):
        action = result.action or {}
        action_id = action.get("id", "")
        params = dict(action.get("voice_params") or {})
        label = result.label or action.get("label", "Action")
        recognized = result.recognized
        spoken = getattr(self, "_last_raw_heard", "") or recognized

        from core.voice.tts import speak_command_feedback
        from core.voice.page_action_executor import run_page_voice_action, _EXPORT_ACTION_IDS

        progress = {
            "save": "Saving…",
            "open_web_purchase": "Opening web purchase entry…",
            "switch_store": "Switching store…",
            "export_menu": "Opening export menu…",
            "export_generic": "Opening export menu…",
            "export_schedule_report": "Opening schedule report…",
            "export_all_data": "Opening export all…",
            "export_sales_data": "Opening export sales…",
            "export_purchases_data": "Opening export purchases…",
            "export_inventory_data": "Opening export inventory…",
            "export_current_view": "Opening export…",
            "close_app": "Closing application…",
            "start_listening": "Starting microphone…",
            "stop_listening": "Pausing listening…",
            "turn_off_mic": "Turning off microphone…",
            "apply_list_filter": "Applying filter…",
        }.get(action_id, f"Running {label}…")
        self._emit_heard(recognized)
        if action_id != "stop_listening":
            self._emit_result(recognized, "Working", progress, spoken)
        if action_id not in (
            "close_dialog", "cancel_dialog", "cancel_all_alerts",
            "close_app", "start_listening", "stop_listening", "turn_off_mic",
        ):
            if action_id not in _EXPORT_ACTION_IDS:
                speak_command_feedback(kind="action", recognized=spoken, action_id=action_id)

        def _on_complete(ok: bool, summary: str):
            self._emit_heard(recognized)
            if ok:
                self._emit_result(recognized, f"Action {label}", summary, spoken)
            else:
                self._emit_result(recognized, "Failed", summary or f"Failed: {label}", spoken)

        if not run_page_voice_action(self.app, action_id, params, on_complete=_on_complete):
            self._emit_result(recognized, "Failed", f"Could not start: {label}", spoken)

    def _execute(self, result):
        label = result.label or result.entry.get("id", "screen")
        screen_id = (result.entry or {}).get("id", "")
        recognized = result.recognized

        from core.voice.voice_sounds import play_ack_sound

        play_ack_sound()
        self._speak_nav_feedback(result, screen_label=label, screen_id=screen_id)

        def _navigate():
            ok = open_screen(self.app, result.entry)
            spoken = getattr(self, "_last_raw_heard", "") or recognized
            if ok:
                self._emit_heard(recognized)
                self._emit_result(recognized, f"Opened {label}", screen_id, spoken)
                voice_log(f"Action: Opened {label}")
            else:
                self._emit_result(recognized, "Failed", "Navigation failed", spoken)
                voice_log("Action: Failed — navigation error")

        self.root.after(0, _navigate)
