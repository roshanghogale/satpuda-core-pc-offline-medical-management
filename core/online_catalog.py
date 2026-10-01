"""Online-mode catalog helpers — store parties + inventory (Offline Mac2 parity).

All lists are scoped to the signed-in store via /api/store/* (JWT), matching the
same rows Offline reads from local SQLite: customers, doctors, suppliers, medicines.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()
_load_locks: dict[str, threading.Lock] = {}
_cache: dict[str, tuple[float, list[dict]]] = {}
_TTL_SEC = 120.0
_BILL_NAMES_TTL_SEC = 300.0
_PAGE = 2500
_customer_index: dict[str, dict] = {}
_customer_by_id: dict[int, dict] = {}
_medicine_by_id: dict[int, dict] = {}
_medicine_by_name: dict[str, list[dict]] = {}
_medicine_name_summaries: list[dict] = []
_supplier_by_id: dict[int, dict] = {}
_supplier_by_name: dict[str, dict] = {}
_doctor_by_id: dict[int, dict] = {}
_doctor_by_name: dict[str, dict] = {}
_bill_customer_names: tuple[float, set[str]] | None = None
_SNAP_KEYS = ("customers", "suppliers", "doctors", "medicines_inventory")
# Why a failed read has to be remembered: every function in this module
# answers a failure with an empty list, because raising would take out bill
# save and printing. That is the right call -- but it makes "the server did
# not answer" and "this shop has no customers" the same thing to all twenty-
# odd callers. This dict is the difference. Guarded by _lock.
_last_error: dict[str, str] = {}


def clear_errors() -> None:
    """Forget why the last reads failed - the server is answering again.

    The shell's warning bar shows the last catalog failure until a read of that
    list succeeds. Lists are cached, so after a blip nothing re-read them for
    minutes and a shop kept looking at "Cannot reach server" long after the
    connection was back - which is how a bill that was already saved gets
    entered a second time. The connectivity monitor calls this on reconnect.
    """
    with _lock:
        _last_error.clear()


def last_error(key: str = "") -> str:
    """Why the last catalog read for `key` came back empty, or "" if it did not fail.

    With no key: the first failure still outstanding, for a status bar.
    """
    with _lock:
        if key:
            return _last_error.get(key, "")
        for k in _SNAP_KEYS:
            msg = _last_error.get(k)
            if msg:
                return msg
        for msg in _last_error.values():
            if msg:
                return msg
        return ""


def _clear_medicine_indexes() -> None:
    global _medicine_by_id, _medicine_by_name, _medicine_name_summaries
    _medicine_by_id = {}
    _medicine_by_name = {}
    _medicine_name_summaries = []


def _clear_customer_indexes() -> None:
    global _customer_index, _customer_by_id
    _customer_index = {}
    _customer_by_id = {}


def _clear_supplier_indexes() -> None:
    global _supplier_by_id, _supplier_by_name
    _supplier_by_id = {}
    _supplier_by_name = {}


def _clear_doctor_indexes() -> None:
    global _doctor_by_id, _doctor_by_name
    _doctor_by_id = {}
    _doctor_by_name = {}


def invalidate(collection: str | None = None) -> None:
    global _bill_customer_names
    # A refresh of the catalogue is also the moment to stop remembering which
    # ids the server answered 404 for: the row may have arrived since.
    try:
        from core.server_crud import forget_missing

        forget_missing(collection)
    except Exception:
        pass
    if collection in (None, "purchases"):
        # Which batches a back-dated bill may use changes when a purchase does. Not on
        # "medicines": that is every stock movement -- each sale on every device -- and
        # clearing on it had nearly every added line pull the store again. A medicine
        # that is new is caught by sale_availability.medicines_changed instead.
        try:
            from core.sale_availability import invalidate as _forget_availability

            _forget_availability()
        except Exception:
            pass
    with _lock:
        if collection:
            _cache.pop(collection, None)
            if collection == "medicines":
                _cache.pop("medicines_inventory", None)
                _clear_medicine_indexes()
            if collection == "customers":
                _clear_customer_indexes()
            if collection == "suppliers":
                _clear_supplier_indexes()
            if collection == "doctors":
                _clear_doctor_indexes()
            if collection in ("sales", "customers"):
                if collection == "sales":
                    _bill_customer_names = None
        else:
            _cache.clear()
            _clear_customer_indexes()
            _clear_medicine_indexes()
            _clear_supplier_indexes()
            _clear_doctor_indexes()
            _bill_customer_names = None


def patch_customer_cache(doc: dict) -> None:
    """Merge an upserted customer into the in-memory cache so phone/address stick immediately."""
    if not isinstance(doc, dict):
        return
    name = str(doc.get("name") or "").strip()
    if not name:
        return
    try:
        cid = int(doc.get("id") or doc.get("local_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    now = time.time()
    with _lock:
        hit = _cache.get("customers")
        rows = list(hit[1]) if hit else []
        updated = False
        for i, row in enumerate(rows):
            try:
                rid = int(row.get("id") or row.get("local_id") or 0)
            except (TypeError, ValueError):
                rid = 0
            same = (cid and rid == cid) or (
                str(row.get("name") or "").strip().upper() == name.upper()
            )
            if same:
                merged = dict(row)
                merged.update(doc)
                if cid:
                    merged["id"] = cid
                    merged["local_id"] = cid
                rows[i] = merged
                updated = True
                break
        if not updated:
            rows.append(dict(doc))
        _cache["customers"] = (now, rows)
        _rebuild_customer_index(rows)
    try:
        _persist_snapshot()
    except Exception:
        pass


def patch_supplier_cache(doc: dict) -> None:
    """Merge an upserted supplier into the in-memory cache so contact fields stick."""
    if not isinstance(doc, dict):
        return
    name = str(doc.get("name") or "").strip()
    if not name:
        return
    try:
        sid = int(doc.get("id") or doc.get("local_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    now = time.time()
    with _lock:
        hit = _cache.get("suppliers")
        rows = list(hit[1]) if hit else []
        updated = False
        for i, row in enumerate(rows):
            try:
                rid = int(row.get("id") or row.get("local_id") or 0)
            except (TypeError, ValueError):
                rid = 0
            same = (sid and rid == sid) or (
                str(row.get("name") or "").strip().upper() == name.upper()
            )
            if same:
                merged = dict(row)
                merged.update(doc)
                if sid:
                    merged["id"] = sid
                    merged["local_id"] = sid
                rows[i] = merged
                updated = True
                break
        if not updated:
            rows.append(dict(doc))
        _cache["suppliers"] = (now, rows)
        _rebuild_supplier_index(rows)


def prefetch_hot() -> None:
    """Warm customers / suppliers / doctors / medicines in a background thread (Online)."""
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return
    except Exception:
        return

    def _run():
        try:
            customers()
            suppliers()
            doctors()
            medicines()
            _persist_snapshot()
        except Exception as exc:
            log.debug("online_catalog prefetch: %s", exc)

    threading.Thread(target=_run, daemon=True, name="OnlineCatalogPrefetch").start()


def _rebuild_customer_index(rows: list[dict]) -> None:
    global _customer_index, _customer_by_id
    idx: dict[str, dict] = {}
    by_id: dict[int, dict] = {}
    for c in rows:
        try:
            cid = int(c.get("id") or c.get("local_id") or 0)
        except (TypeError, ValueError):
            cid = 0
        if cid > 0:
            by_id[cid] = c
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        key = name.upper()
        prev = idx.get(key)
        if prev is None:
            idx[key] = c
            continue
        # Duplicate names (e.g. two CHANDU rows): prefer the one with due/credit/phone.
        try:
            prev_score = (
                float(prev.get("total_due") or 0)
                + float(prev.get("total_credit") or 0)
                + (1.0 if str(prev.get("phone") or "").strip() else 0.0)
            )
            cur_score = (
                float(c.get("total_due") or 0)
                + float(c.get("total_credit") or 0)
                + (1.0 if str(c.get("phone") or "").strip() else 0.0)
            )
        except (TypeError, ValueError):
            prev_score, cur_score = 0.0, 0.0
        if cur_score > prev_score:
            idx[key] = c
        elif cur_score == prev_score:
            # Stable: keep lower local_id (original master row).
            try:
                prev_id = int(prev.get("id") or prev.get("local_id") or 0)
                cur_id = int(c.get("id") or c.get("local_id") or 0)
                if 0 < cur_id < prev_id:
                    idx[key] = c
            except (TypeError, ValueError):
                pass
    _customer_index = idx
    _customer_by_id = by_id


def _rebuild_medicine_index(rows: list[dict]) -> None:
    global _medicine_by_id, _medicine_by_name, _medicine_name_summaries
    by_id: dict[int, dict] = {}
    by_name: dict[str, list[dict]] = {}
    summaries: dict[str, dict] = {}
    for m in rows:
        try:
            mid = int(m.get("id") or m.get("local_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid > 0:
            by_id[mid] = m
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        by_name.setdefault(key, []).append(m)
        if m.get("is_hidden"):
            continue
        stock = float(m.get("stock_qty") or 0)
        mrp = float(m.get("mrp") or 0)
        cur = summaries.get(key)
        if cur is None:
            summaries[key] = {
                "name": name,
                "stock": stock,
                "mrp": mrp,
                "unit": m.get("unit") or "1",
                "type": m.get("type") or "",
                "schedule": str(m.get("schedule") or ""),
                "batch_count": 1,
            }
        else:
            cur["stock"] = float(cur.get("stock") or 0) + stock
            cur["batch_count"] = int(cur.get("batch_count") or 0) + 1
            if mrp > float(cur.get("mrp") or 0):
                cur["mrp"] = mrp
    _medicine_by_id = by_id
    _medicine_by_name = by_name
    _medicine_name_summaries = sorted(
        summaries.values(), key=lambda n: str(n.get("name") or "").upper()
    )


def _rebuild_supplier_index(rows: list[dict]) -> None:
    global _supplier_by_id, _supplier_by_name
    by_id: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    for s in rows:
        try:
            sid = int(s.get("id") or s.get("local_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid > 0:
            by_id[sid] = s
        name = str(s.get("name") or "").strip()
        if name:
            by_name[name.upper()] = s
    _supplier_by_id = by_id
    _supplier_by_name = by_name


def _rebuild_doctor_index(rows: list[dict]) -> None:
    global _doctor_by_id, _doctor_by_name
    by_id: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    for d in rows:
        try:
            did = int(d.get("id") or d.get("local_id") or 0)
        except (TypeError, ValueError):
            did = 0
        if did > 0:
            by_id[did] = d
        name = str(d.get("name") or "").strip()
        if name:
            by_name[name.upper()] = d
    _doctor_by_id = by_id
    _doctor_by_name = by_name


def _apply_list_indexes(key: str, rows: list[dict]) -> None:
    if key == "customers":
        _rebuild_customer_index(rows)
    elif key == "medicines_inventory":
        _rebuild_medicine_index(rows)
    elif key == "suppliers":
        _rebuild_supplier_index(rows)
    elif key == "doctors":
        _rebuild_doctor_index(rows)


def _load_lock_for(key: str) -> threading.Lock:
    with _lock:
        lk = _load_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _load_locks[key] = lk
        return lk


def _snapshot_path() -> str:
    from core.license_manager import _appdata_dir
    from core.store_manager import get_active_store_key

    folder = os.path.join(_appdata_dir(), "online_catalog_snapshot")
    os.makedirs(folder, exist_ok=True)
    key = get_active_store_key() or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return os.path.join(folder, f"{safe}.json")


def _persist_snapshot() -> None:
    try:
        snap = {}
        with _lock:
            for key in _SNAP_KEYS:
                hit = _cache.get(key)
                if hit:
                    snap[key] = hit[1]
        # `not any(...)`, not `not snap`: a dict of EMPTY lists is truthy, so a
        # successful zero-row read (the shape a wrong-store pairing produces)
        # used to be written straight over the good snapshot -- and then the
        # emptiness survived every restart, even after the link was repaired.
        if not snap or not any(snap.values()):
            return
        path = _snapshot_path()
        tmp = path + ".tmp"
        from core.server_api import _json_dumps

        with open(tmp, "wb") as fh:
            fh.write(_json_dumps(snap))
        os.replace(tmp, path)
    except Exception as exc:
        log.debug("catalog snapshot save: %s", exc)


def _load_snapshot_key(key: str) -> list[dict]:
    try:
        path = _snapshot_path()
        if not os.path.isfile(path):
            return []
        from core.server_api import _json_loads

        with open(path, "rb") as fh:
            raw = _json_loads(fh.read() or b"{}")
        rows = (raw or {}).get(key) or []
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    except Exception as exc:
        log.debug("catalog snapshot load %s: %s", key, exc)
    return []


def patch_docs(collection: str, docs: list[dict] | None) -> None:
    """Merge flushed server docs into the in-memory catalog (no full wipe).

    A patch describes the handful of rows a save just touched. It must never be
    allowed to BECOME the catalog, and it used to do exactly that: on a cache
    miss `rows` started empty, the saved bill's medicines were appended, and the
    result was stored under the inventory key with a fresh timestamp -- so the
    medicine picker showed only the medicines from the bill that had just been
    saved. `_apply_list_indexes` then rebuilt the id index from those same few
    rows, discarding every other medicine in the shop.

    Worse, it did not clear on its own. Every save calls this, and every call
    re-stamped the entry's timestamp, so under continuous billing the TTL never
    elapsed and the shortened list stayed until the app was restarted -- which
    is exactly the workaround the counter found.

    So: with no cached list, patch nothing and let the next read load the real
    one. With a cached list, merge into it but KEEP ITS ORIGINAL TIMESTAMP, so a
    stream of saves can no longer hold a stale catalog alive for ever.
    """
    if not docs:
        return
    key = "medicines_inventory" if collection == "medicines" else collection
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return
        cached_at, rows = hit[0], list(hit[1])
        by_id: dict[int, int] = {}
        for i, r in enumerate(rows):
            try:
                rid = int(r.get("id") or r.get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            if rid:
                by_id[rid] = i
        for d in docs:
            if not isinstance(d, dict):
                continue
            try:
                did = int(d.get("id") or d.get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            if did <= 0:
                continue
            if did in by_id:
                merged = dict(rows[by_id[did]])
                merged.update(d)
                rows[by_id[did]] = merged
            else:
                by_id[did] = len(rows)
                rows.append(dict(d))
        _cache[key] = (cached_at, rows)
        _apply_list_indexes(key, rows)


def _cached_list(key: str, loader, *, force: bool = False) -> list[dict]:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if not force and hit and (now - hit[0]) < _TTL_SEC:
            return list(hit[1])
    lk = _load_lock_for(key)
    with lk:
        now = time.time()
        with _lock:
            hit = _cache.get(key)
            if not force and hit and (now - hit[0]) < _TTL_SEC:
                return list(hit[1])
        try:
            rows = loader()
        except Exception as exc:
            log.warning("online_catalog %s: %s", key, exc)
            with _lock:
                _last_error[key] = str(exc).strip() or exc.__class__.__name__
                hit = _cache.get(key)
                if hit:
                    return list(hit[1])
            snap = _load_snapshot_key(key)
            if snap:
                with _lock:
                    _cache[key] = (time.time(), snap)
                    _apply_list_indexes(key, snap)
                return list(snap)
            return []
        with _lock:
            _last_error.pop(key, None)
            _cache[key] = (now, rows)
            _apply_list_indexes(key, rows)
        return list(rows)


def _token() -> str:
    from core import server_api as api

    return api.store_token_for_active()


def _rows_from_store(kind: str, *, limit: int = 10000) -> list[dict]:
    """Paginate store-query list endpoints until exhausted."""
    from core import store_query_client as sq

    fetcher = {
        "customers": sq.list_customers,
        "doctors": sq.list_doctors,
        "suppliers": sq.list_suppliers,
    }.get(kind)
    if fetcher is None:
        raise ValueError(f"unknown store list kind: {kind}")

    out: list[dict] = []
    offset = 0
    while offset < max(1, int(limit)):
        page_limit = min(_PAGE, max(1, int(limit) - offset))
        data = fetcher(limit=page_limit, offset=offset, include_total=False) or {}
        chunk = data.get("rows") or data.get("items") or []
        if not isinstance(chunk, list):
            break
        for r in chunk:
            if isinstance(r, dict) and not r.get("deleted"):
                out.append(r)
        if len(chunk) < page_limit:
            break
        offset += page_limit
        if offset >= 50000:
            break
    return out


def _pull_sync_pages(collection: str, *, limit: int = 8000) -> list[dict]:
    """Fallback: full store sync pull with keyset pagination."""
    from core import server_api as api

    token = _token()
    all_docs: list[dict] = []
    after_id = None
    seen: set[str] = set()
    page_size = 5000
    while len(all_docs) < limit:
        docs, _ = api.pull_collection(
            token,
            collection,
            after_id=after_id,
            include_deleted=False,
            limit=page_size,
            timeout=90.0,
        )
        if not docs:
            break
        new_count = 0
        last_id = after_id
        for d in docs:
            if not isinstance(d, dict) or d.get("deleted"):
                continue
            raw = d.get("id") if d.get("id") is not None else d.get("local_id")
            key = str(raw) if raw is not None else ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            all_docs.append(d)
            new_count += 1
            try:
                last_id = int(raw)
            except (TypeError, ValueError):
                pass
        if len(docs) < page_size or new_count == 0:
            break
        if last_id is None or last_id == after_id:
            break
        after_id = last_id
    return all_docs


def customers(*, force: bool = False) -> list[dict]:
    def _load():
        try:
            return _rows_from_store("customers")
        except Exception as exc:
            log.warning("online_catalog customers store API: %s — sync fallback", exc)
            return _pull_sync_pages("customers")

    return _cached_list("customers", _load, force=force)


def suppliers(*, force: bool = False) -> list[dict]:
    def _load():
        try:
            return _rows_from_store("suppliers")
        except Exception as exc:
            log.warning("online_catalog suppliers store API: %s — sync fallback", exc)
            return _pull_sync_pages("suppliers")

    return _cached_list("suppliers", _load, force=force)


def doctors(*, force: bool = False) -> list[dict]:
    def _load():
        try:
            return _rows_from_store("doctors")
        except Exception as exc:
            log.warning("online_catalog doctors store API: %s — sync fallback", exc)
            return _pull_sync_pages("doctors")

    return _cached_list("doctors", _load, force=force)


def shelf_entities(kind: str, *, force: bool = False) -> list[dict]:
    """racks / sections / boxes held on the server.

    The Shelf screen read these three tables straight off the local connection,
    which Online is an empty :memory: shell -- so a shop that had mapped every
    rack in the pharmacy opened Shelf and saw nothing at all.
    """
    key = str(kind or "").strip().lower()
    if key not in ("racks", "sections", "boxes"):
        return []
    return _cached_list(key, lambda: _pull_sync_pages(key), force=force)


def stock_disposals(*, force: bool = False) -> list[dict]:
    """Write-offs and disposals held on the server.

    There is no /api/store route for these, so the sync pull is the only way
    to see them. Without it the Returns screen's Write-off tab read the
    engine's :memory: connection Online and showed an empty history with a
    zero badge on a shop with a drawer full of write-offs.
    """
    return _cached_list(
        "stock_disposals", lambda: _pull_sync_pages("stock_disposals"), force=force
    )


def medicines(*, force: bool = False) -> list[dict]:
    """Store inventory only (same as Offline medicines table) — never global master."""

    def _load():
        rows: list[dict] = []
        try:
            from core import store_query_client as sq

            offset = 0
            while offset < 50000:
                data = sq.list_inventory(q="", limit=_PAGE, offset=offset, include_total=False) or {}
                raw = data.get("rows") or data.get("medicines") or data.get("items") or []
                if not isinstance(raw, list) or not raw:
                    break
                for r in raw:
                    if not isinstance(r, dict):
                        continue
                    mid = r.get("id") or r.get("local_id")
                    rows.append(
                        {
                            "id": mid,
                            "local_id": mid,
                            "name": r.get("name") or "",
                            "batch_no": r.get("batch_no") or r.get("batch") or "",
                            "expiry_date": r.get("expiry_date") or r.get("expiry") or "",
                            "stock_qty": r.get("stock_qty")
                            if r.get("stock_qty") is not None
                            else r.get("stock"),
                            "unit": r.get("unit") or "1",
                            "mrp": r.get("mrp") or 0,
                            "rate": r.get("rate") or 0,
                            "type": r.get("type") or "",
                            "schedule": r.get("schedule") or "",
                            "gst_percent": r.get("gst_percent") or 0,
                            "is_hidden": r.get("is_hidden") or 0,
                            "manufacturer": r.get("manufacturer") or "",
                            "location": r.get("location") or "",
                            # the batch's own supplier, for Alert & Monitoring
                            "supplier_name": r.get("supplier_name") or "",
                            # Carry the fields an EDIT needs. Dropping version
                            # here made every doc built from this cache born
                            # stale: _meta() stamped version 1 against a server
                            # copy further ahead and the write was refused as
                            # "your copy is out of date", with no way to recover
                            # because a refetch rebuilt the same version-less row.
                            "version": r.get("version"),
                            "hsn_code": r.get("hsn_code") or "",
                            "content_drug": r.get("content_drug") or "",
                            "client_uuid": r.get("client_uuid") or "",
                        }
                    )
                if len(raw) < _PAGE:
                    break
                offset += _PAGE
        except Exception as exc:
            log.warning("online_catalog inventory: %s — sync medicines fallback", exc)
            rows = _pull_sync_pages("medicines")
        return rows

    rows = _cached_list("medicines_inventory", _load, force=force)
    with _lock:
        if rows and not _medicine_by_name:
            _rebuild_medicine_index(rows)
    return rows


def _bill_customer_name_set() -> set[str]:
    """Cached distinct customer names from current-FY sales — not fetched on every keystroke."""
    global _bill_customer_names
    now = time.time()
    with _lock:
        hit = _bill_customer_names
        if hit and (now - hit[0]) < _BILL_NAMES_TTL_SEC:
            return set(hit[1])
    names: set[str] = set()
    try:
        from core import store_query_client as sq

        from_date = ""
        to_date = ""
        try:
            from core.history_prefs import current_fy_bounds

            from_date, to_date = current_fy_bounds()
        except Exception:
            pass
        data = (
            sq.list_sales(
                from_date=from_date or "",
                to_date=to_date or "",
                limit=1000,
                # Only customer_name is read below; the default include_total makes
                # the server COUNT the whole financial year for nothing.
                include_total=False,
            )
            or {}
        )
        for r in data.get("rows") or []:
            if not isinstance(r, dict):
                continue
            n = str(r.get("customer_name") or "").strip()
            if n:
                names.add(n)
    except Exception as exc:
        log.debug("bill customer names: %s", exc)
        with _lock:
            hit = _bill_customer_names
            if hit:
                return set(hit[1])
        return set()
    with _lock:
        _bill_customer_names = (now, names)
    return set(names)


def customer_names(*, include_from_sales: bool = True) -> list[str]:
    """Store customers for Sales / History — same Roshan store list Online."""
    from core.customer_service import COUNTER_SALE

    names = {
        str(c.get("name") or "").strip()
        for c in customers()
        if str(c.get("name") or "").strip()
    }
    if include_from_sales:
        names |= _bill_customer_name_set()
    ordered = sorted(names, key=str.upper)
    if not any(n.upper() == COUNTER_SALE for n in ordered):
        return [COUNTER_SALE] + ordered
    return ordered


def doctor_names() -> list[str]:
    return sorted(
        {str(d.get("name") or "").strip() for d in doctors() if str(d.get("name") or "").strip()},
        key=str.upper,
    )


def supplier_names() -> list[str]:
    return sorted(
        {str(s.get("name") or "").strip() for s in suppliers() if str(s.get("name") or "").strip()},
        key=str.upper,
    )


def _customer_view(c: dict) -> dict:
    return {
        "id": int(c.get("id") or c.get("local_id") or 0),
        "name": c.get("name") or "",
        "phone": c.get("phone") or "",
        "address": c.get("address") or "",
        "total_due": float(c.get("total_due") or 0),
        "total_credit": float(c.get("total_credit") or 0),
    }


def _supplier_view(s: dict) -> dict:
    return {
        "id": int(s.get("id") or s.get("local_id") or 0),
        "name": s.get("name") or "",
        "phone": s.get("phone") or "",
        "address": s.get("address") or "",
        "gstin": s.get("gstin") or "",
        "dl_numbers": s.get("dl_numbers") or "",
        "total_due": float(s.get("total_due") or 0),
        "total_credit": float(s.get("total_credit") or 0),
    }


def find_customer_by_name(name: str, *, force: bool = False) -> Optional[dict]:
    from core.customer_service import COUNTER_SALE, is_counter_sale_name

    n = (name or "").strip()
    if not n:
        return None
    if is_counter_sale_name(n):
        n = COUNTER_SALE
    up = n.upper()
    customers(force=force)
    with _lock:
        c = _customer_index.get(up)
        if c:
            return _customer_view(c)
    return None


def find_customer_by_id(customer_id: int | str) -> Optional[dict]:
    try:
        cid = int(customer_id)
    except (TypeError, ValueError):
        return None
    if cid <= 0:
        return None
    customers()
    with _lock:
        c = _customer_by_id.get(cid)
        if c:
            return _customer_view(c)
    return None


def find_supplier_by_name(name: str, *, force: bool = False) -> Optional[dict]:
    up = (name or "").strip().upper()
    if not up:
        return None
    suppliers(force=force)
    with _lock:
        s = _supplier_by_name.get(up)
        if s:
            return _supplier_view(s)
    return None


def find_supplier_by_id(supplier_id: int | str) -> Optional[dict]:
    try:
        sid = int(supplier_id)
    except (TypeError, ValueError):
        return None
    if sid <= 0:
        return None
    suppliers()
    with _lock:
        s = _supplier_by_id.get(sid)
        if s:
            return _supplier_view(s)
    return None


def find_doctor_by_name(name: str) -> Optional[dict]:
    up = (name or "").strip().upper()
    if not up:
        return None
    doctors()
    with _lock:
        d = _doctor_by_name.get(up)
        return dict(d) if d else None


def medicine_by_id(medicine_id: int | str) -> Optional[dict]:
    try:
        mid = int(medicine_id)
    except (TypeError, ValueError):
        return None
    if mid <= 0:
        return None
    with _lock:
        hit = _medicine_by_id.get(mid)
        if hit:
            return dict(hit)
    medicines()
    with _lock:
        hit = _medicine_by_id.get(mid)
        if hit:
            return dict(hit)
    return None


def medicines_for_name(name: str) -> list[dict]:
    """All inventory rows (batches) for one medicine name. Case-insensitive exact match."""
    key = (name or "").strip().lower()
    if not key:
        return []
    medicines()
    with _lock:
        rows = _medicine_by_name.get(key) or []
        return [dict(r) for r in rows]


def medicines_for_name_match(name: str) -> list[dict]:
    """Name+batch match pool — includes hidden stubs (inventory UI hides them).

    Purchase resolve must see hidden zero-stock clones so we reuse their id
    instead of allocating a second batch row.
    """
    from core.name_utils import normalize_medicine_name

    key = normalize_medicine_name(name).strip().lower()
    if not key:
        return []
    # Start with visible inventory cache.
    out = {int(r.get("id") or r.get("local_id") or 0): dict(r) for r in medicines_for_name(key)}
    try:
        from core import store_query_client as sq

        offset = 0
        while offset < 50000:
            data = sq.list_inventory(
                q=key, limit=_PAGE, offset=offset, include_total=False, hidden="all",
            ) or {}
            raw = data.get("rows") or data.get("medicines") or data.get("items") or []
            if not isinstance(raw, list) or not raw:
                break
            for r in raw:
                if not isinstance(r, dict):
                    continue
                nm = str(r.get("name") or "").strip().lower()
                if nm != key:
                    continue
                mid = int(r.get("id") or r.get("local_id") or 0)
                if mid <= 0:
                    continue
                out[mid] = {
                    "id": mid,
                    "local_id": mid,
                    "name": r.get("name") or "",
                    "batch_no": r.get("batch_no") or r.get("batch") or "",
                    "expiry_date": r.get("expiry_date") or r.get("expiry") or "",
                    "stock_qty": r.get("stock_qty")
                    if r.get("stock_qty") is not None
                    else r.get("stock"),
                    "unit": r.get("unit") or "1",
                    "mrp": r.get("mrp") or 0,
                    "rate": r.get("rate") or 0,
                    "type": r.get("type") or "",
                    "schedule": r.get("schedule") or "",
                    "gst_percent": r.get("gst_percent") or 0,
                    "is_hidden": r.get("is_hidden") or 0,
                    "manufacturer": r.get("manufacturer") or "",
                    "location": r.get("location") or "",
                    "hsn_code": r.get("hsn_code") or "",
                    "content_drug": r.get("content_drug") or "",
                    "deleted": r.get("deleted") or False,
                    "version": r.get("version") or 1,
                }
            if len(raw) < _PAGE:
                break
            offset += _PAGE
    except Exception as exc:
        log.warning("medicines_for_name_match inventory: %s — sync fallback", exc)
        try:
            for r in _pull_sync_pages("medicines"):
                if not isinstance(r, dict) or r.get("deleted"):
                    continue
                nm = str(r.get("name") or "").strip().lower()
                if nm != key:
                    continue
                mid = int(r.get("id") or r.get("local_id") or 0)
                if mid > 0:
                    out[mid] = dict(r)
                    out[mid]["id"] = mid
                    out[mid]["local_id"] = mid
        except Exception:
            pass
    return list(out.values())


def medicine_name_representatives() -> list[dict]:
    """One visible inventory row per unique medicine name."""
    medicines()
    with _lock:
        out = []
        for rows in _medicine_by_name.values():
            for m in rows:
                if m.get("is_hidden"):
                    continue
                out.append(dict(m))
                break
        return out


def search_medicine_names(
    q: str = "",
    *,
    limit: int = 40,
    show_zero: bool = True,
    as_of: Any = None,
    reserved: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Inventory medicine names for Sales dropdown (store-scoped, same as Inventory).

    When ``as_of`` is set, expired batches (as of that date) are excluded from totals.
    ``reserved`` maps medicine id → qty already on the current bill.
    """
    from datetime import date as _date

    qn = (q or "").strip().lower()
    reserved = reserved or {}
    as_of_date: Optional[_date] = None
    if as_of is not None:
        try:
            from core.batch_visibility import parse_bill_as_of

            as_of_date = parse_bill_as_of(as_of)
        except Exception:
            as_of_date = None

    cap = max(1, min(int(limit or 40), 20000))
    medicines()
    # Batches that had not come in yet on a past bill date (core.sale_availability).
    missing: frozenset = frozenset()
    if as_of_date is not None:
        try:
            from core.sale_availability import batches_missing_on_online

            missing = batches_missing_on_online(as_of_date)
        except Exception:
            missing = frozenset()

    if as_of_date is None and not reserved:
        with _lock:
            names = list(_medicine_name_summaries)
        if qn:
            names = [n for n in names if qn in str(n.get("name") or "").lower()]
        if not show_zero:
            names = [n for n in names if float(n.get("stock") or 0) > 0]
        # Best matches first: names that START with what was typed, then a word
        # starting with it, then the rest. Catalogue order put AMOXY above
        # MECOVET when the shop typed "m".
        from core.name_search_rank import rank_rows

        return [dict(n) for n in rank_rows(names, qn, limit=cap)]

    def _reserved_for(mid: int) -> float:
        try:
            return float(
                reserved.get(mid, reserved.get(str(mid), reserved.get(int(mid), 0))) or 0
            )
        except (TypeError, ValueError):
            try:
                return float(reserved.get(str(mid), 0) or 0)
            except (TypeError, ValueError):
                return 0.0

    with _lock:
        groups = [
            (k, list(v))
            for k, v in _medicine_by_name.items()
            if not qn or qn in k
        ]

    by_name: dict[str, dict[str, Any]] = {}
    for _key, rows in groups:
        for m in rows:
            if m.get("is_hidden"):
                continue
            name = str(m.get("name") or "").strip()
            if not name:
                continue
            if as_of_date is not None:
                try:
                    from core.batch_visibility import is_expired_as_of

                    exp = str(m.get("expiry_date") or "")
                    if exp and is_expired_as_of(exp, as_of_date):
                        continue
                except Exception:
                    pass
            mid = int(m.get("id") or m.get("local_id") or 0)
            if mid in missing:
                continue
            stock = float(m.get("stock_qty") or 0)
            avail = max(0.0, stock - _reserved_for(mid))
            cur = by_name.get(name)
            if not cur:
                by_name[name] = {
                    "name": name,
                    "stock": avail,
                    "mrp": float(m.get("mrp") or 0),
                    "unit": m.get("unit") or "1",
                    "type": m.get("type") or "",
                    "schedule": str(m.get("schedule") or ""),
                    "batch_count": 1,
                }
            else:
                cur["stock"] = float(cur.get("stock") or 0) + avail
                cur["batch_count"] = int(cur.get("batch_count") or 0) + 1
                if float(m.get("mrp") or 0) > float(cur.get("mrp") or 0):
                    cur["mrp"] = float(m.get("mrp") or 0)
    names = list(by_name.values())
    if not show_zero:
        names = [n for n in names if float(n.get("stock") or 0) > 0]
    # Same ordering as everywhere else -- see core.name_search_rank.
    from core.name_search_rank import rank_rows

    return rank_rows(names, qn, limit=cap)


