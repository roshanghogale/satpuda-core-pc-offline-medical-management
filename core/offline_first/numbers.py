"""Bill and purchase numbers from blocks reserved on the server; per-device numbers for returns,
payments and write-offs.

A block (for example sales 101-300 of FY 2026-27) is reserved while the PC is online; offline it
prints from the block, so two devices never print the same number. The worker tops blocks up
when fewer than half the numbers are left. If a block runs out while offline the PC carries on
from the highest number it knows (the old way) -- the server then keeps the bill whatever
happens: a clash is quarantined with the whole bill, never lost.
"""
from __future__ import annotations

import sqlite3

from core.offline_first.schema import meta_get

BLOCK_SIZE = {"sales": 200, "purchases": 50}


def take_serial(conn: sqlite3.Connection, kind: str, fy_start_year: int):
    """Next serial from this device's blocks for ``kind`` / FY, or None when none is left."""
    row = conn.execute(
        "SELECT from_serial, next_serial, to_serial FROM of_number_blocks "
        "WHERE kind=? AND fy_start_year=? AND next_serial <= to_serial "
        "ORDER BY from_serial LIMIT 1",
        (kind, int(fy_start_year)),
    ).fetchone()
    if not row:
        return None
    frm, nxt, _to = int(row[0]), int(row[1]), int(row[2])
    conn.execute(
        "UPDATE of_number_blocks SET next_serial=? WHERE kind=? AND fy_start_year=? AND from_serial=?",
        (nxt + 1, kind, int(fy_start_year), frm),
    )
    return nxt


def remaining(conn: sqlite3.Connection, kind: str, fy_start_year: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(to_serial - next_serial + 1), 0) FROM of_number_blocks "
        "WHERE kind=? AND fy_start_year=? AND next_serial <= to_serial",
        (kind, int(fy_start_year)),
    ).fetchone()
    return int(row[0] or 0)


def add_block(conn: sqlite3.Connection, kind: str, fy_start_year: int, frm: int, to: int,
              next_serial: int | None = None) -> None:
    """A block this device holds; ``next_serial`` for one it already used part of (handed
    back by the server on registration). A number never goes backwards."""
    nxt = max(int(frm), int(next_serial or frm))
    conn.execute(
        "INSERT INTO of_number_blocks (kind, fy_start_year, from_serial, to_serial, next_serial) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, fy_start_year, from_serial) "
        "DO UPDATE SET next_serial=MAX(next_serial, excluded.next_serial)",
        (kind, int(fy_start_year), int(frm), int(to), nxt),
    )


def doc_number(conn: sqlite3.Connection, prefix: str) -> str:
    """'SR3-17': the device's own running number for returns (SR/PR), supplier payments (SP),
    write-offs (SD) and reorders (PO), unique because the device number is in it."""
    dev = meta_get(conn, "device_no")
    row = conn.execute("SELECT next_no FROM of_doc_counters WHERE prefix=?", (prefix,)).fetchone()
    n = int(row[0]) if row else 1
    conn.execute(
        "INSERT INTO of_doc_counters (prefix, next_no) VALUES (?, ?) "
        "ON CONFLICT(prefix) DO UPDATE SET next_no=excluded.next_no",
        (prefix, n + 1),
    )
    return f"{prefix}{dev}-{n}" if dev else f"{prefix}{n}"


def maybe_doc_number(conn: sqlite3.Connection, prefix: str):
    """doc_number() when this PC is on offline-first, else None (the caller's old way)."""
    try:
        from core.offline_first.runtime import is_active, registered

        if is_active() and registered(conn):
            return doc_number(conn, prefix)
    except Exception:
        return None
    return None
