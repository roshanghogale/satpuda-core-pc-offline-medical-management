"""
backup_manager.py
Silent background Google Drive backup using OAuth2 refresh token.
Files are uploaded to YOUR personal Gmail Drive (15GB free quota).
Store owner sees nothing - completely silent background operation.
Backup runs every 1 hour while app is open.
"""

import os
import sys
import json
import gzip
import shutil
import socket
import hashlib
import base64
import tempfile
import threading
import logging
from datetime import datetime

_BACKUP_SECRET_STATIC = b'SatpudaCoreBackupSecret_2026'

def _legacy_machine_secret() -> bytes:
    """Legacy machine-bound secret kept only for backward decryption compatibility."""
    try:
        import uuid
        machine_id = str(uuid.getnode()).encode()
    except Exception:
        machine_id = b'SatpudaVetApp'
    return machine_id + b'_SatpudaCoreVet2026'

def _get_secret() -> bytes:
    """Primary backup secret (portable across devices)."""
    return _BACKUP_SECRET_STATIC
_MAX_BACKUPS = 5
_last_backup_time = None   # datetime of last successful backup
_DEDUP_MINUTES    = 5      # skip on-open backup if app was just closed within this window

# Today's protected slots — persisted to backup_slots.dat
# open1        : first backup of the day - first app open (set once, never overwritten)
# hourly_first : first hourly of the day (set once, never overwritten)
# hourly_last  : most recent hourly backup (always updated)
# close_last   : most recent close backup  (always updated)
# Only TODAY's files are ever candidates for mid-day cleanup.
# All other days are untouched unless older than 3 years.
_slots: dict = {}

def _slots_path():
    try:
        from core.store_manager import get_active_slots_path
        return get_active_slots_path()
    except Exception:
        from core.license_manager import _appdata_dir
        return os.path.join(_appdata_dir(), 'backup_slots.dat')


def reload_slots_for_active_store():
    """Reload backup slot state after switching stores."""
    global _slots
    _load_slots()

def _load_slots():
    global _slots
    try:
        path = _slots_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                _slots = json.load(f)
    except Exception:
        _slots = {}

def _save_slots():
    try:
        with open(_slots_path(), 'w', encoding='utf-8') as f:
            json.dump(_slots, f)
    except Exception:
        pass

def _reset_slots_for_today():
    today = datetime.now().strftime('%Y-%m-%d')
    _slots['date']         = today
    _slots['open1']        = None
    _slots['hourly_first'] = None
    _slots['hourly_last']  = None
    _slots['close_last']   = None
    _save_slots()

def _protected_filenames() -> set:
    """Return filenames that must never be deleted during today's cleanup."""
    return {v for k, v in _slots.items() if k != 'date' and v}

def _register_filename(filename: str, trigger: str):
    """Assign filename to the correct slot.
    Resets slots if the filename's date differs from the stored date (new day)."""
    try:
        file_date = filename.split('_')[1]   # SatpudaCore_YYYY-MM-DD_HH-MM.db.gz
    except Exception:
        file_date = datetime.now().strftime('%Y-%m-%d')

    if _slots.get('date') != file_date:
        _slots['date']         = file_date
        _slots['open1']        = None
        _slots['hourly_first'] = None
        _slots['hourly_last']  = None
        _slots['close_last']   = None

    if trigger == 'open':
        if not _slots.get('open1'):
            _slots['open1'] = filename
            _logger.info(f"Slot open1 = {filename}")
    elif trigger == 'hourly':
        if not _slots.get('hourly_first'):
            _slots['hourly_first'] = filename
            _logger.info(f"Slot hourly_first = {filename}")
        _slots['hourly_last'] = filename
        _logger.info(f"Slot hourly_last = {filename}")
    elif trigger == 'close':
        _slots['close_last'] = filename
        _logger.info(f"Slot close_last = {filename}")
    elif trigger == 'manual':
        # Protect today's manual Backup Now the same way as close.
        _slots['close_last'] = filename
        _logger.info(f"Slot manual/close_last = {filename}")

    _save_slots()

# Logging (silent file log - never shown in UI)
def _log_path():
    from core.license_manager import _appdata_dir
    return os.path.join(_appdata_dir(), 'backup_log.txt')

def _setup_logger():
    logger = logging.getLogger('satpuda_backup')
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        from core.log_policy import should_write_logs
        if not should_write_logs():
            return logger
    except Exception:
        pass
    try:
        fh = logging.FileHandler(_log_path(), encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s  %(levelname)s  %(message)s',
                                          datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(fh)
    except Exception:
        pass
    return logger

_logger = _setup_logger()

# Load persisted slots after logger is ready
_load_slots()


# Paths
def _bundled_creds_path():
    if getattr(sys, 'frozen', False):
        p = os.path.join(sys._MEIPASS, 'config', 'backup_creds.dat')
        if os.path.exists(p):
            return p
    p = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config', 'backup_creds.dat',
    )
    return p if os.path.exists(p) else ''


def _oauth_token_valid(token_data: dict) -> bool:
    return bool(
        token_data
        and (token_data.get('refresh_token') or token_data.get('token'))
        and token_data.get('client_id')
        and token_data.get('client_secret')
    )


def _oauth_client_id_from_bytes(data: bytes) -> str:
    return (_read_token_bytes(data).get('client_id') or '').strip()


def _should_reseed_backup_file(fname: str, bundled: str, dst: str, force: bool) -> bool:
    """Decide whether AppData should be replaced from the EXE bundle."""
    if force or not os.path.exists(dst):
        return True
    try:
        with open(bundled, 'rb') as bf, open(dst, 'rb') as df:
            bundled_raw, dst_raw = bf.read(), df.read()
    except Exception:
        return True

    if fname == 'backup_creds.dat':
        if not _oauth_token_valid(_read_token_bytes(dst_raw)):
            return True
        bundled_id = _oauth_client_id_from_bytes(bundled_raw)
        dst_id = _oauth_client_id_from_bytes(dst_raw)
        # EXE was rebuilt with a new OAuth client — replace stale AppData creds.
        if bundled_id and dst_id and bundled_id != dst_id:
            _logger.info(
                f"Replacing stale backup_creds.dat (OAuth client changed) -> {dst}"
            )
            return True
        return False

    if not _decrypt_dict(dst_raw).get('folder_id'):
        return True
    bundled_cfg = _decrypt_dict(bundled_raw)
    dst_cfg = _decrypt_dict(dst_raw)
    bundled_id = (bundled_cfg.get('folder_id') or '').strip()
    dst_id = (dst_cfg.get('folder_id') or '').strip()
    if bundled_id and dst_id and bundled_id != dst_id:
        # LEAVE THE SHOP'S OWN FOLDER ALONE.
        #
        # This used to overwrite dst_id with the bundled one and keep only the
        # local store NAME -- so the visible field looked right while the folder
        # the backups actually go to had been changed underneath. A shop that
        # corrected its Drive folder was dragged back to whichever folder the
        # build happened to carry, on the next start after every update, and the
        # screen still showed its own name.
        #
        # An installed shop's backup destination is its own. A build has no
        # business moving it.
        _logger.info(
            "backup_config.dat: keeping this PC's own Drive folder; the bundled "
            "one differs and is not applied."
        )
        return False
    return False


def seed_bundled_backup_files(force: bool = False):
    """
    Copy backup_creds.dat and backup_config.dat from the EXE bundle into AppData
    when missing, unreadable, or superseded by a newer EXE build.
    """
    from core.license_manager import _appdata_dir
    appdata = _appdata_dir()
    os.makedirs(appdata, exist_ok=True)

    pairs = (
        ('backup_creds.dat', _bundled_creds_path),
        ('backup_config.dat', _bundled_config_path),
    )
    for fname, bundled_fn in pairs:
        bundled = bundled_fn() if callable(bundled_fn) else bundled_fn
        if not bundled or not os.path.exists(bundled):
            continue
        dst = os.path.join(appdata, fname)
        if not _should_reseed_backup_file(fname, bundled, dst, force):
            continue
        try:
            shutil.copy2(bundled, dst)
            _logger.info(f"Seeded {fname} from bundle -> {dst}")
        except Exception as e:
            _logger.error(f"Failed to seed {fname}: {e}")


def _read_token_bytes(data: bytes) -> dict:
    raw = _decrypt_bytes(data)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode())
    except Exception:
        return {}


