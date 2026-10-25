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
