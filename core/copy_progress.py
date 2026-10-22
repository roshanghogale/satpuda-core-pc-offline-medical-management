"""How far a store copy (Online -> Offline / offline-first) has got, from real totals.

The switch screens used to guess: a bar that moved one step per list (13 lists, so the
medicines -- most of a big shop -- were one step like the doctors), and no idea of the time
left. Before the copy starts the server is asked how many records each list holds (the
same list endpoints the Online screens use), and every record written moves the bar:

    Copying medicines: 12,400 of 61,561 (34% done, about 1 min left)

The latest figures are kept here too (``current()``), so the progress endpoints can hand the
screens a number instead of a sentence to pick apart.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

ProgressCb = Optional[Callable[[str], None]]

# Plain names for the lists, as the shop calls them.
LABELS = {
    "sales": "bills",
    "medicines": "medicines",
    "customers": "customers",
    "purchases": "purchases",
    "suppliers": "suppliers",
    "doctors": "doctors",
    "customer_payments": "customer payments",
    "supplier_payments": "supplier payments",
    "sales_returns": "sale returns",
    "purchase_returns": "purchase returns",
    "racks": "shelves",
    "sections": "shelves",
    "boxes": "shelves",
}

# Rough cost of one record next to one medicine (measured 22 Oct 2026 on a 61,561-medicine
# shop: a bill with its lines ~6x a medicine, a purchase ~10x). Only shares out the bar.
WEIGHTS = {"sales": 6.0, "purchases": 10.0, "sales_returns": 4.0, "purchase_returns": 4.0}

# Where the store-query list endpoints answer `total` for the whole store.
_COUNT_PATHS = {
    "sales": "/api/store/sales?limit=1&from=2000-01-01&to=2100-12-31",
    "purchases": "/api/store/purchases?limit=1&from=2000-01-01&to=2100-12-31",
    "customers": "/api/store/customers?limit=1",
    "suppliers": "/api/store/suppliers?limit=1",
    "customer_payments": "/api/store/payments/customers?limit=1&from=2000-01-01&to=2100-12-31",
    "supplier_payments": "/api/store/payments/suppliers?limit=1&from=2000-01-01&to=2100-12-31",
    "sales_returns": "/api/store/returns/sales?limit=1&from=2000-01-01&to=2100-12-31",
    "purchase_returns": "/api/store/returns/purchases?limit=1&from=2000-01-01&to=2100-12-31",
}

_lock = threading.Lock()
_current: Optional[dict] = None


def current() -> Optional[dict]:
    """The newest figures of the copy running now (or the one that just ended)."""
    with _lock:
        return dict(_current) if _current else None


def clear() -> None:
    global _current
    with _lock:
        _current = None


def _publish(snap: dict) -> None:
    global _current
    with _lock:
        _current = dict(snap)


def fetch_totals(token: str, *, timeout: float = 15.0) -> dict[str, int]:
    """{list: records on the server}, asked all at once; a list that cannot be counted is
    left out (the bar then counts it as it arrives)."""
    from core import server_api as api

    def one(item):
        col, path = item
        try:
            res = api._request("GET", path, token=token, timeout=timeout)
            data = res.get("data") if isinstance(res, dict) else None
            if col == "medicines":
                n = (data or {}).get("all")
            else:
                n = (data or {}).get("total")
            return col, (int(n) if n is not None else None)
        except Exception:
            return col, None

    items = list(_COUNT_PATHS.items()) + [("medicines", "/api/store/inventory/counts")]
    out: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for col, n in pool.map(one, items):
            if n is not None and n >= 0:
                out[col] = n
    return out


def _fmt_left(secs: float) -> str:
    if secs < 45:
        return "less than a minute left"
    mins = int(round(secs / 60.0))
    return "about 1 min left" if mins <= 1 else f"about {mins} min left"


class CopyProgress:
    """Counts the records written against the totals and reports it.

    ``tail_share`` keeps the end of the bar for work after the copy (offline-first's
    registration), so the copy itself never shows 100%."""

    def __init__(self, cb: ProgressCb = None, totals: Optional[dict] = None, *, tail_share: float = 0.04,
                 min_interval: float = 0.5):
        self.cb = cb
        self.totals: dict[str, int] = dict(totals or {})
        self.done: dict[str, int] = {}
        self.tail_share = max(0.0, min(0.3, float(tail_share)))
        self.min_interval = min_interval
        self.col = ""
        self.stage_label = ""
        self.stage_pct: Optional[float] = None
        self.started = time.monotonic()
        self.first_write: Optional[float] = None
        self._last_emit = 0.0
        self._pct = 0.0
        self._publish()

    # -- figures
    def _weight(self, col: str) -> float:
        return WEIGHTS.get(col, 1.0)

    def _units(self) -> tuple[float, float]:
        cols = set(self.totals) | set(self.done)
        total = sum(self._weight(c) * max(self.totals.get(c, 0), self.done.get(c, 0)) for c in cols)
        done = sum(self._weight(c) * self.done.get(c, 0) for c in cols)
        return done, total

    def fraction(self) -> float:
        done, total = self._units()
        return (done / total) if total > 0 else 0.0

    def percent(self) -> int:
        if self.stage_pct is not None:
            p = self.stage_pct
        else:
            # 2% for making the empty store, the records up to 90% of the copy's part of
            # the bar, then the finishing steps (stage()).
            p = 2.0 + (90.0 * (1.0 - self.tail_share) - 2.0) * self.fraction()
        self._pct = max(self._pct, min(100.0, p))
        return int(self._pct)

    def seconds_left(self) -> Optional[float]:
        if self.first_write is None:
            return None
        spent = time.monotonic() - self.first_write
        done, total = self._units()
        if spent < 3.0 or done <= 0 or total <= 0 or done / total < 0.02:
            return None
        rate = done / spent
        return (total - done) / rate + 5.0  # + the finishing steps

    def snapshot(self) -> dict:
        col = self.col
        left = self.seconds_left() if self.stage_pct is None else None
        return {
            "pct": self.percent(),
            "label": self.label(),
            "collection": col,
            "done": int(self.done.get(col, 0)) if col else 0,
            "total": int(max(self.totals.get(col, 0), self.done.get(col, 0))) if col else 0,
            "eta_s": int(left) if left is not None else None,
            "elapsed_s": int(time.monotonic() - self.started),
        }

    def label(self) -> str:
        if self.stage_pct is not None or not self.col:
            return self.stage_label
        name = LABELS.get(self.col, self.col.replace("_", " "))
        n = self.done.get(self.col, 0)
        tot = max(self.totals.get(self.col, 0), n)
        return f"Copying {name}: {n:,} of {tot:,}" if tot else f"Copying {name}"

    def message(self) -> str:
        snap = self.snapshot()
        extra = [f"{snap['pct']}% done"]
        if snap["eta_s"] is not None:
            extra.append(_fmt_left(snap["eta_s"]))
        return f"{snap['label']} ({', '.join(extra)})"

    # -- events
    def begin(self, col: str) -> None:
        self.col = col
        self.stage_pct = None
        self.done.setdefault(col, 0)
        self._emit(force=True)

    def advance(self, col: str, n: int) -> None:
        if n <= 0:
            return
        if self.first_write is None:
            self.first_write = time.monotonic()
        self.col = col
        self.done[col] = self.done.get(col, 0) + int(n)
        self._emit()

    def stage(self, label: str, pct: Optional[float] = None) -> None:
        """A step that is not a list of records ("Fixing stock figures…")."""
        self.stage_label = label
        self.stage_pct = pct
        self.col = ""
        self._emit(force=True)

    def _publish(self) -> None:
        _publish(self.snapshot())

    def _emit(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < self.min_interval:
            return
        self._last_emit = now
        self._publish()
        if self.cb:
            try:
                self.cb(self.message() if self.stage_pct is None else self.stage_label)
            except Exception:
                pass
