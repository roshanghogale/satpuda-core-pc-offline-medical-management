"""Unified SyncPipeline.write — local row + outbox in one transaction."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


class SyncPipeline:
    """
    One write discipline for all entities:

      validate → LOCAL_TXN (row mutation + outbox_v2) → COMMIT
      → DataChangeBus.emit → OutboxWorker push
    """

    def __init__(self, conn):
        self.conn = conn

    def write(
        self,
        *,
        collection: str,
        local_id: int,
        operation: str,
        mutate_fn: Callable[[], None],
        payload: Optional[dict[str, Any]] = None,
        needs_fy: bool = False,
        fy_kind: Optional[str] = None,
        fy_date: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> int:
        """
        Run mutate_fn inside the caller's open transaction expectations, then
        enqueue outbox and commit. Returns local_id.
        """
        from core.sync_v3.schema import (
            SYNC_STATE_PENDING,
            ensure_sync_v3_schema,
            set_sync_state,
        )
        from core.sync_v3.data_change_bus import emit
        from core.sync_v3.outbox_worker import enqueue, kick_worker

        ensure_sync_v3_schema(self.conn)
        col = str(collection or "").strip()
        op = str(operation or "update").strip().lower()
        lid = int(local_id)

        mutate_fn()

        key = idempotency_key or f"{col}:{op}:{lid}:{uuid.uuid4().hex[:12]}"
        enqueue(
            self.conn,
            collection=col,
            local_id=lid,
            operation=op,
            payload=payload or {},
            needs_fy=needs_fy,
            fy_kind=fy_kind,
            fy_date=fy_date,
            idempotency_key=key,
            commit=False,
        )
        try:
            set_sync_state(self.conn, col, lid, SYNC_STATE_PENDING, commit=False)
        except Exception:
            pass

        self.conn.commit()
        emit(col, local=True)
        kick_worker()
        return lid


def write_entity(
    conn,
    *,
    collection: str,
    local_id: int,
    operation: str,
    mutate_fn: Callable[[], None],
    payload: Optional[dict[str, Any]] = None,
    needs_fy: bool = False,
    fy_kind: Optional[str] = None,
    fy_date: Optional[str] = None,
) -> int:
    return SyncPipeline(conn).write(
        collection=collection,
        local_id=local_id,
        operation=operation,
        mutate_fn=mutate_fn,
        payload=payload,
        needs_fy=needs_fy,
        fy_kind=fy_kind,
        fy_date=fy_date,
    )
