"""The voice pack: downloaded only by a store that wants voice, never part of the app.

The app stays its normal size. The voice pack -- a portable Python, the voice service and the
IndicConformer model, about 1.6 GB -- is published as one zip next to a small manifest:

    MANIFEST_URL -> {"version": "2026.09.27", "url": ".../SatpudaVoicePack.zip",
                     "sha256": "...", "size": 1634000000}

From the voice bar the owner presses "Download voice"; this module then

  1. reads the manifest,
  2. downloads the zip into PACK_HOME (resuming a broken download),
  3. checks its SHA-256,
  4. unzips it next to the current pack and swaps it in -- the store's learned spellings,
     saved word list and settings carry over from the old pack,
  5. starts the voice service (python\\pythonw.exe voice\\voice_service.py) from it.

The service is a child of this engine: it is told the engine's PID and quits by itself when the
engine is gone, so closing the app never leaves 3 GB of RAM behind.

HTTP (core.desktop_api): GET /api/voice/pack, POST /api/voice/pack/{install,cancel,remove,start,check}.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from typing import Any, Optional

MANIFEST_URL = os.environ.get(
    "SATPUDA_VOICE_MANIFEST",
    "https://github.com/roshanghogale/exes-for-satpuda-core/releases/download/voice-pack/voice-pack.json")
SERVICE_URL = "http://127.0.0.1:47811/health"
TOP = "SatpudaVoicePack"                 # the folder inside the zip
# the store's own files, carried from the old pack into the new one
KEEP = (os.path.join("voice", "sarav", "learned_aliases.json"), os.path.join("voice", "vocab_cache.json"),
        os.path.join("voice", "config.json"))
NEED_FREE_GB = 6.0                       # zip + unpacked + the model prepared on first start

_lock = threading.Lock()
_state: dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "error": "", "available": None}
_cancel = threading.Event()
_worker: Optional[threading.Thread] = None
_service: Optional[subprocess.Popen] = None


def pack_home() -> str:
    base = os.environ.get("SATPUDA_VOICE_PACK_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SatpudaCore", "voice")
    os.makedirs(base, exist_ok=True)
    return base


def _current() -> str:
    return os.path.join(pack_home(), "current")


def installed() -> Optional[dict]:
    """{"version": ...} of the pack in use, or None."""
    marker = os.path.join(_current(), "pack.json")
    if not os.path.isfile(marker) or not os.path.isfile(os.path.join(_current(), "python", "pythonw.exe")):
        return None
    try:
        with open(marker, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"version": "?"}


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def service_running() -> bool:
    try:
        with urllib.request.urlopen(SERVICE_URL, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def status() -> dict:
    with _lock:
        s = dict(_state)
    inst = installed()
    try:
        free = shutil.disk_usage(pack_home()).free / 2**30
    except Exception:
        free = None
    s.update({"installed": bool(inst), "version": (inst or {}).get("version"), "running": service_running(),
              "path": pack_home(), "free_gb": round(free, 1) if free is not None else None,
              "need_gb": NEED_FREE_GB})
    return s


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "SatpudaCore"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def check() -> dict:
    """Read the manifest: what version is on offer, and how big."""
    try:
        m = _get_json(MANIFEST_URL)
        _set(available={k: m.get(k) for k in ("version", "size", "notes")}, error="")
    except Exception as exc:
        _set(error=f"Voice pack chi mahiti milali nahi: {exc}")
    return status()


def _download(url: str, dest: str, size: int) -> None:
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    if size and have > size:
        os.remove(dest)
        have = 0
    if size and have == size:
        return
    headers = {"User-Agent": "SatpudaCore"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:           # the server would not resume: start over
            have = 0
        mode = "ab" if have else "wb"
        _set(state="downloading", done=have, total=size or int(r.headers.get("Content-Length") or 0) + have)
        with open(dest, mode) as out:
            while True:
                if _cancel.is_set():
                    raise InterruptedError("cancelled")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                have += len(chunk)
                _set(done=have)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 22)
            if not b:
                break
            h.update(b)
            done += len(b)
            _set(done=done)
            if _cancel.is_set():
                raise InterruptedError("cancelled")
    return h.hexdigest()


def _merge_aliases(old: str, new: str) -> None:
    """The store's learned spellings win; the pack's shipped ones fill in the rest."""
    try:
        with open(old, encoding="utf-8") as fh:
            mine = json.load(fh)
        with open(new, encoding="utf-8") as fh:
            shipped = json.load(fh)
        for k, v in mine.items():
            if isinstance(v, dict):
                shipped.setdefault(k, {}).update(v)
        with open(new, "w", encoding="utf-8") as fh:
            json.dump(shipped, fh, ensure_ascii=False, indent=1)
    except Exception:
        shutil.copy2(old, new)


def _install_worker() -> None:
    home = pack_home()
    try:
        _set(state="checking", error="", done=0, total=0)
        m = _get_json(MANIFEST_URL)
        size, url, want = int(m.get("size") or 0), m["url"], str(m.get("sha256") or "").lower()
        free = shutil.disk_usage(home).free / 2**30
        if free < NEED_FREE_GB:
            raise RuntimeError(f"Disk var jaga kami: {free:.1f} GB mokli, {NEED_FREE_GB:.0f} GB lagel")
        zip_path = os.path.join(home, "SatpudaVoicePack.zip")
        _download(url, zip_path, size)
        if want:
            _set(state="verifying", done=0, total=os.path.getsize(zip_path))
            got = _sha256(zip_path)
            if got != want:
                os.remove(zip_path)
                raise RuntimeError("Download kharab zala (SHA-256 julat nahi) -- punha download kara")
        _set(state="extracting", done=0, total=0)
        staging = os.path.join(home, "staging")
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as z:
            members = z.infolist()
            _set(total=len(members))
            for n, info in enumerate(members, 1):
                if _cancel.is_set():
                    raise InterruptedError("cancelled")
                z.extract(info, staging)
                if n % 200 == 0:
                    _set(done=n)
        new = os.path.join(staging, TOP)
        if not os.path.isfile(os.path.join(new, "python", "pythonw.exe")):
            raise RuntimeError("Voice pack adhura aahe (python sapadla nahi)")
        cur = _current()
        if os.path.isdir(cur):
            for rel in KEEP:
                src, dst = os.path.join(cur, rel), os.path.join(new, rel)
                if os.path.isfile(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if rel.endswith("learned_aliases.json") and os.path.isfile(dst):
                        _merge_aliases(src, dst)
                    else:
                        shutil.copy2(src, dst)
            stop_service()
            old = os.path.join(home, "old")
            shutil.rmtree(old, ignore_errors=True)
            os.replace(cur, old)
            shutil.rmtree(old, ignore_errors=True)
        with open(os.path.join(new, "pack.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": m.get("version"), "installed_at": time.strftime("%Y-%m-%d %H:%M")}, fh)
        os.replace(new, cur)
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.remove(zip_path)
        except OSError:
            pass
        _set(state="starting", done=0, total=0)
        start_service()
        _set(state="ready")
    except InterruptedError:
        _set(state="idle", error="Download thambavla")
    except Exception as exc:
        _set(state="error", error=str(exc))


def install() -> dict:
    global _worker
    with _lock:
        busy = _worker is not None and _worker.is_alive()
    if not busy:
        _cancel.clear()
        _worker = threading.Thread(target=_install_worker, daemon=True, name="VoicePack")
        _worker.start()
    return status()


def cancel() -> dict:
    _cancel.set()
    return status()


_start_lock = threading.Lock()


def start_service() -> dict:
    """Start the voice service from the installed pack, unless one is already answering.

    Or already STARTING: the engine's autostart and the voice bar's own "start" came 4 s
    apart, before the first service had loaded its model and opened its port, so two
    services ran at 2.7 GB each (2 Oct 2026). The one this engine started is waited for.
    """
    global _service
    with _start_lock:
        if _service is not None and _service.poll() is None:
            return status()
        if service_running():
            return status()
        return _spawn_service()


def _spawn_service() -> dict:
    global _service
    cur = _current()
    pyw = os.path.join(cur, "python", "pythonw.exe")
    script = os.path.join(cur, "voice", "voice_service.py")
    if not (os.path.isfile(pyw) and os.path.isfile(script)):
        _set(error="Voice pack install nahi")
        return status()
    env = dict(os.environ, PYTHONNOUSERSITE="1", SATPUDA_PARENT_PID=str(os.getpid()))
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _service = subprocess.Popen([pyw, script], cwd=cur, env=env, creationflags=flags,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return status()


def stop_service() -> None:
    global _service
    if _service is not None and _service.poll() is None:
        try:
            _service.terminate()
            _service.wait(timeout=5)
        except Exception:
            pass
    _service = None


def remove() -> dict:
    """Uninstall the voice pack (asked for by the owner): stop the service, delete the folder."""
    cancel()
    stop_service()
    shutil.rmtree(_current(), ignore_errors=True)
    shutil.rmtree(os.path.join(pack_home(), "staging"), ignore_errors=True)
    _set(state="idle", error="", done=0, total=0)
    return status()


def autostart() -> None:
    """At engine start: if the pack is installed and the store has voice on, start the service."""
    def go() -> None:
        try:
            from core.voice_switch import voice_status

            if installed() and voice_status().get("enabled"):
                start_service()
        except Exception:
            pass
    threading.Thread(target=go, daemon=True, name="VoiceAutostart").start()
