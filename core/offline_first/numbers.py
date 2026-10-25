"""Bill and purchase numbers from blocks reserved on the server; per-device numbers for returns,
payments and write-offs.

A block (for example sales 101-300 of FY 2026-27) is reserved while the PC is online; offline it
prints from the block, so two devices never print the same number. The worker tops blocks up
when fewer than half the numbers are left. If a block runs out while offline the PC carries on
from the highest number it knows (the old way) -- the server then keeps the bill whatever
happens: a clash is quarantined with the whole bill, never lost.

A block can also be overtaken on the server: a data import (live store 154, Oct 2026: SCB1..
SCB4607 while the PC held 1-200) or a restored backup puts bills inside numbers this PC still
holds. While online the worker asks the server now and then (``client.number_head``) whether the
numbers left in each block are still free, and drops a block that is not (``drop_taken``); the
top-up then reserves a fresh one, which the server starts after every number issued. Offline the
blocks are used as they are -- nothing better is possible, and the server quarantines a clash.
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


def ranges_left(conn: sqlite3.Connection, fy_start_year: int) -> dict[str, list[tuple[int, int]]]:
    """The numbers this device has not printed yet, per kind: {"sales": [(51, 200)], ...}."""
    out: dict[str, list[tuple[int, int]]] = {}
    for kind, nxt, to in conn.execute(
        "SELECT kind, next_serial, to_serial FROM of_number_blocks "
        "WHERE fy_start_year=? AND next_serial <= to_serial ORDER BY kind, from_serial",
        (int(fy_start_year),),
    ):
        out.setdefault(str(kind), []).append((int(nxt), int(to)))
    return out


def drop_taken(conn: sqlite3.Connection, fy_start_year: int, answer: dict) -> list[dict]:
    """Drop every block whose unprinted part holds a number some other bill already has on the
    server (``answer`` is GET /api/sync/v2/number-head). ``taken`` is the highest such number,
    so a block is dropped only when it reaches this device's next number -- a bill printed while
    the question was out moved that on, and the numbers below it are spent either way. A dropped
    block keeps its row, used up (next_serial past to_serial), so registration's open_blocks can
    never hand it back."""
    dropped = []
    for kind in BLOCK_SIZE:
        for rng in ((answer or {}).get(kind) or {}).get("ranges") or []:
            try:
                to, taken = int(rng["to"]), int(rng.get("taken") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if taken <= 0:
                continue
            row = conn.execute(
                "SELECT from_serial, next_serial FROM of_number_blocks "
                "WHERE kind=? AND fy_start_year=? AND to_serial=? AND next_serial <= to_serial",
                (kind, int(fy_start_year), to),
            ).fetchone()
            if not row or taken < int(row[1]):
                continue
            conn.execute(
                "UPDATE of_number_blocks SET next_serial=to_serial + 1 "
                "WHERE kind=? AND fy_start_year=? AND from_serial=?",
                (kind, int(fy_start_year), int(row[0])),
            )
            dropped.append({"kind": kind, "from_serial": int(row[0]), "next_serial": int(row[1]),
                            "to_serial": to, "taken": taken})
    return dropped


def check_with_server(conn: sqlite3.Connection, fy_start_year: int, ask) -> list[dict]:
    """Ask the server (``ask(fy, ranges)`` = client.number_head) about the numbers left and drop
    the blocks that are overtaken. The caller commits; nothing is asked when nothing is left."""
    left = ranges_left(conn, fy_start_year)
    if not left:
        return []
    return drop_taken(conn, fy_start_year, ask(int(fy_start_year), left))


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