def _creds_path():
    """First readable backup_creds.dat (AppData, then EXE bundle, then project)."""
    from core.license_manager import _appdata_dir
    candidates = [
        os.path.join(_appdata_dir(), 'backup_creds.dat'),
        _bundled_creds_path() or '',
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config', 'backup_creds.dat',
        ),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'rb') as f:
                if _oauth_token_valid(_read_token_bytes(f.read())):
                    return path
        except Exception:
            continue
    return candidates[0] if candidates[0] else ''

def _config_path():
    from core.license_manager import _appdata_dir
    return os.path.join(_appdata_dir(), 'backup_config.dat')


def _auto_backup_pref_path() -> str:
    from core.license_manager import _appdata_dir
    return os.path.join(_appdata_dir(), 'backup_auto_enabled.txt')


#: What a PC with no backup_auto_enabled.txt does. See is_auto_backup_enabled.
AUTO_BACKUP_DEFAULT = True


def is_auto_backup_enabled() -> bool:
    """When False, skip backup on open, close, and hourly scheduler.

    The default is ON, and it used to be OFF.
    Nothing ever wrote this file except the two Settings checkboxes, so a PC
    where nobody thought to tick the box did no automatic backup at all and
    said nothing about it -- the shop found out only when it needed the data.
    Four of the seventeen stores in the vendor's Drive folder have not uploaded
    since June 2026, which is exactly what an unticked box looks like.
    A shop that deliberately turned it off wrote '0' here and stays off; only
    the "nobody ever decided" case changes, and for that case backing up is the
    safe answer. An unreadable file is treated as absent for the same reason.
    """
    path = _auto_backup_pref_path()
    if not os.path.exists(path):
        return AUTO_BACKUP_DEFAULT
    try:
        with open(path, encoding='utf-8') as f:
            raw = f.read().strip().lower()
    except Exception:
        return AUTO_BACKUP_DEFAULT
    if not raw:
        return AUTO_BACKUP_DEFAULT
    return raw in ('1', 'true', 'yes', 'on')


def set_auto_backup_enabled(enabled: bool) -> None:
    path = _auto_backup_pref_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('1' if enabled else '0')


def _project_config_path():
    """config/backup_config.dat in the project — embedded into the EXE at build time."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config', 'backup_config.dat',
    )


def _bundled_config_path():
    """Shipped inside the EXE (PyInstaller) or project config when running from source."""
    if getattr(sys, 'frozen', False):
        bundled = os.path.join(sys._MEIPASS, 'config', 'backup_config.dat')
        if os.path.exists(bundled):
            return bundled
    proj = _project_config_path()
    return proj if os.path.exists(proj) else ''


# ── The vendor's own Drive parent folder ─────────────────────────────────────
#
# Every shop backs up into ONE folder on the vendor's Google account, under a
# per-store subfolder that _ensure_store_subfolder creates at runtime. That
# parent folder is PRODUCT DATA -- the destination half of the same pair as
# backup_creds.dat, which is useless without it and which the build already
# ships on purpose.
#
# It used to travel inside config/backup_config.dat. That file also carries a
# STORE NAME, so the clean-release rule of 2026-09-11 classified the whole file
# as one shop's identity and build_release_filter.clean_release() stripped it
# from all seven specs -- and scripts/audit_release_folder.py refuses any clean
# build that contains a file by that name. Correct for the store name, fatal for
# the folder id: since then a FRESH install has had no backup destination at all
# and _do_backup returned before it wrote anything. Shops that had ever run an
# older build kept working, because their %LOCALAPPDATA%\VeterinaryApp copy
# survived -- which is why it stayed invisible.
#
# So the destination now travels in its own file that holds the folder id and
# NOTHING else: no store name, so there is no shop identity in it to leak, and
# it is listed as a vendor credential in both guards. Keep it a FALLBACK, never
# an override: a shop that set its own folder keeps it (see
# _should_reseed_backup_file).
_VENDOR_FOLDER_FILENAME = 'drive_backup_folder.dat'


def _project_vendor_folder_path():
    """config/drive_backup_folder.dat in the project — embedded into the EXE."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config', _VENDOR_FOLDER_FILENAME,
    )


def _bundled_vendor_folder_path():
    if getattr(sys, 'frozen', False):
        bundled = os.path.join(sys._MEIPASS, 'config', _VENDOR_FOLDER_FILENAME)
        if os.path.exists(bundled):
            return bundled
    proj = _project_vendor_folder_path()
    return proj if os.path.exists(proj) else ''


def read_vendor_drive_folder() -> str:
    """The vendor's shared Drive parent folder id, or '' when this build has none.

    AppData first so the vendor can correct the destination on one PC without a
    rebuild, then the copy shipped inside the build.
    """
    try:
        from core.license_manager import _appdata_dir

        appdata_copy = os.path.join(_appdata_dir(), _VENDOR_FOLDER_FILENAME)
    except Exception:
        appdata_copy = ''
    for path in (appdata_copy, _bundled_vendor_folder_path() or ''):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'rb') as f:
                folder_id = (_decrypt_dict(f.read()).get('folder_id') or '').strip()
        except Exception:
            continue
        if folder_id:
            return folder_id
    return ''


def write_vendor_drive_folder(folder_id: str, path: str = '') -> str:
    """Write the vendor folder file (folder id only — never a store name).

    Used by scripts/make_vendor_drive_folder.py to (re)generate the shipped file
    from whatever destination the vendor already uses, so the id never has to be
    typed out or pasted anywhere.
    """
    folder_id = (folder_id or '').strip()
    if not folder_id:
        raise ValueError('folder_id is empty')
    target = path or _project_vendor_folder_path()
    os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
    with open(target, 'wb') as f:
        f.write(_encrypt_dict({'folder_id': folder_id}))
    return target


def _db_path():
    try:
        from core.store_manager import get_active_db_path
        return get_active_db_path()
    except Exception:
        if getattr(sys, 'frozen', False):
            return os.path.join(os.path.dirname(sys.executable), 'veterinary.db')
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'veterinary.db')


# Encryption
def _get_fernet(secret: bytes):
    try:
        from cryptography.fernet import Fernet
        raw = hashlib.sha256(secret).digest()
        key = base64.urlsafe_b64encode(raw)
        return Fernet(key)
    except Exception:
        return None

def _decrypt_bytes(data: bytes) -> bytes:
    for secret in (_get_secret(), _legacy_machine_secret()):
        f = _get_fernet(secret)
        if not f:
            continue
        try:
            return f.decrypt(data)
        except Exception:
            continue
    return b''

def _decrypt_dict(data: bytes) -> dict:
    raw = _decrypt_bytes(data)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode())
    except Exception:
        return {}

def _encrypt_dict(d: dict) -> bytes:
    f = _get_fernet(_get_secret())
    if f:
        return f.encrypt(json.dumps(d).encode())
    return json.dumps(d).encode()


def _backup_config_payload(folder_id: str, store_name: str) -> dict:
    return {
        'folder_id':    (folder_id or '').strip(),
        'store_name':   (store_name or '').strip(),
        'backup_count': _MAX_BACKUPS,
    }


def _write_config_file(path: str, folder_id: str, store_name: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(_encrypt_dict(_backup_config_payload(folder_id, store_name)))


def _exe_appdata_config_path() -> str:
    """Frozen EXE always reads backup_config.dat from LOCALAPPDATA\\VeterinaryApp."""
    base = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
        'VeterinaryApp',
    )
    return os.path.join(base, 'backup_config.dat')


# Public: write backup_config.dat (AppData — legacy / optional override)
def write_backup_config(folder_id: str, store_name: str):
    try:
        _write_config_file(_config_path(), folder_id, store_name)
        _logger.info(f"backup_config.dat written for store: {store_name}")
        # Dev mode uses project config/; the EXE uses LOCALAPPDATA. Mirror on save so both match.
        if not getattr(sys, 'frozen', False):
            exe_cfg = _exe_appdata_config_path()
            if os.path.normcase(exe_cfg) != os.path.normcase(_config_path()):
                _write_config_file(exe_cfg, folder_id, store_name)
                _logger.info(f"Mirrored backup_config.dat to {exe_cfg}")
    except Exception as e:
        _logger.error(f"Failed to write backup_config.dat: {e}")


def write_bundled_backup_config(folder_id: str, store_name: str):
    """Write config/backup_config.dat before building the EXE (bundled into the installer)."""
    path = _project_config_path()
    try:
        _write_config_file(path, folder_id, store_name)
        _logger.info(f"Bundled backup_config.dat written: {store_name}")
        print(f"Written: {path}")
    except Exception as e:
        _logger.error(f"Failed to write bundled backup_config.dat: {e}")
        raise


