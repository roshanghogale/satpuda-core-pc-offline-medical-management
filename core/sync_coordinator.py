"""Routes cloud sync by mode: offline (Drive) vs online (Satpuda Core Server)."""
from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)

from core.sync_prefs import is_online_mode, is_offline_mode


def stamp_sale_meta(
    conn, sale_id: int, medicine_ids: Optional[list] = None, *, commit: bool = False,
) -> None:
    """Bump version/updated_at for a sale + related customer/medicines (Online or Offline)."""
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'sales', int(sale_id), commit=commit)
    cur = conn.cursor()
    cur.execute('SELECT customer_id FROM sales WHERE id=?', (sale_id,))
    row = cur.fetchone()
    if row and row[0]:
        bump_row_meta(conn, 'customers', int(row[0]), commit=commit)
    ids = list(medicine_ids or [])
    if not ids:
        cur.execute('SELECT medicine_id FROM sales_items WHERE sale_id=?', (sale_id,))
        ids = [r[0] for r in cur.fetchall()]
    seen: set[int] = set()
    for mid in ids:
        try:
            i = int(mid)
        except (TypeError, ValueError):
            continue
        if i <= 0 or i in seen:
            continue
        seen.add(i)
        bump_row_meta(conn, 'medicines', i, commit=commit)


def stamp_purchase_meta(
    conn, purchase_id: int, *, extra_supplier_ids: Optional[list] = None, commit: bool = False,
) -> None:
    """Bump version/updated_at for a purchase + related suppliers/medicines."""
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'purchases', int(purchase_id), commit=commit)
    cur = conn.cursor()
    cur.execute('SELECT supplier_id FROM purchases WHERE id=?', (purchase_id,))
    row = cur.fetchone()
    supplier_id = int(row[0]) if row and row[0] else 0
    if supplier_id:
        bump_row_meta(conn, 'suppliers', supplier_id, commit=commit)
    for sid in extra_supplier_ids or []:
        try:
            i = int(sid)
        except (TypeError, ValueError):
            continue
        if i > 0 and i != supplier_id:
            bump_row_meta(conn, 'suppliers', i, commit=commit)
    cur.execute(
        'SELECT DISTINCT medicine_id FROM purchase_items WHERE purchase_id=?',
        (purchase_id,),
    )
    for (mid,) in cur.fetchall():
        try:
            bump_row_meta(conn, 'medicines', int(mid), commit=commit)
        except Exception:
            pass


def push_sale_now(
    conn, sale_id: int, medicine_ids: Optional[list] = None, *, commit_meta: bool = False,
) -> bool:
    """Synchronous server push for a sale (Online server-first)."""
    from core import server_live as live

    stamp_sale_meta(conn, sale_id, medicine_ids, commit=commit_meta)
    ids = list(medicine_ids or [])
    if not ids:
        cur = conn.cursor()
        cur.execute('SELECT medicine_id FROM sales_items WHERE sale_id=?', (sale_id,))
        ids = [r[0] for r in cur.fetchall()]
    seen = []
    seen_set: set[int] = set()
    for mid in ids:
        try:
            i = int(mid)
        except (TypeError, ValueError):
            continue
        if i <= 0 or i in seen_set:
            continue
        seen_set.add(i)
        seen.append(i)
    try:
        ok = bool(live.push_sale_bundle(conn, int(sale_id), seen))
    except Exception as exc:
        log.warning('[SYNC][PUSH] push_sale_now failed sale=%s: %s', sale_id, exc)
        try:
            from core.sync_outbox import enqueue

            enqueue(conn, 'sales', int(sale_id), error=str(exc))
        except Exception:
            pass
        return False
    if ok:
        try:
            from core.sync_status import note_collection_change, note_last_sync

            note_collection_change('sales')
            note_collection_change('medicines')
            note_last_sync('push')
        except Exception:
            pass
    else:
        try:
            from core.sync_outbox import enqueue

            enqueue(conn, 'sales', int(sale_id), error='push_sale_bundle returned false')
        except Exception:
            pass
    return ok


