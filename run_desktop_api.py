"""
Start the local Python API used by the Tauri desktop UI.

Usage (from repo root):
  python run_desktop_api.py

Then in another terminal:
  cd desktop
  npm run tauri dev
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

# Match main.py: point HTTPS at a real CA bundle before any request is made.
try:
    from core.ssl_utils import configure_ssl_certificates
    configure_ssl_certificates()
except Exception:
    pass

def _repo_root() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # Tauri standalone: .../SatpudaCore_Desktop_Win10/engine/SatpudaEngine.exe
        parent = os.path.dirname(exe_dir)
        if (
            os.path.basename(exe_dir).lower() == "engine"
            and os.path.isfile(os.path.join(parent, "SatpudaCore_Desktop.exe"))
        ):
            return parent
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


ROOT = _repo_root()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _ensure_python_310() -> None:
    """Tauri `py -3` can launch 3.8; PEP 604 `X | None` types need 3.10+."""
    if getattr(sys, "frozen", False) or sys.version_info >= (3, 10):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    here = os.path.abspath(__file__)
    for ver in ("Python313", "Python312", "Python311", "Python310"):
        exe = os.path.join(local, "Programs", "Python", ver, "python.exe")
        if not os.path.isfile(exe):
            continue
        if os.path.normcase(os.path.abspath(exe)) == os.path.normcase(
            os.path.abspath(sys.executable)
        ):
            continue
        print(
            f"Re-launching desktop engine with {exe} "
            f"(need 3.10+, this is {sys.version.split()[0]})",
            flush=True,
        )
        raise SystemExit(subprocess.call([exe, here, *sys.argv[1:]]))
    print(
        f"ERROR: Desktop engine needs Python 3.10 or newer. "
        f"This process is {sys.version.split()[0]}. "
        f"Install Python 3.13 or run: py -3.13 run_desktop_api.py",
        flush=True,
    )
    raise SystemExit(1)


def main() -> int:
    _ensure_python_310()
    if getattr(sys, "frozen", False):
        from core.frozen_appdata_setup import setup_frozen_appdata

        setup_frozen_appdata(chdir=True)
    else:
        dev_root = os.path.dirname(os.path.abspath(__file__))
        os.chdir(dev_root)

    try:
        from core.layout_config import repair_layout_config_file

        repair_layout_config_file()
    except Exception:
        pass

    from core.desktop_bundle_imports import preload_desktop_services

    # Import heavy modules off the listen path so /api/health can bind sooner.
    threading.Thread(
        target=preload_desktop_services,
        daemon=True,
        name="PreloadDesktop",
    ).start()

    from core.store_manager import (
        ensure_registry_on_startup,
        ensure_startup_migration,
        get_active_db_path,
    )
    from core.sync_prefs import is_online_mode

    try:
        ensure_registry_on_startup()
        ensure_startup_migration()
    except Exception as exc:
        print(f"Store init warning: {exc}")

    try:
        from core.online_guard import start_connectivity_monitor

        start_connectivity_monitor()
    except Exception:
        pass

    if is_online_mode():
        # Same as main.py _init_database: Online is server-only.
        # Do not require (or open) stores/<key>/veterinary.db.
        # Migrate gate also runs inside start_desktop_api — do not duplicate it here.
        print("Online mode: server-only (local store DB ignored)", flush=True)
        print(f"Active store path (unused): {get_active_db_path()}", flush=True)
    else:
        db_path = get_active_db_path()
        print(f"Active DB: {db_path}", flush=True)
        if not os.path.isfile(db_path):
            try:
                from core.store_manager import ensure_active_store_db_exists

                db_path = ensure_active_store_db_exists()
                print(f"Created offline store DB: {db_path}", flush=True)
            except Exception as exc:
                print(
                    "ERROR: Database file not found. Open the Tk app once "
                    f"to create a store, then retry. ({exc})",
                    flush=True,
                )
                return 1

    from core.desktop_api import serve_forever_blocking

    serve_forever_blocking()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
