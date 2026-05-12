"""
License Manager — Hardware-locked activation with 3-factor login.

Activation flow (first run on any device):
  1. Hardware fingerprint generated (CPU + MAC + Disk)
  2. Encrypted as device.key in AppData
  3. Login dialog: Username + Password + Device Key
  4. All 3 correct → activation.dat written (hardware hash inside) → device.key deleted
  5. App opens

Every subsequent run:
  1. activation.dat found → decrypt → compare hardware hash
  2. Match → open app silently
  3. Mismatch → blocked
"""

import os
import sys
import json
import hashlib
import subprocess

# ── Master credentials (hardcoded, compiled into exe) ─────────────────────────
_MASTER_USERNAME = "RoshanMedicalManagerUserName"
_MASTER_PASSWORD = "RoshanMedicalManagerPassword"

# Fernet secret — 32-url-safe-base64 bytes, fixed forever
_SECRET = b"Vm9ldGVyaW5hcnlBcHBTZWNyZXRLZXkyMDI2IQ=="

# ── AppData path ───────────────────────────────────────────────────────────────
def _appdata_dir():
    if getattr(sys, 'frozen', False):
        base = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp')
    else:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'config')
    os.makedirs(base, exist_ok=True)
    return base


def get_icon_path() -> str:
    """Return absolute path to satpuda_logo.ico — works in both exe and dev mode."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'assets', 'satpuda_logo.ico')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'assets', 'satpuda_logo.ico')

def _activation_path():
    return os.path.join(_appdata_dir(), 'activation.dat')

def _device_key_path():
    return os.path.join(_appdata_dir(), 'device.key')

# ── Fernet helpers ─────────────────────────────────────────────────────────────
def _get_fernet():
    try:
        from cryptography.fernet import Fernet
        import base64
        # Pad/derive a valid 32-byte url-safe base64 key
        raw = hashlib.sha256(_SECRET).digest()
        key = base64.urlsafe_b64encode(raw)
        return Fernet(key)
    except ImportError:
        return None

def _encrypt(data: dict) -> bytes:
    f = _get_fernet()
    if f:
        return f.encrypt(json.dumps(data).encode())
    # Fallback: simple XOR obfuscation if cryptography not installed
    raw = json.dumps(data).encode()
    key = _SECRET * (len(raw) // len(_SECRET) + 1)
    return bytes(a ^ b for a, b in zip(raw, key))

def _decrypt(data: bytes) -> dict:
    f = _get_fernet()
    if f:
        try:
            return json.loads(f.decrypt(data).decode())
        except Exception:
            return {}
    try:
        key = _SECRET * (len(data) // len(_SECRET) + 1)
        raw = bytes(a ^ b for a, b in zip(data, key))
        return json.loads(raw.decode())
    except Exception:
        return {}

# ── Hardware fingerprint ───────────────────────────────────────────────────────
def _get_hardware_hash() -> str:
    parts = []

    # MAC address
    try:
        import uuid
        parts.append(str(uuid.getnode()))
    except Exception:
        pass

    # CPU ID
    try:
        out = subprocess.check_output(
            'wmic cpu get processorid', shell=True,
            stderr=subprocess.DEVNULL, timeout=5).decode()
        parts.append(out.strip().split('\n')[-1].strip())
    except Exception:
        pass

    # Motherboard serial
    try:
        out = subprocess.check_output(
            'wmic baseboard get serialnumber', shell=True,
            stderr=subprocess.DEVNULL, timeout=5).decode()
        parts.append(out.strip().split('\n')[-1].strip())
    except Exception:
        pass

    # Disk serial
    try:
        out = subprocess.check_output(
            'wmic diskdrive get serialnumber', shell=True,
            stderr=subprocess.DEVNULL, timeout=5).decode()
        parts.append(out.strip().split('\n')[-1].strip())
    except Exception:
        pass

    combined = '|'.join(p for p in parts if p and p.lower() not in ('', 'serialnumber', 'processorid'))
    return hashlib.sha256(combined.encode()).hexdigest()

# ── Device key (written on first run) ─────────────────────────────────────────
def _write_device_key(hw_hash: str):
    """Write hw_hash as plain text — you open this in Notepad and copy it."""
    path = _device_key_path()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(hw_hash)

def _read_device_key() -> str:
    path = _device_key_path()
    if not os.path.exists(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''

def _delete_device_key():
    path = _device_key_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

# ── Activation file ────────────────────────────────────────────────────────────
def _write_activation(hw_hash: str):
    from datetime import date
    payload = _encrypt({'hw': hw_hash, 'date': str(date.today())})
    with open(_activation_path(), 'wb') as f:
        f.write(payload)

def _read_activation() -> dict:
    path = _activation_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            return _decrypt(f.read())
    except Exception:
        return {}

# ── Expiry file ────────────────────────────────────────────────────────────────
def _expiry_path():
    return os.path.join(_appdata_dir(), 'expiry.dat')

def write_expiry(expiry_date_str: str, enabled: bool = True):
    """Write expiry.dat with given date (YYYY-MM-DD) and enabled flag.
    Call this to create or update the expiry restriction on a device."""
    payload = _encrypt({'enabled': enabled, 'expiry_date': expiry_date_str})
    with open(_expiry_path(), 'wb') as f:
        f.write(payload)

def _read_expiry() -> dict:
    path = _expiry_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            return _decrypt(f.read())
    except Exception:
        return {}

def _write_default_expiry():
    """Write expiry.dat with today + 5 days, enabled=True.
    Called automatically when device.key is generated (first run or hardware mismatch)."""
    from datetime import date, timedelta
    expiry = str(date.today() + timedelta(days=5))
    write_expiry(expiry, enabled=True)

def check_expiry() -> bool:
    """Check if the expiry restriction has triggered.
    Returns True if the app should be blocked (expired), False if OK.
    Side effect: if expired, deletes activation.dat + expiry.dat and regenerates device.key."""
    from datetime import date
    data = _read_expiry()
    if not data:
        return False  # no expiry file — no restriction
    if not data.get('enabled', False):
        return False  # enabled=false — restriction disabled
    try:
        expiry_date = date.fromisoformat(data['expiry_date'])
    except Exception:
        return False  # bad date format — ignore
    if date.today() >= expiry_date:
        # Expired — revoke activation and regenerate device key
        act = _activation_path()
        exp = _expiry_path()
        if os.path.exists(act):
            try: os.remove(act)
            except Exception: pass
        if os.path.exists(exp):
            try: os.remove(exp)
            except Exception: pass
        prepare_device_key()  # generates fresh device.key
        return True
    return False

# ── Public API ─────────────────────────────────────────────────────────────────
def is_activated() -> bool:
    """True if activation.dat exists AND hardware hash matches current device."""
    data = _read_activation()
    if not data or 'hw' not in data:
        return False
    return data['hw'] == _get_hardware_hash()

def needs_activation() -> bool:
    return not is_activated()

def prepare_device_key():
    """Generate hardware fingerprint, write device.key, and write default expiry.dat."""
    hw = _get_hardware_hash()
    _write_device_key(hw)
    _write_default_expiry()

def get_device_key_path() -> str:
    return _device_key_path()

def attempt_activation(username: str, password: str, device_key_input: str) -> tuple:
    """
    Validate all 3 factors.
    Returns (True, '') on success or (False, error_message) on failure.
    """
    if username != _MASTER_USERNAME:
        return False, "Invalid username."
    if password != _MASTER_PASSWORD:
        return False, "Invalid password."

    # Read the actual device key from file and compare
    stored_hw = _read_device_key()
    if not stored_hw:
        return False, "Device key file not found.\nPlease contact the developer."

    # The user pastes the raw content of device.key file — we decrypt and compare
    # But device.key is binary encrypted, so user actually reads the hw_hash we show
    # We compare the entered text against the stored hw hash directly
    current_hw = _get_hardware_hash()
    if device_key_input.strip() != stored_hw:
        return False, "Invalid device key."
    if stored_hw != current_hw:
        return False, "Device key does not match this hardware."

    # All 3 factors valid — activate
    _write_activation(current_hw)
    _delete_device_key()
    return True, ""