def get_backup_store_name(cfg: dict = None) -> str:
    """Drive subfolder name — uses the active store when multi-store is enabled."""
    try:
        from core.store_manager import get_active_display_name, has_registry
        if has_registry():
            name = (get_active_display_name() or '').strip()
            if name:
                return name
    except Exception:
        pass
    if cfg is None:
        cfg = _read_backup_config()
    return (cfg.get('store_name') or '').strip() or 'UnknownStore'


def sync_backup_config_to_active_store():
    """Keep backup_config.dat store_name aligned with the active store."""
    try:
        from core.store_manager import get_active_display_name, has_registry
        if not has_registry():
            return
        name = (get_active_display_name() or '').strip()
        if not name:
            return
        cfg = _read_backup_config()
        folder_id = (cfg.get('folder_id') or '').strip()
        if not folder_id:
            return
        if cfg.get('source') == 'vendor':
            # The vendor default is read fresh on every backup. Writing it here
            # would freeze today's parent folder into this PC's own file, and
            # _should_reseed_backup_file deliberately never moves a folder a PC
            # already holds -- so a later change of destination could no longer
            # reach it. The subfolder name comes from the ACTIVE store anyway
            # (get_backup_store_name), so there is nothing to keep in step.
            return
        if (cfg.get('store_name') or '').strip() != name:
            write_backup_config(folder_id, name)
    except Exception as e:
        _logger.error(f"sync_backup_config_to_active_store failed: {e}")


def get_backup_config_status() -> dict:
    """Return {configured, folder_id, store_name, creds_ok, ...} for UI.

    folder_ok and creds_ok are reported separately on purpose: the Tauri screen
    used to say "check backup_creds.dat" for every unconfigured state, which
    pointed at the one file that was never missing.
    """
    cfg = _read_backup_config()
    folder_id = (cfg.get('folder_id') or '').strip()
    store_name = get_backup_store_name(cfg)
    creds = _read_oauth_token()
    creds_ok = _oauth_token_valid(creds)
    return {
        'configured': bool(folder_id and store_name and creds_ok),
        'folder_id': folder_id,
        'store_name': store_name,
        'creds_ok': creds_ok,
        'folder_ok': bool(folder_id),
        'folder_source': cfg.get('source', ''),
        'usb_connected': bool(_detect_pendrives()),
    }


def _read_backup_config() -> dict:
    """AppData override first, then EXE-bundled config, then the vendor default.

    The returned dict carries a 'source' key so callers can tell a destination
    this PC actually chose ('appdata'/'bundled') from the vendor default every
    build now ships ('vendor'). Nothing may write the vendor default back into
    backup_config.dat: that would pin today's parent folder into the shop's own
    file, and _should_reseed_backup_file would then refuse to ever move it.
    """
    for path, source in ((_config_path(), 'appdata'),
                         (_bundled_config_path() or '', 'bundled')):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'rb') as f:
                cfg = _decrypt_dict(f.read())
            if cfg.get('folder_id'):
                cfg['source'] = source
                return cfg
        except Exception:
            continue
    vendor = read_vendor_drive_folder()
    if vendor:
        return {'folder_id': vendor, 'store_name': '',
                'backup_count': _MAX_BACKUPS, 'source': 'vendor'}
    return {}

def _read_oauth_token() -> dict:
    from core.license_manager import _appdata_dir
    for path in (
        os.path.join(_appdata_dir(), 'backup_creds.dat'),
        _bundled_creds_path() or '',
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config', 'backup_creds.dat',
        ),
    ):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'rb') as f:
                token = _read_token_bytes(f.read())
            if _oauth_token_valid(token):
                return token
        except Exception:
            continue
    return {}


# Internet check - tries multiple hosts/ports in case one is blocked
def _is_internet_available() -> bool:
    checks = [
        ("8.8.8.8",        53),
        ("1.1.1.1",        53),
        ("www.google.com", 80),
        ("www.google.com", 443),
    ]
    for host, port in checks:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(4)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            continue
    return False


# Drive service using OAuth2 refresh token
def _get_drive_service(token_data: dict):
    from core.ssl_utils import configure_ssl_certificates, httplib2_http
    configure_ssl_certificates()
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials(
        token=token_data.get('token'),
        refresh_token=token_data.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=token_data.get('client_id'),
        client_secret=token_data.get('client_secret'),
        scopes=['https://www.googleapis.com/auth/drive'],
    )
    if not creds.valid:
        creds.refresh(Request())
    # googleapiclient rejects credentials + http together; authorize http instead.
    from google_auth_httplib2 import AuthorizedHttp
    http = AuthorizedHttp(creds, http=httplib2_http())
    return build('drive', 'v3', http=http, cache_discovery=False)


def _drive_subfolder_name(store_name: str) -> str:
    try:
        from core.store_manager import display_name_key
        return display_name_key(store_name)
    except Exception:
        return f"Store_{store_name.replace(' ', '_')}"


def _pick_store_subfolder(files: list) -> str:
    """One id out of however many folders came back with the store's name.

    The vendor's Drive already holds two Store_Bramhandnayak_Medical folders a
    second apart -- list-then-create is not atomic, so two PCs (or a retry after
    a slow list) can both create it. Whichever is picked, BACKUP and RESTORE
    must pick the same one, or a shop's copies split and the restore list shows
    half of them. Oldest wins: it is stable, and it is the one with the history.
    """
    if not files:
        return ''
    ordered = sorted(
        files, key=lambda f: (f.get('createdTime') or '9999', f.get('id') or '')
    )
    return ordered[0].get('id', '')


def _ensure_store_subfolder(service, parent_folder_id: str, store_name: str) -> str:
    safe_name = _drive_subfolder_name(store_name)
    q = (f"'{parent_folder_id}' in parents "
         f"and name='{safe_name}' "
         f"and mimeType='application/vnd.google-apps.folder' "
         f"and trashed=false")
    res = service.files().list(q=q, fields='files(id, createdTime)').execute()
    files = res.get('files', [])
    if files:
        return _pick_store_subfolder(files)
    meta = {
        'name': safe_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_folder_id],
    }
    folder = service.files().create(body=meta, fields='id').execute()
    return folder['id']

def _upload_file(service, file_path: str, folder_id: str, filename: str):
    from googleapiclient.http import MediaFileUpload
    import time

    meta = {'name': filename, 'parents': [folder_id]}
    # Resumable uploads hit httplib2 redirect bugs on Windows; multipart is fine under 100 MB.
    file_size = os.path.getsize(file_path)
    use_resumable = file_size > 100 * 1024 * 1024
    media_kwargs = {'mimetype': 'application/gzip', 'resumable': use_resumable}
    if use_resumable:
        media_kwargs['chunksize'] = 256 * 1024
    media = MediaFileUpload(file_path, **media_kwargs)
    request = service.files().create(body=meta, media_body=media, fields='id')
    last_err = None
    for attempt in range(3):
        try:
            if use_resumable:
                response = None
                while response is None:
                    status, response = request.next_chunk(num_retries=3)
            else:
                request.execute()
            return
        except Exception as exc:
            last_err = exc
            if attempt >= 2:
                raise
            time.sleep(2 * (attempt + 1))
    if last_err:
        raise last_err

def _cleanup_old_backups(service, folder_id: str, protected: set):
    """Rules:
    - Files older than 3 years: deleted (never if in protected).
    - TODAY only: if more than 4 files, keep first 2 + last 2, protected files always kept.
    - All other days: never touched.
    """
    res = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields='files(id, name, createdTime)',
        orderBy='name asc'
    ).execute()
    files = res.get('files', [])
    if not files:
        return

    from collections import defaultdict
    today     = datetime.now().strftime('%Y-%m-%d')
    cutoff    = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff    = cutoff.replace(year=cutoff.year - 3)

    by_date = defaultdict(list)
    for f in files:
        try:
            date_part = f['name'].split('_')[1]
            file_date = datetime.strptime(date_part, '%Y-%m-%d')
        except Exception:
            date_part = f['createdTime'][:10]
            file_date = datetime.strptime(date_part, '%Y-%m-%d')

        # 3-year rule — applies to ALL days, protected files exempt
        if file_date < cutoff:
            if f['name'] not in protected:
                try:
                    service.files().delete(fileId=f['id']).execute()
                    _logger.info(f"Deleted old backup (>3 years): {f['name']}")
                except Exception:
                    pass
            continue

        by_date[date_part].append(f)

    # Mid-day cleanup — ONLY for today, only when >4 files
    today_files = by_date.get(today, [])
    if len(today_files) <= 4:
        return
    keep = set(f['id'] for f in today_files[:2] + today_files[-2:])
    keep |= {f['id'] for f in today_files if f['name'] in protected}
    for f in today_files:
        if f['id'] not in keep:
            try:
                service.files().delete(fileId=f['id']).execute()
                _logger.info(f"Cleanup: deleted today middle backup {f['name']}")
            except Exception:
                pass


