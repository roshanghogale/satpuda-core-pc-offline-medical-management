"""Offline navigation TTS — Windows SAPI (primary) with pyttsx3 fallback."""
from __future__ import annotations

import queue
import re
import sys
import threading
import time
from collections import deque

from core.voice.voice_log import voice_log

_queue: queue.Queue = queue.Queue()
_worker_started = False
_speaking = False
_speaking_lock = threading.Lock()
_sapi_voice = None
_pyttsx_engine = None
_engine_ready = threading.Event()
_selected_voice_name = ""
_backend = ""

_FEMALE_VOICE_HINTS = (
    "zira",
    "heera",
    "neerja",
    "hazel",
    "aria",
    "jenny",
    "susan",
    "samantha",
    "female",
)

TTS_RATE = 178
TTS_VOLUME = 100
_SAPI_SYNC = 0  # SVSFDefault — block until speech finishes
_ERROR_TTS_COOLDOWN_SECONDS = 14.0
_last_error_tts_at = 0.0
_error_tts_lock = threading.Lock()
_TTS_ECHO_WINDOW_SECONDS = 9.0
_recent_spoken: deque[tuple[float, str]] = deque(maxlen=8)


def _set_speaking(active: bool):
    global _speaking
    with _speaking_lock:
        _speaking = active


def is_speaking() -> bool:
    with _speaking_lock:
        return _speaking or not _queue.empty()


def wait_until_idle(timeout: float = 12.0) -> None:
    deadline = time.monotonic() + max(0.1, timeout)
    while is_speaking() and time.monotonic() < deadline:
        time.sleep(0.05)


def _ensure_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker_loop, daemon=True, name="SatpudaTTS").start()


def _pick_sapi_voice(voice_obj) -> str:
    try:
        voices = voice_obj.GetVoices()
        count = int(voices.Count)
    except Exception:
        return ""
    for hint in _FEMALE_VOICE_HINTS:
        for idx in range(count):
            try:
                item = voices.Item(idx)
                desc = (item.GetDescription() or "").lower()
                if hint in desc:
                    voice_obj.Voice = item
                    return item.GetDescription()
            except Exception:
                continue
    if count >= 2:
        try:
            voice_obj.Voice = voices.Item(1)
            return voices.Item(1).GetDescription()
        except Exception:
            pass
    if count >= 1:
        try:
            voice_obj.Voice = voices.Item(0)
            return voices.Item(0).GetDescription()
        except Exception:
            pass
    return ""


def _init_sapi_in_worker() -> bool:
    global _sapi_voice, _selected_voice_name, _backend
    if _sapi_voice is not None:
        return True
    if sys.platform != "win32":
        return False
    try:
        import win32com.client
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voice.Rate = 1
        voice.Volume = TTS_VOLUME
        name = _pick_sapi_voice(voice)
        _sapi_voice = voice
        _backend = "sapi"
        if name and name != _selected_voice_name:
            _selected_voice_name = name
            voice_log(f"Navigation TTS voice (SAPI): {name}")
        _engine_ready.set()
        voice_log("TTS engine ready (SAPI)")
        return True
    except Exception as exc:
        voice_log(f"SAPI TTS init failed: {exc}", level="error")
        return False


def _voice_blob(voice) -> str:
    parts = (
        getattr(voice, "name", "") or "",
        getattr(voice, "id", "") or "",
    )
    return " ".join(parts).lower()


def _pick_pyttsx_voice(engine):
    voices = engine.getProperty("voices") or []
    for hint in _FEMALE_VOICE_HINTS:
        for voice in voices:
            if hint in _voice_blob(voice):
                engine.setProperty("voice", voice.id)
                return voice.name
    if len(voices) >= 2:
        engine.setProperty("voice", voices[1].id)
        return voices[1].name
    if voices:
        engine.setProperty("voice", voices[0].id)
        return voices[0].name
    return ""


def _init_pyttsx_in_worker() -> bool:
    global _pyttsx_engine, _selected_voice_name, _backend
    if _pyttsx_engine is not None:
        return True
    try:
        import pyttsx3
        engine = pyttsx3.init(driverName="sapi5") if sys.platform == "win32" else pyttsx3.init()
        engine.setProperty("rate", TTS_RATE)
        engine.setProperty("volume", 1.0)
        name = _pick_pyttsx_voice(engine)
        _pyttsx_engine = engine
        _backend = "pyttsx3"
        if name and name != _selected_voice_name:
            _selected_voice_name = name
            voice_log(f"Navigation TTS voice (pyttsx3): {name}")
        _engine_ready.set()
        voice_log("TTS engine ready (pyttsx3)")
        return True
    except Exception as exc:
        voice_log(f"pyttsx3 TTS init failed: {exc}", level="error")
        return False


def _init_engine_in_worker() -> bool:
    if _init_sapi_in_worker():
        return True
    return _init_pyttsx_in_worker()


def _speak_phrase(text: str) -> bool:
    global _sapi_voice, _pyttsx_engine
    if not _init_engine_in_worker():
        from core.voice.voice_sounds import play_ack_sound
        play_ack_sound()
        return False
    try:
        if _sapi_voice is not None:
            _sapi_voice.Speak(text, _SAPI_SYNC)
        elif _pyttsx_engine is not None:
            _pyttsx_engine.say(text)
            _pyttsx_engine.runAndWait()
        else:
            raise RuntimeError("No TTS backend")
        voice_log(f'Spoke ({_backend}): "{text}"')
        return True
    except Exception as exc:
        voice_log(f"TTS speak error: {exc}", level="error")
        _sapi_voice = None
        _pyttsx_engine = None
        _engine_ready.clear()
        from core.voice.voice_sounds import play_ack_sound
        play_ack_sound()
        return False


