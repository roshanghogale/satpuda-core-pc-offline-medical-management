"""
Multi-store management — local SQLite per store, Drive subfolder per store name.

Each store lives under %LOCALAPPDATA%/VeterinaryApp/stores/<Store_Key>/veterinary.db
Drive backups use Store_<name> under the configured parent folder ID.
"""

from __future__ import annotations

import os
import re
import sys
import json
import shutil
from datetime import datetime
from typing import Optional

_REGISTRY_FILE = 'stores_registry.dat'


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as _dir
    return _dir()


def _registry_path() -> str:
    return os.path.join(_appdata_dir(), _REGISTRY_FILE)


def _encrypt_registry(data: dict) -> bytes:
    from core.license_manager import _encrypt
    return _encrypt(data)


def _decrypt_registry(data: bytes) -> dict:
    from core.license_manager import _decrypt
    return _decrypt(data) if data else {}


def normalize_display_name(name: str) -> str:
    return ' '.join((name or '').strip().split())


def display_name_key(display_name: str) -> str:
    """Local folder + Drive subfolder key: Store_<spaces_to_underscores>."""
    safe = normalize_display_name(display_name).replace(' ', '_')
    safe = re.sub(r'[^\w\-]', '_', safe)
    safe = re.sub(r'_+', '_', safe).strip('_')
    return f'Store_{safe or "Unnamed"}'


def names_match(a: str, b: str) -> bool:
    return display_name_key(a).lower() == display_name_key(b).lower()


def get_stores_root() -> str:
    path = os.path.join(_appdata_dir(), 'stores')
    os.makedirs(path, exist_ok=True)
    return path


def get_legacy_db_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'veterinary.db')
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'veterinary.db',
    )


def get_store_dir(store_key: str) -> str:
    path = os.path.join(get_stores_root(), store_key)
    os.makedirs(path, exist_ok=True)
    return path


def get_store_db_path(store_key: str) -> str:
    return os.path.join(get_store_dir(store_key), 'veterinary.db')


def get_store_slots_path(store_key: str) -> str:
    return os.path.join(get_store_dir(store_key), 'backup_slots.dat')


def _display_name_from_store_key(store_key: str) -> str:
    if store_key.startswith('Store_'):
        return store_key[6:].replace('_', ' ')
    return store_key


def load_registry(*, _allow_reconcile: bool = True) -> dict:
    path = _registry_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        if not raw:
            return {}
        data = _decrypt_registry(raw)
        if not isinstance(data, dict):
            data = {}
        data.setdefault('stores', [])
        data.setdefault('device_role', 'admin')
        if _allow_reconcile and not data.get('stores'):
            if reconcile_registry_with_disk():
                return load_registry(_allow_reconcile=False)
        return data
    except Exception:
        if _allow_reconcile and reconcile_registry_with_disk():
            return load_registry(_allow_reconcile=False)
        return {}


def save_registry(data: dict):
    """Atomic write so registry is never left half-written on crash/restart."""
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dict(data or {})
    data.setdefault('stores', [])
    data.setdefault('device_role', 'admin')
    data['updated_at'] = datetime.now().isoformat(timespec='seconds')
    payload = _encrypt_registry(data)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'wb') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def reconcile_registry_with_disk() -> bool:
    """Recover registry entries from store folders on disk (dev + prod AppData)."""
    root = get_stores_root()
    if not os.path.isdir(root):
        return False

    reg = load_registry(_allow_reconcile=False)
    changed = False
    stores = list(reg.get('stores') or [])
    known_keys = {s.get('store_key') for s in stores if s.get('store_key')}

    for name in sorted(os.listdir(root)):
        dir_path = os.path.join(root, name)
        if not os.path.isdir(dir_path):
            continue
        if not name.startswith('Store_'):
            continue
        db_file = os.path.join(dir_path, 'veterinary.db')
        if not os.path.isfile(db_file):
            continue
        if name in known_keys:
            continue
        stores.append({
            'store_key': name,
            'display_name': _display_name_from_store_key(name),
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'recovered_from_disk': True,
        })
        known_keys.add(name)
        changed = True

    store_keys = [s.get('store_key') for s in stores if s.get('store_key')]
    active = (reg.get('active_store_key') or '').strip()
    if store_keys and active not in store_keys:
        reg['active_store_key'] = store_keys[0]
        changed = True
    if not reg.get('device_role'):
        reg['device_role'] = 'admin'
        changed = True

    if changed:
        reg['stores'] = stores
        save_registry(reg)
    return changed


