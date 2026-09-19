"""Store-auth query client for Online-mode history / inventory (server-first UI)."""
from __future__ import annotations

import logging
from typing import Any, Optional
import time
from urllib.parse import urlencode

from core import server_api as api

log = logging.getLogger(__name__)


def _token(force_pair: bool = False) -> str:
    tok = (api.store_token_for_active(force_pair=force_pair) or "").strip()
    if not tok:
        raise RuntimeError("Not signed in to Satpuda Core Server")
    return tok


# A healthy link answers these in well under a second. Waiting a full minute on
# one that has stalled just holds the page: a shop laptop with two live network
# adapters flaps between them, and Inventory or Sales History then sat for 60 to
# 90 seconds looking frozen. Give up quickly and try again -- a reconnect is
# usually instant, and two short attempts beat one long wait.
_READ_TIMEOUT = 15
_READ_ATTEMPTS = 3


def _get(path: str, params: Optional[dict] = None) -> Any:
    qs = ""
    if params:
        clean = {k: v for k, v in params.items() if v is not None and str(v).strip() != ""}
        if clean:
            qs = "?" + urlencode(clean)
    url = f"{path}{qs}"
    last: Exception | None = None
    for attempt in range(_READ_ATTEMPTS):
        try:
            res = api._request("GET", url, token=_token(), timeout=_READ_TIMEOUT)
            break
        except api.ServerHttpError as exc:
            # ensure_store_session no longer probes /auth/license before every
            # call, so a token revoked or rotated server-side first shows up
            # here. Re-pair once and retry; anything else is a real error.
            if exc.status not in (401, 403):
                raise
            api.invalidate_session_probe()
            res = api._request(
                "GET", url, token=_token(force_pair=True), timeout=_READ_TIMEOUT
            )
            break
        except (OSError, TimeoutError) as exc:
            # The link dropped mid-request. Worth another go on a flapping
            # connection; pointless to keep waiting on the same dead socket.
            last = exc
            if attempt == _READ_ATTEMPTS - 1:
                raise
            time.sleep(0.4 * (attempt + 1))
    else:  # pragma: no cover - the loop always breaks or raises
        raise last or RuntimeError("Store query failed")
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Store query failed")
    return res.get("data") or {}


def list_sales(
    *,
    from_date: str = "",
    to_date: str = "",
    q: str = "",
    schedule: str = "",
    medicine: str = "",
    batch: str = "",
    limit: int = 5000,
    include_total: bool = True,
    offset: int = 0,
) -> dict:
    return _get(
        "/api/store/sales",
        {
            "from": from_date or None,
            "to": to_date or None,
            "q": q or None,
            "schedule": schedule or None,
            "medicine": medicine or None,
            "batch": batch or None,
            "limit": limit,
            "include_total": None if include_total else "0",
            # The server pages with OFFSET (adminService.listSales). Sent only for a later
            # page, so every existing call asks exactly what it asked before.
            "offset": int(offset) if int(offset or 0) > 0 else None,
        },
    )


def list_purchases(
    *,
    from_date: str = "",
    to_date: str = "",
    q: str = "",
    schedule: str = "",
    medicine: str = "",
    batch: str = "",
    limit: int = 5000,
    include_total: bool = True,
) -> dict:
    return _get(
        "/api/store/purchases",
        {
            "from": from_date or None,
            "to": to_date or None,
            "q": q or None,
            "schedule": schedule or None,
            "medicine": medicine or None,
            "batch": batch or None,
            "limit": limit,
            "include_total": None if include_total else "0",
        },
    )


def list_inventory(
    *,
    q: str = "",
    limit: int = 1000,
    offset: int = 0,
    include_total: bool = True,
    hidden: str = "0",
    type_filter: str = "",
    schedule: str = "",
) -> dict:
    """Inventory page. type_filter and schedule are applied by the SERVER.

    They used to be applied in Python over whatever single page came back, so
    on a shop with more batches than the page holds, "Type: Syrup" searched
    only the first slice of the catalogue alphabetically and quietly reported
    the rest as not existing. The Offline branch has always put these two in
    its SQL WHERE; this is the same filter on the same data, in the mode the
    shops actually run in.

    Stock status, expiry status and low-stock stay client-side on purpose: they
    depend on the shop's own per-type minimums and near-expiry months, and the
    server's WHERE knows only a hardcoded 10 units / 90 days. Delegating those
    would silently overrule the thresholds the shop set.
    """
    return _get(
        "/api/store/inventory",
        {
            "q": q or None,
            "limit": limit,
            "offset": offset,
            "hidden": hidden if hidden is not None else "0",
            "include_total": None if include_total else "0",
            "type": (type_filter or "").strip() or None,
            "schedule": (schedule or "").strip() or None,
        },
    )


