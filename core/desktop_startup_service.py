"""Startup alert popup payload for the Tauri desktop shell."""
from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
from typing import Any


def get_startup_alerts(conn, *, force: bool = False, recheck: bool = False) -> dict[str, Any]:
    """The popup payload.

    force   -- "Test the popup now": ignores on/off and Skip Today, marks nothing seen.
    recheck -- the while-running check: only rows not already shown today, and
               only when a re-check interval is set.
    A failed read (Online: the store) is {"ok": False, "error": ...}, never an
    empty list -- an empty popup is exactly what a shop with a clean shelf sees.
    """
    # The Tk-free gatherer. Importing core.startup_alerts here pulled in
    # tkinter, which the packaged engine does not ship, so this endpoint raised
    # ModuleNotFoundError on every launch and no startup warning was ever shown.
    from core import startup_alerts_prefs as ap
    from core.startup_alerts_data import collect_startup_alerts
    from core.sync_prefs import is_online_mode

    prefs = ap.load_alert_popup_prefs()
    base = {"recheck_minutes": prefs["recheck_minutes"], "recheck": bool(recheck)}
    if not force:
        if not prefs["enabled"] or prefs["snoozed_today"]:
            return {"ok": True, "show": False, "tabs": [], **base}
        if recheck and prefs["recheck_minutes"] <= 0:
            return {"ok": True, "show": False, "tabs": [], **base}
    # Offline: own SQLite connection so alert SQL does not lock the API conn.
    db_path = None
    if not is_online_mode():
        try:
            from core.background_workers import db_path_from_conn

            db_path = db_path_from_conn(conn) or None
        except Exception:
            db_path = None
    try:
        tabs = collect_startup_alerts(
            conn, db_path=db_path, categories=prefs["categories"]
        )
    except Exception as exc:
        return {
            "ok": False,
            "show": False,
            "tabs": [],
            "error": str(exc).strip() or exc.__class__.__name__,
            **base,
        }
    if recheck and not force:
        tabs = ap.unseen_alerts(tabs)
    if tabs and not force:
        ap.mark_alerts_seen(tabs)
    return {"ok": True, "show": bool(tabs), "tabs": tabs, **base}


def get_alert_prefs(conn) -> dict[str, Any]:
    """Settings -> Alert & Monitoring -> Popup & Thresholds."""
    from core.startup_alerts_prefs import load_alert_popup_prefs

    out: dict[str, Any] = dict(load_alert_popup_prefs())
    try:
        from core.desktop_settings_service import get_thresholds

        out["thresholds"] = get_thresholds(conn) if conn is not None else None
    except Exception:
        out["thresholds"] = None
    return out


def save_alert_prefs(conn, data: dict[str, Any]) -> dict[str, Any]:
    from core.startup_alerts_prefs import save_alert_popup_prefs

    save_alert_popup_prefs(data or {})
    return get_alert_prefs(conn)


def snooze_startup_alerts() -> dict[str, Any]:
    from core.startup_alerts_prefs import snooze_startup_alerts_for_today

    snooze_startup_alerts_for_today()
    return {"ok": True}


def startup_alert_action(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    action = str(data.get("action") or "").strip().lower()
    if action == "row_action":
        return _startup_row_action(conn, data)
    if action in ("bulk_reorder", "bulk_return"):
        from core.desktop_alert_service import alert_action

        return alert_action(conn, {"action": action})
    if action == "export_pdf":
        return export_startup_alert_pdf(data)
    return {"ok": False, "error": f"Unknown startup alert action: {action}"}


def _prefill_reorder_from_startup(title: str, values: tuple[Any, ...]) -> dict[str, Any]:
    if title == "Low Stock Alerts":
        name = str(values[0] or "")
        med_type = str(values[1] or "Others")
        pack = str(values[4] or "")
        stock = float(values[3] or 0)
        return {
            "medicine_name": name,
            "pack_size": pack,
            "med_type": med_type,
            "current_stock": stock,
        }
    name = str(values[0] or "")
    pack = str(values[1] or "")
    rate = float(values[3] or 0) if len(values) > 3 else 0.0
    med_type = str(values[4] or "Others") if len(values) > 4 else "Others"
    return {
        "medicine_name": name,
        "pack_size": pack,
        "med_type": med_type,
        "unit_price": rate,
    }


def _prefill_disposal_from_startup(
    conn: sqlite3.Connection, values: tuple[Any, ...]
) -> dict[str, Any]:
    name = str(values[0] or "")
    batch = str(values[2] or "") if len(values) > 2 else ""
    expiry = str(values[3] or "") if len(values) > 3 else ""
    qty = 0.0
    try:
        from core.desktop_alert_service import _online

        if _online():
            # Online the SQL below reads an empty :memory: shell and said 0.
            from core.online_catalog import medicines_for_name

            qty = sum(
                float(m.get("stock_qty") or 0)
                for m in medicines_for_name(name)
                if str(m.get("batch_no") or "").strip() == batch.strip()
                and not m.get("is_hidden")
            )
        else:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(stock_qty),0) FROM medicines "
                "WHERE name=? AND COALESCE(batch_no,'')=?",
                (name, batch),
            )
            row = cur.fetchone()
            qty = float(row[0] or 0) if row else 0.0
    except Exception:
        pass
    return {
        "from_alert": True,
        "medicine_name": name,
        "batch_no": batch,
        "expiry_date": expiry,
        "available_qty": qty,
    }