def ensure_registry_on_startup():
    """Run before DB open — keeps registry aligned with on-disk store folders."""
    reconcile_registry_with_disk()


def has_registry() -> bool:
    reg = load_registry()
    return bool(reg.get('stores'))


def list_stores() -> list[dict]:
    reg = load_registry()
    return list(reg.get('stores') or [])


def get_device_role() -> str:
    return (load_registry().get('device_role') or 'admin').strip()


def is_satellite_device() -> bool:
    return get_device_role() == 'satellite'


def get_active_store_key() -> str:
    reg = load_registry()
    key = (reg.get('active_store_key') or '').strip()
    if key:
        return key
    stores = reg.get('stores') or []
    if stores:
        return stores[0].get('store_key', '')
    return ''


def get_active_store() -> Optional[dict]:
    key = get_active_store_key()
    if not key:
        return None
    for s in list_stores():
        if s.get('store_key') == key:
            return s
    return None


def get_active_display_name() -> str:
    store = get_active_store()
    if store:
        return store.get('display_name') or store.get('store_key', '')
    return ''


def get_active_db_path() -> str:
    key = get_active_store_key()
    if key:
        return get_store_db_path(key)
    return get_legacy_db_path()


def get_active_slots_path() -> str:
    key = get_active_store_key()
    if key:
        return get_store_slots_path(key)
    from core.license_manager import _appdata_dir
    return os.path.join(_appdata_dir(), 'backup_slots.dat')


def _find_store_by_key(store_key: str) -> Optional[dict]:
    for s in list_stores():
        if s.get('store_key') == store_key:
            return s
    return None


def _find_store_by_display_name(display_name: str) -> Optional[dict]:
    for s in list_stores():
        if names_match(s.get('display_name', ''), display_name):
            return s
    return None


def _sync_backup_config_for_store(display_name: str):
    try:
        from core.backup_manager import _read_backup_config, write_backup_config
        cfg = _read_backup_config()
        folder_id = (cfg.get('folder_id') or '').strip()
        if folder_id:
            write_backup_config(folder_id, display_name)
    except Exception:
        pass


def _migrate_legacy_slots(store_key: str):
    from core.license_manager import _appdata_dir
    legacy = os.path.join(_appdata_dir(), 'backup_slots.dat')
    dest = get_store_slots_path(store_key)
    if os.path.exists(legacy) and not os.path.exists(dest):
        try:
            shutil.copy2(legacy, dest)
        except Exception:
            pass


def _init_empty_db(db_path: str):
    import sqlite3
    from core.db_setup import initialise
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        initialise(conn)
        conn.commit()
    finally:
        conn.close()


def _copy_or_move_legacy_db(dest_path: str, move: bool = False) -> bool:
    legacy = get_legacy_db_path()
    if not os.path.isfile(legacy):
        return False
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if os.path.exists(dest_path):
        return True
    if move:
        shutil.move(legacy, dest_path)
    else:
        shutil.copy2(legacy, dest_path)
    return True


def create_store(display_name: str, *, device_role: str = 'admin',
                 empty_db: bool = True, migrate_legacy: bool = False,
                 activate: bool = True) -> dict:
    display_name = normalize_display_name(display_name)
    if not display_name:
        raise ValueError('Store name is required.')

    existing = _find_store_by_display_name(display_name)
    if existing:
        raise ValueError(f'Store "{display_name}" already exists on this device.')

    store_key = display_name_key(display_name)
    db_path = get_store_db_path(store_key)

    if migrate_legacy:
        if not _copy_or_move_legacy_db(db_path, move=True):
            _init_empty_db(db_path)
    elif empty_db and not os.path.exists(db_path):
        _init_empty_db(db_path)

    entry = {
        'store_key': store_key,
        'display_name': display_name,
        'created_at': datetime.now().isoformat(timespec='seconds'),
    }

    reg = load_registry(_allow_reconcile=False)
    stores = list(reg.get('stores') or [])
    stores.append(entry)
    reg['stores'] = stores
    if activate or not reg.get('active_store_key'):
        reg['active_store_key'] = store_key
    if device_role in ('admin', 'satellite'):
        reg['device_role'] = device_role
    save_registry(reg)

    _migrate_legacy_slots(store_key)
    if activate or reg.get('active_store_key') == store_key:
        _sync_backup_config_for_store(display_name)
    return entry


