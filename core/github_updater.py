"""
GitHub Releases updater for Satpuda Core.

Checks https://github.com/roshanghogale/exes-for-satpuda-core/releases
for a newer release. Installed apps use the zip portable build (same as
SatpudaCoreInstaller). Legacy single-EXE portable builds can still swap the
running binary in place.
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
GITHUB_REPO = "exes-for-satpuda-core"
GITHUB_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
GITHUB_API_RELEASES = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
)
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

EXE_MODERN = "SatpudaCore.exe"       # Windows 8 / 10 / 11 (64-bit)
EXE_WIN7 = "SatpudaCore_Win7.exe"    # Windows 7 / 8 / 8.1 (32-bit Python 3.8 build)

INSTALLER_FALLBACK_NAME = "SatpudaCoreInstaller.exe"
INSTALLER_FALLBACK_URL = (
    "https://github.com/roshanghogale/exes-for-satpuda-core/releases/download/v1.0.0/"
    + INSTALLER_FALLBACK_NAME
)

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
    installer_download_url: str = ""
    installer_download_name: str = ""
    html_url: str = ""
    error: str = ""
    update_mode: str = "zip"  # zip | exe | installer_only

    @property
    def has_download(self) -> bool:
        return self.can_install_via_installer

    @property
    def can_install_via_installer(self) -> bool:
        if self.installer_download_url and self.installer_download_name:
            return True
        if self.update_mode == "installer_local" and self.download_url:
            return True
        try:
            from core.install_updater import resolve_installer_exe
            return bool(resolve_installer_exe())
        except Exception:
            return False


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
            with open(path, encoding="utf-8-sig") as fh:
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
    try:
        from core.install_updater import EXE_WIN10
        return EXE_WIN10
    except Exception:
        return EXE_MODERN


def is_win7_build() -> bool:
    if getattr(sys, "frozen", False):
        name = os.path.basename(sys.executable).lower()
        if name in (EXE_WIN7.lower(), "satpudacore_win8.exe"):
            return True
        try:
            from core.build_features import is_lite_build
            return is_lite_build()
        except Exception:
            pass
    return expected_exe_name().lower() == EXE_WIN7.lower()


def platform_label() -> str:
    try:
        from core.install_updater import platform_label as _install_platform_label
        return _install_platform_label()
    except Exception:
        return "Windows 7 / 8 / 8.1" if is_win7_build() else "Windows 10 / 11"


def _ca_bundle_path() -> Optional[str]:
    try:
        from core.ssl_utils import ca_bundle_path
        path = ca_bundle_path()
        return path or None
    except Exception:
        pass
    return None


def _ssl_context():
    try:
        from core.ssl_utils import ssl_context as _ctx
        return _ctx()
    except Exception:
        import ssl
        return ssl.create_default_context()


def _api_request(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_latest_release() -> dict:
    """
    Load the newest published release from GitHub.

    Tries /releases/latest first, then falls back to the releases list
    (some networks or API edge cases fail on the latest endpoint only).
    """
    try:
        return _api_request(GITHUB_API_LATEST)
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 403):
            raise
    releases = _api_request(f"{GITHUB_API_RELEASES}?per_page=10")
    if not isinstance(releases, list) or not releases:
        raise urllib.error.HTTPError(
            GITHUB_API_LATEST, 404, "No releases", hdrs=None, fp=None,
        )
    for rel in releases:
        if not rel.get("draft") and not rel.get("prerelease"):
            return rel
    return releases[0]


def _pick_asset(assets: List[dict]) -> Tuple[str, str, str]:
    """
    Pick the best update asset: zip portable build first, legacy single EXE second.
    Returns (name, url, mode) where mode is zip | exe | empty.
    """
    try:
        from core.install_updater import pick_zip_asset
        name, url = pick_zip_asset(assets)
        if url:
            return name, url, "zip"
    except Exception:
        pass
    target = expected_exe_name().lower()
    for asset in assets or []:
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if name.lower() == target and url:
            return name, url, "exe"
    legacy = EXE_MODERN.lower()
    if target != legacy:
        for asset in assets or []:
            name = str(asset.get("name") or "").strip()
            url = str(asset.get("browser_download_url") or "").strip()
            if name.lower() == legacy and url:
                return name, url, "exe"
    return "", "", ""


def _pick_installer_asset(assets: List[dict]) -> Tuple[str, str]:
    try:
        from core.install_updater import pick_installer_asset
        return pick_installer_asset(assets)
    except Exception:
        return "", ""


def check_for_update(current: str = APP_VERSION) -> UpdateInfo:
    info = UpdateInfo(available=False, current_version=current)
    try:
        payload = _fetch_latest_release()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            info.error = "No GitHub releases published yet."
        elif exc.code == 403:
            info.error = (
                "GitHub rate limit reached. Try again in a few minutes, "
                "or open the releases page in your browser."
            )
        else:
            info.error = f"GitHub API error ({exc.code})."
        return info
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        text = str(reason).lower()
        if "certificate" in text or "ssl" in text:
            info.error = (
                "Secure connection to GitHub failed (SSL). "
                "Check system date/time or open the releases page in your browser."
            )
        elif "timed out" in text or "timeout" in text:
            info.error = "GitHub timed out. Check your internet connection and try again."
        else:
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

    mark_checked_today()

    if not is_newer_version(latest, current):
        return info

    if get_skipped_version() and parse_version(get_skipped_version()) >= parse_version(latest):
        return info

    assets = payload.get("assets") or []
    inst_name, inst_url = _pick_installer_asset(assets)
    info.installer_download_name = inst_name
    info.installer_download_url = inst_url
    info.available = True

    try:
        from core.install_updater import resolve_installer_exe
        local_installer = resolve_installer_exe()
    except Exception:
        local_installer = ""

    if local_installer:
        info.download_name = os.path.basename(local_installer)
        info.download_url = local_installer
        info.update_mode = "installer_local"
    elif inst_url:
        info.download_name = inst_name
        info.download_url = inst_url
        info.update_mode = "installer"
    else:
        info.error = (
            f"Version {latest} is available but SatpudaCoreInstaller.exe was not found "
            f"on this PC or on the GitHub release."
        )
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
    with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
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
    """Replace running EXE and restart. Legacy single-file portable builds only."""
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
            "set TCL_LIBRARY=",
            "set TK_LIBRARY=",
            "set PYTHONHOME=",
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


def apply_update(
    info: UpdateInfo,
    downloaded_path: str = "",
    *,
    parent=None,
    progress_cb: Optional[Callable[[str, object], None]] = None,
) -> None:
    """Open Satpuda Core Installer, downloading it first only when not already present."""
    from core.install_updater import open_or_download_installer, resolve_install_dir

    def _dl_progress(read: int, total: int) -> None:
        if progress_cb:
            pct = min(100, int(read * 100 / total)) if total > 0 else 0
            progress_cb(
                "download_installer",
                {"message": f"Downloading installer… {pct}%" if total else "Downloading installer…"},
            )

    open_or_download_installer(
        info.installer_download_url,
        info.installer_download_name,
        install_dir=resolve_install_dir(),
        parent=parent,
        progress_cb=_dl_progress,
    )


def fetch_installer_download() -> Tuple[str, str, str]:
    """
    Fetch SatpudaCoreInstaller.exe from the latest GitHub release.

    Returns (url, name, error). Does not require a newer app version.
    """
    try:
        payload = _fetch_latest_release()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "", "", "No GitHub releases published yet."
        if exc.code == 403:
            return (
                "",
                "",
                "GitHub rate limit reached. Try again in a few minutes.",
            )
        return "", "", f"GitHub API error ({exc.code})."
    except urllib.error.URLError:
        return "", "", "Could not reach GitHub. Check your internet connection."
    except Exception as exc:
        return "", "", str(exc)

    assets = payload.get("assets") or []
    name, url = _pick_installer_asset(assets)
    if url:
        return url, name, ""

    tag = str(payload.get("tag_name") or "").strip().lstrip("vV")
    if tag:
        direct = (
            f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/"
            f"v{tag}/{INSTALLER_FALLBACK_NAME}"
        )
        return direct, INSTALLER_FALLBACK_NAME, ""

    return INSTALLER_FALLBACK_URL, INSTALLER_FALLBACK_NAME, ""


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
