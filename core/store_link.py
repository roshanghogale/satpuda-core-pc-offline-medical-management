"""Android store connection key — links devices via Satpuda Core Server pairing."""
from __future__ import annotations

import os
import secrets
from typing import Optional

_KEY_FILE = 'android_store_key.txt'


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as d
    return d()


def _active_store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return str(get_active_store_key() or '').strip()
    except Exception:
        return ''


def _local_path(store_key: str = '') -> str:
    """Per-store pairing key, falling back to the single-store file.

    One shared file meant a store created second inherited the FIRST store's
    pairing key and therefore paired to that shop's server account, showing its
    data. The key belongs to one store, so it is stored per store now; the old
    file is still read for the store that owns it.
    """
    key = store_key or _active_store_key()
    if key:
        safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in key)
        return os.path.join(_appdata_dir(), f'store_key_{safe}.txt')
    return os.path.join(_appdata_dir(), _KEY_FILE)


def _legacy_path() -> str:
    return os.path.join(_appdata_dir(), _KEY_FILE)


def _generate_key() -> str:
    return f"SC-{secrets.token_hex(4).upper()}"


def _read(p: str) -> str:
    try:
        if os.path.isfile(p):
            return open(p, encoding='utf-8-sig').read().strip()
    except Exception:
        pass
    return ''


def _legacy_is_for(store_key: str, legacy_key: str) -> bool:
    """Is the pre-multi-store key file THIS store's?

    It belongs to one store. "Any store, while nobody has claimed it" was the
    rule, and it let a switch to a second store pair that store with the first
    shop's key: the Switch button opened the same books under the other name and
    relabelled the store after the first shop (2 Oct 2026). Now: the store that
    claimed it; else the store whose registry entry carries that key; else the
    first store to pair with it (``claim_legacy_key``) -- never a second one.
    """
    owner = _legacy_owner()
    if owner:
        return owner == store_key
    try:
        from core.store_manager import list_stores

        stores = list_stores()
    except Exception:
        stores = []
    for s in stores:
        if str(s.get('android_key') or '').strip() == legacy_key:
            return s.get('store_key') == store_key
    others_paired = any(
        _read(_local_path(str(s.get('store_key') or ''))) == legacy_key
        for s in stores if s.get('store_key') and s.get('store_key') != store_key
    )
    return not others_paired


def claim_legacy_key(store_key: str, key: str) -> None:
    """The first store to PAIR with the old shared key owns it from then on."""
    if not store_key or not key or _legacy_owner():
        return
    if _read(_legacy_path()) != key:
        return
    try:
        with open(os.path.join(_appdata_dir(), 'store_key_owner.txt'), 'w', encoding='utf-8') as fh:
            fh.write(store_key)
    except Exception:
        pass


def get_local_android_key(store_key: str = '') -> str:
    key = store_key or _active_store_key()
    own = _read(_local_path(key))
    if own:
        return own
    legacy = _read(_legacy_path())
    if legacy and _legacy_is_for(key, legacy):
        return legacy
    return ''


def _legacy_owner() -> str:
    """Which store the pre-multi-store key file belongs to, if any claimed it."""
    try:
        p = os.path.join(_appdata_dir(), 'store_key_owner.txt')
        if os.path.isfile(p):
            return open(p, encoding='utf-8-sig').read().strip()
    except Exception:
        pass
    return ''


def _save_local(key: str, store_key: str = '') -> None:
    os.makedirs(_appdata_dir(), exist_ok=True)
    with open(_local_path(store_key), 'w', encoding='utf-8') as fh:
        fh.write(key)
    # Claim the legacy file for this store so a second store cannot read it.
    owner_file = os.path.join(_appdata_dir(), 'store_key_owner.txt')
    if not _legacy_owner():
        try:
            with open(owner_file, 'w', encoding='utf-8') as fh:
                fh.write(store_key or _active_store_key())
        except Exception:
            pass


# NOTE: ensure_android_store_key and publish_store_key below have no callers
# anywhere in this repo (checked 2026-09-07). They still pass
# create_if_missing=True; left alone deliberately rather than spending risk on
# dead code. Delete them, or fix the flag, if either ever gets a caller.
def ensure_android_store_key(store_display_name: str, app_mode: str = 'medical') -> str:
    """Return pairing key for the active store (creates server store when Online)."""
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core import server_live as live
            session = live.ensure_active_store_on_server(create_if_missing=True)
            key = (session.get('android_key') or '').strip()
            if key:
                _save_local(key)
                return key
    except Exception:
        pass
    key = get_local_android_key()
    if not key:
        key = _generate_key()
        _save_local(key)
    return key


