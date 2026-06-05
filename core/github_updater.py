"""
GitHub Releases updater for Satpuda Core.

Checks https://github.com/roshanghogale/satpuda-core-pc-offline-medical-management
for a newer release, downloads the matching EXE asset, and replaces the running
binary. Activation (AppData) and veterinary.db next to the EXE are preserved.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Tuple

from core.app_version import APP_NAME, APP_VERSION

GITHUB_OWNER = "roshanghogale"
GITHUB_REPO = "satpuda-core-pc-offline-medical-management"
GITHUB_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

EXE_MODERN = "SatpudaCore.exe"       # Windows 8 / 10 / 11 (64-bit)
EXE_WIN7 = "SatpudaCore_Win7.exe"    # Windows 7 / 8 / 8.1 (32-bit Python 3.8 build)

_USER_AGENT = f"{APP_NAME.replace(' ', '')}/{APP_VERSION}"


@dataclass
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: str = ""
    release_name: str = ""
    release_notes: str = ""
    published_at: str = ""
    download_url: str = ""
    download_name: str = ""
    html_url: str = ""
    error: str = ""

    @property
    def has_download(self) -> bool:
        return bool(self.download_url and self.download_name)


def _app_data_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        path = os.path.join(base, "VeterinaryApp")
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
    os.makedirs(path, exist_ok=True)
    return path


def _prefs_path() -> str:
    return os.path.join(_app_data_dir(), "update_prefs.json")


def _load_prefs() -> Dict[str, str]:
    path = _prefs_path()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _save_prefs(**kwargs) -> None:
    data = _load_prefs()
    data.update(kwargs)
    try:
        with open(_prefs_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


def is_auto_check_enabled() -> bool:
    return str(_load_prefs().get("auto_check", "1")).strip() not in ("0", "false", "no")


def set_auto_check_enabled(enabled: bool) -> None:
    _save_prefs(auto_check="1" if enabled else "0")


def get_skipped_version() -> str:
    return str(_load_prefs().get("skip_version", "") or "").strip()


def set_skipped_version(version: str) -> None:
    _save_prefs(skip_version=(version or "").strip())


def mark_checked_today() -> None:
    _save_prefs(last_check=date.today().isoformat())


def should_auto_check_today() -> bool:
    if not is_auto_check_enabled():
        return False
    last = str(_load_prefs().get("last_check", "") or "").strip()
    return last != date.today().isoformat()


def parse_version(value: str) -> Tuple[int, ...]:
    """Parse 'v1.2.3' or '1.2.3' into a comparable tuple."""
    text = (value or "").strip().lstrip("vV")
    parts = re.findall(r"\d+", text)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


def expected_exe_name() -> str:
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable)
    return EXE_MODERN


def is_win7_build() -> bool:
    return expected_exe_name().lower() == EXE_WIN7.lower()


def platform_label() -> str:
    return "Windows 7 / 8 / 8.1" if is_win7_build() else "Windows 10 / 11"


def _api_request(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_asset(assets: List[dict]) -> Tuple[str, str]:
    """
    Pick the EXE asset that matches the running build only.
    Win7 PCs download SatpudaCore_Win7.exe; Win10/11 PCs download SatpudaCore.exe.
    """
    target = expected_exe_name().lower()
    for asset in assets or []:
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if name.lower() == target and url:
            return name, url
    return "", ""


def check_for_update(current: str = APP_VERSION) -> UpdateInfo:
    info = UpdateInfo(available=False, current_version=current)
    try:
        payload = _api_request(GITHUB_API_LATEST)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            info.error = "No GitHub releases published yet."
        else:
            info.error = f"GitHub API error ({exc.code})."
        return info
    except urllib.error.URLError:
        info.error = "Could not reach GitHub. Check your internet connection."
        return info
    except Exception as exc:
        info.error = str(exc)
        return info

    tag = str(payload.get("tag_name") or "").strip()
    latest = tag.lstrip("vV") or tag
    info.latest_version = latest or tag
    info.release_name = str(payload.get("name") or "").strip()
    info.release_notes = str(payload.get("body") or "").strip()
    info.published_at = str(payload.get("published_at") or "").strip()
    info.html_url = str(payload.get("html_url") or GITHUB_RELEASES_PAGE).strip()

    if not latest:
        info.error = "Release has no version tag."
        return info

    if not is_newer_version(latest, current):
        return info

    if get_skipped_version() and parse_version(get_skipped_version()) >= parse_version(latest):
        return info

    name, url = _pick_asset(payload.get("assets") or [])
    info.download_name = name
    info.download_url = url
    info.available = True
    if not url:
        needed = expected_exe_name()
        other = EXE_WIN7 if needed == EXE_MODERN else EXE_MODERN
        info.error = (
            f"Version {latest} is available but {needed} was not found on the release "
            f"({platform_label()} build). Upload both {EXE_MODERN} and {EXE_WIN7} "
            f"when publishing. This PC needs {needed}, not {other}."
        )
    mark_checked_today()
    return info


def download_update(
    info: UpdateInfo,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> str:
    if not info.download_url:
        raise RuntimeError(info.error or "No download URL for this release.")
    tmp_dir = tempfile.mkdtemp(prefix="satpuda_update_")
    dest = os.path.join(tmp_dir, info.download_name or expected_exe_name())
    req = urllib.request.Request(
        info.download_url,
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        read = 0
        chunk_size = 256 * 1024
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                read += len(chunk)
                if progress_cb:
                    progress_cb(read, total)
    if os.path.getsize(dest) < 1024 * 100:
        raise RuntimeError("Downloaded file is too small — release asset may be invalid.")
    return dest


def apply_downloaded_update(new_exe_path: str, parent=None) -> None:
    """Replace running EXE and restart. AppData activation is untouched."""
    if not getattr(sys, "frozen", False):
        webbrowser.open(GITHUB_RELEASES_PAGE)
        return

    current_exe = os.path.abspath(sys.executable)
    app_dir = os.path.dirname(current_exe)
    new_exe_path = os.path.abspath(new_exe_path)
    backup_exe = current_exe + ".bak"
    bat_path = os.path.join(app_dir, "_apply_satpuda_update.bat")

    bat = "\r\n".join(
        [
            "@echo off",
            "setlocal",
            f'ping 127.0.0.1 -n 3 > nul',
            f'if exist "{backup_exe}" del /f /q "{backup_exe}"',
            f'move /Y "{current_exe}" "{backup_exe}"',
            f'move /Y "{new_exe_path}" "{current_exe}"',
            f'start "" "{current_exe}"',
            f'del /f /q "%~f0"',
        ]
    )
    with open(bat_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(bat)

    if parent is not None:
        try:
            parent.destroy()
        except Exception:
            pass
        try:
            parent.quit()
        except Exception:
            pass

    subprocess.Popen(
        ["cmd", "/c", bat_path],
        cwd=app_dir,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    sys.exit(0)


def open_releases_page() -> None:
    webbrowser.open(GITHUB_RELEASES_PAGE)


def format_release_summary(info: UpdateInfo) -> str:
    lines = [
        f"Current version: v{info.current_version}",
        f"Latest version:  v{info.latest_version}",
    ]
    if info.release_name:
        lines.append(f"Release: {info.release_name}")
    if info.published_at:
        try:
            dt = datetime.fromisoformat(info.published_at.replace("Z", "+00:00"))
            lines.append(f"Published: {dt.strftime('%d-%b-%Y')}")
        except Exception:
            lines.append(f"Published: {info.published_at[:10]}")
    if info.release_notes:
        notes = info.release_notes.strip()
        if len(notes) > 1200:
            notes = notes[:1200] + "\n…"
        lines.append("")
        lines.append(notes)
    return "\n".join(lines)


def run_in_thread(target: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread
