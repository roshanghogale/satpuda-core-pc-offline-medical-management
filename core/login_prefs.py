"""
App login gate prefs — optional username/password shown on every app open.

Password is stored as PBKDF2-SHA256 hash (never plaintext). File is also
Fernet-encrypted via license_manager helpers when cryptography is available.

THE STORE'S LOGIN, NOT ONE PC'S (owner, 25 Oct 2026: "the username and password for
the store is not working in offline first"). The login used to live only in this PC's
``app_login.dat``. Settings > Pharmacy Profile calls it the shop login and the shop set
it once -- but every other PC of the store (a second counter, the laptop, a PC set up
again when v1.0.11 moved every shop to offline-first) had no copy of it, so the shop's
username and password were refused there, or not asked at all.

Now it is one of the store's own settings rows, ``app_login`` (JSON: enabled, username,
salt, password_hash, iterations -- a salted hash, never the password):

  * offline-first / Offline: this PC's ``settings`` table, so it is checked with no
    internet at all; ``app_login`` is a shop-wide row (core/offline_first/schema.py), so
    the trigger sends it to the server and every other device of the store pulls it;
  * Online: the server's key/value settings (where the other devices' saves land), and
    this PC's settings rows (the per-store mirror, core/settings_mirror.py) when the
    server cannot be reached.

A store with no ``app_login`` row yet keeps this PC's own ``app_login.dat`` exactly as
before, so the update locks nobody out and lets nobody in. That login becomes the
store's the first time it is saved in Settings, or typed correctly at the login screen.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys

log = logging.getLogger(__name__)

_FILE = 'app_login.dat'
_ITERATIONS = 120_000

# The store's settings row. Shop-wide: core/offline_first/schema.SHOP_WIDE_SETTING_NAMES
# and core/store_kv_carry.PREFIXES carry it between devices and modes.
STORE_KV = 'app_login'


def _config_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp',
        )
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config',
    )


def _path() -> str:
    return os.path.join(_config_dir(), _FILE)


def _hash_password(password: str, salt_hex: str, iterations: int = _ITERATIONS) -> str:
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac(
        'sha256',
        (password or '').encode('utf-8'),
        salt,
        int(iterations or _ITERATIONS),
    )
    return digest.hex()


def _default() -> dict:
    return {
        'enabled': False,
        'username': '',
        'salt': '',
        'password_hash': '',
        'iterations': _ITERATIONS,
    }


def _clean(parsed) -> dict:
    data = _default()
    if not isinstance(parsed, dict):
        return data
    data['enabled'] = bool(parsed.get('enabled'))
    data['username'] = str(parsed.get('username') or '').strip()
    data['salt'] = str(parsed.get('salt') or '')
    data['password_hash'] = str(parsed.get('password_hash') or '')
    try:
        data['iterations'] = int(parsed.get('iterations') or _ITERATIONS)
    except (TypeError, ValueError):
        data['iterations'] = _ITERATIONS
    return data


# ── this PC's own file (the only copy before the login was the store's) ──────

def _load_file() -> dict:
    path = _path()
    data = _default()
    if not os.path.isfile(path):
        return data
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        if not raw:
            return data
        try:
            from core.license_manager import _decrypt
            parsed = _decrypt(raw)
            if not isinstance(parsed, dict):
                parsed = {}
        except Exception:
            parsed = json.loads(raw.decode('utf-8'))
        data = _clean(parsed)
    except Exception:
        pass
    return data


def _write_file(payload: dict) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    try:
        from core.license_manager import _encrypt
        blob = _encrypt(payload)
    except Exception:
        blob = json.dumps(payload).encode('utf-8')
    path = _path()
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(blob)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


# ── the store's row ──────────────────────────────────────────────────────────

def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _parse_row(value):
    """The row's value as a clean dict, or None when it holds no login (empty, damaged)."""
    if isinstance(value, dict):
        parsed = value
    else:
        s = str(value if value is not None else '').strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, str):  # a JSON string of the JSON
                parsed = json.loads(parsed)
        except (TypeError, ValueError):
            return None
    if not isinstance(parsed, dict):
        return None
    return _clean(parsed)


