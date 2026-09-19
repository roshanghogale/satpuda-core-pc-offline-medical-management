"""Satpuda voice assistant tuning — responsive: accurate + low latency."""
from __future__ import annotations

import os
import sys

from core.voice.marathi_prefixes import MR_COMMAND_PREFIXES

# Profiles: responsive (default) | fast | balanced | accuracy
RECOGNITION_PROFILE = "responsive"

_PROFILES = {
    "responsive": {
        "model_size": "base",
        "beam_size": 2,
        "use_vad": True,
        "rerank_prompts": True,
        "rerank_max_passes": 2,
        "compact_prompt": False,
        "compute_type": "int8",
        "prompt_max_chars": 300,
        "min_avg_logprob": -1.05,
        "skip_rerank_logprob": -0.55,
        "skip_rerank_score": 9000,
        "rerank_replace_margin": 3500,
    },
    "fast": {
        "model_size": "base",
        "beam_size": 1,
        "use_vad": True,
        "rerank_prompts": False,
        "rerank_max_passes": 1,
        "compact_prompt": True,
        "compute_type": "int8",
        "prompt_max_chars": 220,
        "min_avg_logprob": None,
        "skip_rerank_logprob": -0.65,
        "skip_rerank_score": 4000,
    },
    "balanced": {
        "model_size": "base",
        "beam_size": 3,
        "use_vad": True,
        "rerank_prompts": True,
        "rerank_max_passes": 3,
        "compact_prompt": False,
        "compute_type": "int8_float16",
        "prompt_max_chars": 448,
        "min_avg_logprob": -1.35,
        "skip_rerank_logprob": -0.78,
        "skip_rerank_score": 5000,
    },
    "accuracy": {
        "model_size": "small",
        "beam_size": 5,
        "use_vad": True,
        "rerank_prompts": True,
        "rerank_max_passes": 3,
        "compact_prompt": False,
        "compute_type": "int8_float16",
        "prompt_max_chars": 448,
        "min_avg_logprob": -1.25,
        "skip_rerank_logprob": -0.85,
        "skip_rerank_score": 5000,
    },
}

MODEL_SIZE = _PROFILES[RECOGNITION_PROFILE]["model_size"]
WHISPER_LANGUAGE = "en"
WHISPER_BEAM_SIZE = _PROFILES[RECOGNITION_PROFILE]["beam_size"]
# Frozen EXE: cap threads so Whisper preload does not starve the Tk UI.
if getattr(sys, "frozen", False):
    WHISPER_CPU_THREADS = min(4, os.cpu_count() or 2)
else:
    WHISPER_CPU_THREADS = min(8, os.cpu_count() or 4)
USE_VAD_FILTER = _PROFILES[RECOGNITION_PROFILE]["use_vad"]
AUDIO_CHUNK_SECONDS = 0.04

# Lower threshold = picks up quiet speech sooner; smaller chunks = faster reaction.
MIC_SILENCE_THRESHOLD = 0.0045
MIC_MAX_SECONDS = 5.0
MIC_SILENCE_SECONDS = 0.22
MIC_MIN_SECONDS = 0.12
MIC_MIN_SPEECH_ENERGY = 0.006
AUDIO_GAIN_TARGET = 0.24

FULL_LISTEN_MAX_SECONDS = 5.0
FULL_LISTEN_SILENCE_SECONDS = 0.22
LISTEN_IDLE_TIMEOUT_SECONDS = 2.6

# Active command session: end phrase quickly after you stop speaking.
ACTIVE_LISTEN_SILENCE_SECONDS = 0.18
ACTIVE_LISTEN_IDLE_TIMEOUT_SECONDS = 3.0
# Standby (Hey Satpuda): slightly longer tail for wake phrases.
STANDBY_LISTEN_SILENCE_SECONDS = 0.28
STANDBY_LISTEN_IDLE_TIMEOUT_SECONDS = 2.2

# Second listen when user pauses after "open" / "go to" before the screen name
COMMAND_COMPLETION_MAX_SECONDS = 3.5
COMMAND_COMPLETION_SILENCE_SECONDS = 0.34

