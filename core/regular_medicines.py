"""Regular medicines: each customer's own standing list (BP, sugar, thyroid ...).

Not read from the sales history -- a customer buys many things once; the regular list is
what the shop keeps for them on purpose. Kept as one setting per customer,
``regular_meds:<customer id>`` = JSON {customer, phone, items: [{name, qty, unit}], updated}:

  Offline  the store's ``settings`` table (and the settings mirror),
  Online   the server's ``store_settings`` (PUT/GET /api/sync/settings/kv), which every PC of
           the store shares -- no new server table, nothing the sales history depends on.

A bill made from the list is an ordinary sale: the history, dues and stock do not know or
care that it came from here.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Optional

PREFIX = "regular_meds:"
_UNITS = {"strip", "tablet", "unit"}
_cache: dict[str, Any] = {"at": 0.0, "rows": {}}
_TTL = 20.0


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _key(customer_id: Any) -> str:
    try:
        cid = int(customer_id)
    except (TypeError, ValueError):
        raise ValueError("Customer nivadla nahi — aadhi grahak nivda.")
    if cid <= 0:
        raise ValueError("Customer nivadla nahi — aadhi grahak nivda.")
    return f"{PREFIX}{cid}"


def clean_items(items: Any) -> list[dict[str, Any]]:
    """[{name, qty, unit}] with names upper-cased, qty > 0, the same medicine once."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = " ".join(str(it.get("name") or "").split()).upper()
        if not name or name in seen:
            continue
        try:
            qty = float(it.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        if qty <= 0:
            continue
        unit = str(it.get("unit") or "").strip().lower()
        out.append({"name": name, "qty": int(qty) if qty == int(qty) else round(qty, 2),
                    "unit": unit if unit in _UNITS else ""})
        seen.add(name)
    return out


def _parse(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(str(raw or ""))
        except (TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    data["items"] = clean_items(data.get("items"))
    return data


# ── the server (Online) ─────────────────────────────────────────────────────────────────
def _server_rows(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force and _cache["rows"] and now - _cache["at"] < _TTL:
        return _cache["rows"]
    from core import server_api as api
    from core.server_live import _token
    from core.store_images import _kv_rows

    res = api._request("GET", "/api/sync/settings/kv", token=_token(), timeout=30)
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("Server varun regular yaadi vachta ali nahi.")
    rows: dict[str, Any] = {}
    for row in _kv_rows(res.get("data")):
        name = str(row.get("name") or "")
        if name.startswith(PREFIX):
            rows[name] = row.get("value")
    _cache.update(at=now, rows=rows)
    return rows


# ── read / write ────────────────────────────────────────────────────────────────────────
def get_regulars(conn, customer_id: Any) -> dict[str, Any]:
    """{customer_id, customer, phone, items} -- items empty when the customer has no list."""
    key = _key(customer_id)
    raw: Any = None
    if _online():
        raw = _server_rows().get(key)
    else:
        from core.desktop_settings_service import _setting_get

        raw = _setting_get(conn, key, "")
    data = _parse(raw) or {}
    return {"customer_id": int(customer_id), "customer": data.get("customer") or "",
            "phone": data.get("phone") or "", "items": data.get("items") or [],
            "updated": data.get("updated") or ""}


def save_regulars(conn, customer_id: Any, customer: str = "", phone: str = "",
                  items: Any = None) -> dict[str, Any]:
    """Replace a customer's regular list (an empty list removes it)."""
    key = _key(customer_id)
    data = {"customer": " ".join(str(customer or "").split()).upper(), "phone": str(phone or "").strip(),
            "items": clean_items(items), "updated": datetime.now().isoformat(timespec="seconds")}
    value = json.dumps(data, ensure_ascii=False) if data["items"] else ""
    if _online():
        from core.server_live import push_settings_kv

        push_settings_kv(key, value)
        _cache["rows"][key] = value
    else:
        from core.desktop_settings_service import _setting_set

        _setting_set(conn, key, value)
        conn.commit()
    return get_regulars(conn, customer_id) if not _online() else {
        "customer_id": int(customer_id), "customer": data["customer"], "phone": data["phone"],
        "items": data["items"], "updated": data["updated"]}


def list_regulars(conn) -> list[dict[str, Any]]:
    """Every customer that has a regular list (for the Tabs & Tools window)."""
    rows: dict[str, Any]
    if _online():
        rows = _server_rows(force=True)
    else:
        from core.desktop_settings_service import _ensure_settings_table

        _ensure_settings_table(conn)
        rows = {n: v for n, v in conn.execute(
            "SELECT name, value FROM settings WHERE name LIKE ?", (PREFIX + "%",)).fetchall()}
    out = []
    for name, raw in rows.items():
        data = _parse(raw)
        if not data or not data.get("items"):
            continue
        try:
            cid = int(str(name)[len(PREFIX):])
        except ValueError:
            continue
        out.append({"customer_id": cid, "customer": data.get("customer") or "", "phone": data.get("phone") or "",
                    "items": data["items"], "updated": data.get("updated") or ""})
    out.sort(key=lambda r: r["customer"])
    return out
