# -*- mode: python ; coding: utf-8 -*-
# Satpuda Core desktop data engine — PyInstaller folder sidecar for Tauri shell.
# No Tk/UI widgets. Gemini bill import + Firebase sync; voice omitted (lighter).

import os
import sys

sys.path.insert(0, SPECPATH)

try:
    import certifi

    _certifi_datas = [(certifi.where(), "certifi")]
except ImportError:
    _certifi_datas = []

from pyinstaller_extra_bundle import (
    bundle_excludes,
    bundle_extras,
    filter_system_dlls,
    prune_discovery_documents,
)

_FEATURES = dict(
    include_heavy_ocr=False,
    include_whisper=False,
    include_voice=False,
)

_extra_datas, _extra_binaries, _extra_hidden = bundle_extras(
    include_gemini=True,
    include_server_sync=True,
    **_FEATURES,
)

# The same flags now also say what must be kept OUT. They used to filter
# hiddenimports only, which the shipped engine showed is not enough: the module
# graph still walked core.voice -> faster_whisper and collected 165 MB of
# speech runtime into a build with include_whisper=False. See bundle_excludes.
#
# Measured on the shipped Win10 engine (635.5 MB), this drops:
#   cv2 111.3  av.libs 62.6  ctranslate2 58.8  onnxruntime 34.6
#   hf_xet 9.1  tokenizers 7.2  sounddevice+pyttsx3 0.6
# and prune_discovery_documents below takes another ~99 MB off googleapiclient.
# What STAYS, deliberately, because the product needs it: googleapiclient
# (Drive backup), the bundled master medicine DB, numpy, grpc, cryptography,
# PIL and pdfplumber.
_extra_excludes = bundle_excludes(
    include_whisper=_FEATURES["include_whisper"],
    include_voice=_FEATURES["include_voice"],
    # The PDF table parsers behind pdfplumber (camelot, tabula-py) both need
    # pandas, which is excluded below -- so they have never been able to run in
    # a frozen build. Saying so here stops camelot dragging opencv in with it.
    include_pdf_table_parsers=False,
)
print("[spec] excluding: " + ", ".join(sorted(_extra_excludes)))


def _asset_datas():
    """Runtime brand/UI assets for the desktop API (logos, banners, status icons)."""
    assets_dir = os.path.join(SPECPATH, "assets")
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


# ── What a build is allowed to carry ─────────────────────────────────────────
#
# A build handed to shop B must not contain shop A's identity, and no build at
# all should contain a live credential. One did: it shipped another shop's Drive
# backup folder, that shop's learned distributor names, and this developer
# machine's printer and bill-output path.
#
# CLEAN is the default on purpose. Forgetting the flag must give the SAFE build,
# never the leaky one. Set SATPUDA_BUILD=paired to make a build for one named
# shop, and say which shop it is in config/store_backup.build.
# The rule about what a release may carry lives in build_release_filter.py so
# that the six classic Tk specs obey the same one. It used to live only here,
# which is exactly why they did not.
sys.path.insert(0, os.path.abspath(SPECPATH))
from build_release_filter import (  # noqa: E402
    CLEAN_RELEASE as _CLEAN_RELEASE,
    SHOP_IDENTITY as _SHOP_IDENTITY,
    VENDOR_CREDENTIALS as _VENDOR_CREDENTIALS,
    clean_release as _clean_release,
    existing as _existing,
)


block_cipher = None

a = Analysis(
    ["run_desktop_api.py"],
    pathex=["."],
    datas=_existing(_clean_release([
        ("config/theme_config.txt", "config"),
        ("config/layout_config.txt", "config"),
        ("config/font_size.txt", "config"),
        ("config/sample_import.json", "config"),
        ("config/backup_creds.dat", "config"),
        ("config/drive_backup_folder.dat", "config"),
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
    ])
    )
    + _asset_datas()
    + _certifi_datas
    + _existing(_clean_release(_extra_datas)),
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
        # Same trap, one level down: bill_context, layout_config and
        # pharmacy_profile_io all reach for core.store_images INSIDE a
        # function, and every one of those calls is wrapped in try/except
        # so a missing module would not raise -- it would silently print a
        # bill with no logo and show the built-in banner. That is the bug
        # this module exists to fix, so name it here.
        "core.store_images",

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
    ]
    + _extra_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

a.binaries = filter_system_dlls(a.binaries)

# Drive is the only Google API this app builds a service for. Keep its
# discovery document, drop the other 598.
a.datas = prune_discovery_documents(a.datas)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SatpudaEngine",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/satpuda_logo.ico",
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
