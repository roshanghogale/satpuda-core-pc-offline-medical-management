"""
App login gate prefs — optional username/password shown on every app open.

Password is stored as PBKDF2-SHA256 hash (never plaintext). File is also
Fernet-encrypted via license_manager helpers when cryptography is available.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys

_FILE = 'app_login.dat'
_ITERATIONS = 120_000


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


def _hash_password(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac(
        'sha256',
        (password or '').encode('utf-8'),
        salt,
        _ITERATIONS,
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


def load_login_prefs() -> dict:
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
        data['enabled'] = bool(parsed.get('enabled'))
        data['username'] = str(parsed.get('username') or '').strip()
        data['salt'] = str(parsed.get('salt') or '')
        data['password_hash'] = str(parsed.get('password_hash') or '')
        data['iterations'] = int(parsed.get('iterations') or _ITERATIONS)
    except Exception:
        pass
    return data


def is_login_enabled() -> bool:
    prefs = load_login_prefs()
    return bool(
        prefs.get('enabled')
        and prefs.get('username')
        and prefs.get('password_hash')
        and prefs.get('salt')
    )


def save_login_prefs(
    *,
    enabled: bool,
    username: str,
    password: str | None = None,
    keep_existing_password: bool = False,
) -> None:
    """Save login settings.

    If enabled and password is empty with keep_existing_password=True, retain
    the previous hash (user did not retype password).
    """
    current = load_login_prefs()
    username = (username or '').strip()
    enabled = bool(enabled)

    if enabled and not username:
        raise ValueError('Username is required when App Login is enabled.')

    salt = current.get('salt') or ''
    password_hash = current.get('password_hash') or ''

    if password is not None and str(password) != '':
        salt = secrets.token_hex(16)
        password_hash = _hash_password(str(password), salt)
    elif enabled and not (keep_existing_password and salt and password_hash):
        raise ValueError('Password is required when enabling App Login.')

    if not enabled:
        # Keep username for convenience when re-enabling; clear secrets only if both empty
        pass

    payload = {
        'enabled': enabled,
        'username': username,
        'salt': salt if enabled else (salt or ''),
        'password_hash': password_hash if enabled else (password_hash or ''),
        'iterations': _ITERATIONS,
    }
    if not enabled:
        # When turning off, keep stored hash so re-enable without retyping still works
        # if they check the box again and leave password blank (keep_existing).
        payload['username'] = username or current.get('username', '')
        payload['salt'] = current.get('salt') or salt
        payload['password_hash'] = current.get('password_hash') or password_hash

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


def verify_login(username: str, password: str) -> bool:
    prefs = load_login_prefs()
    if not prefs.get('enabled'):
        return True
    stored_user = (prefs.get('username') or '').strip()
    salt = prefs.get('salt') or ''
    stored_hash = prefs.get('password_hash') or ''
    if not stored_user or not salt or not stored_hash:
        return False
    if (username or '').strip() != stored_user:
        return False
    got = _hash_password(password or '', salt)
    return hmac.compare_digest(got, stored_hash)