def set_active_store(store_key: str) -> bool:
    if not _find_store_by_key(store_key):
        return False
    reg = load_registry()
    reg['active_store_key'] = store_key
    save_registry(reg)
    store = _find_store_by_key(store_key)
    if store:
        _sync_backup_config_for_store(store.get('display_name', ''))
    return True


def setup_initial_store_on_activation(display_name: str) -> dict:
    """First activation: create initial store and migrate legacy veterinary.db if present."""
    display_name = normalize_display_name(display_name)
    if not display_name:
        raise ValueError('Initial store name is required.')

    if has_registry():
        store = _find_store_by_display_name(display_name)
        if store:
            set_active_store(store['store_key'])
            return store
        return create_store(display_name, device_role='admin', migrate_legacy=False)

    return create_store(
        display_name,
        device_role='admin',
        empty_db=not os.path.isfile(get_legacy_db_path()),
        migrate_legacy=os.path.isfile(get_legacy_db_path()),
    )


def setup_satellite_store_from_restore(display_name: str, db_path: str) -> dict:
    display_name = normalize_display_name(display_name)
    if not display_name:
        raise ValueError('Store name is required.')

    if has_registry():
        raise ValueError('This device is already linked to a store.')

    store_key = display_name_key(display_name)
    dest = get_store_db_path(store_key)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if not os.path.isfile(db_path):
        raise ValueError('Restored database file is missing.')
    shutil.copy2(db_path, dest)

    entry = {
        'store_key': store_key,
        'display_name': display_name,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'restored_from_drive': True,
    }
    reg = {
        'device_role': 'satellite',
        'active_store_key': store_key,
        'stores': [entry],
    }
    save_registry(reg)
    _sync_backup_config_for_store(display_name)
    return entry


def update_active_store_display_name(new_display_name: str) -> dict:
    """Rename active store (updates local folder key and backup target name)."""
    new_display_name = normalize_display_name(new_display_name)
    if not new_display_name:
        raise ValueError('Store name is required.')

    store = get_active_store()
    if not store:
        raise ValueError('No active store.')

    old_key = store['store_key']
    new_key = display_name_key(new_display_name)

    if old_key != new_key:
        for s in list_stores():
            if s.get('store_key') == new_key:
                raise ValueError(
                    f'Store "{new_display_name}" already exists on this device.'
                )

    old_dir = get_store_dir(old_key)
    new_dir = get_store_dir(new_key)

    if old_key != new_key and os.path.isdir(old_dir):
        if os.path.isdir(new_dir) and os.listdir(new_dir):
            raise ValueError(
                f'Cannot rename — folder {new_key} already exists locally.'
            )
        if os.path.isdir(new_dir):
            os.rmdir(new_dir)
        shutil.move(old_dir, new_dir)

    reg = load_registry()
    for s in reg.get('stores', []):
        if s.get('store_key') == old_key:
            s['store_key'] = new_key
            s['display_name'] = new_display_name
            break
    if reg.get('active_store_key') == old_key:
        reg['active_store_key'] = new_key
    save_registry(reg)
    _sync_backup_config_for_store(new_display_name)
    return _find_store_by_key(new_key) or {}


def ensure_startup_migration():
    """Upgrade path: activated app with legacy db but no stores registry yet."""
    if has_registry():
        return
    if not os.path.isfile(get_legacy_db_path()):
        return

    display_name = 'Main Store'
    try:
        from core.backup_manager import get_backup_config_status
        st = get_backup_config_status()
        if st.get('store_name'):
            display_name = normalize_display_name(st['store_name'])
    except Exception:
        pass

    try:
        create_store(
            display_name,
            device_role='admin',
            empty_db=False,
            migrate_legacy=True,
        )
    except Exception:
        pass


def delete_local_store(store_key: str) -> bool:
    """Remove store from registry and delete local folder (not Drive)."""
    reg = load_registry()
    stores = [s for s in reg.get('stores', []) if s.get('store_key') != store_key]
    if len(stores) == len(reg.get('stores', [])):
        return False
    reg['stores'] = stores
    if reg.get('active_store_key') == store_key:
        reg['active_store_key'] = stores[0]['store_key'] if stores else ''
    save_registry(reg)
    store_dir = os.path.join(get_stores_root(), store_key)
    if os.path.isdir(store_dir):
        shutil.rmtree(store_dir, ignore_errors=True)
    if stores:
        _sync_backup_config_for_store(stores[0].get('display_name', ''))
    return True
