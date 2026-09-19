"""Installer-layout updates for Satpuda Core."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

INSTALL_FOLDER_NAME = "Satpuda Core"
INSTALLER_EXE = "SatpudaCoreInstaller.exe"

ZIP_WIN10_NAMES = ("SatpudaCore_Win10.zip", "SatpudaCore_Win10_Portable.zip")
ZIP_WIN7_NAMES = ("SatpudaCore_Win7.zip", "SatpudaCore_Win7_Portable.zip")

EXE_WIN10 = "SatpudaCore_Win10.exe"
EXE_WIN7 = "SatpudaCore_Win7.exe"
EXE_LEGACY_MODERN = "SatpudaCore.exe"

PRESERVE_FILENAMES = frozenset({
    "veterinary.db",
    "expiry.dat",
    "activation.dat",
    "device.key",
})

PRESERVE_SUFFIXES = (".db", ".dat", ".json")


@dataclass
class InstallContext:
    install_dir: str
    uses_installer_layout: bool
    desktop_shortcut: str
    installer_exe: str
    platform_key: str
    exe_name: str
    mode_label: str


def _local_app_data() -> str:
    return os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))


def default_install_dir() -> str:
    return os.path.join(_local_app_data(), "Programs", INSTALL_FOLDER_NAME)


def downloads_dir() -> str:
    userprofile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    for candidate in (
        os.path.join(userprofile, "Downloads"),
        os.path.join(userprofile, "Download"),
    ):
        if os.path.isdir(candidate):
            return candidate
    return candidate


def installer_state_dir() -> str:
    path = os.path.join(_local_app_data(), "SatpudaInstaller")
    os.makedirs(path, exist_ok=True)
    return path


def installer_state_path() -> str:
    return os.path.join(installer_state_dir(), "installer_state.json")


def load_installer_state() -> dict:
    path = installer_state_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_installer_state(**kwargs) -> None:
    data = load_installer_state()
    data.update(kwargs)
    path = installer_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def is_win7_platform() -> bool:
    if getattr(sys, "frozen", False):
        name = os.path.basename(sys.executable).lower()
        if name in (EXE_WIN7.lower(), "satpudacore_win8.exe"):
            return True
        try:
            from core.build_features import is_lite_build
            return is_lite_build()
        except Exception:
            pass
    try:
        v = sys.getwindowsversion()
        return v.major == 6 and v.minor == 1
    except Exception:
        return False


def platform_key() -> str:
    st = load_installer_state().get("platform")
    if st in ("win7", "win10"):
        return st
    return "win7" if is_win7_platform() else "win10"


def expected_app_exe_name() -> str:
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable)
    return EXE_WIN10 if platform_key() == "win10" else EXE_WIN7


def zip_names_for_platform(key: Optional[str] = None) -> Tuple[str, ...]:
    return ZIP_WIN7_NAMES if (key or platform_key()) == "win7" else ZIP_WIN10_NAMES


def app_exe_for_platform(key: Optional[str] = None) -> str:
    return EXE_WIN7 if (key or platform_key()) == "win7" else EXE_WIN10


def _resolve_install_dir_from_state() -> str:
    st = load_installer_state()
    for key in ("install_root", "install_path"):
        path = str(st.get(key) or "").strip()
        if path and os.path.isdir(path):
            return path
    return ""


def _running_install_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ""


def resolve_install_dir() -> str:
    from_state = _resolve_install_dir_from_state()
    if from_state:
        return from_state
    running = _running_install_dir()
    if running and INSTALL_FOLDER_NAME.lower() in running.replace("/", "\\").lower():
        return running
    default = default_install_dir()
    if os.path.isdir(default):
        return default
    return running or default


def _ps_escape(value: str) -> str:
    return str(value).replace('"', '`"')


def _powershell_desktop_dir() -> str:
    script = '[Environment]::GetFolderPath("Desktop")'
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            desktop = (proc.stdout or "").strip()
            if desktop and os.path.isdir(desktop):
                return desktop
    except Exception:
        pass
    return os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")


def _resolve_lnk_target(lnk_path: str) -> str:
    try:
        script = f'''
$ws = New-Object -ComObject WScript.Shell
$ws.CreateShortcut("{_ps_escape(lnk_path)}").TargetPath
'''
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            target = (proc.stdout or "").strip()
            if target and os.path.isfile(target):
                return target
    except Exception:
        pass
    return ""


def find_desktop_shortcut(key: Optional[str] = None) -> str:
    desktop = _desktop_dir_for_shortcuts()
    if not desktop or not os.path.isdir(desktop):
        return ""
    names = []
    pk = key or platform_key()
    names.append(f"{os.path.splitext(app_exe_for_platform(pk))[0]}.lnk")
    names.extend(
        f"{os.path.splitext(name)[0]}.lnk"
        for name in (EXE_WIN10, EXE_WIN7, EXE_LEGACY_MODERN)
    )
    seen = set()
    for name in names:
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        path = os.path.join(desktop, name)
        if os.path.isfile(path):
            return path
    return ""


def find_installer_in_folder(folder: str) -> str:
    if not folder or not os.path.isdir(folder):
        return ""
    candidate = os.path.join(folder, INSTALLER_EXE)
    if os.path.isfile(candidate):
        return candidate
    lnk = os.path.join(folder, f"{os.path.splitext(INSTALLER_EXE)[0]}.lnk")
    if os.path.isfile(lnk):
        return _resolve_lnk_target(lnk)
    return ""


def find_installer_on_desktop() -> str:
    return find_installer_in_folder(_powershell_desktop_dir())


def find_installer_in_downloads() -> str:
    return find_installer_in_folder(downloads_dir())


def find_installer_exe(install_dir: Optional[str] = None) -> str:
    dirs = []
    if install_dir:
        dirs.append(install_dir)
    running = _running_install_dir()
    if running:
        dirs.append(running)
    dirs.append(default_install_dir())
    seen = set()
    for folder in dirs:
        folder = os.path.abspath(folder)
        if not folder or folder in seen:
            continue
        seen.add(folder)
        found = find_installer_in_folder(folder)
        if found:
            return found
    return ""


def search_installer_locations() -> List[str]:
    """All known SatpudaCoreInstaller.exe paths on this PC."""
    found: List[str] = []
    seen = set()
    for folder in (
        default_install_dir(),
        _running_install_dir(),
        _powershell_desktop_dir(),
        downloads_dir(),
    ):
        folder = os.path.abspath(folder or "")
        if not folder or folder in seen:
            continue
        seen.add(folder)
        path = find_installer_in_folder(folder)
        if path and path.lower() not in {p.lower() for p in found}:
            found.append(path)
    extra = find_installer_exe()
    if extra and extra.lower() not in {p.lower() for p in found}:
        found.append(extra)
    return found


def resolve_installer_exe(install_dir: Optional[str] = None) -> str:
    for path in search_installer_locations():
        if install_dir:
            target = os.path.abspath(install_dir)
            folder = os.path.dirname(os.path.abspath(path))
            if folder.lower() == target.lower():
                return path
        else:
            return path
    if install_dir:
        return find_installer_in_folder(install_dir)
    for path in search_installer_locations():
        return path
    return ""


def get_install_context() -> InstallContext:
    install_dir = resolve_install_dir()
    shortcut = find_desktop_shortcut()
    installer_exe = resolve_installer_exe(install_dir)
    pk = platform_key()
    exe_name = app_exe_for_platform(pk)

    under_programs = False
    if getattr(sys, "frozen", False):
        low = _running_install_dir().replace("/", "\\").lower()
        under_programs = f"programs\\{INSTALL_FOLDER_NAME.lower()}" in low
    has_state = bool(_resolve_install_dir_from_state())
    app_present = bool(
        install_dir and os.path.isfile(os.path.join(install_dir, exe_name))
    )

    uses_layout = bool(
        shortcut or installer_exe or has_state or under_programs or app_present
    )

    if shortcut or installer_exe or has_state or under_programs:
        mode = "Installed via Satpuda Core Installer"
    elif app_present:
        mode = f"Installed in {INSTALL_FOLDER_NAME} folder"
    else:
        mode = "Portable folder (not yet installed to Programs)"

    return InstallContext(
        install_dir=install_dir,
        uses_installer_layout=uses_layout,
        desktop_shortcut=shortcut,
        installer_exe=installer_exe,
        platform_key=pk,
        exe_name=exe_name,
        mode_label=mode,
    )


def _asset_priority(name: str, supported: Tuple[str, ...]) -> int:
    low = name.lower()
    for index, candidate in enumerate(supported):
        if low == candidate.lower():
            return index
    return -1


def pick_zip_asset(assets: List[dict], key: Optional[str] = None) -> Tuple[str, str]:
    pk = key or platform_key()
    supported = zip_names_for_platform(pk)
    best_name, best_url, best_rank = "", "", len(supported) + 1
    for asset in assets or []:
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if not name or not url:
            continue
        rank = _asset_priority(name, supported)
        if rank != -1 and rank < best_rank:
            best_name, best_url, best_rank = name, url, rank
    return best_name, best_url


def pick_installer_asset(assets: List[dict]) -> Tuple[str, str]:
    target = INSTALLER_EXE.lower()
    for asset in assets or []:
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if name.lower() == target and url:
            return name, url
    return "", ""


def _desktop_dir_for_shortcuts() -> str:
    desktop = _powershell_desktop_dir()
    if desktop and os.path.isdir(desktop):
        return desktop
    fallback = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    if os.path.isdir(fallback):
        return fallback
    return desktop or fallback


def _find_app_exe_in_dir(install_dir: str) -> str:
    install_dir = os.path.abspath(install_dir or "")
    if not install_dir or not os.path.isdir(install_dir):
        return ""
    for exe in (
        expected_app_exe_name(),
        app_exe_for_platform(),
        EXE_WIN10,
        EXE_WIN7,
        EXE_LEGACY_MODERN,
    ):
        if exe and os.path.isfile(os.path.join(install_dir, exe)):
            return exe
    return ""


def resolve_app_install_dir() -> str:
    """Folder containing the installed Satpuda Core app exe."""
    candidates: List[str] = []
    shortcut = find_desktop_shortcut()
    if shortcut:
        target = _resolve_lnk_target(shortcut)
        if target:
            candidates.append(os.path.dirname(os.path.abspath(target)))

    for folder in (
        _running_install_dir(),
        _resolve_install_dir_from_state(),
        resolve_install_dir(),
        default_install_dir(),
    ):
        folder = os.path.abspath(folder or "")
        if folder:
            candidates.append(folder)

    seen = set()
    for folder in candidates:
        key = folder.lower()
        if not folder or key in seen:
            continue
        seen.add(key)
        if _find_app_exe_in_dir(folder):
            return folder
    return ""


def _ps_single_quote(value: str) -> str:
    return str(value).replace("'", "''")


def create_desktop_shortcut(install_dir: str, exe_name: Optional[str] = None) -> str:
    """Create the app desktop shortcut the same way Satpuda Core Installer does."""
    install_dir = os.path.abspath(install_dir)
    exe = exe_name or _find_app_exe_in_dir(install_dir) or app_exe_for_platform()
    exe_path = os.path.join(install_dir, exe)
    if not os.path.isfile(exe_path):
        raise FileNotFoundError(exe_path)

    desktop = _desktop_dir_for_shortcuts()
    os.makedirs(desktop, exist_ok=True)
    lnk = os.path.join(desktop, f"{os.path.splitext(exe)[0]}.lnk")

    for legacy in (EXE_WIN10, EXE_WIN7, EXE_LEGACY_MODERN):
        legacy_lnk = os.path.join(desktop, f"{os.path.splitext(legacy)[0]}.lnk")
        if legacy_lnk.lower() != lnk.lower() and os.path.isfile(legacy_lnk):
            try:
                os.remove(legacy_lnk)
            except OSError:
                pass

    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{_ps_single_quote(lnk)}')
$s.TargetPath = '{_ps_single_quote(exe_path)}'
$s.WorkingDirectory = '{_ps_single_quote(install_dir)}'
$s.IconLocation = '{_ps_single_quote(exe_path)},0'
$s.Save()
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            detail or f"PowerShell exited with code {proc.returncode}"
        )
    if not os.path.isfile(lnk):
        raise RuntimeError(f"Desktop shortcut was not created: {lnk}")
    return lnk


def create_installer_desktop_shortcut(installer_path: str) -> str:
    """Create a desktop shortcut for SatpudaCoreInstaller.exe."""
    installer_path = os.path.abspath(installer_path)
    if not os.path.isfile(installer_path):
        raise FileNotFoundError(installer_path)

    desktop = _desktop_dir_for_shortcuts()
    os.makedirs(desktop, exist_ok=True)
    lnk = os.path.join(
        desktop,
        f"{os.path.splitext(os.path.basename(installer_path))[0]}.lnk",
    )
    install_dir = os.path.dirname(installer_path)

    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{_ps_single_quote(lnk)}')
$s.TargetPath = '{_ps_single_quote(installer_path)}'
$s.WorkingDirectory = '{_ps_single_quote(install_dir)}'
$s.IconLocation = '{_ps_single_quote(installer_path)},0'
$s.Save()
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            detail or f"PowerShell exited with code {proc.returncode}"
        )
    if not os.path.isfile(lnk):
        raise RuntimeError(f"Desktop shortcut was not created: {lnk}")
    return lnk


def launch_installer(installer_path: str, *, parent=None) -> None:
    installer_path = os.path.abspath(installer_path)
    if not os.path.isfile(installer_path):
        raise FileNotFoundError(installer_path)
    if parent is not None:
        for fn in ("destroy", "quit"):
            try:
                getattr(parent, fn)()
            except Exception:
                pass
    subprocess.Popen(
        [installer_path],
        cwd=os.path.dirname(installer_path),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    sys.exit(0)


def download_installer_exe(
    download_url: str,
    download_name: str = "",
    *,
    install_dir: Optional[str] = None,
    force: bool = False,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Download SatpudaCoreInstaller.exe to the install folder."""
    folder = os.path.abspath(install_dir or resolve_install_dir() or default_install_dir())
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, download_name or INSTALLER_EXE)
    if not force and os.path.isfile(dest) and os.path.getsize(dest) > 100 * 1024:
        return dest
    from core.github_updater import _USER_AGENT, _ssl_context
    import urllib.request

    req = urllib.request.Request(download_url, headers={"User-Agent": _USER_AGENT})
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
    if os.path.getsize(dest) < 100 * 1024:
        raise RuntimeError("Downloaded installer file is too small.")
    return dest