def batches_for_name(
    name: str,
    *,
    include_zero: bool = False,
    as_of: Any = None,
) -> list[dict[str, Any]]:
    """Batches for one medicine name from the name index (no full-catalog scan)."""
    as_of_date = None
    if as_of is not None:
        try:
            from core.batch_visibility import parse_bill_as_of

            as_of_date = parse_bill_as_of(as_of)
        except Exception:
            as_of_date = None
    # Batches that had not come in yet on a past bill date (core.sale_availability).
    missing: frozenset = frozenset()
    if as_of_date is not None:
        try:
            from core.sale_availability import batches_missing_on_online

            missing = batches_missing_on_online(as_of_date)
        except Exception:
            missing = frozenset()
    out = []
    for m in medicines_for_name(name):
        if m.get("is_hidden"):
            continue
        if as_of_date is not None:
            try:
                from core.batch_visibility import is_expired_as_of

                exp = str(m.get("expiry_date") or "")
                if exp and is_expired_as_of(exp, as_of_date):
                    continue
            except Exception:
                pass
        stock = float(m.get("stock_qty") or 0)
        if not include_zero and stock <= 0:
            continue
        mid = int(m.get("id") or m.get("local_id") or 0)
        if mid in missing:
            continue
        out.append(
            {
                "id": mid,
                "name": m.get("name") or "",
                "batch": m.get("batch_no") or "",
                "expiry": m.get("expiry_date") or "",
                "stock": stock,
                "available": stock,
                "unit": m.get("unit") or "1",
                "mrp": float(m.get("mrp") or 0),
                "rate": float(m.get("rate") or 0),
                "type": m.get("type") or "",
                "schedule": m.get("schedule") or "",
                "gst_percent": float(m.get("gst_percent") or 0),
                "location": m.get("location") or "",
            }
        )
    out.sort(key=lambda b: (str(b.get("expiry") or ""), str(b.get("batch") or "")))
    return out