# Pendrive backup
def _detect_pendrives() -> list:
    """Every connected removable USB drive root, in letter order.

    Backup writes to the first one; RESTORE looks at all of them, because the
    stick somebody brings back with last week's copy is rarely the one that
    happens to sort first.
    """
    import ctypes
    DRIVE_REMOVABLE = 2
    found = []
    for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        root = f"{letter}:\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE:
                if os.path.exists(root):
                    found.append(root)
        except Exception:
            pass
    return found


def _detect_pendrive() -> str:
    """Return drive letter of first connected removable USB drive, or empty string."""
    drives = _detect_pendrives()
    return drives[0] if drives else ''

def _cleanup_pendrive_backups(folder: str, protected: set):
    """Same rules as Drive cleanup:
    - Files older than 3 years: deleted (protected files exempt).
    - TODAY only: if more than 4 files, keep first 2 + last 2, protected always kept.
    - All other days: never touched.
    """
    from collections import defaultdict
    try:
        files = sorted([f for f in os.listdir(folder) if f.endswith('.db.gz')])
    except Exception:
        return

    today  = datetime.now().strftime('%Y-%m-%d')
    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = cutoff.replace(year=cutoff.year - 3)

    by_date = defaultdict(list)
    for name in files:
        try:
            date_part = name.split('_')[1]
            file_date = datetime.strptime(date_part, '%Y-%m-%d')
        except Exception:
            continue
        if file_date < cutoff:
            if name not in protected:
                try:
                    os.remove(os.path.join(folder, name))
                    _logger.info(f"Pendrive: deleted old backup (>3 years): {name}")
                except Exception:
                    pass
            continue
        by_date[date_part].append(name)

    # Mid-day cleanup — ONLY for today, only when >4 files
    today_files = by_date.get(today, [])
    if len(today_files) <= 4:
        return
    keep = set(today_files[:2] + today_files[-2:]) | (protected & set(today_files))
    for name in today_files:
        if name not in keep:
            try:
                os.remove(os.path.join(folder, name))
            except Exception:
                pass

def _do_pendrive_backup(gz_path: str, filename: str, store_name: str, protected: set,
                        drive: str = '') -> str:
    """Copy the snapshot to a USB stick. Returns 'ok' | 'no_drive' | 'failed'."""
    drive = drive or _detect_pendrive()
    if not drive:
        return 'no_drive'
    safe_name = _drive_subfolder_name(store_name)
    dest_dir = os.path.join(drive, 'SatpudaCore_Backup', safe_name)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(gz_path, os.path.join(dest_dir, filename))
        _cleanup_pendrive_backups(dest_dir, protected)
        _logger.info(f"Pendrive backup OK - {filename} -> {drive}")
        return 'ok'
    except Exception as e:
        _logger.error(f"Pendrive backup failed: {e}")
        return 'failed'


