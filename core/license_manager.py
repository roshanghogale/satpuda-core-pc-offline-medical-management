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

Online mode: expiry / access is read from the Satpuda Core Server.
"""
from __future__ import annotations

import os
import sys
import json
import hashlib
import subprocess
import time

# ── Master credentials (hardcoded, compiled into exe) ─────────────────────────
_MASTER_USERNAME = "satpudacoreusername"
_MASTER_PASSWORD = "satpuda core"

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
    try:
        from core.window_icon import get_icon_path as _path
        return _path()
    except Exception:
        if getattr(sys, 'frozen', False):
            return os.path.join(sys._MEIPASS, 'assets', 'satpuda_logo.ico')
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'assets', 'satpuda_logo.ico',
        )

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
    """Read a blob written by ANY build, whichever format it used.

    _encrypt picks Fernet or XOR purely on whether `cryptography` imported in
    the build that WROTE the file -- which is not necessarily this one. The old
    code guessed one format and returned {} on mismatch, silently. Callers read
    that as "no data", and the next save overwrote the file in the other format,
    permanently wiping the store registry, licence state and saved logins.
    That is the "settings reset when I replace the build folder" bug.
    """
    if not data:
        return {}
    f = _get_fernet()
    if f:
        try:
            obj = json.loads(f.decrypt(data).decode())
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    try:
        key = _SECRET * (len(data) // len(_SECRET) + 1)
        obj = json.loads(bytes(a ^ b for a, b in zip(data, key)).decode())
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    try:
        obj = json.loads(data.decode())
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {}


def decrypt_failed(data: bytes) -> bool:
    """True when data is non-empty but no known format could read it."""
    return bool(data) and not _decrypt(data)

# ── Hardware fingerprint ───────────────────────────────────────────────────────
_HW_CACHE_FILE = 'hw_fingerprint.cache'
_HW_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600


def _hw_cache_path() -> str:
    return os.path.join(_appdata_dir(), _HW_CACHE_FILE)


def _read_hw_cache() -> str:
    path = _hw_cache_path()
    if not os.path.isfile(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        hw = (data.get('hw') or '').strip()
        ts = float(data.get('ts') or 0)
        if hw and (time.time() - ts) < _HW_CACHE_MAX_AGE_SECONDS:
            return hw
    except Exception:
        pass
    return ''


def _write_hw_cache(hw_hash: str) -> None:
    try:
        with open(_hw_cache_path(), 'w', encoding='utf-8') as f:
            json.dump({'hw': hw_hash, 'ts': time.time()}, f)
    except Exception:
        pass


_HW_NOISE = ('', 'serialnumber', 'processorid', 'node', 'to be filled by o.e.m.',
             'default string', 'none', 'not applicable', 'system serial number',
             '0', '00000000', 'null')


def _wmic_values(query: str) -> list[str]:
    """Every data row of a WMIC query, cleaned. Order is NOT relied upon."""
    try:
        out = subprocess.check_output(
            query, shell=True, stderr=subprocess.DEVNULL, timeout=5).decode(
            'utf-8', 'ignore')
    except Exception:
        return []
    vals = []
    for line in out.splitlines()[1:]:          # first line is the column header
        v = line.strip()
        if v and v.lower() not in _HW_NOISE:
            vals.append(v)
    return vals


def _stable_mac() -> str:
    """The adapter MAC, but only when it is a real one.

    uuid.getnode() invents a RANDOM address when it cannot read a card, marking
    it with the multicast bit. Feeding that into the fingerprint made the whole
    hash different on every launch, so the machine kept looking new and
    activation kept coming back.
    """
    try:
        import uuid

        node = uuid.getnode()
    except Exception:
        return ''
    if node >> 40 & 0x01:       # locally administered / multicast = made up
        return ''
    return str(node)


_CIM_QUERY = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$d=@(Get-CimInstance Win32_DiskDrive |"
    " Where-Object {$_.MediaType -like '*Fixed*'} |"
    " ForEach-Object {$_.SerialNumber});"
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "ConvertTo-Json -Compress @{"
    "cpu=(Get-CimInstance Win32_Processor | Select-Object -First 1 -Expand ProcessorId);"
    "board=(Get-CimInstance Win32_BaseBoard | Select-Object -First 1 -Expand SerialNumber);"
    "sysuuid=(Get-CimInstance Win32_ComputerSystemProduct | Select-Object -First 1 -Expand UUID);"
    "disk=($d -join '|')}"
)


def _cim_parts() -> dict:
    """Read the machine identity through CIM, in one PowerShell call.

    WMIC is gone from current Windows builds -- on those machines every `wmic`
    call failed, the fingerprint collapsed to the network adapter alone, and any
    change to that adapter looked like a different computer. That is the real
    reason activation kept coming back. CIM answers on every Windows this app
    supports.
    """
    try:
        out = subprocess.check_output(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', _CIM_QUERY],
            stderr=subprocess.DEVNULL,
            timeout=25,
        ).decode('utf-8', 'ignore').strip()
    except Exception:
        return {}
    try:
        data = json.loads(out)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out_parts = {}
    for key in ('cpu', 'board', 'sysuuid', 'disk'):
        raw = data.get(key)
        if isinstance(raw, list):
            raw = '|'.join(str(x).strip() for x in raw if x)
        val = str(raw or '').strip()
        if key == 'disk' and val:
            # Sort, so disk enumeration order can never move the fingerprint.
            val = '|'.join(sorted(
                p.strip() for p in val.split('|')
                if p.strip() and p.strip().lower() not in _HW_NOISE
            ))
        if val.lower() in _HW_NOISE:
            val = ''
        out_parts[key] = val
    return out_parts


def _hardware_parts() -> dict:
    """Named identity components for this machine.

    Only fixed internal hardware. Disk serials used to be taken as the LAST line
    of `wmic diskdrive`, so plugging in a USB stick appended a line, changed the
    fingerprint, and asked the shop to activate again -- on a machine that had
    not changed at all. Removable drives are now excluded outright and the
    remaining serials are sorted, so enumeration order cannot matter either.
    """
    cached = _PARTS_MEMO.get('parts')
    if cached is not None:
        return dict(cached)

    parts = {'mac': _stable_mac(), 'cpu': '', 'board': '', 'sysuuid': '', 'disk': ''}
    parts.update({k: v for k, v in _cim_parts().items() if v})

    if not any(parts.get(k) for k in ('cpu', 'board', 'sysuuid', 'disk')):
        # Machines old enough to still have WMIC and no usable CIM.
        parts['cpu'] = (_wmic_values('wmic cpu get processorid') or [''])[0]
        parts['board'] = (_wmic_values('wmic baseboard get serialnumber') or [''])[0]
        fixed = _wmic_values(
            'wmic diskdrive where "MediaType like \'%Fixed%\'" get serialnumber'
        )
        if not fixed:
            fixed = _wmic_values('wmic diskdrive get serialnumber')
        parts['disk'] = '|'.join(sorted(fixed))

    _PARTS_MEMO['parts'] = dict(parts)
    return parts


# Components that identify the machine. 'mac' is deliberately not here: docking
# stations, USB ethernet and the virtual adapters a Windows update installs all
# move it.
_HW_IDENTITY_KEYS = ('cpu', 'board', 'sysuuid', 'disk')

# Reading the identity spawns a PowerShell process. is_activated() is polled by
# the desktop status endpoint, so without this the app would launch one on every
# poll. Held for the life of the process; hardware does not change under a
# running app.
_PARTS_MEMO: dict = {}


def _hash_parts(parts: dict) -> str:
    # 'mac' stays first so that on a machine where nothing else can be read the
    # value still matches what the older build hashed.
    combined = '|'.join(
        str(parts.get(k) or '')
        for k in ('mac', 'cpu', 'board', 'sysuuid', 'disk')
    )
    return hashlib.sha256(combined.encode()).hexdigest()


def _compute_hardware_hash() -> str:
    """Slow path: WMIC subprocess calls. Use _get_hardware_hash() instead."""
    return _hash_parts(_hardware_parts())


def _legacy_hardware_hash() -> str:
    """The pre-2026 fingerprint, reproduced exactly.

    Kept so the shops already running do not all get an activation prompt the
    day they take this update: their activation.dat holds a hash built this way,
    and it has to keep matching until the record has been upgraded.
    """
    parts = []
    try:
        import uuid

        parts.append(str(uuid.getnode()))
    except Exception:
        pass
    for query in (
        'wmic cpu get processorid',
        'wmic baseboard get serialnumber',
        'wmic diskdrive get serialnumber',
    ):
        try:
            out = subprocess.check_output(
                query, shell=True, stderr=subprocess.DEVNULL, timeout=5).decode()
            parts.append(out.strip().split('\n')[-1].strip())
        except Exception:
            pass
    combined = '|'.join(
        p for p in parts
        if p and p.lower() not in ('', 'serialnumber', 'processorid')
    )
    return hashlib.sha256(combined.encode()).hexdigest()


def hardware_matches(stored: dict | str | None) -> bool:
    """Is this the machine the licence was activated on?

    Exact-hash matching meant one WMIC call timing out at boot, or one component
    changing after a Windows update, read as a different computer. Records
    written by this build carry the components, so a single change is tolerated
    as long as the machine is still recognisable; older records keep the strict
    hash comparison they have always had.
    """
    if not stored:
        return False
    if isinstance(stored, str):
        return stored == _get_hardware_hash()

    saved_parts = stored.get('hw_parts')
    if isinstance(saved_parts, dict) and not _identity_readable(saved_parts):
        # Components were stored but every one is blank -- that record cannot
        # identify anything, so fall back to the hash rather than waving it through.
        saved_parts = None
    if not isinstance(saved_parts, dict):
        saved_hash = str(stored.get('hw') or '')
        if saved_hash == _get_hardware_hash():
            return True
        # Written by an older build, whose fingerprint was computed differently.
        # Re-run that exact recipe before deciding this is a different machine.
        return bool(saved_hash) and saved_hash == _legacy_hardware_hash()

    now = _hardware_parts()
    known = matched = 0
    for key in _HW_IDENTITY_KEYS:
        was, is_ = str(saved_parts.get(key) or ''), str(now.get(key) or '')
        if not was or not is_:
            continue        # unreadable on one side proves nothing either way
        known += 1
        if was == is_:
            matched += 1
    if known == 0:
        # Nothing readable at all (WMIC blocked or missing) -- trust the record
        # rather than locking the shop out of its own till.
        return True
    if known == 1:
        return matched == 1
    return matched >= known - 1


def _get_hardware_hash(*, force_refresh: bool = False) -> str:
    """Return device fingerprint; cached for 7 days to avoid WMIC on every launch."""
    if not force_refresh:
        cached = _read_hw_cache()
        if cached:
            return cached
    hw = _compute_hardware_hash()
    _write_hw_cache(hw)
    return hw

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
    # Carry forward the three facts a re-activation must never erase: when this
    # computer FIRST activated (the starter window is measured from it, so
    # re-dating cannot buy another one), that a real licence has been held here,
    # and the rollback ratchet's serial floor. Rewriting them away was a free
    # licence renewal -- proven, 2026-09-16.
    previous = _read_activation() or {}
    record = {'hw': hw_hash, 'date': str(date.today())}
    record['first_date'] = str(previous.get('first_date') or previous.get('date')
                               or record['date'])
    for keep in ('lic_seen', 'lic_ser'):
        if previous.get(keep):
            record[keep] = previous[keep]
    # Store the individual components too, so a later check can tell "one part
    # of this PC changed" apart from "this is a different PC". An unreadable
    # machine stores nothing rather than a blank identity that would match
    # every computer.
    try:
        parts = _hardware_parts()
        if _identity_readable(parts):
            record['hw_parts'] = parts
    except Exception:
        pass
    payload = _encrypt(record)
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


def get_expiry_storage_path() -> str:
    """Canonical expiry.dat path — the only location used at runtime."""
    return _expiry_path()


def all_expiry_paths():
    """Single canonical expiry.dat location (AppData in EXE, config/ in dev)."""
    return [_expiry_path()]


def expiry_write_paths():
    """Only the canonical store is written at runtime."""
    return [_expiry_path()]


def _coerce_enabled(value) -> bool:
    """Normalize enabled flag — handles bool, 0/1, and string values from manual edits."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _read_expiry_payload_from_path(path: str) -> dict:
    if path != _expiry_path() or not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            data = _decrypt(f.read())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_expiry() -> dict:
    """Read expiry from the single canonical AppData/config path."""
    return _read_expiry_payload_from_path(_expiry_path())


