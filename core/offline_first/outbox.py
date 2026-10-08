"""Noted changes -> numbered events -> the server.

``build_events`` turns what the triggers noted (of_dirty, of_stock_journal) into events in
of_events, numbered 1, 2, 3 ... after the last number the server confirmed. The record is
copied into the event when the event is made, with the same builders the bulk "Push to server"
uses, so a resend is byte-for-byte the same event. Masters go first, then documents, then one
stock event with every stock movement since the last one.

``apply_push_answer`` records what the server did with each event. An event stays until the
server has it (applied, flagged or quarantined -- all three are kept on the server); nothing is
ever deleted on this side.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.offline_first.schema import meta_get, meta_set

ORDER = (
    "customers", "suppliers", "doctors", "medicines", "general_products",
    "racks", "sections", "boxes",
    "sales", "purchases", "customer_payments", "supplier_payments",
    "sales_returns", "purchase_returns", "stock_disposals", "pending_orders",
    "pharmacy_profile", "settings",
)
_ORDER_SQL = "CASE collection " + " ".join(
    f"WHEN '{c}' THEN {i}" for i, c in enumerate(ORDER)
) + f" ELSE {len(ORDER)} END"

MAX_EVENTS_PER_BUILD = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_doc(conn: sqlite3.Connection, collection: str, local_id: int) -> Optional[dict]:
    """The record as the server expects it, or None when it no longer exists locally."""
    from core import server_sync as ss
    from core import server_entity_sync as fb

    if collection == "pharmacy_profile":
        row = conn.execute("SELECT * FROM pharmacy_profile ORDER BY id LIMIT 1").fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM pharmacy_profile LIMIT 0").description]
        doc = dict(zip(cols, row))
        doc.pop("id", None)
        doc.pop("logo_path", None)
        return doc
    if collection == "settings":
        row = conn.execute("SELECT name, value FROM settings WHERE id=?", (int(local_id),)).fetchone()
        return {"name": row[0], "value": row[1]} if row else None
    if collection in ("racks", "sections", "boxes"):
        build = {"racks": fb.build_rack_payload, "sections": fb.build_section_payload,
                 "boxes": fb.build_box_payload}[collection]
        doc = build(conn, int(local_id))
        if doc:
            doc = dict(doc)
            doc["id"] = int(local_id)
        return doc
    docs = ss._build_docs(conn, collection, ids=[int(local_id)])
    return docs[0] if docs else None


def _next_seq(conn: sqlite3.Connection) -> int:
    top = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM of_events").fetchone()[0]
    server = int(meta_get(conn, "server_last_seq", 0) or 0)
    return max(int(top or 0), server) + 1


def build_events(conn: sqlite3.Connection, install_id: str) -> int:
    """Turn noted changes into events. Call inside the worker's write transaction."""
    made = 0
    rows = conn.execute(
        f"SELECT collection, local_id, op FROM of_dirty ORDER BY {_ORDER_SQL}, at LIMIT ?",
        (MAX_EVENTS_PER_BUILD,),
    ).fetchall()
    for collection, local_id, op in rows:
        doc = None
        if op != "delete":
            doc = build_doc(conn, collection, int(local_id))
            if doc is None and collection not in ("pharmacy_profile", "settings"):
                op = "delete"
        if op == "delete":
            if collection in ("pharmacy_profile", "settings"):
                conn.execute("DELETE FROM of_dirty WHERE collection=? AND local_id=?", (collection, local_id))
                continue
            doc = {"id": int(local_id)}
        if collection in ("pharmacy_profile", "settings"):
            ver = None
        else:
            v = conn.execute(
                "SELECT version FROM of_versions WHERE collection=? AND local_id=?",
                (collection, int(local_id)),
            ).fetchone()
            ver = int(v[0]) if v else 0
        seq = _next_seq(conn)
        event = {
            "seq": seq,
            "event_uuid": f"pc-{uuid.uuid4().hex}",
            "op": op,
            "collection": collection,
            "doc": doc,
            "base_version": ver,
            "device_time": _now(),
        }
        conn.execute(
            "INSERT INTO of_events (seq, event_uuid, collection, op, local_id, base_version, payload, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (seq, event["event_uuid"], collection, op, int(local_id), ver,
             json.dumps(event, default=str), event["device_time"]),
        )
        conn.execute("DELETE FROM of_dirty WHERE collection=? AND local_id=?", (collection, local_id))
        made += 1

    journal = conn.execute(
        "SELECT id, medicine_id, delta FROM of_stock_journal WHERE event_seq IS NULL ORDER BY id LIMIT 2000"
    ).fetchall()
    if journal:
        seq = _next_seq(conn)
        # The op ids carry the event's own random id: a journal numbered from 1 again (a
        # reinstalled PC on a fresh store file) must never repeat an op id the server already
        # holds -- the server keeps ONE movement per op id and drops a repeat as a resend.
        # A real resend sends this stored event as it is, with the same ids.
        event_uuid = f"pc-{uuid.uuid4().hex}"
        ops = [
            {"op_uuid": f"{event_uuid}:j{jid}", "medicine_id": int(mid), "op": "adjust", "qty_delta": int(delta)}
            for jid, mid, delta in journal
        ]
        event = {
            "seq": seq,
            "event_uuid": event_uuid,
            "op": "stock",
            "collection": "stock_operations",
            "stock_ops": ops,
            "device_time": _now(),
        }
        conn.execute(
            "INSERT INTO of_events (seq, event_uuid, collection, op, local_id, base_version, payload, created_at) "
            "VALUES (?,?,?,?,NULL,NULL,?,?)",
            (seq, event["event_uuid"], "stock_operations", "stock", json.dumps(event), event["device_time"]),
        )
        last = journal[-1][0]
        conn.execute(
            "UPDATE of_stock_journal SET event_seq=? WHERE event_seq IS NULL AND id <= ?", (seq, last),
        )
        made += 1
    return made


