"""Is the voice assistant switched on for this store? -- GET /api/voice/enabled.

The owner flips a per-store "Voice assistant" switch (and a level cap) in the
server's admin panel. The server sends it inside the plain licence
(``voice_enabled``, ``voice_tier``); ``core.license_manager`` keeps the last
answer in memory and in voice.dat. This module turns that into one answer:

    {"enabled": bool, "tier": "auto"|"1"|"2"|"3",
     "source": "override"|"server"|"cache"|"default"}

in this order:

  override  voice/voice_override.json exists and says {"enabled": true|false}
            (optional "tier"). For the TEST COPY only, so the owner can try voice
            before the server change is deployed. Delete the file to follow the
            admin panel again.
  server    the server answered in this engine process within LIVE_FOR_S.
  cache     the last answer saved on this PC (offline, or server not asked yet).
  default   never heard from the server: OFF.

The endpoint must answer fast (the desktop waits 5 s, the voice service 2 s), so
it never waits on the network for long: a refresh runs in a background thread at
most once per REFRESH_EVERY_S, and only the very first request of a process
waits up to FIRST_WAIT_S for it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

from core import license_manager as lm

OVERRIDE_ENV = "SATPUDA_VOICE_OVERRIDE"
LIVE_FOR_S = 10 * 60
REFRESH_EVERY_S = 60
FIRST_WAIT_S = 1.0

_lock = threading.Lock()
_last_attempt = 0.0
_worker: threading.Thread | None = None


def override_paths() -> list[str]:
    """Where the test copy's override file may be. The env var wins (tests)."""
    env = os.environ.get(OVERRIDE_ENV, "").strip()
    if env:
        return [env]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = [os.path.join(root, "voice", "voice_override.json")]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        paths.append(os.path.join(exe_dir, "voice", "voice_override.json"))
    return paths


def read_override() -> dict | None:
    """{enabled, tier} from the override file, or None when there is none (or it
    is not readable JSON with a true/false "enabled")."""
    for path in override_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("enabled"), bool):
            return {"enabled": data["enabled"], "tier": lm.normalize_voice_tier(data.get("tier"))}
    return None


def _refresh() -> None:
    try:
        lm.fetch_server_license()  # records the voice switch as a side effect
    except Exception:
        pass


def _maybe_refresh(*, now: float | None = None) -> None:
    """Start a background licence read if the last one is old enough. The first
    one of a process is waited for briefly so a fresh start answers "server"."""
    global _last_attempt, _worker
    now = time.time() if now is None else now
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        if now - _last_attempt < REFRESH_EVERY_S:
            return
        first = _last_attempt == 0.0
        _last_attempt = now
        _worker = threading.Thread(target=_refresh, name="voice-switch-refresh", daemon=True)
        _worker.start()
        worker = _worker
    if first:
        worker.join(FIRST_WAIT_S)


def voice_status(*, refresh: bool = True) -> dict[str, Any]:
    """The answer for GET /api/voice/enabled. Never raises."""
    try:
        ov = read_override()
        if ov is not None:
            return {**ov, "source": "override"}
        if refresh:
            _maybe_refresh()
        live = lm.live_server_voice(LIVE_FOR_S)
        if live is not None:
            return {**live, "source": "server"}
        cached = lm.read_cached_voice()
        if cached is not None:
            return {"enabled": cached["enabled"], "tier": cached["tier"], "source": "cache"}
    except Exception:
        pass
    return {"enabled": False, "tier": "auto", "source": "default"}