def _write_expiry_payload(payload: dict):
    """Write encrypted expiry.dat to the canonical store only."""
    path = _expiry_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(_encrypt(payload))
    except Exception:
        pass


def _expiry_config_path() -> str:
    return os.path.join(_appdata_dir(), 'expiry_config.json')


def read_expiry_config() -> dict:
    """JSON settings for whether expiry.dat is consulted at all."""
    path = _expiry_config_path()
    if not os.path.isfile(path):
        return {'apply_expiry_check': True}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {'apply_expiry_check': True}


def write_expiry_config(apply_expiry_check: bool) -> None:
    path = _expiry_config_path()
    payload = {'apply_expiry_check': bool(apply_expiry_check)}
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def is_expiry_check_applied() -> bool:
    return _coerce_enabled(read_expiry_config().get('apply_expiry_check', True))


def write_expiry(expiry_date_str: str, enabled: bool = True):
    """Write expiry.dat with given date (YYYY-MM-DD) and enabled flag."""
    _write_expiry_payload({
        'enabled': bool(enabled),
        'expiry_date': str(expiry_date_str).strip(),
    })


def write_build_expiry(expiry_date_str: str, enabled: bool = True):
    """Write config/expiry.dat before EXE build — same path as dev-mode storage."""
    write_expiry(expiry_date_str, enabled=enabled)


