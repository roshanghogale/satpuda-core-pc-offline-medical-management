"""Ids in this PC's own range, so two offline devices never create the same record id.

The server gives each device a device_no; the ids it makes start at device_no * 1e9
(``id_base``) and end below the next device's range (``id_max``). Every synced table uses
``INTEGER PRIMARY KEY AUTOINCREMENT``, and SQLite's next id is "one more than the largest id
in the table" -- which, once other devices' records are in the copy, would land in THEIR
range. So the engine's connection is made with ``OfConnection``: an INSERT into a synced table
that does not name its id gets the next id of this device's range added to it, wherever in the
code it comes from.
"""
from __future__ import annotations

import re
import sqlite3
import threading

from core.offline_first.schema import COLLECTIONS

_RANGE_TABLES = frozenset(COLLECTIONS)
_lock = threading.Lock()

# Ids from here up belong to devices that make their own (device_no * 1e9 + n); the server's
# own allocator and the older apps stay below it (server-live syncService.js LEGACY_ID_LIMIT).
LEGACY_ID_LIMIT = 1_000_000_000
_legacy_next: dict = {}

# INSERT [OR x] INTO table (cols) VALUES (params)  -- one row, qmark style
_INSERT_RX = re.compile(
    r"^\s*INSERT\s+(?:OR\s+(?:IGNORE|REPLACE|ABORT|FAIL|ROLLBACK)\s+)?INTO\s+"
    r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<cols>[^()]*)\)\s*VALUES\s*\((?P<vals>[^()]*)\)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def device_range(conn: sqlite3.Connection):
    """(id_base, id_max) for this store's device, or None before registration.

    On a ``LegacyIdConnection`` (Offline mode) with no device of its own: (0, LEGACY_ID_LIMIT - 1).
    """
    rows = {}
    try:
        rows = dict(conn.execute(
            "SELECT k, v FROM of_meta WHERE k IN ('id_base','id_max')"
        ).fetchall())
    except sqlite3.Error:
        pass
    try:
        base, top = int(rows.get("id_base") or 0), int(rows.get("id_max") or 0)
    except (TypeError, ValueError):
        base, top = 0, 0
    if base > 0 and top > base:
        return (base, top)
    if getattr(conn, "legacy_ids", False):
        return (0, LEGACY_ID_LIMIT - 1)
    return None


def has_device_range_ids(conn: sqlite3.Connection) -> bool:
    """True when the copy holds records some device made in its own range."""
    for table in COLLECTIONS:
        try:
            if conn.execute(f"SELECT 1 FROM {table} WHERE id >= ? LIMIT 1", (LEGACY_ID_LIMIT,)).fetchone():
                return True
        except sqlite3.Error:
            continue
    return False


def _legacy_next_id(conn: sqlite3.Connection, table: str) -> int:
    """Offline mode on a copy holding other devices' ids: one more than the largest id below
    LEGACY_ID_LIMIT (SQLite alone would continue after the device-range ids, inside another
    device's range). Called under _lock."""
    used = conn.execute(
        f"SELECT MAX(id) FROM {table} WHERE id BETWEEN 1 AND ?", (LEGACY_ID_LIMIT - 1,)
    ).fetchone()[0]
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        path = ""
    key = (path, table)
    nid = max((int(used) + 1) if used else 1, _legacy_next.get(key, 0))
    if nid >= LEGACY_ID_LIMIT:
        raise RuntimeError(f"no free id below {LEGACY_ID_LIMIT} for {table}")
    _legacy_next[key] = nid + 1
    return nid


def next_id(conn: sqlite3.Connection, table: str) -> int:
    """The next free id of this device's range for ``table`` (reserved: never handed out twice)."""
    rng = device_range(conn)
    if not rng:
        raise RuntimeError("offline-first: this PC is not registered with the server yet")
    base, top = rng
    if base == 0:
        with _lock:
            return _legacy_next_id(conn, table)
    with _lock:
        row = conn.execute("SELECT next_id FROM of_id_counters WHERE tbl=?", (table,)).fetchone()
        have = int(row[0]) if row else base + 1
        used = conn.execute(
            f"SELECT MAX(id) FROM {table} WHERE id BETWEEN ? AND ?", (base, top)
        ).fetchone()[0]
        nid = max(have, (int(used) + 1) if used else base + 1)
        if nid > top:
            raise RuntimeError(f"offline-first: id range of this device is full for {table}")
        conn.execute(
            "INSERT INTO of_id_counters (tbl, next_id) VALUES (?, ?) "
            "ON CONFLICT(tbl) DO UPDATE SET next_id=excluded.next_id",
            (table, nid + 1),
        )
    return nid


def rewrite_insert(conn: sqlite3.Connection, sql: str, params):
    """(sql, params) with this device's next id added when an INSERT into a synced table
    names no id. Anything else is returned unchanged."""
    if not isinstance(sql, str) or "insert" not in sql[:40].lower():
        return sql, params
    m = _INSERT_RX.match(sql)
    if not m:
        return sql, params
    table = m.group("table")
    if table.lower() not in _RANGE_TABLES:
        return sql, params
    cols = [c.strip().strip('"`[]').lower() for c in m.group("cols").split(",")]
    if "id" in cols:
        return sql, params
    vals = m.group("vals")
    if ":" in vals or "$" in vals or "@" in vals:
        return sql, params            # named parameters: left alone (none in the app today)
    if not isinstance(params, (list, tuple)):
        return sql, params
    if device_range(conn) is None:
        return sql, params
    nid = next_id(conn, table)
    head = sql[: m.start("cols")] + "id, " + m.group("cols") + sql[m.end("cols"): m.start("vals")]
    new_sql = head + "?, " + vals + sql[m.end("vals"):]
    return new_sql, (nid, *params)


class OfCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        sql, parameters = rewrite_insert(self.connection, sql, parameters)
        return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        m = _INSERT_RX.match(sql) if isinstance(sql, str) else None
        if not m or m.group("table").lower() not in _RANGE_TABLES or device_range(self.connection) is None:
            return super().executemany(sql, seq_of_parameters)
        for params in seq_of_parameters:
            s2, p2 = rewrite_insert(self.connection, sql, params)
            super().execute(s2, p2)
        return self


class OfConnection(sqlite3.Connection):
    """The engine's store connection in offline-first mode."""

    def cursor(self, factory=OfCursor):  # noqa: D401
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        cur = self.cursor()
        return cur.execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        cur = self.cursor()
        return cur.executemany(sql, seq_of_parameters)


class LegacyIdConnection(OfConnection):
    """Offline mode on a copy that holds records made in device ranges (it was on offline-first,
    or it was copied from a server where offline-first devices work): new records stay below
    LEGACY_ID_LIMIT, where the server and the older apps make theirs."""

    legacy_ids = True