def _startup_row_action(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title") or "").strip()
    kind = str(data.get("action_kind") or "").strip().lower()
    values = data.get("values") or []
    if not isinstance(values, list):
        values = list(values) if values else []
    row = tuple(values)

    if kind == "reorder":
        from core.reorder_service import (
            current_stock_for_medicine,
            min_stock_level,
            suggest_order_quantity,
        )

        prefill = _prefill_reorder_from_startup(title, row)
        name = prefill.get("medicine_name", "")
        pack = prefill.get("pack_size", "")
        med_type = prefill.get("med_type", "Others")
        if "current_stock" not in prefill or title == "Out of Stock":
            stock = current_stock_for_medicine(conn, name, pack)
            prefill["current_stock"] = stock
        else:
            stock = float(prefill.get("current_stock") or 0)
        prefill.setdefault("min_stock", min_stock_level(conn, med_type))
        prefill.setdefault(
            "suggested_qty",
            suggest_order_quantity(conn, name, med_type, stock, pack),
        )
        return {
            "ok": True,
            "navigate": {
                "page": "settings",
                "settingsTab": "reorder",
                "settingsToggle": "by_supplier",
                "reorderMedicinePrefill": {
                    "medicine_name": name,
                    "pack_size": pack,
                    "quantity": float(prefill.get("suggested_qty") or 0),
                    "unit_price": float(prefill.get("unit_price") or 0),
                },
            },
        }

    if kind == "return":
        from core.desktop_alert_service import _online, online_return_navigation

        if _online():
            # Popup columns: Medicine, Type, Batch, Expiry, Days.
            return online_return_navigation(
                conn,
                str(row[0] or "").strip(),
                str(row[2] or "").strip() if len(row) > 2 else "",
                _prefill_disposal_from_startup(conn, row),
            )
        from core.desktop_returns_service import bulk_purchase_prefill
        from core.stock_disposal_service import (
            build_bulk_return_by_purchase,
            collect_return_candidates,
        )

        name = str(row[0] or "").strip()
        batch = str(row[2] or "").strip() if len(row) > 2 else ""
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
        disposal = _prefill_disposal_from_startup(conn, row)
        return {
            "ok": True,
            "navigate": {
                "page": "returns",
                "returnsTab": "disposal",
                "disposalPrefill": disposal,
            },
        }

    return {"ok": False, "error": f"No row action for tab: {title}"}


def export_startup_alert_pdf(data: dict[str, Any]) -> dict[str, Any]:
    title = str(data.get("title") or "Startup Alerts").strip()
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    if not columns:
        return {"ok": False, "error": "No columns to export."}
    if not rows:
        return {"ok": False, "error": "No rows to export."}
    if not isinstance(columns, list):
        columns = list(columns)
    norm_rows = []
    for row in rows:
        vals = list(row) if isinstance(row, (list, tuple)) else [row]
        while len(vals) < len(columns):
            vals.append("")
        norm_rows.append(vals[: len(columns)])

    from core.export_manager import _save_pdf_to_path

    base_name = title.lower().replace(" ", "_")
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix=f"{base_name}_")
    os.close(fd)
    saved = ""
    payload = b""
    try:
        saved = _save_pdf_to_path(path, title, columns, norm_rows)
        if not saved or not os.path.isfile(saved):
            return {"ok": False, "error": "PDF export failed."}
        with open(saved, "rb") as fh:
            payload = fh.read()
    except Exception as exc:
        return {"ok": False, "error": f"PDF export failed: {exc}"}
    finally:
        for p in (saved, path, saved.replace(".pdf", ".html") if saved else ""):
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass

    return {
        "ok": True,
        "filename": f"{base_name}.pdf",
        "mime": "application/pdf",
        "content_base64": base64.b64encode(payload).decode("ascii"),
        "row_count": len(norm_rows),
    }
