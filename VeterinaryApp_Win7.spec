# -*- mode: python ; coding: utf-8 -*-
# Single-file EXE — Windows 7 / 8 / 8.1  (Python 3.8, 32-bit)
# Build with:  py -3.8-32 -m PyInstaller VeterinaryApp_Win7.spec --noconfirm

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('config/theme_config.txt',   '.'),
        ('config/layout_config.txt',  '.'),
        ('config/font_size.txt',      '.'),
        ('config/sample_import.json', '.'),
        ('config/backup_creds.dat',   'config'),
        ('assets',          'assets'),
        ('web_app/index.html', 'web_app'),
        ('core',            'core'),
        ('ui',              'ui'),
        ('widgets',         'widgets'),
    ],
    hiddenimports=[
        # ttkbootstrap
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
        # Pillow
        'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL.ImageDraw', 'PIL.ImageFont',
        'PIL._imaging', 'PIL._imagingtk', 'PIL.ImageColor',
        'PIL.ImageFilter', 'PIL.ImageOps',
        # stdlib
        'sqlite3', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox',
        'tkinter.filedialog', 'tkinter.simpledialog',
        'csv', 'json', 'shutil', 'tempfile', 'math', 'datetime',
        'hashlib', 'subprocess', 'uuid', 'webbrowser', 'base64',
        'threading', 'logging', 're',
        # cryptography
        'cryptography', 'cryptography.fernet',
        'cryptography.hazmat', 'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.primitives.kdf',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.backends.openssl',
        # openpyxl
        'openpyxl', 'openpyxl.styles', 'openpyxl.utils',
        'openpyxl.writer.excel', 'et_xmlfile',
        # reportlab
        'reportlab', 'reportlab.lib', 'reportlab.lib.pagesizes',
        'reportlab.lib.colors', 'reportlab.lib.styles',
        'reportlab.lib.units', 'reportlab.platypus', 'reportlab.pdfgen',
        # Google Drive backup
        'googleapiclient', 'googleapiclient.discovery', 'googleapiclient.http',
        'google.auth', 'google.oauth2', 'google.oauth2.service_account',
        'google.auth.transport.requests',
        # App modules
        'core.alert_colors', 'core.customer_service', 'core.font_config',
        'core.font_updater', 'core.input_controller', 'core.layout_config',
        'core.scroll_manager', 'core.export_manager', 'core.license_manager',
        'ui.billing', 'ui.customers', 'ui.import_purchases', 'ui.inventory',
        'ui.purchase', 'ui.purchase_history', 'ui.sales_history',
        'ui.settings', 'ui.shelf_management',
        'widgets.activation_dialog', 'widgets.bill_edit', 'widgets.bill_preview',
        'widgets.searchable_combo', 'widgets.two_step_medicine_combo',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'scipy', 'wx', 'PyQt5', 'PyQt6'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SatpudaCore_Win7',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,          # UPX compression reduces file size — helps on older systems
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x86',  # 32-bit — required for Windows 7 compatibility
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/satpuda_logo.ico',
    version_info={
        'version': (1, 0, 0, 0),
        'company_name': 'Satpuda Medical',
        'file_description': 'Satpuda Core — Billing. Management. Simplified.',
        'internal_name': 'SatpudaCore',
        'legal_copyright': 'Satpuda Medical',
        'original_filename': 'SatpudaCore_Win7.exe',
        'product_name': 'Satpuda Core',
        'product_version': '1.0.0.0',
    },
)
