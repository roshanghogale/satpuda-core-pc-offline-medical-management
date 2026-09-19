"""SaleRepository — Sync V3 write path for sales."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def maybe_save_new_bill(conn, *args, **kwargs):
    """Route to V3 when sales flag on; otherwise legacy billing_service."""
    from core.sync_v3.flags import is_entity_v3_enabled, ENTITY_SALES

    if is_entity_v3_enabled(ENTITY_SALES):
        return save_new_bill_v3(conn, *args, **kwargs)
    from core.billing_service import save_new_bill

    return save_new_bill(conn, *args, **kwargs)


def save_new_bill_v3(conn, customer_id, medicines, discount_pct,
                     rounding, cash_paid, online_paid,
                     doctor_name, doctor_phone, previous_due,
                     discount_rs=None, bill_date=None, **extra):
    """
    V3 sales path: reuse legacy insert/stock logic but force local-first commit
    (already via commit_local_then_push) and also enqueue sync_outbox_v2 + emit bus.
    Full allocate-deferred PENDING bill_no is purchases-first; sales keep
    immediate allocate but never roll back the row.

    ``extra`` is whatever else the legacy save takes (customer_name,
    customer_phone, sync, ...). Classic passes customer_name and customer_phone,
    and this signature refused them with a TypeError whenever the V3 sales flag
    was on -- the bill was never saved.
    """
    from core.billing_service import save_new_bill
    from core.sync_v3.schema import ensure_sync_v3_schema, set_sync_state, SYNC_STATE_PENDING, SYNC_STATE_SYNCED
    from core.sync_v3.data_change_bus import emit
    from core.sync_v3.outbox_worker import enqueue, kick_worker
    from core.sync_prefs import is_online_mode

    ensure_sync_v3_schema(conn)
    bill_no, sale_id = save_new_bill(
        conn,
        customer_id,
        medicines,
        discount_pct,
        rounding,
        cash_paid,
        online_paid,
        doctor_name,
        doctor_phone,
        previous_due,
        discount_rs=discount_rs,
        bill_date=bill_date,
        **extra,
    )
    # Legacy path already committed. Ensure v2 outbox + bus for uniformity.
    try:
        if is_online_mode():
            # If already pushed successfully, mark synced; else pending.
            row = conn.execute(
                "SELECT id FROM sync_outbox WHERE collection='sales' AND local_id=? "
                "AND status='pending' LIMIT 1",
                (int(sale_id),),
            ).fetchone()
            if row:
                enqueue(
                    conn,
                    collection="sales",
                    local_id=int(sale_id),
                    operation="create",
                    commit=True,
                )
                set_sync_state(conn, "sales", int(sale_id), SYNC_STATE_PENDING)
                kick_worker()
            else:
                set_sync_state(conn, "sales", int(sale_id), SYNC_STATE_SYNCED)
        emit("sales", local=True)
    except Exception as exc:
        log.debug("sale v3 post-write: %s", exc)
    return bill_no, sale_id