def search_medicines_flat(q: str = "", *, limit: int = 40) -> list[dict[str, Any]]:
    qn = (q or "").strip().lower()
    cap = max(1, min(int(limit or 40), 200))
    medicines()
    with _lock:
        groups = list(_medicine_by_name.items())
    out = []
    for key, rows in groups:
        name_hit = (not qn) or (qn in key)
        # Depleted-batch visibility, matching Android BatchVisibility.
        #
        # Selling a batch to zero leaves the row in place, so buying a fresh
        # batch of the same medicine listed BOTH in the sales picker -- the
        # new one and the empty one showing 0 stock, side by side. Picking the
        # empty one is an easy mistake to make at a counter.
        #
        # Hide an empty batch while any other batch of that name has stock.
        # When every batch is empty keep the newest as an anchor, so a
        # medicine you are out of can still be found and reordered.
        visible = [m for m in rows if not m.get("is_hidden")]
        name_total = sum(float(m.get("stock_qty") or 0) for m in visible)
        anchor_id = 0
        if name_total <= 0 and visible:
            anchor_id = max(
                int(m.get("id") or m.get("local_id") or 0) for m in visible
            )
        for m in visible:
            stock_val = float(m.get("stock_qty") or 0)
            this_id = int(m.get("id") or m.get("local_id") or 0)
            # A negative row is an unsettled shortage, not a depleted batch. Keep
            # it findable so the counter can see what is owed, instead of the
            # medicine quietly disappearing from the picker while the shortage
            # keeps reducing the name total.
            if stock_val == 0 and (name_total > 0 or this_id != anchor_id):
                continue
            if stock_val < 0 and name_total > 0 and this_id != anchor_id:
                continue
            name = str(m.get("name") or "").strip()
            if not name:
                continue
            if not name_hit and qn not in str(m.get("batch_no") or "").lower():
                continue
            mid = int(m.get("id") or m.get("local_id") or 0)
            out.append(
                {
                    "id": mid,
                    "name": name,
                    "batch": m.get("batch_no") or "",
                    "expiry": m.get("expiry_date") or "",
                    "stock": float(m.get("stock_qty") or 0),
                    "unit": m.get("unit") or "1",
                    "mrp": float(m.get("mrp") or 0),
                    "rate": float(m.get("rate") or 0),
                    "type": m.get("type") or "",
                    "schedule": m.get("schedule") or "",
                    "gst_percent": float(m.get("gst_percent") or 0),
                }
            )
    # Everything that matches is collected first, THEN ranked, THEN cut.
    #
    # Stopping at `cap` while collecting looked like a harmless optimisation, but
    # the catalogue is held alphabetically: typing "m" filled the list with
    # ALFUIN, ALKEMFLAM, AMLOKIND and stopped long before it reached M or
    # M CEFT+TAZO INJ, so the medicines that actually START with the typed
    # letter were dropped before the ranking that was meant to lift them to the
    # top ever ran. The shop typed "m" and could not find "M".
    #
    # A catalogue is a few thousand rows; ranking all of them costs a sort.
    from core.name_search_rank import rank_rows

    return rank_rows(out, qn, limit=cap)
