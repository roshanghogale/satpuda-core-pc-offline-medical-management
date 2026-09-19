"""UI bus for store-scoped live refresh hints (Online server-first screens)."""
from __future__ import annotations

import threading
from typing import Callable, Iterable, Optional

_lock = threading.Lock()
_subscribers: list[tuple[frozenset[str], Callable[[dict], None]]] = []


def subscribe(collections: Iterable[str], callback: Callable[[dict], None]) -> object:
    token = (frozenset(str(c).strip().lower() for c in collections if str(c).strip()), callback)
    with _lock:
        _subscribers.append(token)
    return token


def unsubscribe(token: object) -> None:
    with _lock:
        try:
            _subscribers.remove(token)  # type: ignore[arg-type]
        except ValueError:
            pass


def emit(event: dict) -> None:
    """event: {head_revision, source_device_id, changes:[{collection,local_id,operation}], full_refresh}

    Only invalidate / notify when something actually changed. Empty periodic polls
    must not wipe caches or reload screens.
    """
    changes = event.get("changes") or []
    full = bool(event.get("full_refresh"))
    touched = {
        str(c.get("collection") or "").strip().lower()
        for c in changes
        if isinstance(c, dict) and str(c.get("collection") or "").strip()
    }
    if not full and not touched:
        return

    try:
        from core.online_catalog import invalidate

        if full:
            invalidate()
        else:
            cache_keys = {
                "medicines": "medicines",
                "stock_operations": "medicines",
                "customers": "customers",
                "doctors": "doctors",
                "suppliers": "suppliers",
                "sales": "sales",
                "purchases": "purchases",
                "supplier_payments": "suppliers",
                "purchase_returns": "purchases",
                "customer_payments": "customers",
                "sales_returns": "sales",
            }
            for col in touched:
                key = cache_keys.get(col)
                if key:
                    invalidate(key)
    except Exception:
        pass

    if not full:
        # What a back-dated bill may use (core.sale_availability) changes only when a
        # purchase changes -- the "purchases" invalidation above forgets it -- or a
        # medicine is added. "medicines" also arrives for every stock movement, so only
        # an id that answer has never seen counts, and "stock_operations" never does.
        try:
            from core.sale_availability import medicines_changed

            medicines_changed(
                c.get("local_id")
                for c in changes
                if isinstance(c, dict)
                and str(c.get("collection") or "").strip().lower() == "medicines"
            )
        except Exception:
            pass

    with _lock:
        subs = list(_subscribers)
    for cols, cb in subs:
        if full or (cols & touched):
            try:
                cb(event)
            except Exception:
                pass
    try:
        tid = event.get("timing_id")
        if tid:
            from core import sync_timing
            sync_timing.mark(tid, "subscribers_notified", n=len(subs), touched=",".join(sorted(touched))[:120])
    except Exception:
        pass
