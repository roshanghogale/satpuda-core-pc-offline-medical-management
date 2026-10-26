"""Remember a whole-store read until the store changes (26 Oct 2026).

Some lists are built from the same whole-table reads several times in one request: the
Alert & Monitoring screen built "where each batch came from" (every purchase line, every
medicine) four times and read the in-stock batches four times -- on a 64,000-medicine
store a second of the page's time, and again for the startup popup a moment later.

``memo(conn, key, build)`` keeps the answer of ``build()`` for this connection until any
change is written to the database: by this connection (``total_changes``) or by another one
(``PRAGMA data_version``). Only reads that depend on nothing but the database belong here
(not today's date, not a setting outside it), and callers must not change what they get.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

_lock = threading.Lock()
# key -> (connection, stamp, value); one answer per key (the engine has one store open)
_memo: dict[str, tuple[Any, tuple, Any]] = {}


def _stamp(conn) -> tuple | None:
    try:
        dv = conn.execute("PRAGMA data_version").fetchone()[0]
        return (int(dv), int(conn.total_changes), bool(conn.in_transaction))
    except Exception:
        return None


def memo(conn, key: str, build: Callable[[], Any]) -> Any:
    stamp = _stamp(conn)
    # A read inside an open write transaction sees changes not yet committed: never kept.
    if stamp is None or stamp[2]:
        return build()
    with _lock:
        hit = _memo.get(key)
    if hit is not None and hit[0] is conn and hit[1] == stamp:
        return hit[2]
    value = build()
    # The database may have changed while it was built: keep it only if not.
    if _stamp(conn) == stamp:
        with _lock:
            _memo[key] = (conn, stamp, value)
    return value


def forget() -> None:
    with _lock:
        _memo.clear()