def pending_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT payload FROM of_events WHERE status='pending' ORDER BY seq LIMIT ?", (limit,),
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def renumber_pending(conn: sqlite3.Connection, start: int) -> None:
    """The server holds numbers up to start - 1 already (a restored or reset PC): move this
    PC's unsent events after them, in the same order. Call inside the write transaction."""
    rows = conn.execute(
        "SELECT seq, payload FROM of_events WHERE status='pending' ORDER BY seq"
    ).fetchall()
    if not rows:
        return
    # Out of the way first (seq is the primary key), then into place.
    offset = 10 ** 12
    conn.execute("UPDATE of_stock_journal SET event_seq = event_seq + ? WHERE event_seq IN "
                 "(SELECT seq FROM of_events WHERE status='pending')", (offset,))
    conn.execute("UPDATE of_events SET seq = seq + ? WHERE status='pending'", (offset,))
    n = int(start)
    for old_seq, payload in rows:
        ev = json.loads(payload)
        ev["seq"] = n
        conn.execute("UPDATE of_events SET seq=?, payload=? WHERE seq=?", (n, json.dumps(ev, default=str), old_seq + offset))
        conn.execute("UPDATE of_stock_journal SET event_seq=? WHERE event_seq=?", (n, old_seq + offset))
        n += 1


def apply_push_answer(conn: sqlite3.Connection, answer: dict) -> dict:
    """Record the server's answer to a push. Returns {'refresh_medicines': set, 'renumber_from': n|None}."""
    out = {"refresh_medicines": set(), "renumber_from": None, "seq_conflict": False}
    for r in answer.get("results") or []:
        seq = int(r.get("seq") or 0)
        outcome = str(r.get("outcome") or "")
        if outcome == "seq_conflict":
            out["seq_conflict"] = True
            continue
        if outcome not in ("applied", "flagged", "quarantined", "duplicate", "duplicate_uuid"):
            continue
        stored = r.get("stored_outcome") if outcome == "duplicate" else None
        conn.execute(
            "UPDATE of_events SET status='sent', outcome=?, flag_code=?, flag_detail=?, acked_at=? WHERE seq=?",
            (stored or outcome, r.get("flag_code"), r.get("flag_detail"), _now(), seq),
        )
        if outcome in ("duplicate", "duplicate_uuid"):
            for (mid,) in conn.execute(
                "SELECT DISTINCT medicine_id FROM of_stock_journal WHERE event_seq=?", (seq,)
            ).fetchall():
                out["refresh_medicines"].add(int(mid))
    last = answer.get("last_seq")
    if last is not None:
        meta_set(conn, "server_last_seq", int(last))
    missing = answer.get("missing_from")
    if missing is not None:
        out["renumber_from"] = int(missing)
    elif out["seq_conflict"] and last is not None:
        out["renumber_from"] = int(last) + 1
    return out


def pending_stock_by_medicine(conn: sqlite3.Connection) -> dict[int, int]:
    """Stock movements of this PC the server does not have yet, per medicine."""
    rows = conn.execute(
        "SELECT medicine_id, SUM(delta) FROM of_stock_journal "
        "WHERE event_seq IS NULL OR event_seq IN (SELECT seq FROM of_events WHERE status='pending') "
        "GROUP BY medicine_id"
    ).fetchall()
    return {int(m): int(d or 0) for m, d in rows}


def counts(conn: sqlite3.Connection) -> dict:
    """For the status line: what is waiting, what was flagged."""
    row = conn.execute(
        "SELECT "
        " (SELECT COUNT(*) FROM of_dirty),"
        " (SELECT COUNT(*) FROM of_events WHERE status='pending'),"
        " (SELECT COUNT(*) FROM of_stock_journal WHERE event_seq IS NULL),"
        " (SELECT COUNT(*) FROM of_events WHERE status='sent' AND outcome IN ('flagged','quarantined'))"
    ).fetchone()
    return {"noted": int(row[0]), "waiting": int(row[1]), "stock_moves_waiting": int(row[2]),
            "flagged": int(row[3])}
