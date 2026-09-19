# -*- mode: python ; coding: utf-8 -*-
# SatpudaCore Win10/11 folder build — Windows 10 & 11 (64-bit, Python 3.13).
# Faster startup (no one-file extract). Full voice + Gemini bill import.
#
# Slim datas: do NOT Tree the whole assets/ or project root (avoids shipping
# 65MB master Excel, PDFs, design mockups). Master DB is enough for medical mode.

# A release build must not carry one shop's identity -- their Drive backup
# folder, the developer's printer, a counter's learned distributor names.
# That rule lived only in SatpudaEngine_Folder.spec, so every build made from
# THIS spec shipped all of it to whoever installed it. One shared definition:
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.abspath(SPECPATH))
from build_release_filter import release_datas as _release_datas


import sys
import os
sys.path.insert(0, SPECPATH)
from pyinstaller_tk_bundle import tcl_tk_datas_and_binaries

_tcl_datas, _tcl_binaries = tcl_tk_datas_and_binaries()

try:
    import certifi
    _certifi_datas = [(certifi.where(), 'certifi')]
except ImportError:
    _certifi_datas = []

from pyinstaller_extra_bundle import bundle_extras
_extra_datas, _extra_binaries, _extra_hidden = bundle_extras(include_heavy_ocr=False)


def _asset_datas():
    """Only runtime brand/UI assets — skip Excel/PDF/design leftovers."""
    assets_dir = os.path.join(SPECPATH, 'assets')
    if not os.path.isdir(assets_dir):
        return []
    # Keep logos, banners, fonts, icon. Drop master Excel (master_medicine.db is bundled),
    # PDFs, and one-off design exports that are not referenced by the app.
    keep_names = {
        'satpuda_logo.ico',
        'satpuda_logo.png',
        'home_banner.png',
        'home_banner2.png',
        'nirmalaui.ttf',
        'nirmalaui_bold.ttf',
        'logo 01.png',
        'logo 02.png',
        'logo 03.png',
        'logo 04.png',
    }
    skip_ext = {'.xlsx', '.xls', '.pdf', '.doc', '.docx', '.zip', '.7z', '.db', '.dat'}
    skip_substrings = (
        'chatgpt', 'mockup', 'card design', 'logo 001', 'logo 002',
        'logo 003', 'logo 004', 'medicines_master',
    )
    out = []
    for name in os.listdir(assets_dir):
        path = os.path.join(assets_dir, name)
        if not os.path.isfile(path):
            continue
        low = name.lower()
        ext = os.path.splitext(low)[1]
        if ext in skip_ext:
            continue
        if any(s in low for s in skip_substrings):
            continue
        if low not in keep_names and ext not in {'.png', '.jpg', '.jpeg', '.ico', '.ttf', '.otf'}:
            continue
        # Prefer explicit keep list for images; still allow listed fonts/icons.
        if ext in {'.png', '.jpg', '.jpeg', '.ico'} and low not in keep_names:
            continue
        out.append((path, 'assets'))
    return out


# Sumatra is copied next to the EXE by build_win10_folder.bat (dist/.../tools/).
# Do not also embed under _internal/tools (duplicate ~20MB).

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    datas=_release_datas([
        ('config/theme_config.txt',    'config'),
        ('config/layout_config.txt',   'config'),
        ('config/font_size.txt',       'config'),
        ('config/sample_import.json',  'config'),
        # Drive backup OAuth seeds (small). Skip backup_slots.dat — created at runtime.
        ('config/backup_creds.dat',    'config'),
        ('config/drive_backup_folder.dat', 'config'),
        ('config/backup_config.dat',   'config'),
        ('config/expiry.dat',          'config'),
        ('config/expiry_config.json',  'config'),
        ('config/activation.dat',      'config'),
        ('config/app_mode.txt',         'config'),
        ('config/import_learned.json',  'config'),
        ('docs/app_help',              'docs/app_help'),
        ('config/bill_print_settings.json', 'config'),
        ('config/build_profile_standard.txt', 'config/build_profile.txt'),
        ('config/firebase_service_account.json', 'config'),
        ('web_app',                    'web_app'),
        ('oauth_client.json',          '.'),
        ('service_account.json',       '.'),
    ]) + _asset_datas() + _tcl_datas + _certifi_datas + _extra_datas,
    binaries=_tcl_binaries + _extra_binaries,
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
        '_tkinter', 'ttkbootstrap', 'ttkbootstrap.constants', 'ttkbootstrap.style',
        'ttkbootstrap.themes', 'ttkbootstrap.themes.standard', 'ttkbootstrap.widgets',
        'ttkbootstrap.dialogs', 'ttkbootstrap.dialogs.dialogs', 'ttkbootstrap.scrolled',
        'ttkbootstrap.tableview', 'ttkbootstrap.tooltip', 'ttkbootstrap.validation',
        'ttkbootstrap.localization',
        'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL.ImageDraw', 'PIL.ImageFont',
        'PIL._imaging', 'PIL._imagingtk', 'PIL.ImageColor', 'PIL.ImageWin',
        '_sqlite3', 'sqlite3', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox',
        'tkinter.filedialog', 'threading', 'queue',
        'core.background_workers', 'core.windows_print_dialog', 'core.bill_output',
        'core.store_images',
        'core.build_features',
        'core.stock_rebuild',
        'core.frozen_appdata_setup',
    ] + _extra_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

from pyinstaller_extra_bundle import filter_system_dlls
a.binaries = filter_system_dlls(a.binaries)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SatpudaCore_Win10',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon='assets/satpuda_logo.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='SatpudaCore_Win10',
)
