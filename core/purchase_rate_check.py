"""Purchase entry: was this medicine bought cheaper before? (owner, 17 Oct 2026)

When the shop types the MRP or rate of a medicine on the Purchase page, the page asks here. If
any supplier -- this one or another -- last sold the same medicine for less, the page shows a
popup listing them, cheapest first.

The old Tk screen only compared with the same supplier's last bill; the Tauri page never
compared at all. One medicine name comes in several packs (a 21-sachet box at Rs 17.56 next to
a 4-sachet pack at Rs 3.57), so a rate is only compared with purchases of the same pack: an MRP
within 10% of the one typed. With no MRP typed yet, every purchase counts.

Online the last purchase from each supplier comes from the server (/api/store/purchase-rates);
Offline from the store file. Either way a failure means no popup, never a blocked entry.
"""
from __future__ import annotations

import sqlite3
from typing import Any

_MRP_SAME_PACK = 0.10      # an MRP within 10% is the same pack
_MIN_GAP = 0.005           # rupees; a rounding difference is not "cheaper"


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def last_rates_local(conn, name: str) -> list[dict]:
    """The last purchase of this medicine from each supplier, from the store file."""
    try:
        cur = conn.execute(
            """
            SELECT COALESCE(s.name, ''), pi.rate, COALESCE(NULLIF(pi.mrp, 0), m.mrp, 0),
                   COALESCE(p.bill_number, ''), COALESCE(p.purchase_date, '')
              FROM purchase_items pi
              JOIN purchases p ON p.id = pi.purchase_id
              JOIN medicines m ON m.id = pi.medicine_id
              LEFT JOIN suppliers s ON s.id = p.supplier_id
             WHERE upper(trim(m.name)) = upper(trim(?)) AND pi.rate > 0
             ORDER BY p.purchase_date DESC, p.id DESC, pi.id DESC
            """,
            (name,),
        )
        rows = cur.fetchall()
    except sqlite3.Error:
        # An older store file without purchase_items.mrp: the medicine's MRP stands in.
        try:
            rows = conn.execute(
                """
                SELECT COALESCE(s.name, ''), pi.rate, COALESCE(m.mrp, 0),
                       COALESCE(p.bill_number, ''), COALESCE(p.purchase_date, '')
                  FROM purchase_items pi
                  JOIN purchases p ON p.id = pi.purchase_id
                  JOIN medicines m ON m.id = pi.medicine_id
                  LEFT JOIN suppliers s ON s.id = p.supplier_id
                 WHERE upper(trim(m.name)) = upper(trim(?)) AND pi.rate > 0
                 ORDER BY p.purchase_date DESC, p.id DESC, pi.id DESC
                """,
                (name,),
            ).fetchall()
        except sqlite3.Error:
            return []
    out: list[dict] = []
    seen: set[str] = set()
    for sup, rate, mrp, bill, pdate in rows:
        key = str(sup or "").strip().upper()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "supplier_name": str(sup or ""), "rate": _f(rate), "mrp": _f(mrp),
            "bill_number": str(bill or ""), "purchase_date": str(pdate or "")[:10],
        })
    return out


def last_rates_online(name: str) -> list[dict]:
    from core import store_query_client as sq

    data = sq._get("/api/store/purchase-rates", {"name": name}) or {}
    return [
        {
            "supplier_name": str(r.get("supplier_name") or ""),
            "rate": _f(r.get("rate")),
            "mrp": _f(r.get("mrp")),
            "bill_number": str(r.get("bill_number") or ""),
            "purchase_date": str(r.get("purchase_date") or "")[:10],
        }
        for r in (data.get("rows") or [])
    ]


def cheaper_before(rows: list[dict], *, rate: float, mrp: float = 0.0) -> list[dict]:
    """The earlier purchases (one per supplier) of the same pack that cost less, cheapest first."""
    rate = _f(rate)
    mrp = _f(mrp)
    if rate <= 0:
        return []
    out = []
    for r in rows:
        r_rate = _f(r.get("rate"))
        if r_rate <= 0 or r_rate >= rate - _MIN_GAP:
            continue
        r_mrp = _f(r.get("mrp"))
        if mrp > 0 and r_mrp > 0 and abs(r_mrp - mrp) > mrp * _MRP_SAME_PACK:
            continue    # another pack of the same name
        out.append(dict(r))
    out.sort(key=lambda r: (_f(r.get("rate")), str(r.get("supplier_name") or "")))
    return out


def rate_check(conn, *, name: str, rate: float, mrp: float = 0.0) -> dict:
    name = str(name or "").strip()
    if not name or _f(rate) <= 0:
        return {"ok": True, "cheaper": []}
    try:
        from core.sync_prefs import is_online_mode

        online = bool(is_online_mode())
    except Exception:
        online = False
    try:
        rows = last_rates_online(name) if online else last_rates_local(conn, name)
    except Exception as exc:
        return {"ok": False, "cheaper": [], "error": str(exc)}
    return {"ok": True, "cheaper": cheaper_before(rows, rate=rate, mrp=mrp)[:6]}
