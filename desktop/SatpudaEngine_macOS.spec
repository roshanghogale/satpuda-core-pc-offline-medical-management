# -*- mode: python ; coding: utf-8 -*-
# Satpuda Core desktop data engine — macOS folder sidecar for the Tauri shell.
#
# Generated from SatpudaEngine_Folder.spec (the Windows one), which is left
# untouched. Differences are only the ones macOS forces:
#   * no .ico icon (PyInstaller wants .icns; the engine is headless anyway)
#   * core.windows_print_dialog dropped -- it imports win32print
#   * filter_system_dlls is a Windows DLL filter, so it is skipped
# No Tk/UI widgets. Gemini bill import + Firebase sync; voice omitted (lighter).

import os
import sys

# This spec lives in desktop/, but the project (and pyinstaller_extra_bundle,
# and every relative data path below) is the PARENT directory. Build from the
# project root: pyinstaller desktop/SatpudaEngine_macOS.spec
_PROJECT_ROOT = os.path.dirname(SPECPATH)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, SPECPATH)

try:
    import certifi

    _certifi_datas = [(certifi.where(), "certifi")]
except ImportError:
    _certifi_datas = []

from pyinstaller_extra_bundle import bundle_extras

_extra_datas, _extra_binaries, _extra_hidden = bundle_extras(
    include_heavy_ocr=False,
    include_whisper=False,
    include_voice=False,
    include_gemini=True,
    include_server_sync=True,
)


def _asset_datas():
    """Runtime brand/UI assets for the desktop API (logos, banners, status icons)."""
    assets_dir = os.path.join(_PROJECT_ROOT, "assets")
    if not os.path.isdir(assets_dir):
        return []
    keep_names = {
        "satpuda_logo.ico",
        "satpuda_logo.png",
        "home_banner.png",
        "home_banner2.png",
        "nirmalaui.ttf",
        "nirmalaui_bold.ttf",
        "logo 01.png",
        "logo 02.png",
        "logo 03.png",
        "logo 04.png",
    }
    skip_ext = {".xlsx", ".xls", ".pdf", ".doc", ".docx", ".zip", ".7z", ".db", ".dat"}
    skip_substrings = (
        "chatgpt",
        "mockup",
        "card design",
        "logo 001",
        "logo 002",
        "logo 003",
        "logo 004",
        "medicines_master",
    )
    out = []
    for name in os.listdir(assets_dir):
        path = os.path.join(assets_dir, name)
        if os.path.isfile(path):
            low = name.lower()
            ext = os.path.splitext(low)[1]
            if ext in skip_ext:
                continue
            if any(s in low for s in skip_substrings):
                continue
            if low not in keep_names and ext not in {".png", ".jpg", ".jpeg", ".ico", ".ttf", ".otf"}:
                continue
            if ext in {".png", ".jpg", ".jpeg", ".ico"} and low not in keep_names:
                continue
            out.append((path, "assets"))
        elif os.path.isdir(path) and name.lower() == "status":
            for sub in os.listdir(path):
                sub_path = os.path.join(path, sub)
                if os.path.isfile(sub_path):
                    out.append((sub_path, "assets/status"))
    return out


def _existing(pairs):
    """Drop data entries whose source path is absent.

    The Windows spec lists every optional config file. Some are only created on
    first run (config/app_mode.txt is one), and PyInstaller treats a missing
    source as a hard error -- so the build died on a file the app is perfectly
    happy to create for itself.
    """
    kept = []
    for src, dest in pairs:
        # Data paths in this spec are written relative to the project root, but
        # PyInstaller resolves them relative to the SPEC file, which now lives in
        # desktop/. Anchor them explicitly.
        full = src if os.path.isabs(src) else os.path.join(_PROJECT_ROOT, src)
        if os.path.exists(full):
            kept.append((full, dest))
        else:
            print(f"[spec] skipping absent optional data file: {src}")
    return kept


block_cipher = None

