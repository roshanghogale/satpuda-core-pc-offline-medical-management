"""The background loop: note -> events -> push -> pull, every few seconds while online.

It uses its OWN connection to the store file. Every step that writes runs inside one
``BEGIN IMMEDIATE`` transaction: while it holds the write lock no other connection can write,
and the capture switch it turns off for pulled changes is turned back on before the commit,
so no other connection ever sees it off -- a cashier's save can never slip past the triggers.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from core.offline_first import client, numbers, outbox, pull, schema
from core.offline_first.schema import meta_get, meta_set

log = logging.getLogger(__name__)

INTERVAL_IDLE = 6.0
INTERVAL_BUSY = 1.0
FULL_STOCK_EVERY = 30 * 60
PULL_PAGE = 200


class HoldConnection(sqlite3.Connection):
    """commit()/rollback() from code called inside a batch must not end the batch."""

    hold = False

    def commit(self):
        if self.hold:
            return None
        return super().commit()

    def rollback(self):
        if self.hold:
            raise RuntimeError("offline-first: a step inside the batch asked to roll back")
        return super().rollback()


def open_worker_connection(path: str) -> HoldConnection:
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False, factory=HoldConnection,
                           isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextlib.contextmanager
def write_batch(conn: HoldConnection, *, capture_paused: bool = False):
    conn.execute("BEGIN IMMEDIATE")
    conn.hold = True
    try:
        if capture_paused:
            conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
        yield conn
        if capture_paused:
            conn.execute("UPDATE of_flags SET v='1' WHERE k='capture'")
        conn.hold = False
        conn.execute("COMMIT")
    except BaseException:
        conn.hold = False
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncCycle:
    """One store file. ``run_once`` does one full push + pull; the Worker calls it in a loop."""

    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[HoldConnection] = None
        self.refresh: set[int] = set()
        self.last_full_stock = 0.0

    def _c(self) -> HoldConnection:
        if self.conn is None:
            self.conn = open_worker_connection(self.path)
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None

    def top_up_numbers(self) -> None:
        from core.offline_first.runtime import current_fy

        conn = self._c()
        fy = current_fy()
        for kind, size in numbers.BLOCK_SIZE.items():
            if numbers.remaining(conn, kind, fy) < size // 2:
                blk = client.number_block(kind, fy, size)
                with write_batch(conn):
                    numbers.add_block(conn, kind, fy, int(blk["from_serial"]), int(blk["to_serial"]))

    def push(self) -> int:
        conn = self._c()
        install = meta_get(conn, "install_id") or client.install_id()
        with write_batch(conn):
            outbox.build_events(conn, install)
        sent = 0
        for _ in range(20):                      # up to 2,000 events a cycle
            events = outbox.pending_events(conn, 100)
            if not events:
                break
            answer = client.push(events)
            with write_batch(conn):
                res = outbox.apply_push_answer(conn, answer)
                if res["renumber_from"] is not None:
                    outbox.renumber_pending(conn, res["renumber_from"])
                meta_set(conn, "last_push_at", _now())
            self.refresh |= res["refresh_medicines"]
            sent += len(events)
            if res["renumber_from"] is None and len(events) < 100:
                break
        return sent

    def pull(self) -> int:
        conn = self._c()
        got = 0
        for _ in range(50):                      # up to 10,000 changes a cycle
            cursor = int(meta_get(conn, "pull_cursor", 0) or 0)
            page = client.changes(cursor, PULL_PAGE)
            changes = page.get("changes") or []
            if not changes:
                break
            touched = pull.touched_medicines(changes) | self.refresh
            snap = client.stock(sorted(touched)) if touched else {"stock": []}
            with write_batch(conn, capture_paused=True):
                pull.apply_page(conn, changes, to_revision=int(page.get("to_revision") or cursor))
                pull.set_stock_from_server(conn, snap.get("stock") or [])
                meta_set(conn, "last_pull_at", _now())
            self.refresh.clear()
            got += len(changes)
            if not page.get("has_more"):
                break
        if self.refresh or time.time() - self.last_full_stock > FULL_STOCK_EVERY:
            snap = client.stock(sorted(self.refresh) if self.refresh and
                                time.time() - self.last_full_stock <= FULL_STOCK_EVERY else None)
            with write_batch(conn, capture_paused=True):
                pull.set_stock_from_server(conn, snap.get("stock") or [])
            if not self.refresh:
                self.last_full_stock = time.time()
            self.refresh.clear()
        return got

    def run_once(self) -> dict:
        conn = self._c()
        schema.ensure_schema(conn) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='of_meta'").fetchone() is None else None
        try:
            self.top_up_numbers()
        except Exception as exc:                 # offline: blocks are topped up later
            log.debug("number blocks: %s", exc)
        sent = self.push()
        got = self.pull()
        with write_batch(conn):
            meta_set(conn, "last_error", None)
        return {"sent": sent, "pulled": got}


class Worker:
    def __init__(self, path: str):
        self.cycle = SyncCycle(path)
        self._stop = threading.Event()
        self._kick = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last: dict = {}

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="offline-first-sync", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._kick.set()
        if self.thread:
            self.thread.join(timeout)
        self.cycle.close()

    def kick(self) -> None:
        self._kick.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            wait = INTERVAL_IDLE
            try:
                self.last = self.cycle.run_once()
                if self.last.get("sent") or self.last.get("pulled"):
                    wait = INTERVAL_BUSY
            except Exception as exc:
                self.last = {"error": str(exc)}
                try:
                    conn = self.cycle._c()
                    with write_batch(conn):
                        meta_set(conn, "last_error", str(exc)[:300])
                        meta_set(conn, "last_error_at", _now())
                except Exception:
                    pass
                wait = 15.0
            self._kick.wait(wait)
            self._kick.clear()


_worker: Optional[Worker] = None
_worker_lock = threading.Lock()


def start_for(path: str) -> Worker:
    global _worker
    with _worker_lock:
        if _worker and _worker.cycle.path == path:
            _worker.start()
            return _worker
        if _worker:
            _worker.stop()
        _worker = Worker(path)
        _worker.start()
        return _worker


def stop() -> None:
    global _worker
    with _worker_lock:
        if _worker:
            _worker.stop()
        _worker = None


def kick() -> None:
    if _worker:
        _worker.kick()


def current() -> Optional[Worker]:
    return _worker
