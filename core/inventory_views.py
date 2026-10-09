"""Inventory views: Active / Hidden / Out of stock / Expired / All (owner, 9 Oct 2026).

The shop must be able to SEE its hidden, out-of-stock and expired medicines when it needs to.
The default view stays what Inventory always showed (Active: every medicine that is not
hidden). The rules are the server's (/api/store/inventory hidden / stock=out / expiry=expired
and /api/store/inventory/counts) and the phone's:

  hidden        is_hidden set
  out of stock  not hidden, stock <= 0
  expired       not hidden, past the month-end of its expiry month (core.batch_visibility)

Online asks the server; Offline and offline-first count their own copy the same way.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

VIEWS = ("active", "hidden", "out_of_stock", "expired", "all")


def normalize_view(raw: Any) -> str:
    v = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if v in ("out", "outofstock", "oos"):
        v = "out_of_stock"
    return v if v in VIEWS else "active"


def hidden_param(view: str) -> str:
    """The server's ?hidden= for a view."""
    if view == "hidden":
        return "1"
    if view == "all":
        return "all"
    return "0"


def hidden_sql(view: str) -> Optional[str]:
    """SQLite WHERE part on medicines.is_hidden for a view (None = no condition)."""
    if view == "hidden":
        return "COALESCE(is_hidden,0)<>0"
    if view == "all":
        return None
    return "COALESCE(is_hidden,0)=0"


def row_in_view(view: str, *, is_hidden: bool) -> bool:
    if view == "hidden":
        return bool(is_hidden)
    if view == "all":
        return True
    return not is_hidden


def status_filters(view: str, stock_status: str, expiry_status: str) -> tuple[str, str]:
    """Out of stock / Expired views are the existing Stock / Expiry filters, pre-set."""
    if view == "out_of_stock" and not stock_status:
        stock_status = "Out of Stock"
    if view == "expired" and not expiry_status:
        expiry_status = "Expired"
    return stock_status, expiry_status


def _expired(expiry: Any, today: date) -> bool:
    if not expiry:
        return False
    try:
        from core.batch_visibility import is_expired_as_of

        return bool(is_expired_as_of(str(expiry), today))
    except Exception:
        return False


def count_local(conn, today: Optional[date] = None) -> dict[str, int]:
    """Counts per view from this PC's own copy (Offline / offline-first)."""
    today = today or date.today()
    out = {v: 0 for v in VIEWS}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(medicines)").fetchall()}
    except Exception:
        return out
    hid = "COALESCE(is_hidden,0)" if "is_hidden" in cols else "0"
    exp = "COALESCE(expiry_date,'')" if "expiry_date" in cols else "''"
    where = "COALESCE(deleted,0)=0" if "deleted" in cols else "1=1"
    for is_hidden, stock, expiry in conn.execute(
        f"SELECT {hid}, COALESCE(stock_qty,0), {exp} FROM medicines WHERE {where}"
    ):
        out["all"] += 1
        if is_hidden:
            out["hidden"] += 1
            continue
        out["active"] += 1
        try:
            if float(stock or 0) <= 0:
                out["out_of_stock"] += 1
        except (TypeError, ValueError):
            pass
        if _expired(expiry, today):
            out["expired"] += 1
    return out


def count_online() -> Optional[dict[str, int]]:
    """Counts from the store server; None when it cannot answer (an older server)."""
    try:
        from core import store_query_client as sq

        data = sq.inventory_counts()
        if isinstance(data, dict) and "active" in data:
            return {v: int(data.get(v) or 0) for v in VIEWS}
    except Exception:
        return None
    return None