a = Analysis(
    [os.path.join(_PROJECT_ROOT, "run_desktop_api.py")],
    pathex=[_PROJECT_ROOT],
    datas=_existing([
        ("config/theme_config.txt", "config"),
        ("config/layout_config.txt", "config"),
        ("config/font_size.txt", "config"),
        ("config/sample_import.json", "config"),
        ("config/backup_creds.dat", "config"),
        ("config/backup_config.dat", "config"),
        ("config/expiry.dat", "config"),
        ("config/expiry_config.json", "config"),
        ("config/activation.dat", "config"),
        ("config/app_mode.txt", "config"),
        ("config/import_learned.json", "config"),
        ("config/bill_print_settings.json", "config"),
        ("config/build_profile_standard.txt", "config/build_profile.txt"),
        ("config/firebase_service_account.json", "config"),
        ("config/desktop_ui_prefs.json", "config"),
        ("config/record_indicators.json", "config"),
        ("config/printer_settings.json", "config"),
        ("config/gemini_bill_enabled.txt", "config"),
        ("config/gemini_tutor_enabled.txt", "config"),
        ("config/gemini_tutor_window_height.txt", "config"),
        ("docs/app_help", "docs/app_help"),
        ("web_app", "web_app"),
        ("bill_templates", "bill_templates"),
        ("oauth_client.json", "."),
        ("service_account.json", "."),
    ]
    )
    + _existing(_asset_datas())
    + _certifi_datas
    # bundle_extras() also returns paths relative to the project root, and the
    # Windows spec is left untouched, so anchor them here rather than there.
    + _existing(_extra_datas),
    binaries=_extra_binaries,
    hiddenimports=[
        # -- cryptography: MUST be bundled in every spec ------------------
        # _encrypt/_decrypt pick Fernet when cryptography imports and XOR
        # when it does not. A build that disagrees with the one that wrote
        # the file cannot read the store registry, licence or saved logins,
        # which is the 'settings wiped after replacing the build' bug.
        'cryptography', 'cryptography.fernet',
        'cryptography.hazmat', 'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.primitives.kdf',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.backends.openssl',
        # Live sync hints ride a websocket. Without this the frozen engine logs
        # "websocket-client not installed" on a loop and falls back to polling.
        "websocket",
        "websocket._app",
        "_sqlite3",
        "sqlite3",
        "threading",
        "queue",
        "core.desktop_api",
        "core.desktop_inventory_service",
        "core.desktop_sales_service",
        "core.desktop_purchase_service",
        "core.desktop_settings_service",
        "core.desktop_pages_service",
        "core.desktop_export_service",
        "core.desktop_returns_service",
        "core.desktop_general_products_service",
        "core.desktop_alert_service",
        "core.desktop_startup_service",
        "core.desktop_file_import_service",
        "core.desktop_mobile_import_service",
        "core.desktop_login_service",
        "core.desktop_license_service",
        "core.desktop_bundle_imports",
        "core.alert_monitoring_service",
        "core.stock_disposal_service",
        "core.online_guard",
        "core.desktop_sync_launch",
        "core.desktop_ui_prefs",
        "core.background_workers",
        "core.windows_print_dialog",
        "core.bill_output",
        # bill_output pulls _build_bill_context out of widgets/ at render
        # time. Only core.* was ever named here, so the frozen engine had
        # no widgets package at all and every bill print died with
        # ModuleNotFoundError: No module named 'widgets'.
        "core.bill_context",

        # Printing modules are imported lazily INSIDE functions
        # (bill_output.py:677 pulls dot_matrix_print + printer_manager at
        # call time), so name them explicitly rather than trusting static
        # analysis. If any is missed the bill silently fails to print.
        "core.dot_matrix_print",
        "core.printer_manager",
        "core.document_output",
        "core.bill_config",

        "core.build_features",
        "core.stock_rebuild",
        "core.brand_assets",
        "core.app_prefs",
        "core.home_dashboard",
        "core.frozen_appdata_setup",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "PIL._imaging",
    ]
    + _extra_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "scipy",
        "pandas",
        "tkinter",
        "ttkbootstrap",
        "_tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# filter_system_dlls() strips Windows system DLLs; a no-op target on macOS.

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SatpudaEngine",
    debug=False,
    strip=False,
    upx=False,
    # Headless sidecar: keep console=True on macOS. Windows hides the console
    # via CREATE_NO_WINDOW in the Tauri shell, but a windowed macOS build sends
    # stdout AND stderr to /dev/null -- the engine failed to bind its port and
    # said nothing at all, which is impossible to support in the field.
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="SatpudaEngine",
)
