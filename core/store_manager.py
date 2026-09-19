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


class StoreNameTakenOnServer(ValueError):
    """A different store on the server already uses this name."""

    def __init__(self, name: str, remote: dict):
        self.name = name
        self.remote = remote or {}
        super().__init__(
            f'A store named "{name}" already exists on the server. '
            'Choose a different name, or connect to that store instead.'
        )


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


def _publish_store_registry_cloud():
    """No-op — store pairing is per-store on Satpuda Core Server (not Server)."""
    return


def _forget_store_scoped_caches():
    """Drop what was cached for the store we are leaving.

    The pictures are what the shop actually sees go wrong. core.store_images
    holds the store server's answer for a banner or a logo for two minutes, and
    core.pharmacy_profile_io holds the whole online profile -- name, address,
    GST, DL and logo_path -- for one; neither used to record WHICH store it had
    asked for, so for that long after a switch the new store was shown the old
    store's picture, and store_images then wrote those bytes into the new
    store's own folder, where they stayed.

    Both of those are keyed by store now, so for them this is belt and braces
    rather than the fix itself. It is here because a switch is the one moment
    nothing stale should survive, and because the next thing to be cached
    against a store will get this for free. Never raises: changing store must
    not fail on a cache.

    core.online_catalog is NOT belt and braces -- it is the third copy of the
    same defect and the only one still live. It holds the whole shop's
    catalogue (customers, suppliers, doctors, medicines) for 120s, and the bill
    customer names for 300s, under keys that are the bare collection name --
    "customers", "medicines_inventory" -- which is the same string for every
    shop on the PC, exactly as store_images' settings-row name was. Its flat
    indexes (_customer_by_id, _medicine_by_id, _supplier_by_name) carry no
    store either. Its on-disk snapshot already knows about stores
    (online_catalog_snapshot/<store>.json); only the memory in front of it did
    not.

    That reaches the printed bill: core/bill_output.py:205 builds a bill from
    online_catalog.find_customer_by_id / medicine_by_id, and
    core/billing_service.py reads the same cache all through a save. The
    desktop never restarts on a switch to clear it -- the switch_store handler
    returns needs_restart, and desktop/src/pages/settings/ImportDataPanels.tsx
    (the switch_store branch) is the one branch that does not act on it -- so
    without this the next two minutes of bills on shop B could be built from
    shop A's customer and medicine rows.
    """
    try:
        from core.store_images import _reset_server_cache
        _reset_server_cache()
    except Exception:
        pass
    try:
        from core.pharmacy_profile_io import forget_cached_profile
        forget_cached_profile()
    except Exception:
        pass
    try:
        from core.online_catalog import invalidate as _forget_catalog
        _forget_catalog()
    except Exception:
        pass


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
    # Refuse to overwrite a registry we could not read. A build that cannot
    # decrypt the existing file used to save over it, destroying every store
    # entry on the device. Preserve it instead so the data stays recoverable.
    if not (data or {}).get('stores') and os.path.exists(path):
        try:
            with open(path, 'rb') as _f:
                _existing = _f.read()
            from core.license_manager import decrypt_failed
            if decrypt_failed(_existing):
                keep = f"{path}.unreadable-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                shutil.copy2(path, keep)
                raise RuntimeError(
                    f'Store registry is unreadable by this build; refusing to '
                    f'overwrite it. Preserved a copy at {keep}. This usually '
                    f'means the build lacks the "cryptography" package.'
                )
        except RuntimeError:
            raise
        except Exception:
            pass
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


def _store_evidence_rank(root: str, store_key: str) -> tuple:
    """How strongly a folder looks like a store that has really been used.

    Higher sorts first. A local database outranks a server session, which
    outranks a folder holding nothing but a stray backup_slots.dat.
    """
    dir_path = os.path.join(root, store_key)
    has_db = os.path.isfile(os.path.join(dir_path, 'veterinary.db'))
    db_size = 0
    if has_db:
        try:
            db_size = os.path.getsize(os.path.join(dir_path, 'veterinary.db'))
        except OSError:
            db_size = 0
    has_session = False
    try:
        from core.server_api import _session_path

        has_session = os.path.isfile(_session_path(store_key))
    except Exception:
        pass
    return (1 if has_db else 0, 1 if has_session else 0, db_size, store_key)


