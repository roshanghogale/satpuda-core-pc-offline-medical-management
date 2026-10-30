"""Inventory > Check with master (owner, 30 Oct 2026): which of this shop's medicines say
something else than the master list, and a Fix that puts the master's value in.

The master is this PC's copy of the server's global list (core/master_medicine_service.py),
rebuilt from the official rules and the web (the master list check). Each master row says which
of its fields that check confirmed (checked_fields); there the master is right "90% and more"
and wins -- "Fix all" changes only those. A medicine's own Fix may also take a field the check
did not confirm, when the shop says so.

Compared per medicine name (every batch of the name is fixed together): schedule, HSN, GST %,
content and manufacturer. Only a master field that has a value counts (a confirmed "no schedule"
counts too: the rules give none).
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

# (key, shop column, master column, label)
FIELDS = (
    ("schedule", "schedule", "schedule", "Schedule"),
    ("hsn", "hsn_code", "hsn_code", "HSN"),
    ("gst", "gst_percent", "gst_percent", "GST %"),
    ("content", "content_drug", "content_drug", "Content"),
    ("manufacturer", "manufacturer", "manufacturer", "Manufacturer"),
)
_BY_KEY = {f[0]: f for f in FIELDS}


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _norm(key: str, v: Any) -> str:
    """The value as compared: '30042019' / '5' / 'h1' / 'cefotaxime(1gm)'."""
    if key == "gst":
        try:
            f = float(v or 0)
        except (TypeError, ValueError):
            return ""
        return "" if f == 0 else (str(int(f)) if f == int(f) else f"{f:g}")
    s = str(v or "").strip()
    if key == "hsn":
        return re.sub(r"\D", "", s)
    if key == "schedule":
        return s.upper()
    if key == "manufacturer":
        return re.sub(r"[^a-z0-9]", "", s.lower())
    return re.sub(r"\s+", "", s.lower())


def _shown(key: str, v: Any) -> str:
    if key == "gst":
        return _norm("gst", v)
    return str(v or "").strip()


def _shop_rows(conn) -> list[dict[str, Any]]:
    """Every medicine row of this shop: id, name and the compared fields."""
    if _online():
        from core.online_catalog import medicines

        return [{"id": int(m.get("id") or m.get("local_id") or 0), "name": str(m.get("name") or ""),
                 **{col: m.get(col) for _k, col, _m, _l in FIELDS}} for m in medicines(force=True)]
    have = {r[1] for r in conn.execute("PRAGMA table_info(medicines)")}
    cols = ", ".join(c if c in have else f"'' AS {c}" for _k, c, _m, _l in FIELDS)
    live = " WHERE COALESCE(deleted, 0) = 0" if "deleted" in have else ""
    return [dict(zip(["id", "name"] + [c for _k, c, _m, _l in FIELDS], r))
            for r in conn.execute(f"SELECT id, name, {cols} FROM medicines{live} ORDER BY id")]


def _master_rows(names: list[str]) -> dict[str, dict[str, Any]]:
    """Upper-case name -> the master row (its fields and checked_fields)."""
    from core.master_medicine_service import MASTER_TABLE, _connect_master, _init_schema, checked_set

    out: dict[str, dict[str, Any]] = {}
    conn = _connect_master()
    try:
        _init_schema(conn)
        cols = ", ".join(m for _k, _c, m, _l in FIELDS)
        for i in range(0, len(names), 500):
            part = names[i:i + 500]
            q = (f"SELECT UPPER(TRIM(name)), {cols}, COALESCE(checked_fields, '') FROM {MASTER_TABLE} "
                 f"WHERE COALESCE(deleted, 0) = 0 AND UPPER(TRIM(name)) IN ({','.join('?' * len(part))})")
            for r in conn.execute(q, part):
                row = dict(zip([m for _k, _c, m, _l in FIELDS], r[1:-1]))
                row["checked"] = checked_set(r[-1])
                out[str(r[0])] = row
    finally:
        conn.close()
    return out


def differences(conn, *, sure_only: bool = False, q: str = "", field: str = "") -> dict[str, Any]:
    """{ok, rows, counts, medicines, compared} -- one row per medicine name that differs."""
    shop = _shop_rows(conn)
    by_name: dict[str, dict[str, Any]] = {}
    for r in shop:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        g = by_name.setdefault(name.upper(), {"name": name, "ids": [], "latest": r})
        g["ids"].append(int(r["id"]))
        g["latest"] = r                       # the newest batch: what the shop types now
    master = _master_rows(list(by_name))
    words = [w for w in (q or "").upper().split() if w]
    rows, counts = [], {k: 0 for k, *_ in FIELDS}
    for key_name, g in sorted(by_name.items()):
        m = master.get(key_name)
        if not m:
            continue
        if words and not all(w in key_name for w in words):
            continue
        diffs = []
        for key, col, mcol, label in FIELDS:
            if field and key != field:
                continue
            mv, sv = m.get(mcol), g["latest"].get(col)
            checked = key in m["checked"]
            if not _norm(key, mv) and not (key == "schedule" and checked):
                continue                      # the master has nothing to say
            if _norm(key, mv) == _norm(key, sv):
                continue
            if sure_only and not checked:
                continue
            diffs.append({"field": key, "label": label, "shop": _shown(key, sv), "master": _shown(key, mv),
                          "sure": checked})
        if diffs:
            for d in diffs:
                counts[d["field"]] += 1
            rows.append({"name": g["name"], "batches": len(g["ids"]), "diffs": diffs,
                         "sure": any(d["sure"] for d in diffs)})
    rows.sort(key=lambda r: (not r["sure"], r["name"]))
    return {"ok": True, "rows": rows, "counts": counts, "medicines": len(by_name),
            "compared": sum(1 for k in by_name if k in master), "online": _online()}


def fix(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Put the master's values into the shop's rows. body {items: [{name, fields: [...]}]} or
    {all: true} (every medicine, only the fields the master list check confirmed)."""
    if body.get("all"):
        found = differences(conn, sure_only=True)
        items = [{"name": r["name"], "fields": [d["field"] for d in r["diffs"]]} for r in found["rows"]]
    else:
        items = [it for it in (body.get("items") or []) if str(it.get("name") or "").strip()]
    if not items:
        return {"ok": True, "fixed": 0, "rows": 0, "failed": []}
    master = _master_rows([str(it["name"]).strip().upper() for it in items])
    shop = _shop_rows(conn)
    ids_of: dict[str, list[int]] = {}
    for r in shop:
        ids_of.setdefault(str(r.get("name") or "").strip().upper(), []).append(int(r["id"]))
    fixed, rows_changed, failed = 0, 0, []
    online = _online()
    for it in items:
        key_name = str(it["name"]).strip().upper()
        m = master.get(key_name)
        ids = ids_of.get(key_name) or []
        fields = [f for f in (it.get("fields") or [k for k, *_ in FIELDS]) if f in _BY_KEY]
        if not m or not ids or not fields:
            failed.append({"name": it["name"], "error": "not in the master list or the inventory"})
            continue
        values = {}
        for f in fields:
            _k, col, mcol, _l = _BY_KEY[f]
            mv = m.get(mcol)
            if _norm(f, mv) or (f == "schedule" and f in m["checked"]):
                values[col] = float(mv or 0) if f == "gst" else str(mv or "").strip()
        if not values:
            continue
        try:
            if online:
                _fix_online(conn, ids, values)
            else:
                _fix_offline(conn, ids, values)
            fixed += 1
            rows_changed += len(ids)
        except Exception as exc:  # noqa: BLE001
            failed.append({"name": it["name"], "error": str(exc)})
    return {"ok": not failed, "fixed": fixed, "rows": rows_changed, "failed": failed}


