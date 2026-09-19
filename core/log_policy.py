"""Disable file logging for release / lite builds."""
from __future__ import annotations


def should_write_logs() -> bool:
    try:
        from core.build_features import is_no_log_build
        return not is_no_log_build()
    except Exception:
        return True


def configure_silent_logs() -> None:
    try:
        import logging
        if not should_write_logs():
            logging.disable(logging.CRITICAL)
    except Exception:
        pass
    try:
        import core.keyboard_registry as kb
        kb.DEBUG_KEYBOARD = False
        if not should_write_logs():
            kb.SHORTCUT_LOG_ALWAYS = False
        elif kb.SHORTCUT_LOG_ALWAYS:
            # Honour keyboard_debug.txt when present
            kb.DEBUG_KEYBOARD = kb._load_keyboard_debug()
    except Exception:
        pass
