# -*- mode: python ; coding: utf-8 -*-
# SatpudaCore — complete single-file EXE build spec  (Windows 8 / 10 / 11, 64-bit)

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
_extra_datas, _extra_binaries, _extra_hidden = bundle_extras(include_heavy_ocr=False)

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    datas=_release_datas([
        # ── Config files (copied to AppData on first run) ──────────────────
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
        ('config/firebase_service_account.json', 'config'),
        # ── Assets (images, fonts, icons, Excel master) ────────────────────
        ('assets',                     'assets'),
        ('assets/NirmalaUI.ttf',        'assets'),
        ('assets/NirmalaUI_Bold.ttf',   'assets'),
        # ── Web app ────────────────────────────────────────────────────────
        ('web_app',                    'web_app'),
        # Source is compiled into the EXE archive — do NOT bundle core/ui/widgets
        # as datas (that extracts readable .py files into %TEMP%\_MEI* on every PC).
        # ── OAuth / service account credentials ────────────────────────────
        ('oauth_client.json',          '.'),
        ('service_account.json',       '.'),
    ]) + _tcl_datas + _certifi_datas + _extra_datas,
    binaries=_tcl_binaries + _extra_binaries,
    hiddenimports=[
        '_tkinter',
        # ── ttkbootstrap ───────────────────────────────────────────────────
        'ttkbootstrap',
        'ttkbootstrap.constants',
        'ttkbootstrap.style',
        'ttkbootstrap.themes',
        'ttkbootstrap.themes.standard',
        'ttkbootstrap.widgets',
        'ttkbootstrap.dialogs',
        'ttkbootstrap.dialogs.dialogs',
        'ttkbootstrap.scrolled',
        'ttkbootstrap.tableview',
        'ttkbootstrap.tooltip',
        'ttkbootstrap.validation',
        'ttkbootstrap.localization',
        # ── Pillow ─────────────────────────────────────────────────────────
        'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL.ImageDraw', 'PIL.ImageFont',
        'PIL._imaging', 'PIL._imagingtk', 'PIL.ImageColor',
        'PIL.ImageFilter', 'PIL.ImageOps', 'PIL.ImageEnhance',
        # ── stdlib ─────────────────────────────────────────────────────────
        '_sqlite3', 'sqlite3', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox',
        'tkinter.filedialog', 'tkinter.simpledialog', 'tkinter.colorchooser',
        'csv', 'json', 'shutil', 'tempfile', 'math', 'datetime',
        'hashlib', 'subprocess', 'uuid', 'webbrowser', 'base64',
        'threading', 'logging', 're', 'os', 'sys', 'io',
        'collections', 'functools', 'itertools', 'copy',
        'pathlib', 'glob', 'zipfile',
        # ── cryptography ───────────────────────────────────────────────────
        'cryptography', 'cryptography.fernet',
        'cryptography.hazmat', 'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.primitives.kdf',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.backends.openssl',
        'certifi',
        # ── openpyxl ───────────────────────────────────────────────────────
        'openpyxl', 'openpyxl.styles', 'openpyxl.utils',
        'openpyxl.writer.excel', 'openpyxl.reader.excel',
        'openpyxl.workbook', 'openpyxl.worksheet',
        'et_xmlfile',
        # ── reportlab ──────────────────────────────────────────────────────
        'reportlab', 'reportlab.lib', 'reportlab.lib.pagesizes',
        'reportlab.lib.colors', 'reportlab.lib.styles',
        'reportlab.lib.units', 'reportlab.platypus', 'reportlab.pdfgen',
        'reportlab.pdfgen.canvas', 'reportlab.platypus.tables',
        'reportlab.platypus.flowables', 'reportlab.platypus.paragraph',
        # ── Google Drive backup ────────────────────────────────────────────
        'googleapiclient', 'googleapiclient.discovery', 'googleapiclient.http',
        'google.auth', 'google.oauth2', 'google.oauth2.service_account',
        'google.auth.transport.requests',
        'google_auth_httplib2',
        'httplib2',
        # ── core modules ───────────────────────────────────────────────────
        'core',
        'core.alert_colors',
        'core.app_setup',
        'core.backup_manager',
        'core.app_version',
        'core.ssl_utils',
        'core.github_updater',
        'core.billing_service',
        'core.calc_engine',
        'core.custom_themes',
        'core.customer_service',
        'core.db_setup',
        'core.export_manager',
        'core.font_config',
        'core.font_updater',
        'core.input_controller',
        'core.layout_config',
        # Imported inside functions (get_home_banner_path, the logo
        # resolver) and every call site swallows ImportError, so a missing
        # module looks exactly like 'the shop has no picture'.
        'core.store_images',
        'core.license_manager',
        'core.window_icon',
        'core.purchase_calculator',
        'core.purchase_importer',
        'core.purchase_invoice_engine',
        'core.purchase_service',
        'core.web_purchase_server',
        'core.mobile_import_server',
        'qrcode', 'qrcode.main', 'qrcode.image.pil', 'qrcode.constants',
        'core.web_purchase_save',
        'core.general_product_service',
        'core.column_config',
        'core.master_medicine_service',
        'core.medicine_type_detector',
        'core.startup_alerts',
        'core.scroll_manager',
        'core.themed_messagebox',
        # ── ui modules ─────────────────────────────────────────────────────
        'ui',
        'ui.billing',
        'ui.billing.billing',
        'ui.billing.billing_form',
        'ui.billing.billing_nav',
        'ui.billing.bill_edit',
        'ui.inventory',
        'ui.inventory.inventory',
        'ui.inventory.inventory_dialogs',
        'ui.purchase',
        'ui.purchase.purchase',
        'ui.purchase.purchase_form',
        'ui.purchase.purchase_nav',
        'ui.purchase.purchase_history',
        'ui.purchase.purchase_history_edit',
        'ui.returns',
        'ui.returns.sales_return',
        'ui.returns.purchase_return',
        'ui.sales',
        'ui.sales.sales_history',
        'ui.sales.sales_history_actions',
        'ui.sales.sales_history_exports',
        'ui.settings',
        'ui.settings.settings',
        'ui.settings.import_purchase_dialog',
        'ui.settings.settings_tabs',
        'ui.settings.settings_tabs.database_tab',
        'ui.settings.settings_tabs.doctors_tab',
        'ui.settings.settings_tabs.layout_tab',
        'ui.settings.settings_tabs.ledger_tab',
        'ui.settings.settings_tabs.misc_tabs',
        'ui.settings.settings_tabs.payment_tab',
        'ui.settings.settings_tabs.pharmacy_tab',
        'ui.settings.settings_tabs.suppliers_tab',
        'ui.settings.settings_tabs.customer_payment_tab',
        'ui.settings.settings_tabs.appearance_scroll',
        'ui.settings.settings_tabs.contacts_tab',
        'ui.settings.settings_tabs.updates_tab',
        'ui.settings.settings_tabs.payment_combined_tab',
        'ui.general_products',
        'ui.general_products.general_products_page',
        'ui.shared',
        'ui.shared.customers',
        'ui.shared.home_page',
        'ui.shared.import_from_mobile',
        'ui.shared.import_purchases',
        'ui.shared.shelf_management',
        # ── widgets ────────────────────────────────────────────────────────
        'widgets',
        'widgets.activation_dialog',
        'widgets.bill_edit',
        'widgets.bill_preview',
        'core.bill_config',
        'core.bill_page_config', 'core.bill_print_settings',
        'core.bill_render_utils',
        'bill_templates', 'bill_templates.classic', 'bill_templates.legacy',
        'widgets.searchable_combo',
        'widgets.two_step_medicine_combo',
    ] + _extra_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_tkinter_win.py', 'rthook_windows_icon.py'],
    excludes=['matplotlib', 'pandas', 'scipy', 'wx', 'PyQt5', 'PyQt6',
              'IPython', 'notebook', 'pytest'],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SatpudaCore',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/satpuda_logo.ico',
    version_info={
        'version': (1, 0, 3, 0),
        'company_name': 'Satpuda Medical',
        'file_description': 'Satpuda Core — Billing. Management. Simplified.',
        'internal_name': 'SatpudaCore',
        'legal_copyright': 'Satpuda Medical',
        'original_filename': 'SatpudaCore.exe',
        'product_name': 'Satpuda Core',
        'product_version': '1.0.3.0',
    },
)
