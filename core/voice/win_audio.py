"""
Windows microphone capture via winmm.dll (waveIn).

Uses only built-in Windows DLLs — no PortAudio / sounddevice native binaries.
Works on Windows 7 through 11 and avoids RtlGetCurrentThreadPrimaryGroup
crashes from bundled PortAudio on older or mismatched PCs.
"""
from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from typing import Callable, List, Optional

winmm = ctypes.windll.winmm

MMSYSERR_NOERROR = 0
WAVE_FORMAT_PCM = 1
CALLBACK_FUNCTION = 0x00030000
WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002
MM_WIM_DATA = 0x3C0


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.DWORD),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_char_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", wintypes.DWORD),
    ]


WAVEINPROC = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
)


def is_available() -> bool:
    try:
        return int(winmm.waveInGetNumDevs()) >= 0
    except Exception:
        return False


def list_input_devices() -> List[dict]:
    """Return waveIn devices: index, name, label, is_default."""
    devices: List[dict] = []
    try:
        count = int(winmm.waveInGetNumDevs())
    except Exception:
        return devices

    for idx in range(count):
        caps = WAVEINCAPSW()
        err = winmm.waveInGetDevCapsW(idx, ctypes.byref(caps), ctypes.sizeof(caps))
        if err != MMSYSERR_NOERROR:
            continue
        name = (caps.szPname or "").strip() or "Microphone {}".format(idx)
        is_default = idx == 0
        devices.append({
            "index": idx,
            "name": name,
            "label": name + (" (default)" if is_default else ""),
            "is_default": is_default,
        })
    return devices


def _pcm16_to_float32(raw: bytes):
    import numpy as np

    if not raw:
        return np.array([], dtype=np.float32)
    samples = np.frombuffer(raw, dtype=np.int16)
    return (samples.astype(np.float32) / 32768.0).copy()


def record_phrase(
    *,
    device_index: int,
    sample_rate: int = 16000,
    channels: int = 1,
    chunk_seconds: float = 0.1,
    max_seconds: float = 12.0,
    silence_threshold: float = 0.012,
    silence_seconds: float = 1.2,
    min_seconds: float = 0.35,
    stop_event: Optional[threading.Event] = None,
    on_audio_level: Optional[Callable[[float], None]] = None,
    idle_timeout_seconds: Optional[float] = None,
) -> Optional[object]:
    """
    Record mono PCM via waveIn; return float32 numpy array or None.
    API mirrors recognizer.record_phrase timing behaviour.
    """
    import numpy as np

    stop_event = stop_event or threading.Event()
    bits = 16
    block_align = channels * (bits // 8)
    bytes_per_sec = sample_rate * block_align
    chunk_bytes = max(block_align, int(bytes_per_sec * chunk_seconds))
    chunk_samples = chunk_bytes // block_align

    fmt = WAVEFORMATEX()
    fmt.wFormatTag = WAVE_FORMAT_PCM
    fmt.nChannels = channels
    fmt.nSamplesPerSec = sample_rate
    fmt.nAvgBytesPerSec = bytes_per_sec
    fmt.nBlockAlign = block_align
    fmt.wBitsPerSample = bits
    fmt.cbSize = 0

    hwavein = wintypes.HANDLE()
    done_queue: queue.Queue = queue.Queue()
    callback_ref = {"fn": None}

    @WAVEINPROC
    def _callback(hwi, msg, instance, param1, param2):
        if msg == MM_WIM_DATA:
            done_queue.put(param1)

    callback_ref["fn"] = _callback

    err = winmm.waveInOpen(
        ctypes.byref(hwavein),
        int(device_index),
        ctypes.byref(fmt),
        callback_ref["fn"],
        0,
        CALLBACK_FUNCTION,
    )
    if err != MMSYSERR_NOERROR:
        raise OSError("waveInOpen failed (error {})".format(err))

    num_buffers = 6
    headers = []
    buffers = []
    for _ in range(num_buffers):
        buf = ctypes.create_string_buffer(chunk_bytes)
        hdr = WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
        hdr.dwBufferLength = chunk_bytes
        hdr.dwFlags = 0
        err = winmm.waveInPrepareHeader(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if err != MMSYSERR_NOERROR:
            winmm.waveInClose(hwavein)
            raise OSError("waveInPrepareHeader failed")
        err = winmm.waveInAddBuffer(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
        if err != MMSYSERR_NOERROR:
            winmm.waveInClose(hwavein)
            raise OSError("waveInAddBuffer failed")
        headers.append(hdr)
        buffers.append(buf)

    frames = []
    silent_chunks = 0
    started = False
    max_chunks = max(1, int(max_seconds / chunk_seconds))
    silence_limit = max(1, int(silence_seconds / chunk_seconds))
    min_chunks = max(1, int(min_seconds / chunk_seconds))
    idle_limit = (
        max(1, int(idle_timeout_seconds / chunk_seconds))
        if idle_timeout_seconds
        else None
    )

    try:
        err = winmm.waveInStart(hwavein)
        if err != MMSYSERR_NOERROR:
            raise OSError("waveInStart failed")

        for _ in range(max_chunks):
            if stop_event.is_set():
                break
            try:
                hdr_ptr = done_queue.get(timeout=0.12)
            except queue.Empty:
                continue

            hdr = None
            addr = int(hdr_ptr)
            for candidate in headers:
                if ctypes.addressof(candidate) == addr:
                    hdr = candidate
                    break
            if hdr is None:
                continue
            raw = ctypes.string_at(hdr.lpData, hdr.dwBytesRecorded)
            mono = _pcm16_to_float32(raw)
            if len(mono) == 0:
                winmm.waveInUnprepareHeader(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
                winmm.waveInPrepareHeader(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
                winmm.waveInAddBuffer(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
                continue

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

            winmm.waveInUnprepareHeader(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
            winmm.waveInPrepareHeader(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
            winmm.waveInAddBuffer(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
    finally:
        try:
            winmm.waveInStop(hwavein)
        except Exception:
            pass
        for hdr in headers:
            try:
                if hdr.dwFlags & WHDR_PREPARED:
                    winmm.waveInUnprepareHeader(hwavein, ctypes.byref(hdr), ctypes.sizeof(hdr))
            except Exception:
                pass
        try:
            winmm.waveInClose(hwavein)
        except Exception:
            pass
        if callable(on_audio_level):
            on_audio_level(0.0)

    if not frames:
        return None
    audio = np.concatenate(frames)
    return audio
