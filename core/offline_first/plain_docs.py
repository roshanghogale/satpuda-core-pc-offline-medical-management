"""Plain shop records: reorders (pending_orders), write-offs (stock_disposals) and general
products -- one row each, no lines, no dues, no stock of their own (a write-off's stock moves
through stock_operations like every other movement).

The PC sends them up like everything else (outbox -> server_sync._build_docs), but nothing ever
wrote them DOWN: the pull handed them to server_entity_sync.sync_down_doc, which has no branch
for them and answered "applied" without writing, and the full copy (Go Offline, offline-first's
switch, the store switch) listed only the bills and masters -- after first wiping these three
tables (server_live._REPLACE_PULL_TABLES). So in offline-first a reorder saved on the phone or
on the other PC never reached this one, and the orders a shop had before the switch were gone
from the Reorder screen ("the reorder is not working", owner, 25 Oct 2026).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

PLAIN = ("pending_orders", "stock_disposals", "general_products")

# The number shown on screen: unique in the local table. Another device's record with the same
# number (two PCs that numbered the old way, by date and row) is kept under "<number>/<id>"
# rather than dropped.
_NUMBER_COLUMN = {"pending_orders": "order_no", "stock_disposals": "disposal_no"}
# Local bookkeeping the server does not own.
_NOT_COPIED = {"id", "deleted", "sync_status", "sync_state", "synced_at"}
# Server name -> local name (the server keeps qty; the PC, phone and server answers say quantity).
_ALIASES = {"quantity": ("quantity", "qty")}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "t")
    return bool(v)


def _value(v: Any) -> Any:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def apply_doc(conn: sqlite3.Connection, collection: str, doc_id: Any, doc: Optional[dict], *,
              delete: bool = False) -> str:
    """Write one server document into the local table: 'applied', 'deleted' or 'skipped'.

    Columns the document does not carry keep what this PC holds. The caller decides whether
    the PC's own unsent change wins (the pull skips such a record before calling this)."""
    if collection not in PLAIN:
        return "skipped"
    try:
        rid = int(doc_id)
    except (TypeError, ValueError):
        return "skipped"
    if rid <= 0:
        return "skipped"
    if delete or not isinstance(doc, dict) or _truthy(doc.get("deleted")):
        # Nothing reads these tables through a deleted flag: the row goes.
        conn.execute(f"DELETE FROM {collection} WHERE id=?", (rid,))
        return "deleted"
    vals: dict[str, Any] = {}
    for col in _columns(conn, collection):
        if col in _NOT_COPIED:
            continue
        for key in _ALIASES.get(col, (col,)):
            if key in doc:
                vals[col] = _value(doc.get(key))
                break
    if not vals:
        return "skipped"

    def _write(values: dict[str, Any]) -> None:
        names = list(values)
        sets = ", ".join(f"{c}=excluded.{c}" for c in names)
        conn.execute(
            f"INSERT INTO {collection} (id, {', '.join(names)}) VALUES (?{', ?' * len(names)}) "
            f"ON CONFLICT(id) DO UPDATE SET {sets}",
            (rid, *values.values()),
        )

    try:
        _write(vals)
    except sqlite3.IntegrityError as exc:
        num = _NUMBER_COLUMN.get(collection)
        if num and vals.get(num):
            try:
                _write({**vals, num: f"{vals[num]}/{rid}"})
                return "applied"
            except sqlite3.IntegrityError as exc2:
                exc = exc2
        # general_products: the same name under another id (two devices added it) -- this
        # PC keeps its own; a missing required field -- nothing to show anyway.
        log.warning("offline-first: %s/%s not written: %s", collection, rid, exc)
        return "skipped"
    return "applied"


def fetch_from_server(token: Optional[str] = None, *, include_deleted: bool = False) -> dict[str, list]:
    """Every record of the three collections the server holds for the active store."""
    from core import server_live as sl

    token = token or sl._token()
    out: dict[str, list] = {}
    for col in PLAIN:
        docs: list = []
        for page in sl._iter_collection_pages(token, col, since=None, include_deleted=include_deleted):
            docs.extend(d for d in page if isinstance(d, dict))
        out[col] = docs
    return out


def apply_fetched(conn: sqlite3.Connection, fetched: dict[str, Iterable[dict]],
                  pending: Optional[set] = None) -> int:
    """Write what fetch_from_server brought; a record this PC changed and has not sent yet is
    left alone (its own change goes up first)."""
    pending = pending or set()
    n = 0
    for col in PLAIN:
        for doc in fetched.get(col) or []:
            rid = doc.get("id", doc.get("local_id"))
            try:
                key = (col, int(rid))
            except (TypeError, ValueError):
                continue
            if key in pending:
                continue
            if apply_doc(conn, col, rid, doc) != "skipped":
                n += 1
    return n


def copy_from_server(conn: sqlite3.Connection, token: Optional[str] = None) -> int:
    """The full copy's share: every reorder, write-off and general product (live ones only)."""
    return apply_fetched(conn, fetch_from_server(token))