def _local_row(conn):
    if conn is None:
        return None
    try:
        row = conn.execute('SELECT value FROM settings WHERE name=?', (STORE_KV,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return _parse_row(row[0])


def _server_row():
    """Online: the store's row from the server; None when it has none or cannot be asked."""
    try:
        from core.store_images import _kv_rows_cached

        rows = _kv_rows_cached((STORE_KV,))
    except Exception as exc:
        log.debug('app login kv fetch: %s', exc)
        return None
    for r in rows or []:
        if str(r.get('name') or '') == STORE_KV:
            return _parse_row(r.get('value'))
    return None


def _forget_server_row() -> None:
    try:
        from core import store_images as si

        with si._lock:
            si._kv_list_cache.pop((si._store_key(), (STORE_KV,)), None)
    except Exception:
        pass


def _row_text(payload: dict) -> str:
    return json.dumps(_clean(payload), sort_keys=True)


def _keep_local(conn, text: str) -> None:
    from core.desktop_settings_service import _setting_set

    _setting_set(conn, STORE_KV, text)
    try:
        conn.commit()
    except Exception:
        pass


def _store_prefs(conn):
    """The store's login, or None when the store has none yet."""
    local = _local_row(conn)
    if not _is_online():
        return local
    server = _server_row()
    if server is None:
        # No internet (or nothing on the server yet): this PC's copy of the store's row.
        return local
    if local != server and conn is not None:
        # Keep the server's row on this PC (the in-memory shell's table and the per-store
        # mirror), so the login still works the next time the internet is down.
        try:
            _keep_local(conn, _row_text(server))
        except Exception as exc:
            log.debug('app login local copy: %s', exc)
    return server


def _write_store_row(conn, payload: dict) -> None:
    text = _row_text(payload)
    if _is_online():
        # Online the server is where the other PCs read it from: refuse the save rather
        # than leave this PC on a login the rest of the store never got.
        from core.server_live import push_settings_kv

        push_settings_kv(STORE_KV, text)
        _forget_server_row()
    if conn is not None:
        _keep_local(conn, text)


def load_login_prefs(conn=None) -> dict:
    """The login the app asks for: the store's when it has one, else this PC's own file."""
    store = _store_prefs(conn)
    if store is not None:
        return store
    return _load_file()


def _enabled(prefs: dict) -> bool:
    return bool(
        prefs.get('enabled')
        and prefs.get('username')
        and prefs.get('password_hash')
        and prefs.get('salt')
    )


def is_login_enabled(conn=None) -> bool:
    return _enabled(load_login_prefs(conn))


def save_login_prefs(
    *,
    enabled: bool,
    username: str,
    password: str | None = None,
    keep_existing_password: bool = False,
    conn=None,
) -> None:
    """Save login settings: the store's row (every PC of the store) and this PC's file.

    If enabled and password is empty with keep_existing_password=True, retain
    the previous hash (user did not retype password).
    """
    store = _store_prefs(conn)
    current = store if store is not None else _load_file()
    username = (username or '').strip()
    enabled = bool(enabled)

    if enabled and not username:
        raise ValueError('Username is required when App Login is enabled.')

    salt = current.get('salt') or ''
    password_hash = current.get('password_hash') or ''
    iterations = int(current.get('iterations') or _ITERATIONS)

    if password is not None and str(password) != '':
        salt = secrets.token_hex(16)
        iterations = _ITERATIONS
        password_hash = _hash_password(str(password), salt, iterations)
    elif enabled and not (keep_existing_password and salt and password_hash):
        raise ValueError('Password is required when enabling App Login.')

    payload = _clean({
        'enabled': enabled,
        'username': username,
        'salt': salt,
        'password_hash': password_hash,
        'iterations': iterations,
    })
    if not enabled:
        # When turning off, keep stored hash so re-enable without retyping still works
        # if they check the box again and leave password blank (keep_existing).
        payload['username'] = username or current.get('username', '')

    # Every Pharmacy Profile save carries the login section. A PC with no login of its own,
    # saving its address with the login untouched, must not write "no login" over the
    # store -- that would switch the login off on the PC that has one. The store's row is
    # written when the store has one, when the login changed, or when it is a login.
    if store is not None or payload != _clean(current) or payload['enabled']:
        _write_store_row(conn, payload)
    # This PC's own file as well: what an older build reads, and the login of a PC whose
    # store database is not open.
    _write_file(payload)


def verify_login(username: str, password: str, conn=None) -> bool:
    store = _store_prefs(conn)
    prefs = store if store is not None else _load_file()
    if not prefs.get('enabled'):
        return True
    stored_user = (prefs.get('username') or '').strip()
    salt = prefs.get('salt') or ''
    stored_hash = prefs.get('password_hash') or ''
    if not stored_user or not salt or not stored_hash:
        return False
    if (username or '').strip() != stored_user:
        return False
    try:
        got = _hash_password(password or '', salt, int(prefs.get('iterations') or _ITERATIONS))
    except (TypeError, ValueError):
        return False
    if not hmac.compare_digest(got, stored_hash):
        return False
    if store is None and conn is not None:
        # The PC's own login, typed right on a store that has none yet: it is the shop's
        # login from now on, on every PC of the store (best effort -- the login stands).
        try:
            _write_store_row(conn, prefs)
        except Exception as exc:
            log.info('app login not made the store\'s yet: %s', exc)
    return True
