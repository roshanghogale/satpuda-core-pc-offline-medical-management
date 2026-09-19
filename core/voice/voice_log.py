"""Terminal logging for Satpuda voice assistant."""

import sys
from datetime import datetime

_PREFIX = "[Satpuda]"


def _logging_enabled() -> bool:
    try:
        from core.log_policy import should_write_logs
        if not should_write_logs():
            return False
    except Exception:
        pass
    try:
        from core.voice.assistant_config import load_voice_assistant_enabled
        return load_voice_assistant_enabled()
    except Exception:
        return False


def voice_log(message: str, *, level: str = "info"):
    if not _logging_enabled():
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"{_PREFIX} [{stamp}] {message}"
    try:
        print(line, flush=True)
    except Exception:
        try:
            sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass
