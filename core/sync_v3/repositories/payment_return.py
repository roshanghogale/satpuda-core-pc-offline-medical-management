"""Payment / return Sync V3 helpers — enqueue outbox_v2 + bus emit after local writes."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def after_local_write(conn, collection: str, local_id: int, *, operation: str = "update") -> None:
    """Call after a payment/return local commit to join the uniform pipeline."""
    from core.sync_v3.flags import is_entity_v3_enabled, ENTITY_PAYMENTS, ENTITY_RETURNS

    col = (collection or "").strip()
    entity = ENTITY_PAYMENTS if "payment" in col else ENTITY_RETURNS
    if not is_entity_v3_enabled(entity):
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
            collection=col,
            local_id=int(local_id),
            operation=operation,
            commit=True,
        )
        set_sync_state(conn, col, int(local_id), SYNC_STATE_PENDING)
        emit(col, local=True)
        kick_worker()
    except Exception as exc:
        log.debug("payment_return after_local_write: %s", exc)
