"""Change hints for the PCs on the LAN (Main PC side).

Every change on the Main PC -- a save from its own screen, a save a counter made through it, a
bill a counter handed over, a change pulled from the cloud -- is written here as (number,
collection, origin). Each counter asks for "everything after n" every second and its engine
passes the collections to its own screen (core.sync_status.note_collection_change), which
refreshes the pages that show them. A counter is not told about its own saves (origin).

Kept in memory only: a counter that has fallen too far behind (or the Main PC restarted) is
told to refresh everything that holds shop data.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Iterable

KEEP = 2000
# What a counter refreshes when it cannot know exactly what changed.
BROAD = (
    "medicines", "sales", "purchases", "customers", "suppliers", "customer_payments",
    "supplier_payments", "sales_returns", "purchase_returns", "stock_disposals",
)

_lock = threading.Lock()
_log: deque = deque(maxlen=KEEP)
_seq = 0
# A number per engine start: a counter holding a number from before a restart starts over.
import secrets as _secrets  # noqa: E402

EPOCH = _secrets.token_hex(4)


def note(collections: Iterable[str], origin: str = "") -> int:
    global _seq
    cols = sorted({str(c or "").strip() for c in collections} - {""})
    if not cols:
        return _seq
    with _lock:
        for col in cols:
            _seq += 1
            _log.append((_seq, col, str(origin or "")))
        return _seq


def head() -> int:
    with _lock:
        return _seq


def since(after: int, exclude_origin: str = "", epoch: str = "") -> dict:
    """{"epoch", "seq", "collections"}: what changed after ``after`` (not by exclude_origin)."""
    with _lock:
        top = _seq
        oldest = _log[0][0] if _log else top + 1
        if epoch and epoch != EPOCH:
            # The Main PC restarted since this counter last asked.
            return {"epoch": EPOCH, "seq": top, "collections": list(BROAD), "reset": True}
        try:
            after = int(after)
        except (TypeError, ValueError):
            after = -1
        if after < 0:
            return {"epoch": EPOCH, "seq": top, "collections": []}
        if after > top:
            return {"epoch": EPOCH, "seq": top, "collections": list(BROAD), "reset": True}
        if after < oldest - 1:
            return {"epoch": EPOCH, "seq": top, "collections": list(BROAD), "reset": True}
        cols = sorted({c for s, c, o in _log if s > after and (not exclude_origin or o != exclude_origin)})
        return {"epoch": EPOCH, "seq": top, "collections": cols}


# Which collections a write through each part of the API touches. A write whose path is not
# listed refreshes the broad set; reads and form arithmetic note nothing.
_PATH_COLLECTIONS = (
    ("/api/wholesale/sale/", ("sales", "medicines", "customers", "customer_payments")),
    ("/api/sales/", ("sales", "medicines", "customers", "customer_payments")),
    ("/api/purchase/", ("purchases", "medicines", "suppliers", "supplier_payments")),
    ("/api/returns/", ("sales_returns", "purchase_returns", "stock_disposals", "medicines", "sales",
                       "purchases", "customers", "customer_payments", "supplier_payments")),
    ("/api/inventory/", ("medicines",)),
    ("/api/general-products", ("medicines",)),
    ("/api/history/pay-bill", ("customer_payments", "supplier_payments", "sales", "purchases",
                               "customers", "suppliers")),
    ("/api/settings/payments", ("customer_payments", "supplier_payments", "sales", "purchases",
                                "customers", "suppliers")),
    ("/api/settings/contacts", ("customers", "suppliers")),
    ("/api/customers/", ("customers",)),
    ("/api/wholesale/", ("sales", "purchases", "customers", "suppliers", "medicines")),
)

# Writes that are not a change worth a refresh on another PC: arithmetic for the open form,
# lookups, previews, drafts in progress (the final save is noted), and this PC's own things.
_QUIET_PARTS = (
    "/calc", "/build-line", "/quick-line", "/preview", "/lookup", "/search", "/autosave",
    "/candidates", "/rate-check", "/merge-lines", "/import/progress", "/import/pick",
    "/import/preview", "/import/cancel", "/prefs", "/pdf", "/export", "/options",
    "/details", "/open-bills", "/report",
)
_QUIET_PREFIXES = (
    "/api/files/", "/api/lan/", "/api/sync/", "/api/offline-first/", "/api/license/",
    "/api/login/", "/api/voice/", "/api/backup/", "/api/settings/appearance",
    "/api/settings/save/appearance", "/api/settings/layout_lists", "/api/settings/save/layout_lists",
    "/api/settings/system", "/api/settings/save/system", "/api/settings/printer",
    "/api/settings/sales_billing", "/api/settings/save/sales_billing", "/api/bill/",
    "/api/startup/", "/api/online/",
)


def collections_for_write(path: str) -> tuple[str, ...]:
    p = str(path or "")
    if not p.startswith("/api/"):
        return ()
    if any(p.startswith(q) for q in _QUIET_PREFIXES):
        return ()
    if any(part in p for part in _QUIET_PARTS):
        return ()
    for prefix, cols in _PATH_COLLECTIONS:
        if p.startswith(prefix):
            return cols
    return BROAD
