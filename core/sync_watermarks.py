"""Per-collection server pull watermarks (updated_at).

B3: Not used by live SyncEngine (USE_REVISION_SYNC=true default).
Kept for:
- Manual Pull from Server / disaster-recovery full merge
- USE_REVISION_SYNC=false rollback watermark poller

After a full merge, clients may query docs with updated_at > watermark so
unchanged bills/medicines are not re-downloaded.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.sync_prefs import _appdata_dir

# Collections that stamp updated_at on every write (ConflictResolver entities).
WATERMARK_COLLECTIONS = frozenset({
    'customers', 'suppliers', 'medicines', 'sales', 'purchases',
    'customer_payments', 'supplier_payments', 'doctors',
    'sales_returns', 'purchase_returns',
    'racks', 'sections', 'boxes',
})

# Re-fetch a small overlap window to tolerate clock skew / same-second writes.
OVERLAP_SECONDS = 120

_TABLE_FOR = {
    'customers': 'customers',
    'suppliers': 'suppliers',
    'medicines': 'medicines',
    'sales': 'sales',
    'purchases': 'purchases',
    'customer_payments': 'customer_payments',
    'supplier_payments': 'supplier_payments',
    'doctors': 'doctors',
    'sales_returns': 'sales_returns',
    'purchase_returns': 'purchase_returns',
    'racks': 'racks',
    'sections': 'sections',
    'boxes': 'boxes',
}


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


def _path() -> str:
    tag = _store_tag()
    new_path = os.path.join(_appdata_dir(), f'server_sync_watermarks_{tag}.json')
    legacy = os.path.join(_appdata_dir(), f'firebase_sync_watermarks_{tag}.json')
    if not os.path.isfile(new_path) and os.path.isfile(legacy):
        try:
            shutil.copy2(legacy, new_path)
        except Exception:
            pass
    return new_path


def _load() -> dict:
    p = _path()
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(_appdata_dir(), exist_ok=True)
    with open(_path(), 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=0)


def get_watermark(collection: str) -> Optional[str]:
    if collection not in WATERMARK_COLLECTIONS:
        return None
    val = _load().get(collection)
    return str(val).strip() if val else None


def set_watermark(collection: str, iso_ts: str) -> None:
    if collection not in WATERMARK_COLLECTIONS:
        return
    ts = (iso_ts or '').strip()
    if not ts:
        return
    data = _load()
    prev = data.get(collection)
    if prev and str(prev) >= ts:
        return
    data[collection] = ts
    _save(data)


def bump_watermark(collection: str, iso_ts: Optional[str]) -> None:
    if iso_ts:
        set_watermark(collection, iso_ts)


def query_since(collection: str) -> Optional[str]:
    """Watermark minus overlap — used as Server where updated_at > since."""
    wm = get_watermark(collection)
    if not wm:
        return None
    try:
        raw = wm.replace('Z', '+00:00')
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        since = dt - timedelta(seconds=OVERLAP_SECONDS)
        return since.isoformat()
    except Exception:
        return wm


def seed_from_local(conn, collection: str) -> Optional[str]:
    """Set watermark from MAX(local.updated_at) after a full pull."""
    table = _TABLE_FOR.get(collection)
    if not table:
        return None
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT MAX(updated_at) FROM {table}')
        row = cur.fetchone()
        ts = (row[0] if row else None) or None
        if not ts:
            ts = datetime.now(timezone.utc).isoformat()
        set_watermark(collection, str(ts))
        return str(ts)
    except Exception:
        ts = datetime.now(timezone.utc).isoformat()
        set_watermark(collection, ts)
        return ts


def seed_all_from_local(conn) -> None:
    for col in WATERMARK_COLLECTIONS:
        seed_from_local(conn, col)


def seed_all_now() -> None:
    """Stamp every watermark to now so the poller only fetches future changes.

    Used when Online is enabled without an automatic full download — manual
    Pull from Server remains the way to import history.
    """
    ts = datetime.now(timezone.utc).isoformat()
    data = _load()
    for col in WATERMARK_COLLECTIONS:
        data[col] = ts
    _save(data)


def clear_all() -> None:
    try:
        os.remove(_path())
    except OSError:
        pass
