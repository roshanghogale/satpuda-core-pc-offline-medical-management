"""Seed %LOCALAPPDATA%\\VeterinaryApp from a frozen PyInstaller bundle (classic + Tauri engine)."""
from __future__ import annotations

import os
import shutil
import sys

from core.frozen_bootstrap import prepare_frozen_runtime


def _appdata_dir() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "VeterinaryApp",
    )


def setup_frozen_appdata(*, chdir: bool = True) -> str:
    """
    Copy bundled config/secrets into AppData when missing (first install only).
    Returns the AppData folder path. No-op when not frozen.
    """
    if not getattr(sys, "frozen", False):
        return _appdata_dir()

    prepare_frozen_runtime()

    app_data = _appdata_dir()
    os.makedirs(app_data, exist_ok=True)
    base_dir = sys._MEIPASS
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    seed_files = [
        "theme_config.txt",
        "layout_config.txt",
        "font_size.txt",
        "expiry.dat",
        "expiry_config.json",
        "backup_creds.dat",
        "sample_import.json",
        "backup_config.dat",
        "backup_slots.dat",
        "app_mode.txt",
        "import_default_schedule.txt",
        "sync_mode.txt",
        "import_learned.json",
        "master_medicine.db",
        "bill_print_settings.json",
        "desktop_ui_prefs.json",
        "printer_settings.json",
        "record_indicators.json",
        "activation.dat",
    ]

    try:
        from core.build_features import (
            is_server_sync_supported,
            is_gemini_supported,
            is_voice_supported,
        )

        if is_server_sync_supported():
            seed_files.append("server_service_account.json")
        if is_voice_supported():
            seed_files.extend(
                [
                    "voice_assistant_enabled.txt",
                    "voice_assistant_name.txt",
                    "voice_auto_start_mic.txt",
                    "voice_language.txt",
                    "voice_mic_device.txt",
                ]
            )
        if is_gemini_supported():
            seed_files.extend(
                [
                    "gemini_bill_enabled.txt",
                    "gemini_api_key.txt",
                    "gemini_tutor_enabled.txt",
                    "gemini_tutor_window_height.txt",
                ]
            )
    except Exception:
        seed_files.extend(
            [
                "server_service_account.json",
                "gemini_bill_enabled.txt",
                "gemini_api_key.txt",
                "gemini_tutor_enabled.txt",
            ]
        )

    for fname in seed_files:
        dst = os.path.join(app_data, fname)
        if os.path.exists(dst):
            continue
        src_config = os.path.join(base_dir, "config", fname)
        src_root = os.path.join(base_dir, fname)
        src = src_config if os.path.exists(src_config) else src_root
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif fname in ("theme_config.txt", "font_size.txt", "layout_config.txt"):
            defaults = {
                "theme_config.txt": "navy-light",
                "font_size.txt": "10",
                "layout_config.txt": "{}",
            }
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(defaults.get(fname, ""))

    # Master DB: copy bundled catalog when AppData copy is missing or empty stub.
    bundled_master = os.path.join(base_dir, "config", "master_medicine.db")
    if not os.path.isfile(bundled_master):
        bundled_master = os.path.join(base_dir, "master_medicine.db")
    app_master = os.path.join(app_data, "master_medicine.db")
    if os.path.isfile(bundled_master):
        try:
            bundled_size = os.path.getsize(bundled_master)
            if bundled_size > 1024 and (
                not os.path.isfile(app_master)
                or os.path.getsize(app_master) < 1024
            ):
                shutil.copy2(bundled_master, app_master)
        except OSError:
            pass

    try:
        from core.layout_config import repair_layout_config_file

        repair_layout_config_file()
    except Exception:
        pass

    try:
        from core.master_medicine_service import ensure_master_db_ready

        ensure_master_db_ready()
    except Exception:
        pass

    try:
        from core.backup_manager import seed_bundled_backup_files

        seed_bundled_backup_files()
    except Exception:
        pass

    try:
        from core.web_purchase_server import prepare_web_purchase_root

        prepare_web_purchase_root()
    except Exception:
        pass

    if chdir:
        os.chdir(app_data)

    return app_data