def _drain_queue():
    while True:
        try:
            _queue.get_nowait()
        except queue.Empty:
            break


def _worker_loop():
    com_initialized = False
    if sys.platform == "win32":
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception as exc:
            voice_log(f"TTS COM init skipped: {exc}")

    _init_engine_in_worker()

    try:
        while True:
            text = _queue.get()
            if text is None:
                break
            _set_speaking(True)
            try:
                _speak_phrase(text)
            finally:
                _set_speaking(False)
    finally:
        if com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _remember_spoken(phrase: str) -> None:
    low = (phrase or "").strip().lower()
    if low:
        _recent_spoken.append((time.monotonic(), low))


def is_likely_tts_echo(heard: str) -> bool:
    """Ignore mic input that matches very recent assistant speech."""
    raw = (heard or "").strip().lower()
    if not raw:
        return False
    now = time.monotonic()
    tokens = set(re.findall(r"[a-z0-9]+", raw))
    for spoken_at, spoken in _recent_spoken:
        if now - spoken_at > _TTS_ECHO_WINDOW_SECONDS:
            continue
        if raw == spoken or raw in spoken or spoken in raw:
            return True
        spoken_tokens = set(re.findall(r"[a-z0-9]+", spoken))
        if len(tokens) >= 2 and len(tokens & spoken_tokens) >= max(2, len(tokens) - 1):
            return True
    return False


def speak(text: str, *, force: bool = False, priority: bool = False) -> None:
    from core.voice.assistant_config import load_voice_tts_enabled

    phrase = (text or "").strip()
    if not phrase:
        return
    _remember_spoken(phrase)
    if not force and not load_voice_tts_enabled():
        voice_log(f'TTS skipped (disabled in settings): "{phrase}"')
        return
    _ensure_worker()
    if priority and not is_speaking():
        _drain_queue()
    _queue.put(phrase)
    voice_log(f'TTS queued: "{phrase}"')


def warmup_engine(timeout: float = 8.0) -> bool:
    """Block until the worker thread has initialized SAPI."""
    _ensure_worker()
    return _engine_ready.wait(timeout=max(0.5, timeout))


def test_speak(phrase: str = "Satpuda voice test") -> bool:
    """Synchronous test for settings — speaks on the calling thread via SAPI."""
    text = (phrase or "").strip()
    if not text:
        return False
    if sys.platform == "win32":
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                voice.Volume = TTS_VOLUME
                _pick_sapi_voice(voice)
                voice.Speak(text, _SAPI_SYNC)
                return True
            finally:
                pythoncom.CoUninitialize()
        except Exception as exc:
            voice_log(f"TTS test failed: {exc}", level="error")
    speak(text, force=True)
    wait_until_idle(timeout=6.0)
    return _engine_ready.is_set()


def list_tts_voices() -> list[str]:
    names = []
    if sys.platform == "win32":
        try:
            import win32com.client
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voices = voice.GetVoices()
            for idx in range(int(voices.Count)):
                names.append(voices.Item(idx).GetDescription())
        except Exception:
            pass
    if not names:
        try:
            import pyttsx3
            engine = pyttsx3.init(driverName="sapi5") if sys.platform == "win32" else pyttsx3.init()
            names = [getattr(v, "name", "") for v in (engine.getProperty("voices") or [])]
            try:
                engine.stop()
            except Exception:
                pass
        except Exception:
            pass
    return [n for n in names if n]


def _command_part_from_recognized(text: str) -> str:
    from core.voice.command_parser import parse_voice_command
    from core.voice.text_normalize import normalize_transcript

    raw = (text or "").strip()
    if not raw:
        return ""
    result = parse_voice_command(raw)
    if result.wake_found and (result.command_text or "").strip():
        return result.command_text.strip()
    return normalize_transcript(raw) or raw.lower()


def speak_command_feedback(
    *,
    kind: str,
    recognized: str = "",
    command: str = "",
    screen_label: str = "",
    screen_id: str = "",
    action_id: str = "",
) -> None:
    from core.voice.tts_phrases import pick_success_phrase, pick_voice_action_phrase

    if kind == "action" and action_id:
        speak(pick_voice_action_phrase(action_id), priority=True)
        return
    if kind == "nav":
        cmd = command or _command_part_from_recognized(recognized)
        speak(pick_success_phrase(screen_label, recognized, cmd, screen_id=screen_id), priority=True)


def phrase_for_navigation(
    action: str,
    detail: str = "",
    recognized: str = "",
) -> str | None:
    from core.voice.assistant_config import get_assistant_display_name, load_voice_language
    from core.voice.tts_phrases import pick_error_phrase

    action = (action or "").strip()
    detail = (detail or "").strip()

    if action.startswith("Opened ") or action.startswith("Action "):
        return None

    if action in ("Failed", "No action"):
        return pick_error_phrase(
            detail,
            wake_name=get_assistant_display_name(),
            lang=load_voice_language(),
        )

    return None


def speak_navigation_feedback(action: str, detail: str = "", recognized: str = "") -> None:
    global _last_error_tts_at
    phrase = phrase_for_navigation(action, detail, recognized)
    if not phrase:
        return
    now = time.monotonic()
    with _error_tts_lock:
        if now - _last_error_tts_at < _ERROR_TTS_COOLDOWN_SECONDS:
            voice_log(f"Error TTS suppressed (cooldown): {phrase[:50]}")
            return
        _last_error_tts_at = now
    speak(phrase)


def speak_action_result(message: str) -> None:
    msg = (message or "").strip()
    if not msg:
        return
    speak(msg)
