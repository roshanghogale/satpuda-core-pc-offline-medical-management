"""Settings → Import Data: JSON purchase bills (classic ImportPurchasesPage)."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSION_TTL_SEC = 3600


def _purge_old_sessions() -> None:
    now = time.time()
    dead = [
        k
        for k, v in _SESSIONS.items()
        if now - float(v.get("created") or 0) > _SESSION_TTL_SEC
    ]
    for k in dead:
        _SESSIONS.pop(k, None)


def _bill_summary(bill: dict[str, Any], index: int) -> dict[str, Any]:
    sup = bill.get("supplier") or {}
    items = bill.get("items") or []
    return {
        "index": index,
        "supplier_name": str(sup.get("name") or "").strip(),
        "bill_number": str(bill.get("bill_number") or "").strip(),
        "purchase_date": str(bill.get("purchase_date") or "").strip(),
        "item_count": len(items),
    }


def _parse_raw_json(raw: str) -> list[dict[str, Any]]:
    from core.web_purchase_save import json_bill_to_internal

    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Paste JSON or load a file first.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    bills = data.get("bills") if isinstance(data, dict) else data
    if not isinstance(bills, list) or not bills:
        raise ValueError('Expected {"bills": [...]} or a list of bills.')

    internal: list[dict[str, Any]] = []
    for b in bills:
        if not isinstance(b, dict):
            raise ValueError("Each bill must be a JSON object.")
        bill = json_bill_to_internal(b)
        if not bill.get("items"):
            raise ValueError(
                f"Bill {bill.get('bill_number') or len(internal) + 1}: no valid items."
            )
        internal.append(bill)
    return internal


def parse_file_import(body: dict[str, Any]) -> dict[str, Any]:
    _purge_old_sessions()
    raw = str(body.get("json") or body.get("raw") or "").strip()
    bills = _parse_raw_json(raw)
    token = uuid.uuid4().hex
    _SESSIONS[token] = {"created": time.time(), "bills": bills}
    summaries = [_bill_summary(b, i) for i, b in enumerate(bills)]
    return {
        "ok": True,
        "token": token,
        "count": len(bills),
        "summaries": summaries,
    }


def get_file_import_bill(body: dict[str, Any]) -> dict[str, Any]:
    token = str(body.get("token") or "").strip()
    sess = _SESSIONS.get(token)
    if not sess:
        return {"ok": False, "error": "Import session expired. Parse JSON again."}
    bills: list[dict[str, Any]] = sess["bills"]
    try:
        index = int(body.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    if index < 0 or index >= len(bills):
        return {"ok": False, "error": f"Bill index out of range (0–{len(bills) - 1})."}
    return {
        "ok": True,
        "token": token,
        "index": index,
        "count": len(bills),
        "bill": bills[index],
        "summary": _bill_summary(bills[index], index),
    }


def update_file_import_bill(body: dict[str, Any]) -> dict[str, Any]:
    token = str(body.get("token") or "").strip()
    sess = _SESSIONS.get(token)
    if not sess:
        return {"ok": False, "error": "Import session expired. Parse JSON again."}
    bills: list[dict[str, Any]] = sess["bills"]
    try:
        index = int(body.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    bill = body.get("bill")
    if not isinstance(bill, dict):
        return {"ok": False, "error": "Missing bill object."}
    if index < 0 or index >= len(bills):
        return {"ok": False, "error": "Bill index out of range."}
    items = bill.get("items")
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "Bill must have at least one item."}
    bills[index] = bill
    return {
        "ok": True,
        "token": token,
        "index": index,
        "summary": _bill_summary(bill, index),
    }


def submit_file_import(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.web_purchase_save import save_internal_bill

    token = str(body.get("token") or "").strip()
    sess = _SESSIONS.pop(token, None)
    if not sess:
        return {"ok": False, "error": "Import session expired. Parse JSON again."}
    bills: list[dict[str, Any]] = sess["bills"]
    saved = 0
    errors: list[str] = []
    purchase_nos: list[str] = []
    for i, bill in enumerate(bills):
        label = bill.get("bill_number") or f"Bill {i + 1}"
        try:
            sup = bill.get("supplier") or {}
            if not str(sup.get("name") or "").strip():
                raise ValueError("supplier name required")
            pno = save_internal_bill(conn, bill)
            conn.commit()
            saved += 1
            purchase_nos.append(str(pno))
        except Exception as exc:
            conn.rollback()
            errors.append(f"{label}: {exc}")
    return {
        "ok": not errors or saved > 0,
        "saved": saved,
        "total": len(bills),
        "errors": errors,
        "purchase_nos": purchase_nos,
    }


def cancel_file_import(body: dict[str, Any]) -> dict[str, Any]:
    token = str(body.get("token") or "").strip()
    if token:
        _SESSIONS.pop(token, None)
    return {"ok": True}
