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

# INSERT [OR x] INTO table (cols) VALUES (params)  -- one row, qmark style
_INSERT_RX = re.compile(
    r"^\s*INSERT\s+(?:OR\s+(?:IGNORE|REPLACE|ABORT|FAIL|ROLLBACK)\s+)?INTO\s+"
    r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<cols>[^()]*)\)\s*VALUES\s*\((?P<vals>[^()]*)\)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def device_range(conn: sqlite3.Connection):
    """(id_base, id_max) for this store's device, or None before registration."""
    try:
        rows = dict(conn.execute(
            "SELECT k, v FROM of_meta WHERE k IN ('id_base','id_max')"
        ).fetchall())
    except sqlite3.Error:
        return None
    try:
        base, top = int(rows.get("id_base") or 0), int(rows.get("id_max") or 0)
    except (TypeError, ValueError):
        return None
    return (base, top) if base > 0 and top > base else None


def next_id(conn: sqlite3.Connection, table: str) -> int:
    """The next free id of this device's range for ``table`` (reserved: never handed out twice)."""
    rng = device_range(conn)
    if not rng:
        raise RuntimeError("offline-first: this PC is not registered with the server yet")
    base, top = rng
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