def _activation_date_path() -> str:
    return os.path.join(_appdata_dir(), 'activation_date.txt')


def get_activation_date() -> str:
    """Local activation date (YYYY-MM-DD) from activation.dat or activation_date.txt."""
    data = _read_activation()
    if data.get('date'):
        return str(data['date']).strip()[:10]
    path = _activation_date_path()
    try:
        if os.path.isfile(path):
            return open(path, encoding='utf-8').read().strip()[:10]
    except Exception:
        pass
    return ''


def _stored_activation_date_file() -> str:
    """What activation_date.txt itself holds -- not what activation.dat says.

    get_activation_date() prefers activation.dat, so comparing against it would
    rewrite activation_date.txt on every licence read whenever the two differ.
    """
    try:
        path = _activation_date_path()
        if os.path.isfile(path):
            return open(path, encoding='utf-8').read().strip()[:10]
    except Exception:
        pass
    return ''


def save_activation_date_local(day: str | None = None) -> str:
    from datetime import date

    raw = (day or str(date.today())).strip()[:10]
    try:
        date.fromisoformat(raw)
    except Exception:
        raw = str(date.today())
    try:
        with open(_activation_date_path(), 'w', encoding='utf-8') as f:
            f.write(raw)
    except Exception:
        pass
    return raw


