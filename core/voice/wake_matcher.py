"""Wake-word accent / Whisper mis-hearing fixes per assistant name."""
from __future__ import annotations

from core.voice.assistant_config import DEFAULT_ASSISTANT_NAME, load_assistant_name

WAKE_MISHEARING_MAP: dict[str, tuple[str, ...]] = {
    "vira": (
        "vera",
        "veera",
        "veeraa",
        "viraa",
        "verra",
        "wira",
        "waira",
        "vaira",
        "wera",
        "weera",
        "bera",
        "bhira",
        "fera",
        "vere",
        "very",
        "vary",
        "veer",
        "hey vira",
        "ok vira",
    ),
    "vera": (
        "veera",
        "vira",
        "viraa",
        "wira",
        "wera",
        "weera",
        "very",
        "vary",
    ),
    "core": ("kor", "coor", "kore", "corey"),
    "pulse": ("puls", "pauls", "pulss"),
    "clerk": ("clark", "clerc"),
    "satpuda": (
        "sat puda",
        "satpudha",
        "satpura",
        "sapuda",
        "sutpuda",
        "setpuda",
        "soft puda",
        "shut puda",
        "satt puda",
        "sat pooda",
        "hey satpuda",
        "ok satpuda",
    ),
}


def get_wake_mishearings(name: str | None = None) -> tuple[str, ...]:
    n = (name or load_assistant_name() or DEFAULT_ASSISTANT_NAME).strip().lower()
    return WAKE_MISHEARING_MAP.get(n, ())


def build_wake_whisper_prompt(name: str | None = None) -> str:
    """Short prompt so Whisper prefers the wake word spellings."""
    from core.voice.command_parser import get_wake_aliases

    wake = (name or load_assistant_name() or DEFAULT_ASSISTANT_NAME).strip().lower()
    aliases = list(get_wake_aliases())
    mis = list(get_wake_mishearings(wake))
    lead = [wake] + mis[:16] + aliases[:6]
    seen = []
    for item in lead:
        key = item.lower().strip()
        if key and key not in seen:
            seen.append(key)
    return " ".join(seen[:24])
