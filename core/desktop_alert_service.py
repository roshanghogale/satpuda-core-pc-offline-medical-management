"""Alert & Monitoring actions for the Tauri desktop shell."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from core.desktop_settings_service import get_alerts


def alert_action(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    action = str(data.get("action") or "").strip().lower()
    handlers = {
        "row_action": _row_action,
        "dismiss_row": _dismiss_row,
        "dismiss_all_expired": _dismiss_all_expired,
        "dismiss_all_out_of_stock": _dismiss_all_out_of_stock,
        "bulk_reorder": _bulk_reorder,
        "bulk_return": _bulk_return,
    }
    fn = handlers.get(action)
    if not fn:
        return {"ok": False, "error": f"Unknown alert action: {action}"}
    return fn(conn, data)


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def online_return_navigation(
    conn: sqlite3.Connection, name: str, batch: str, disposal: dict[str, Any]
) -> dict[str, Any]:
    """Online "Return" on one expired / near-expiry alert row.

    The offline path finds the row's batch with collect_return_candidates and its
    bill with build_bulk_return_by_purchase -- both SQL, and Online the engine's
    connection is an empty :memory: shell. So they found nothing, and Return fell
    through to a write-off form prefilled with a quantity of 0. The candidates
    here come from the store's shelf (the alert sections' own rows) and the
    bills from the server, capped by what each bill still allows.
    """
    from core.desktop_returns_service import bulk_purchase_prefill
    from core.stock_disposal_service import collect_return_candidates_online

    try:
        items = [
            i
            for i in collect_return_candidates_online(
                conn, include_expired=True, include_near_expiry=True
            )
            if (i.get("medicine_name") or "").strip() == name
            and (not batch or (i.get("batch_no") or "").strip() == batch)
        ]
    except Exception as exc:
        return {"ok": False, "error": f"Could not read the store's stock: {exc}"}
    if items:
        enriched = bulk_purchase_prefill(conn, items=items)
        if not enriched.get("ok"):
            return {"ok": False, "error": enriched.get("error") or "Could not build the return."}
        if enriched.get("unread_bills"):
            # A bill left out is a return the shop was not shown; say so here,
            # where it can be read, rather than open a screen missing it.
            return {"ok": False, "error": enriched.get("message") or "Could not read the purchase bills."}
        if not enriched.get("empty"):
            return {
                "ok": True,
                "navigate": {
                    "page": "returns",
                    "returnsTab": "bulk",
                    "returnsBulkPrefill": enriched,
                },
            }
    return {
        "ok": True,
        "navigate": {
            "page": "returns",
            "returnsTab": "disposal",
            "disposalPrefill": disposal,
        },
    }


def _row_action(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    section = str(data.get("section") or "").strip()
    values = data.get("values") or []
    if not isinstance(values, list):
        values = list(values) if values else []
    row = tuple(values)

    if section in ("low_stock", "out_of_stock"):
        prefill = _prefill_reorder(conn, section, row)
        return {
            "ok": True,
            "navigate": {
                "page": "settings",
                "settingsTab": "reorder",
                "settingsToggle": "by_supplier",
                "reorderMedicinePrefill": {
                    "medicine_name": prefill.get("medicine_name", ""),
                    "pack_size": prefill.get("pack_size", ""),
                    "quantity": float(prefill.get("suggested_qty") or 0),
                    "unit_price": float(prefill.get("unit_price") or 0),
                },
            },
        }

    if section in ("expired", "near_expiry"):
        if _online():
            return online_return_navigation(
                conn,
                str(row[0] or "").strip(),
                str(row[1] or "").strip() if len(row) > 1 else "",
                _prefill_disposal(section, row),
            )
        from core.stock_disposal_service import (
            build_bulk_return_by_purchase,
            collect_return_candidates,
        )
        from core.desktop_returns_service import bulk_purchase_prefill

        name = str(row[0] or "").strip()
        batch = str(row[1] or "").strip() if len(row) > 1 else ""
        items = [
            i
            for i in collect_return_candidates(
                conn, include_expired=True, include_near_expiry=True
            )
            if (i.get("medicine_name") or "").strip() == name
            and (not batch or (i.get("batch_no") or "").strip() == batch)
        ]
        raw = build_bulk_return_by_purchase(conn, items=items)
        if raw.get("purchase_groups") or raw.get("writeoff_lines"):
            enriched = bulk_purchase_prefill(conn, items=items)
            return {
                "ok": True,
                "navigate": {
                    "page": "returns",
                    "returnsTab": "bulk",
                    "returnsBulkPrefill": enriched,
                },
            }
        disposal = _prefill_disposal(section, row)
        return {
            "ok": True,
            "navigate": {
                "page": "returns",
                "returnsTab": "disposal",
                "disposalPrefill": disposal,
            },
        }

    return {"ok": False, "error": f"No row action for section: {section}"}


def _prefill_reorder(
    conn: sqlite3.Connection, section_key: str, values: tuple[Any, ...]
) -> dict[str, Any]:
    from core.reorder_service import (
        current_stock_for_medicine,
        min_stock_level,
        suggest_order_quantity,
    )

    if section_key == "low_stock":
        name = str(values[0] or "")
        stock = float(values[1] or 0)
        pack = str(values[2] or "")
        med_type = "Others"
        return {
            "medicine_name": name,
            "pack_size": pack,
            "med_type": med_type,
            "current_stock": stock,
            "unit_price": 0.0,
            "suggested_qty": suggest_order_quantity(
                conn, name, med_type, stock, pack
            ),
        }
    name = str(values[0] or "")
    pack = str(values[1] or "")
    rate = float(values[3] or 0) if len(values) > 3 else 0.0
    med_type = str(values[4] or "Others") if len(values) > 4 else "Others"
    stock = current_stock_for_medicine(conn, name, pack)
    return {
        "medicine_name": name,
        "pack_size": pack,
        "med_type": med_type,
        "current_stock": stock,
        "unit_price": rate,
        "min_stock": min_stock_level(conn, med_type),
        "suggested_qty": suggest_order_quantity(conn, name, med_type, stock, pack),
    }


def _prefill_disposal(section_key: str, values: tuple[Any, ...]) -> dict[str, Any]:
    if section_key == "expired":
        name, batch, expiry, qty = values[0], values[1], values[2], values[3]
    else:
        name, batch, expiry, qty = values[0], values[1], values[2], values[4]
    return {
        "from_alert": True,
        "medicine_name": str(name or ""),
        "batch_no": str(batch or ""),
        "expiry_date": str(expiry or ""),
        "available_qty": float(qty or 0),
    }


def _medicine_ids_for_dismiss(
    conn: sqlite3.Connection, section_key: str, values: tuple[Any, ...]
) -> list[int]:
    cur = conn.cursor()
    name = str(values[0] or "").strip()
    if not name:
        return []
    if section_key == "low_stock":
        cur.execute(
            "SELECT id FROM medicines "
            "WHERE TRIM(name)=TRIM(?) AND COALESCE(is_hidden, 0) = 0",
            (name,),
        )
    elif section_key == "out_of_stock":
        pack = str(values[1] or "").strip()
        cur.execute(
            "SELECT id FROM medicines "
            "WHERE TRIM(name)=TRIM(?) AND TRIM(COALESCE(unit, ''))=TRIM(?) "
            "AND COALESCE(is_hidden, 0) = 0",
            (name, pack),
        )
    elif section_key in ("expired", "near_expiry"):
        batch = str(values[1] or "").strip()
        cur.execute(
            "SELECT id FROM medicines "
            "WHERE TRIM(name)=TRIM(?) AND TRIM(COALESCE(batch_no, ''))=TRIM(?) "
            "AND COALESCE(is_hidden, 0) = 0",
            (name, batch),
        )
    else:
        return []
    return [int(r[0]) for r in cur.fetchall()]


def _normalize_pack(value: Any) -> str:
    try:
        from core.alert_monitoring_service import _normalize_pack_size

        return str(_normalize_pack_size(value) or "").strip()
    except Exception:
        return str(value or "").strip()


def _online_alert_candidates(section: str, row: tuple) -> list[int]:
    """Medicine ids behind one alert row, matched the way the row was built.

    The alert rows come from the online catalog, but every dismiss handler below
    went straight to SQLite -- which in Online mode is an empty :memory: shell.
    They hid nothing and still answered {"ok": True}, so "Remove from list" looked
    like it worked and the row was still there on the next refresh.
    """
    from core.online_catalog import medicines as oc_medicines

    name = str((row[0] if len(row) > 0 else "") or "").strip().upper()
    if not name:
        return []
    second = str((row[1] if len(row) > 1 else "") or "").strip()
    out: list[int] = []
    for m in oc_medicines() or []:
        if not isinstance(m, dict):
            continue
        if m.get("is_hidden") or m.get("deleted"):
            continue
        if str(m.get("name") or "").strip().upper() != name:
            continue
        if section == "out_of_stock":
            if _normalize_pack(m.get("unit")) != _normalize_pack(second):
                continue
        elif section in ("expired", "near_expiry"):
            batch = str(m.get("batch_no") or m.get("batch") or "").strip()
            if batch.upper() != second.upper():
                continue
        try:
            mid = int(m.get("id") or m.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid > 0:
            out.append(mid)
    return out


def _hide_online_ids(ids: list[int]) -> int:
    from core.desktop_inventory_service import _hide_medicine_online

    hidden = 0
    for mid in ids:
        try:
            if _hide_medicine_online(int(mid)):
                hidden += 1
        except Exception:
            continue
    if hidden:
        try:
            from core.online_catalog import invalidate

            invalidate("medicines")
        except Exception:
            pass
    return hidden


def _dismiss_row(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from core.medicine_visibility import (
        hide_medicines_by_name,
        hide_medicines_by_name_and_batch,
        hide_medicines_by_name_and_pack,
    )
    from core.sync_coordinator import after_medicines_hidden

    section = str(data.get("section") or "").strip()
    values = data.get("values") or []
    if not isinstance(values, list):
        values = list(values) if values else []
    row = tuple(values)

    if section not in ("low_stock", "out_of_stock", "expired", "near_expiry"):
        return {"ok": False, "error": f"Cannot dismiss section: {section}"}

    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        ids = _online_alert_candidates(section, row)
        if not ids:
            return {
                "ok": False,
                "error": "Could not match that row to a medicine. Refresh and try again.",
            }
        hidden = _hide_online_ids(ids)
        if not hidden:
            return {"ok": False, "error": "Server did not accept the change."}
        return {
            "ok": True,
            "alerts": get_alerts(conn),
            "message": f"Removed {hidden} batch(es) from list.",
        }

    sync_ids = _medicine_ids_for_dismiss(conn, section, row)
    if section == "low_stock":
        name = str(row[0] or "").strip()
        if not name:
            return {"ok": False, "error": "Missing medicine name."}
        hide_medicines_by_name(conn, name)
    elif section == "out_of_stock":
        name = str(row[0] or "").strip()
        pack = str(row[1] or "").strip()
        if not name:
            return {"ok": False, "error": "Missing medicine name."}
        hide_medicines_by_name_and_pack(conn, name, pack)
    elif section in ("expired", "near_expiry"):
        name = str(row[0] or "").strip()
        batch = str(row[1] or "").strip()
        if not name:
            return {"ok": False, "error": "Missing medicine name."}
        hide_medicines_by_name_and_batch(conn, name, batch)
    else:
        return {"ok": False, "error": f"Cannot dismiss section: {section}"}

    after_medicines_hidden(conn, sync_ids)
    conn.commit()
    return {"ok": True, "alerts": get_alerts(conn), "message": "Removed from list."}


def _dismiss_all_expired(conn: sqlite3.Connection, _data: dict[str, Any]) -> dict[str, Any]:
    from core.medicine_visibility import hide_all_expired_medicines
    from core.sync_coordinator import after_medicines_hidden

    today = date.today()
    from core.batch_visibility import expiry_cutoff

    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        from core.online_catalog import medicines as oc_medicines

        ids = []
        for m in oc_medicines() or []:
            if not isinstance(m, dict) or m.get("is_hidden") or m.get("deleted"):
                continue
            if float(m.get("stock_qty") or 0) <= 0:
                continue
            cutoff = expiry_cutoff(
                str(m.get("expiry_date") or m.get("expiry") or "")
            )
            if not cutoff or cutoff >= today:
                continue
            try:
                ids.append(int(m.get("id") or m.get("local_id") or 0))
            except (TypeError, ValueError):
                continue
        hidden = _hide_online_ids([i for i in ids if i > 0])
        return {
            "ok": bool(hidden),
            "alerts": get_alerts(conn),
            "message": (
                f"Removed {hidden} expired batch(es) from lists."
                if hidden
                else "No expired batches to remove."
            ),
        }

    cur = conn.cursor()
    cur.execute(
        "SELECT id, COALESCE(expiry_date, '') FROM medicines "
        "WHERE COALESCE(is_hidden, 0) = 0 AND COALESCE(stock_qty, 0) > 0"
    )
    # Month-only expiries run to the month end; matches expiry_cutoff elsewhere.
    sync_ids = [
        int(med_id)
        for med_id, expiry_raw in cur.fetchall()
        if (cut := expiry_cutoff(expiry_raw)) and cut < today
    ]
    count = hide_all_expired_medicines(conn)
    after_medicines_hidden(conn, sync_ids)
    conn.commit()
    return {
        "ok": True,
        "alerts": get_alerts(conn),
        "message": f"Removed {count} expired batch(es) from lists.",
    }


def _dismiss_all_out_of_stock(
    conn: sqlite3.Connection, _data: dict[str, Any]
) -> dict[str, Any]:
    from core.medicine_visibility import hide_all_out_of_stock_medicines
    from core.sync_coordinator import after_medicines_hidden

    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        from core.online_catalog import medicines as oc_medicines

        # Group by name and judge on the SUMMED stock, the way
        # hide_all_out_of_stock_medicines does -- hiding per-batch zeros would
        # take away one empty batch of a medicine that still has stock elsewhere.
        by_name: dict[str, list[dict]] = {}
        for m in oc_medicines() or []:
            if not isinstance(m, dict) or m.get("is_hidden") or m.get("deleted"):
                continue
            by_name.setdefault(str(m.get("name") or "").strip().upper(), []).append(m)
        ids: list[int] = []
        for rows in by_name.values():
            if sum(float(r.get("stock_qty") or 0) for r in rows) > 0:
                continue
            for r in rows:
                try:
                    mid = int(r.get("id") or r.get("local_id") or 0)
                except (TypeError, ValueError):
                    continue
                if mid > 0:
                    ids.append(mid)
        hidden = _hide_online_ids(ids)
        return {
            "ok": bool(hidden),
            "alerts": get_alerts(conn),
            "message": (
                f"Removed {hidden} out-of-stock batch(es) from lists."
                if hidden
                else "No out-of-stock batches to remove."
            ),
        }

    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM medicines "
        "WHERE COALESCE(stock_qty, 0) <= 0 AND COALESCE(is_hidden, 0) = 0"
    )
    sync_ids = [int(r[0]) for r in cur.fetchall()]
    count = hide_all_out_of_stock_medicines(conn)
    after_medicines_hidden(conn, sync_ids)
    conn.commit()
    return {
        "ok": True,
        "alerts": get_alerts(conn),
        "message": f"Removed {count} out-of-stock batch(es) from lists.",
    }


def _bulk_reorder(conn: sqlite3.Connection, _data: dict[str, Any]) -> dict[str, Any]:
    from core.reorder_service import collect_reorder_candidates

    try:
        # Online this reads the store's shelf (reorder_service's online branch);
        # an unreadable store is an error, not "nothing to reorder".
        candidates = collect_reorder_candidates(conn)
    except Exception as exc:
        return {"ok": False, "error": f"Could not read the store's stock: {exc}"}
    if not candidates:
        return {
            "ok": False,
            "error": "No low or out-of-stock medicines to reorder.",
        }
    return {
        "ok": True,
        "navigate": {
            "page": "settings",
            "settingsTab": "reorder",
            "settingsToggle": "new",
            "bulkReorderLoad": True,
        },
    }


def _bulk_return(conn: sqlite3.Connection, _data: dict[str, Any]) -> dict[str, Any]:
    from core.desktop_returns_service import bulk_purchase_prefill

    if _online():
        # The batches the popup listed (the alert sections' own rows), not the
        # bulk builder's separate catalog scan with its own horizon.
        from core.stock_disposal_service import collect_return_candidates_online

        try:
            items = collect_return_candidates_online(
                conn, include_expired=True, include_near_expiry=True
            )
        except Exception as exc:
            return {"ok": False, "error": f"Could not read the store's stock: {exc}"}
        if not items:
            return {"ok": False, "error": "No near-expiry or expired stock to return."}
        enriched = bulk_purchase_prefill(conn, items=items)
    else:
        enriched = bulk_purchase_prefill(
            conn, include_expired=True, include_near_expiry=True
        )
    if not enriched.get("ok", True):
        # Online a failed read answered ok:False, had no "empty" key, and the
        # popup closed onto a bulk tab with nothing on it.
        return {"ok": False, "error": enriched.get("error") or "Could not build the return list."}
    if enriched.get("unread_bills"):
        return {"ok": False, "error": enriched.get("message") or "Could not read the purchase bills."}
    if enriched.get("empty"):
        return {"ok": False, "error": "No near-expiry or expired stock to return."}
    return {
        "ok": True,
        "navigate": {
            "page": "returns",
            "returnsTab": "bulk",
            "returnsBulkPrefill": enriched,
        },
    }