def _is_online_mode() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def fetch_server_license() -> dict | None:
    """Fetch license/access from server when Online. None if unavailable."""
    if not _is_online_mode():
        return None
    try:
        from core import server_api as api

        token = api.store_token_for_active()
        data = api.get_license(token)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def push_server_license(payload: dict) -> dict | None:
    """Push license fields to server (Online). Returns server license or None."""
    if not _is_online_mode():
        return None
    try:
        from core import server_api as api

        token = api.store_token_for_active()
        return api.put_license(token, payload or {})
    except Exception:
        return None


def record_activation_online() -> str:
    """Save activation date on PC and to server when Online (sets default expiry)."""
    day = save_activation_date_local()
    if _is_online_mode():
        # Server stores activation_date + default expiry window
        push_server_license({
            "activation_date": day,
            "expiry_enabled": True,
            "apply_expiry_check": True,
        })
    return day


def get_expiry_state(*, force_server: bool = True) -> dict:
    """Return expiry/access state. Online always fetches from the server.

    Local ``expiry.dat`` is only a cache of the last successful server read/write.
    Offline mode reads AppData only.
    """
    from datetime import date as _date

    online = _is_online_mode()
    if online and force_server:
        lic = fetch_server_license()
        if isinstance(lic, dict):
            try:
                _cache_server_license_locally(lic)
            except Exception:
                pass
            # The server has just answered, so this is the cheapest moment there
            # will ever be to refresh the signed blob -- and the blob is what
            # this PC falls back to the next time the server is unreachable. The
            # fetch rate-limits itself to once a minute, so a status endpoint
            # polled several times a second cannot turn this into traffic.
            try:
                from core import license_seal as seal

                # wait=0: start it and walk away. The refresh is for the NEXT
                # launch, when the server may be unreachable; blocking a polled
                # status read on it would be paying now for a benefit later.
                seal.fetch_seal(timeout=10.0, wait=0.0)
            except Exception:
                pass
            return {
                "online": True,
                "source": "server",
                "enabled": bool(lic.get("expiry_enabled", False)),
                "expiry_date": str(lic.get("expiry_date") or "")[:10],
                "apply_expiry_check": bool(lic.get("apply_expiry_check", True)),
                "activation_date": str(lic.get("activation_date") or "")[:10]
                or get_activation_date(),
                "is_active": lic.get("is_active"),
                "access_allowed": lic.get("access_allowed"),
                "server_date": str(lic.get("server_date") or _date.today())[:10],
                "server": lic,
            }
        # Unreachable: the SIGNED blob first, and only then the unsigned mirror.
        return _offline_expiry_state(online=True)

    return _offline_expiry_state(online=False)


