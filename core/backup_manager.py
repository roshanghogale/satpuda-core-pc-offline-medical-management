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

def _get_secret() -> bytes:
    """Derive encryption key from machine UUID + app name (not hardcoded)."""
    try:
        import uuid
        machine_id = str(uuid.getnode()).encode()
    except Exception:
        machine_id = b'SatpudaVetApp'
    return machine_id + b'_SatpudaCoreVet2026'
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
    from core.license_manager import _appdata_dir
    return os.path.join(_appdata_dir(), 'backup_slots.dat')

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
def _creds_path():
    """backup_creds.dat - encrypted OAuth2 token JSON, bundled with app."""
    if getattr(sys, 'frozen', False):
        bundled = os.path.join(sys._MEIPASS, 'config', 'backup_creds.dat')
        if os.path.exists(bundled):
            return bundled
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'config', 'backup_creds.dat')

def _config_path():
    from core.license_manager import _appdata_dir
    return os.path.join(_appdata_dir(), 'backup_config.dat')

def _db_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'veterinary.db')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'veterinary.db')


# Encryption
def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        raw = hashlib.sha256(_get_secret()).digest()
        key = base64.urlsafe_b64encode(raw)
        return Fernet(key)
    except Exception:
        return None

def _decrypt_bytes(data: bytes) -> bytes:
    f = _get_fernet()
    if f:
        try:
            return f.decrypt(data)
        except Exception:
            return b''
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
    f = _get_fernet()
    if f:
        return f.encrypt(json.dumps(d).encode())
    return json.dumps(d).encode()


# Public: write backup_config.dat
def write_backup_config(folder_id: str, store_name: str):
    data = {
        'folder_id':    folder_id,
        'store_name':   store_name,
        'backup_count': _MAX_BACKUPS,
    }
    path = _config_path()
    try:
        with open(path, 'wb') as f:
            f.write(_encrypt_dict(data))
        _logger.info(f"backup_config.dat written for store: {store_name}")
    except Exception as e:
        _logger.error(f"Failed to write backup_config.dat: {e}")

def _read_backup_config() -> dict:
    path = _config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            return _decrypt_dict(f.read())
    except Exception:
        return {}

def _read_oauth_token() -> dict:
    path = _creds_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            raw = _decrypt_bytes(f.read())
        return json.loads(raw.decode()) if raw else {}
    except Exception:
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


def _ensure_store_subfolder(service, parent_folder_id: str, store_name: str) -> str:
    safe_name = f"Store_{store_name.replace(' ', '_')}"
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
    safe_name = f"Store_{store_name.replace(' ', '_')}"
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

        store_name = cfg.get('store_name', 'UnknownStore')
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


# Public API
def run_backup_on_open(on_error=None):
    """On app open: respects dedup window, fills open1/open2 slot."""
    t = threading.Thread(target=lambda: _do_backup(force=False, trigger='open', on_error=on_error), daemon=True)
    t.start()

def run_backup_silently(on_error=None):
    """Hourly scheduler: always runs. Hourly files get no slot — they are
    eligible for mid-day cleanup once today has more than 4 files."""
    t = threading.Thread(target=lambda: _do_backup(force=True, trigger='hourly', on_error=on_error), daemon=True)
    t.start()

def run_backup_now():
    """Non-blocking backup for app close and manual trigger — fills close slot.
    Waits up to 20 seconds for the backup thread to finish before returning.
    """
    t = threading.Thread(
        target=lambda: _do_backup(force=True, trigger='close'), daemon=True)
    t.start()
    t.join(timeout=20)
