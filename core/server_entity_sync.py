"""
Server Server sync for mac2 — mirrors Android ServerSyncHelper.

Path: stores/{store_id}/{collection}/{doc_id}

Requires a Satpuda Core Server credentials JSON bundled at config/server_service_account.json
(same project as Android google-services.json). Administrator paste is optional override.
"""
from __future__ import annotations

import json
import logging
import os
import re
import queue
import sqlite3
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_DEFAULT_PROJECT = 'satpuda-core-online'
COLLECTIONS = [
    'customers', 'suppliers', 'medicines', 'sales', 'purchases',
    'customer_payments', 'supplier_payments', 'doctors',
    'sales_returns', 'purchase_returns', 'medicines_master',
    'racks', 'sections', 'boxes',
]
SETTINGS_COLLECTION = 'settings'

_client = None
_listeners: list = []
_ready = False
_on_change: Optional[Callable[[str], None]] = None
_cached_project_id: Optional[str] = None
_db_lock = threading.RLock()
_apply_queue: queue.Queue = queue.Queue()
_apply_worker_started = False
_notify_cols: set = set()
_notify_lock = threading.Lock()
_notify_timer = None
_permission_error: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_only(value: Any) -> str:
    """Normalize calendar dates — strip sync ISO noise like …T00:00:00.000Z."""
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return ''
    if 'T' in s:
        s = s.split('T', 1)[0]
    elif ' ' in s:
        s = s.split(' ', 1)[0]
    return s[:10]


# Server rejects some SQLite/Python types (e.g. datetime.date).
SERVER_PUSH_TIMEOUT = 8.0
SERVER_PUSH_RETRY_TIMEOUT = 15.0


def _server_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _sanitize_server_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return _sanitize_server_doc(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_server_value(v) for v in value]
    return _server_scalar(value)


def _sanitize_server_doc(data: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        out[str(key)] = _sanitize_server_value(value)
    return out


def _db_retry(fn, *, attempts: int = 12, base_delay: float = 0.05):
    """Retry SQLite writes when another connection (UI / listener) holds the DB."""
    from core.db_utils import db_retry
    return db_retry(fn, attempts=attempts, base_delay=base_delay)


def _appdata_dir() -> str:
    from core.license_manager import _appdata_dir as d
    return d()


def _creds_path() -> str:
    return os.path.join(_appdata_dir(), 'server_service_account.json')


def _bundled_creds_path() -> str:
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'config', 'server_service_account.json')
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'config', 'server_service_account.json',
    )


def _active_creds_path() -> str:
    if os.path.isfile(_creds_path()):
        return _creds_path()
    return _bundled_creds_path()


def _project_id_from_file(path: str) -> str:
    try:
        with open(path, encoding='utf-8') as fh:
            pid = json.load(fh).get('project_id')
        if pid:
            return str(pid)
    except Exception:
        pass
    return _DEFAULT_PROJECT


def get_project_id() -> str:
    global _cached_project_id
    if _cached_project_id:
        return _cached_project_id
    path = _active_creds_path()
    _cached_project_id = _project_id_from_file(path) if os.path.isfile(path) else _DEFAULT_PROJECT
    return _cached_project_id


def _reset_client_cache() -> None:
    global _client, _ready, _cached_project_id, _permission_error
    _client = None
    _ready = False
    _cached_project_id = None
    _permission_error = None


def is_configured() -> bool:
    """Online sync is always configured via Satpuda Core Server (no SA JSON)."""
    return True


def ensure_credentials() -> bool:
    """No Server credentials required — server sync uses JWT pairing."""
    return True


def get_status() -> dict:
    try:
        from core.server_api import api_base, health_ok
        reachable = bool(health_ok(timeout=2.0))
        base = api_base()
    except Exception:
        reachable = False
        # Only a label when server_api itself would not import. Named once, in
        # server_api, so the tunnel host has a single source of truth.
        try:
            from core.server_api import _TUNNEL_BASE as base
        except Exception:
            base = ''
    return {
        'configured': True,
        'ready': reachable,
        'project_id': 'satpuda-core-server',
        'store_id': get_store_id(),
        'api_base': base,
        'permission_error': _permission_error,
    }


def get_store_id() -> str:
    try:
        from core.store_manager import get_active_store_key
        key = get_active_store_key() or 'Store_Default'
        sid = key.lower()
        sid = re.sub(r'[^a-z0-9_]', '_', sid)
        return sid or 'store_default'
    except Exception:
        return 'store_default'


def _get_client():
    """Server cloud client is disabled — Online sync uses Satpuda Core Server."""
    global _client, _ready
    _client = None
    _ready = False
    return None


def server_network_reachable(*, timeout: float = 2.0) -> bool:
    """Compatibility shim — Online reachability is Satpuda Core Server health."""
    try:
        from core.server_api import health_ok
        return bool(health_ok(timeout=min(2.5, float(timeout) or 2.0)))
    except Exception:
        return False


def verify_server_access(*, timeout: float = 8.0) -> tuple[bool, str]:
    """Compatibility shim — checks Satpuda Core Server health (Server removed)."""
    global _permission_error
    if server_network_reachable(timeout=min(3.0, float(timeout))):
        _permission_error = None
        return True, ''
    msg = (
        'No internet / cannot reach Satpuda Core Server. '
        'Online sync is skipped until the network is available.'
    )
    _permission_error = msg
    return False, msg