def _preferred_active_store(root: str, store_keys: list) -> str:
    """Which store to activate when the registry no longer names a valid one."""
    ranked = sorted(store_keys, key=lambda k: _store_evidence_rank(root, k), reverse=True)
    try:
        from core.server_live import _load_adoptions

        # The ledger records which server store a local store belongs to -- a
        # statement of identity, where a folder name is only a folder name. But
        # it is written with sort_keys=True, so taking the FIRST entry would
        # just be the alphabet choosing again under a better name. Rank the
        # recorded stores the same way as any other, so a folder holding no
        # database can never win over one holding the shop's.
        recorded = [k for k in ranked if k in _load_adoptions()]
        if recorded:
            return recorded[0]
    except Exception:
        pass
    return ranked[0]


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
        # Online (server-only) stores never create a local veterinary.db, so
        # requiring one made every Online store invisible to recovery -- the
        # app then fell through to create_store("Default"). Accept any store
        # folder that carries a db OR any other store artefact.
        has_db = os.path.isfile(os.path.join(dir_path, 'veterinary.db'))
        if not has_db and not os.listdir(dir_path):
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
        # store_keys[0] is whatever `sorted(os.listdir(root))` put first, so the
        # ALPHABET decided which shop opened after a registry loss. On a machine
        # holding Store_Default (an empty leftover folder with no veterinary.db)
        # and Store_ZZ_Test_Pharmacy (the real one), it picked Store_Default --
        # and Online mode then paired the PC to that empty store and rendered
        # the blackout as a clean, healthy app. Ask the link ledger first, then
        # rank by what the folder actually carries, and record that the choice
        # was a guess either way.
        picked = _preferred_active_store(root, store_keys)
        reg['active_store_key'] = picked
        reg['active_auto_selected'] = True if len(store_keys) > 1 else False
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


def active_store_was_auto_selected() -> bool:
    """True when a registry rebuild picked the active store, not a person."""
    try:
        return bool(load_registry().get('active_auto_selected'))
    except Exception:
        return False


def confirm_active_store() -> None:
    """Operator has confirmed the auto-picked store is the right one."""
    try:
        reg = load_registry()
        if reg.pop('active_auto_selected', None) is not None:
            save_registry(reg)
    except Exception:
        pass


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


def ensure_active_store_db_exists() -> str:
    """Ensure Offline has a usable store SQLite file (create empty schema if missing)."""
    path = get_active_db_path()
    if os.path.isfile(path):
        return path
    key = get_active_store_key()
    if not key:
        create_store(
            "Default",
            device_role="admin",
            empty_db=True,
            migrate_legacy=False,
            activate=True,
        )
        path = get_active_db_path()
        if os.path.isfile(path):
            return path
    _init_empty_db(path)
    return path


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
        # Also match folder key (Store_Roshan vs typed "Roshan" / "roshan")
        if names_match(_display_name_from_store_key(s.get('store_key', '')), display_name):
            return s
    return None


def _find_store_key_on_disk(display_name: str) -> str:
    """Return existing Store_* folder key under stores root if DB already exists."""
    wanted = display_name_key(display_name).lower()
    root = get_stores_root()
    if not os.path.isdir(root):
        return ''
    try:
        names = os.listdir(root)
    except OSError:
        return ''
    for name in names:
        if not name.startswith('Store_'):
            continue
        if name.lower() != wanted:
            continue
        db_file = os.path.join(root, name, 'veterinary.db')
        if os.path.isfile(db_file):
            return name
    return ''


