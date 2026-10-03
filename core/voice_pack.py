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
import sys
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
PARTS = 4                                # connections for one download (2 Oct: 1.5 MB/s on one, 2.8 on four)
LOCAL_NAME = "satpudavoicepack"          # SatpudaVoicePack.zip, "SatpudaVoicePack (1).zip", ...

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
    local = ""
    if s.get("state") in ("idle", "error", "ready"):
        try:
            local = find_local_pack(int((s.get("available") or {}).get("size") or 0))
        except Exception:
            local = ""
    s.update({"installed": bool(inst), "version": (inst or {}).get("version"), "running": service_running(),
              "local": local,
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


def _download_parallel(url: str, dest: str, size: int) -> None:
    """The pack in PARTS pieces at once, each resumable; one connection when the size is unknown.

    GitHub's CDN gave one connection 1.5 MB/s on the owner's line and four 2.8 MB/s
    (2 Oct 2026): 16 minutes became 8.5. Each piece writes into its own place in the
    file; how far each got is kept in <zip>.parts, so a broken line resumes.
    """
    if size <= 0:
        return _download(url, dest, size)
    state_path = dest + ".parts"
    n = PARTS
    bounds = [(i * size // n, (i + 1) * size // n - 1) for i in range(n)]
    done = [0] * n
    try:
        with open(state_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        if (isinstance(saved, list) and len(saved) == n and os.path.getsize(dest) == size
                and all(0 <= int(d) <= e - b + 1 for d, (b, e) in zip(saved, bounds))):
            done = [int(d) for d in saved]
    except (OSError, ValueError):
        pass
    if not os.path.exists(dest) or os.path.getsize(dest) != size or not any(done):
        with open(dest, "wb") as fh:
            fh.truncate(size)
        done = [0] * n
    lock = threading.Lock()
    errors: list = []
    _set(state="downloading", done=sum(done), total=size)

    def save() -> None:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(done, fh)

    def piece(i: int) -> None:
        start, end = bounds[i]
        for attempt in range(4):
            pos = start + done[i]
            if pos > end:
                return
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "SatpudaCore",
                                                           "Range": f"bytes={pos}-{end}"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    if r.status != 206:
                        raise RuntimeError("no-ranges")
                    with open(dest, "r+b") as out:
                        out.seek(pos)
                        while True:
                            if _cancel.is_set() or errors:
                                return
                            b = r.read(1 << 20)
                            if not b:
                                break
                            out.write(b)
                            with lock:
                                done[i] += len(b)
                                _set(done=sum(done))
                                if done[i] % (32 << 20) < len(b):
                                    save()
                if start + done[i] > end:
                    return
            except RuntimeError as exc:
                if str(exc) == "no-ranges":
                    errors.append(exc)
                    return
                if attempt == 3:
                    errors.append(exc)
            except Exception as exc:       # a dropped connection: try this piece again
                if attempt == 3:
                    errors.append(exc)
                time.sleep(2 + 3 * attempt)

    threads = [threading.Thread(target=piece, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    save()
    if _cancel.is_set():
        raise InterruptedError("cancelled")
    if any(str(e) == "no-ranges" for e in errors):
        os.remove(dest)
        os.remove(state_path)
        return _download(url, dest, size)
    if errors or sum(done) != size:
        raise RuntimeError(f"Download madhech thambla ({errors[0] if errors else 'adhura'}) -- punha Download kara, "
                           "jitka zala titka thevla aahe")
    os.remove(state_path)


def _local_dirs() -> list:
    """Where a copied SatpudaVoicePack.zip may be: this user's folders, the app's own, every pendrive."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, "Downloads"), os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
            os.path.dirname(os.path.abspath(sys.executable)), pack_home()]
    if os.name == "nt":
        try:
            import ctypes
            import string

            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for k, letter in enumerate(string.ascii_uppercase):
                root = f"{letter}:\\"
                if mask & (1 << k) and ctypes.windll.kernel32.GetDriveTypeW(root) == 2:   # removable
                    dirs += [root, os.path.join(root, "SatpudaCore")]
        except Exception:
            pass
    return dirs


def find_local_pack(size: int = 0) -> str:
    """A SatpudaVoicePack zip already on this PC or a pendrive: no download at all.

    Copying the 1.5 GB pack on a pendrive takes a minute or two; downloading it on a
    shop's line took 16 (2 Oct 2026). With the size from the manifest only the same
    pack is taken; offline, any zip of that name is tried (the zip's own checksums
    still refuse a damaged copy while it is extracted).
    """
    for d in _local_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if not (low.startswith(LOCAL_NAME) and low.endswith(".zip")):
                continue
            path = os.path.join(d, name)
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(
                    os.path.abspath(os.path.join(pack_home(), "SatpudaVoicePack.zip"))):
                continue                    # the download in progress is not a copy
            try:
                if not size or os.path.getsize(path) == size:
                    return path
            except OSError:
                continue
    return ""


def _prepare_model(new: str) -> None:
    """The model's float16 download made float32 now, while the screen shows it, not at first start."""
    py = os.path.join(new, "python", "python.exe")
    assets = os.path.join(new, "voice", "models", "indic600m", "assets")
    if not (os.path.isfile(py) and os.path.isfile(os.path.join(assets, "encoder.skel.onnx"))):
        return
    env = dict(os.environ, PYTHONNOUSERSITE="1")
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from voice.indic_ear import unpack_fp32; unpack_fp32(sys.argv[2])")
    try:
        subprocess.run([py, "-c", code, new, assets], cwd=new, env=env, timeout=1800,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass                                # the service unpacks it on its first start instead


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
        try:
            m = _get_json(MANIFEST_URL)
        except Exception:
            m = {}                          # offline: only a copied pack can be installed
        size, url, want = int(m.get("size") or 0), m.get("url"), str(m.get("sha256") or "").lower()
        free = shutil.disk_usage(home).free / 2**30
        if free < NEED_FREE_GB:
            raise RuntimeError(f"Disk var jaga kami: {free:.1f} GB mokli, {NEED_FREE_GB:.0f} GB lagel")
        local = find_local_pack(size)
        if local:
            zip_path = local
            _set(source=local)
        elif url:
            zip_path = os.path.join(home, "SatpudaVoicePack.zip")
            _download_parallel(url, zip_path, size)
        else:
            raise RuntimeError("Internet nahi ani SatpudaVoicePack.zip sapadla nahi (Downloads / Desktop / pendrive)")
        if want:
            _set(state="verifying", done=0, total=os.path.getsize(zip_path))
            got = _sha256(zip_path)
            if got != want:
                if not local:
                    os.remove(zip_path)
                raise RuntimeError("Download kharab zala (SHA-256 julat nahi) -- punha download kara"
                                   if not local else f"{local} ha voice pack kharab / juna aahe -- kadhun taka")
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
        _set(state="preparing", done=0, total=0)
        _prepare_model(new)
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
        if not local:                       # a copy the shop brought is theirs to keep
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
