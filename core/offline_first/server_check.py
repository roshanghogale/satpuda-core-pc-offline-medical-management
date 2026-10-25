"""Quick questions to the server before a write that this PC's own copy cannot judge alone.

Offline-first checks a delete or a save against this PC's copy, which lags the other devices by
whatever has not been pulled yet. Where being behind would do harm that cannot be taken back
(a delete taking stock another device already sold), the write asks the server first -- once,
briefly, and only when the sync is working. With no internet the answer is None and the write
is judged on the local copy as before: offline-first never refuses work for want of a network.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Iterable, Optional

log = logging.getLogger(__name__)

ASK_TIMEOUT = 5.0


def _askable(conn: sqlite3.Connection) -> bool:
    """Offline-first, registered, and the sync's last round did not fail (no point waiting on a
    network the worker just found down)."""
    try:
        from core.offline_first.runtime import is_active, registered

        if not is_active() or not registered(conn):
            return False
        from core.offline_first import worker

        w = worker.current()
        if w is not None and isinstance(w.last, dict) and w.last.get("error"):
            return False
        return True
    except Exception:
        return False


def stock_after_sync(conn: sqlite3.Connection, medicine_ids: Iterable[int]) -> Optional[dict[int, int]]:
    """Each medicine's stock as this PC will hold it after the next pull: the server's figure
    (every device's movements it has) plus this PC's own movements the server does not have yet
    -- the same sum the pull writes (pull.set_stock_from_server). None when it cannot be asked."""
    ids = sorted({int(m) for m in medicine_ids if m})
    if not ids or not _askable(conn):
        return None
    try:
        from core.offline_first import client
        from core.offline_first.outbox import pending_stock_by_medicine

        snap = client.stock(ids, timeout=ASK_TIMEOUT)
        pending = pending_stock_by_medicine(conn)
        out: dict[int, int] = {}
        for row in snap.get("stock") or []:
            try:
                mid, qty = int(row[0]), int(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            out[mid] = qty + pending.get(mid, 0)
        return out
    except Exception as exc:
        log.info("offline-first: server stock not asked: %s", exc)
        return None


def purchases_with_bill(conn: sqlite3.Connection, bill_number: str) -> Optional[list[dict]]:
    """The server's live purchases whose supplier bill number is ``bill_number`` and that this
    PC does not hold yet (another device's, not pulled so far). None when it cannot be asked.

    A record this PC holds -- live, deleted, or edited and not sent -- is judged on this PC's
    own copy, never on the server's older one: a purchase deleted here a moment ago is still on
    the server until the delete goes up, and must not refuse its own re-entry."""
    bill = str(bill_number or "").strip()
    if not bill or not _askable(conn):
        return None
    try:
        from urllib.parse import urlencode

        from core import server_api as api
        from core.offline_first import client

        qs = urlencode({"q": bill, "from": "2000-01-01", "to": "2099-12-31", "limit": 200,
                        "include_total": "0"})
        res = api._request("GET", f"/api/store/purchases?{qs}", token=client._token(),
                           timeout=ASK_TIMEOUT)
        if not res.get("ok"):
            return None
        rows = (res.get("data") or {}).get("rows") or []
    except Exception as exc:
        log.info("offline-first: server purchases not asked: %s", exc)
        return None
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("deleted") or row.get("is_autosave"):
            continue
        if str(row.get("bill_number") or "").strip().upper() != bill.upper():
            continue
        try:
            rid = int(row.get("id") or row.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or conn.execute("SELECT 1 FROM purchases WHERE id=?", (rid,)).fetchone():
            continue
        if conn.execute(
            "SELECT 1 FROM of_dirty WHERE collection='purchases' AND local_id=? "
            "UNION ALL SELECT 1 FROM of_events WHERE collection='purchases' AND local_id=? "
            "AND status='pending' LIMIT 1", (rid, rid),
        ).fetchone():
            continue
        out.append(row)
    if out:
        try:
            from core.offline_first import worker

            worker.kick()                        # bring it down now: the screen offers to open it
        except Exception:
            pass
    return out
