"""PurchaseRepository — Sync V3 write path for purchases."""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)


def maybe_save_purchase(conn, *args, **kwargs) -> str:
    from core.sync_v3.flags import is_entity_v3_enabled, ENTITY_PURCHASES

    if is_entity_v3_enabled(ENTITY_PURCHASES):
        return save_purchase_v3(conn, *args, **kwargs)
    from core.purchase_service import save_purchase

    return save_purchase(conn, *args, **kwargs)


def maybe_finalize_autosave(conn, *args, **kwargs) -> str:
    from core.sync_v3.flags import is_entity_v3_enabled, ENTITY_PURCHASES

    if is_entity_v3_enabled(ENTITY_PURCHASES):
        # Finalize still uses allocate-deferred via same local-first path
        # after converting draft → real row.
        return finalize_autosave_v3(conn, *args, **kwargs)
    from core.purchase_service import finalize_autosave_purchase

    return finalize_autosave_purchase(conn, *args, **kwargs)


def save_purchase_v3(conn, supplier_id: int, purchase_date_str: str,
                     bill_number: str, calc_result: dict, items: list) -> str:
    from core.online_guard import ensure_can_mutate
    from core.purchase_service import (
        _insert_items,
        payment_split_from_calc,
        recalculate_supplier_due,
        _allocate_purchase_number,
    )
    from core.sync_prefs import is_online_mode
    from core.fy_serial import display_purchase_no, patch_purchase_fy_fields
    from core.sync_v3.schema import ensure_sync_v3_schema, set_sync_state, SYNC_STATE_PENDING
    from core.sync_v3.data_change_bus import emit
    from core.sync_v3.outbox_worker import enqueue, kick_worker

    ensure_can_mutate()
    ensure_sync_v3_schema(conn)
    try:
        purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d").date()
    except ValueError:
        purchase_date = datetime.now().date()

    online = is_online_mode()
    if online:
        # Unique temp key — never reuse date-only PENDING (UNIQUE purchase_no).
        import uuid

        purchase_no = f"PENDING/{uuid.uuid4().hex[:12]}"
        needs_fy = True
    else:
        purchase_no = _allocate_purchase_number(conn, purchase_date=purchase_date)
        needs_fy = False

    cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)
    gst_calc_method = calc_result.get("gst_calc_method") or "discount_after_gst"
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO purchases (
            purchase_no, supplier_id, purchase_date, bill_number,
            subtotal, total_gst, cgst, sgst, total_amount,
            overall_discount, rounding, need_to_pay, final_amount,
            amount_paid, amount_paid_at_entry, cash_paid_at_entry, online_paid_at_entry,
            expenditure,
            previous_due, previous_credit, due, current_credit, total_due,
            bill_cleared, account_cleared,
            due_amount, credit_amount, gst_calc_method, sync_state
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            purchase_no,
            supplier_id,
            purchase_date,
            bill_number,
            calc_result["subtotal"],
            calc_result["total_gst"],
            calc_result["cgst"],
            calc_result["sgst"],
            calc_result["total_amount"],
            calc_result["overall_discount"],
            calc_result["rounding"],
            calc_result["need_to_pay"],
            calc_result["final_amount"],
            entry_paid,
            entry_paid,
            cash_paid,
            online_paid,
            round(float(calc_result.get("expenditure", 0) or 0), 2),
            calc_result["previous_due"],
            calc_result["previous_credit"],
            calc_result["due"],
            calc_result["current_credit"],
            calc_result["total_due"],
            calc_result["bill_cleared"],
            calc_result["account_cleared"],
            calc_result["due"],
            calc_result["current_credit"],
            gst_calc_method,
            "pending_sync",
        ),
    )
    purchase_id = int(cur.lastrowid)
    if not needs_fy:
        patch_purchase_fy_fields(cur, purchase_id, purchase_no, purchase_date)
    _insert_items(cur, purchase_id, items, conn=conn)
    recalculate_supplier_due(conn, supplier_id, commit=False)
    try:
        from core.sync_coordinator import stamp_purchase_meta

        stamp_purchase_meta(conn, purchase_id, commit=False)
    except Exception:
        pass

    enqueue(
        conn,
        collection="purchases",
        local_id=purchase_id,
        operation="create",
        needs_fy=needs_fy,
        fy_kind="purchases" if needs_fy else None,
        fy_date=purchase_date.isoformat() if needs_fy else None,
        commit=False,
    )
    set_sync_state(conn, "purchases", purchase_id, SYNC_STATE_PENDING, commit=False)
    conn.commit()
    emit("purchases", local=True)
    kick_worker()

    if needs_fy:
        # Worker will assign serial shortly; show pending indicator.
        return "…"
    return display_purchase_no(purchase_no)


