"""Records moving between the Main PC's copy and a counter's copy over the LAN.

Everything that crosses is in the offline-first event format the PCs already send to the server
(core/offline_first/outbox.py): {seq, event_uuid, op, collection, doc, base_version} for a
record, {op: "stock", stock_ops: [{op_uuid, medicine_id, qty_delta}]} for stock movements. It is
written into the other copy with the same code that writes a change pulled from the server
(core/offline_first/pull.py), with the capture switch off, so nothing that arrives is noted as
this PC's own change and sent again.

Stock is never taken from a record's figure: before the records are written the stock of every
medicine they touch is noted, put back afterwards, and only the stock events' movements are
added. Each event is applied once per copy (of_lan_seen, by event id).

Three flows:

  handover  counter -> Main: the bills a counter made on its own copy while the Main PC was off.
            The Main PC keeps them in of_lan_relay, writes them into its copy at once, and sends
            them on to the cloud under the COUNTER's device id (relay_to_cloud, from the Main
            PC's sync loop). The server keeps one copy of every event (device + number, and
            event id), so an event that reaches it from both the counter and the Main PC is
            counted once. The counter is told what the server said (results) and marks its own
            events sent -- or sends them itself later and is answered "duplicate".
  feed      Main -> counter: the Main PC's own events (and other counters' handed-over ones),
            so the counter's copy is fresh for a failover even with no internet.
  bundle    Main -> counter, one bill at once: the bill a counter is about to print, with its
            customer and medicines, so it is printed here from this PC's copy.

Until the server has a handed-over stock movement the Main PC counts it as its own unsent
movement (pending_relay_stock, used by outbox.pending_stock_by_medicine), so a pull from the
server does not take it away again.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from core.offline_first.schema import meta_get, meta_set

FEED_PAGE = 200
HANDOVER_PAGE = 200

_TABLES = """
CREATE TABLE IF NOT EXISTS of_lan_relay (
    rid INTEGER PRIMARY KEY AUTOINCREMENT,
    install_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_uuid TEXT NOT NULL UNIQUE,
    collection TEXT,
    op TEXT,
    payload TEXT NOT NULL,
    received_at TEXT,
    cloud_status TEXT NOT NULL DEFAULT 'pending',
    outcome TEXT,
    flag_code TEXT,
    flag_detail TEXT,
    acked_at TEXT,
    reported INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS of_lan_relay_pending ON of_lan_relay (cloud_status, install_id, seq);
CREATE TABLE IF NOT EXISTS of_lan_seen (event_uuid TEXT PRIMARY KEY, origin TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS of_lan_handed (event_uuid TEXT PRIMARY KEY, seq INTEGER, at TEXT);
"""

_RELAY_DONE = ("applied", "flagged", "quarantined", "duplicate", "duplicate_uuid")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def ensure_tables(conn: sqlite3.Connection) -> None:
    for stmt in _TABLES.strip().split(";"):
        if stmt.strip():
            conn.execute(stmt)


# --------------------------------------------------------------------------------------------
# One connection per store file for the LAN work (the worker's kind: one write transaction at
# a time, with the capture switch).
# --------------------------------------------------------------------------------------------

_conns: dict[str, Any] = {}
_conn_lock = threading.RLock()


def connection(path: str):
    from core.offline_first.worker import open_worker_connection

    with _conn_lock:
        conn = _conns.get(path)
        if conn is None:
            conn = open_worker_connection(path)
            _conns[path] = conn
        return conn


def close_all() -> None:
    with _conn_lock:
        for conn in _conns.values():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        _conns.clear()


# --------------------------------------------------------------------------------------------
# Writing events / documents from another PC into this copy
# --------------------------------------------------------------------------------------------

def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _change_for_event(ev: dict) -> Optional[dict]:
    col = str(ev.get("collection") or "")
    doc = ev.get("doc")
    if not col or ev.get("op") == "stock":
        return None
    lid = _int((doc or {}).get("id") if isinstance(doc, dict) else 0) or _int(ev.get("local_id"))
    return {
        "collection": col,
        "local_id": lid,
        "doc": doc,
        "operation": "delete" if ev.get("op") == "delete" else "upsert",
        "base_version": _int(ev.get("base_version")),
    }


def _stock_of(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[int, Optional[int]]:
    out: dict[int, Optional[int]] = {}
    for mid in ids:
        row = conn.execute("SELECT stock_qty FROM medicines WHERE id=?", (int(mid),)).fetchone()
        out[int(mid)] = None if row is None else _int(row[0])
    return out


def apply_foreign(conn, *, events: Optional[list] = None, docs: Optional[list] = None,
                  origin: str = "") -> dict:
    """Write another PC's events (deduplicated by event id) and/or plain documents into this
    copy. Call inside ``write_batch(conn, capture_paused=True)``. Returns the collections
    touched and how many events were new."""
    from core.offline_first import pull

    ensure_tables(conn)
    changes: list[dict] = []
    deltas: dict[int, int] = {}
    new_uuids: list[str] = []
    for ev in sorted(events or [], key=lambda e: _int(e.get("seq"))):
        uid = str(ev.get("event_uuid") or "")
        if not uid:
            continue
        if conn.execute("SELECT 1 FROM of_lan_seen WHERE event_uuid=?", (uid,)).fetchone():
            continue
        new_uuids.append(uid)
        if ev.get("op") == "stock":
            for op in ev.get("stock_ops") or []:
                mid = _int(op.get("medicine_id"))
                if mid:
                    deltas[mid] = deltas.get(mid, 0) + _int(op.get("qty_delta"))
            continue
        ch = _change_for_event(ev)
        if ch is not None:
            changes.append(ch)
    for d in docs or []:
        if isinstance(d, dict) and d.get("collection"):
            changes.append(dict(d))

    touched = pull.touched_medicines(changes)
    for ch in changes:
        if ch.get("collection") == "medicines" and _int(ch.get("local_id")):
            touched.add(_int(ch.get("local_id")))
    before = _stock_of(conn, touched)
    if changes:
        cursor = meta_get(conn, "pull_cursor")
        pull.apply_page(conn, changes, to_revision=_int(cursor))
        if cursor is None:
            conn.execute("DELETE FROM of_meta WHERE k='pull_cursor'")
    # Stock only through movements: what the records did to it is taken back.
    for mid, qty in before.items():
        conn.execute("UPDATE medicines SET stock_qty=? WHERE id=?", (0 if qty is None else qty, mid))
    for mid, delta in deltas.items():
        if delta:
            conn.execute("UPDATE medicines SET stock_qty = COALESCE(stock_qty, 0) + ? WHERE id=?", (delta, mid))
    for ch in changes:
        if ch.get("operation") == "delete" or not _int(ch.get("local_id")):
            continue
        ver = _int(ch.get("version")) or (_int(ch.get("base_version")) + 1 if "base_version" in ch else 0)
        if ver > 0:
            conn.execute(
                "INSERT INTO of_versions (collection, local_id, version) VALUES (?,?,?) "
                "ON CONFLICT(collection, local_id) DO UPDATE SET version=MAX(version, excluded.version)",
                (str(ch["collection"]), _int(ch["local_id"]), ver),
            )
    now = _now()
    for uid in new_uuids:
        conn.execute("INSERT OR IGNORE INTO of_lan_seen (event_uuid, origin, at) VALUES (?,?,?)",
                     (uid, origin, now))
    cols = {str(c.get("collection")) for c in changes}
    if deltas or cols & {"sales", "purchases", "sales_returns", "purchase_returns", "stock_disposals"}:
        cols.add("medicines")
    return {"new_events": len(new_uuids), "collections": sorted(cols - {""}), "docs": len(changes)}


# --------------------------------------------------------------------------------------------
# Main PC: handover from a counter, and sending it on to the cloud
# --------------------------------------------------------------------------------------------

def receive_handover(path: str, install_id: str, events: list, origin: str = "") -> dict:
    """A counter's events: kept, written into this copy, and (later) sent to the cloud.
    Returns the event ids now held here and the server's answers for earlier ones."""
    from core.offline_first.worker import write_batch

    install_id = str(install_id or "").strip()
    if not install_id:
        raise ValueError("handover without the counter's device id")
    conn = connection(path)
    with _conn_lock:
        accepted: list[str] = []
        with write_batch(conn, capture_paused=True):
            ensure_tables(conn)
            fresh: list[dict] = []
            now = _now()
            for ev in events or []:
                uid = str(ev.get("event_uuid") or "")
                if not uid:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO of_lan_relay (install_id, seq, event_uuid, collection, op, payload, "
                    "received_at) VALUES (?,?,?,?,?,?,?)",
                    (install_id, _int(ev.get("seq")), uid, str(ev.get("collection") or ""), str(ev.get("op") or ""),
                     json.dumps(ev, default=str), now),
                )
                accepted.append(uid)
                if cur.rowcount:
                    fresh.append(ev)
            applied = apply_foreign(conn, events=fresh, origin=origin or install_id) if fresh else {
                "new_events": 0, "collections": []}
            results = _take_relay_results(conn, install_id)
    return {"accepted": accepted, "applied": applied, "results": results}


def _take_relay_results(conn, install_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT rid, seq, outcome, flag_code, flag_detail FROM of_lan_relay "
        "WHERE install_id=? AND cloud_status='sent' AND reported=0 ORDER BY seq LIMIT 500",
        (install_id,),
    ).fetchall()
    out = []
    for rid, seq, outcome, code, detail in rows:
        out.append({"seq": int(seq), "outcome": outcome or "applied", "flag_code": code, "flag_detail": detail})
        conn.execute("UPDATE of_lan_relay SET reported=1 WHERE rid=?", (rid,))
    return out


def relay_results(path: str, install_id: str) -> list[dict]:
    from core.offline_first.worker import write_batch

    conn = connection(path)
    with _conn_lock:
        if not _table_exists(conn, "of_lan_relay"):
            return []
        with write_batch(conn):
            return _take_relay_results(conn, install_id)


def relay_to_cloud(conn, push=None) -> int:
    """Send the counters' handed-over events to the server under each counter's device id.
    Called from the Main PC's sync loop (core/offline_first/worker.py) with its connection.
    ``push(install_id, events) -> answer`` is the server call (core.offline_first.client.push_as)."""
    from core.offline_first.worker import write_batch

    if not _table_exists(conn, "of_lan_relay"):
        return 0
    if push is None:
        from core.offline_first.client import push_as as push
    installs = [r[0] for r in conn.execute(
        "SELECT DISTINCT install_id FROM of_lan_relay WHERE cloud_status='pending'").fetchall()]
    sent = 0
    for install in installs:
        rows = conn.execute(
            "SELECT rid, seq, payload FROM of_lan_relay WHERE install_id=? AND cloud_status='pending' "
            "ORDER BY seq LIMIT 100", (install,),
        ).fetchall()
        if not rows:
            continue
        events = [json.loads(p) for _r, _s, p in rows]
        # An event a counter's older build made with no document: the server refuses the whole
        # push over it (core/offline_first/outbox.mend_docless) -- sent as the no-op filler.
        from core.offline_first.outbox import mend_docless

        for ev in events:
            mend_docless(ev)
        try:
            answer = push(install, events)
        except Exception as exc:
            with write_batch(conn):
                conn.execute("UPDATE of_lan_relay SET last_error=? WHERE install_id=? AND cloud_status='pending'",
                             (str(exc)[:300], install))
            raise
        by_seq = {_int(r.get("seq")): r for r in (answer or {}).get("results") or []}
        with write_batch(conn):
            for rid, seq, _p in rows:
                r = by_seq.get(int(seq))
                if not r:
                    continue
                outcome = str(r.get("outcome") or "")
                if outcome not in _RELAY_DONE:
                    conn.execute("UPDATE of_lan_relay SET last_error=? WHERE rid=?",
                                 (f"server: {outcome or 'no answer'}", rid))
                    continue
                stored = r.get("stored_outcome") if outcome == "duplicate" else None
                conn.execute(
                    "UPDATE of_lan_relay SET cloud_status='sent', outcome=?, flag_code=?, flag_detail=?, "
                    "acked_at=?, last_error=NULL WHERE rid=?",
                    (stored or outcome, r.get("flag_code"), r.get("flag_detail"), _now(), rid),
                )
                sent += 1
    return sent


def pending_relay_stock(conn: sqlite3.Connection) -> dict[int, int]:
    """Stock movements counters handed over that the server does not have yet, per medicine."""
    out: dict[int, int] = {}
    try:
        if not _table_exists(conn, "of_lan_relay"):
            return out
        rows = conn.execute(
            "SELECT payload FROM of_lan_relay WHERE cloud_status='pending' AND op='stock'").fetchall()
    except sqlite3.Error:
        return out
    for (payload,) in rows:
        try:
            ev = json.loads(payload)
        except ValueError:
            continue
        for op in ev.get("stock_ops") or []:
            mid = _int(op.get("medicine_id"))
            if mid:
                out[mid] = out.get(mid, 0) + _int(op.get("qty_delta"))
    return out


def relay_counts(conn: sqlite3.Connection) -> dict:
    if not _table_exists(conn, "of_lan_relay"):
        return {"relay_waiting": 0, "relay_sent": 0}
    row = conn.execute(
        "SELECT SUM(cloud_status='pending'), SUM(cloud_status='sent') FROM of_lan_relay").fetchone()
    return {"relay_waiting": int(row[0] or 0), "relay_sent": int(row[1] or 0)}


# --------------------------------------------------------------------------------------------
# Main PC: the feed of its own changes, and one bill's documents for printing
# --------------------------------------------------------------------------------------------

def feed(path: str, after: int, relay_after: int, exclude_install: str = "") -> dict:
    """This PC's own events after ``after`` and the handed-over events of OTHER counters after
    ``relay_after``. ``after`` < 0: the starting point (the first event the server may not have)."""
    conn = connection(path)
    with _conn_lock:
        if not _table_exists(conn, "of_events"):
            return {"events": [], "cursor": max(after, 0), "relay_events": [], "relay_cursor": max(relay_after, 0),
                    "has_more": False}
        if after < 0:
            low = conn.execute("SELECT MIN(seq) FROM of_events WHERE status='pending'").fetchone()[0]
            top = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM of_events").fetchone()[0]
            after = int(low) - 1 if low is not None else int(top or 0)
        rows = conn.execute(
            "SELECT seq, payload FROM of_events WHERE seq > ? ORDER BY seq LIMIT ?", (int(after), FEED_PAGE),
        ).fetchall()
        events = [json.loads(p) for _s, p in rows]
        cursor = int(rows[-1][0]) if rows else int(after)
        relay_events: list[dict] = []
        relay_cursor = max(int(relay_after), 0)
        if _table_exists(conn, "of_lan_relay"):
            if relay_after < 0:
                relay_cursor = int(conn.execute("SELECT COALESCE(MAX(rid), 0) FROM of_lan_relay").fetchone()[0] or 0)
            else:
                rrows = conn.execute(
                    "SELECT rid, install_id, payload FROM of_lan_relay WHERE rid > ? ORDER BY rid LIMIT ?",
                    (int(relay_after), FEED_PAGE),
                ).fetchall()
                for rid, inst, p in rrows:
                    relay_cursor = int(rid)
                    if exclude_install and inst == exclude_install:
                        continue
                    relay_events.append(json.loads(p))
        return {"events": events, "cursor": cursor, "relay_events": relay_events, "relay_cursor": relay_cursor,
                "has_more": len(rows) >= FEED_PAGE}


_BUNDLE_PARTS = {
    "sales": ("customers", "customer_id", "sales_items", "sale_id"),
    "purchases": ("suppliers", "supplier_id", "purchase_items", "purchase_id"),
}


def _version_of(conn, collection: str, local_id: int) -> int:
    try:
        row = conn.execute("SELECT version FROM of_versions WHERE collection=? AND local_id=?",
                           (collection, int(local_id))).fetchone()
    except sqlite3.Error:
        return 0
    return _int(row[0]) if row else 0


def bundle(path: str, collection: str, local_id: int) -> list[dict]:
    """One bill as documents, with what it shows: its party, its medicines and (for a sale) the
    returns against it. For a counter that prints it from its own copy."""
    from core.offline_first.outbox import build_doc

    collection = str(collection or "")
    if collection not in _BUNDLE_PARTS:
        raise ValueError(f"no bundle for {collection}")
    party_col, party_key, line_table, line_key = _BUNDLE_PARTS[collection]
    conn = connection(path)
    out: list[dict] = []
    with _conn_lock:
        row = conn.execute(f"SELECT * FROM {collection} WHERE id=?", (int(local_id),)).fetchone()
        if row is None:
            return out
        cols = [d[0] for d in conn.execute(f"SELECT * FROM {collection} LIMIT 0").description]
        rec = dict(zip(cols, row))

        def add(col: str, lid) -> None:
            lid = _int(lid)
            if lid <= 0:
                return
            doc = build_doc(conn, col, lid)
            if doc is None:
                return
            out.append({"collection": col, "local_id": lid, "doc": doc, "operation": "upsert",
                        "version": _version_of(conn, col, lid)})

        add(party_col, rec.get(party_key))
        if collection == "sales" and rec.get("doctor_id"):
            add("doctors", rec.get("doctor_id"))
        try:
            mids = [r[0] for r in conn.execute(
                f"SELECT DISTINCT medicine_id FROM {line_table} WHERE {line_key}=?", (int(local_id),)).fetchall()]
        except sqlite3.Error:
            mids = []
        for mid in mids:
            add("medicines", mid)
        add(collection, local_id)
        if collection == "sales":
            try:
                rids = [r[0] for r in conn.execute(
                    "SELECT id FROM sales_returns WHERE sale_id=?", (int(local_id),)).fetchall()]
            except sqlite3.Error:
                rids = []
            for rid in rids:
                add("sales_returns", rid)
    return out


# --------------------------------------------------------------------------------------------
# Counter: what to hand over, and what came back
# --------------------------------------------------------------------------------------------

def handover_batch(path: str, limit: int = HANDOVER_PAGE) -> tuple[str, list[dict]]:
    """(install id, this PC's events the Main PC has not been given yet). Changes still only
    noted are made into events first (the same step the sync loop takes)."""
    from core.offline_first import client, outbox
    from core.offline_first.worker import write_batch

    conn = connection(path)
    with _conn_lock:
        if not _table_exists(conn, "of_events"):
            return "", []
        install = meta_get(conn, "install_id") or ""
        if not install:
            try:
                install = client.install_id()
            except Exception:
                return "", []
        if not _table_exists(conn, "of_lan_handed"):
            with write_batch(conn):
                ensure_tables(conn)
        if conn.execute("SELECT 1 FROM of_dirty LIMIT 1").fetchone() or conn.execute(
                "SELECT 1 FROM of_stock_journal WHERE event_seq IS NULL LIMIT 1").fetchone():
            with write_batch(conn):
                outbox.build_events(conn, install)
                outbox.repair_docless_events(conn)
        rows = conn.execute(
            "SELECT payload FROM of_events WHERE status='pending' AND event_uuid NOT IN "
            "(SELECT event_uuid FROM of_lan_handed) ORDER BY seq LIMIT ?", (int(limit),),
        ).fetchall()
        return str(install), [json.loads(r[0]) for r in rows]


def mark_handed(path: str, events: list[dict], accepted: Iterable[str], results: list[dict]) -> int:
    """Note what the Main PC now holds; take the server's answers it passed back."""
    from core.offline_first import outbox
    from core.offline_first.worker import write_batch

    ok = set(accepted or [])
    conn = connection(path)
    with _conn_lock:
        with write_batch(conn):
            ensure_tables(conn)
            now = _now()
            for ev in events:
                uid = str(ev.get("event_uuid") or "")
                if uid in ok:
                    conn.execute("INSERT OR IGNORE INTO of_lan_handed (event_uuid, seq, at) VALUES (?,?,?)",
                                 (uid, _int(ev.get("seq")), now))
            if results:
                outbox.apply_push_answer(conn, {"results": list(results)})
    return len(ok)


def apply_feed(path: str, page: dict, origin: str = "main") -> dict:
    from core.offline_first.worker import write_batch

    conn = connection(path)
    events = list(page.get("events") or []) + list(page.get("relay_events") or [])
    with _conn_lock:
        with write_batch(conn, capture_paused=True):
            ensure_tables(conn)
            res = apply_foreign(conn, events=events, origin=origin) if events else {"new_events": 0,
                                                                                   "collections": []}
            meta_set(conn, "lan_feed_cursor", int(page.get("cursor") or 0))
            meta_set(conn, "lan_relay_cursor", int(page.get("relay_cursor") or 0))
    return res


def apply_docs(path: str, docs: list[dict], origin: str = "main") -> dict:
    from core.offline_first.worker import write_batch

    conn = connection(path)
    with _conn_lock:
        with write_batch(conn, capture_paused=True):
            return apply_foreign(conn, docs=docs, origin=origin)


def feed_cursors(path: str, main_id: str) -> tuple[int, int]:
    """Where this counter is in the Main PC's feed (-1, -1: start; also when the Main PC
    changed)."""
    from core.offline_first.worker import write_batch

    conn = connection(path)
    with _conn_lock:
        if not _table_exists(conn, "of_meta"):
            return -1, -1
        if (meta_get(conn, "lan_feed_main") or "") != (main_id or ""):
            with write_batch(conn):
                meta_set(conn, "lan_feed_main", main_id or "")
                meta_set(conn, "lan_feed_cursor", None)
                meta_set(conn, "lan_relay_cursor", None)
            return -1, -1
        a = meta_get(conn, "lan_feed_cursor")
        b = meta_get(conn, "lan_relay_cursor")
        return (_int(a) if a is not None else -1), (_int(b) if b is not None else -1)


def counter_counts(path: str) -> dict:
    conn = connection(path)
    with _conn_lock:
        if not _table_exists(conn, "of_events"):
            return {"waiting": 0, "not_handed": 0}
        ensure = _table_exists(conn, "of_lan_handed")
        waiting = conn.execute("SELECT COUNT(*) FROM of_events WHERE status='pending'").fetchone()[0]
        not_handed = waiting
        if ensure:
            not_handed = conn.execute(
                "SELECT COUNT(*) FROM of_events WHERE status='pending' AND event_uuid NOT IN "
                "(SELECT event_uuid FROM of_lan_handed)").fetchone()[0]
        noted = conn.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0]
        return {"waiting": int(waiting), "not_handed": int(not_handed) + int(noted)}