def publish_store_key(key: str, store_display_name: str, app_mode: str = 'medical') -> bool:
    """No-op for Server; Online pairing uses server android_key instead."""
    if key:
        _save_local(key)
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core import server_live as live
            live.ensure_active_store_on_server(create_if_missing=True)
            return True
    except Exception:
        return False
    return bool(key)


def lookup_key(key: str) -> Optional[dict]:
    """Which shop an SC- key belongs to, asked with the key itself.

    This used to sign in as the vendor ADMINISTRATOR and scan every store on
    the account for a matching android_key -- a password compiled into the
    build, spent on a question the key can answer by itself. ``/api/auth/pair``
    takes the key and answers with the one store that owns it: no credential,
    no list of other people's shops, and nothing to leak if the reply is logged.

    Returns None when the key is not a real one. The pairing this makes is the
    same one the shop would make anyway, so there is no side effect to undo.
    """
    key = (key or '').strip()
    if not key:
        return None
    try:
        from core import server_api as api

        data = api.pair_store(
            android_key=key,
            device_id=api._pc_device_id() or 'mac2-pc',
            device_type='pc',
        ) or {}
        s = data.get('store') or {}
        if not s:
            return None
        return {
            'store_id': s.get('store_id'),
            'store_name': s.get('store_name'),
            'store_key': s.get('store_key'),
            'android_key': s.get('android_key') or key,
            'app_mode': s.get('app_mode') or 'online',
        }
    except Exception:
        pass
    return None


def store_id_from_key(store_key: str) -> str:
    """Server store_id slug for a local store folder key."""
    import re
    sid = (store_key or 'Store_Default').lower()
    sid = re.sub(r'[^a-z0-9_]', '_', sid)
    return sid or 'store_default'


def publish_pc_store_registry() -> bool:
    """Server has no Server-style PC registry; pairing is per-store."""
    return False


def regenerate_android_key(
    store_display_name: str, app_mode: str = 'medical', *, admin_token: str = ''
) -> str:
    """Regenerate pairing key on the server for the active store.

    Rotating a store's key is an administrator's act -- it unpairs every phone
    and second PC on that store -- and it used to be taken with a password
    compiled into the build. ``admin_token`` comes from a person signing in
    (core/admin_session.py) and is checked FIRST, before anything local is
    touched, so a refusal cannot reach the local-key fallback at the bottom.
    """
    admin = str(admin_token or '').strip()
    if not admin:
        from core import admin_session

        admin = admin_session.token()
    try:
        from core import server_api as api
        from core import server_live as live
        from core.store_manager import get_active_store_key

        # Same one-click reproduction as Verify server sync, and this one goes
        # on to _save_local(key) below -- overwriting the PC's pairing key with
        # the new empty store's.
        session = live.ensure_active_store_on_server(
            create_if_new=True, admin_token=admin
        )
        store_id = session.get('store_id') or store_id_from_key(get_active_store_key() or '')
        updated = api.regenerate_android_key(admin, store_id)
        key = (updated.get('android_key') or '').strip()
        if not key:
            key = _generate_key()
        _save_local(key)
        # Refresh JWT against new key
        api.ensure_store_session(
            store_key=get_active_store_key() or 'Store_Default',
            android_key=key,
            store_name=store_display_name or session.get('store_name') or '',
            device_id=api._pc_device_id() or 'mac2-pc',
            force_pair=True,
        )
        return key
    except Exception as exc:
        # An identity failure must NOT land here. This handler answers any
        # problem by inventing a pairing key and writing it over this PC's real
        # one -- so the very errors that exist to stop a PC pairing to the wrong
        # store would, on their way past, destroy its link to the right one.
        # "The server said no" and "the network is down" are not the same
        # thing, and only the second one deserves a locally generated key.
        try:
            from core import server_live as live

            if isinstance(
                exc, (live.StoreNotLinkedOnServer, live.StoreNameTakenOnServer)
            ):
                raise
        except ImportError:
            pass
        key = _generate_key()
        _save_local(key)
        return key