def _register_existing_disk_store(store_key: str, display_name: str, *, activate: bool = True) -> dict:
    """Add an on-disk store folder to the registry and optionally activate it."""
    display_name = normalize_display_name(display_name) or _display_name_from_store_key(store_key)
    existing = _find_store_by_key(store_key) or _find_store_by_display_name(display_name)
    if existing:
        if activate:
            set_active_store(existing['store_key'])
        return existing

    entry = {
        'store_key': store_key,
        'display_name': display_name,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'recovered_from_disk': True,
    }
    reg = load_registry(_allow_reconcile=False)
    stores = list(reg.get('stores') or [])
    stores.append(entry)
    reg['stores'] = stores
    if activate or not reg.get('active_store_key'):
        reg['active_store_key'] = store_key
    if not reg.get('device_role'):
        reg['device_role'] = 'admin'
    save_registry(reg)
    _forget_store_scoped_caches()
    if activate:
        _sync_backup_config_for_store(display_name)
    _publish_store_registry_cloud()
    try:
        from core.sync_coordinator import after_store_registry_changed
        after_store_registry_changed()
    except Exception:
        pass
    return entry


def _sync_backup_config_for_store(display_name: str):
    try:
        from core.backup_manager import _read_backup_config, write_backup_config
        cfg = _read_backup_config()
        folder_id = (cfg.get('folder_id') or '').strip()
        if cfg.get('source') == 'vendor':
            # Nothing to sync. The destination is the vendor's default that
            # every build carries and _read_backup_config picks up fresh; the
            # Drive subfolder is named after the ACTIVE store either way. Saving
            # it here would turn the default into this PC's own setting, which
            # no later build could correct.
            return
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


def remote_store_with_name(display_name: str, *, admin_token: str = '') -> Optional[dict]:
    """The server's store of this name, or None. Offline always returns None.

    A courtesy check, and only a courtesy: it reads the whole account's store
    list, which is an administrator's act. It used to take that token from a
    password compiled into the build, so every local "create store" quietly
    signed the shop's PC in as the vendor. Without a token typed by a person it
    now answers None -- the same answer it has always given when the server is
    unreachable, and a case every caller already handles.
    """
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return None
    except Exception:
        return None
    token = str(admin_token or '').strip()
    if not token:
        return None
    try:
        from core import server_api as api

        wanted = normalize_display_name(display_name).strip().lower()
        for s in api.list_remote_stores(token) or []:
            if (s.get('store_name') or '').strip().lower() == wanted:
                return s
    except Exception:
        # Unreachable server must not block a shop from working. The same check
        # runs again on the switch to Online, where it can be acted on.
        return None
    return None


def create_store(display_name: str, *, device_role: str = 'admin',
                 empty_db: bool = True, migrate_legacy: bool = False,
                 activate: bool = True, allow_existing_remote: bool = True) -> dict:
    display_name = normalize_display_name(display_name)
    if not display_name:
        raise ValueError('Store name is required.')

    existing = _find_store_by_display_name(display_name)
    if existing:
        raise ValueError(f'Store "{display_name}" already exists on this device.')

    # Online, the name must be free on the server too. Creating a store whose
    # name another shop already uses used to pair this device straight into that
    # shop's account and show its ledger. Offline has no such risk, so it is
    # left alone -- the check happens again when that store is taken online.
    #
    # Default-allowed on purpose: startup recovery, activation and legacy
    # migration all call this to re-establish a store that legitimately exists.
    # Only the shop's own "create store" action asks for the check.
    #
    # Since the vendor administrator password left the build, this check can
    # only run when somebody has signed in as the administrator -- otherwise it
    # answers None and is skipped, exactly as it is skipped when the server is
    # unreachable. Nothing is lost that mattered: the accident it guards
    # against (pairing into a shop that happens to share a name) is now
    # impossible anyway, because ensure_active_store_on_server resolves by SC-
    # key and no longer matches on the display name at all.
    if not allow_existing_remote:
        conflict = remote_store_with_name(display_name)
        if conflict:
            raise StoreNameTakenOnServer(display_name, conflict)

    # Prefer the real on-disk folder key (Windows case) so we keep existing data.
    store_key = _find_store_key_on_disk(display_name) or display_name_key(display_name)
    db_path = get_store_db_path(store_key)
    db_already = os.path.isfile(db_path)

    if migrate_legacy and not db_already:
        if not _copy_or_move_legacy_db(db_path, move=True):
            _init_empty_db(db_path)
    elif empty_db and not db_already:
        _init_empty_db(db_path)
    # If db_already: keep that store's existing data — never overwrite on create.

    entry = {
        'store_key': store_key,
        'display_name': display_name,
        'created_at': datetime.now().isoformat(timespec='seconds'),
    }
    if db_already:
        entry['recovered_from_disk'] = True

    reg = load_registry(_allow_reconcile=False)
    stores = list(reg.get('stores') or [])
    stores.append(entry)
    reg['stores'] = stores
    if activate or not reg.get('active_store_key'):
        reg['active_store_key'] = store_key
    if device_role in ('admin', 'satellite'):
        reg['device_role'] = device_role
    save_registry(reg)
    if reg.get('active_store_key') == store_key:
        _forget_store_scoped_caches()

    _migrate_legacy_slots(store_key)
    if activate or reg.get('active_store_key') == store_key:
        _sync_backup_config_for_store(display_name)
    _publish_store_registry_cloud()
    try:
        from core.sync_coordinator import after_store_registry_changed
        after_store_registry_changed()
    except Exception:
        pass
    return entry