def finalize_autosave_v3(conn, purchase_id: int, supplier_id: int,
                         bill_number: str, purchase_date_str: str,
                         calc_result: dict, items: list) -> str:
    """Convert APU draft → real purchase via outbox + allocate-deferred."""
    from core.online_guard import ensure_can_mutate
    from core.purchase_service import (
        _insert_items,
        payment_split_from_calc,
        recalculate_supplier_due,
    )
    from core.sync_prefs import is_online_mode
    from core.fy_serial import display_purchase_no
    from core.sync_v3.schema import ensure_sync_v3_schema, set_sync_state, SYNC_STATE_PENDING
    from core.sync_v3.data_change_bus import emit
    from core.sync_v3.outbox_worker import enqueue, kick_worker

    ensure_can_mutate()
    ensure_sync_v3_schema(conn)
    try:
        purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d").date()
    except ValueError:
        purchase_date = datetime.now().date()

    online = is_online_mode()
    needs_fy = online
    if online:
        import uuid

        purchase_no = f"PENDING/{uuid.uuid4().hex[:12]}"
    else:
        from core.purchase_service import _allocate_purchase_number

        purchase_no = _allocate_purchase_number(
            conn, purchase_date=purchase_date, exclude_purchase_id=purchase_id
        )

    cash_paid, online_paid, entry_paid = payment_split_from_calc(calc_result)
    gst_calc_method = calc_result.get("gst_calc_method") or "discount_after_gst"
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE purchases SET
            purchase_no=?, supplier_id=?, purchase_date=?, bill_number=?,
            subtotal=?, total_gst=?, cgst=?, sgst=?, total_amount=?,
            overall_discount=?, rounding=?, need_to_pay=?, final_amount=?,
            amount_paid=?, amount_paid_at_entry=?, cash_paid_at_entry=?, online_paid_at_entry=?,
            expenditure=?,
            previous_due=?, previous_credit=?, due=?, current_credit=?, total_due=?,
            bill_cleared=?, account_cleared=?,
            due_amount=?, credit_amount=?, gst_calc_method=?, is_autosave=0,
            sync_state=?
        WHERE id=?
        """,
        (
            purchase_no,
            supplier_id,
            purchase_date,
            bill_number,
            calc_result["subtotal"],
            calc_result["total_gst"],
            calc_result["cgst"],
            calc_result["sgst"],
            calc_result["total_amount"],
            calc_result["overall_discount"],
            calc_result["rounding"],
            calc_result["need_to_pay"],
            calc_result["final_amount"],
            entry_paid,
            entry_paid,
            cash_paid,
            online_paid,
            round(float(calc_result.get("expenditure", 0) or 0), 2),
            calc_result["previous_due"],
            calc_result["previous_credit"],
            calc_result["due"],
            calc_result["current_credit"],
            calc_result["total_due"],
            calc_result["bill_cleared"],
            calc_result["account_cleared"],
            calc_result["due"],
            calc_result["current_credit"],
            gst_calc_method,
            "pending_sync",
            purchase_id,
        ),
    )
    cur.execute("DELETE FROM purchase_items WHERE purchase_id=?", (purchase_id,))
    _insert_items(cur, purchase_id, items, conn=conn)
    recalculate_supplier_due(conn, supplier_id, commit=False)
    try:
        from core.sync_coordinator import stamp_purchase_meta

        stamp_purchase_meta(conn, purchase_id, commit=False)
    except Exception:
        pass

    enqueue(
        conn,
        collection="purchases",
        local_id=int(purchase_id),
        operation="create",
        needs_fy=needs_fy,
        fy_kind="purchases" if needs_fy else None,
        fy_date=purchase_date.isoformat() if needs_fy else None,
        commit=False,
    )
    set_sync_state(conn, "purchases", int(purchase_id), SYNC_STATE_PENDING, commit=False)
    conn.commit()
    emit("purchases", local=True)
    kick_worker()
    if needs_fy:
        return "…"
    return display_purchase_no(purchase_no)