def push_purchase_now(
    conn, purchase_id: int, *, commit_meta: bool = False,
    extra_supplier_ids: Optional[list] = None,
) -> bool:
    from core import server_live as live

    stamp_purchase_meta(
        conn, purchase_id, extra_supplier_ids=extra_supplier_ids, commit=commit_meta,
    )
    try:
        ok = bool(
            live.push_purchase_bundle(
                conn,
                int(purchase_id),
                extra_supplier_ids=extra_supplier_ids,
            )
        )
    except Exception as exc:
        log.warning('push_purchase_now failed purchase=%s: %s', purchase_id, exc)
        try:
            from core.sync_outbox import enqueue

            enqueue(conn, 'purchases', int(purchase_id), error=str(exc))
        except Exception:
            pass
        return False
    if not ok:
        try:
            from core.sync_outbox import enqueue

            enqueue(
                conn, 'purchases', int(purchase_id),
                error='push_purchase_bundle returned false',
            )
        except Exception:
            pass
        return False
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change('purchases')
        note_collection_change('medicines')
        note_collection_change('suppliers')
        note_last_sync('push')
    except Exception:
        pass
    return True


def after_save(conn, entity: str, entity_id: int) -> None:
    """Legacy hook — Online mode must use commit_after_cloud_push (server first)."""
    return


def after_sale_saved(
    conn, sale_id: int, medicine_ids: Optional[list] = None, *, already_pushed: bool = False,
) -> None:
    try:
        from core.sync_status import note_collection_change

        note_collection_change('sales')
        note_collection_change('medicines')
        note_collection_change('customers')
    except Exception:
        pass
    try:
        from core.page_refresh import refresh_after_sale
        refresh_after_sale()
    except Exception:
        pass
    if is_online_mode() and not already_pushed:
        log.warning(
            'after_sale_saved sale=%s without already_pushed — '
            'caller should use commit_after_cloud_push',
            sale_id,
        )


def after_purchase_saved(
    conn, purchase_id: int, *, already_pushed: bool = False,
) -> None:
    try:
        from core.sync_status import note_collection_change

        note_collection_change('purchases')
        note_collection_change('medicines')
        note_collection_change('suppliers')
    except Exception:
        pass
    try:
        from core.page_refresh import refresh_after_purchase
        refresh_after_purchase()
    except Exception:
        pass
    if is_online_mode() and not already_pushed:
        log.warning(
            'after_purchase_saved purchase=%s without already_pushed — '
            'caller should use commit_after_cloud_push',
            purchase_id,
        )


