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

from core.offline_first import client, numbers, outbox, plain_docs, pull, schema
from core.offline_first.schema import meta_get, meta_set

log = logging.getLogger(__name__)

INTERVAL_IDLE = 6.0
INTERVAL_BUSY = 1.0
FULL_STOCK_EVERY = 30 * 60
NUMBER_CHECK_EVERY = 5 * 60   # are the numbers left in the blocks still free on the server?
PULL_PAGE = 200
PLAIN_BACKFILL_KEY = "plain_backfill_v1"   # reorders / write-offs / general products fetched once


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


def _note_pulled(changes: list[dict]) -> None:
    """Tell the open screens what arrived (core.sync_status -> the shell's refresh poller), so
    a bill made on another device shows in Sales History without a manual refresh."""
    try:
        from core.sync_status import note_collection_change

        cols = {str(c.get("collection") or "") for c in changes} - {""}
        for col in cols:
            note_collection_change(col)
        if cols & {"sales", "purchases", "sales_returns", "purchase_returns", "stock_operations",
                   "stock_disposals"}:
            note_collection_change("medicines")  # their stock was set from the server too
    except Exception:
        pass


class SyncCycle:
    """One store file. ``run_once`` does one full push + pull; the Worker calls it in a loop."""

    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[HoldConnection] = None
        self.refresh: set[int] = set()
        self.last_full_stock = 0.0
        self.last_number_check = 0.0          # 0: the first cycle asks

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

    def check_numbers(self, *, force: bool = False) -> list[dict]:
        """Drop blocks the server has overtaken (numbers.drop_taken): on the first cycle, then
        every few minutes. A failed question (offline, or a server without the route) is asked
        again a minute later; the blocks are used as they are meanwhile."""
        from core.offline_first.runtime import current_fy

        if not force and time.time() - self.last_number_check < NUMBER_CHECK_EVERY:
            return []
        conn = self._c()
        fy = current_fy()
        left = numbers.ranges_left(conn, fy)
        try:
            answer = client.number_head(fy, left) if left else {}
        except Exception:
            self.last_number_check = time.time() - NUMBER_CHECK_EVERY + 60
            raise
        with write_batch(conn):
            dropped = numbers.drop_taken(conn, fy, answer)
        self.last_number_check = time.time()
        for d in dropped:
            log.warning("offline-first: %s numbers %s-%s are already used on the server (up to %s); "
                        "a fresh block is reserved", d["kind"], d["next_serial"], d["to_serial"], d["taken"])
        return dropped

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
            _note_pulled(changes)
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
            self.check_numbers()
        except Exception as exc:                 # offline: the blocks are used as they are
            log.debug("number check: %s", exc)
        try:
            self.top_up_numbers()
        except Exception as exc:                 # offline: blocks are topped up later
            log.debug("number blocks: %s", exc)
        sent = self.push()
        try:
            # A Main PC on the LAN: its counters' handed-over bills go on to the server
            # (core/lan/bridge.py). Nothing to do anywhere else.
            from core.lan.bridge import relay_to_cloud

            sent += relay_to_cloud(conn)
        except Exception as exc:
            log.debug("lan relay: %s", exc)
        got = self.pull()
        try:
            got += self.backfill_plain()
        except Exception as exc:                 # offline / older server: asked again next cycle
            log.debug("plain records backfill: %s", exc)
        with write_batch(conn):
            meta_set(conn, "last_error", None)
        return {"sent": sent, "pulled": got}

    def backfill_plain(self) -> int:
        """Once per store file: every reorder, write-off and general product the server holds.

        The pull and the switch's copy never wrote them (core/offline_first/plain_docs.py), so a
        PC already on offline-first lacks every one made before this version -- on the phone,
        on the other PCs, or Online before the switch. The pull keeps them in step after this.
        A record this PC changed and has not sent yet is left alone."""
        conn = self._c()
        if meta_get(conn, PLAIN_BACKFILL_KEY):
            return 0
        fetched = plain_docs.fetch_from_server(client._token(), include_deleted=True)
        with write_batch(conn, capture_paused=True):
            n = plain_docs.apply_fetched(conn, fetched, pull._locally_pending(conn))
            meta_set(conn, PLAIN_BACKFILL_KEY, _now())
        if n:
            _note_pulled([{"collection": c} for c in plain_docs.PLAIN if fetched.get(c)])
        return n