def ensure_installer_exe(
    download_url: str,
    download_name: str = "",
    install_dir: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> str:
    return download_installer_exe(
        download_url,
        download_name,
        install_dir=install_dir,
        force=False,
        progress_cb=progress_cb,
    )


def open_or_download_installer(
    download_url: str = "",
    download_name: str = "",
    *,
    install_dir: Optional[str] = None,
    parent=None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> None:
    existing = resolve_installer_exe(install_dir)
    if existing:
        launch_installer(existing, parent=parent)
        return
    if not download_url:
        raise RuntimeError(
            "Satpuda Core Installer was not found on this PC and "
            "SatpudaCoreInstaller.exe is not on the GitHub release."
        )
    folder = os.path.abspath(install_dir or resolve_install_dir() or default_install_dir())
    dest = ensure_installer_exe(
        download_url,
        download_name,
        install_dir=folder,
        progress_cb=progress_cb,
    )
    launch_installer(dest, parent=parent)


def reinstall_installer(
    download_url: str,
    download_name: str = "",
    *,
    install_dir: Optional[str] = None,
    create_shortcut: bool = True,
    open_after: bool = False,
    parent=None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[str, str, str]:
    """
    Force-download SatpudaCoreInstaller.exe to Programs\\Satpuda Core,
    recreate desktop shortcuts, optionally open the installer.

    Returns (installer_path, app_shortcut_path, installer_shortcut_path).
    """
    folder = os.path.abspath(install_dir or resolve_install_dir() or default_install_dir())
    os.makedirs(folder, exist_ok=True)
    dest = download_installer_exe(
        download_url,
        download_name,
        install_dir=folder,
        force=True,
        progress_cb=progress_cb,
    )
    desktop = _desktop_dir_for_shortcuts().lower()
    downloads = downloads_dir().lower()
    for old_path in search_installer_locations():
        old_folder = os.path.dirname(os.path.abspath(old_path))
        old_folder_low = old_folder.lower()
        if old_folder_low in (desktop, downloads) and old_folder_low != folder.lower():
            try:
                shutil.copy2(dest, os.path.join(old_folder, INSTALLER_EXE))
            except OSError:
                pass

    app_shortcut = ""
    installer_shortcut = ""
    if create_shortcut:
        app_dir = resolve_app_install_dir()
        if app_dir:
            app_shortcut = create_desktop_shortcut(app_dir)
        installer_shortcut = create_installer_desktop_shortcut(dest)

    save_installer_state(
        install_path=folder,
        install_root=folder,
        platform=platform_key(),
    )
    if open_after:
        launch_installer(dest, parent=parent)
    return dest, app_shortcut, installer_shortcut


def platform_label() -> str:
    return "Windows 7 / 8 / 8.1" if platform_key() == "win7" else "Windows 10 / 11"