def save_service_account_json(raw_json: str) -> None:
    data = json.loads(raw_json)
    os.makedirs(_appdata_dir(), exist_ok=True)
    with open(_creds_path(), 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
    _reset_client_cache()


def _col_ref(client, name: str):
    return client.collection('stores').document(get_store_id()).collection(name)


def _settings_ref(client, doc_id: str):
    return _col_ref(client, SETTINGS_COLLECTION).document(doc_id)


def push_doc(collection: str, doc_id: str, data: dict) -> Optional[str]:
    """Push document to Satpuda Core Server (Server network path removed)."""
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return None
        from core import server_live as live
        payload = dict(data or {})
        try:
            payload['id'] = int(doc_id)
        except (TypeError, ValueError):
            payload['id'] = doc_id
        if live.push_docs(collection, [payload]):
            synced_at = _now_iso()
            try:
                from core.sync_status import note_last_sync, note_push
                note_push(collection, doc_id)
                note_last_sync('push')
            except Exception:
                pass
            return synced_at
        return None
    except Exception as exc:
        log.warning('Server push %s/%s failed: %s', collection, doc_id, exc)
        try:
            from core.sync_status import note_error
            note_error(f'push {collection}/{doc_id}: {exc}')
        except Exception:
            pass
        return None


def _finalize_doc_payload(data: dict) -> dict:
    """Normalize sync fields on a Server document payload."""
    cleaned = _sanitize_server_doc(dict(data))
    synced_at = _now_iso()
    cleaned['synced_at'] = synced_at
    if not cleaned.get('updated_at'):
        cleaned['updated_at'] = synced_at
    try:
        cleaned['version'] = int(cleaned.get('version') or 1)
    except (TypeError, ValueError):
        cleaned['version'] = 1
    if cleaned['version'] < 1:
        cleaned['version'] = 1
    if not cleaned.get('device_id'):
        try:
            from core.sync_metadata_schema import get_sync_device_id
            cleaned['device_id'] = get_sync_device_id() or ''
        except Exception:
            cleaned['device_id'] = ''
    cleaned['deleted'] = bool(cleaned.get('deleted'))
    return cleaned


def push_docs_batch(
    writes: list[tuple[str, str, dict]],
    *,
    timeout: float = SERVER_PUSH_TIMEOUT,
) -> bool:
    """Push many documents via Satpuda Core Server bundle/collection APIs."""
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return False
        if not writes:
            return True
        from core import server_live as live
        bundle: dict[str, list] = {}
        for collection, doc_id, data in writes:
            payload = dict(data or {})
            try:
                payload['id'] = int(doc_id)
            except (TypeError, ValueError):
                payload['id'] = doc_id
            bundle.setdefault(collection, []).append(payload)
        ok = live.push_bundle(bundle)
        if ok:
            try:
                from core.sync_status import note_last_sync, note_push
                for collection, doc_id, _data in writes:
                    note_push(collection, doc_id)
                note_last_sync('push')
            except Exception:
                pass
        return ok
    except Exception as exc:
        log.warning('Server batch push failed (%s docs): %s', len(writes or []), exc)
        try:
            from core.sync_status import note_error
            note_error(f'batch push: {exc}')
        except Exception:
            pass
        return False


def delete_doc(collection: str, doc_id: str) -> bool:
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return False
        from core import server_live as live
        return bool(live.delete_remote(collection, int(doc_id)))
    except Exception as exc:
        log.warning('Server delete %s/%s failed: %s', collection, doc_id, exc)
        return False


def wipe_store_data() -> tuple[int, str]:
    """Server wipe removed — clear store data from the server admin dashboard."""
    return 0, (
        'Cloud wipe via Server is disabled. '
        'Clear this store from the Satpuda Core Server admin dashboard if needed.'
    )


def push_settings_doc(doc_id: str, data: dict) -> bool:
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return False
        from core import server_live as live
        from core import server_api as api
        token = live._token()
        key = (doc_id or '').strip()
        payload = dict(data or {})
        if key == 'pharmacy_profile':
            api.push_settings_profile(token, payload)
            return True
        if key == 'dropdowns':
            api.push_settings_dropdowns(token, payload)
            return True
        if key == 'shelf_settings':
            api.push_settings_shelf(token, payload)
            return True
        return bool(live.push_settings_kv(key, payload))
    except Exception as exc:
        log.warning('Server settings push failed: %s', exc)
        return False


# ── Push from SQLite ──────────────────────────────────────────────────────────

def _row_dict(cursor, row) -> dict:
    """Build a dict from a cursor row (first value wins on duplicate column names)."""
    cols = [d[0] for d in cursor.description]
    out: dict = {}
    for col, val in zip(cols, row):
        if col not in out:
            out[col] = val
    return out


def _line_medicine_id(item: dict) -> int:
    """Resolve medicine id from SQLite row or Android-style item keys."""
    for key in ('medicine_id', 'med_id', 'medicineId', 'medId'):
        val = item.get(key)
        if val is None or val == '':
            continue
        try:
            return int(val)
        except (TypeError, ValueError):
            continue
    return 0


def build_sale_payload(conn, sale_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM sales WHERE id=?', (sale_id,))
    row = cur.fetchone()
    if not row:
        return None
    sale = _row_dict(cur, row)
    cur.execute('SELECT * FROM customers WHERE id=?', (sale['customer_id'],))
    cr = cur.fetchone()
    customer = _row_dict(cur, cr) if cr else {}
    cur.execute(
        '''
        SELECT
            si.id AS line_id,
            si.medicine_id,
            si.qty, si.rate, si.gst_percent, si.amount,
            si.item_discount, si.cost_price,
            m.name AS med_name,
            m.type AS med_type,
            m.batch_no AS med_batch_no,
            m.expiry_date AS med_expiry_date,
            m.hsn_code AS med_hsn_code,
            m.schedule AS med_schedule,
            m.manufacturer AS med_manufacturer
        FROM sales_items si
        LEFT JOIN medicines m ON si.medicine_id = m.id
        WHERE si.sale_id=?
        ''',
        (sale_id,),
    )
    items = []
    for ir in cur.fetchall():
        item = _row_dict(cur, ir)
        items.append(
            {
                'medicine_id': _line_medicine_id(item),
                'name': item.get('med_name') or item.get('name'),
                'type': item.get('med_type') or item.get('type'),
                'batch_no': item.get('med_batch_no') or item.get('batch_no'),
                'expiry_date': item.get('med_expiry_date') or item.get('expiry_date'),
                'hsn_code': item.get('med_hsn_code') or item.get('hsn_code'),
                'schedule': item.get('med_schedule') or item.get('schedule'),
                'manufacturer': item.get('med_manufacturer') or item.get('manufacturer'),
                'qty': item.get('qty', 0),
                'rate': item.get('rate', 0),
                'gst_percent': item.get('gst_percent'),
                'amount': item.get('amount', 0),
                'item_discount': item.get('item_discount', 0),
                'cost_price': item.get('cost_price', 0),
            }
        )
    payload = {
        'bill_no': sale['bill_no'],
        'bill_date': sale['bill_date'],
        'total_amount': sale['total_amount'],
        'discount': sale.get('discount', 0),
        'discount_pct': sale.get('discount_pct', 0),
        'rounding': sale.get('rounding', 0),
        'amount_paid': sale.get('amount_paid', 0),
        'cash_paid': sale.get('cash_paid', 0),
        'online_paid': sale.get('online_paid', 0),
        'previous_due': sale.get('previous_due', 0),
        'previous_credit': sale.get('previous_credit', 0),
        'due_amount': sale.get('due_amount', 0),
        'credit_amount': sale.get('credit_amount', 0),
        'total_due': sale.get('total_due', 0),
        'paid_due': sale.get('paid_due', 0),
        'bill_cleared': bool(sale.get('bill_cleared')),
        'account_cleared': bool(sale.get('account_cleared')),
        'doctor_name': sale.get('doctor_name'),
        'created_at': sale.get('created_at'),
        'customer_id': sale['customer_id'],
        'customer_name': customer.get('name'),
        'customer_phone': customer.get('phone'),
        'customer_address': customer.get('address'),
        'item_count': len(items),
        'items': items,
    }
    try:
        from core.fy_serial import display_sales_bill_no, fy_label

        payload['display_bill_no'] = display_sales_bill_no(sale.get('bill_no'))
        if sale.get('fy_start_year') not in (None, ''):
            fy = int(sale['fy_start_year'])
            payload['fy_start_year'] = fy
            payload['fy_label'] = fy_label(fy)
        if sale.get('fy_serial') not in (None, ''):
            payload['fy_serial'] = int(sale['fy_serial'])
    except Exception:
        pass
    payload.update(_sync_meta_fields_from_row(sale))
    try:
        from core.client_uuid import ensure_row_client_uuid

        cu = ensure_row_client_uuid(conn, "sales", int(sale_id))
        if cu:
            payload["client_uuid"] = cu
    except Exception:
        pass
    return _sanitize_server_doc(payload)


def build_customer_payload(conn, customer_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM customers WHERE id=?', (customer_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    c = dict(zip(cols, row))
    payload = {
        'name': c['name'],
        'phone': c.get('phone'),
        'address': c.get('address'),
        'total_due': c.get('total_due', 0),
        'total_credit': c.get('total_credit', 0),
        'created_at': c.get('created_at'),
    }
    payload.update(_sync_meta_fields_from_row(c))
    return payload


def build_medicine_payload(conn, medicine_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM medicines WHERE id=?', (medicine_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    m = dict(zip(cols, row))
    payload = {
        'name': m['name'],
        'type': m.get('type'),
        'stock_qty': m.get('stock_qty', 0),
        'unit': m.get('unit'),
        'gst_percent': m.get('gst_percent'),
        'mrp': m.get('mrp'),
        'rate': m.get('rate'),
        'manufacturer': m.get('manufacturer'),
        'batch_no': m.get('batch_no'),
        'expiry_date': m.get('expiry_date'),
        'hsn_code': m.get('hsn_code'),
        'schedule': m.get('schedule'),
        'location': m.get('location'),
        'content_drug': m.get('content_drug'),
        'is_hidden': bool(int(m.get('is_hidden') or 0)),
    }
    payload.update(_sync_meta_fields_from_row(m))
    try:
        from core.client_uuid import ensure_row_client_uuid

        cu = ensure_row_client_uuid(conn, "medicines", int(medicine_id))
        if cu:
            payload["client_uuid"] = cu
    except Exception:
        pass
    return payload


def build_supplier_payload(conn, supplier_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM suppliers WHERE id=?', (supplier_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    s = dict(zip(cols, row))
    payload = {
        'name': s['name'],
        'phone': s.get('phone'),
        'address': s.get('address'),
        'gstin': s.get('gstin'),
        'total_due': s.get('total_due', 0),
        'total_credit': s.get('total_credit', 0),
    }
    payload.update(_sync_meta_fields_from_row(s))
    return payload


def build_purchase_payload(conn, purchase_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM purchases WHERE id=?', (purchase_id,))
    row = cur.fetchone()
    if not row:
        return None
    p = _row_dict(cur, row)
    cur.execute('SELECT name, phone FROM suppliers WHERE id=?', (p['supplier_id'],))
    sr = cur.fetchone()
    supplier_name = sr[0] if sr else None
    supplier_phone = sr[1] if sr else None
    # See the bulk builder in core/server_sync.py: an old store file predates
    # these three columns, and one unknown column would abort the push.
    try:
        _pi_cols = {r[1] for r in cur.execute("PRAGMA table_info(purchase_items)")}
    except Exception:
        _pi_cols = set()
    _extra = [f"pi.{c}" for c in ("schedule", "hsn_code", "manufacturer") if c in _pi_cols]
    _extra_sql = ("," + ", ".join(_extra)) if _extra else ""
    cur.execute(
        f'''
        SELECT
            pi.id AS line_id,
            pi.medicine_id,
            pi.qty, pi.free_qty, pi.type, pi.rate, pi.mrp, pi.gst_pct,
            pi.batch_no, pi.expiry_date, pi.item_amount{_extra_sql},
            m.name AS med_name,
            m.schedule AS med_schedule,
            m.hsn_code AS med_hsn_code,
            m.manufacturer AS med_manufacturer
        FROM purchase_items pi
        LEFT JOIN medicines m ON pi.medicine_id = m.id
        WHERE pi.purchase_id=?
        ''',
        (purchase_id,),
    )
    items = []
    for ir in cur.fetchall():
        item = _row_dict(cur, ir)
        items.append(
            {
                'medicine_id': _line_medicine_id(item),
                'name': item.get('med_name') or item.get('name'),
                'type': item.get('type'),
                'qty': item.get('qty', 0),
                'free_qty': item.get('free_qty', 0),
                'rate': item.get('rate', 0),
                'mrp': item.get('mrp', 0),
                'gst_pct': item.get('gst_pct', 0),
                'batch_no': item.get('batch_no'),
                'expiry_date': item.get('expiry_date'),
                # Line value first, medicines master second.
                'schedule': item.get('schedule') or item.get('med_schedule'),
                'hsn_code': item.get('hsn_code') or item.get('med_hsn_code'),
                'manufacturer': item.get('manufacturer') or item.get('med_manufacturer'),
                'item_amount': item.get('item_amount', 0),
            }
        )
    payload = {
        'purchase_no': p['purchase_no'],
        'purchase_date': p['purchase_date'],
        'bill_number': p.get('bill_number'),
        'subtotal': p.get('subtotal', 0),
        'total_gst': p.get('total_gst', 0),
        'cgst': p.get('cgst', 0),
        'sgst': p.get('sgst', 0),
        'total_amount': p.get('total_amount', 0),
        'overall_discount': p.get('overall_discount', 0),
        'rounding': p.get('rounding', 0),
        'need_to_pay': p.get('need_to_pay', p.get('final_amount', 0)),
        'final_amount': p.get('final_amount', 0),
        'amount_paid': p.get('amount_paid', 0),
        'amount_paid_at_entry': p.get('amount_paid_at_entry', 0),
        'cash_paid_at_entry': p.get('cash_paid_at_entry', 0),
        'online_paid_at_entry': p.get('online_paid_at_entry', 0),
        'previous_due': p.get('previous_due', 0),
        'previous_credit': p.get('previous_credit', 0),
        'due': p.get('due', p.get('due_amount', 0)),
        'due_amount': p.get('due_amount', p.get('due', 0)),
        'current_credit': p.get('current_credit', p.get('credit_amount', 0)),
        'credit_amount': p.get('credit_amount', p.get('current_credit', 0)),
        'total_due': p.get('total_due', 0),
        'paid_due': p.get('paid_due', 0),
        'bill_cleared': bool(p.get('bill_cleared')),
        'account_cleared': bool(p.get('account_cleared')),
        'expenditure': p.get('expenditure', 0),
        'supplier_id': p['supplier_id'],
        'supplier_name': supplier_name,
        'supplier_phone': supplier_phone,
        'item_count': len(items),
        'items': items,
        'created_at': p.get('created_at'),
    }
    try:
        from core.fy_serial import display_purchase_no, fy_label

        payload['display_purchase_no'] = display_purchase_no(p.get('purchase_no'))
        if p.get('fy_start_year') not in (None, ''):
            fy = int(p['fy_start_year'])
            payload['fy_start_year'] = fy
            payload['fy_label'] = fy_label(fy)
        if p.get('fy_serial') not in (None, ''):
            payload['fy_serial'] = int(p['fy_serial'])
    except Exception:
        pass
    payload.update(_sync_meta_fields_from_row(p))
    try:
        from core.client_uuid import ensure_row_client_uuid

        cu = ensure_row_client_uuid(conn, "purchases", int(purchase_id))
        if cu:
            payload["client_uuid"] = cu
    except Exception:
        pass
    return _sanitize_server_doc(payload)


def push_sale_bundle(
    conn,
    sale_id: int,
    medicine_ids: Optional[list] = None,
    *,
    timeout: float = SERVER_PUSH_TIMEOUT,
) -> bool:
    """One Server batch: sale + customer + affected medicines."""
    writes: list[tuple[str, str, dict]] = []
    sale_payload = build_sale_payload(conn, int(sale_id))
    if not sale_payload:
        return False
    writes.append(('sales', str(sale_id), sale_payload))
    customer_id = sale_payload.get('customer_id')
    if customer_id:
        cp = build_customer_payload(conn, int(customer_id))
        if cp:
            writes.append(('customers', str(customer_id), cp))
    ids: list[int] = []
    seen: set[int] = set()
    for mid in medicine_ids or []:
        try:
            i = int(mid)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            ids.append(i)
    if not ids:
        cur = conn.cursor()
        cur.execute('SELECT medicine_id FROM sales_items WHERE sale_id=?', (sale_id,))
        for (mid,) in cur.fetchall():
            i = int(mid)
            if i not in seen:
                seen.add(i)
                ids.append(i)
    for mid in ids:
        mp = build_medicine_payload(conn, mid)
        if mp:
            writes.append(('medicines', str(mid), mp))
    return push_docs_batch(writes, timeout=timeout)


def push_purchase_bundle(
    conn,
    purchase_id: int,
    *,
    extra_supplier_ids: Optional[list] = None,
    timeout: float = SERVER_PUSH_TIMEOUT,
) -> bool:
    """One Server batch: purchase + supplier(s) + affected medicines."""
    writes: list[tuple[str, str, dict]] = []
    purchase_payload = build_purchase_payload(conn, int(purchase_id))
    if not purchase_payload:
        return False
    writes.append(('purchases', str(purchase_id), purchase_payload))
    supplier_ids: list[int] = []
    seen_sup: set[int] = set()
    supplier_id = purchase_payload.get('supplier_id')
    if supplier_id:
        try:
            i = int(supplier_id)
            if i > 0:
                supplier_ids.append(i)
                seen_sup.add(i)
        except (TypeError, ValueError):
            pass
    for sid in extra_supplier_ids or []:
        try:
            i = int(sid)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen_sup:
            seen_sup.add(i)
            supplier_ids.append(i)
    for sid in supplier_ids:
        sp = build_supplier_payload(conn, sid)
        if sp:
            writes.append(('suppliers', str(sid), sp))
    cur = conn.cursor()
    cur.execute(
        'SELECT DISTINCT medicine_id FROM purchase_items WHERE purchase_id=?',
        (purchase_id,),
    )
    for (mid,) in cur.fetchall():
        mp = build_medicine_payload(conn, int(mid))
        if mp:
            writes.append(('medicines', str(mid), mp))
    return push_docs_batch(writes, timeout=timeout)


def push_sale(conn, sale_id: int) -> bool:
    payload = build_sale_payload(conn, sale_id)
    if not payload:
        return False
    return push_doc('sales', str(sale_id), payload) is not None


def push_medicine(conn, medicine_id: int, *, commit_synced_at: bool = True) -> bool:
    def _load_row():
        with _db_lock:
            cur = conn.cursor()
            cur.execute('SELECT * FROM medicines WHERE id=?', (medicine_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    m = _db_retry(_load_row)
    if not m:
        return False
    payload = {
        'name': m['name'],
        'type': m.get('type'),
        'stock_qty': m.get('stock_qty', 0),
        'unit': m.get('unit'),
        'gst_percent': m.get('gst_percent'),
        'mrp': m.get('mrp'),
        'rate': m.get('rate'),
        'manufacturer': m.get('manufacturer'),
        'batch_no': m.get('batch_no'),
        'expiry_date': m.get('expiry_date'),
        'hsn_code': m.get('hsn_code'),
        'schedule': m.get('schedule'),
        'location': m.get('location'),
        'content_drug': m.get('content_drug'),
        'is_hidden': bool(int(m.get('is_hidden') or 0)),
    }
    payload.update(_sync_meta_fields_from_row(m))
    synced_at = push_doc('medicines', str(medicine_id), payload)
    if synced_at and commit_synced_at:
        def _mark_synced():
            with _db_lock:
                cur = conn.cursor()
                cur.execute(
                    'UPDATE medicines SET synced_at=? WHERE id=?',
                    (synced_at, medicine_id),
                )
                conn.commit()

        _db_retry(_mark_synced)
    elif synced_at:
        try:
            cur = conn.cursor()
            cur.execute(
                'UPDATE medicines SET synced_at=? WHERE id=?',
                (synced_at, medicine_id),
            )
        except Exception:
            pass
    return synced_at is not None


def _schedule_medicine_repush(conn, medicine_id: int) -> None:
    """Do not re-upload local medicine stock over the server copy."""
    log.debug('skip medicine re-push %s (server is source of truth)', medicine_id)


def push_supplier(conn, supplier_id: int) -> bool:
    cur = conn.cursor()
    cur.execute('SELECT * FROM suppliers WHERE id=?', (supplier_id,))
    row = cur.fetchone()
    if not row:
        return False
    cols = [d[0] for d in cur.description]
    s = dict(zip(cols, row))
    payload = {
        'name': s['name'],
        'phone': s.get('phone'),
        'address': s.get('address'),
        'gstin': s.get('gstin'),
        'total_due': s.get('total_due', 0),
        'total_credit': s.get('total_credit', 0),
    }
    payload.update(_sync_meta_fields_from_row(s))
    return push_doc('suppliers', str(supplier_id), payload) is not None


def build_doctor_payload(conn, doctor_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM doctors WHERE id=?', (doctor_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    d = dict(zip(cols, row))
    payload = {
        'name': d['name'],
        'phone': d.get('phone'),
        'registration_number': d.get('registration_number'),
    }
    payload.update(_sync_meta_fields_from_row(d))
    return payload


def push_doctor(conn, doctor_id: int) -> bool:
    payload = build_doctor_payload(conn, doctor_id)
    if not payload:
        return False
    return push_doc('doctors', str(doctor_id), payload) is not None


def build_rack_payload(conn, rack_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM racks WHERE id=?', (rack_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    r = dict(zip(cols, row))
    payload = {'name': r.get('name') or ''}
    payload.update(_sync_meta_fields_from_row(r))
    return payload


def build_section_payload(conn, section_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM sections WHERE id=?', (section_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    s = dict(zip(cols, row))
    payload = {
        'rack_id': int(s.get('rack_id') or 0),
        'name': s.get('name') or '',
    }
    payload.update(_sync_meta_fields_from_row(s))
    return payload


def build_box_payload(conn, box_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM boxes WHERE id=?', (box_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    b = dict(zip(cols, row))
    payload = {
        'section_id': int(b.get('section_id') or 0),
        'name': b.get('name') or '',
    }
    payload.update(_sync_meta_fields_from_row(b))
    return payload


def push_rack(conn, rack_id: int) -> bool:
    payload = build_rack_payload(conn, rack_id)
    if not payload:
        return False
    return push_doc('racks', str(rack_id), payload) is not None


def push_section(conn, section_id: int) -> bool:
    payload = build_section_payload(conn, section_id)
    if not payload:
        return False
    return push_doc('sections', str(section_id), payload) is not None


def push_box(conn, box_id: int) -> bool:
    payload = build_box_payload(conn, box_id)
    if not payload:
        return False
    return push_doc('boxes', str(box_id), payload) is not None


def push_customer(conn, customer_id: int) -> bool:
    payload = build_customer_payload(conn, customer_id)
    if not payload:
        return False
    return push_doc('customers', str(customer_id), payload) is not None


def push_purchase(conn, purchase_id: int) -> bool:
    payload = build_purchase_payload(conn, purchase_id)
    if not payload:
        return False
    return push_doc('purchases', str(purchase_id), payload) is not None


def build_customer_payment_payload(conn, payment_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM customer_payments WHERE id=?', (payment_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    p = dict(zip(cols, row))
    cur.execute('SELECT name FROM customers WHERE id=?', (p['customer_id'],))
    cr = cur.fetchone()
    payload = {
        'customer_id': p['customer_id'],
        'customer_name': cr[0] if cr else None,
        'payment_date': p.get('payment_date'),
        'amount': p.get('amount', 0),
        'payment_mode': p.get('payment_mode'),
        'cash_amount': p.get('cash_amount', 0),
        'online_amount': p.get('online_amount', 0),
        'reference_no': p.get('reference_no'),
        'note': p.get('note'),
        'created_at': p.get('created_at'),
    }
    payload.update(_sync_meta_fields_from_row(p))
    return payload


def push_customer_payment(conn, payment_id: int) -> bool:
    payload = build_customer_payment_payload(conn, payment_id)
    if not payload:
        return False
    return push_doc('customer_payments', str(payment_id), payload) is not None


def build_supplier_payment_payload(conn, payment_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM supplier_payments WHERE id=?', (payment_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    p = dict(zip(cols, row))
    cur.execute('SELECT name FROM suppliers WHERE id=?', (p['supplier_id'],))
    sr = cur.fetchone()
    payload = {
        'payment_no': p.get('payment_no'),
        'supplier_id': p['supplier_id'],
        'supplier_name': sr[0] if sr else None,
        'payment_date': p.get('payment_date'),
        'amount': p.get('amount', 0),
        'mode': p.get('mode'),
        'reference': p.get('reference'),
        'due_before': p.get('due_before', 0),
        'due_after': p.get('due_after', 0),
        'created_at': p.get('created_at'),
    }
    payload.update(_sync_meta_fields_from_row(p))
    return payload


def push_supplier_payment(conn, payment_id: int) -> bool:
    payload = build_supplier_payment_payload(conn, payment_id)
    if not payload:
        return False
    return push_doc('supplier_payments', str(payment_id), payload) is not None


def build_sales_return_payload(conn, return_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM sales_returns WHERE id=?', (return_id,))
    row = cur.fetchone()
    if not row:
        return None
    r = _row_dict(cur, row)
    cur.execute('SELECT bill_no FROM sales WHERE id=?', (r['sale_id'],))
    bill_row = cur.fetchone()
    cur.execute('SELECT name FROM customers WHERE id=?', (r['customer_id'],))
    cust_row = cur.fetchone()
    cur.execute('SELECT * FROM sales_return_items WHERE return_id=?', (return_id,))
    # Every line read BEFORE the medicine lookups: those reuse no cursor of ours.
    # _row_dict reads cur.description, and the lookup below used to run on this same
    # cursor -- so from the second line on each line was read with the medicine
    # query's two column names and went up as medicine 0, qty 0 (SR113 / SR114,
    # Vaibhav, 4 Oct 2026). A return of one medicine was never affected.
    lines = [_row_dict(cur, ir) for ir in cur.fetchall()]
    mcur = conn.cursor()
    items = []
    for item in lines:
        mid = _line_medicine_id(item)
        mr = None
        if mid:
            mcur.execute('SELECT name, batch_no FROM medicines WHERE id=?', (mid,))
            mr = mcur.fetchone()
        items.append({
            'medicine_id': mid,
            'name': mr[0] if mr else item.get('name'),
            'batch': mr[1] if mr else item.get('batch'),
            'qty': item.get('qty', 0),
            'rate': item.get('rate', 0),
            'amount': item.get('amount', 0),
        })
    payload = {
        'return_no': r.get('return_no'),
        'sale_id': r.get('sale_id'),
        'bill_no': bill_row[0] if bill_row else None,
        'customer_id': r.get('customer_id'),
        'customer_name': cust_row[0] if cust_row else None,
        'return_date': r.get('return_date'),
        'refund_amount': r.get('refund_amount', 0),
        'discount': r.get('discount', 0),
        'reason': r.get('reason'),
        'created_at': r.get('created_at'),
        'item_count': len(items),
        'items': items,
    }
    payload.update(_sync_meta_fields_from_row(r))
    return payload


def push_sales_return(conn, return_id: int) -> bool:
    payload = build_sales_return_payload(conn, return_id)
    if not payload:
        return False
    return push_doc('sales_returns', str(return_id), payload) is not None


def build_purchase_return_payload(conn, return_id: int) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute('SELECT * FROM purchase_returns WHERE id=?', (return_id,))
    row = cur.fetchone()
    if not row:
        return None
    r = _row_dict(cur, row)
    cur.execute('SELECT purchase_no FROM purchases WHERE id=?', (r['purchase_id'],))
    pr = cur.fetchone()
    cur.execute('SELECT name FROM suppliers WHERE id=?', (r['supplier_id'],))
    sr = cur.fetchone()
    cur.execute('SELECT * FROM purchase_return_items WHERE return_id=?', (return_id,))
    # Every line read BEFORE the medicine lookups: those reuse no cursor of ours.
    # _row_dict reads cur.description, and the lookup below used to run on this same
    # cursor -- so from the second line on each line was read with the medicine
    # query's two column names and went up as medicine 0, qty 0 (SR113 / SR114,
    # Vaibhav, 4 Oct 2026). A return of one medicine was never affected.
    lines = [_row_dict(cur, ir) for ir in cur.fetchall()]
    mcur = conn.cursor()
    items = []
    for item in lines:
        mid = _line_medicine_id(item)
        mr = None
        if mid:
            mcur.execute('SELECT name, batch_no FROM medicines WHERE id=?', (mid,))
            mr = mcur.fetchone()
        items.append({
            'medicine_id': mid,
            'name': mr[0] if mr else item.get('name'),
            'batch': mr[1] if mr else item.get('batch'),
            'qty': item.get('qty', 0),
            'rate': item.get('rate', 0),
            'amount': item.get('amount', 0),
        })
    payload = {
        'return_no': r.get('return_no'),
        'purchase_id': r.get('purchase_id'),
        'purchase_no': pr[0] if pr else None,
        'supplier_id': r.get('supplier_id'),
        'supplier_name': sr[0] if sr else None,
        'return_date': r.get('return_date'),
        'refund_amount': r.get('refund_amount', 0),
        'discount': r.get('discount', 0),
        'reason': r.get('reason'),
        'created_at': r.get('created_at'),
        'item_count': len(items),
        'items': items,
    }
    payload.update(_sync_meta_fields_from_row(r))
    return payload


def push_purchase_return(conn, return_id: int) -> bool:
    payload = build_purchase_return_payload(conn, return_id)
    if not payload:
        return False
    return push_doc('purchase_returns', str(return_id), payload) is not None


def push_pharmacy_profile(conn) -> None:
    cur = conn.cursor()
    cur.execute('SELECT * FROM pharmacy_profile LIMIT 1')
    row = cur.fetchone()
    if not row:
        return
    cols = [d[0] for d in cur.description]
    p = dict(zip(cols, row))
    push_settings_doc('pharmacy_profile', {
        'name': p.get('name'),
        'address': p.get('address'),
        'phone': p.get('phone'),
        'email': p.get('email'),
        'gstin': p.get('gstin'),
        'dl_number': p.get('dl_number'),
    })


def push_dropdowns(conn) -> None:
    """Push villages, doctors, suppliers lists for Android dropdown sync."""
    from core.layout_config import (
        _DEFAULT_MED_TYPES,
        _DEFAULT_SCHEDULES,
        get_configured_schedules,
        get_med_types,
    )
    from core.village_service import load_villages, get_default_village
    med_types = list(get_med_types() or [])
    schedules = list(get_configured_schedules() or [])
    if not med_types:
        med_types = list(_DEFAULT_MED_TYPES)
    if not schedules:
        schedules = [s for s in _DEFAULT_SCHEDULES if s]
    cur = conn.cursor()
    cur.execute('SELECT id, name, phone, registration_number FROM doctors ORDER BY name')
    doctors = [
        {
            'id': r[0],
            'name': r[1],
            'phone': r[2],
            'reg_no': r[3],
        }
        for r in cur.fetchall()
    ]
    cur.execute(
        'SELECT id, name, phone, gstin, address, total_due, total_credit '
        'FROM suppliers ORDER BY name'
    )
    suppliers = [
        {
            'id': r[0],
            'name': r[1],
            'phone': r[2],
            'gstin': r[3],
            'address': r[4],
            'total_due': r[5],
            'total_credit': r[6],
        }
        for r in cur.fetchall()
    ]
    push_settings_doc('dropdowns', {
        'villages': load_villages(conn),
        'default_village': get_default_village(conn),
        'doctors': doctors,
        'suppliers': suppliers,
        'med_types': med_types,
        'schedules': schedules,
    })


# ── Pull into SQLite ──────────────────────────────────────────────────────────


def _sync_meta_fields_from_row(row: dict) -> dict:
    """Attach version / updated_at / device_id / deleted from a local SQLite row."""
    from core.conflict_resolver import meta_from_mapping, sync_meta_to_payload
    return sync_meta_to_payload(meta_from_mapping(row))


def _table_columns(conn, table: str) -> set[str]:
    cur = conn.cursor()
    try:
        cur.execute(f'PRAGMA table_info({table})')
        return {str(r[1]) for r in cur.fetchall()}
    except Exception:
        return set()


def _load_local_sync_meta(conn, collection: str, doc_id: str):
    """Return SyncMeta for a local row, or None if missing."""
    from core.conflict_resolver import COLLECTION_TO_TABLE, meta_from_mapping
    table = COLLECTION_TO_TABLE.get(collection)
    if not table:
        return None
    try:
        rid = int(doc_id)
    except (TypeError, ValueError):
        return None
    cols = _table_columns(conn, table)
    if not cols:
        return None
    prefer = [c for c in (
        'version', 'updated_at', 'synced_at', 'last_updated', 'created_at',
        'device_id', 'deleted',
    ) if c in cols]
    if not prefer:
        # Row may still exist without meta columns (pre-migration).
        cur = conn.cursor()
        try:
            cur.execute(f'SELECT id FROM {table} WHERE id=?', (rid,))
            return meta_from_mapping({}) if cur.fetchone() else None
        except Exception:
            return None
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT {', '.join(prefer)} FROM {table} WHERE id=?",
            (rid,),
        )
        row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    return meta_from_mapping(dict(zip(prefer, row)))


def _apply_sync_meta_columns(cur, table: str, row_id: int, data: dict) -> None:
    """Write conflict-metadata columns after a successful cloud apply."""
    from core.conflict_resolver import meta_from_mapping, sync_meta_to_payload
    cols = set()
    try:
        cur.execute(f'PRAGMA table_info({table})')
        cols = {str(r[1]) for r in cur.fetchall()}
    except Exception:
        return
    payload = sync_meta_to_payload(meta_from_mapping(data))
    sets = []
    vals = []
    if 'version' in cols:
        sets.append('version=?')
        vals.append(int(payload.get('version') or 1))
    if 'updated_at' in cols and payload.get('updated_at'):
        sets.append('updated_at=?')
        vals.append(payload['updated_at'])
    if 'device_id' in cols:
        sets.append('device_id=?')
        vals.append(payload.get('device_id') or '')
    if 'deleted' in cols:
        sets.append('deleted=?')
        vals.append(1 if payload.get('deleted') else 0)
    if 'sync_status' in cols:
        sets.append('sync_status=?')
        vals.append('synced')
    if not sets:
        return
    vals.append(int(row_id))
    cur.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", vals)


def _sale_stock_affects(data: Optional[dict] = None, *, local_is_autosave: int = 0) -> bool:
    """Autosave drafts never touch stock (local or synced)."""
    if int(local_is_autosave or 0):
        return False
    if data and int(data.get('is_autosave') or 0):
        return False
    return True


def _bump_medicine_stock(cur, medicine_id: int, delta: float) -> None:
    """Adjust stock only. Visibility (is_hidden) is handled separately so
    Remove Out of Stock / purchase-return hides are not undone by sync."""
    mid = int(medicine_id or 0)
    if mid <= 0:
        return
    try:
        d = float(delta or 0)
    except (TypeError, ValueError):
        return
    if d == 0:
        return
    if d > 0:
        cur.execute(
            'UPDATE medicines SET stock_qty=stock_qty+? WHERE id=?',
            (d, mid),
        )
    else:
        cur.execute(
            'UPDATE medicines SET stock_qty=MAX(0, stock_qty+?) WHERE id=?',
            (d, mid),
        )


def _unhide_medicines_in_stock(cur, medicine_ids) -> None:
    """Show again only when stock is actually back (purchase / undo sale)."""
    ids = sorted({int(x) for x in (medicine_ids or []) if x})
    if not ids:
        return
    placeholders = ','.join('?' * len(ids))
    cur.execute(
        f'UPDATE medicines SET is_hidden=0 '
        f'WHERE id IN ({placeholders}) AND COALESCE(stock_qty,0) > 0 '
        f'AND COALESCE(is_hidden,0)=1',
        ids,
    )


def _hide_medicines_at_zero(cur, medicine_ids) -> None:
    """Match Remove Out of Stock / purchase-return: hide zero batches."""
    ids = sorted({int(x) for x in (medicine_ids or []) if x})
    if not ids:
        return
    placeholders = ','.join('?' * len(ids))
    cur.execute(
        f'UPDATE medicines SET is_hidden=1 '
        f'WHERE id IN ({placeholders}) AND COALESCE(stock_qty,0) <= 0 '
        f'AND COALESCE(is_hidden,0)=0',
        ids,
    )


def _sale_item_medicine_ids(cur, sale_id: int) -> list:
    cur.execute(
        'SELECT DISTINCT medicine_id FROM sales_items WHERE sale_id=?',
        (sale_id,),
    )
    return [int(r[0]) for r in cur.fetchall() if r and r[0]]


def _restore_sale_items_stock(cur, sale_id: int) -> None:
    cur.execute(
        'SELECT medicine_id, qty FROM sales_items WHERE sale_id=?',
        (sale_id,),
    )
    for med_id, qty in cur.fetchall():
        _bump_medicine_stock(cur, med_id, abs(float(qty or 0)))


def _deduct_sale_items_stock(cur, sale_id: int) -> None:
    cur.execute(
        'SELECT medicine_id, qty FROM sales_items WHERE sale_id=?',
        (sale_id,),
    )
    for med_id, qty in cur.fetchall():
        _bump_medicine_stock(cur, med_id, -abs(float(qty or 0)))


def _purchase_return_stock_units(cur, medicine_id: int, qty) -> float:
    """purchase_return_items.qty is strips for strip types; stock is tablets."""
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    q = abs(float(qty or 0))
    cur.execute(
        'SELECT type, COALESCE(unit, \'1\') FROM medicines WHERE id=?',
        (int(medicine_id or 0),),
    )
    row = cur.fetchone()
    if not row:
        return q
    mtype, unit = row[0], row[1]
    if is_strip_count_type(mtype or ''):
        tps = parse_tablets_per_stripe(unit or '1') or 1
        return q * tps
    return q


def _restore_sales_return_stock(cur, return_id: int) -> None:
    """Undo a sales return: remove previously restored stock."""
    cur.execute(
        'SELECT medicine_id, qty FROM sales_return_items WHERE return_id=?',
        (return_id,),
    )
    for med_id, qty in cur.fetchall():
        _bump_medicine_stock(cur, med_id, -abs(float(qty or 0)))


def _apply_sales_return_stock(cur, return_id: int) -> None:
    cur.execute(
        'SELECT medicine_id, qty FROM sales_return_items WHERE return_id=?',
        (return_id,),
    )
    for med_id, qty in cur.fetchall():
        _bump_medicine_stock(cur, med_id, abs(float(qty or 0)))


def _restore_purchase_return_stock(cur, return_id: int) -> None:
    """Undo a purchase return: put stock back."""
    cur.execute(
        'SELECT medicine_id, qty FROM purchase_return_items WHERE return_id=?',
        (return_id,),
    )
    for med_id, qty in cur.fetchall():
        units = _purchase_return_stock_units(cur, med_id, qty)
        _bump_medicine_stock(cur, med_id, units)


def _apply_purchase_return_stock(cur, return_id: int) -> None:
    cur.execute(
        'SELECT medicine_id, qty FROM purchase_return_items WHERE return_id=?',
        (return_id,),
    )
    for med_id, qty in cur.fetchall():
        units = _purchase_return_stock_units(cur, med_id, qty)
        _bump_medicine_stock(cur, med_id, -units)


def _soft_delete_local(conn, collection: str, doc_id: str, data: dict) -> None:
    """Apply remote delete. Sales/purchases are permanently purged; others soft-delete."""
    from core.conflict_resolver import COLLECTION_TO_TABLE
    table = COLLECTION_TO_TABLE.get(collection)
    if not table:
        return
    try:
        rid = int(doc_id)
    except (TypeError, ValueError):
        return
    with _db_lock:
        cur = conn.cursor()
        try:
            cols = _table_columns(conn, table)
            already = False
            exists_row = None
            if 'deleted' in cols:
                cur.execute(f'SELECT COALESCE(deleted,0) FROM {table} WHERE id=?', (rid,))
                exists_row = cur.fetchone()
                if exists_row is None and collection in ('sales', 'purchases'):
                    return
                already = bool(exists_row and int(exists_row[0] or 0))

            if collection == 'sales':
                cur.execute('SELECT 1 FROM sales WHERE id=?', (rid,))
                if not cur.fetchone():
                    return
                # Peer inbound delete: stock already restored by deleting device + medicine push.
                cur.execute('DELETE FROM sales_items WHERE sale_id=?', (rid,))
                cur.execute('DELETE FROM sales WHERE id=?', (rid,))
                conn.commit()
                return

            if collection == 'purchases':
                cur.execute('SELECT 1 FROM purchases WHERE id=?', (rid,))
                if not cur.fetchone():
                    return
                # Peer inbound delete: stock reverse already done by deleter + medicine push.
                cur.execute('DELETE FROM purchase_items WHERE purchase_id=?', (rid,))
                cur.execute('DELETE FROM purchases WHERE id=?', (rid,))
                conn.commit()
                return

            if collection == 'racks':
                cur.execute(
                    "DELETE FROM boxes WHERE section_id IN "
                    "(SELECT id FROM sections WHERE rack_id=?)",
                    (rid,),
                )
                cur.execute("DELETE FROM sections WHERE rack_id=?", (rid,))
                cur.execute("DELETE FROM racks WHERE id=?", (rid,))
                conn.commit()
                return
            if collection == 'sections':
                cur.execute("DELETE FROM boxes WHERE section_id=?", (rid,))
                cur.execute("DELETE FROM sections WHERE id=?", (rid,))
                conn.commit()
                return
            if collection == 'boxes':
                cur.execute("DELETE FROM boxes WHERE id=?", (rid,))
                conn.commit()
                return

            if not already and collection == 'sales_returns':
                cur.execute(
                    'SELECT DISTINCT medicine_id FROM sales_return_items WHERE return_id=?',
                    (rid,),
                )
                med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                _restore_sales_return_stock(cur, rid)
                _hide_medicines_at_zero(cur, med_ids)
            elif not already and collection == 'purchase_returns':
                cur.execute(
                    'SELECT DISTINCT medicine_id FROM purchase_return_items WHERE return_id=?',
                    (rid,),
                )
                med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                _restore_purchase_return_stock(cur, rid)
                _unhide_medicines_in_stock(cur, med_ids)
            if 'deleted' in cols:
                cur.execute(f'UPDATE {table} SET deleted=1 WHERE id=?', (rid,))
            _apply_sync_meta_columns(cur, table, rid, {**data, 'deleted': True})
            conn.commit()
        except Exception as exc:
            log.warning('soft_delete_local %s/%s: %s', collection, doc_id, exc)
            conn.rollback()


def _schedule_entity_repush(conn, collection: str, doc_id: str) -> None:
    """Online poller/pull must never upload local copies.

    Server is the source of truth. Uploads happen only on explicit save / edit /
    delete (online mutation queue). KEEP_LOCAL re-push from a second PC was
    reverting purchases/sales/customers after the first PC saved.
    """
    log.debug(
        'skip entity re-push %s/%s (server is source of truth)',
        collection, doc_id,
    )


def _resolve_pull_decision(conn, collection: str, doc_id: str, data: dict):
    """ConflictResolver gate used by sync_down_doc (bootstrap + listeners)."""
    from core.conflict_resolver import (
        ConflictDecision,
        meta_from_mapping,
        resolve,
        should_resolve,
    )
    if not should_resolve(collection):
        return ConflictDecision.APPLY_REMOTE
    local = _load_local_sync_meta(conn, collection, doc_id)
    remote = meta_from_mapping(data)
    return resolve(local, remote, collection=collection, doc_id=str(doc_id))


def sync_down_doc(conn, collection: str, doc_id: str, data: dict) -> str:
    """
    Apply one cloud document locally.
    Returns: 'applied' | 'kept_local' | 'skipped' | 'soft_deleted' | 'ignored'
             | 'skipped_offline'
    No-op when sync mode is Offline (local DB is source of truth).
    """
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return 'skipped_offline'
    except Exception:
        return 'skipped_offline'

    from core.conflict_resolver import ConflictDecision, COLLECTION_TO_TABLE

    # Serialize all SQLite use — UI also uses this connection.
    with _db_lock:
        decision = _resolve_pull_decision(conn, collection, doc_id, data)
        if decision == ConflictDecision.SKIP:
            return 'skipped'
        if decision == ConflictDecision.KEEP_LOCAL:
            pending = False
            try:
                from core.online_mutation_queue import pending_by_local_id
                pending = bool(pending_by_local_id(collection, int(doc_id)))
            except Exception:
                pending = False
            if pending:
                # This device has an in-flight save — keep it; do not re-push the
                # stale poller copy. The mutation queue is the only upload path.
                return 'kept_local'
            # Other PC / leftover local row is newer than an older watermark but
            # the server document is authoritative. Apply remote; never upload.
            decision = ConflictDecision.APPLY_REMOTE
        if decision == ConflictDecision.APPLY_SOFT_DELETE:
            _soft_delete_local(conn, collection, doc_id, data)
            return 'soft_deleted'

        cur = conn.cursor()
        try:
            if collection == 'customers':
                cur.execute('''
                    INSERT OR REPLACE INTO customers
                    (id, name, phone, address, total_due, total_credit, created_at)
                    VALUES (?,?,?,?,?,?,?)
                ''', (
                    int(doc_id),
                    data.get('name', ''),
                    data.get('phone'),
                    data.get('address'),
                    float(data.get('total_due') or 0),
                    float(data.get('total_credit') or 0),
                    data.get('created_at'),
                ))
            elif collection == 'medicines':
                from core.medicine_sync_merge import merge_medicine_pull
                from core.conflict_resolver import meta_from_mapping

                med_id = int(doc_id)
                med_cols = _table_columns(conn, 'medicines')
                select_cols = ['stock_qty', 'COALESCE(is_hidden, 0)', 'synced_at']
                meta_keys = []
                for key in ('version', 'updated_at', 'device_id', 'deleted'):
                    if key in med_cols:
                        select_cols.append(key)
                        meta_keys.append(key)
                cur.execute(
                    f"SELECT {', '.join(select_cols)} FROM medicines WHERE id=?",
                    (med_id,),
                )
                row = cur.fetchone()
                local_meta = None
                if row is not None and meta_keys:
                    meta_map = {}
                    # stock, hidden, synced_at occupy 0..2
                    for i, key in enumerate(meta_keys):
                        meta_map[key] = row[3 + i]
                    if 'synced_at' not in meta_map:
                        meta_map['synced_at'] = row[2]
                    local_meta = meta_from_mapping(meta_map)
                elif row is not None:
                    local_meta = meta_from_mapping({'synced_at': row[2]})
                merged = merge_medicine_pull(
                    local_stock=int(row[0]) if row else None,
                    local_hidden=int(row[1]) if row else None,
                    local_synced_at=row[2] if row else None,
                    remote_data=data,
                    local_meta=local_meta,
                )
                if merged.decision == ConflictDecision.SKIP:
                    return 'skipped'
                # Server is source of truth — apply remote medicine, never re-push.
                remote_stock = int(data.get('stock_qty') or 0)
                remote_hidden = 1 if data.get('is_hidden') else 0
                cur.execute('''
                        INSERT INTO medicines
                        (id, name, type, stock_qty, unit, gst_percent, mrp, rate,
                         manufacturer, batch_no, expiry_date, hsn_code, schedule, location,
                         content_drug, is_hidden, synced_at, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                COALESCE(?, CURRENT_TIMESTAMP))
                        ON CONFLICT(id) DO UPDATE SET
                            -- When the batch came in decides which back-dated bills may
                            -- use it: keep the earliest known, never the pull time.
                            created_at=CASE
                                WHEN excluded.created_at IS NOT NULL
                                 AND (medicines.created_at IS NULL
                                      OR TRIM(CAST(medicines.created_at AS TEXT)) = ''
                                      OR excluded.created_at < medicines.created_at)
                                THEN excluded.created_at ELSE medicines.created_at END,
                            name=excluded.name,
                            type=excluded.type,
                            stock_qty=excluded.stock_qty,
                            unit=excluded.unit,
                            gst_percent=excluded.gst_percent,
                            mrp=excluded.mrp,
                            rate=excluded.rate,
                            manufacturer=excluded.manufacturer,
                            batch_no=excluded.batch_no,
                            expiry_date=excluded.expiry_date,
                            hsn_code=excluded.hsn_code,
                            schedule=excluded.schedule,
                            location=excluded.location,
                            content_drug=excluded.content_drug,
                            is_hidden=excluded.is_hidden,
                            synced_at=excluded.synced_at
                    ''', (
                        med_id,
                        data.get('name', ''),
                        data.get('type'),
                        remote_stock,
                        data.get('unit'),
                        data.get('gst_percent'),
                        data.get('mrp'),
                        data.get('rate'),
                        data.get('manufacturer'),
                        data.get('batch_no'),
                        data.get('expiry_date'),
                        data.get('hsn_code'),
                        data.get('schedule'),
                        data.get('location'),
                        data.get('content_drug'),
                        remote_hidden,
                        data.get('synced_at') or data.get('updated_at'),
                        data.get('created_at') or None,
                    ))
                conn.commit()
            elif collection == 'sales':
                sale_id = int(doc_id)
                customer_id = int(data.get('customer_id') or 0)
                if customer_id > 0:
                    cur.execute('''
                        INSERT OR IGNORE INTO customers
                        (id, name, phone, address, created_at)
                        VALUES (?,?,?,?,?)
                    ''', (
                        customer_id,
                        data.get('customer_name') or f'Customer #{customer_id}',
                        data.get('customer_phone'),
                        data.get('customer_address'),
                        data.get('created_at'),
                    ))
                local_auto = 0
                try:
                    cur.execute(
                        'SELECT COALESCE(is_autosave,0) FROM sales WHERE id=?',
                        (sale_id,),
                    )
                    row_a = cur.fetchone()
                    local_auto = int(row_a[0] or 0) if row_a else 0
                except Exception:
                    local_auto = 0
                if _sale_stock_affects(local_is_autosave=local_auto):
                    _restore_sale_items_stock(cur, sale_id)
                cur.execute('DELETE FROM sales_items WHERE sale_id=?', (sale_id,))
                sales_cols = _table_columns(conn, 'sales')
                has_cust_denorm = 'customer_name' in sales_cols
                if has_cust_denorm:
                    cur.execute('''
                        INSERT OR REPLACE INTO sales
                        (id, bill_no, customer_id, bill_date, total_amount, discount, discount_pct,
                         rounding, amount_paid, cash_paid, online_paid, previous_due, previous_credit,
                         due_amount, credit_amount, total_due, paid_due, bill_cleared, account_cleared,
                         doctor_name, created_at, customer_name, customer_phone, customer_address)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ''', (
                        sale_id,
                        data.get('bill_no', ''),
                        customer_id,
                        _date_only(data.get('bill_date')),
                        float(data.get('total_amount') or 0),
                        float(data.get('discount') or 0),
                        float(data.get('discount_pct') or 0),
                        float(data.get('rounding') or 0),
                        float(data.get('amount_paid') or 0),
                        float(data.get('cash_paid') or 0),
                        float(data.get('online_paid') or 0),
                        float(data.get('previous_due') or 0),
                        float(data.get('previous_credit') or 0),
                        float(data.get('due_amount') or 0),
                        float(data.get('credit_amount') or 0),
                        float(data.get('total_due') or 0),
                        float(data.get('paid_due') or 0),
                        1 if data.get('bill_cleared') else 0,
                        1 if data.get('account_cleared') else 0,
                        data.get('doctor_name'),
                        data.get('created_at'),
                        data.get('customer_name') or None,
                        data.get('customer_phone') or None,
                        data.get('customer_address') or None,
                    ))
                else:
                    cur.execute('''
                        INSERT OR REPLACE INTO sales
                        (id, bill_no, customer_id, bill_date, total_amount, discount, discount_pct,
                         rounding, amount_paid, cash_paid, online_paid, previous_due, previous_credit,
                         due_amount, credit_amount, total_due, paid_due, bill_cleared, account_cleared,
                         doctor_name, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ''', (
                        sale_id,
                        data.get('bill_no', ''),
                        customer_id,
                        _date_only(data.get('bill_date')),
                        float(data.get('total_amount') or 0),
                        float(data.get('discount') or 0),
                        float(data.get('discount_pct') or 0),
                        float(data.get('rounding') or 0),
                        float(data.get('amount_paid') or 0),
                        float(data.get('cash_paid') or 0),
                        float(data.get('online_paid') or 0),
                        float(data.get('previous_due') or 0),
                        float(data.get('previous_credit') or 0),
                        float(data.get('due_amount') or 0),
                        float(data.get('credit_amount') or 0),
                        float(data.get('total_due') or 0),
                        float(data.get('paid_due') or 0),
                        1 if data.get('bill_cleared') else 0,
                        1 if data.get('account_cleared') else 0,
                        data.get('doctor_name'),
                        data.get('created_at'),
                    ))
                from core.fy_serial import stamp_pulled_fy_fields
                stamp_pulled_fy_fields(cur, 'sales', sale_id, data.get('bill_no'), data.get('bill_date'),
                                       fy_start_year=data.get('fy_start_year'),
                                       fy_serial=data.get('fy_serial'))
                sale_med_ids = []
                for item in data.get('items') or []:
                    medicine_id = int(_line_medicine_id(item))
                    if medicine_id > 0:
                        sale_med_ids.append(medicine_id)
                        cur.execute('''
                            INSERT OR IGNORE INTO medicines
                            (id, name, type, batch_no, expiry_date, hsn_code, schedule, manufacturer, gst_percent)
                            VALUES (?,?,?,?,?,?,?,?,?)
                        ''', (
                            medicine_id,
                            item.get('name') or f'Medicine #{medicine_id}',
                            item.get('type'),
                            item.get('batch_no'),
                            item.get('expiry_date'),
                            item.get('hsn_code'),
                            item.get('schedule'),
                            item.get('manufacturer'),
                            item.get('gst_percent'),
                        ))
                    cur.execute('''
                        INSERT INTO sales_items
                        (sale_id, medicine_id, qty, rate, gst_percent, amount, item_discount, cost_price)
                        VALUES (?,?,?,?,?,?,?,?)
                    ''', (
                        sale_id,
                        medicine_id,
                        int(item.get('qty') or 0),
                        float(item.get('rate') or 0),
                        item.get('gst_percent'),
                        float(item.get('amount') or 0),
                        float(item.get('item_discount') or 0),
                        float(item.get('cost_price') or 0),
                    ))
                if _sale_stock_affects(data):
                    _deduct_sale_items_stock(cur, sale_id)
                    # Sales do not auto-hide locally; leave is_hidden as user set it.
            elif collection == 'purchases':
                purchase_id = int(doc_id)
                supplier_id = int(data.get('supplier_id') or 0)
                if supplier_id > 0:
                    cur.execute('''
                        INSERT OR IGNORE INTO suppliers
                        (id, name, phone, created_at)
                        VALUES (?,?,?,?)
                    ''', (
                        supplier_id,
                        data.get('supplier_name') or f'Supplier #{supplier_id}',
                        data.get('supplier_phone'),
                        data.get('created_at'),
                    ))
                try:
                    from core.purchase_service import _reverse_stock_for_purchase
                    cur.execute(
                        'SELECT DISTINCT medicine_id FROM purchase_items WHERE purchase_id=?',
                        (purchase_id,),
                    )
                    old_med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                    _reverse_stock_for_purchase(cur, purchase_id)
                    _hide_medicines_at_zero(cur, old_med_ids)
                except Exception as exc:
                    log.warning('sync purchase reverse stock %s: %s', purchase_id, exc)
                cur.execute('DELETE FROM purchase_items WHERE purchase_id=?', (purchase_id,))
                purch_cols = _table_columns(conn, 'purchases')
                base_cols = [
                    'id', 'purchase_no', 'supplier_id', 'purchase_date', 'bill_number',
                    'subtotal', 'total_gst', 'cgst', 'sgst', 'total_amount', 'final_amount',
                    'amount_paid', 'amount_paid_at_entry', 'cash_paid_at_entry',
                    'online_paid_at_entry',
                ]
                extra_map = {
                    'overall_discount': float(data.get('overall_discount') or 0),
                    'rounding': float(data.get('rounding') or 0),
                    'need_to_pay': float(
                        data.get('need_to_pay')
                        or data.get('final_amount')
                        or data.get('total_amount')
                        or 0
                    ),
                    'previous_due': float(data.get('previous_due') or 0),
                    'previous_credit': float(data.get('previous_credit') or 0),
                    'due': float(data.get('due') or data.get('due_amount') or 0),
                    'due_amount': float(data.get('due_amount') or data.get('due') or 0),
                    'current_credit': float(data.get('current_credit') or 0),
                    'credit_amount': float(data.get('credit_amount') or data.get('current_credit') or 0),
                    'total_due': float(data.get('total_due') or 0),
                    'paid_due': float(data.get('paid_due') or 0),
                    'bill_cleared': 1 if data.get('bill_cleared') else 0,
                    'account_cleared': 1 if data.get('account_cleared') else 0,
                    'expenditure': float(data.get('expenditure') or 0),
                    'fy_start_year': data.get('fy_start_year'),
                    'fy_serial': data.get('fy_serial'),
                    'created_at': data.get('created_at'),
                }
                insert_cols = [c for c in base_cols]
                insert_vals = [
                    purchase_id,
                    data.get('purchase_no', ''),
                    supplier_id,
                    _date_only(data.get('purchase_date')),
                    data.get('bill_number'),
                    float(data.get('subtotal') or 0),
                    float(data.get('total_gst') or 0),
                    float(data.get('cgst') or 0),
                    float(data.get('sgst') or 0),
                    float(data.get('total_amount') or 0),
                    float(data.get('final_amount') or 0),
                    float(data.get('amount_paid') or 0),
                    float(data.get('amount_paid_at_entry') or 0),
                    float(data.get('cash_paid_at_entry')
                          or data.get('amount_paid_at_entry')
                          or data.get('amount_paid') or 0),
                    float(data.get('online_paid_at_entry') or 0),
                ]
                for col, val in extra_map.items():
                    if col in purch_cols:
                        insert_cols.append(col)
                        insert_vals.append(val)
                placeholders = ','.join('?' * len(insert_cols))
                cur.execute(
                    f"INSERT OR REPLACE INTO purchases ({', '.join(insert_cols)}) "
                    f"VALUES ({placeholders})",
                    insert_vals,
                )
                for item in data.get('items') or []:
                    medicine_id = int(_line_medicine_id(item))
                    if medicine_id > 0:
                        cur.execute('''
                            INSERT OR IGNORE INTO medicines
                            (id, name, type, batch_no, expiry_date, hsn_code, schedule, manufacturer, gst_percent)
                            VALUES (?,?,?,?,?,?,?,?,?)
                        ''', (
                            medicine_id,
                            item.get('name') or f'Medicine #{medicine_id}',
                            item.get('type'),
                            item.get('batch_no'),
                            item.get('expiry_date'),
                            item.get('hsn_code'),
                            item.get('schedule'),
                            item.get('manufacturer'),
                            item.get('gst_percent'),
                        ))
                    cur.execute('''
                        INSERT INTO purchase_items
                        (purchase_id, medicine_id, qty, free_qty, type, rate, mrp, gst_pct,
                         batch_no, expiry_date, item_amount)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ''', (
                        purchase_id,
                        medicine_id,
                        float(item.get('qty') or 0),
                        float(item.get('free_qty') or 0),
                        item.get('type'),
                        float(item.get('rate') or 0),
                        float(item.get('mrp') or 0),
                        float(item.get('gst_pct') or 0),
                        item.get('batch_no'),
                        item.get('expiry_date'),
                        float(item.get('item_amount') or 0),
                    ))
                    # The line's HSN, discount and tax, which this pull used to drop: every
                    # pulled purchase line had no HSN and no taxable value, so the GST
                    # reports could not split the bill by rate (3 Oct 2026). Only the
                    # columns this store's table has, after the insert it always made.
                    extra = {k: item.get(k) for k in ('hsn_code', 'discount_pct', 'taxable', 'gst_amt')
                             if item.get(k) not in (None, '')}
                    if extra:
                        row_id = cur.lastrowid
                        have = {r[1] for r in cur.execute('PRAGMA table_info(purchase_items)').fetchall()}
                        extra = {k: v for k, v in extra.items() if k in have}
                        if extra and row_id:
                            cur.execute(
                                'UPDATE purchase_items SET ' + ', '.join(f'{k}=?' for k in extra)
                                + ' WHERE id=?', (*extra.values(), row_id))
                try:
                    from core.purchase_service import _apply_stock_for_purchase
                    _apply_stock_for_purchase(cur, purchase_id)
                except Exception as exc:
                    log.warning('sync purchase apply stock %s: %s', purchase_id, exc)
            elif collection == 'suppliers':
                cur.execute('''
                    INSERT OR REPLACE INTO suppliers
                    (id, name, phone, address, gstin, total_due, total_credit)
                    VALUES (?,?,?,?,?,?,?)
                ''', (
                    int(doc_id),
                    data.get('name', ''),
                    data.get('phone'),
                    data.get('address'),
                    data.get('gstin'),
                    float(data.get('total_due') or 0),
                    float(data.get('total_credit') or 0),
                ))
            elif collection == 'doctors':
                cur.execute('''
                    INSERT OR REPLACE INTO doctors (id, name, phone, registration_number)
                    VALUES (?,?,?,?)
                ''', (
                    int(doc_id),
                    data.get('name', ''),
                    data.get('phone'),
                    data.get('registration_number'),
                ))
            elif collection == 'racks':
                cur.execute('''
                    INSERT OR REPLACE INTO racks (id, name) VALUES (?,?)
                ''', (int(doc_id), data.get('name', '')))
            elif collection == 'sections':
                cur.execute('''
                    INSERT OR REPLACE INTO sections (id, rack_id, name) VALUES (?,?,?)
                ''', (
                    int(doc_id),
                    int(data.get('rack_id') or 0),
                    data.get('name', ''),
                ))
            elif collection == 'boxes':
                cur.execute('''
                    INSERT OR REPLACE INTO boxes (id, section_id, name) VALUES (?,?,?)
                ''', (
                    int(doc_id),
                    int(data.get('section_id') or 0),
                    data.get('name', ''),
                ))
            elif collection == 'customer_payments':
                cur.execute('''
                    INSERT OR REPLACE INTO customer_payments
                    (id, customer_id, payment_date, amount, payment_mode,
                     cash_amount, online_amount, reference_no, note, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                ''', (
                    int(doc_id),
                    int(data.get('customer_id') or 0),
                    data.get('payment_date', ''),
                    float(data.get('amount') or 0),
                    data.get('payment_mode', 'cash'),
                    float(data.get('cash_amount') or 0),
                    float(data.get('online_amount') or 0),
                    data.get('reference_no'),
                    data.get('note'),
                    data.get('created_at'),
                ))
            elif collection == 'supplier_payments':
                cur.execute('''
                    INSERT OR REPLACE INTO supplier_payments
                    (id, payment_no, supplier_id, payment_date, amount, mode,
                     reference, due_before, due_after, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                ''', (
                    int(doc_id),
                    data.get('payment_no', ''),
                    int(data.get('supplier_id') or 0),
                    data.get('payment_date', ''),
                    float(data.get('amount') or 0),
                    data.get('mode', 'Cash'),
                    data.get('reference'),
                    float(data.get('due_before') or 0),
                    float(data.get('due_after') or 0),
                    data.get('created_at'),
                ))
            elif collection == 'sales_returns':
                return_id = int(doc_id)
                cur.execute(
                    'SELECT DISTINCT medicine_id FROM sales_return_items WHERE return_id=?',
                    (return_id,),
                )
                old_med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                _restore_sales_return_stock(cur, return_id)
                _hide_medicines_at_zero(cur, old_med_ids)
                cur.execute('DELETE FROM sales_return_items WHERE return_id=?', (return_id,))
                cur.execute('''
                    INSERT OR REPLACE INTO sales_returns
                    (id, return_no, sale_id, customer_id, return_date,
                     refund_amount, discount, reason, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                ''', (
                    return_id,
                    data.get('return_no', ''),
                    int(data.get('sale_id') or 0),
                    int(data.get('customer_id') or 0),
                    data.get('return_date', ''),
                    float(data.get('refund_amount') or 0),
                    float(data.get('discount') or 0),
                    data.get('reason'),
                    data.get('created_at'),
                ))
                new_med_ids = []
                for item in data.get('items') or []:
                    mid = int(_line_medicine_id(item))
                    if mid:
                        new_med_ids.append(mid)
                    cur.execute('''
                        INSERT INTO sales_return_items
                        (return_id, medicine_id, qty, rate, amount)
                        VALUES (?,?,?,?,?)
                    ''', (
                        return_id,
                        mid,
                        int(item.get('qty') or 0),
                        float(item.get('rate') or 0),
                        float(item.get('amount') or 0),
                    ))
                _apply_sales_return_stock(cur, return_id)
                _unhide_medicines_in_stock(cur, new_med_ids)
            elif collection == 'purchase_returns':
                return_id = int(doc_id)
                cur.execute(
                    'SELECT DISTINCT medicine_id FROM purchase_return_items WHERE return_id=?',
                    (return_id,),
                )
                old_med_ids = [int(r[0]) for r in cur.fetchall() if r and r[0]]
                _restore_purchase_return_stock(cur, return_id)
                _unhide_medicines_in_stock(cur, old_med_ids)
                cur.execute('DELETE FROM purchase_return_items WHERE return_id=?', (return_id,))
                cur.execute('''
                    INSERT OR REPLACE INTO purchase_returns
                    (id, return_no, purchase_id, supplier_id, return_date,
                     refund_amount, discount, reason, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                ''', (
                    return_id,
                    data.get('return_no', ''),
                    int(data.get('purchase_id') or 0),
                    int(data.get('supplier_id') or 0),
                    data.get('return_date', ''),
                    float(data.get('refund_amount') or 0),
                    float(data.get('discount') or 0),
                    data.get('reason'),
                    data.get('created_at'),
                ))
                new_med_ids = []
                for item in data.get('items') or []:
                    mid = int(_line_medicine_id(item))
                    if mid:
                        new_med_ids.append(mid)
                    cur.execute('''
                        INSERT INTO purchase_return_items
                        (return_id, medicine_id, qty, rate, amount)
                        VALUES (?,?,?,?,?)
                    ''', (
                        return_id,
                        mid,
                        float(item.get('qty') or 0),
                        float(item.get('rate') or 0),
                        float(item.get('amount') or 0),
                    ))
                _apply_purchase_return_stock(cur, return_id)
                reason_l = str(data.get('reason') or '').lower()
                expired_return = any(
                    token in reason_l
                    for token in ('expired', 'near expiry', 'near-expiry', 'expiry')
                )
                if expired_return and new_med_ids:
                    placeholders = ','.join('?' * len(new_med_ids))
                    cur.execute(
                        f'UPDATE medicines SET is_hidden=1 '
                        f'WHERE id IN ({placeholders}) AND COALESCE(is_hidden,0)=0',
                        new_med_ids,
                    )
                else:
                    _hide_medicines_at_zero(cur, new_med_ids)
            # Persist conflict metadata when columns exist.
            table = COLLECTION_TO_TABLE.get(collection)
            if table:
                try:
                    _apply_sync_meta_columns(cur, table, int(doc_id), data)
                except Exception:
                    pass
            conn.commit()
            return 'applied'
        except Exception as exc:
            log.warning('sync_down_doc %s/%s: %s', collection, doc_id, exc)
            conn.rollback()
            return 'ignored'


def delete_down_doc(conn, collection: str, doc_id: str) -> None:
    """Remove a Server document from local SQLite (remote delete).

    Sales/Purchases (and their items) never auto-delete on cloud absence —
    push local back instead. Soft-delete only via deleted=true on an existing doc.
    """
    from core.conflict_resolver import never_auto_delete
    if never_auto_delete(collection):
        # Absence is not deletion for bills — re-push local if present.
        local = _load_local_sync_meta(conn, collection, doc_id)
        if local is not None and not local.deleted:
            log.info(
                'delete_down_doc skipped for %s/%s (never auto-delete); pushing local',
                collection, doc_id,
            )
            _schedule_entity_repush(conn, collection, doc_id)
        return
    with _db_lock:
        cur = conn.cursor()
        try:
            doc_key = str(doc_id)
            if collection == 'sales':
                sale_id = int(doc_key)
                cur.execute('DELETE FROM sales_items WHERE sale_id=?', (sale_id,))
                cur.execute('DELETE FROM sales WHERE id=?', (sale_id,))
            elif collection == 'purchases':
                purchase_id = int(doc_key)
                cur.execute('DELETE FROM purchase_items WHERE purchase_id=?', (purchase_id,))
                cur.execute('DELETE FROM purchases WHERE id=?', (purchase_id,))
            elif collection == 'sales_returns':
                return_id = int(doc_key)
                cur.execute('DELETE FROM sales_return_items WHERE return_id=?', (return_id,))
                cur.execute('DELETE FROM sales_returns WHERE id=?', (return_id,))
            elif collection == 'purchase_returns':
                return_id = int(doc_key)
                cur.execute('DELETE FROM purchase_return_items WHERE return_id=?', (return_id,))
                cur.execute('DELETE FROM purchase_returns WHERE id=?', (return_id,))
            elif collection == 'customers':
                cur.execute('DELETE FROM customers WHERE id=?', (int(doc_key),))
            elif collection == 'suppliers':
                cur.execute('DELETE FROM suppliers WHERE id=?', (int(doc_key),))
            elif collection == 'medicines':
                cur.execute('DELETE FROM medicines WHERE id=?', (int(doc_key),))
            elif collection == 'doctors':
                cur.execute('DELETE FROM doctors WHERE id=?', (int(doc_key),))
            elif collection == 'racks':
                rid = int(doc_key)
                cur.execute(
                    "DELETE FROM boxes WHERE section_id IN "
                    "(SELECT id FROM sections WHERE rack_id=?)",
                    (rid,),
                )
                cur.execute("DELETE FROM sections WHERE rack_id=?", (rid,))
                cur.execute("DELETE FROM racks WHERE id=?", (rid,))
            elif collection == 'sections':
                sid = int(doc_key)
                cur.execute("DELETE FROM boxes WHERE section_id=?", (sid,))
                cur.execute("DELETE FROM sections WHERE id=?", (sid,))
            elif collection == 'boxes':
                cur.execute('DELETE FROM boxes WHERE id=?', (int(doc_key),))
            elif collection == 'customer_payments':
                cur.execute('DELETE FROM customer_payments WHERE id=?', (int(doc_key),))
            elif collection == 'supplier_payments':
                cur.execute('DELETE FROM supplier_payments WHERE id=?', (int(doc_key),))
            conn.commit()
        except Exception as exc:
            log.warning('delete_down_doc %s/%s: %s', collection, doc_id, exc)
            conn.rollback()


# ── Bootstrap local-only push (ConflictResolver runs in sync_down_doc for all paths) ──

# Local SQLite tables for LWW entities (medicines keep existing synced_at merge).
_BOOTSTRAP_LWW_TABLES = {
    'customers': 'customers',
    'suppliers': 'suppliers',
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

_BOOTSTRAP_PUSH_FNS = {
    'customers': lambda conn, i: push_customer(conn, i),
    'suppliers': lambda conn, i: push_supplier(conn, i),
    'sales': lambda conn, i: push_sale(conn, i),
    'purchases': lambda conn, i: push_purchase(conn, i),
    'customer_payments': lambda conn, i: push_customer_payment(conn, i),
    'supplier_payments': lambda conn, i: push_supplier_payment(conn, i),
    'doctors': lambda conn, i: push_doctor(conn, i),
    'sales_returns': lambda conn, i: push_sales_return(conn, i),
    'purchase_returns': lambda conn, i: push_purchase_return(conn, i),
    'racks': lambda conn, i: push_rack(conn, i),
    'sections': lambda conn, i: push_section(conn, i),
    'boxes': lambda conn, i: push_box(conn, i),
}

# Payload builders for batched bootstrap / full upload (450 docs per Server commit).
_BOOTSTRAP_BUILD_PAYLOAD = {
    'customers': build_customer_payload,
    'suppliers': build_supplier_payload,
    'medicines': build_medicine_payload,
    'doctors': build_doctor_payload,
    'sales': build_sale_payload,
    'purchases': build_purchase_payload,
    'customer_payments': build_customer_payment_payload,
    'supplier_payments': build_supplier_payment_payload,
    'sales_returns': build_sales_return_payload,
    'purchase_returns': build_purchase_return_payload,
    'racks': build_rack_payload,
    'sections': build_section_payload,
    'boxes': build_box_payload,
}

_BOOTSTRAP_PUSH_ORDER = (
    'customers',
    'suppliers',
    'medicines',
    'doctors',
    'racks',
    'sections',
    'boxes',
    'sales',
    'purchases',
    'customer_payments',
    'supplier_payments',
    'sales_returns',
    'purchase_returns',
)

_BOOTSTRAP_BATCH_CHUNK = 450
_BOOTSTRAP_BATCH_TIMEOUT = 60.0


def _push_writes_batched(
    conn,
    queued: set[tuple[str, str]],
    *,
    progress_cb: Optional[Callable[[str], None]] = None,
    timeout: float = _BOOTSTRAP_BATCH_TIMEOUT,
) -> int:
    """Upload many local rows using Server batch commits (fast first sync)."""
    if not queued:
        return 0
    by_col: dict[str, list[str]] = {c: [] for c in _BOOTSTRAP_PUSH_ORDER}
    for col, sid in queued:
        if col in by_col:
            by_col[col].append(str(sid))
    writes: list[tuple[str, str, dict]] = []
    pushed = 0
    total = len(queued)

    def _flush(force: bool = False) -> None:
        nonlocal writes, pushed
        if not writes:
            return
        if not force and len(writes) < _BOOTSTRAP_BATCH_CHUNK:
            return
        if progress_cb:
            progress_cb(
                f'Uploading to Server… {pushed + len(writes):,} / {total:,}',
            )
        ok = push_docs_batch(writes, timeout=timeout)
        if ok:
            pushed += len(writes)
            writes = []
            return
        # Fallback: halve the Node batch — never one HTTP request per row.
        queue = [list(writes)]
        writes = []
        while queue:
            chunk = queue.pop(0)
            if not chunk:
                continue
            if push_docs_batch(chunk, timeout=timeout):
                pushed += len(chunk)
                continue
            if len(chunk) == 1:
                log.warning(
                    'batch push failed for %s/%s',
                    chunk[0][0],
                    chunk[0][1],
                )
                continue
            mid = len(chunk) // 2
            queue.append(chunk[:mid])
            queue.append(chunk[mid:])

    for col in _BOOTSTRAP_PUSH_ORDER:
        build_fn = _BOOTSTRAP_BUILD_PAYLOAD.get(col)
        if not build_fn:
            continue
        ids = sorted(by_col.get(col) or [], key=lambda x: int(x) if str(x).isdigit() else 0)
        if not ids:
            continue
        if progress_cb:
            progress_cb(
                f'Preparing {col.replace("_", " ")}… ({len(ids):,} record(s))',
            )
        for sid in ids:
            try:
                payload = build_fn(conn, int(sid))
            except Exception as exc:
                log.warning('bootstrap build %s/%s: %s', col, sid, exc)
                continue
            if not payload:
                continue
            writes.append((col, str(sid), payload))
            if len(writes) >= _BOOTSTRAP_BATCH_CHUNK:
                _flush(force=True)
    _flush(force=True)
    return pushed


def _bootstrap_parse_ts(value: Any):
    """Parse ISO-ish timestamps for bootstrap compare (updated_at / created_at)."""
    from core.medicine_sync_merge import parse_sync_ts
    return parse_sync_ts(value)


def _bootstrap_cloud_ts(data: dict):
    """Prefer updated_at, then push stamp synced_at, then last_updated, then created_at."""
    for key in ('updated_at', 'synced_at', 'last_updated', 'created_at'):
        ts = _bootstrap_parse_ts(data.get(key))
        if ts is not None:
            return ts
    return None


def _bootstrap_table_columns(conn, table: str) -> set[str]:
    cur = conn.cursor()
    try:
        cur.execute(f'PRAGMA table_info({table})')
        return {str(r[1]) for r in cur.fetchall()}
    except Exception:
        return set()


def _bootstrap_local_ts(conn, collection: str, doc_id: str):
    """
    Return (exists, timestamp_or_None) for a local row.
    Prefer updated_at, then last_updated, then created_at (legacy rows).
    """
    table = _BOOTSTRAP_LWW_TABLES.get(collection)
    if not table:
        return False, None
    try:
        rid = int(doc_id)
    except (TypeError, ValueError):
        return False, None
    cols = _bootstrap_table_columns(conn, table)
    if not cols:
        return False, None
    prefer = [c for c in ('updated_at', 'last_updated', 'created_at') if c in cols]
    select_cols = ', '.join(prefer) if prefer else 'id'
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT {select_cols} FROM {table} WHERE id=?', (rid,))
        row = cur.fetchone()
    except Exception:
        return False, None
    if not row:
        return False, None
    if not prefer:
        return True, None
    for i, _name in enumerate(prefer):
        ts = _bootstrap_parse_ts(row[i])
        if ts is not None:
            return True, ts
    return True, None


def _bootstrap_should_apply_cloud(conn, collection: str, doc_id: str, data: dict) -> bool:
    """
    Bootstrap gate via ConflictResolver (version → updated_at → device_id).
    Returns True only when cloud should overwrite local.
    """
    from core.conflict_resolver import ConflictDecision, should_resolve
    if not should_resolve(collection):
        return True
    if collection == 'medicines':
        # Medicine merge runs inside sync_down_doc.
        return True
    decision = _resolve_pull_decision(conn, collection, doc_id, data)
    return decision in (
        ConflictDecision.APPLY_REMOTE,
        ConflictDecision.APPLY_SOFT_DELETE,
    )


def _bootstrap_push_local_only_and_pending(
    conn,
    seen_ids: dict,
    pending_push: list,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Bootstrap bug fix: a local bill/row that never appears in the cloud pull must
    never be dropped — always push it (Sales/Purchases and other LWW entities).
    Also re-push rows we skipped because local was newer/ambiguous.
    """
    queued: set[tuple[str, str]] = set()
    for col, doc_id in pending_push:
        queued.add((col, str(doc_id)))
    for col, table in _BOOTSTRAP_LWW_TABLES.items():
        try:
            cur = conn.cursor()
            cur.execute(f'SELECT id FROM {table}')
            for (rid,) in cur.fetchall():
                sid = str(rid)
                if sid not in seen_ids.get(col, set()):
                    # Absent from cloud pull entirely — always push (esp. bills).
                    queued.add((col, sid))
        except Exception as exc:
            log.warning('bootstrap local-only scan %s: %s', col, exc)
    # Medicines are not LWW in bootstrap tables but must upload on first online sync.
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM medicines')
        for (rid,) in cur.fetchall():
            sid = str(rid)
            if sid not in seen_ids.get('medicines', set()):
                queued.add(('medicines', sid))
    except Exception as exc:
        log.warning('bootstrap local-only scan medicines: %s', exc)
    if progress_cb and queued:
        progress_cb(f'Uploading {len(queued):,} local record(s) in batches…')
    return _push_writes_batched(conn, queued, progress_cb=progress_cb)


def sync_down_all(
    conn,
    bootstrap_newer_wins: bool = False,
    progress_cb: Optional[Callable[[str], None]] = None,
    incremental: Optional[bool] = None,
) -> int:
    """Pull server docs into SQLite (Server path removed)."""
    from core import server_live as live
    from core.sync_bootstrap import is_bootstrap_done

    if incremental is None:
        incremental = is_bootstrap_done() and not bootstrap_newer_wins
    return live.sync_down_all(
        conn,
        progress_cb=progress_cb,
        incremental=bool(incremental),
    )
    # Dead Server path below kept for reference only (unreachable).
    from core import sync_watermarks as wm  # noqa: F401

    client = _get_client()
    if client is None:
        return 0
    if incremental is None:
        incremental = is_bootstrap_done() and not bootstrap_newer_wins
    count = 0
    seen_ids: dict[str, set[str]] = {c: set() for c in COLLECTIONS}
    pending_push: list[tuple[str, str]] = []
    for col in COLLECTIONS:
        if col == 'medicines_master':
            continue
        try:
            ref = _col_ref(client, col)
            since = wm.query_since(col) if (incremental and col in wm.WATERMARK_COLLECTIONS) else None
            if progress_cb:
                if since:
                    progress_cb(f'Checking new {col.replace("_", " ")}…')
                else:
                    progress_cb(f'Downloading {col.replace("_", " ")}…')
            if since:
                try:
                    query = ref.where('updated_at', '>', since).order_by('updated_at')
                    docs = list(query.stream())
                except Exception as exc:
                    log.warning('incremental %s failed (%s); falling back to full', col, exc)
                    docs = list(ref.stream())
                    since = None
            else:
                docs = list(ref.stream())
            max_ts = since
            for doc in docs:
                seen_ids[col].add(str(doc.id))
                data = doc.to_dict() or {}
                ts = data.get('updated_at') or data.get('synced_at')
                if isinstance(ts, str) and ts and (max_ts is None or ts > max_ts):
                    max_ts = ts
                status = sync_down_doc(conn, col, doc.id, data)
                if status in ('applied', 'soft_deleted'):
                    count += 1
            if col in wm.WATERMARK_COLLECTIONS:
                if max_ts:
                    wm.bump_watermark(col, max_ts)
                else:
                    wm.seed_from_local(conn, col)
        except Exception as exc:
            log.warning('sync_down_all %s: %s', col, exc)
    uploaded = 0
    if bootstrap_newer_wins:
        uploaded = _bootstrap_push_local_only_and_pending(
            conn, seen_ids, pending_push, progress_cb=progress_cb,
        )
        wm.seed_all_from_local(conn)
    else:
        # Ensure watermarks exist after any full collection pull (first run / fallback).
        for col in wm.WATERMARK_COLLECTIONS:
            if not wm.get_watermark(col):
                wm.seed_from_local(conn, col)
    sync_down_all.last_uploaded = uploaded
    return count


_VERIFY_TABLES = {
    'customers': 'customers',
    'suppliers': 'suppliers',
    'medicines': 'medicines',
    'doctors': 'doctors',
    'sales': 'sales',
    'purchases': 'purchases',
    'customer_payments': 'customer_payments',
    'supplier_payments': 'supplier_payments',
    'sales_returns': 'sales_returns',
    'purchase_returns': 'purchase_returns',
    'racks': 'racks',
    'sections': 'sections',
    'boxes': 'boxes',
}


def verify_local_vs_server(
    conn,
    *,
    progress_cb: Optional[Callable[[str], None]] = None,
    sample_sales: int = 5,
) -> dict[str, Any]:
    """Compare active store SQLite rows with Server documents."""
    from core.sync_prefs import is_online_mode, mode_label, get_sync_mode

    result: dict[str, Any] = {
        'ok': False,
        'match': False,
        'store_id': get_store_id(),
        'collections': {},
        'issues': [],
        'spot_checks': [],
        'sales_total_local': None,
        'sales_total_server': None,
    }
    if not is_online_mode():
        result['error'] = (
            f'Sync mode is {mode_label(get_sync_mode())}. Switch to Online first.'
        )
        return result
    st = get_status()
    if not st.get('configured'):
        result['error'] = 'Server is not configured.'
        return result
    ensure_credentials()
    ok, access_msg = verify_server_access()
    if not ok:
        result['error'] = access_msg
        return result
    client = _get_client()
    if client is None:
        result['error'] = 'Server client unavailable.'
        return result

    store_ref = client.collection('stores').document(get_store_id())
    cur = conn.cursor()
    mismatched_cols: list[str] = []
    id_issues: list[str] = []

    for col, table in _VERIFY_TABLES.items():
        if progress_cb:
            progress_cb(f'Checking {col.replace("_", " ")}…')
        try:
            local_count = int(cur.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
        except Exception as exc:
            result['issues'].append(f'{col}: local count failed ({exc})')
            continue
        try:
            docs = list(store_ref.collection(col).stream())
            remote_count = len(docs)
        except Exception as exc:
            result['issues'].append(f'{col}: Server read failed ({exc})')
            continue
        local_ids = {
            str(r[0])
            for r in cur.execute(f'SELECT id FROM {table}').fetchall()
        }
        remote_ids = {str(d.id) for d in docs}
        only_local = sorted(local_ids - remote_ids, key=lambda x: int(x) if x.isdigit() else 0)
        only_remote = sorted(remote_ids - local_ids, key=lambda x: int(x) if x.isdigit() else 0)
        match = local_count == remote_count and not only_local and not only_remote
        if not match:
            mismatched_cols.append(col)
        if only_local:
            id_issues.append(
                f'{col}: {len(only_local)} in local only (e.g. {only_local[:5]})',
            )
        if only_remote:
            id_issues.append(
                f'{col}: {len(only_remote)} on Server only (e.g. {only_remote[:5]})',
            )
        result['collections'][col] = {
            'local': local_count,
            'server': remote_count,
            'match': match,
            'only_local': len(only_local),
            'only_remote': len(only_remote),
        }

    # Sales amount sanity check
    try:
        local_total = round(float(cur.execute(
            'SELECT COALESCE(SUM(total_amount), 0) FROM sales',
        ).fetchone()[0] or 0), 2)
        remote_total = 0.0
        for doc in store_ref.collection('sales').stream():
            remote_total += float((doc.to_dict() or {}).get('total_amount') or 0)
        remote_total = round(remote_total, 2)
        result['sales_total_local'] = local_total
        result['sales_total_server'] = remote_total
        if abs(local_total - remote_total) > 1.0:
            result['issues'].append(
                f'sales totals differ: local {local_total} vs server {remote_total}',
            )
    except Exception as exc:
        result['issues'].append(f'sales total check failed: {exc}')

    # Random sale spot checks
    try:
        sample_n = max(0, min(int(sample_sales or 0), 20))
        if sample_n:
            rows = cur.execute(
                f'SELECT id FROM sales ORDER BY RANDOM() LIMIT {sample_n}',
            ).fetchall()
            for (sid,) in rows:
                lr = cur.execute(
                    'SELECT bill_no, bill_date, total_amount FROM sales WHERE id=?',
                    (sid,),
                ).fetchone()
                doc = store_ref.collection('sales').document(str(sid)).get()
                if not doc.exists:
                    result['spot_checks'].append({
                        'collection': 'sales',
                        'id': sid,
                        'status': 'missing_in_server',
                    })
                    continue
                data = doc.to_dict() or {}
                problems: list[str] = []
                if str(lr[0] or '') != str(data.get('bill_no') or ''):
                    problems.append('bill_no')
                if str(lr[1] or '') != str(data.get('bill_date') or ''):
                    problems.append('bill_date')
                if abs(float(lr[2] or 0) - float(data.get('total_amount') or 0)) > 0.02:
                    problems.append('total_amount')
                li = cur.execute(
                    'SELECT COUNT(*) FROM sales_items WHERE sale_id=?',
                    (sid,),
                ).fetchone()[0]
                ri = len(data.get('items') or [])
                if int(li) != int(ri):
                    problems.append('item_count')
                result['spot_checks'].append({
                    'collection': 'sales',
                    'id': sid,
                    'status': 'ok' if not problems else 'mismatch',
                    'fields': problems,
                })
    except Exception as exc:
        result['issues'].append(f'spot check failed: {exc}')

    result['issues'].extend(id_issues)
    bad_spots = [s for s in result['spot_checks'] if s.get('status') != 'ok']
    if bad_spots:
        result['issues'].append(
            f'{len(bad_spots)} sampled sale(s) failed spot check',
        )
    result['match'] = not mismatched_cols and not result['issues']
    result['ok'] = True
    if result['match']:
        result['message'] = 'Local database matches Server for all collections.'
    else:
        parts = []
        if mismatched_cols:
            parts.append('count/id mismatch: ' + ', '.join(mismatched_cols))
        if result['issues']:
            parts.append(result['issues'][0])
        result['message'] = 'Mismatch found — ' + ('; '.join(parts) if parts else 'see details')
    return result


def push_all_local(conn, progress_cb: Optional[Callable[[str], None]] = None) -> int:
    """Upload all local rows for the active store to Satpuda Core Server."""
    from core import server_live as live
    from core.server_sync import push_store_conn

    # The most damaging of the four: this creates the store AND then uploads
    # the local ledger into it, leaving a real duplicate store on the server
    # for someone to reconcile by hand. A store that has never been published
    # still publishes -- that is what Push to Server is for.
    session = live.ensure_active_store_on_server(create_if_new=True)
    return int(push_store_conn(conn, session['token'], progress_cb=progress_cb, label='Upload') or 0)


def run_bootstrap(conn, progress_cb: Optional[Callable[[str], None]] = None) -> tuple[bool, str, int]:
    """Full local↔server merge. Returns (ok, message, uploaded_count)."""
    from core import server_live as live
    return live.run_bootstrap(conn, progress_cb=progress_cb)


# ── Realtime listeners ────────────────────────────────────────────────────────

def _schedule_ui_notify(collection: str) -> None:
    """Debounce UI refresh callbacks off the gRPC/apply threads."""
    global _notify_timer
    if _on_change is None:
        return
    with _notify_lock:
        _notify_cols.add(collection)
        if _notify_timer is not None:
            try:
                _notify_timer.cancel()
            except Exception:
                pass

        def _flush():
            global _notify_timer
            with _notify_lock:
                cols = set(_notify_cols)
                _notify_cols.clear()
                _notify_timer = None
            cb = _on_change
            if not cb or not cols:
                return
            for col in cols:
                try:
                    cb(col)
                except Exception:
                    pass

        _notify_timer = threading.Timer(1.2, _flush)
        _notify_timer.daemon = True
        _notify_timer.start()


def _ensure_apply_worker(conn) -> None:
    global _apply_worker_started
    if _apply_worker_started:
        return
    _apply_worker_started = True

    def _worker():
        while True:
            item = _apply_queue.get()
            if item is None:
                break
            collection, doc_id, data, removed = item
            try:
                if removed:
                    delete_down_doc(conn, collection, doc_id)
                    _schedule_ui_notify(collection)
                else:
                    status = sync_down_doc(conn, collection, doc_id, data or {})
                    if status in ('applied', 'soft_deleted'):
                        _schedule_ui_notify(collection)
            except Exception as exc:
                log.warning('server apply %s/%s: %s', collection, doc_id, exc)

    threading.Thread(target=_worker, daemon=True, name='server-apply').start()


def start_listeners(conn, on_change: Optional[Callable[[str], None]] = None) -> bool:
    """Compatibility shim — starts Satpuda Core Server poller (no Server)."""
    stop_listeners()
    try:
        from core import server_live as live
        return bool(live.start_poller(conn, on_change=on_change))
    except Exception as exc:
        log.warning('server poller start failed: %s', exc)
        return False


def stop_listeners() -> None:
    global _listeners
    _listeners = []
    try:
        from core import server_live as live
        live.stop_poller()
    except Exception:
        pass


def start_background(conn, on_change: Optional[Callable[[str], None]] = None) -> None:
    """Start server poller — bootstrap is run separately with UI progress."""

    def _run():
        start_listeners(conn, on_change)

    threading.Thread(target=_run, daemon=True, name='server-sync').start()