def list_customers(*, q: str = "", limit: int = 1000, offset: int = 0, include_total: bool = True) -> dict:
    return _get(
        "/api/store/customers",
        {
            "q": q or None,
            "limit": limit,
            "offset": offset,
            "include_total": None if include_total else "0",
        },
    )


def list_doctors(*, q: str = "", limit: int = 1000, offset: int = 0, include_total: bool = True) -> dict:
    return _get(
        "/api/store/doctors",
        {
            "q": q or None,
            "limit": limit,
            "offset": offset,
            "include_total": None if include_total else "0",
        },
    )


def list_suppliers(*, q: str = "", limit: int = 1000, offset: int = 0, include_total: bool = True) -> dict:
    return _get(
        "/api/store/suppliers",
        {
            "q": q or None,
            "limit": limit,
            "offset": offset,
            "include_total": None if include_total else "0",
        },
    )


def list_customer_payments(*, limit: int = 5000, from_date: str = "", to_date: str = "") -> dict:
    return _get(
        "/api/store/payments/customers",
        {
            "limit": limit,
            "from": from_date or None,
            "to": to_date or None,
        },
    )


def list_supplier_payments(*, limit: int = 5000, from_date: str = "", to_date: str = "") -> dict:
    return _get(
        "/api/store/payments/suppliers",
        {
            "limit": limit,
            "from": from_date or None,
            "to": to_date or None,
        },
    )


def get_sale(local_id: int) -> dict:
    return _get(f"/api/store/sales/{int(local_id)}")


def get_purchase(local_id: int) -> dict:
    return _get(f"/api/store/purchases/{int(local_id)}")


def list_sales_returns(*, limit: int = 2000, from_date: str = "", to_date: str = "") -> dict:
    return _get(
        "/api/store/returns/sales",
        {
            "limit": limit,
            "from": from_date or None,
            "to": to_date or None,
        },
    )


def list_purchase_returns(*, limit: int = 2000, from_date: str = "", to_date: str = "") -> dict:
    return _get(
        "/api/store/returns/purchases",
        {
            "limit": limit,
            "from": from_date or None,
            "to": to_date or None,
        },
    )


def sales_summary(
    *,
    from_date: str = "",
    to_date: str = "",
    q: str = "",
    ids: Optional[list] = None,
) -> dict:
    params: dict[str, Any] = {
        "from": from_date or None,
        "to": to_date or None,
        "q": q or None,
    }
    if ids is not None:
        params["scoped"] = "1"
        params["ids"] = ",".join(str(int(i)) for i in ids if i is not None) if ids else ""
    return _get("/api/store/summaries/sales", params)


def purchases_summary(
    *,
    from_date: str = "",
    to_date: str = "",
    q: str = "",
    ids: Optional[list] = None,
) -> dict:
    params: dict[str, Any] = {
        "from": from_date or None,
        "to": to_date or None,
        "q": q or None,
    }
    if ids is not None:
        params["scoped"] = "1"
        params["ids"] = ",".join(str(int(i)) for i in ids if i is not None) if ids else ""
    return _get("/api/store/summaries/purchases", params)


def inventory_summary() -> dict:
    return _get("/api/store/summaries/inventory")


def home_summary() -> dict:
    return _get("/api/store/summaries/home")


def fetch_parallel(*callables):
    """Run independent store GETs on separate TLS connections."""
    from concurrent.futures import ThreadPoolExecutor

    if not callables:
        return []
    if len(callables) == 1:
        return [callables[0]()]
    with ThreadPoolExecutor(max_workers=len(callables)) as pool:
        futs = [pool.submit(fn) for fn in callables]
        return [fut.result() for fut in futs]