def _snapshot_db_for_backup(src_path: str, dst_path: str) -> None:
    """Copy veterinary.db including recent WAL writes while the app is still open."""
    import sqlite3
    abs_src = os.path.abspath(src_path)
    try:
        src = sqlite3.connect(f'file:{abs_src}?mode=ro', uri=True, timeout=60)
        try:
            dst = sqlite3.connect(dst_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return
    except Exception as e:
        _logger.warning(f"SQLite backup API failed, falling back to WAL checkpoint: {e}")
    conn = sqlite3.connect(abs_src, timeout=60)
    try:
        conn.execute('PRAGMA wal_checkpoint(FULL)')
    finally:
        conn.close()
    shutil.copy2(src_path, dst_path)


# Core backup logic
def _backup_result(status: str, code: str, message: str, **extra) -> dict:
    """The answer _do_backup hands back to whoever asked for the backup.

    The desktop screen used to guess the outcome from the last line of
    backup_log.txt, failing only on the substrings "fail" and "error" -- so
    "Backup skipped - backup_config.dat missing or invalid." was shown to the
    shop as a green "Backup finished." A result the caller can read means the
    screen says what happened, and it keeps working if logging is ever turned
    off in a release profile (core/log_policy.py), which would leave the log
    empty and the old guess with nothing at all to read.
    """
    out = {
        'ok': status == 'ok',
        'status': status,          # 'ok' | 'skipped' | 'error'
        'code': code,
        'message': message,
        'drive': '',               # 'ok'|'failed'|'not_configured'|'no_internet'|'no_creds'|''
        'pendrive': '',            # 'ok'|'failed'|'no_drive'|''
        'filename': '',
        'store_name': '',
        'trigger': '',
    }
    out.update(extra)
    return out


def _do_backup(force: bool = False, trigger: str = 'open', on_error=None) -> dict:
    """Run a backup. Returns a result dict (see _backup_result).

    force=False  → skip if a backup ran within _DEDUP_MINUTES (used on app open).
    force=True   → always run (used on app close and hourly scheduler).
    trigger      → 'open' | 'close' | 'hourly' | 'manual' — which slot to fill.

    The Drive folder id no longer gates the whole routine. It used to: a PC with
    no folder configured returned here before the snapshot was even taken, so it
    got no USB copy either, and the one message that named the missing folder
    was written to a log nobody reads. A shop with a pendrive and no internet is
    a normal shop; its local copy does not depend on Google.
    """
    global _last_backup_time
    if getattr(sys, 'frozen', False):
        try:
            seed_bundled_backup_files()
        except Exception:
            pass
    tmp_dir = None
    online_tmp = None
    try:
        sync_backup_config_to_active_store()

        # On-open dedup: skip if app was just closed and reopened within 5 min
        if not force and _last_backup_time is not None:
            elapsed = (datetime.now() - _last_backup_time).total_seconds() / 60
            if elapsed < _DEDUP_MINUTES:
                _logger.info(f"On-open backup skipped - last backup was {elapsed:.1f} min ago.")
                return _backup_result(
                    'skipped', 'recent',
                    f"Backup skipped - one ran {elapsed:.0f} min ago.",
                    trigger=trigger,
                )

        cfg = _read_backup_config()
        folder_id = (cfg.get('folder_id') or '').strip()
        usb_root = _detect_pendrive()
        if not folder_id and not usb_root:
            # Nowhere to put it. Say so plainly, and do not spend an Online
            # store's server round-trip materialising a snapshot with no home.
            _logger.info(
                "Backup skipped - no Drive folder configured and no USB drive connected."
            )
            return _backup_result(
                'skipped', 'no_destination',
                "Backup skipped: no Google Drive folder is set for this PC and no "
                "USB drive is connected.\n"
                "Settings → Data & System → Administrator → Drive folder ID, or "
                "plug in a pendrive.",
                drive='not_configured', pendrive='no_drive', trigger=trigger,
            )

        db = _db_path()

        # An Online store keeps its data on the SERVER. The local veterinary.db
        # is either absent or frozen at whatever it held before the store went
        # online, so backing it up captured nothing new -- one store's last
        # backup held 4,142 sales while the server had moved on to 4,410, and a
        # store with no local file at all was skipped silently. Materialise the
        # server store into a throwaway database and back THAT up instead.
        try:
            from core.sync_prefs import is_online_mode

            _is_online = bool(is_online_mode())
        except Exception:
            _is_online = False

        if _is_online:
            try:
                from core.online_migrate import download_store_for_offline

                online_tmp = tempfile.mkdtemp(prefix='satpuda_backup_online_')
                snap = os.path.join(online_tmp, 'veterinary.db')
                res = download_store_for_offline(
                    db_path=snap, preserve_sync_state=True
                )
                if res.get('ok') and os.path.exists(snap):
                    db = snap
                    _logger.info(
                        "Backup: captured Online store from server (%s rows).",
                        res.get('rows'),
                    )
                else:
                    # Do NOT fall back to the local file. For an Online store that
                    # file is stale or absent, so the fallback uploaded a backup
                    # that LOOKED current while being months behind -- one store's
                    # newest backup held 4,142 sales against 4,410 live. Skipping
                    # is honest; a confidently wrong backup is worse than none.
                    _logger.error(
                        "Backup ABORTED: could not capture Online store (%s). "
                        "Refusing to upload the stale local file.",
                        res.get('error'),
                    )
                    msg = (
                        "Backup skipped: could not read this store from the "
                        "server, and the local copy is not current.\n"
                        f"{res.get('error') or ''}"
                    )
                    if on_error:
                        on_error(msg)
                    return _backup_result(
                        'error', 'online_capture_failed', msg, trigger=trigger
                    )
            except Exception as exc:
                _logger.error("Backup ABORTED: online capture failed: %s", exc)
                msg = (
                    "Backup skipped: could not read the store from the server.\n"
                    f"{exc}"
                )
                if on_error:
                    on_error(msg)
                return _backup_result(
                    'error', 'online_capture_failed', msg, trigger=trigger
                )

        if not os.path.exists(db):
            _logger.warning("Backup skipped - veterinary.db not found.")
            return _backup_result(
                'skipped', 'no_database',
                "Backup skipped: this store has no database file yet.",
                trigger=trigger,
            )

        store_name = get_backup_store_name(cfg)
        ts         = datetime.now().strftime('%Y-%m-%d_%H-%M')
        filename   = f"SatpudaCore_{ts}.db.gz"

        tmp_dir = tempfile.mkdtemp()
        tmp_db  = os.path.join(tmp_dir, 'veterinary.db')
        tmp_gz  = os.path.join(tmp_dir, filename)
        _snapshot_db_for_backup(db, tmp_db)
        with open(tmp_db, 'rb') as f_in, gzip.open(tmp_gz, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

        # Register filename in the correct slot BEFORE cleanup runs
        _register_filename(filename, trigger)
        _last_backup_time = datetime.now()
        protected = _protected_filenames()

        # Pendrive backup — no internet and no Drive folder needed.
        pendrive = _do_pendrive_backup(
            tmp_gz, filename, store_name, protected, drive=usb_root
        )

        def _done(status, code, message, drive_state):
            return _backup_result(
                status, code, message, drive=drive_state, pendrive=pendrive,
                filename=filename, store_name=store_name, trigger=trigger,
            )

        usb_note = (
            f"\nUSB copy saved to SatpudaCore_Backup on {usb_root}."
            if pendrive == 'ok' else ''
        )

        if not folder_id:
            _logger.info(
                "Drive upload skipped - no Drive folder configured for this PC. "
                "Local USB copy: %s", pendrive,
            )
            return _done(
                'ok' if pendrive == 'ok' else 'skipped',
                'drive_not_configured',
                "No Google Drive folder is set for this PC, so nothing was "
                "uploaded.\nSettings → Data & System → Administrator → Drive "
                "folder ID." + usb_note,
                'not_configured',
            )

        # Google Drive backup (only if internet available)
        if not _is_internet_available():
            _logger.info("Drive backup skipped - no internet.")
            return _done(
                'ok' if pendrive == 'ok' else 'skipped', 'no_internet',
                "No internet connection, so nothing was uploaded to Google "
                "Drive." + usb_note,
                'no_internet',
            )

        token_data = _read_oauth_token()
        if not token_data or not token_data.get('refresh_token'):
            _logger.info("Drive backup skipped - backup_creds.dat missing or invalid.")
            return _done(
                'ok' if pendrive == 'ok' else 'skipped', 'no_creds',
                "Google Drive credentials are missing or invalid on this PC "
                "(backup_creds.dat)." + usb_note,
                'no_creds',
            )

        try:
            service   = _get_drive_service(token_data)
            subfolder = _ensure_store_subfolder(service, folder_id, store_name)
            _upload_file(service, tmp_gz, subfolder, filename)
            _cleanup_old_backups(service, subfolder, protected)
            stats = _sale_stats(tmp_db)
            _logger.info(
                f"Backup OK [{trigger}] - {filename} -> {store_name} "
                f"({stats['count']} sales, latest {stats['latest']})"
            )
            return _done(
                'ok', 'ok',
                f"Backup OK - {filename} -> {store_name} "
                f"({stats['count']} sales, latest {stats['latest']})" + usb_note,
                'ok',
            )
        except Exception as drive_err:
            err_msg = str(drive_err)
            _logger.error(f"Backup Drive error: {err_msg}")
            if on_error:
                on_error(
                    f"Google Drive upload failed: {err_msg}\n"
                    "If a USB drive is connected, check SatpudaCore_Backup on the pendrive."
                )
            return _done(
                'error', 'drive_failed',
                _drive_error_hint(err_msg) + usb_note,
                'failed',
            )

    except Exception as e:
        _logger.error(f"Backup failed: {e}")
        return _backup_result('error', 'failed', f"Backup failed: {e}", trigger=trigger)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if online_tmp:
            shutil.rmtree(online_tmp, ignore_errors=True)


def _drive_error_hint(err_msg: str) -> str:
    """Turn a Drive exception into something a shop can act on.

    The same wording Classic shows (ui/settings/settings_tabs/database_tab.py),
    kept here so both front-ends say one thing.
    """
    low = (err_msg or '').lower()
    if 'disabled_client' in low:
        return ("Google Drive upload failed: the OAuth client is disabled. "
                "This needs a new build from the developer.")
    if '404' in low or 'not found' in low:
        return ("Google Drive upload failed: folder not found. Check the Drive "
                "folder ID in Settings → Data & System → Administrator.")
    if '403' in low:
        return ("Google Drive upload failed: no access to that Drive folder. "
                "Share it with the backup Google account.")
    if 'timed out' in low or 'timeout' in low or '10054' in low:
        return ("Google Drive upload timed out or the connection dropped. Try "
                "again on a stable connection.")
    return f"Google Drive upload failed: {err_msg}"


def _sale_stats(db_path: str) -> dict:
    """Return counts used for backup/restore verification messages."""
    out = {
        'count': 0,
        'latest': '—',
        'medicines': 0,
        'customers': 0,
        'purchases': 0,
    }
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM sales')
            out['count'] = int(cur.fetchone()[0] or 0)
            cur.execute('SELECT bill_no FROM sales ORDER BY id DESC LIMIT 1')
            row = cur.fetchone()
            out['latest'] = row[0] if row and row[0] else '—'
            for table, key in (
                ('medicines', 'medicines'),
                ('customers', 'customers'),
                ('purchases', 'purchases'),
            ):
                try:
                    cur.execute(f'SELECT COUNT(*) FROM {table}')
                    out[key] = int(cur.fetchone()[0] or 0)
                except Exception:
                    pass
            return out
        finally:
            conn.close()
    except Exception:
        return out


def _close_all_db_users(extra_conn=None) -> None:
    """Stop pollers and close every known open connection before file replace."""
    try:
        from core.sync_coordinator import stop_online_sync
        stop_online_sync()
    except Exception:
        pass
    try:
        from core.desktop_sync_launch import stop_desktop_online_sync
        stop_desktop_online_sync()
    except Exception:
        pass
    if extra_conn is not None:
        try:
            extra_conn.close()
        except Exception:
            pass
    try:
        from core import desktop_api as dap
        old = dap._db.get("conn")
        dap._db["conn"] = None
        if old is not None and old is not extra_conn:
            try:
                old.close()
            except Exception:
                pass
    except Exception:
        pass


def _after_restore_sync_policy() -> None:
    """Treat restored local DB as source of truth until user Pull/Push manually.

    Clears incremental watermarks and stamps "now" so the Online poller does
    not immediately overwrite the Drive restore with older/newer server rows.
    """
    try:
        from core.sync_bootstrap import clear_pending_bootstrap, mark_bootstrap_done
        clear_pending_bootstrap()
        mark_bootstrap_done()
    except Exception:
        pass
    try:
        from core.sync_watermarks import clear_all, seed_all_now
        clear_all()
        seed_all_now()
    except Exception:
        pass


def _is_drive_backup_filename(name: str) -> bool:
    """True for SatpudaCore_*.db.gz or legacy plain SatpudaCore_*.db."""
    n = (name or '').strip()
    if not n.lower().startswith('satpudacore_'):
        return False
    lower = n.lower()
    return lower.endswith('.db.gz') or lower.endswith('.db')


def _backup_name_sort_key(item) -> str:
    name = item.get('name', '') if isinstance(item, dict) else str(item or '')
    base = name
    for suffix in ('.db.gz', '.db'):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    parts = base.split('_', 2)
    if len(parts) >= 3 and parts[0] == 'SatpudaCore':
        return parts[1] + '_' + parts[2]
    if isinstance(item, dict):
        return item.get('modifiedTime', '') or ''
    return ''


def _looks_like_gzip(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            return f.read(2) == b'\x1f\x8b'
    except Exception:
        return False


def _looks_like_sqlite(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            return f.read(16).startswith(b'SQLite format 3')
    except Exception:
        return False


def _materialize_backup_file(src_path: str, db_path: str) -> bool:
    """Write a usable SQLite file to db_path from .db.gz or plain .db (possibly misnamed)."""
    try:
        if os.path.getsize(src_path) < 100:
            return False
        name = os.path.basename(src_path).lower()
        if name.endswith('.db.gz') or _looks_like_gzip(src_path):
            with gzip.open(src_path, 'rb') as f_in, open(db_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy2(src_path, db_path)
        if not os.path.isfile(db_path) or os.path.getsize(db_path) < 100:
            return False
        if not _looks_like_sqlite(db_path):
            # Some older uploads were gzip with a plain .db name already handled above;
            # reject non-sqlite payloads.
            return False
        return True
    except Exception as e:
        _logger.error(f"Failed to materialize backup {src_path}: {e}")
        return False


def _upgrade_restored_db(db_path: str) -> None:
    """Run current schema migrations on an old restored backup before it goes live."""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        conn.execute('PRAGMA busy_timeout=60000')
        from core.db_setup import initialise
        initialise(conn)
        conn.commit()
        # Stock is NOT recomputed here any more.
        #
        # A restore must reproduce the file it was given, not reinterpret it.
        # rebuild_stock_from_ledger derives every medicine's stock from purchase
        # and sale rows, so any quantity that has no ledger behind it -- opening
        # stock typed in when the shop started, an imported inventory, a manual
        # correction -- was silently reset to zero on EVERY Sync from Drive. The
        # backup held the right numbers; the restore threw them away.
        #
        # If a store's stock really does look wrong, rebuild it deliberately from
        # the Inventory tools, where the user can see what changed.
        _logger.info("Drive restore: keeping stock exactly as backed up")
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


def _remove_sqlite_sidecars(db_path: str) -> None:
    """Delete -wal/-shm/-journal next to db_path so a replaced DB is not mixed with old WAL."""
    for suffix in ('-wal', '-shm', '-journal'):
        side = db_path + suffix
        try:
            if os.path.isfile(side):
                os.remove(side)
        except OSError:
            pass


def _replace_store_database(src_db: str, dest_db: str) -> None:
    """Atomically replace store veterinary.db and clear leftover WAL/SHM files."""
    os.makedirs(os.path.dirname(dest_db) or '.', exist_ok=True)
    _remove_sqlite_sidecars(dest_db)
    tmp_dest = dest_db + '.restore_tmp'
    try:
        if os.path.isfile(tmp_dest):
            os.remove(tmp_dest)
    except OSError:
        pass
    shutil.copy2(src_db, tmp_dest)
    _remove_sqlite_sidecars(dest_db)
    os.replace(tmp_dest, dest_db)
    _remove_sqlite_sidecars(dest_db)


def _list_drive_files_in_folder(service, folder_id: str) -> list:
    """List all non-trashed files in a Drive folder (paginated)."""
    files = []
    page_token = None
    while True:
        kwargs = dict(
            q=f"'{folder_id}' in parents and trashed=false and name contains 'SatpudaCore_'",
            fields='nextPageToken, files(id, name, modifiedTime, size)',
            orderBy='modifiedTime desc',
            pageSize=100,
        )
        if page_token:
            kwargs['pageToken'] = page_token
        res = service.files().list(**kwargs).execute()
        files.extend(res.get('files', []) or [])
        page_token = res.get('nextPageToken')
        if not page_token:
            break
    return files


def _find_store_subfolder_id(service, parent_folder_id: str, store_name: str) -> str:
    safe_name = _drive_subfolder_name(store_name)
    q = (f"'{parent_folder_id}' in parents "
         f"and name='{safe_name}' "
         f"and mimeType='application/vnd.google-apps.folder' "
         f"and trashed=false")
    res = service.files().list(q=q, fields='files(id, createdTime)').execute()
    return _pick_store_subfolder(res.get('files', []))


def _drive_auth_for_restore():
    """Shared pre-checks for list/restore. Returns (service, cfg) or (None, error_msg)."""
    cfg = _read_backup_config()
    if not cfg or not cfg.get('folder_id'):
        return None, (
            'No Google Drive folder is set for this PC.\n'
            'Settings → Data & System → Administrator → Drive folder ID, '
            'or restore from a USB backup instead.'
        )

    if not _is_internet_available():
        return None, 'No internet connection. Connect and try again.'

    token_data = _read_oauth_token()
    if not token_data or not token_data.get('refresh_token'):
        return None, 'Backup credentials are missing or invalid.'

    try:
        service = _get_drive_service(token_data)
    except Exception as e:
        return None, str(e)
    return service, cfg


def list_drive_backups(store_name: str) -> tuple:
    """List Drive backup files for a store (newest first).

    Returns (True, [{'id','name','modifiedTime','label'}, ...]) or (False, error_message).
    Accepts both current .db.gz and legacy plain .db backups.
    """
    service, cfg_or_err = _drive_auth_for_restore()
    if service is None:
        return False, cfg_or_err
    cfg = cfg_or_err
    try:
        subfolder_id = _find_store_subfolder_id(service, cfg['folder_id'], store_name)
        if not subfolder_id:
            return False, (
                f'No backup folder found on Drive for store "{store_name}".\n'
                f'Expected folder: {_drive_subfolder_name(store_name)}'
            )

        raw = _list_drive_files_in_folder(service, subfolder_id)
        files = [f for f in raw if _is_drive_backup_filename(f.get('name', ''))]
        if not files:
            return False, (
                f'No backup files found in Drive folder for store "{store_name}".\n'
                'Ask the admin device to run at least one backup first.\n'
                'Supported names: SatpudaCore_YYYY-MM-DD_HH-MM.db.gz or .db'
            )

        files.sort(key=_backup_name_sort_key, reverse=True)
        out = []
        for f in files:
            name = f.get('name', '')
            mod = (f.get('modifiedTime') or '')[:19].replace('T', ' ')
            label = f"{name}" + (f"  ({mod} UTC)" if mod else "")
            out.append({
                'id': f.get('id'),
                'name': name,
                'modifiedTime': f.get('modifiedTime', ''),
                'size': f.get('size'),
                'label': label,
            })
        return True, out
    except Exception as e:
        _logger.error(f"List Drive backups failed: {e}")
        return False, str(e)


def _download_drive_backup_file(service, file_meta: dict, tmp_dir: str) -> tuple:
    """Download one Drive backup and materialize to veterinary.db.
    Returns (True, result_dict) or (False, None)."""
    from googleapiclient.http import MediaIoBaseDownload

    name = file_meta.get('name', 'backup.db.gz')
    src_path = os.path.join(tmp_dir, name)
    db_path = os.path.join(tmp_dir, 'veterinary.db')

    request = service.files().get_media(fileId=file_meta['id'])
    with open(src_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    if not _materialize_backup_file(src_path, db_path):
        return False, None

    stats = _sale_stats(db_path)
    if stats['count'] <= 0 and os.path.getsize(db_path) <= 500_000:
        # Allow old but valid DBs that may have few sales; only skip tiny empty shells.
        if os.path.getsize(db_path) < 50_000 and stats['count'] == 0:
            return False, None

    try:
        _upgrade_restored_db(db_path)
    except Exception as e:
        _logger.error(f"Schema upgrade after restore failed for {name}: {e}")
        return False, None
    # Re-read counts after upgrade/rebuild so UI message matches live file.
    stats = _sale_stats(db_path)
    return True, {
        'db_path': db_path,
        'backup_file': name,
        'tmp_dir': tmp_dir,
        'sale_count': stats['count'],
        'medicine_count': stats.get('medicines', 0),
        'customer_count': stats.get('customers', 0),
        'purchase_count': stats.get('purchases', 0),
        'latest_bill': stats.get('latest', '—'),
    }


def restore_backup_from_drive(store_name: str, file_id: str = None) -> tuple:
    """Download a specific Drive backup (or the newest usable one if file_id is None).

    Returns (True, dest_info_dict) or (False, error_message).
    dest_info_dict includes db_path, backup_file, store_name, tmp_dir.
    """
    service, cfg_or_err = _drive_auth_for_restore()
    if service is None:
        return False, cfg_or_err
    cfg = cfg_or_err

    tmp_dir = None
    try:
        subfolder_id = _find_store_subfolder_id(service, cfg['folder_id'], store_name)
        if not subfolder_id:
            return False, (
                f'No backup folder found on Drive for store "{store_name}".\n'
                f'Expected folder: {_drive_subfolder_name(store_name)}'
            )

        raw = _list_drive_files_in_folder(service, subfolder_id)
        files = [f for f in raw if _is_drive_backup_filename(f.get('name', ''))]
        if not files:
            return False, (
                f'No backup files found in Drive folder for store "{store_name}".\n'
                'Ask the admin device to run at least one backup first.'
            )

        # Newest by Drive's own clock (modifiedTime), as the phone picks it -- the name's
        # timestamp is the uploading device's clock, and a PC and a phone with clocks
        # apart would each call a different file "latest" (3 Oct 2026).
        files.sort(key=lambda f: (f.get('modifiedTime') or '', _backup_name_sort_key(f)), reverse=True)

        if file_id:
            chosen = [f for f in files if f.get('id') == file_id]
            if not chosen:
                return False, 'Selected backup was not found on Drive. Refresh the list and try again.'
            candidates = chosen
        else:
            candidates = files

        tmp_dir = tempfile.mkdtemp()
        last_err = None
        for candidate in candidates:
            ok, result = _download_drive_backup_file(service, candidate, tmp_dir)
            if ok and result:
                _logger.info(f"Restore OK - {result['backup_file']} for store {store_name}")
                result['store_name'] = store_name
                return True, result
            last_err = candidate.get('name')

        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir = None
        return False, (
            f'Drive backup for "{store_name}" looks empty or corrupt'
            + (f' ({last_err}).' if last_err else '.')
            + '\nTry another file from the backup list, or run Backup Now from a device that has your data.'
        )
    except Exception as e:
        _logger.error(f"Restore failed: {e}")
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, str(e)


def restore_latest_backup_from_drive(store_name: str) -> tuple:
    """Download the most recent usable Drive backup for store_name.
    Returns (True, dest_info_dict) or (False, error_message)."""
    return restore_backup_from_drive(store_name, file_id=None)


def restore_latest_backup_to_store(
    store_name: str, store_key: str, *, close_conn=None, file_id: str = None,
    allow_loss: bool = False,
) -> tuple:
    """Restore a Drive backup into the store's local veterinary.db.

    Closes every live DB user (API conn + poller), replaces the file, clears
    Online watermarks so the poller cannot overwrite the restore, and returns
    verification counts.
    """
    ok, result = restore_backup_from_drive(store_name, file_id=file_id)
    if not ok:
        return False, result
    return _apply_restored_db(result, store_name, store_key, close_conn=close_conn,
                              allow_loss=allow_loss)


# A file that came back from the phone has no FSSAI columns (the phone's database never
# had them), so the restore printed bills without the shop's FSSAI number from then on.
# What this PC had is put back when the incoming file has nothing of its own.
_PROFILE_EXTRAS = ("fssai_number", "show_fssai_on_bill")


def _profile_extras(db_path: str) -> dict:
    import sqlite3

    if not db_path or not os.path.isfile(db_path):
        return {}
    try:
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            have = {r[1] for r in c.execute("PRAGMA table_info(pharmacy_profile)")}
            cols = [k for k in _PROFILE_EXTRAS if k in have]
            if not cols:
                return {}
            row = c.execute(f"SELECT {', '.join(cols)} FROM pharmacy_profile ORDER BY id LIMIT 1").fetchone()
            return {k: v for k, v in zip(cols, row or ()) if v not in (None, '', 0)}
        finally:
            c.close()
    except Exception:
        return {}


def _restore_profile_extras(db_path: str, kept: dict) -> None:
    import sqlite3

    if not kept:
        return
    try:
        c = sqlite3.connect(db_path)
        try:
            have = {r[1] for r in c.execute("PRAGMA table_info(pharmacy_profile)")}
            if not have:
                return
            for k in kept:
                if k not in have:
                    c.execute(f"ALTER TABLE pharmacy_profile ADD COLUMN {k} "
                              + ("INTEGER DEFAULT 0" if k.startswith("show_") else "TEXT"))
            row = c.execute(f"SELECT {', '.join(kept)} FROM pharmacy_profile ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return
            current = dict(zip(kept, row))
            put = {k: v for k, v in kept.items() if current.get(k) in (None, '', 0)}
            if put:
                c.execute("UPDATE pharmacy_profile SET " + ", ".join(f"{k}=?" for k in put)
                          + " WHERE id=(SELECT id FROM pharmacy_profile ORDER BY id LIMIT 1)", tuple(put.values()))
                c.commit()
        finally:
            c.close()
    except Exception as exc:
        _logger.warning(f"restore: FSSAI kept from this PC could not be written back: {exc}")


WOULD_LOSE = "WOULD_LOSE:"

# What a restore would throw away: records in the store now that the incoming file does
# not have. Matched on what a person sees (bill / purchase / return numbers) and, for
# payments, on party + amount + date + entry time -- never on row ids, which two devices
# continuing from the same file hand out twice.
_LOSS_CHECKS = (
    ("sales", ("bill_no",), "bills"),
    ("purchases", ("purchase_no",), "purchases"),
    ("sales_returns", ("return_no",), "sales returns"),
    ("purchase_returns", ("return_no",), "purchase returns"),
    ("customer_payments", ("customer_id", "amount", "payment_date", "created_at"), "customer payments"),
    ("supplier_payments", ("supplier_id", "amount", "payment_date", "created_at"), "supplier payments"),
)


def would_lose(local_db: str, incoming_db: str) -> dict:
    """{label: [shown keys]} of records on this device that the incoming file lacks.

    One device at a time, whole file passed over Drive: the file a device restores must
    be newer than its own work. A bill made here and not backed up before taking the
    other device's file is lost by the restore -- this says which, before it happens."""
    import sqlite3

    if not local_db or not os.path.isfile(local_db) or not os.path.isfile(incoming_db):
        return {}
    out: dict = {}
    a = sqlite3.connect(f"file:{local_db}?mode=ro", uri=True)
    b = sqlite3.connect(f"file:{incoming_db}?mode=ro", uri=True)
    try:
        for table, cols, label in _LOSS_CHECKS:
            def keys(conn):
                have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                use = [c for c in cols if c in have]
                if not use:
                    return None
                where = " AND ".join(f"COALESCE({c},0)=0" for c in ("deleted", "is_autosave") if c in have) or "1=1"
                return {tuple(str(v) for v in r) for r in conn.execute(
                    f"SELECT {', '.join(use)} FROM {table} WHERE {where}")}
            mine, theirs = keys(a), keys(b)
            if mine is None or theirs is None:
                continue
            missing = sorted(mine - theirs)
            if missing:
                out[label] = [" ".join(k) for k in missing]
    finally:
        a.close()
        b.close()
    return out


def _loss_message(lost: dict) -> str:
    parts = []
    for label, keys in lost.items():
        sample = ", ".join(keys[:8]) + (" …" if len(keys) > 8 else "")
        parts.append(f"{len(keys)} {label} ({sample})")
    return (
        WOULD_LOSE + " Ya PC var ase " + "; ".join(parts) + " aahet je navin backup file madhe NAHIT. "
        "Restore kela tar te jaatil. Aadhi ya PC cha Backup Now ghya ani dusrya device var Sync kara -- "
        "kiva he jaane manya asel tarach pudhe ja."
    )


def _apply_restored_db(result: dict, store_name: str, store_key: str,
                       *, close_conn=None, allow_loss: bool = False) -> tuple:
    """Put a downloaded/copied backup in place as the store's veterinary.db.

    Shared by the Drive restore and the USB restore so a pendrive copy lands
    with exactly the same care: live connections closed, WAL sidecars cleared,
    Online watermarks reset, and the row counts verified afterwards.
    """
    tmp_dir = result.get('tmp_dir')
    try:
        from core.store_manager import get_store_db_path, get_store_dir
        get_store_dir(store_key)
        dest = get_store_db_path(store_key)

        # Must close the live desktop/Tk connection + stop Online poller first,
        # otherwise Windows keeps the old DB and the UI looks "not updated".
        if not allow_loss:
            lost = would_lose(dest, result['db_path'])
            if lost:
                return False, _loss_message(lost)

        _close_all_db_users(extra_conn=close_conn)

        before = _sale_stats(dest) if os.path.isfile(dest) else {'count': 0}
        kept_profile = _profile_extras(dest)
        _replace_store_database(result['db_path'], dest)
        _restore_profile_extras(dest, kept_profile)
        after = _sale_stats(dest)
        _after_restore_sync_policy()

        expected = int(result.get('sale_count') or 0)
        got = int(after.get('count') or 0)
        if expected and got and got < expected:
            return False, (
                f"Restore incomplete: backup had {expected} sales but local DB "
                f"now has {got}. Close the app fully and try again."
            )

        msg = (
            f"Store: {store_name}\n"
            f"Backup file: {result.get('backup_file', '')}\n"
            f"Local database: {dest}\n"
            f"Before: {before.get('count', 0)} sales → After: {got} sales "
            f"(latest {after.get('latest', '—')})\n"
            f"Medicines: {after.get('medicines', 0)} · "
            f"Customers: {after.get('customers', 0)} · "
            f"Purchases: {after.get('purchases', 0)}\n"
            "Restart the app to load the restored database.\n"
            "Online poller will not auto-overwrite this restore "
            "(use Pull/Push manually if needed)."
        )
        return True, {
            "message": msg,
            "sale_count": got,
            "medicine_count": after.get('medicines', 0),
            "customer_count": after.get('customers', 0),
            "purchase_count": after.get('purchases', 0),
            "backup_file": result.get('backup_file', ''),
            "db_path": dest,
            "needs_restart": True,
        }
    except Exception as e:
        return False, str(e)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Restore from a USB stick / local folder ──────────────────────────────────
#
# Backup has written to a pendrive since the beginning; restore could only ever
# read from Google Drive. So the copy on the stick in the shop's drawer -- the
# one that survives a dead internet connection, a disabled OAuth client, or a
# PC that was never given a Drive folder -- could not be put back without the
# developer. It goes through exactly the same _apply_restored_db as the Drive
# path, so the safety around it (close connections, clear WAL, reset Online
# watermarks, verify counts) is not a second implementation.
def _local_backup_dirs_for_store(store_name: str, roots=None) -> list:
    """Every SatpudaCore_Backup/<store>/ folder on the given roots."""
    safe_name = _drive_subfolder_name(store_name)
    out = []
    for root in (roots if roots is not None else _detect_pendrives()):
        if not root:
            continue
        folder = os.path.join(root, 'SatpudaCore_Backup', safe_name)
        if os.path.isdir(folder):
            out.append(folder)
        elif os.path.isdir(root) and os.path.basename(
            os.path.normpath(root)
        ) == safe_name:
            # The owner may point straight at the store folder.
            out.append(root)
    return out


def list_local_backups(store_name: str, roots=None) -> tuple:
    """List USB / local backup files for a store, newest first.

    Returns (True, [{'path','name','label','size','modifiedTime','source'}, ...])
    or (False, error_message).
    """
    folders = _local_backup_dirs_for_store(store_name, roots)
    if not folders:
        drives = roots if roots is not None else _detect_pendrives()
        if not drives:
            return False, (
                'No USB drive is connected. Plug in the pendrive that holds the '
                'backups and try again.'
            )
        return False, (
            f'No backup folder for store "{store_name}" on the connected drive(s).\n'
            f'Expected: SatpudaCore_Backup\\{_drive_subfolder_name(store_name)}'
        )

    items = []
    for folder in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not _is_drive_backup_filename(name):
                continue
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            try:
                stat = os.stat(full)
            except OSError:
                continue
            mod = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
            items.append({
                'path': full,
                'name': name,
                'size': stat.st_size,
                'modifiedTime': mod,
                'source': folder,
                'label': f"{name}  ({mod})",
            })
    if not items:
        return False, (
            f'No backup files found on the USB drive for store "{store_name}".\n'
            'Supported names: SatpudaCore_YYYY-MM-DD_HH-MM.db.gz or .db'
        )
    items.sort(key=_backup_name_sort_key, reverse=True)
    return True, items


def _read_local_backup_file(path: str, tmp_dir: str) -> tuple:
    """Copy one local backup into tmp_dir and materialize veterinary.db."""
    name = os.path.basename(path)
    src_path = os.path.join(tmp_dir, name)
    db_path = os.path.join(tmp_dir, 'veterinary.db')
    shutil.copy2(path, src_path)

    if not _materialize_backup_file(src_path, db_path):
        return False, None
    stats = _sale_stats(db_path)
    if os.path.getsize(db_path) < 50_000 and stats['count'] == 0:
        return False, None
    try:
        _upgrade_restored_db(db_path)
    except Exception as e:
        _logger.error(f"Schema upgrade after USB restore failed for {name}: {e}")
        return False, None
    stats = _sale_stats(db_path)
    return True, {
        'db_path': db_path,
        'backup_file': name,
        'tmp_dir': tmp_dir,
        'sale_count': stats['count'],
        'medicine_count': stats.get('medicines', 0),
        'customer_count': stats.get('customers', 0),
        'purchase_count': stats.get('purchases', 0),
        'latest_bill': stats.get('latest', '—'),
    }


def restore_local_backup_to_store(store_name: str, store_key: str, *,
                                  path: str = '', close_conn=None,
                                  roots=None, allow_loss: bool = False) -> tuple:
    """Restore a USB / local backup into the store's veterinary.db.

    path='' picks the newest usable file found on the connected drives.
    """
    candidates = []
    if path:
        if not os.path.isfile(path):
            return False, f'Backup file not found: {path}'
        candidates = [{'path': path, 'name': os.path.basename(path)}]
    else:
        ok, listing = list_local_backups(store_name, roots)
        if not ok:
            return False, listing
        candidates = listing

    tmp_dir = tempfile.mkdtemp()
    last_err = ''
    for candidate in candidates:
        ok, result = _read_local_backup_file(candidate['path'], tmp_dir)
        if ok and result:
            _logger.info(
                f"USB restore - {result['backup_file']} for store {store_name}"
            )
            result['store_name'] = store_name
            return _apply_restored_db(
                result, store_name, store_key, close_conn=close_conn, allow_loss=allow_loss
            )
        last_err = candidate.get('name', '')
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return False, (
        f'The USB backup for "{store_name}" looks empty or corrupt'
        + (f' ({last_err}).' if last_err else '.')
        + '\nTry another file from the list.'
    )


def sync_active_store_from_drive(*, close_conn=None, file_id: str = None,
                                 allow_loss: bool = False) -> tuple:
    """One-click sync: replace the active store DB with a Drive backup (latest or selected)."""
    try:
        from core.store_manager import get_active_store, get_active_display_name, has_registry
    except Exception as e:
        return False, str(e)

    if not has_registry():
        return False, 'No store is configured on this device.'

    store = get_active_store()
    if not store:
        name = get_active_display_name()
        if not name:
            return False, 'No active store. Open Store Management and select a store.'
        store = {'display_name': name, 'store_key': None}

    display_name = store.get('display_name', '')
    store_key = store.get('store_key')
    if not store_key:
        from core.store_manager import display_name_key
        store_key = display_name_key(display_name)

    ok, result = restore_latest_backup_to_store(
        display_name, store_key, close_conn=close_conn, file_id=file_id, allow_loss=allow_loss,
    )
    if not ok:
        return False, result
    if isinstance(result, dict):
        return True, str(result.get("message") or result)
    return True, str(result)


# Public API
def run_backup_on_open(on_error=None):
    """On app open: respects dedup window, fills open1/open2 slot."""
    if not is_auto_backup_enabled():
        return
    t = threading.Thread(target=lambda: _do_backup(force=False, trigger='open', on_error=on_error), daemon=True)
    t.start()

def run_backup_silently(on_error=None):
    """Hourly scheduler: always runs. Hourly files get no slot — they are
    eligible for mid-day cleanup once today has more than 4 files."""
    if not is_auto_backup_enabled():
        return
    t = threading.Thread(target=lambda: _do_backup(force=True, trigger='hourly', on_error=on_error), daemon=True)
    t.start()

def run_backup_now(manual: bool = False) -> dict:
    """Backup on close (when auto enabled) or manual 'Backup Now' from settings.

    Waits up to 90 seconds for the backup thread to finish, then returns what
    the backup actually did (see _backup_result). Callers that only want the
    side effect can keep ignoring the return value.
    """
    if not manual and not is_auto_backup_enabled():
        return _backup_result(
            'skipped', 'auto_disabled',
            'Automatic backup is turned off for this PC.', trigger='close',
        )
    trigger = 'manual' if manual else 'close'
    holder: dict = {}

    def _run():
        holder['result'] = _do_backup(force=True, trigger=trigger)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=90)
    if 'result' in holder:
        return holder['result']
    if t.is_alive():
        return _backup_result(
            'error', 'timeout',
            'Backup is still running after 90 seconds. Check again in a minute.',
            trigger=trigger,
        )
    return _backup_result(
        'error', 'failed', 'Backup did not finish.', trigger=trigger
    )


def last_backup_log_message() -> str:
    """Return the last non-empty line from backup_log.txt, or empty string."""
    try:
        path = _log_path()
        if not os.path.exists(path):
            return ''
        with open(path, encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]
        return lines[-1] if lines else ''
    except Exception:
        return ''