def _offline_expiry_state(*, online: bool) -> dict:
    """Expiry as this computer knows it without the server.

    The signed blob is consulted first and, when it verifies, it is the answer --
    including the ``apply_expiry_check`` flag, which used to be read out of
    ``expiry_config.json``. That file is plain JSON in the shop's own AppData:
    ``{"apply_expiry_check": false}`` typed into Notepad switched the entire
    expiry mechanism off. It is now only consulted for a shop that predates the
    seal, and even then only inside the grace window.
    """
    from datetime import date as _date

    from core import license_seal as seal

    st = seal.seal_state(fetch=False)
    if st.get("state") == seal.STATE_FRESH:
        # A fresh install whose sign-up never reached the server, running on its
        # starter window. Reported with real dates so Settings and the licence
        # actually being enforced say the same thing -- an empty expiry here is
        # what made the Settings page claim "no restriction" about a PC that was
        # three days from stopping.
        return {
            "online": online,
            "source": "fresh",
            "enabled": True,
            "expiry_date": str(st.get("expiry_date") or "")[:10],
            "apply_expiry_check": True,
            "activation_date": str(st.get("activation_date") or "") or get_activation_date(),
            "is_active": True,
            "access_allowed": not bool(st.get("blocked")),
            "server_date": str(_date.today())[:10],
            "seal_state": st.get("state"),
            "seal_message": str(st.get("message") or ""),
            "server": None,
        }
    if st.get("state") == seal.STATE_SIGNED:
        return {
            "online": online,
            "source": "sealed",
            "enabled": bool(st.get("enabled")),
            "expiry_date": str(st.get("expiry_date") or "")[:10],
            "apply_expiry_check": bool(st.get("apply_expiry_check", True)),
            "activation_date": str(st.get("activation_date") or "") or get_activation_date(),
            "is_active": bool(st.get("is_active", True)),
            "access_allowed": not bool(st.get("blocked")),
            "server_date": str(_date.today())[:10],
            "seal_state": st.get("state"),
            "seal_message": str(st.get("message") or ""),
            "server": None,
        }
    if st.get("blocked"):
        # No licence on this computer, and no grace left. Not "no restriction":
        # no answer, and the shop is told what to do about it.
        return {
            "online": online,
            "source": "unsealed",
            "enabled": True,
            "expiry_date": "",
            "apply_expiry_check": True,
            "activation_date": get_activation_date(),
            "is_active": True,
            "access_allowed": False,
            "server_date": str(_date.today())[:10],
            "seal_state": st.get("state"),
            "seal_message": str(st.get("message") or ""),
            "server": None,
        }

    data = _read_expiry() or {}
    cfg = read_expiry_config()
    return {
        "online": online,
        "source": "cache" if online else "local",
        "enabled": bool(data.get("enabled", False)),
        "expiry_date": str(data.get("expiry_date") or "")[:10],
        "apply_expiry_check": bool(cfg.get("apply_expiry_check", True)),
        "activation_date": get_activation_date(),
        "is_active": None if online else True,
        "access_allowed": None if online else True,
        "server_date": str(_date.today())[:10],
        "seal_state": st.get("state"),
        "seal_message": str(st.get("message") or ""),
        "server": None,
    }


def _cache_server_license_locally(lic: dict) -> None:
    """Mirror server license into AppData cache (not a source of truth Online).

    A BLANK server value must never overwrite a good local one. The old
    condition fired whenever "expiry_enabled" was present, even if the server
    sent no date at all, and then wrote expiry_date="" over the real one. A shop
    with an expiry years away lost it on the next licence read -- the file kept
    changing on its own, and the app started asking to be activated again.
    A cache may fill a gap; it may not erase what it is caching.
    """
    current = _read_expiry() or {}
    remote_date = str(lic.get("expiry_date") or "").strip()
    wanted = None
    if remote_date:
        wanted = {
            "enabled": bool(lic.get("expiry_enabled", False)),
            "expiry_date": remote_date,
        }
    elif "expiry_enabled" in lic:
        # The server spoke about expiry but gave no date: keep the stored date
        # and only follow the on/off flag.
        wanted = {
            "enabled": bool(lic.get("expiry_enabled", False)),
            "expiry_date": str(current.get("expiry_date") or "").strip(),
        }
    if wanted is not None:
        # Write only on a real change. Every licence read used to rewrite this
        # file, and because the encryption embeds a fresh timestamp the bytes
        # differed every time -- so the expiry file appeared to keep changing on
        # its own even while the date stayed years away. Same value, no write.
        same = (
            bool(current.get("enabled")) == wanted["enabled"]
            and str(current.get("expiry_date") or "").strip() == wanted["expiry_date"]
        )
        if not same:
            _write_expiry_payload(wanted)
    if "apply_expiry_check" in lic:
        flag = bool(lic.get("apply_expiry_check", True))
        if _coerce_enabled(read_expiry_config().get("apply_expiry_check", True)) != flag:
            write_expiry_config(flag)
    remote_activation = str(lic.get("activation_date") or "")[:10]
    if remote_activation and remote_activation != _stored_activation_date_file():
        save_activation_date_local(remote_activation)


def _resolve_remote_store_id(admin_token: str) -> str:
    """Which store on the server is this PC's store?

    The saved session first -- it holds the ``store_id`` the pairing returned.
    An Offline shop that activated while the server was unreachable has no
    session, so the fallback is to match this PC's SC- key against the store
    list, which an administrator token is entitled to read. Nothing here matches
    on the shop NAME: two shops may be called the same thing, and picking the
    wrong one would move a stranger's expiry date.
    """
    try:
        from core import server_api as api
        from core.store_manager import get_active_store_key

        store_key = get_active_store_key() or "Store_Default"
        sid = str((api.load_session(store_key) or {}).get("store_id") or "").strip()
        if sid:
            return sid
    except Exception:
        pass
    try:
        from core import server_api as api
        from core.store_link import get_local_android_key

        key = (get_local_android_key() or "").strip()
        if not key:
            return ""
        for row in api.list_remote_stores(admin_token) or []:
            if str((row or {}).get("android_key") or "").strip() == key:
                return str(row.get("store_id") or "").strip()
    except Exception:
        pass
    return ""


