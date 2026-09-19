"""Microphone backend selection — WASAPI (sounddevice) preferred, waveIn fallback."""
from __future__ import annotations

import re
import sys
import threading
import time
from typing import Callable, List, Optional

# PortAudio/WASAPI rejects overlapping InputStreams with a misleading
# "Invalid sample rate" error. Serialize all capture opens.
_SOUNDDEVICE_LOCK = threading.RLock()
_SOUNDDEVICE_LOCK_TIMEOUT = 8.0


def _clean_name(text: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


_USE_WINMM = sys.platform == "win32"
_JUNK_NAME_PARTS = (
    "stereo mix",
    "what u hear",
    "wave out mix",
    "loopback",
    "microsoft sound mapper",
    "primary sound capture",
    "pc speaker",
)

# Names Windows / PortAudio often use for headset mics (not built-in arrays).
_EXTERNAL_HINTS = (
    "headphone", "headset", "earbud", "earphone", "airpods", "buds",
    "bluetooth", "hands-free", "hands free", "external", "usb",
    "wireless", "dongle", "boom",
)


def _is_junk_device_name(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return True
    # Never treat headphone / headset / external mic names as junk.
    if any(h in n for h in _EXTERNAL_HINTS) or "mic" in n:
        if "stereo mix" in n or "loopback" in n or "pc speaker" in n:
            return True
        if "microsoft sound mapper" in n or "primary sound capture" in n:
            return True
        return False
    return any(junk in n for junk in _JUNK_NAME_PARTS)


def _is_externalish(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _EXTERNAL_HINTS)


def _friendly_mic_label(name: str, *, tag: str = "", is_default: bool = False) -> str:
    """
    Windows often lists the headset mic under the headphone product name
    (e.g. 'Headphones (Galaxy Buds)') — make that obvious in the picker.
    """
    short = name
    low = name.lower()
    if _is_externalish(name) and "mic" not in low:
        short = f"{name} (mic)"
    elif "external" in low and "mic" not in low:
        short = f"{name} (mic)"
    if is_default:
        short += " (default)"
    if tag:
        short = f"{short} [{tag}]"
    return short


def _dedupe_key(name: str) -> str:
    """Normalize names so truncated winmm labels match full WASAPI names."""
    n = (name or "").strip().lower()
    n = re.sub(r"\s*\(\d+[-–]\s*", " (", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    for fluff in (
        "wasapi", "mme", "directsound", "wdm ks", "wave",
        "default", "mapper",
    ):
        n = n.replace(fluff, " ")
    return re.sub(r"\s+", " ", n).strip()


def _keys_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # Truncated MME names: "microphone array realtek r au" vs "... audio"
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 12 and longer.startswith(shorter):
        return True
    return False


def _already_seen(seen: set, key: str) -> bool:
    if key in seen:
        return True
    for prev in seen:
        if _keys_match(prev, key):
            return True
    return False


def _prepare_sounddevice_path() -> None:
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        return
    import os
    base = sys._MEIPASS
    path = os.environ.get("PATH", "")
    parts = [p for p in path.split(os.pathsep) if p]
    if base not in parts:
        parts.insert(0, base)
    os.environ["PATH"] = os.pathsep.join(parts)


def mic_backend_name() -> str:
    devices = list_input_devices()
    if not devices:
        return "none"
    if any(d.get("backend") == "sounddevice" for d in devices):
        return "wasapi"
    return "winmm"


def mic_backend_available() -> bool:
    return bool(list_input_devices())


def list_input_devices() -> List[dict]:
    """
    List capture devices for the mic picker.

    On Windows this is the same active-input list as
    Settings → System → Sound → Input (MMDevice API).
    Each entry is mapped to a sounddevice/WASAPI index for recording.
    """
    if _USE_WINMM:
        windows_list = _list_windows_settings_devices()
        if windows_list:
            return windows_list

    # Non-Windows / MMDevice unavailable — PortAudio fallback.
    return _list_sounddevice_fallback_devices()


def _hostapi_kind(hostapis, hostapi: int) -> str:
    try:
        name = str(hostapis[hostapi].get("name") or "").lower()
    except Exception:
        return ""
    if "wasapi" in name:
        return "wasapi"
    if name == "mme" or name.endswith(" mme") or name.startswith("mme"):
        return "mme"
    if "directsound" in name:
        return "directsound"
    if "wdm" in name:
        return "wdm"
    return name


def _device_supports_rate(sd, device_index: int, rate: int) -> bool:
    try:
        sd.check_input_settings(device=device_index, samplerate=rate, channels=1)
        return True
    except Exception:
        pass
    try:
        info = sd.query_devices(device_index)
        ch = min(2, max(1, int(info.get("max_input_channels") or 1)))
        sd.check_input_settings(device=device_index, samplerate=rate, channels=ch)
        return True
    except Exception:
        return False


def _match_sounddevice_index(windows_name: str) -> tuple[int | None, str]:
    """Map a Windows Settings mic name to a PortAudio index.

    Prefer a host API that accepts 16 kHz (MME/DirectSound) when available,
    otherwise WASAPI (often locked to 48 kHz — we resample).
    """
    try:
        _prepare_sounddevice_path()
        import sounddevice as sd
        raw = list(sd.query_devices())
        hostapis = list(sd.query_hostapis())
    except Exception:
        return None, "winmm"

    target = _clean_name(windows_name).lower()
    if not target:
        return None, "winmm"

    scored = []
    for idx, dev in enumerate(raw):
        if int(dev.get("max_input_channels") or 0) < 1:
            continue
        name = _clean_name(dev.get("name") or "").lower()
        if not name:
            continue
        hostapi = int(dev.get("hostapi") or 0)
        if name == target:
            score = 100
        elif name.startswith(target) or target.startswith(name):
            score = 80
        elif target in name or name in target:
            score = 60
        else:
            continue
        kind = _hostapi_kind(hostapis, hostapi)
        # Prefer APIs that open cleanly at Whisper's 16 kHz.
        if _device_supports_rate(sd, idx, 16000):
            score += 40
        elif kind == "mme":
            score += 30
        elif kind == "directsound":
            score += 25
        elif kind == "wasapi":
            score += 15
        scored.append((score, idx, hostapi))

    if not scored:
        return None, "winmm"
    scored.sort(key=lambda row: (-row[0], row[1]))
    best = scored[0]
    return best[1], "sounddevice"


def _match_winmm_index(windows_name: str) -> int | None:
    try:
        from core.voice import win_audio
        if not win_audio.is_available():
            return None
        target = _clean_name(windows_name).lower()
        for dev in win_audio.list_input_devices():
            name = _clean_name(dev.get("name") or "").lower()
            if name == target or name.startswith(target[:20]) or target.startswith(name):
                return int(dev["index"])
    except Exception:
        return None
    return None


def _list_windows_settings_devices() -> List[dict]:
    """Exact Windows Settings → Sound → Input list, recordable via sounddevice."""
    try:
        from core.voice.win_mmdevice import list_windows_capture_devices
        endpoints = list_windows_capture_devices()
    except Exception:
        endpoints = []
    if not endpoints:
        return []

    out: List[dict] = []
    for ep in endpoints:
        name = _clean_name(ep.get("name") or "")
        if not name or _is_junk_device_name(name):
            continue
        sd_index, backend = _match_sounddevice_index(name)
        index = sd_index
        if index is None:
            index = _match_winmm_index(name)
            backend = "winmm" if index is not None else "sounddevice"
            if index is None:
                # Still show it — open may use default mapping by name later.
                index = int(ep.get("index") or 0)
                backend = "sounddevice"

        is_default = bool(ep.get("is_default"))
        label = name + (" (default)" if is_default else "")
        out.append({
            "index": index,
            "name": name,
            "label": label,
            "is_default": is_default,
            "backend": backend,
            "windows_id": ep.get("id") or "",
            "windows_index": int(ep.get("index") or 0),
        })
    return out


def _list_sounddevice_fallback_devices() -> List[dict]:
    """Non-Windows / fallback device list from PortAudio."""
    merged: List[dict] = []
    seen = set()
    for dev in _sounddevice_list_devices():
        key = _dedupe_key(dev.get("name") or "")
        if not key or _already_seen(seen, key) or _is_junk_device_name(dev.get("name") or ""):
            continue
        item = dict(dev)
        item["backend"] = "sounddevice"
        if not item.get("label"):
            item["label"] = item.get("name") or "Microphone"
        merged.append(item)
        seen.add(key)

    if _USE_WINMM:
        from core.voice import win_audio
        if win_audio.is_available():
            for dev in win_audio.list_input_devices():
                name = dev.get("name") or ""
                key = _dedupe_key(name)
                if not key or _already_seen(seen, key) or _is_junk_device_name(name):
                    continue
                item = dict(dev)
                item["backend"] = "winmm"
                item["label"] = name + (" (default)" if item.get("is_default") else "")
                merged.append(item)
                seen.add(key)

    if not merged and _USE_WINMM:
        from core.voice import win_audio
        if win_audio.is_available():
            merged.append({
                "index": -1,
                "name": "Default microphone",
                "label": "Default microphone",
                "is_default": True,
                "backend": "winmm",
            })
    return merged


def record_phrase(
    *,
    device_index: int,
    backend: str = "winmm",
    sample_rate: int = 16000,
    chunk_seconds: float = 0.1,
    max_seconds: float = 12.0,
    silence_threshold: float = 0.012,
    silence_seconds: float = 1.2,
    min_seconds: float = 0.35,
    stop_event=None,
    on_audio_level: Optional[Callable[[float], None]] = None,
    idle_timeout_seconds: Optional[float] = None,
):
    if backend == "sounddevice":
        return _sounddevice_record_phrase(
            device_index=device_index,
            sample_rate=sample_rate,
            chunk_seconds=chunk_seconds,
            max_seconds=max_seconds,
            silence_threshold=silence_threshold,
            silence_seconds=silence_seconds,
            min_seconds=min_seconds,
            stop_event=stop_event,
            on_audio_level=on_audio_level,
            idle_timeout_seconds=idle_timeout_seconds,
        )
    if _USE_WINMM:
        from core.voice import win_audio
        if win_audio.is_available():
            return win_audio.record_phrase(
                device_index=device_index,
                sample_rate=sample_rate,
                chunk_seconds=chunk_seconds,
                max_seconds=max_seconds,
                silence_threshold=silence_threshold,
                silence_seconds=silence_seconds,
                min_seconds=min_seconds,
                stop_event=stop_event,
                on_audio_level=on_audio_level,
                idle_timeout_seconds=idle_timeout_seconds,
            )
    return _sounddevice_record_phrase(
        device_index=device_index,
        sample_rate=sample_rate,
        chunk_seconds=chunk_seconds,
        max_seconds=max_seconds,
        silence_threshold=silence_threshold,
        silence_seconds=silence_seconds,
        min_seconds=min_seconds,
        stop_event=stop_event,
        on_audio_level=on_audio_level,
        idle_timeout_seconds=idle_timeout_seconds,
    )


def preview_levels(
    *,
    device_index: int,
    backend: str = "winmm",
    sample_rate: int = 16000,
    seconds: float = 3.0,
    stop_event=None,
    on_audio_level: Optional[Callable[[float], None]] = None,
) -> float:
    import numpy as np

    stop_event = stop_event or __import__("threading").Event()
    peak = 0.0
    chunk_seconds = 0.1
    max_chunks = max(1, int(seconds / chunk_seconds))

    for _ in range(max_chunks):
        if stop_event.is_set():
            break
        audio = record_phrase(
            device_index=device_index,
            backend=backend,
            sample_rate=sample_rate,
            chunk_seconds=chunk_seconds,
            max_seconds=chunk_seconds + 0.05,
            silence_threshold=0.0,
            silence_seconds=chunk_seconds + 1.0,
            min_seconds=0.05,
            stop_event=stop_event,
            on_audio_level=on_audio_level,
            idle_timeout_seconds=None,
        )
        if audio is None or len(audio) == 0:
            continue
        energy = float(np.sqrt(np.mean(audio ** 2)))
        level = min(1.0, energy / 0.08)
        peak = max(peak, level)

    if callable(on_audio_level):
        on_audio_level(0.0)
    return peak


def _sounddevice_list_devices() -> List[dict]:
    """Enumerate PortAudio input devices, preferring Windows WASAPI."""
    try:
        _prepare_sounddevice_path()
        import sounddevice as sd
    except Exception:
        return []
    try:
        raw = list(sd.query_devices())
    except Exception:
        return []
    try:
        hostapis = list(sd.query_hostapis())
    except Exception:
        hostapis = []

    wasapi_ids = {
        i for i, api in enumerate(hostapis)
        if "wasapi" in str(api.get("name") or "").lower()
    }
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None

    def _api_tag(hostapi: int) -> str:
        try:
            name = str(hostapis[hostapi].get("name") or "")
        except Exception:
            return ""
        low = name.lower()
        if "wasapi" in low:
            return "WASAPI"
        if "mme" in low:
            return "MME"
        if "directsound" in low:
            return "DS"
        if "wdm" in low:
            return "WDM"
        return name.split()[-1][:8] if name else ""

    # Prefer WASAPI (Bluetooth / USB / headphone-named mics), then other APIs.
    # Sort external-looking devices first so they are easy to find in the picker.
    order = []
    for idx, dev in enumerate(raw):
        if int(dev.get("max_input_channels") or 0) < 1:
            continue
        name = _clean_name(dev.get("name") or "")
        if _is_junk_device_name(name):
            continue
        hostapi = int(dev.get("hostapi") or 0)
        # Priority: WASAPI + external/headphone name, then WASAPI, then others.
        wasapi = 0 if hostapi in wasapi_ids else 1
        external = 0 if _is_externalish(name) else 1
        order.append((external, wasapi, idx, dev, name, hostapi))

    order.sort(key=lambda row: (row[0], row[1], row[2]))

    devices = []
    seen_names = set()
    for _ext, _wasapi, idx, dev, name, hostapi in order:
        key = _dedupe_key(name)
        # Within sounddevice, keep one entry per physical mic — prefer WASAPI.
        if _already_seen(seen_names, key):
            continue
        seen_names.add(key)
        is_default = idx == default_in
        tag = _api_tag(hostapi)
        devices.append({
            "index": idx,
            "name": name,
            "label": _friendly_mic_label(name, tag=tag, is_default=is_default),
            "is_default": is_default,
            "hostapi": hostapi,
            "max_input_channels": int(dev.get("max_input_channels") or 1),
            "default_samplerate": float(dev.get("default_samplerate") or 16000),
            "externalish": _is_externalish(name),
        })
    return devices


def _sounddevice_candidate_rates(device_index: int, sample_rate: int) -> list[int]:
    """Prefer the device's native rate — WASAPI often rejects 16 kHz."""
    import sounddevice as sd

    rates: list[int] = []
    native = 0
    try:
        info = sd.query_devices(device_index)
        native = int(float(info.get("default_samplerate") or 0))
    except Exception:
        info = {}
    # Native first (e.g. 48000 on Realtek WASAPI), then requested, then commons.
    for rate in (native, int(sample_rate), 48000, 44100, 16000, 8000):
        if rate and rate not in rates:
            rates.append(int(rate))
    return rates


def _sounddevice_open_stream(device_index: int, sample_rate: int, chunk_seconds: float, callback):
    """Open InputStream; fall back to device default rate (WASAPI often 48 kHz)."""
    import sounddevice as sd

    rates = _sounddevice_candidate_rates(device_index, sample_rate)
    try:
        info = sd.query_devices(device_index)
        max_ch = max(1, int(info.get("max_input_channels") or 1))
    except Exception:
        max_ch = 1
    channel_opts = [1] if max_ch == 1 else [1, min(2, max_ch)]

    last_err = None
    for rate in rates:
        for channels in channel_opts:
            stream = None
            try:
                # Skip rates PortAudio already knows are invalid for this device.
                try:
                    sd.check_input_settings(
                        device=device_index, samplerate=rate, channels=channels,
                    )
                except Exception as exc:
                    last_err = exc
                    continue
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=channels,
                    dtype="float32",
                    blocksize=max(1, int(rate * float(chunk_seconds or 0.1))),
                    callback=callback,
                    device=device_index,
                )
                stream.start()
                return stream, rate
            except Exception as exc:
                last_err = exc
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue
    raise RuntimeError(f"Could not open microphone #{device_index}: {last_err}")


def _resample_mono(audio, src_rate: int, dst_rate: int):
    import numpy as np

    if audio is None or len(audio) == 0 or src_rate == dst_rate or src_rate <= 0:
        return audio
    duration = len(audio) / float(src_rate)
    n_out = max(1, int(round(duration * dst_rate)))
    x_old = np.linspace(0.0, duration, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _sounddevice_record_phrase(**kwargs):
    import queue

    import numpy as np

    _prepare_sounddevice_path()

    device_index = kwargs["device_index"]
    sample_rate = kwargs.get("sample_rate", 16000)
    chunk_seconds = kwargs.get("chunk_seconds", 0.1)
    max_seconds = kwargs.get("max_seconds", 12.0)
    silence_threshold = kwargs.get("silence_threshold", 0.012)
    silence_seconds = kwargs.get("silence_seconds", 1.2)
    min_seconds = kwargs.get("min_seconds", 0.35)
    stop_event = kwargs.get("stop_event")
    on_audio_level = kwargs.get("on_audio_level")
    idle_timeout_seconds = kwargs.get("idle_timeout_seconds")

    stop_event = stop_event or __import__("threading").Event()
    frames = []
    silent_chunks = 0
    started = False
    chunk_duration = chunk_seconds
    max_chunks = int(max_seconds / chunk_duration)
    silence_limit = max(1, int(silence_seconds / chunk_duration))
    min_chunks = max(1, int(min_seconds / chunk_duration))
    idle_limit = (
        max(1, int(idle_timeout_seconds / chunk_duration))
        if idle_timeout_seconds
        else None
    )
    q: queue.Queue = queue.Queue()

    def _callback(indata, _frames_count, _time, _status):
        # Downmix stereo → mono in case WASAPI opens with 2 channels.
        if indata.ndim > 1 and indata.shape[1] > 1:
            q.put(indata.mean(axis=1).astype("float32").reshape(-1, 1).copy())
        else:
            q.put(indata.copy())

    acquired = _SOUNDDEVICE_LOCK.acquire(timeout=_SOUNDDEVICE_LOCK_TIMEOUT)
    if not acquired:
        raise RuntimeError(
            f"Could not open microphone #{device_index}: microphone busy"
        )
    stream = None
    open_rate = sample_rate
    try:
        stream, open_rate = _sounddevice_open_stream(
            device_index, sample_rate, chunk_seconds, _callback,
        )
        for _ in range(max_chunks):
            if stop_event.is_set():
                break
            try:
                chunk = q.get(timeout=0.08)
            except queue.Empty:
                continue
            mono = chunk.reshape(-1)
            energy = float(np.sqrt(np.mean(mono ** 2)))
            if callable(on_audio_level):
                on_audio_level(min(1.0, energy / 0.08))
            frames.append(mono)
            if energy >= silence_threshold:
                started = True
                silent_chunks = 0
            elif started:
                silent_chunks += 1
                if silent_chunks >= silence_limit and len(frames) >= min_chunks:
                    break
            elif idle_limit and len(frames) >= idle_limit:
                return None
    finally:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            # Brief settle so the next open does not hit a busy device.
            time.sleep(0.03)
        try:
            _SOUNDDEVICE_LOCK.release()
        except Exception:
            pass

    if not frames:
        return None
    audio = np.concatenate(frames)
    if open_rate != sample_rate:
        audio = _resample_mono(audio, open_rate, sample_rate)
    return audio
