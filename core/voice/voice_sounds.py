"""Short UI sounds for Satpuda voice feedback."""

import sys
import threading


def _play_beep(freq: int, duration_ms: int):
    def _run():
        try:
            if sys.platform == "win32":
                import winsound
                winsound.Beep(freq, duration_ms)
            else:
                print("\a", end="", flush=True)
        except Exception:
            try:
                print("\a", end="", flush=True)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


def play_ack_sound():
    """Instant acknowledgment when a command is accepted (non-blocking)."""
    _play_beep(1046, 70)


def play_error_sound():
    """Play a short error beep (non-blocking)."""
    def _beep():
        try:
            if sys.platform == "win32":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONHAND)
            else:
                print("\a", end="", flush=True)
        except Exception:
            try:
                print("\a", end="", flush=True)
            except Exception:
                pass

    threading.Thread(target=_beep, daemon=True).start()
