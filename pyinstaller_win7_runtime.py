"""
Collect MSVC + Universal CRT DLLs for Python 3.8 / PyInstaller on Windows 7.

IMPORTANT: Never bundle api-ms-win-core-*.dll from the Win10 SDK — they break
Winsock on Windows 7 (_socket: The parameter is incorrect).
Only bundle: ucrtbase.dll, api-ms-win-crt-*.dll, and MSVC runtime DLLs.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
REDIST = os.path.join(ROOT, "redist")
UCRT_CACHE = os.path.join(REDIST, "ucrt_x86")
MSVC_CACHE = os.path.join(REDIST, "msvc_x86")

_INCOMPATIBLE_PREFIXES = ("api-ms-win-core-",)


def _is_win7_ucrt_dll(filename: str) -> bool:
    lower = filename.lower()
    if lower == "ucrtbase.dll":
        return True
    if lower.startswith("api-ms-win-crt-") and lower.endswith(".dll"):
        return True
    return False


def _dlls_in(folder: str, *, ucrt_only: bool = False) -> list[str]:
    if not os.path.isdir(folder):
        return []
    out: list[str] = []
    for name in os.listdir(folder):
        if not name.lower().endswith(".dll"):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if ucrt_only and not _is_win7_ucrt_dll(name):
            continue
        if any(name.lower().startswith(p) for p in _INCOMPATIBLE_PREFIXES):
            continue
        out.append(path)
    return sorted(out)


def _find_windows_sdk_ucrt() -> str | None:
    base = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Windows Kits", "10", "Redist",
    )
    for pattern in (
        os.path.join(base, "ucrt", "DLLs", "x86"),
        os.path.join(base, "10.0.*", "ucrt", "DLLs", "x86"),
    ):
        for path in sorted(glob.glob(pattern), reverse=True):
            if _dlls_in(path, ucrt_only=True):
                return path
    return None


def _find_msvc_crt() -> str | None:
    vs = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Microsoft Visual Studio",
    )
    if not os.path.isdir(vs):
        return None
    candidates: list[str] = []
    for path in glob.glob(os.path.join(vs, "*", "VC", "Redist", "MSVC", "*", "x86", "Microsoft.VC*.CRT")):
        if _dlls_in(path):
            candidates.append(path)
    return sorted(candidates)[-1] if candidates else None


def _python38_dir() -> str | None:
    try:
        out = subprocess.check_output(
            ["py", "-3.8-32", "-c", "import sys, os; print(os.path.dirname(sys.executable))"],
            text=True, timeout=30,
        ).strip()
        return out if out and os.path.isdir(out) else None
    except Exception:
        return None


def _ensure_cache() -> None:
    if len(_dlls_in(UCRT_CACHE, ucrt_only=True)) < 10:
        src = _find_windows_sdk_ucrt()
        if src:
            os.makedirs(UCRT_CACHE, exist_ok=True)
            for path in _dlls_in(src, ucrt_only=True):
                shutil.copy2(path, os.path.join(UCRT_CACHE, os.path.basename(path)))
    for name in os.listdir(UCRT_CACHE) if os.path.isdir(UCRT_CACHE) else []:
        if any(name.lower().startswith(p) for p in _INCOMPATIBLE_PREFIXES):
            os.remove(os.path.join(UCRT_CACHE, name))
    if not _dlls_in(MSVC_CACHE):
        src = _find_msvc_crt()
        if src:
            os.makedirs(MSVC_CACHE, exist_ok=True)
            for path in _dlls_in(src):
                shutil.copy2(path, os.path.join(MSVC_CACHE, os.path.basename(path)))


def collect_runtime_dll_paths() -> list[str]:
    _ensure_cache()
    seen: set[str] = set()
    out: list[str] = []
    for folder in (UCRT_CACHE, MSVC_CACHE):
        ucrt_only = folder == UCRT_CACHE
        for path in _dlls_in(folder, ucrt_only=ucrt_only):
            base = os.path.basename(path).lower()
            if base in seen:
                continue
            seen.add(base)
            out.append(path)
    py_dir = _python38_dir()
    if py_dir:
        for name in ("python38.dll", "python3.dll", "vcruntime140.dll"):
            path = os.path.join(py_dir, name)
            base = name.lower()
            if os.path.isfile(path) and base not in seen:
                seen.add(base)
                out.append(path)
    return out


def remove_incompatible_dlls(dist_dir: str) -> int:
    """Delete Win10 api-ms-win-core DLLs that break Winsock on Win7."""
    removed = 0
    for sub in (dist_dir, os.path.join(dist_dir, "_internal")):
        if not os.path.isdir(sub):
            continue
        for name in os.listdir(sub):
            lower = name.lower()
            if lower.endswith(".dll") and lower.startswith("api-ms-win-core-"):
                os.remove(os.path.join(sub, name))
                removed += 1
    return removed


def copy_runtime_to_folder(dist_dir: str) -> int:
    remove_incompatible_dlls(dist_dir)
    internal = os.path.join(dist_dir, "_internal")
    os.makedirs(internal, exist_ok=True)
    count = 0
    for src in collect_runtime_dll_paths():
        name = os.path.basename(src)
        for dest_dir in (dist_dir, internal):
            shutil.copy2(src, os.path.join(dest_dir, name))
            count += 1
    remove_incompatible_dlls(dist_dir)
    return count
