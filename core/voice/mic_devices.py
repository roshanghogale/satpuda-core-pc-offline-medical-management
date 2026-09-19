"""Microphone device enumeration and saved device preference."""

import os
import re
import sys
import threading

from core.voice.audio_input import list_input_devices as _backend_list_devices

_device_cache = {"devices": None, "lock": threading.Lock()}


def _config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
        )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "voice_mic_device.txt")


def format_device_label(dev: dict) -> str:
    """Single-line label — prefer exact Windows Settings name."""
    existing = (dev.get("label") or "").strip()
    if existing:
        return _clean_name(existing)[:72]
    name = _clean_name(dev.get("name") or "Microphone")
    if dev.get("is_default") and "(default)" not in name.lower():
        name += " (default)"
    return name



def _clean_name(text: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 48:
        text = text[:45] + "..."
    return text or "Microphone"


def _normalize_devices(devices: list) -> list:
    out = []
    for dev in devices or []:
        if not isinstance(dev, dict):
            continue
        item = dict(dev)
        item["name"] = _clean_name(item.get("name") or "")
        item["label"] = format_device_label(item)
        out.append(item)
    return out


def _query_devices_sync() -> list:
    try:
        return _normalize_devices(_backend_list_devices())
    except Exception:
        return []


def list_input_devices(timeout: float = 8.0):
    """Return input devices; uses cache after first successful scan."""
    with _device_cache["lock"]:
        if _device_cache["devices"] is not None:
            return list(_device_cache["devices"])

    result = {"devices": []}

    def _query():
        result["devices"] = _query_devices_sync()

    worker = threading.Thread(target=_query, daemon=True, name="MicDeviceQuery")
    worker.start()
    worker.join(timeout=max(2.0, float(timeout or 8.0)))
    devices = result["devices"]
    if devices:
        with _device_cache["lock"]:
            _device_cache["devices"] = list(devices)
    return devices


def _ui_root(root):
    if root is None:
        return None
    if hasattr(root, "after"):
        return root
    for attr in ("root", "win"):
        candidate = getattr(root, attr, None)
        if candidate is not None and hasattr(candidate, "after"):
            return candidate
    return None


def list_input_devices_async(on_ready, *, root=None, timeout: float = 8.0):
    """Enumerate microphones in the background; call on_ready(devices) on the UI thread."""
    tk_root = _ui_root(root)

    def _work():
        devices = _query_devices_sync()
        if devices:
            with _device_cache["lock"]:
                _device_cache["devices"] = list(devices)

        def _deliver():
            try:
                on_ready(devices)
            except Exception:
                pass

        if tk_root is not None:
            try:
                tk_root.after(0, _deliver)
                return
            except Exception:
                pass
        _deliver()

    threading.Thread(target=_work, daemon=True, name="MicDeviceQuery").start()


def resolve_device(preferred=None, backend=None):
    """Return full device dict (index, backend, label, ...)."""
    devices = list_input_devices()
    if not devices:
        return None

    if isinstance(preferred, dict):
        pb = preferred.get("backend")
        pi = preferred.get("index")
        for d in devices:
            if d.get("index") == pi and (pb is None or d.get("backend") == pb):
                return d

    if preferred is not None and backend is not None:
        for d in devices:
            if d.get("index") == preferred and d.get("backend") == backend:
                return d

    if preferred is not None and backend is None:
        for d in devices:
            if d.get("index") == preferred:
                return d

    saved = load_saved_device()
    if saved:
        for d in devices:
            if d.get("index") == saved.get("index") and d.get("backend") == saved.get("backend"):
                return d

    for d in devices:
        if d.get("is_default"):
            return d
    return devices[0]


def load_saved_device():
    path = _config_path()
    try:
        if not os.path.exists(path):
            return None
        raw = open(path, encoding="utf-8").read().strip()
        if ":" in raw:
            backend, idx = raw.split(":", 1)
            return {"backend": backend.strip(), "index": int(idx.strip())}
        return {"backend": "winmm", "index": int(raw)}
    except Exception:
        return None


def load_saved_device_index():
    saved = load_saved_device()
    return saved.get("index") if saved else None


def save_device(device) -> None:
    if isinstance(device, dict):
        backend = device.get("backend", "winmm")
        index = device.get("index")
    else:
        backend, index = "winmm", device
    if index is None:
        return
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            f.write("{}:{}".format(backend, int(index)))
    except Exception:
        pass


def save_device_index(index: int, backend: str = "winmm"):
    save_device({"backend": backend, "index": index})


def resolve_device_index(preferred=None):
    """Pick a valid input device index."""
    dev = resolve_device(preferred)
    return dev["index"] if dev else None


def device_label(index, backend=None) -> str:
    for d in list_input_devices():
        if d.get("index") == index and (backend is None or d.get("backend") == backend):
            return d.get("label") or format_device_label(d)
    return "Device {}".format(index)


def invalidate_device_cache():
    with _device_cache["lock"]:
        _device_cache["devices"] = None
