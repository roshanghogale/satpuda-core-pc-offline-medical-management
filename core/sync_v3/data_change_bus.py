"""Tk DataChangeBus — explicit pub/sub for reactive UI (no native binding)."""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Callable, Optional, Set

log = logging.getLogger(__name__)

Callback = Callable[[str], None]


class DataChangeBus:
    """
    Process-wide change bus.

    Screens call subscribe(collections, callback) on open and unsubscribe on destroy.
    SyncPipeline / SyncApplier call emit(collection) after local writes and remote apply.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, tuple[Set[str], Callback]] = {}
        self._tk_root = None

    def set_tk_root(self, root) -> None:
        """Bind Tk root so callbacks are marshalled onto the UI thread."""
        self._tk_root = root

    def subscribe(self, collections: Set[str] | list[str] | tuple[str, ...], callback: Callback) -> str:
        token = uuid.uuid4().hex
        cols = {str(c).strip() for c in collections if str(c).strip()}
        if not cols or not callable(callback):
            return token
        with self._lock:
            self._subs[token] = (cols, callback)
        return token

    def unsubscribe(self, token: str) -> None:
        with self._lock:
            self._subs.pop(token or "", None)

    def emit(self, collection: str, *, local: bool = True) -> None:
        col = str(collection or "").strip()
        if not col:
            return
        # Keep React desktop poller / sync_status in sync with the bus.
        try:
            from core.sync_status import note_collection_change

            note_collection_change(col)
        except Exception:
            pass
        with self._lock:
            targets = [
                (tok, cb)
                for tok, (cols, cb) in list(self._subs.items())
                if col in cols or "*" in cols
            ]
        for tok, cb in targets:
            self._dispatch(tok, cb, col)

    def _dispatch(self, token: str, cb: Callback, collection: str) -> None:
        root = self._tk_root

        def _run():
            try:
                cb(collection)
            except Exception as exc:
                log.debug("DataChangeBus callback failed (%s): %s", token[:8], exc)
                # Drop dead subscriptions (destroyed widgets)
                msg = str(exc).lower()
                if "application has been destroyed" in msg or "invalid command name" in msg:
                    self.unsubscribe(token)

        if root is not None:
            try:
                root.after(0, _run)
                return
            except Exception:
                pass
        _run()


_bus: Optional[DataChangeBus] = None
_bus_lock = threading.Lock()


def get_bus() -> DataChangeBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = DataChangeBus()
        return _bus


def emit(collection: str, *, local: bool = True) -> None:
    get_bus().emit(collection, local=local)


def subscribe(collections, callback: Callback) -> str:
    return get_bus().subscribe(collections, callback)


def unsubscribe(token: str) -> None:
    get_bus().unsubscribe(token)
