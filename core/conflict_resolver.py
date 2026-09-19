"""
Shared sync conflict resolution for bootstrap pulls and realtime listeners.

Precedence (uniform for every synced entity):
  1. Higher version wins
  2. If version equal -> newer updated_at wins
  3. If updated_at equal -> device_id tiebreaker (lexicographic; non-empty beats empty)
  4. If still equal -> SKIP (no-op, logged)

Bills / line items: never treat cloud *absence* as a remote delete. Only honor
an explicit deleted=true on a document that still exists.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

NEVER_AUTO_DELETE = frozenset({
    'sales',
    'purchases',
    'sales_items',
    'purchase_items',
})

RESOLVED_COLLECTIONS = frozenset({
    'customers',
    'suppliers',
    'doctors',
    'medicines',
    'sales',
    'purchases',
    'customer_payments',
    'supplier_payments',
    'sales_returns',
    'purchase_returns',
    'racks',
    'sections',
    'boxes',
})

COLLECTION_TO_TABLE = {
    'customers': 'customers',
    'suppliers': 'suppliers',
    'doctors': 'doctors',
    'medicines': 'medicines',
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


class ConflictDecision(str, Enum):
    APPLY_REMOTE = 'apply_remote'
    KEEP_LOCAL = 'keep_local'
    SKIP = 'skip'
    APPLY_SOFT_DELETE = 'apply_soft_delete'


@dataclass(frozen=True)
class SyncMeta:
    version: int = 1
    updated_at: Optional[datetime] = None
    updated_at_raw: Optional[str] = None
    device_id: str = ''
    deleted: bool = False


@dataclass(frozen=True)
class SkipRecord:
    collection: str
    doc_id: str
    reason: str
    at: str


_skip_lock = threading.Lock()
_skip_log: list[SkipRecord] = []
_SKIP_CAP = 50


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        try:
            if len(raw) == 10 and raw[4] == '-' and raw[7] == '-':
                return datetime.fromisoformat(raw + 'T00:00:00+00:00')
        except Exception:
            pass
        return None


def _truthy_deleted(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes')
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return bool(value)


def meta_from_mapping(data: Optional[Mapping[str, Any]]) -> SyncMeta:
    if not data:
        return SyncMeta()
    try:
        version = int(data.get('version') or 1)
    except (TypeError, ValueError):
        version = 1
    if version < 1:
        version = 1
    raw = None
    ts = None
    for key in ('updated_at', 'synced_at', 'last_updated', 'created_at'):
        if key not in data or data.get(key) in (None, ''):
            continue
        raw = str(data.get(key))
        ts = parse_ts(raw)
        if ts is not None:
            break
    device_id = str(data.get('device_id') or '').strip()
    deleted = _truthy_deleted(data.get('deleted')) if 'deleted' in data else False
    return SyncMeta(
        version=version,
        updated_at=ts,
        updated_at_raw=raw,
        device_id=device_id,
        deleted=deleted,
    )


def sync_meta_to_payload(meta: SyncMeta) -> dict[str, Any]:
    out: dict[str, Any] = {
        'version': int(meta.version or 1),
        'device_id': meta.device_id or '',
        'deleted': bool(meta.deleted),
    }
    if meta.updated_at_raw:
        out['updated_at'] = meta.updated_at_raw
    elif meta.updated_at is not None:
        out['updated_at'] = meta.updated_at.isoformat()
    return out


def record_skip(collection: str, doc_id: str, reason: str) -> None:
    at = datetime.now(timezone.utc).isoformat()
    rec = SkipRecord(collection=collection, doc_id=str(doc_id), reason=reason, at=at)
    log.info('ConflictResolver SKIP %s/%s — %s', collection, doc_id, reason)
    with _skip_lock:
        _skip_log.append(rec)
        if len(_skip_log) > _SKIP_CAP:
            del _skip_log[:-_SKIP_CAP]


def recent_skips(limit: int = 20) -> list[SkipRecord]:
    with _skip_lock:
        return list(_skip_log[-limit:])


def clear_skip_log() -> None:
    with _skip_lock:
        _skip_log.clear()


def resolve(
    local: Optional[SyncMeta],
    remote: SyncMeta,
    *,
    collection: str = '',
    doc_id: str = '',
) -> ConflictDecision:
    # Explicit server delete always wins. Hard-delete changelog rows often omit
    # entity_version; without this, KEEP_LOCAL re-pushes and resurrects the bill.
    if remote.deleted:
        return ConflictDecision.APPLY_SOFT_DELETE
    if local is None:
        return ConflictDecision.APPLY_REMOTE
    if remote.version != local.version:
        if remote.version > local.version:
            return ConflictDecision.APPLY_REMOTE
        return ConflictDecision.KEEP_LOCAL
    rt, lt = remote.updated_at, local.updated_at
    if rt is not None and lt is not None:
        if rt > lt:
            return ConflictDecision.APPLY_REMOTE
        if lt > rt:
            return ConflictDecision.KEEP_LOCAL
    elif rt is not None and lt is None:
        return ConflictDecision.APPLY_REMOTE
    elif lt is not None and rt is None:
        return ConflictDecision.KEEP_LOCAL
    ld = (local.device_id or '').strip()
    rd = (remote.device_id or '').strip()
    if ld != rd:
        if not ld and rd:
            winner_remote = True
        elif ld and not rd:
            winner_remote = False
        else:
            winner_remote = rd > ld
        if winner_remote:
            return ConflictDecision.APPLY_REMOTE
        return ConflictDecision.KEEP_LOCAL
    record_skip(
        collection or '?',
        doc_id or '?',
        'version/updated_at/device_id identical',
    )
    try:
        from core.sync_status import note_skip
        note_skip(collection or '?', str(doc_id or '?'), 'identical meta')
    except Exception:
        pass
    return ConflictDecision.SKIP


def never_auto_delete(collection: str) -> bool:
    return collection in NEVER_AUTO_DELETE


def should_resolve(collection: str) -> bool:
    return collection in RESOLVED_COLLECTIONS

