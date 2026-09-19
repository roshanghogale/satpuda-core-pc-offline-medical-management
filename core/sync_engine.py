"""
Option B SyncEngine (Phase B1 / Mac2).

- WebSocket sync_hint → pull GET /api/sync/changes/full
- Safety poll GET /api/sync/status every 45s
- Apply ascending revisions into SQLite via apply_server_doc
- sync_inbox for idempotency; UI notified via on_change / note_collection_change
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional, Set

log = logging.getLogger(__name__)

OnChangeCb = Callable[[str], None]

_SAFETY_POLL_SEC = 45.0
_PULL_PAGE = 200


def _collections_from_change_list(changes) -> Set[str]:
    cols: Set[str] = set()
    for c in changes or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("collection") or "").strip().lower()
        if name:
            cols.add(name)
    return cols


def _fetch_touched_collections(token: str, after_head: int) -> Set[str]:
    """Peek changelog after `after_head` and return distinct collection names (no SQLite apply)."""
    from core import server_api as api

    touched: Set[str] = set()
    cursor = max(0, int(after_head or 0))
    pages = 0
    while pages < 25:
        page = api.get_sync_changes(
            token, after=cursor, limit=_PULL_PAGE, timeout=20.0
        )
        changes = page.get("changes") or []
        if not changes:
            break
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            col = str(ch.get("collection") or "").strip().lower()
            if col:
                touched.add(col)
            try:
                rev = int(ch.get("revision") or 0)
            except (TypeError, ValueError):
                rev = 0
            if rev > cursor:
                cursor = rev
        pages += 1
        if len(changes) < _PULL_PAGE:
            break
    return touched

_engine_lock = threading.Lock()
_engine: Optional["SyncEngine"] = None


def is_sync_engine_v2_enabled() -> bool:
    """B3: prefer USE_REVISION_SYNC (SYNC_ENGINE_V2 still accepted)."""
    from core.revision_sync_flags import is_revision_sync_enabled

    return is_revision_sync_enabled()


def ensure_sync_inbox(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_inbox (
            revision INTEGER PRIMARY KEY,
            collection TEXT,
            local_id INTEGER,
            operation TEXT,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_inbox_col "
        "ON sync_inbox(collection, local_id)"
    )


class SyncEngine:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._pull_lock = threading.Lock()
        self._on_change: Optional[OnChangeCb] = None
        self._db_path: Optional[str] = None
        self._safety_thread: Optional[threading.Thread] = None
        self._started = False
        self._our_device_id: Optional[str] = None
        self._hints_only = False
        self._last_ui_head = 0

    @property
    def started(self) -> bool:
        return self._started

    def start(
        self,
        conn,
        db_path: Optional[str] = None,
        on_change: OnChangeCb = None,
        *,
        adopt_server_head: bool = False,
        hints_only: bool = False,
    ) -> bool:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return False

        path = (db_path or "").strip() or (
            getattr(conn, "db_path", None) if conn is not None else None
        ) or None
        self._db_path = path
        self._on_change = on_change
        self._hints_only = bool(hints_only)
        self._stop.clear()
        self._our_device_id = self._resolve_device_id()

        if not self._hints_only:
            # Legacy SQLite apply path (kept for rollback; Online uses hints_only)
            try:
                if conn is not None:
                    ensure_sync_inbox(conn)
                    from core.sync_outbox import ensure_sync_outbox

                    ensure_sync_outbox(conn)
                    conn.commit()
            except Exception as exc:
                log.warning("sync_inbox on shared conn: %s", exc)

            if adopt_server_head:
                try:
                    from core import server_api as api
                    from core import sync_revision as rev

                    status = api.get_sync_status(self._token(), timeout=20.0)
                    server_head = int(status.get("head_revision") or 0)
                    if server_head > 0:
                        rev.set_head_revision(server_head)
                    log.info(
                        "sync_engine adopt_server_head=%s (skipped initial catch-up)",
                        server_head,
                    )
                except Exception as exc:
                    log.warning("sync_engine adopt_server_head failed: %s", exc)
            else:
                try:
                    self._initial_catchup()
                except Exception as exc:
                    log.warning("sync_engine initial catch-up: %s", exc)
        else:
            log.info("sync_engine hints_only — no SQLite catch-up / apply")
            try:
                from core import server_api as api

                status = api.get_sync_status(self._token(), timeout=20.0)
                self._last_ui_head = int(status.get("head_revision") or 0)
                log.info("sync_engine hints_only baseline head=%s", self._last_ui_head)
            except Exception as exc:
                log.debug("sync_engine hints_only baseline head: %s", exc)
                self._last_ui_head = 0

        # WebSocket hints
        try:
            from core.sync_ws import get_sync_ws

            get_sync_ws().start(
                token_fn=self._token,
                on_hint=self._on_hint,
                on_connect=None if self._hints_only else self._reconcile_on_connect,
            )
        except Exception as exc:
            log.warning("sync_engine ws start: %s", exc)

        # Safety poll (hints_only: scoped UI refresh when head moves; no SQLite pull)
        if not self._safety_thread or not self._safety_thread.is_alive():
            self._safety_thread = threading.Thread(
                target=self._safety_loop, daemon=True, name="SyncEngineSafetyPoll"
            )
            self._safety_thread.start()

        self._started = True
        log.info(
            "SyncEngine started (hints_only=%s db=%s)",
            self._hints_only,
            path or "(none)",
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        self._started = False
        try:
            from core.sync_ws import get_sync_ws

            get_sync_ws().stop()
        except Exception:
            pass
        thread = self._safety_thread
        self._safety_thread = None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass

    def is_alive(self) -> bool:
        return bool(
            self._started
            and self._safety_thread is not None
            and self._safety_thread.is_alive()
        )

    def pull_through(self, after: Optional[int] = None) -> int:
        """Pull and apply all changes after `after` (default: local head)."""
        from core import sync_revision as rev

        start_after = rev.get_head_revision() if after is None else max(0, int(after))
        with self._pull_lock:
            return self._pull_through_locked(start_after)

    def _token(self) -> str:
        from core.server_api import store_token_for_active

        return store_token_for_active()

    def _resolve_device_id(self) -> Optional[str]:
        try:
            from core.store_manager import get_active_store_key
            from core.server_api import load_session

            sk = get_active_store_key() or ""
            sess = load_session(sk) if sk else {}
            did = (sess.get("device_id") or sk or "").strip()
            return did or None
        except Exception:
            return None

    def _open_poll_conn(self):
        from core.db_utils import open_store_db

        if self._db_path:
            return open_store_db(self._db_path, timeout=60.0), True
        raise RuntimeError("SyncEngine requires db_path for dedicated poll_conn")

    def _initial_catchup(self) -> None:
        from core import server_api as api
        from core import sync_revision as rev
        from core.db_utils import open_store_db

        path = self._db_path
        if not path:
            return
        poll_conn = open_store_db(path, timeout=60.0)
        try:
            ensure_sync_inbox(poll_conn)
            poll_conn.commit()
            local = rev.get_head_revision()
            # Pre-changelog history: one watermark incremental pull when cursor is 0
            if local <= 0:
                try:
                    from core import server_live as live

                    n = live.sync_down_all(poll_conn, incremental=True)
                    log.info("sync_engine watermark catch-up: %s change(s)", n)
                except Exception as exc:
                    log.warning("sync_engine watermark catch-up failed: %s", exc)
            # Apply any revision log entries (also covers local==0 after B0)
            applied = self._pull_through_locked(local, conn=poll_conn)
            if applied:
                log.info("sync_engine revision catch-up: %s change(s)", applied)
            # Align cursor to server head if still behind with empty pages
            try:
                status = api.get_sync_status(self._token(), timeout=15.0)
                server_head = int(status.get("head_revision") or 0)
                if server_head > rev.get_head_revision():
                    # Empty gap (e.g. only skipped writes) — advance carefully via pull
                    more = self._pull_through_locked(rev.get_head_revision(), conn=poll_conn)
                    if more == 0 and server_head > rev.get_head_revision():
                        # No changelog rows (shouldn't happen) — snap to server head
                        rev.set_head_revision(server_head)
            except Exception as exc:
                log.debug("sync_engine head align: %s", exc)
        finally:
            try:
                poll_conn.commit()
            except Exception:
                pass
            poll_conn.close()

    def _device_ids_match(self, a: Optional[str], b: Optional[str]) -> bool:
        if not a or not b:
            return False
        return str(a).strip().lower() == str(b).strip().lower()

    def _reconcile_on_connect(self) -> None:
        """On WS (re)connect, pull if server head is ahead of local cursor."""
        if self._stop.is_set():
            return
        try:
            from core import server_api as api
            from core import sync_revision as rev

            status = api.get_sync_status(self._token(), timeout=15.0)
            server_head = int(status.get("head_revision") or 0)
            local = rev.get_head_revision()
            log.info(
                "[SYNC][HINT] ws reconnect reconcile server=%s local=%s",
                server_head,
                local,
            )
            if server_head > local:
                self.pull_through(local)
        except Exception as exc:
            log.warning("[SYNC][HINT] reconcile on connect: %s", exc)

    def _on_hint(
        self,
        head_revision: int,
        source_device_id: Optional[str],
        changes=None,
        full_refresh: bool = False,
    ) -> None:
        if self._stop.is_set():
            return
        try:
            head = int(head_revision or 0)
        except (TypeError, ValueError):
            head = 0
        change_list = list(changes or [])
        log.info(
            "[SYNC][HINT] head=%s source=%s our=%s changes=%s full=%s hints_only=%s",
            head,
            source_device_id,
            self._our_device_id,
            len(change_list),
            full_refresh,
            self._hints_only,
        )
        timing_id = None
        try:
            from core import sync_timing

            timing_id = sync_timing.begin_hint(
                head=head,
                source=str(source_device_id or ""),
                changes=len(change_list),
                full=bool(full_refresh),
            )
        except Exception:
            timing_id = None

        # Server-only Online: never apply changelog into SQLite — UI refetches
        # only when collections actually changed (or explicit full_refresh).
        if self._hints_only:
            full = bool(full_refresh)
            prev_head = int(self._last_ui_head or 0)
            if head <= prev_head and not change_list and not full:
                try:
                    from core import sync_timing
                    sync_timing.end(timing_id, outcome="skip_stale")
                except Exception:
                    pass
                return
            if head > prev_head:
                self._last_ui_head = head

            cols = _collections_from_change_list(change_list)
            # Head advanced with no collection list: peek changelog (scoped), never fake full.
            if not cols and not full and head > prev_head:
                try:
                    cols = _fetch_touched_collections(self._token(), prev_head)
                    change_list = [{"collection": c} for c in sorted(cols)]
                    try:
                        from core import sync_timing
                        sync_timing.mark(timing_id, "peek_collections", cols=len(cols))
                    except Exception:
                        pass
                except Exception as exc:
                    log.debug("[SYNC][HINT] peek collections: %s", exc)

            if not full and not cols:
                # Head-only / empty — track head, skip UI storm.
                try:
                    from core import sync_timing
                    sync_timing.end(timing_id, outcome="skip_empty")
                except Exception:
                    pass
                return

            try:
                from core import store_live_refresh

                store_live_refresh.emit(
                    {
                        "head_revision": head,
                        "source_device_id": source_device_id,
                        "changes": change_list,
                        "full_refresh": full,
                        "timing_id": timing_id,
                    }
                )
                try:
                    from core import sync_timing
                    sync_timing.mark(
                        timing_id,
                        "live_refresh_emitted",
                        cols=",".join(sorted(cols))[:120],
                    )
                except Exception:
                    pass
            except Exception as exc:
                log.debug("store_live_refresh emit: %s", exc)

            # Peer wins: cancel matching pending mutation rows (never on self-hints).
            try:
                is_self = self._device_ids_match(source_device_id, self._our_device_id)
                if not is_self:
                    from core.online_mutation_queue import cancel_matching

                    for ch in change_list:
                        if not isinstance(ch, dict):
                            continue
                        col = str(ch.get("collection") or "").strip()
                        try:
                            lid = int(ch.get("local_id") or ch.get("id") or 0)
                        except (TypeError, ValueError):
                            lid = 0
                        cu = str(ch.get("client_uuid") or "").strip() or None
                        if col and (lid > 0 or cu):
                            cancel_matching(
                                collection=col,
                                local_id=lid if lid > 0 else None,
                                client_uuid=cu,
                            )
            except Exception as exc:
                log.debug("peer cancel_matching: %s", exc)

            # Desktop WebView poller reads refresh_collections from sync_status.
            try:
                from core.sync_status import note_collection_change

                if full:
                    note_collection_change("all")
                else:
                    for col in cols:
                        note_collection_change(col)
            except Exception:
                pass

            if self._on_change:
                try:
                    if full:
                        self._on_change("all")
                    else:
                        for col in cols:
                            self._on_change(col)
                except Exception:
                    pass
            try:
                from core import sync_timing
                sync_timing.end(timing_id, outcome="hints_only_dispatched")
            except Exception:
                pass
            return

        try:
            from core import store_live_refresh

            store_live_refresh.emit(
                {
                    "head_revision": head,
                    "source_device_id": source_device_id,
                    "changes": change_list,
                    "full_refresh": bool(full_refresh),
                }
            )
        except Exception as exc:
            log.debug("store_live_refresh emit: %s", exc)

        if self._on_change:
            try:
                self._on_change("server")
            except Exception:
                pass

        is_self = self._device_ids_match(source_device_id, self._our_device_id)
        if is_self:
            try:
                from core import sync_revision as rev

                local = rev.get_head_revision()
                if head > int(local):
                    self.pull_through(local)
            except Exception as exc:
                log.warning("[SYNC][HINT] self-hint pull: %s", exc)
            return
        try:
            from core import sync_revision as rev

            if head <= rev.get_head_revision():
                return
            self.pull_through()
        except Exception as exc:
            log.warning("[SYNC][HINT] on_hint: %s", exc)

    def _safety_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(_SAFETY_POLL_SEC)
            if self._stop.is_set():
                break
            try:
                from core.sync_prefs import is_online_mode

                if not is_online_mode():
                    continue
                from core import server_api as api

                status = api.get_sync_status(self._token(), timeout=15.0)
                server_head = int(status.get("head_revision") or 0)

                if self._hints_only:
                    # Only refresh UI when another device advanced the store head.
                    if server_head <= int(self._last_ui_head or 0):
                        continue
                    prev = int(self._last_ui_head or 0)
                    self._last_ui_head = server_head
                    log.info(
                        "[SYNC][SAFETY] hints_only head %s → %s (scoped UI refresh)",
                        prev,
                        server_head,
                    )
                    cols: Set[str] = set()
                    try:
                        cols = _fetch_touched_collections(self._token(), prev)
                    except Exception as exc:
                        log.debug("[SYNC][SAFETY] peek collections: %s", exc)
                    if not cols:
                        # Head moved but no changelog rows readable — skip full storm.
                        continue
                    change_list = [{"collection": c} for c in sorted(cols)]
                    try:
                        from core import store_live_refresh

                        store_live_refresh.emit(
                            {
                                "head_revision": server_head,
                                "source_device_id": None,
                                "changes": change_list,
                                "full_refresh": False,
                            }
                        )
                    except Exception:
                        pass
                    try:
                        from core.sync_status import note_collection_change

                        for col in cols:
                            note_collection_change(col)
                    except Exception:
                        pass
                    if self._on_change:
                        try:
                            for col in cols:
                                self._on_change(col)
                        except Exception:
                            pass
                    continue

                from core import sync_revision as rev

                local = rev.get_head_revision()
                if server_head > local:
                    log.info(
                        "[SYNC][SAFETY] pull after=%s server=%s",
                        local,
                        server_head,
                    )
                    self.pull_through(local)
                    try:
                        from core.sync_outbox import flush_pending

                        poll_conn, own = self._open_poll_conn()
                        try:
                            flush_pending(poll_conn)
                        finally:
                            if own:
                                try:
                                    poll_conn.close()
                                except Exception:
                                    pass
                    except Exception as exc:
                        log.debug("sync_engine outbox flush: %s", exc)
            except Exception as exc:
                log.debug("sync_engine safety: %s", exc)

    def _pull_through_locked(self, after: int, conn=None) -> int:
        from core import server_api as api
        from core import sync_revision as rev
        from core.server_live import apply_server_doc

        own = False
        poll_conn = conn
        if poll_conn is None:
            poll_conn, own = self._open_poll_conn()
        applied = 0
        changed: Set[str] = set()
        cursor = max(0, int(after or 0))
        token = None
        try:
            ensure_sync_inbox(poll_conn)
            token = self._token()
            while not self._stop.is_set():
                page = api.get_sync_changes_full(
                    token, after=cursor, limit=_PULL_PAGE, timeout=120.0
                )
                changes = page.get("changes") or []
                if not changes:
                    break
                for ch in changes:
                    if not isinstance(ch, dict):
                        continue
                    try:
                        revision = int(ch.get("revision") or 0)
                    except (TypeError, ValueError):
                        continue
                    if revision <= 0:
                        continue
                    if self._inbox_has(poll_conn, revision):
                        cursor = max(cursor, revision)
                        rev.bump_head_revision(revision)
                        continue

                    collection = str(ch.get("collection") or "").strip()
                    local_id = ch.get("local_id")
                    operation = str(ch.get("operation") or "upsert").lower()
                    doc = ch.get("doc")

                    status = self._apply_change(
                        poll_conn,
                        collection=collection,
                        local_id=local_id,
                        operation=operation,
                        doc=doc,
                        entity_version=ch.get("entity_version"),
                        entity_updated_at=ch.get("entity_updated_at"),
                        apply_fn=apply_server_doc,
                    )
                    self._inbox_mark(
                        poll_conn,
                        revision=revision,
                        collection=collection,
                        local_id=local_id,
                        operation=operation,
                    )
                    cursor = max(cursor, revision)
                    rev.bump_head_revision(revision)
                    if status in ("applied", "soft_deleted"):
                        applied += 1
                        if collection:
                            changed.add(collection)
                try:
                    poll_conn.commit()
                except Exception:
                    pass
                if not page.get("has_more"):
                    break
                # Advance cursor from page.to_revision when present
                try:
                    to_rev = int(page.get("to_revision") or cursor)
                    cursor = max(cursor, to_rev)
                except (TypeError, ValueError):
                    pass
        finally:
            if own and poll_conn is not None:
                try:
                    poll_conn.commit()
                except Exception:
                    pass
                try:
                    poll_conn.close()
                except Exception:
                    pass

        if changed:
            log.info("[SYNC][UI] pull applied collections=%s", sorted(changed))
            self._notify_changed(changed)
            try:
                from core.sync_status import note_last_sync

                note_last_sync("revision_pull")
            except Exception:
                pass
        # B4.3 — ack applied head so admin can show device lag
        try:
            head = rev.get_head_revision()
            if head > 0 and token:
                api.ack_sync_revision(
                    token, head, device_id=self._our_device_id
                )
        except Exception as exc:
            log.debug("sync_engine ack: %s", exc)
        return applied

    def _apply_change(
        self,
        conn,
        *,
        collection: str,
        local_id,
        operation: str,
        doc,
        entity_version,
        entity_updated_at,
        apply_fn,
    ) -> str:
        if not collection:
            return "ignored"

        if collection == "settings" and isinstance(doc, dict) and isinstance(doc.get("settings"), list):
            # Server packs all KV under one revision; apply each row
            last = "skipped"
            for row in doc["settings"]:
                if isinstance(row, dict) and row.get("name"):
                    last = apply_fn(conn, "settings", row) or last
            return last

        if operation == "delete":
            data = dict(doc) if isinstance(doc, dict) else {}
            data.setdefault("id", local_id)
            data["deleted"] = True
            # Hard-delete changelog often has no entity_version; bump so we never
            # KEEP_LOCAL + re-push (that resurrected bills across devices).
            try:
                ev = int(entity_version) if entity_version is not None else 0
            except (TypeError, ValueError):
                ev = 0
            data["version"] = max(ev + 1_000_000, int(data.get("version") or 0) or 0)
            if entity_updated_at is not None:
                data.setdefault("updated_at", entity_updated_at)
            return apply_fn(conn, collection, data) or "soft_deleted"

        if not isinstance(doc, dict) or not doc:
            return "ignored"
        data = dict(doc)
        if local_id is not None and data.get("id") is None:
            data["id"] = local_id
        return apply_fn(conn, collection, data) or "skipped"

    @staticmethod
    def _inbox_has(conn, revision: int) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sync_inbox WHERE revision=? LIMIT 1", (int(revision),)
        ).fetchone()
        return bool(row)

    @staticmethod
    def _inbox_mark(conn, *, revision: int, collection: str, local_id, operation: str) -> None:
        from datetime import datetime, timezone

        lid = None
        try:
            if local_id is not None:
                lid = int(local_id)
        except (TypeError, ValueError):
            lid = None
        conn.execute(
            """
            INSERT OR IGNORE INTO sync_inbox
            (revision, collection, local_id, operation, applied_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(revision),
                collection or None,
                lid,
                operation or None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def _notify_changed(self, collections: Set[str]) -> None:
        # Prefer sync_status so desktop WebView poller also sees it
        try:
            from core.sync_status import note_collection_change

            for col in collections:
                note_collection_change(col)
        except Exception:
            pass
        cb = self._on_change
        if not cb:
            return
        # Sales first for snappy history refresh
        order = [
            "sales",
            "medicines",
            "customers",
            "purchases",
            "customer_payments",
            "supplier_payments",
            "sales_returns",
            "purchase_returns",
        ]
        seen = set()
        for col in order:
            if col in collections:
                seen.add(col)
                try:
                    cb(col)
                except Exception:
                    pass
        for col in sorted(collections - seen):
            try:
                cb(col)
            except Exception:
                pass


def get_sync_engine() -> SyncEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = SyncEngine()
        return _engine


def start_sync_engine(
    conn,
    db_path: Optional[str] = None,
    on_change: OnChangeCb = None,
    *,
    adopt_server_head: bool = False,
    hints_only: bool = False,
) -> bool:
    return get_sync_engine().start(
        conn,
        db_path=db_path,
        on_change=on_change,
        adopt_server_head=adopt_server_head,
        hints_only=hints_only,
    )


def stop_sync_engine() -> None:
    with _engine_lock:
        eng = _engine
    if eng:
        eng.stop()


def is_sync_engine_alive() -> bool:
    with _engine_lock:
        eng = _engine
    return bool(eng and eng.started and eng.is_alive())
