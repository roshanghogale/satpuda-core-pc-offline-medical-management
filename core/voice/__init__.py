"""Satpuda voice assistant package."""

from core.voice.assistant import SatpudaVoiceAssistant
from core.voice.assistant_config import (
    DEFAULT_ASSISTANT_NAME,
    get_assistant_display_name,
    load_assistant_name,
)
from core.voice.command_parser import (
    ASSISTANT_NAME,
    get_assistant_name,
    parse_voice_command,
    reload_wake_config,
)
from core.voice.screen_registry import SCREEN_ENTRIES

__all__ = [
    "ASSISTANT_NAME",
    "DEFAULT_ASSISTANT_NAME",
    "SCREEN_ENTRIES",
    "SatpudaVoiceAssistant",
    "get_assistant_display_name",
    "get_assistant_name",
    "load_assistant_name",
    "parse_voice_command",
    "reload_wake_config",
]