def save_expiry_settings(
    *,
    enabled: bool,
    expiry_date: str,
    apply_expiry_check: bool = True,
    activation_date: str | None = None,
    admin_username: str = "",
    admin_password: str = "",
    push_remote: bool = True,
) -> dict:
    """Administrator expiry save. REQUIRES the internet, and writes both records.

    THE OWNER'S RULE. Once an expiry is set at first activation the only two
    things that may move it are the server and this screen -- and this screen may
    only move it THROUGH the server. There is no local-only path any more, in
    either mode. An Offline shop editing its expiry with the network unplugged
    used to write a local file and nothing else, which is the same as saying the
    shop sets its own expiry.

    ONE CALL, so the two records cannot disagree. The server writes the row and
    signs the blob from that same row, in one request; the blob is then stored
    here. Either both change or neither does.

    THE CREDENTIAL is the vendor's administrator username and password, typed at
    the moment of the edit. It is deliberately NOT compiled into the build, and
    deliberately NOT the store's own token: ``PUT /auth/license`` on the server
    strips every expiry field out of a device's request precisely so that a shop
    cannot extend itself, and this must not be the way around that.
    """
    from datetime import date as _date

    expiry_date = str(expiry_date or "").strip()
    act = str(activation_date or "").strip()[:10]
    if act:
        try:
            _date.fromisoformat(act)
        except Exception:
            act = ""

    if not push_remote:
        return {
            "ok": False,
            "error": (
                "Expiry can only be changed through the Satpuda server. "
                "There is no offline way to set it."
            ),
            "local": None,
            "activation_date": get_activation_date(),
            "apply_expiry_check": bool(apply_expiry_check),
            "server": None,
        }

    admin_username = str(admin_username or "").strip()
    admin_password = str(admin_password or "")
    if not admin_username or not admin_password:
        return {
            "ok": False,
            "error": (
                "Changing the expiry needs the Satpuda administrator username "
                "and password."
            ),
            "local": None,
            "activation_date": get_activation_date(),
            "apply_expiry_check": bool(apply_expiry_check),
            "server": None,
        }

    from core import license_seal as seal
    from core import server_api as api

    def _refuse(message: str) -> dict:
        return {
            "ok": False,
            "error": message,
            "local": None,
            "activation_date": get_activation_date(),
            "apply_expiry_check": bool(apply_expiry_check),
            "server": None,
        }

    try:
        admin_token = api.admin_login(admin_username, admin_password)
    except Exception as exc:
        return _refuse(
            "Could not sign in to the Satpuda server as administrator: "
            f"{exc}. Expiry was not changed."
        )

    store_id = _resolve_remote_store_id(admin_token)
    if not store_id:
        return _refuse(
            "This computer is not linked to a store on the Satpuda server, so "
            "its expiry cannot be changed from here. Expiry was not changed."
        )

    body = {
        "expiry_enabled": bool(enabled),
        "expiry_date": expiry_date,
        "apply_expiry_check": bool(apply_expiry_check),
    }
    if act:
        body["activation_date"] = act
    try:
        body.update(seal._binding_body())
    except Exception:
        pass

    try:
        result = api.admin_set_license(admin_token, store_id, body)
    except Exception as exc:
        return _refuse(f"The server did not save the expiry: {exc}. Nothing was changed.")

    remote = dict((result or {}).get("license") or {})
    blob = str((result or {}).get("signed") or "")
    if not remote:
        return _refuse("The server did not return the licence. Nothing was changed.")

    stored = seal.store_seal(blob, fresh=True) if blob else None
    if blob and not stored:
        # The row moved but the blob will not verify here -- a build carrying the
        # wrong public key, or a machine binding that does not match. Say so
        # rather than leaving the shop believing the two agree.
        return {
            "ok": False,
            "error": (
                "The server saved the expiry but this computer could not verify "
                "the signed licence it sent back. The licence file was not "
                "changed; contact Satpuda."
            ),
            "local": None,
            "activation_date": str(remote.get("activation_date") or "")[:10],
            "apply_expiry_check": bool(remote.get("apply_expiry_check", apply_expiry_check)),
            "server": remote,
        }

    # The unsigned mirror is kept in step so an older build reading the same
    # AppData, and every screen that still reads it, shows the same date. It is
    # no longer an authority anywhere.
    _cache_server_license_locally(remote)
    return {
        "ok": True,
        "local": {
            "enabled": bool(remote.get("expiry_enabled", enabled)),
            "expiry_date": str(remote.get("expiry_date") or expiry_date)[:10],
        },
        "activation_date": str(remote.get("activation_date") or act or "")[:10]
        or get_activation_date(),
        "apply_expiry_check": bool(remote.get("apply_expiry_check", apply_expiry_check)),
        "sealed": bool(stored),
        "server": remote,
    }


