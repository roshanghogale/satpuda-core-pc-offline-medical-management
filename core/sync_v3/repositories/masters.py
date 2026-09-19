"""Master entity Sync V3 helpers (customers, suppliers, medicines, doctors, settings)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def after_master_write(conn, collection: str, local_id: int, *, operation: str = "update") -> None:
    from core.sync_v3.flags import is_entity_v3_enabled, ENTITY_MASTERS

    if not is_entity_v3_enabled(ENTITY_MASTERS):
        return
    try:
        from core.sync_v3.schema import (
            ensure_sync_v3_schema,
            set_sync_state,
            SYNC_STATE_PENDING,
        )
        from core.sync_v3.data_change_bus import emit
        from core.sync_v3.outbox_worker import enqueue, kick_worker

        ensure_sync_v3_schema(conn)
        enqueue(
            conn,
            collection=str(collection),
            local_id=int(local_id),
            operation=operation,
            commit=True,
        )
        set_sync_state(conn, str(collection), int(local_id), SYNC_STATE_PENDING)
        emit(str(collection), local=True)
        kick_worker()
    except Exception as exc:
        log.debug("master after_write: %s", exc)
