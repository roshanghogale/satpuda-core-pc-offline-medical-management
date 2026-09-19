"""When to run a full local↔server merge (push all + pull all).

Runs only:
  1. First Online mode with existing local business data, or
  2. Administrator switches Offline → Online (pending flag), or
  3. Online activation (pending flag).

Normal operation uses watermark polling + per-save push.
"""
from __future__ import annotations

import os
import sqlite3

from core.sync_prefs import _appdata_dir, is_online_mode


def _store_tag() -> str:
    try:
        from core.store_manager import get_active_store_key
        from core import server_api as api
        key = get_active_store_key() or ''
        if key:
            sid = (api.load_session(key).get('store_id') or '').strip()
            if sid:
                return sid
            return key.lower().replace(' ', '_')
    except Exception:
        pass
    return 'store_default'


def _pending_path() -> str:
    tag = _store_tag()
    new_p = os.path.join(_appdata_dir(), f'server_bootstrap_pending_{tag}.txt')
    legacy = os.path.join(_appdata_dir(), f'firebase_bootstrap_pending_{tag}.txt')
    if not os.path.isfile(new_p) and os.path.isfile(legacy):
        return legacy
    return new_p


def _done_path() -> str:
    tag = _store_tag()
    new_p = os.path.join(_appdata_dir(), f'server_bootstrap_done_{tag}.txt')
    legacy = os.path.join(_appdata_dir(), f'firebase_bootstrap_done_{tag}.txt')
    if not os.path.isfile(new_p) and os.path.isfile(legacy):
        return legacy
    return new_p


def mark_pending_bootstrap() -> None:
    os.makedirs(_appdata_dir(), exist_ok=True)
    with open(_pending_path(), 'w', encoding='utf-8') as fh:
        fh.write('1')
    try:
        from core.sync_watermarks import clear_all
        clear_all()
    except Exception:
        pass


def clear_pending_bootstrap() -> None:
    try:
        os.remove(_pending_path())
    except OSError:
        pass


def mark_bootstrap_done() -> None:
    os.makedirs(_appdata_dir(), exist_ok=True)
    with open(_done_path(), 'w', encoding='utf-8') as fh:
        fh.write('1')
    clear_pending_bootstrap()


def is_bootstrap_done() -> bool:
    return os.path.isfile(_done_path())


def is_pending_bootstrap() -> bool:
    return os.path.isfile(_pending_path())


def local_db_has_business_data(conn: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    for table in (
        'customers', 'medicines', 'sales', 'purchases', 'suppliers',
        'doctors', 'customer_payments', 'supplier_payments',
    ):
        try:
            cur.execute(f'SELECT COUNT(*) FROM {table}')
            if (cur.fetchone() or (0,))[0] > 0:
                return True
        except Exception:
            pass
    try:
        cur.execute(
            "SELECT COUNT(*) FROM settings WHERE name IN "
            "('pharmacy_name', 'pharmacy_address', 'pharmacy_phone', 'pharmacy_gst')"
        )
        if (cur.fetchone() or (0,))[0] > 0:
            return True
    except Exception:
        pass
    return False


def should_run_bootstrap(conn: sqlite3.Connection) -> bool:
    """Automatic first-sync is disabled.

    Online mode only starts the live poller (incremental). Users download
    history explicitly via Settings → Pull from Server.
    """
    del conn  # unused — kept for call-site compatibility
    if not is_online_mode():
        return False
    # Clear any leftover pending flags from older builds so they never re-fire.
    if is_pending_bootstrap():
        try:
            clear_pending_bootstrap()
            mark_bootstrap_done()
        except Exception:
            pass
    return False