def _invalidate_activation_for_reauth() -> None:
    act = _activation_path()
    if os.path.exists(act):
        try:
            os.remove(act)
        except Exception:
            pass
    hw = _get_hardware_hash()
    _write_device_key(hw)


# Set once the offline expiry day has already forced a re-activation in this
# process, so a repeated status read cannot wipe activation.dat again.
_REAUTH_FORCED = False


def _legacy_check_expiry(*, reauth: bool) -> bool:
    """The pre-seal rules, applied to the unsigned local files.

    Kept EXACTLY as they were, and reachable only from the legacy grace window --
    a shop that was already running when the seal did not exist yet. It is the
    behaviour that must not change for those shops on the day they take this
    update; it is also the behaviour that could be defeated by deleting a file,
    which is why nothing else reaches it.
    """
    from datetime import date

    if not is_expiry_check_applied():
        return False
    data = _read_expiry()
    if not data:
        return False
    if not _coerce_enabled(data.get('enabled', False)):
        return False
    try:
        expiry_date = date.fromisoformat(str(data['expiry_date']).strip())
    except Exception:
        return False
    # >= not ==. Exact-date matching meant the licence lapsed for exactly one
    # day: a PC switched off on the expiry date, or simply opened the next
    # morning, sailed past it and never expired again.
    if date.today() < expiry_date:
        return False
    if reauth:
        _force_reauth_once()
    return True


def _force_reauth_once() -> None:
    """Blank activation.dat, at most once per run, to force re-activation.

    This function is named like a question but answers it by DELETING
    activation.dat. The Tauri status endpoint calls the licence check on every
    poll, so on the expiry day the sequence became: read status -> activation
    wiped -> user types correct credentials -> next poll wipes it again. No
    credentials could break out of that until midnight. Blocking access is
    intended; doing it over and over is not.
    """
    global _REAUTH_FORCED
    if not _REAUTH_FORCED:
        _REAUTH_FORCED = True
        _invalidate_activation_for_reauth()


def _sealed_expiry_blocks(*, reauth: bool) -> bool:
    """Does the SIGNED licence deny access? The authority when there is one.

    Three outcomes, and the third is the whole point of the exercise:

      * a verified blob -> it decides, exactly as the server wrote it. Editing
        the date breaks the signature and never gets here; copying the blob to
        another PC breaks the machine binding and never gets here either.
      * no blob, or one that does not verify -> BLOCKED, and deliberately not as
        an expiry. A missing licence is not "no restriction" -- it is no licence,
        and the shop is asked to connect the internet once so the same one comes
        back. It must NOT force re-activation: that deletes activation.dat, which
        is the one record saying this PC was ever activated, and destroying it
        because a file went missing makes a recoverable situation worse.
      * a shop that predates the seal, inside the grace window -> the old rules,
        unchanged, while it gets its one chance to be online.
    """
    from core import license_seal as seal

    state = seal.seal_state(fetch=True, timeout=8.0)
    if state.get("state") == seal.STATE_SIGNED:
        if not state.get("blocked"):
            return False
        if reauth and state.get("reason") == "expired":
            _force_reauth_once()
        return True
    if state.get("state") == seal.STATE_FRESH:
        # The starter window a fresh install runs on. It answers on its own and
        # never falls through to the legacy files, and it never forces
        # re-activation: the window is measured FROM activation.dat, so deleting
        # that record to make the shop re-activate would also hand it a new
        # window. The way out of a lapsed starter window is a licence from the
        # server or the Administrator screen, not a wiped activation.
        return bool(state.get("blocked"))
    if state.get("blocked"):
        return True
    return _legacy_check_expiry(reauth=reauth)


def check_expiry() -> bool:
    """Check if the expiry / access restriction has triggered.

    Returns True if access is blocked, False otherwise.

    Online mode: the server, live, exactly as before. When it cannot be reached
    the fallback is now the SIGNED blob rather than the unsigned local mirror,
    because the unsigned mirror is a file the shop can delete.

    Offline mode: the signed blob. See ``_sealed_expiry_blocks``.
    """
    from datetime import date

    # ── Online: server is source of truth (never Offline exact-day rules) ──
    if _is_online_mode():
        state = get_expiry_state(force_server=True)
        lic = state.get("server")
        if isinstance(lic, dict):
            allowed = lic.get("access_allowed")
            if allowed is False or lic.get("is_active") is False:
                # Keep SC- pairing / Online activation — admin extends expiry; no re-activate.
                return True
            if not bool(lic.get("apply_expiry_check", True)):
                return False
            if not bool(lic.get("expiry_enabled", False)):
                return False
            try:
                expiry_date = date.fromisoformat(str(lic.get("expiry_date") or "").strip())
            except Exception:
                return False
            today_raw = str(lic.get("server_date") or date.today()).strip()[:10]
            try:
                today = date.fromisoformat(today_raw)
            except Exception:
                today = date.today()
            if today >= expiry_date:
                return True
            return False
        # Server unreachable. The signed blob, NOT the unsigned cache: the cache
        # is a file, and a file that grants access when it is deleted is exactly
        # what this change exists to remove. Online mode never forces
        # re-activation -- the administrator extends the date instead.
        return _sealed_expiry_blocks(reauth=False)

    # ── Offline ───────────────────────────────────────────────────────────
    return _sealed_expiry_blocks(reauth=True)