def _fix_offline(conn, ids: list[int], values: dict[str, Any]) -> None:
    """This PC's books: the rows, then the sync is told (core/sync_coordinator), as an edit is."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(medicines)")}
    sets = [f"{c}=?" for c in values if c in have]
    args = [values[c] for c in values if c in have]
    if not sets:
        return
    stamp = ", synced_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')" if "synced_at" in have else ""
    for one in ids:
        conn.execute(f"UPDATE medicines SET {', '.join(sets)}{stamp} WHERE id=?", args + [one])
    conn.commit()
    try:
        from core.sync_coordinator import after_medicine_saved

        for one in ids:
            after_medicine_saved(conn, one)
    except Exception:
        pass


def _fix_online(conn, ids: list[int], values: dict[str, Any]) -> None:
    """An Online shop's books are on the server: each row through the normal medicine edit."""
    from core.desktop_inventory_service import update_medicine
    from core.gst_extras import _edit_body
    from core.server_crud import get_doc

    for one in ids:
        doc = get_doc("medicines", one) or {}
        body = {**_edit_body(doc, one), "hsn_code": doc.get("hsn_code") or "",
                "gst_percent": doc.get("gst_percent") or 0}
        body.update(values)
        res = update_medicine(conn, body)
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "the server did not take the change")
