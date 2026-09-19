"""Standby activation and mic-command phrase normalization."""

from __future__ import annotations

import re

from core.voice.text_normalize import normalize_transcript


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


_MIC_MISHEAR_FIXES = (
    (r"\bfrom listening\b", "stop listening"),
    (r"\bfor listening\b", "stop listening"),
    (r"\bform listening\b", "stop listening"),
    (r"\bstop listings?\b", "stop listening"),
    (r"\bstop listing\b", "stop listening"),
    (r"\btop listening\b", "stop listening"),
    (r"\bstock listening\b", "stop listening"),
    (r"\bpaused? listening\b", "pause listening"),
    (r"\bstart listings?\b", "start listening"),
    (r"\bnose app\b", "close app"),
    (r"\bclosed app\b", "close app"),
    (r"\bclose up app\b", "close app"),
    (r"\bplaudred\b", "close app"),
    (r"\bplod app\b", "close app"),
    (r"\bquit up\b", "quit app"),
    (r"\bclosed\b", "close"),
    (r"\bcloses\b", "close"),
)


def normalize_mic_command(text: str) -> str:
    """Fix common Whisper mis-hearings for stop/start listening and close."""
    t = _norm(normalize_transcript(text or "") or (text or ""))
    if not t:
        return ""
    for pattern, repl in _MIC_MISHEAR_FIXES:
        t = re.sub(pattern, repl, t)
    if t in ("stop", "pause", "halt", "quiet"):
        return "stop listening"
    if re.fullmatch(r"stop\s+\w+", t) and "listening" not in t:
        # "stop satpuda", "stop now" → stop listening
        return "stop listening"
    return t


def match_active_mic_action(text: str) -> dict | None:
    """Match stop/turn-off mic phrases while actively listening (no wake word)."""
    from core.voice.page_action_registry import match_page_voice_action

    norm = normalize_mic_command(text)
    if not norm:
        return None
    for candidate in (norm, _norm(text)):
        action, _params = match_page_voice_action(candidate, exact_only=True)
        if action is not None and action.get("id") in (
            "stop_listening", "turn_off_mic", "close_app",
        ):
            return action
    return None


def _activate_leads(name: str) -> tuple[str, ...]:
    n = _norm(name)
    return (
        f"hey {n}",
        f"hi {n}",
        f"ok {n}",
        f"okay {n}",
        n,
        f"turn on {n}",
        f"enable {n}",
        f"start {n}",
        f"wake up {n}",
        f"wake {n}",
        f"{n} on",
        f"{n} wake up",
        f"{n} wake",
    )


def parse_standby_wake(text: str) -> tuple[bool, str]:
    """
    In standby mode, detect wake-to-listen phrases.
    Returns (activated, command_remainder). Remainder may be empty (wake only).
    """
    from core.voice.command_parser import get_assistant_name, get_wake_aliases

    raw = (text or "").strip()
    if not raw:
        return False, ""

    t = normalize_mic_command(raw)
    if not t:
        return False, ""

    candidates = list(_activate_leads(get_assistant_name()))
    for alias in get_wake_aliases():
        a = _norm(alias)
        if a and a not in candidates:
            candidates.append(a)
            candidates.append(f"hey {a}")

    candidates.sort(key=len, reverse=True)
    for lead in candidates:
        if t == lead:
            return True, ""
        if t.startswith(lead + " "):
            rest = t[len(lead) :].strip()
            return True, rest

    return False, ""


def is_turn_off_phrase(text: str) -> bool:
    from core.voice.command_parser import get_assistant_name

    t = normalize_mic_command(text)
    name = _norm(get_assistant_name())
    patterns = (
        f"turn off {name}",
        f"turn {name} off",
        f"{name} off",
        f"stop {name}",
        f"disable {name}",
        f"sleep {name}",
        f"mute {name}",
        "turn off satpuda",
        "satpuda off",
        "stop satpuda",
        "turn off mic",
        "mic off",
    )
    return t in patterns