def after_customer_saved(conn, customer_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    # Always stamp local meta so Offline → Push to Server updates only changed rows.
    bump_row_meta(conn, 'customers', int(customer_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_entity(conn, 'customers', int(customer_id)):
        if _v3_enqueue(conn, 'customers', int(customer_id)):
            return
        raise CloudSaveError("Customer could not be saved to server.")
    _v3_emit('customers')


def after_supplier_saved(conn, supplier_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'suppliers', int(supplier_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_entity(conn, 'suppliers', int(supplier_id)):
        if _v3_enqueue(conn, 'suppliers', int(supplier_id)):
            return
        raise CloudSaveError("Supplier could not be saved to server.")
    _v3_emit('suppliers')


def after_supplier_deleted(conn, supplier_id: int) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.delete_remote('suppliers', int(supplier_id)):
        raise CloudSaveError("Supplier delete could not be saved to server.")


def after_customer_deleted(conn, customer_id: int) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.delete_remote('customers', int(customer_id)):
        raise CloudSaveError("Customer delete could not be saved to server.")


def after_medicine_saved(conn, medicine_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'medicines', int(medicine_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_entity(conn, 'medicines', int(medicine_id)):
        if _v3_enqueue(conn, 'medicines', int(medicine_id)):
            return
        raise CloudSaveError("Medicine could not be saved to server.")
    _v3_emit('medicines')


def after_medicines_hidden(conn, medicine_ids: list) -> None:
    """Stamp meta locally; push is_hidden in one Node collection POST."""
    ids = [int(x) for x in (medicine_ids or []) if x]
    if not ids:
        return
    from core.sync_meta_bump import bump_row_meta

    for mid in ids:
        bump_row_meta(conn, 'medicines', int(mid))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_entities(conn, 'medicines', ids):
        raise CloudSaveError("Could not sync hidden medicines to server.")


def after_doctor_saved(conn, doctor_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'doctors', int(doctor_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_entity(conn, 'doctors', int(doctor_id)):
        raise CloudSaveError("Doctor could not be saved to server.")


def after_stock_disposal_saved(conn, disposal_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'stock_disposals', int(disposal_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_stock_disposal(conn, int(disposal_id)):
        raise CloudSaveError("Stock disposal could not be saved to server.")


def after_general_product_saved(conn, product_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'general_products', int(product_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_general_product(conn, int(product_id)):
        raise CloudSaveError("General product could not be saved to server.")


def after_customer_payment_saved(conn, payment_id: int, customer_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'customer_payments', int(payment_id))
    if customer_id:
        bump_row_meta(conn, 'customers', int(customer_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError
    ok = live.push_customer_payment_bundle(
        conn, int(payment_id), int(customer_id) if customer_id else None,
    )
    if not ok:
        if _v3_enqueue(conn, 'customer_payments', int(payment_id)):
            return
        raise CloudSaveError("Customer payment could not be saved to server.")
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change('customer_payments')
        note_collection_change('customers')
        note_collection_change('sales')
        note_last_sync('push')
    except Exception:
        pass
    _v3_emit('customer_payments')


def _v3_enqueue(conn, collection: str, local_id: int) -> bool:
    try:
        from core.sync_v3.flags import is_sync_v3_enabled
        if not is_sync_v3_enabled():
            return False
        from core.sync_v3.repositories.payment_return import after_local_write
        from core.sync_v3.repositories.masters import after_master_write

        if collection in (
            'customer_payments', 'supplier_payments',
            'sales_returns', 'purchase_returns',
        ):
            after_local_write(conn, collection, local_id, operation='update')
        else:
            after_master_write(conn, collection, local_id, operation='update')
        return True
    except Exception:
        return False


def _v3_emit(collection: str) -> None:
    try:
        from core.sync_v3.data_change_bus import emit
        emit(collection, local=True)
    except Exception:
        pass


def after_supplier_payment_saved(conn, payment_id: int, supplier_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'supplier_payments', int(payment_id))
    if supplier_id:
        bump_row_meta(conn, 'suppliers', int(supplier_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError
    ok = live.push_supplier_payment_bundle(
        conn, int(payment_id), int(supplier_id) if supplier_id else None,
    )
    if not ok:
        if _v3_enqueue(conn, 'supplier_payments', int(payment_id)):
            return
        raise CloudSaveError("Supplier payment could not be saved to server.")
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change('supplier_payments')
        note_collection_change('suppliers')
        note_collection_change('purchases')
        note_last_sync('push')
    except Exception:
        pass
    _v3_emit('supplier_payments')


def after_sales_return_saved(conn, return_id: int, customer_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'sales_returns', int(return_id))
    if customer_id:
        bump_row_meta(conn, 'customers', int(customer_id))
    cur = conn.cursor()
    cur.execute(
        'SELECT medicine_id FROM sales_return_items WHERE return_id=?',
        (return_id,),
    )
    med_ids = [int(mid) for (mid,) in cur.fetchall() if mid]
    for mid in med_ids:
        bump_row_meta(conn, 'medicines', mid)
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError
    if not live.push_sales_return_bundle(
        conn,
        int(return_id),
        int(customer_id) if customer_id else None,
        med_ids,
    ):
        if _v3_enqueue(conn, 'sales_returns', int(return_id)):
            return
        raise CloudSaveError("Sales return could not be saved to server.")
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change('sales_returns')
        note_collection_change('medicines')
        note_collection_change('customers')
        note_last_sync('push')
    except Exception:
        pass
    _v3_emit('sales_returns')


def after_purchase_return_saved(conn, return_id: int, supplier_id: int) -> None:
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, 'purchase_returns', int(return_id))
    if supplier_id:
        bump_row_meta(conn, 'suppliers', int(supplier_id))
    cur = conn.cursor()
    cur.execute(
        'SELECT medicine_id FROM purchase_return_items WHERE return_id=?',
        (return_id,),
    )
    med_ids = [int(mid) for (mid,) in cur.fetchall() if mid]
    for mid in med_ids:
        bump_row_meta(conn, 'medicines', mid)
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError
    if not live.push_purchase_return_bundle(
        conn,
        int(return_id),
        int(supplier_id) if supplier_id else None,
        med_ids,
    ):
        if _v3_enqueue(conn, 'purchase_returns', int(return_id)):
            return
        raise CloudSaveError("Purchase return could not be saved to server.")
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change('purchase_returns')
        note_collection_change('medicines')
        note_collection_change('suppliers')
        note_last_sync('push')
    except Exception:
        pass
    _v3_emit('purchase_returns')


def after_profile_saved(conn) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_pharmacy_profile(conn):
        raise CloudSaveError("Pharmacy profile could not be saved to server.")
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change('pharmacy_profile')
        note_last_sync('push')
    except Exception:
        pass


def after_shelf_settings_saved(conn) -> None:
    """Push shelf_settings singleton when Online (content-equal skip on server)."""
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_shelf_settings(conn):
        raise CloudSaveError("Shelf settings could not be saved to server.")
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change('shelf_settings')
        note_last_sync('push')
    except Exception:
        pass


def after_shelf_entity_saved(conn, collection: str, local_id: int) -> None:
    """Bump meta + Online-push a rack/section/box layout row."""
    col = str(collection or "").strip().lower()
    if col not in ("racks", "sections", "boxes"):
        return
    from core.sync_meta_bump import bump_row_meta

    bump_row_meta(conn, col, int(local_id))
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate
    ensure_can_mutate()
    if not live.push_entity(conn, col, int(local_id)):
        raise CloudSaveError(f"Shelf {col} could not be saved to server.")
    try:
        from core.sync_status import note_collection_change, note_last_sync

        note_collection_change(col)
        note_last_sync('push')
    except Exception:
        pass


def after_shelf_entity_deleted(collection: str, local_id: int) -> None:
    """Online soft-delete rack/section/box on server after local delete."""
    col = str(collection or "").strip().lower()
    if col not in ("racks", "sections", "boxes"):
        return
    after_entity_deleted(col, int(local_id))


def _run_async(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


def after_entity_deleted(collection: str, doc_id: int) -> None:
    """Soft-delete on server after local SQLite delete."""
    if not is_online_mode():
        return

    def _delete():
        try:
            from core import server_live as live
            live.delete_remote(collection, int(doc_id))
        except Exception:
            pass

    _run_async(_delete)


def after_sale_deleted(
    conn,
    sale_id: int,
    customer_id: Optional[int] = None,
    medicine_ids: Optional[list] = None,
) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM sales WHERE id=?', (int(sale_id),))
        if cur.fetchone():
            ok = live.push_sale_bundle(conn, int(sale_id), medicine_ids)
        else:
            ok = live.delete_remote('sales', int(sale_id))
            parts = {}
            if customer_id:
                parts['customers'] = [int(customer_id)]
            if medicine_ids:
                parts['medicines'] = list(medicine_ids)
            if parts:
                live.push_related_bundle(conn, parts)
        if not ok:
            raise CloudSaveError("Sale delete could not be saved to server.")
        try:
            from core.sync_status import note_push, note_last_sync, note_collection_change
            note_push('sales', sale_id)
            note_collection_change('sales')
            note_collection_change('medicines')
            note_last_sync('push')
        except Exception:
            pass
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_purchase_deleted(
    conn,
    purchase_id: int,
    supplier_id: Optional[int] = None,
    medicine_ids: Optional[list] = None,
) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM purchases WHERE id=?', (int(purchase_id),))
        if cur.fetchone():
            ok = live.push_purchase_bundle(conn, int(purchase_id))
        else:
            ok = live.delete_remote('purchases', int(purchase_id))
            parts = {}
            if supplier_id:
                parts['suppliers'] = [int(supplier_id)]
            if medicine_ids:
                parts['medicines'] = list(medicine_ids)
            if parts:
                live.push_related_bundle(conn, parts)
        if not ok:
            raise CloudSaveError("Purchase delete could not be saved to server.")
        try:
            from core.sync_status import note_push, note_last_sync, note_collection_change
            note_push('purchases', purchase_id)
            note_collection_change('purchases')
            note_collection_change('medicines')
            note_last_sync('push')
        except Exception:
            pass
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_medicine_deleted(medicine_id: int) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        if not live.delete_remote('medicines', int(medicine_id)):
            raise CloudSaveError("Medicine delete could not be saved to server.")
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_customer_payment_deleted(conn, payment_id: int, customer_id: int) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        if not live.delete_remote('customer_payments', int(payment_id)):
            raise CloudSaveError("Payment delete could not be saved to server.")
        if customer_id:
            live.push_entity(conn, 'customers', int(customer_id))
        try:
            from core.sync_status import note_collection_change, note_last_sync

            note_collection_change('customer_payments')
            note_collection_change('customers')
            note_collection_change('sales')
            note_last_sync('push')
        except Exception:
            pass
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_supplier_payment_deleted(conn, payment_id: int, supplier_id: int) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        if not live.delete_remote('supplier_payments', int(payment_id)):
            raise CloudSaveError("Payment delete could not be saved to server.")
        if supplier_id:
            live.push_entity(conn, 'suppliers', int(supplier_id))
        try:
            from core.sync_status import note_collection_change, note_last_sync

            note_collection_change('supplier_payments')
            note_collection_change('suppliers')
            note_collection_change('purchases')
            note_last_sync('push')
        except Exception:
            pass
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_sales_return_deleted(
    conn,
    return_id: int,
    customer_id: Optional[int] = None,
    medicine_ids: Optional[list] = None,
) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT id FROM sales_returns WHERE id=?',
            (int(return_id),),
        )
        if cur.fetchone():
            ok = live.push_sales_return_bundle(
                conn, int(return_id), customer_id, medicine_ids,
            )
        else:
            ok = live.delete_remote('sales_returns', int(return_id))
            parts = {}
            if customer_id:
                parts['customers'] = [int(customer_id)]
            if medicine_ids:
                parts['medicines'] = list(medicine_ids)
            if parts:
                live.push_related_bundle(conn, parts)
        if not ok:
            raise CloudSaveError("Sales return delete could not be saved to server.")
        try:
            from core.sync_status import note_push, note_last_sync, note_collection_change

            note_push('sales_returns', return_id)
            note_collection_change('sales_returns')
            note_collection_change('medicines')
            note_collection_change('customers')
            note_last_sync('push')
        except Exception:
            pass
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_purchase_return_deleted(
    conn,
    return_id: int,
    supplier_id: Optional[int] = None,
    medicine_ids: Optional[list] = None,
) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT id FROM purchase_returns WHERE id=?',
            (int(return_id),),
        )
        if cur.fetchone():
            ok = live.push_purchase_return_bundle(
                conn, int(return_id), supplier_id, medicine_ids,
            )
        else:
            ok = live.delete_remote('purchase_returns', int(return_id))
            parts = {}
            if supplier_id:
                parts['suppliers'] = [int(supplier_id)]
            if medicine_ids:
                parts['medicines'] = list(medicine_ids)
            if parts:
                live.push_related_bundle(conn, parts)
        if not ok:
            raise CloudSaveError("Purchase return delete could not be saved to server.")
        try:
            from core.sync_status import note_push, note_last_sync, note_collection_change

            note_push('purchase_returns', return_id)
            note_collection_change('purchase_returns')
            note_collection_change('medicines')
            note_collection_change('suppliers')
            note_last_sync('push')
        except Exception:
            pass
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_villages_saved(conn) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        if not live.push_dropdowns(conn):
            raise CloudSaveError("Villages / dropdowns could not be saved to server.")
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_layout_saved(conn=None) -> None:
    """Push med_types / schedules after layout settings save (Online)."""
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        # conn unused — layout is file-based; villages still come from SQLite via push_dropdowns
        if conn is None:
            from core.db_utils import open_store_db
            c = open_store_db()
            try:
                ok = live.push_dropdowns(c)
            finally:
                try:
                    c.close()
                except Exception:
                    pass
        else:
            ok = live.push_dropdowns(conn)
        if not ok:
            raise CloudSaveError("Medicine layout could not be saved to server.")
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_doctor_deleted(conn, doctor_id: int) -> None:
    if not is_online_mode():
        return
    from core import server_live as live
    from core.online_guard import CloudSaveError, ensure_can_mutate

    ensure_can_mutate()
    try:
        if not live.delete_remote('doctors', int(doctor_id)):
            raise CloudSaveError("Doctor delete could not be saved to server.")
        live.push_dropdowns(conn)
    except CloudSaveError:
        raise
    except Exception as exc:
        raise CloudSaveError(str(exc)) from exc


def after_store_registry_changed() -> None:
    """Server stores are managed via admin/pair — no Server registry publish."""
    return


def ensure_online_store_link() -> str:
    """Ensure active store exists on server and return its SC- pairing key."""
    if not is_online_mode():
        return ''
    try:
        from core import server_live as live
        session = live.ensure_active_store_on_server()
        return (session.get('android_key') or '').strip()
    except Exception as exc:
        log.warning('ensure_online_store_link: %s', exc)
        return ''


def should_run_drive_backup() -> bool:
    """Whether the automatic (open / hourly / close) Drive backup may run.

    This used to be `is_offline_mode()`, which meant Online stores got NO automatic
    backup at all -- their newest Drive copy was whenever someone last pressed
    Backup Now by hand. One live store went 20 days and 268 sales without one.
    _do_backup now captures Online stores from the server (and aborts rather than
    uploading a stale local file), so both modes are safe to back up automatically.
    """
    return True


def run_bootstrap_if_needed(
    conn,
    progress_cb=None,
) -> tuple[bool, str]:
    """Run first-time full merge when due. Returns (ran, message)."""
    from core.sync_bootstrap import should_run_bootstrap, mark_bootstrap_done
    if not should_run_bootstrap(conn):
        return False, 'Bootstrap not needed.'
    from core import server_live as live
    ok, msg, _uploaded = live.run_bootstrap(conn, progress_cb=progress_cb)
    if not ok:
        return True, msg
    mark_bootstrap_done()
    return True, msg


def start_online_sync(
    conn,
    on_change=None,
    db_path=None,
    *,
    adopt_server_head: bool = False,
    hints_only: bool = True,
) -> bool:
    """Start Online live sync. Default hints_only=True (server-only Online)."""
    if not is_online_mode():
        return False
    try:
        from core import server_api as api
        from core import server_live as live
        from core.sync_engine import start_sync_engine

        if not api.health_ok(timeout=3.0):
            log.warning('Server health check failed — cannot start online sync')
            return False
        ensure_online_store_link()
        path = db_path
        if not path and conn is not None:
            try:
                path = getattr(conn, 'db_path', None) or None
            except Exception:
                path = None

        # Server-only Online: WS hints only — no Sync V3 bootstrap / outbox / SQLite apply
        if hints_only or adopt_server_head:
            try:
                live.stop_poller()
            except Exception:
                pass
            try:
                from core.sync_v3.bootstrap import _set_state as _v3_set

                _v3_set("ready")
            except Exception:
                pass
            ok = start_sync_engine(
                conn,
                db_path=path,
                on_change=on_change,
                adopt_server_head=True,
                hints_only=True,
            )
            if ok:
                _ensure_engine_watchdog(conn, on_change, path, hints_only=True)
                try:
                    from core.online_catalog import prefetch_hot

                    prefetch_hot()
                except Exception:
                    pass
                return True
            log.warning('SyncEngine hints-only failed to start')
            return False

        # Legacy path (should not run for current Online)
        log.warning('start_online_sync legacy SQLite path — unexpected for server-only Online')
        return False
    except Exception as exc:
        log.warning('start_online_sync failed: %s', exc)
        return False


_poller_watchdog_started = False
_engine_watchdog_started = False


def _ensure_poller_watchdog(conn, on_change=None, db_path=None) -> None:
    """Restart server poller if the thread dies while Online mode stays on."""
    global _poller_watchdog_started
    if _poller_watchdog_started:
        return
    _poller_watchdog_started = True

    def _watch() -> None:
        import time
        from core import server_api as api
        from core import server_live as live
        from core.revision_sync_flags import is_revision_sync_enabled

        while True:
            time.sleep(15.0)
            try:
                if not is_online_mode():
                    continue
                if is_revision_sync_enabled():
                    continue
                if live.is_poller_alive():
                    continue
                log.warning('DEPRECATED watermark poller stopped — restarting')
                if not api.health_ok(timeout=3.0):
                    continue
                live.start_poller(conn, on_change=on_change, db_path=db_path)
            except Exception as exc:
                log.debug('poller watchdog: %s', exc)

    threading.Thread(
        target=_watch,
        daemon=True,
        name='OnlinePollerWatchdog',
    ).start()


def _ensure_engine_watchdog(conn, on_change=None, db_path=None, *, hints_only: bool = True) -> None:
    """Restart SyncEngine if it dies while Online mode stays on."""
    global _engine_watchdog_started
    if _engine_watchdog_started:
        return
    _engine_watchdog_started = True

    def _watch() -> None:
        import time
        from core import server_api as api
        from core.sync_engine import is_sync_engine_alive, start_sync_engine

        while True:
            time.sleep(15.0)
            try:
                if not is_online_mode():
                    continue
                if is_sync_engine_alive():
                    continue
                log.warning('SyncEngine stopped — restarting (hints_only)')
                if not api.health_ok(timeout=3.0):
                    continue
                start_sync_engine(
                    conn,
                    db_path=db_path,
                    on_change=on_change,
                    adopt_server_head=True,
                    hints_only=hints_only,
                )
            except Exception as exc:
                log.debug('engine watchdog: %s', exc)

    threading.Thread(
        target=_watch,
        daemon=True,
        name='SyncEngineWatchdog',
    ).start()


def stop_online_sync() -> None:
    try:
        from core.sync_engine import stop_sync_engine

        stop_sync_engine()
    except Exception:
        pass
    try:
        from core import server_live as live
        live.stop_poller()
    except Exception:
        pass
