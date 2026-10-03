"""A customer's GSTIN, for GSTR-1 B2B (3 Oct 2026).

The customers table has no GSTIN, and the server's customers neither; neither is
changed. Kept like the regular medicines list, one setting per customer,
``customer_gst:<customer id>`` = JSON {gstin, legal_name, state, since, updated}:

  Offline  the store's ``settings`` table,
  Online   the server's ``store_settings`` (PUT/GET /api/sync/settings/kv),

so no API, table or old PC changes. ``since`` is the first bill date the GSTIN applies
to: a bill made before the shop had it on file went out without it, and stays a B2C
supply in GSTR-1 -- a return filed for it already said so.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from typing import Any, Optional

PREFIX = "customer_gst:"
_cache: dict[str, Any] = {"at": 0.0, "rows": {}}
_TTL = 20.0

STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory",
}

_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def clean_gstin(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(value or "")).upper()


def gstin_problem(value: Any) -> str:
    """'' for a well-formed GSTIN (shape, state and check digit), else why not."""
    g = clean_gstin(value)
    if len(g) != 15:
        return "GSTIN 15 akshar asto"
    if not _GSTIN_RE.match(g):
        return "GSTIN cha format chukla (2 ank, 10 PAN, 1, Z, 1)"
    if g[:2] not in STATES:
        return f"GSTIN madhla rajya code {g[:2]} chukla"
    total = 0
    for i, ch in enumerate(g[:14]):
        v = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    if _CHARS[(36 - total % 36) % 36] != g[14]:
        return "GSTIN cha shevatcha check akshar julat nahi -- GSTIN punha tapasa"
    return ""


def state_of(gstin: Any) -> str:
    g = clean_gstin(gstin)
    return g[:2] if len(g) >= 2 and g[:2] in STATES else ""


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
        raise ValueError("Customer nivadla nahi.")
    if cid <= 0:
        raise ValueError("Customer nivadla nahi.")
    return f"{PREFIX}{cid}"


def _parse(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(str(raw or ""))
        except (TypeError, ValueError):
            return None
    if not isinstance(data, dict) or not clean_gstin(data.get("gstin")):
        return None
    return data


def _server_rows(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force and _cache["rows"] and now - _cache["at"] < _TTL:
        return _cache["rows"]
    from core import server_api as api
    from core.server_live import _token
    from core.store_images import _kv_rows

    res = api._request("GET", "/api/sync/settings/kv", token=_token(), timeout=30)
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("Server varun grahakanche GSTIN vachta ale nahit.")
    rows: dict[str, Any] = {}
    for row in _kv_rows(res.get("data")):
        name = str(row.get("name") or "")
        if name.startswith(PREFIX):
            rows[name] = row.get("value")
    _cache.update(at=now, rows=rows)
    return rows


def _shape(customer_id: int, data: Optional[dict[str, Any]]) -> dict[str, Any]:
    data = data or {}
    g = clean_gstin(data.get("gstin"))
    st = str(data.get("state") or state_of(g) or "")
    return {"customer_id": int(customer_id), "gstin": g, "legal_name": str(data.get("legal_name") or ""),
            "state": st, "state_name": STATES.get(st, ""), "since": str(data.get("since") or ""),
            "updated": str(data.get("updated") or "")}


def get_customer_gst(conn, customer_id: Any) -> dict[str, Any]:
    key = _key(customer_id)
    if _online():
        raw = _server_rows().get(key)
    else:
        from core.desktop_settings_service import _setting_get

        raw = _setting_get(conn, key, "")
    return _shape(int(customer_id), _parse(raw))


def save_customer_gst(conn, customer_id: Any, gstin: Any = "", legal_name: str = "",
                      since: str = "") -> dict[str, Any]:
    """Keep (or with an empty GSTIN, remove) a customer's GSTIN. Refuses a malformed one."""
    key = _key(customer_id)
    g = clean_gstin(gstin)
    value = ""
    data: dict[str, Any] = {}
    if g:
        problem = gstin_problem(g)
        if problem:
            raise ValueError(problem)
        since = str(since or "").strip()[:10] or date.today().isoformat()
        try:
            datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            raise ValueError("'Kadhi pasun' tarikh YYYY-MM-DD asavi")
        data = {"gstin": g, "legal_name": " ".join(str(legal_name or "").split()).upper(),
                "state": state_of(g), "since": since,
                "updated": datetime.now().isoformat(timespec="seconds")}
        value = json.dumps(data, ensure_ascii=False)
    if _online():
        from core.server_live import push_settings_kv

        push_settings_kv(key, value)
        _cache["rows"][key] = value
    else:
        from core.desktop_settings_service import _setting_set

        _setting_set(conn, key, value)
        conn.commit()
    return _shape(int(customer_id), data or None)


def all_customer_gst(conn) -> dict[int, dict[str, Any]]:
    """{customer id: {gstin, legal_name, state, since}} for every customer that has one."""
    rows: dict[str, Any]
    if _online():
        rows = _server_rows(force=True)
    else:
        from core.desktop_settings_service import _ensure_settings_table

        _ensure_settings_table(conn)
        rows = {str(n): v for n, v in conn.execute(
            "SELECT name, value FROM settings WHERE name LIKE ?", (PREFIX + "%",)).fetchall()}
    out: dict[int, dict[str, Any]] = {}
    for name, raw in rows.items():
        try:
            cid = int(str(name)[len(PREFIX):])
        except ValueError:
            continue
        data = _parse(raw)
        if data:
            out[cid] = _shape(cid, data)
    return out


def gstin_for_bill(entry: Optional[dict[str, Any]], bill_date: str) -> str:
    """The customer's GSTIN if it was on file for a bill of this date, else ''."""
    if not entry or not entry.get("gstin"):
        return ""
    since = str(entry.get("since") or "")
    if since and str(bill_date or "")[:10] < since:
        return ""
    return str(entry["gstin"])
