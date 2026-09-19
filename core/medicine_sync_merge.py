"""Timestamp-aware medicine merge for Server sync (stock_qty / is_hidden).

Precedence for which side wins comes from ConflictResolver (version /
updated_at / device_id). Stock/hidden merge rules are unchanged once a winner
is chosen; equal-meta SKIP is a no-op.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from core.conflict_resolver import (
    ConflictDecision,
    SyncMeta,
    meta_from_mapping,
    resolve,
)


@dataclass
class MedicineMergeResult:
    stock_qty: int
    is_hidden: int
    synced_at: Optional[str]
    needs_repush: bool
    decision: ConflictDecision = ConflictDecision.APPLY_REMOTE


def parse_sync_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _remote_hidden(data: dict, fallback: int) -> int:
    if "is_hidden" not in data:
        return fallback
    return 1 if data.get("is_hidden") else 0


def merge_medicine_pull(
    *,
    local_stock: Optional[int],
    local_hidden: Optional[int],
    local_synced_at: Optional[str],
    remote_data: dict,
    local_meta: Optional[SyncMeta] = None,
) -> MedicineMergeResult:
    """Resolve stock_qty / is_hidden using ConflictResolver precedence."""
    remote_stock = int(remote_data.get("stock_qty") or 0)
    remote_meta = meta_from_mapping(remote_data)

    if local_stock is None:
        hidden = _remote_hidden(remote_data, 0)
        return MedicineMergeResult(
            stock_qty=remote_stock,
            is_hidden=hidden,
            synced_at=remote_data.get("synced_at") or remote_meta.updated_at_raw,
            needs_repush=False,
            decision=ConflictDecision.APPLY_REMOTE,
        )

    # Prefer explicit local_meta; else build from synced_at (legacy) fields.
    if local_meta is None:
        local_meta = SyncMeta(
            version=1,
            updated_at=parse_sync_ts(local_synced_at),
            updated_at_raw=local_synced_at,
            device_id="",
            deleted=False,
        )

    decision = resolve(local_meta, remote_meta, collection="medicines")

    local_hidden_val = int(local_hidden or 0)
    remote_hidden = _remote_hidden(remote_data, local_hidden_val)

    if decision == ConflictDecision.SKIP:
        return MedicineMergeResult(
            stock_qty=int(local_stock),
            is_hidden=local_hidden_val,
            synced_at=local_synced_at,
            needs_repush=False,
            decision=decision,
        )

    if decision == ConflictDecision.KEEP_LOCAL:
        return MedicineMergeResult(
            stock_qty=int(local_stock),
            is_hidden=local_hidden_val,
            synced_at=local_synced_at,
            needs_repush=True,
            decision=decision,
        )

    # APPLY_REMOTE / APPLY_SOFT_DELETE: remote metadata wins; keep stock/hidden
    # merge nuances when timestamps were historically equal — not needed here
    # because ConflictResolver already chose a winner. Apply remote values.
    if decision in (ConflictDecision.APPLY_REMOTE, ConflictDecision.APPLY_SOFT_DELETE):
        return MedicineMergeResult(
            stock_qty=remote_stock,
            is_hidden=remote_hidden,
            synced_at=remote_data.get("synced_at") or remote_meta.updated_at_raw,
            needs_repush=False,
            decision=decision,
        )

    # Fallback: conservative stock max (should not hit)
    stock = max(int(local_stock), remote_stock)
    if int(local_stock) > 0 and remote_stock == 0:
        stock = int(local_stock)
    hidden = remote_hidden if "is_hidden" in remote_data else local_hidden_val
    needs_repush = stock != remote_stock or (
        "is_hidden" in remote_data and hidden != remote_hidden
    )
    return MedicineMergeResult(stock, hidden, local_synced_at, needs_repush, decision)


def ensure_medicine_sync_triggers(cur) -> None:
    """Bump medicines.synced_at on any local medicine edit so Server wins correctly."""
    cur.execute("DROP TRIGGER IF EXISTS medicines_sync_bump")
    cur.execute("DROP TRIGGER IF EXISTS medicines_sync_insert")
    cur.execute(
        """
        CREATE TRIGGER medicines_sync_bump
        AFTER UPDATE ON medicines
        FOR EACH ROW
        WHEN
            OLD.stock_qty IS NOT NEW.stock_qty
            OR COALESCE(OLD.is_hidden, 0) IS NOT COALESCE(NEW.is_hidden, 0)
            OR COALESCE(OLD.mrp, -1) IS NOT COALESCE(NEW.mrp, -1)
            OR COALESCE(OLD.rate, -1) IS NOT COALESCE(NEW.rate, -1)
            OR COALESCE(OLD.name, '') IS NOT COALESCE(NEW.name, '')
            OR COALESCE(OLD.type, '') IS NOT COALESCE(NEW.type, '')
            OR COALESCE(OLD.unit, '') IS NOT COALESCE(NEW.unit, '')
            OR COALESCE(OLD.batch_no, '') IS NOT COALESCE(NEW.batch_no, '')
            OR COALESCE(OLD.expiry_date, '') IS NOT COALESCE(NEW.expiry_date, '')
            OR COALESCE(OLD.manufacturer, '') IS NOT COALESCE(NEW.manufacturer, '')
            OR COALESCE(OLD.schedule, '') IS NOT COALESCE(NEW.schedule, '')
            OR COALESCE(OLD.location, '') IS NOT COALESCE(NEW.location, '')
            OR COALESCE(OLD.hsn_code, '') IS NOT COALESCE(NEW.hsn_code, '')
            OR COALESCE(OLD.content_drug, '') IS NOT COALESCE(NEW.content_drug, '')
            OR COALESCE(OLD.gst_percent, -1) IS NOT COALESCE(NEW.gst_percent, -1)
        BEGIN
            UPDATE medicines
            SET synced_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = NEW.id;
        END
        """
    )
    cur.execute(
        """
        CREATE TRIGGER medicines_sync_insert
        AFTER INSERT ON medicines
        FOR EACH ROW
        WHEN NEW.synced_at IS NULL
        BEGIN
            UPDATE medicines
            SET synced_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = NEW.id;
        END
        """
    )
