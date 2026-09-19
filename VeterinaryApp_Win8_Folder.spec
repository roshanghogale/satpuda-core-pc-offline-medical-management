# -*- mode: python ; coding: utf-8 -*-
# SatpudaCore Win8 folder build — Windows 8 / 8.1 (Python 3.8, 32-bit).
# No voice, no Gemini, no bill photo import (lighter runtime for older Windows).
# Build with:  py -3.8-32 -m PyInstaller VeterinaryApp_Win8_Folder.spec --noconfirm

# A release build must not carry one shop's identity -- their Drive backup
# folder, the developer's printer, a counter's learned distributor names.
# That rule lived only in SatpudaEngine_Folder.spec, so every build made from
# THIS spec shipped all of it to whoever installed it. One shared definition:
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.abspath(SPECPATH))
from build_release_filter import release_datas as _release_datas


import sys
sys.path.insert(0, SPECPATH)
from pyinstaller_tk_bundle import tcl_tk_datas_and_binaries

_tcl_datas, _tcl_binaries = tcl_tk_datas_and_binaries()

try:
    import certifi
    _certifi_datas = [(certifi.where(), 'certifi')]
except ImportError:
    _certifi_datas = []

from pyinstaller_extra_bundle import bundle_extras
_extra_datas, _extra_binaries, _extra_hidden = bundle_extras(
    include_heavy_ocr=False,
    include_whisper=False,
    include_voice=False,
    include_gemini=False,
)

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    datas=_release_datas([
        ('config/theme_config.txt',    'config'),
        ('config/layout_config.txt',   'config'),
        ('config/font_size.txt',       'config'),
        ('config/sample_import.json',  'config'),
        ('config/backup_creds.dat',    'config'),
        ('config/drive_backup_folder.dat', 'config'),
        ('config/backup_config.dat',   'config'),
        ('config/backup_slots.dat',    'config'),
        ('config/expiry.dat',          'config'),
        ('config/activation.dat',      'config'),
        ('config/app_mode.txt',         'config'),
        ('config/import_learned.json',  'config'),
        ('config/bill_print_settings.json', 'config'),
        ('config/build_profile_win8.txt', 'config/build_profile.txt'),
        ('config/firebase_service_account.json', 'config'),
        ('assets',                     'assets'),
        ('web_app',                    'web_app'),
        ('oauth_client.json',          '.'),
        ('service_account.json',       '.'),
    ]) + _tcl_datas + _certifi_datas + _extra_datas,
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
    ] + _extra_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_tkinter_win.py', 'rthook_windows_icon.py'],
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
    name='SatpudaCore_Win8',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch='x86',
    icon='assets/satpuda_logo.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='SatpudaCore_Win8',
)
