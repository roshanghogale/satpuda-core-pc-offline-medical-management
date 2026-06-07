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
        # Update folder ID only — keep the active/local store name.
        keep_name = get_backup_store_name(dst_cfg)
        try:
            _write_config_file(dst, bundled_id, keep_name)
            _logger.info(
                f"Updated backup_config.dat folder ID from EXE bundle; "
                f"kept store name: {keep_name}"
            )
        except Exception as e:
            _logger.error(f"Failed to merge backup_config.dat: {e}")
            return True
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


def is_auto_backup_enabled() -> bool:
    """When False, skip backup on open, close, and hourly scheduler."""
    path = _auto_backup_pref_path()
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip().lower() in ('1', 'true', 'yes', 'on')
    except Exception:
        return False


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
        if (cfg.get('store_name') or '').strip() != name:
            write_backup_config(folder_id, name)
    except Exception as e:
        _logger.error(f"sync_backup_config_to_active_store failed: {e}")


def get_backup_config_status() -> dict:
    """Return {configured, folder_id, store_name, creds_ok} for UI."""
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
    }


def _read_backup_config() -> dict:
    """AppData override first, then EXE-bundled config (works on any new PC)."""
    for path in (_config_path(), _bundled_config_path() or ''):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'rb') as f:
                cfg = _decrypt_dict(f.read())
            if cfg.get('folder_id'):
                return cfg
        except Exception:
            continue
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
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def _drive_subfolder_name(store_name: str) -> str:
    try:
        from core.store_manager import display_name_key
        return display_name_key(store_name)
    except Exception:
        return f"Store_{store_name.replace(' ', '_')}"


def _ensure_store_subfolder(service, parent_folder_id: str, store_name: str) -> str:
    safe_name = _drive_subfolder_name(store_name)
    q = (f"'{parent_folder_id}' in parents "
         f"and name='{safe_name}' "
         f"and mimeType='application/vnd.google-apps.folder' "
         f"and trashed=false")
    res = service.files().list(q=q, fields='files(id)').execute()
    files = res.get('files', [])
    if files:
        return files[0]['id']
    meta = {
        'name': safe_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_folder_id],
    }
    folder = service.files().create(body=meta, fields='id').execute()
    return folder['id']

def _upload_file(service, file_path: str, folder_id: str, filename: str):
    from googleapiclient.http import MediaFileUpload
    meta = {'name': filename, 'parents': [folder_id]}
    media = MediaFileUpload(file_path, mimetype='application/gzip', resumable=False)
    service.files().create(body=meta, media_body=media, fields='id').execute()

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
def _detect_pendrive() -> str:
    """Return drive letter of first connected removable USB drive, or empty string."""
    import ctypes
    DRIVE_REMOVABLE = 2
    for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        root = f"{letter}:\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE:
                if os.path.exists(root):
                    return root
        except Exception:
            pass
    return ''

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

def _do_pendrive_backup(gz_path: str, filename: str, store_name: str, protected: set):
    drive = _detect_pendrive()
    if not drive:
        return
    safe_name = _drive_subfolder_name(store_name)
    dest_dir = os.path.join(drive, 'SatpudaCore_Backup', safe_name)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(gz_path, os.path.join(dest_dir, filename))
        _cleanup_pendrive_backups(dest_dir, protected)
        _logger.info(f"Pendrive backup OK - {filename} -> {drive}")
    except Exception as e:
        _logger.error(f"Pendrive backup failed: {e}")