# Don't block the mic too long waiting for TTS to finish.
TTS_WAIT_ACTIVE_SECONDS = 1.1
TTS_WAIT_STANDBY_SECONDS = 3.0

# Legacy names (unused — single-pass listen only)
WAKE_IDLE_TIMEOUT_SECONDS = LISTEN_IDLE_TIMEOUT_SECONDS
COMMAND_LISTEN_MAX_SECONDS = FULL_LISTEN_MAX_SECONDS
COMMAND_SILENCE_SECONDS = FULL_LISTEN_SILENCE_SECONDS

_COMMAND_HINTS = (
    "satpuda open home sales purchase inventory settings returns",
    "customer payment supplier payment customer ledger supplier ledger ledger",
    "sales history purchase history suppliers customers billing payment",
    "open show go to switch navigate launch",
    "stop listening close app quit app turn off mic",
)

_MR_HINTS = (
    " ".join(MR_COMMAND_PREFIXES),
    "Purchase Sales Inventory Settings Home Supplier Ledger Customer Payment",
    "Sales History Purchase History Sales Return Purchase Return",
)


def get_whisper_language() -> str:
    """Always transcribe in English so the wake word Satpuda stays Latin text.

    Marathi mode only changes command parsing (Marathi action words), not Whisper.
    Using language=mr makes Whisper rewrite Satpuda as Devanagari gibberish.
    """
    return "en"


def get_recognition_profile() -> dict:
    """Active Whisper decode profile (see RECOGNITION_PROFILE)."""
    return dict(_PROFILES.get(RECOGNITION_PROFILE, _PROFILES["responsive"]))


def _build_vocabulary_prompt() -> str:
    """Screen names + aliases so Whisper biases toward in-app vocabulary."""
    try:
        from core.voice.screen_registry import SCREEN_ENTRIES
    except Exception:
        return ""
    words = []
    seen = set()
    for entry in SCREEN_ENTRIES:
        for alias in entry.get("aliases", ()):
            norm = " ".join(str(alias).lower().split())
            if norm and norm not in seen:
                seen.add(norm)
                words.append(norm)
        label = str(entry.get("label", "")).lower().strip()
        if label and label not in seen:
            seen.add(label)
            words.append(label)
    return " ".join(words[:80])


def _compact_vocabulary_prompt() -> str:
    """Short bias list for low-latency decode."""
    return (
        "home sales purchase inventory settings returns billing "
        "sales history purchase history customer payment supplier payment "
        "customer ledger supplier ledger suppliers customers "
        "check for updates download the update open installer reinstall installer "
        "download install backup now sync from drive sink from drive "
        "data and system alert and monitoring sales and billing reorder "
        "restore from drive download backup app updates "
        "stop listening close app quit app turn off mic"
    )


def get_whisper_initial_prompt() -> str:
    from core.voice.assistant_config import load_voice_language
    from core.voice.wake_matcher import build_wake_whisper_prompt

    profile = get_recognition_profile()
    wake = build_wake_whisper_prompt()
    vocab = _compact_vocabulary_prompt() if profile.get("compact_prompt") else _build_vocabulary_prompt()
    if load_voice_language() == "mr":
        roman_actions = (
            "ugaad ughad uger dakhau dakhav dakho warda warja "
            "purchase ugaad sales dakhau open purchase sales inventory"
        )
        if profile.get("compact_prompt"):
            return f"{wake} {roman_actions} {vocab}"
        return f"{wake} {roman_actions} {vocab} {' '.join(_COMMAND_HINTS)}"
    if profile.get("compact_prompt"):
        return f"{wake} open {vocab}"
    return f"{wake} {vocab} {' '.join(_COMMAND_HINTS)}"


WHISPER_INITIAL_PROMPT = get_whisper_initial_prompt()


def get_wake_listen_prompt() -> str:
    from core.voice.wake_matcher import build_wake_whisper_prompt
    return build_wake_whisper_prompt()


def get_command_listen_prompt() -> str:
    return " ".join(_COMMAND_HINTS)