def set_active_store(store_key: str) -> bool:
    if not _find_store_by_key(store_key):
        return False
    reg = load_registry()
    reg['active_store_key'] = store_key
    # A person has now said which store this is, so the recovery guess is no
    # longer a guess and the banner asking them to confirm can go away.
    reg.pop('active_auto_selected', None)
    save_registry(reg)
    _forget_store_scoped_caches()
    store = _find_store_by_key(store_key)
    if store:
        _sync_backup_config_for_store(store.get('display_name', ''))
    return True


def setup_initial_store_on_activation(display_name: str) -> dict:
    """Create or switch to the named store on activation.

    - Same name as an existing AppData/config store → activate it and load its data.
    - New name → create an EMPTY store and activate it.
    - First store on device with legacy veterinary.db → migrate that legacy file once.
    """
    display_name = normalize_display_name(display_name)
    if not display_name:
        raise ValueError('Initial store name is required.')

    # Pick up Store_* folders already on disk (AppData / project config).
    try:
        reconcile_registry_with_disk()
    except Exception:
        pass

    store = _find_store_by_display_name(display_name)
    if store:
        set_active_store(store['store_key'])
        return store

    disk_key = _find_store_key_on_disk(display_name)
    if disk_key:
        # Existing folder/DB for this name — load that data, do not wipe or recreate.
        return _register_existing_disk_store(disk_key, display_name, activate=True)

    if has_registry():
        return create_store(
            display_name,
            device_role='admin',
            empty_db=True,
            migrate_legacy=False,
            activate=True,
        )

    has_legacy = os.path.isfile(get_legacy_db_path())
    return create_store(
        display_name,
        device_role='admin',
        empty_db=True,
        migrate_legacy=has_legacy,
        activate=True,
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
    try:
        from core.backup_manager import _replace_store_database
        _replace_store_database(db_path, dest)
    except Exception:
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                os.remove(dest + suffix)
            except OSError:
                pass
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
    _forget_store_scoped_caches()
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
    _forget_store_scoped_caches()
    _sync_backup_config_for_store(new_display_name)
    _publish_store_registry_cloud()
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
    _forget_store_scoped_caches()
    store_dir = os.path.join(get_stores_root(), store_key)
    if os.path.isdir(store_dir):
        shutil.rmtree(store_dir, ignore_errors=True)
    if stores:
        _sync_backup_config_for_store(stores[0].get('display_name', ''))
    _publish_store_registry_cloud()
    try:
        from core.sync_coordinator import after_store_registry_changed
        after_store_registry_changed()
    except Exception:
        pass
    return True