# Core backup logic
def _do_backup(force: bool = False, trigger: str = 'open', on_error=None):
    """Run a backup.
    force=False  → skip if a backup ran within _DEDUP_MINUTES (used on app open).
    force=True   → always run (used on app close and hourly scheduler).
    trigger      → 'open' | 'close' | 'hourly'  — determines which slot to fill.
    """
    global _last_backup_time
    if getattr(sys, 'frozen', False):
        try:
            seed_bundled_backup_files()
        except Exception:
            pass
    tmp_dir = None
    try:

        # On-open dedup: skip if app was just closed and reopened within 5 min
        if not force and _last_backup_time is not None:
            elapsed = (datetime.now() - _last_backup_time).total_seconds() / 60
            if elapsed < _DEDUP_MINUTES:
                _logger.info(f"On-open backup skipped - last backup was {elapsed:.1f} min ago.")
                return

        cfg = _read_backup_config()
        if not cfg or not cfg.get('folder_id'):
            _logger.info("Backup skipped - backup_config.dat missing or invalid.")
            return

        db = _db_path()
        if not os.path.exists(db):
            _logger.warning("Backup skipped - veterinary.db not found.")
            return

        store_name = get_backup_store_name(cfg)
        ts         = datetime.now().strftime('%Y-%m-%d_%H-%M')
        filename   = f"SatpudaCore_{ts}.db.gz"

        tmp_dir = tempfile.mkdtemp()
        tmp_db  = os.path.join(tmp_dir, 'veterinary.db')
        tmp_gz  = os.path.join(tmp_dir, filename)
        shutil.copy2(db, tmp_db)
        with open(tmp_db, 'rb') as f_in, gzip.open(tmp_gz, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

        # Register filename in the correct slot BEFORE cleanup runs
        _register_filename(filename, trigger)
        _last_backup_time = datetime.now()
        protected = _protected_filenames()

        # Pendrive backup (always runs, no internet needed)
        _do_pendrive_backup(tmp_gz, filename, store_name, protected)

        # Google Drive backup (only if internet available)
        if not _is_internet_available():
            _logger.info("Drive backup skipped - no internet.")
            return

        token_data = _read_oauth_token()
        if not token_data or not token_data.get('refresh_token'):
            _logger.info("Drive backup skipped - backup_creds.dat missing or invalid.")
            return

        try:
            service   = _get_drive_service(token_data)
            subfolder = _ensure_store_subfolder(service, cfg['folder_id'], store_name)
            _upload_file(service, tmp_gz, subfolder, filename)
            _cleanup_old_backups(service, subfolder, protected)
            _logger.info(f"Backup OK [{trigger}] - {filename} -> {store_name}")
        except Exception as drive_err:
            err_msg = str(drive_err)
            _logger.error(f"Backup Drive error: {err_msg}")
            if on_error:
                on_error(err_msg)

    except Exception as e:
        _logger.error(f"Backup failed: {e}")
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _find_store_subfolder_id(service, parent_folder_id: str, store_name: str) -> str:
    safe_name = _drive_subfolder_name(store_name)
    q = (f"'{parent_folder_id}' in parents "
         f"and name='{safe_name}' "
         f"and mimeType='application/vnd.google-apps.folder' "
         f"and trashed=false")
    res = service.files().list(q=q, fields='files(id)').execute()
    files = res.get('files', [])
    return files[0]['id'] if files else ''


def restore_latest_backup_from_drive(store_name: str) -> tuple:
    """Download the most recent Drive backup for store_name.
    Returns (True, dest_db_path) or (False, error_message)."""
    cfg = _read_backup_config()
    if not cfg or not cfg.get('folder_id'):
        return False, 'Backup is not configured. Set the Drive folder ID in Administrator settings.'

    if not _is_internet_available():
        return False, 'No internet connection. Connect and try again.'

    token_data = _read_oauth_token()
    if not token_data or not token_data.get('refresh_token'):
        return False, 'Backup credentials are missing or invalid.'

    tmp_dir = None
    try:
        service = _get_drive_service(token_data)
        subfolder_id = _find_store_subfolder_id(service, cfg['folder_id'], store_name)
        if not subfolder_id:
            return False, (
                f'No backup folder found on Drive for store "{store_name}".\n'
                f'Expected folder: {_drive_subfolder_name(store_name)}'
            )

        res = service.files().list(
            q=f"'{subfolder_id}' in parents and trashed=false and name contains 'SatpudaCore_'",
            fields='files(id, name)',
            orderBy='name desc',
        ).execute()
        files = [f for f in res.get('files', []) if f.get('name', '').endswith('.db.gz')]
        if not files:
            return False, (
                f'No backup files found in Drive folder for store "{store_name}".\n'
                'Ask the admin device to run at least one backup first.'
            )

        latest = files[0]['name']
        tmp_dir = tempfile.mkdtemp()
        gz_path = os.path.join(tmp_dir, latest)
        db_path = os.path.join(tmp_dir, 'veterinary.db')

        from googleapiclient.http import MediaIoBaseDownload
        import io
        request = service.files().get_media(fileId=files[0]['id'])
        with open(gz_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        with gzip.open(gz_path, 'rb') as f_in, open(db_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

        if not os.path.isfile(db_path) or os.path.getsize(db_path) < 100:
            return False, 'Downloaded backup file is empty or corrupt.'

        _logger.info(f"Restore OK - {latest} for store {store_name}")
        return True, {
            'db_path': db_path,
            'backup_file': latest,
            'store_name': store_name,
            'tmp_dir': tmp_dir,
        }
    except Exception as e:
        _logger.error(f"Restore failed: {e}")
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, str(e)


def restore_latest_backup_to_store(store_name: str, store_key: str, *, close_conn=None) -> tuple:
    """Restore Drive backup into the store's local veterinary.db.
    Pass close_conn (sqlite3.Connection) so the file can be replaced on Windows."""
    ok, result = restore_latest_backup_from_drive(store_name)
    if not ok:
        return False, result

    tmp_dir = result.get('tmp_dir')
    try:
        from core.store_manager import get_store_db_path, get_store_dir
        get_store_dir(store_key)
        dest = get_store_db_path(store_key)

        if close_conn is not None:
            try:
                close_conn.close()
            except Exception:
                pass

        shutil.copy2(result['db_path'], dest)
        msg = (
            f"Store: {store_name}\n"
            f"Backup file: {result.get('backup_file', '')}\n"
            f"Local database: {dest}"
        )
        return True, msg
    except Exception as e:
        return False, str(e)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def sync_active_store_from_drive(*, close_conn=None) -> tuple:
    """One-click sync: replace the active store DB with the latest Drive backup."""
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

    return restore_latest_backup_to_store(
        display_name, store_key, close_conn=close_conn,
    )


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

def run_backup_now(manual: bool = False):
    """Backup on close (when auto enabled) or manual 'Backup Now' from settings.
    Waits up to 90 seconds for the backup thread to finish before returning.
    """
    if not manual and not is_auto_backup_enabled():
        return
    trigger = 'manual' if manual else 'close'
    t = threading.Thread(
        target=lambda: _do_backup(force=True, trigger=trigger), daemon=True)
    t.start()
    t.join(timeout=90)


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
