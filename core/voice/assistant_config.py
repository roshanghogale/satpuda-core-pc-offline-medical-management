"""Voice assistant wake word — stored locally, editable in Administrator settings."""
from __future__ import annotations

import os
import re
import sys

DEFAULT_ASSISTANT_NAME = "satpuda"
DEFAULT_VOICE_LANGUAGE = "en"
VALID_VOICE_LANGUAGES = frozenset({"en", "mr"})
_MIN_LEN = 2
_MAX_LEN = 16
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z\s\-']{0,15}$")


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
        )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "voice_assistant_name.txt")


def normalize_assistant_name(name: str) -> str:
    if not name:
        return ""
    cleaned = re.sub(r"\s+", " ", name.strip().lower())
    return cleaned[:_MAX_LEN].strip()


def validate_assistant_name(name: str) -> tuple[bool, str]:
    cleaned = normalize_assistant_name(name)
    if not cleaned:
        return False, "Assistant name cannot be empty."
    if len(cleaned) < _MIN_LEN:
        return False, f"Use at least {_MIN_LEN} letters."
    if len(cleaned) > _MAX_LEN:
        return False, f"Use at most {_MAX_LEN} characters."
    if not _NAME_RE.match(cleaned):
        return False, "Use English letters only (a–z). Spaces allowed."
    return True, cleaned


def load_assistant_name() -> str:
    path = _config_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip()
            ok, value = validate_assistant_name(raw)
            if ok:
                if value in ("vira", "vera"):
                    value = DEFAULT_ASSISTANT_NAME
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(value)
                    except Exception:
                        pass
                return value
    except Exception:
        pass
    return DEFAULT_ASSISTANT_NAME


def save_assistant_name(name: str) -> tuple[bool, str]:
    ok, value = validate_assistant_name(name)
    if not ok:
        return False, value
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            f.write(value)
        return True, value
    except Exception as exc:
        return False, f"Could not save: {exc}"


def get_assistant_display_name() -> str:
    name = load_assistant_name()
    return name[:1].upper() + name[1:] if name else DEFAULT_ASSISTANT_NAME.capitalize()


def _language_path() -> str:
    return os.path.join(os.path.dirname(_config_path()), "voice_language.txt")


def load_voice_language() -> str:
    path = _language_path()
    try:
        if os.path.exists(path):
            lang = open(path, encoding="utf-8").read().strip().lower()
            if lang in VALID_VOICE_LANGUAGES:
                return lang
    except Exception:
        pass
    return DEFAULT_VOICE_LANGUAGE


def save_voice_language(language: str) -> tuple[bool, str]:
    lang = (language or "").strip().lower()
    if lang not in VALID_VOICE_LANGUAGES:
        return False, "Choose English or Marathi."
    try:
        with open(_language_path(), "w", encoding="utf-8") as f:
            f.write(lang)
        return True, lang
    except Exception as exc:
        return False, f"Could not save: {exc}"


def get_voice_language_label(language: str | None = None) -> str:
    lang = language or load_voice_language()
    return {"en": "English", "mr": "Marathi"}.get(lang, "English")


def get_voice_listening_example() -> str:
    """Short example phrase for mic overlay / dialog hints."""
    name = get_assistant_display_name()
    if load_voice_language() == "mr":
        return f'{name} Purchase ugaad'
    return f'{name} open home'


def _tts_enabled_path() -> str:
    return os.path.join(os.path.dirname(_config_path()), "voice_tts_enabled.txt")


def load_voice_tts_enabled() -> bool:
    path = _tts_enabled_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip().lower()
            return raw not in ("0", "false", "no", "off")
    except Exception:
        pass
    return True


def save_voice_tts_enabled(enabled: bool) -> tuple[bool, str]:
    try:
        with open(_tts_enabled_path(), "w", encoding="utf-8") as f:
            f.write("1" if enabled else "0")
        return True, "1" if enabled else "0"
    except Exception as exc:
        return False, f"Could not save: {exc}"


def _wake_only_path() -> str:
    return os.path.join(os.path.dirname(_config_path()), "voice_wake_only.txt")


def load_voice_wake_only() -> bool:
    """When True, turning the mic on starts in standby until Hey Satpuda."""
    path = _wake_only_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip().lower()
            return raw in ("1", "true", "yes", "on")
    except Exception:
        pass
    return False


def save_voice_wake_only(enabled: bool) -> tuple[bool, str]:
    try:
        with open(_wake_only_path(), "w", encoding="utf-8") as f:
            f.write("1" if enabled else "0")
        return True, "1" if enabled else "0"
    except Exception as exc:
        return False, f"Could not save: {exc}"


def _auto_start_mic_path() -> str:
    return os.path.join(os.path.dirname(_config_path()), "voice_auto_start_mic.txt")


def load_voice_auto_start_mic() -> bool:
    """Start mic in standby when app opens so Hey Satpuda works without the UI button."""
    path = _auto_start_mic_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip().lower()
            return raw in ("1", "true", "yes", "on")
    except Exception:
        pass
    return False


def save_voice_auto_start_mic(enabled: bool) -> tuple[bool, str]:
    try:
        with open(_auto_start_mic_path(), "w", encoding="utf-8") as f:
            f.write("1" if enabled else "0")
        return True, "1" if enabled else "0"
    except Exception as exc:
        return False, f"Could not save: {exc}"


def _assistant_enabled_path() -> str:
    return os.path.join(os.path.dirname(_config_path()), "voice_assistant_enabled.txt")


def load_voice_assistant_enabled() -> bool:
    """Master switch for Satpuda voice assistant (mic, TTS preload, nav button)."""
    from core.build_features import is_voice_supported

    if not is_voice_supported():
        return False
    path = _assistant_enabled_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip().lower()
            return raw in ("1", "true", "yes", "on")
    except Exception:
        pass
    return True


def save_voice_assistant_enabled(enabled: bool) -> tuple[bool, str]:
    try:
        with open(_assistant_enabled_path(), "w", encoding="utf-8") as f:
            f.write("1" if enabled else "0")
        return True, "1" if enabled else "0"
    except Exception as exc:
        return False, f"Could not save: {exc}"