class Worker:
    """The loop, plus the two things that make it run NOW instead of at the next tick:
    ``kick()`` after this PC's own save (core/desktop_api.py), and the server's ``sync_hint``
    over /ws/sync when another device's change landed (core/sync_ws.py). Without them a
    bill made on the phone took up to 6 s to be pulled and the PC's own bill waited up to
    6 s before it was sent; the socket brings that to about one second each way."""

    def __init__(self, path: str, store_key: Optional[str] = None):
        self.cycle = SyncCycle(path)
        self._stop = threading.Event()
        self._kick = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last: dict = {}
        self.ws = None
        self.hints = 0
        # The store this file belongs to, fixed when the worker is made (the store being
        # opened). Its thread works FOR that store whatever the registry says later.
        #
        # The token, the store key file and every other "active store" lookup used to follow
        # the registry. Settings > Stores > Switch points the registry at the other store and
        # asks for a restart; until the app closed, this worker kept cycling on the OLD file
        # with the NEW store's token: the old store's unsent bills were pushed into the other
        # store's account and the other store's changes were written into the old file with
        # capture off (night audit, 25 Oct 2026).
        self.store_key = store_key if store_key is not None else _active_store_key()

    def _acting(self):
        """This worker's store for the code run in the calling thread (a no-op without one)."""
        key = str(self.store_key or "").strip()
        if not key:
            return contextlib.nullcontext()
        try:
            from core.store_manager import _find_store_by_key, acting_for_store

            if _find_store_by_key(key):
                return acting_for_store(key)
        except Exception as exc:
            log.warning("offline-first: store %s: %s", key, exc)
        return contextlib.nullcontext()          # removed from this PC meanwhile

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="offline-first-sync", daemon=True)
        self.thread.start()
        self._start_hints()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._kick.set()
        self._stop_hints()
        if self.thread:
            self.thread.join(timeout)
        self.cycle.close()

    def kick(self) -> None:
        self._kick.set()

    # -- the server's hints ------------------------------------------------------------
    def _on_hint(self, *_args, **_kwargs) -> None:
        self.hints += 1
        self.kick()

    def _start_hints(self) -> None:
        try:
            from core.sync_ws import SyncWebSocket

            if self.ws is None:
                self.ws = SyncWebSocket()
            def token() -> str:
                with self._acting():             # this worker's store, not the registry's
                    return client._token()

            # on_connect: the socket was down for a while -- pull whatever happened meanwhile.
            self.ws.start(token_fn=token, on_hint=self._on_hint, on_connect=self.kick)
        except Exception as exc:                 # no websocket module: polling alone
            log.warning("offline-first hints: %s", exc)
            self.ws = None

    def _stop_hints(self) -> None:
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                ws.stop()
            except Exception:
                pass

    def _loop(self) -> None:
        with self._acting():
            try:
                self._run()
            finally:
                # A cycle that outlived stop()'s wait reopened the file to note its error:
                # nothing of this thread stays open on it.
                if self._stop.is_set():
                    self.cycle.close()

    def _run(self) -> None:
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


def _active_store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return str(get_active_store_key() or "")
    except Exception:
        return ""


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


def hints_connected() -> bool:
    """Is the server's hint socket up (so other devices' changes arrive within a second)?"""
    w = _worker
    return bool(w and w.ws is not None and w.ws.connected)


def current() -> Optional[Worker]:
    return _worker