def expiry_block_reason() -> str:
    """Why access is blocked: 'expired', 'access_disabled', 'needs_internet' or ''.

    The screen has to tell these apart. "Your licence ran out" and "this computer
    has lost its licence file and needs the internet for a minute" are different
    sentences with different next steps, and the second must not send a shopkeeper
    hunting for a device key that would not help him.

    LOCAL ONLY, and never a network call. It is read from the polled licence
    status endpoint, right after ``check_expiry`` has already asked the server;
    asking again on the same poll would double the traffic of every shop in the
    fleet to answer a question about wording. So it reports what this computer
    can see, and "" means "blocked, but not for want of a licence file" -- which
    is all any caller needs from it.
    """
    from core import license_seal as seal

    st = seal.seal_state(fetch=False)
    if st.get("state") == seal.STATE_SIGNED:
        return str(st.get("reason") or "")
    if st.get("blocked"):
        # The state's own word for it where there is one -- a lapsed starter
        # window says "expired", because that shop is out of TIME, not out of a
        # file, and telling it to connect the internet would send it looking for
        # a fix that is not there. Everything else still means "needs_internet".
        return str(st.get("reason") or "needs_internet")
    return ""


# ── Public API ─────────────────────────────────────────────────────────────────
def is_activated() -> bool:
    """True if activation.dat exists AND this is still the activated device."""
    data = _read_activation()
    if not data or 'hw' not in data:
        return False
    if hardware_matches(data):
        # A record from before this build has no components. Now that it has
        # been accepted, record them, so the next Windows update or USB drive
        # cannot make this machine look like a stranger.
        if not isinstance(data.get('hw_parts'), dict):
            try:
                _upgrade_activation_record(data)
            except Exception:
                pass
        return True
    return False


def _identity_readable(parts: dict) -> bool:
    """True when at least one component that identifies the machine was read."""
    return any(str((parts or {}).get(k) or '').strip() for k in _HW_IDENTITY_KEYS)


def _upgrade_activation_record(data: dict) -> None:
    """Add components to an accepted legacy activation, keeping its own date.

    Only when the machine could actually be read. Storing an all-blank identity
    would turn this activation.dat into a key that matches ANY computer, because
    a record with nothing to compare is deliberately accepted rather than
    locking a shop out of its own till.
    """
    parts = _hardware_parts()
    if not _identity_readable(parts):
        return
    record = dict(data)
    record['hw_parts'] = parts
    with open(_activation_path(), 'wb') as f:
        f.write(_encrypt(record))


def has_valid_online_session() -> bool:
    """Online: SC- key present and JWT obtainable. License expiry is separate."""
    try:
        from core.store_link import get_local_android_key
        from core import server_api as api

        if not (get_local_android_key() or "").strip():
            return False
        token = (api.store_token_for_active() or "").strip()
        return bool(token)
    except Exception:
        # Key exists but server unreachable — still considered paired.
        try:
            from core.store_link import get_local_android_key

            return bool((get_local_android_key() or "").strip())
        except Exception:
            return False


def needs_activation() -> bool:
    """Offline: hardware activation.dat.

    Online: only when this PC has never been paired (no SC- key). JWT refresh is
    silent; server expiry blocks via ``check_expiry`` — no second activation until
    the license date is reached or access is disabled.
    """
    if _is_online_mode():
        try:
            from core.store_link import get_local_android_key

            if not (get_local_android_key() or "").strip():
                return True
            # Best-effort silent JWT refresh; do not treat license deny as "needs activation"
            try:
                from core import server_api as api

                api.store_token_for_active()
            except Exception:
                pass
            return False
        except Exception:
            return True
    return not is_activated()


def prepare_device_key():
    """Generate hardware fingerprint and write device.key (does not touch expiry.dat)."""
    hw = _get_hardware_hash(force_refresh=True)
    _write_device_key(hw)

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
    current_hw = _get_hardware_hash(force_refresh=True)
    if device_key_input.strip() != stored_hw:
        return False, "Invalid device key."
    if stored_hw != current_hw:
        return False, "Device key does not match this hardware."

    # All 3 factors valid — activate (Offline license file; Online also writes for audit)
    _write_activation(current_hw)
    _write_hw_cache(current_hw)
    _delete_device_key()
    try:
        record_activation_online()
    except Exception:
        pass
    return True, ""
