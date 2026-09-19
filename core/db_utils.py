"""SQLite helpers — consistent WAL / busy_timeout for store DB access."""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def open_store_db(
    path: Optional[str] = None,
    *,
    readonly: bool = False,
    timeout: float = 30.0,
    force: bool = False,
) -> sqlite3.Connection:
    """
    Open the active store database with WAL and a generous busy timeout.

    Prefer this for any second connection (import, sync, master prep) so writers
    wait on busy_timeout instead of failing with "database is locked".
    Use readonly=True only for pure lookup paths that never INSERT/UPDATE.

    Online server-only: refuses to open store SQLite unless migrate/restore
    temporarily allows it (or force=True for controlled migrate helpers).
    """
    if not force:
        try:
            from core.online_migrate import assert_can_open_store_db

            assert_can_open_store_db()
        except ImportError:
            pass
    if path is None:
        from core.store_manager import get_active_db_path
        path = get_active_db_path()
    db_path = os.path.abspath(path)
    ms = max(1000, int(float(timeout) * 1000))

    if readonly:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=float(timeout),
            check_same_thread=False,
        )
    else:
        conn = sqlite3.connect(
            db_path,
            timeout=float(timeout),
            check_same_thread=False,
        )
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
    conn.execute(f"PRAGMA busy_timeout={ms}")
    return conn


def configure_connection(conn: sqlite3.Connection, *, timeout: float = 30.0) -> None:
    """Apply WAL + busy_timeout to an existing connection."""
    ms = max(1000, int(float(timeout) * 1000))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    conn.execute(f"PRAGMA busy_timeout={ms}")


def db_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 12,
    base_delay: float = 0.05,
) -> T:
    """Retry SQLite ops when another connection holds a brief lock."""
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last = exc
            msg = str(exc).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            if attempt >= attempts - 1:
                raise
            time.sleep(base_delay * (2 ** min(attempt, 6)))
    if last is not None:
        raise last
    raise RuntimeError("db_retry: unreachable")
