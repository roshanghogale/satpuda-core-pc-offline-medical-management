"""Read helpers for Tauri pages — same store DB as python main.py (Tk)."""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from typing import Any, Optional


def history_search_rank(q: str | None, bill_no: Any, *texts: Any) -> int:
    """How a history row answers the search box: 2 = it IS the bill typed, 1 = the text is in
    its bill number, name, phone or doctor, 0 = no match.

    A sale is stored as "SCB2/FY2026-27". Searched as stored, "2" matched every bill of
    the year through its "/FY2026" and bill 2 -- one of the oldest -- sat at the bottom of
    the list (1 Oct 2026). Only the number the shop sees ("SCB2") is searched, and the
    bill whose number is the one typed comes first."""
    from core.fy_serial import display_sales_bill_no

    want = (q or "").strip().lower()
    if not want:
        return 1
    shown = display_sales_bill_no(str(bill_no or "")).lower()
    digits = re.sub(r"\D", "", shown)
    if shown == want or (want.isdigit() and digits and int(digits) == int(want)
                         and re.fullmatch(r"[a-z]*\d+", shown)):
        return 2
    if want in shown or any(want in str(t or "").lower() for t in texts):
        return 1
    return 0


def _search_history_dicts(rows: list, q: str | None) -> list:
    """Online history rows narrowed to the search box, the bill typed first."""
    if not (q or "").strip():
        return rows
    ranked = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        rank = history_search_rank(
            q, r.get("bill_no"), r.get("customer_name"), r.get("customer_phone") or r.get("phone"),
            r.get("doctor_name"),
        )
        if rank:
            ranked.append((-rank, i, r))
    ranked.sort(key=lambda x: (x[0], x[1]))
    return [r for _, _, r in ranked]


def _server_search_term(q: str | None, party: str | None) -> str:
    """The ONE search term the server can take, out of the two the page has.

    The store API accepts a single `q`, which it tests as one wildcard against
    bill_no OR customer_name OR doctor_name (purchases: purchase_no OR
    supplier_name OR bill_number). The page has two independent filters — a free
    search box and a customer/supplier picker — and it used to send them JOINED
    BY A SPACE. "SCB12 RAHUL" is not a substring of anything, so using both
    filters at once returned an empty list while the matching bills sat right
    there in the shop's history.

    So: hand the server whichever one is set, preferring the typed search box,
    and let the caller's own customer/supplier check narrow what comes back. The
    party filter is applied locally against `customer_name` / `supplier_name`
    either way, so the result is the same set — it just no longer depends on the
    two strings happening to sit next to each other in one column.
    """
    typed = (q or "").strip()
    if typed:
        return typed
    return (party or "").strip()


#: Sales History lists every bill in the chosen range, up to this many. It used to stop at
#: 500 without a word -- the default limit of list_sales_history, which the page never
#: overrides -- and worked its summary out on those 500: a range from 2026-03-20 to today
#: showed 500 bills and left the back-dated ones and the rest of the money out.
SALES_HISTORY_ROW_CEILING = 10000
#: The server's own page size for /api/store/sales (adminService.listSales caps at 5000).
_SALES_PAGE = 5000
#: Sale ids per summary request. The ids travel in the query string; thousands of them
#: would overflow the server's header limit, so the summary is asked in parts and added up.
_SUMMARY_IDS_PER_CALL = 800
_SUMMARY_SUM_KEYS = (
    "total_sales",
    "total_discount",
    "total_profit",
    "total_returns",
    "today_revenue",
    "today_cash",
    "today_online",
    "month_revenue",
)


def _sales_history_row_cap(limit) -> int:
    """How many bills Sales History may list: all of them up to the ceiling."""
    try:
        wanted = int(limit or 0)
    except (TypeError, ValueError):
        wanted = 0
    if wanted <= 0:
        return SALES_HISTORY_ROW_CEILING
    return max(1, min(wanted, SALES_HISTORY_ROW_CEILING))


def _all_sales_history_rows(sq, cap: int, **filters) -> dict:
    """Every sale in the range, page after page, up to ``cap``.

    Keeps the first page's envelope (filter_from / filter_to / default_fy_applied). When the
    range holds more than ``cap`` bills, ``rows_total`` says how many it holds.
    """
    page = max(1, min(_SALES_PAGE, cap))
    first = sq.list_sales(limit=page, include_total=False, **filters) or {}
    got = list(first.get("rows") or [])
    full = len(got) >= page
    while full and len(got) < cap:
        want = max(1, min(_SALES_PAGE, cap - len(got)))
        chunk = list(
            (
                sq.list_sales(limit=want, offset=len(got), include_total=False, **filters)
                or {}
            ).get("rows")
            or []
        )
        got.extend(chunk)
        full = len(chunk) >= want
    # A bill saved between two page reads shifts the pages by one; list it once.
    rows: list = []
    seen: set = set()
    for r in got[:cap]:
        key = (r.get("id") or r.get("local_id")) if isinstance(r, dict) else None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        rows.append(r)
    out = dict(first)
    out["rows"] = rows
    if full and len(got) >= cap:
        probe = sq.list_sales(limit=1, offset=cap, include_total=True, **filters) or {}
        if probe.get("rows"):
            try:
                out["rows_total"] = max(int(probe.get("total") or 0), cap + 1)
            except (TypeError, ValueError):
                out["rows_total"] = cap + 1
    return out


def _sales_summary_over(sq, ids: list, **filters) -> dict:
    """The server's sales summary for these bills, asked in parts when there are many."""
    ids = list(ids or [])
    if len(ids) <= _SUMMARY_IDS_PER_CALL:
        return sq.sales_summary(ids=ids, **filters) or {}
    from core.store_query_client import fetch_parallel

    chunks = [
        ids[i : i + _SUMMARY_IDS_PER_CALL] for i in range(0, len(ids), _SUMMARY_IDS_PER_CALL)
    ]
    parts: list = []
    for i in range(0, len(chunks), 4):
        parts.extend(
            fetch_parallel(
                *[
                    (lambda c=c: sq.sales_summary(ids=c, **filters) or {})
                    for c in chunks[i : i + 4]
                ]
            )
        )
    return {
        key: round(sum(float((p or {}).get(key) or 0) for p in parts), 2)
        for key in _SUMMARY_SUM_KEYS
    }


def _rows_note(listed: int, total) -> str:
    """What the page says when the range holds more bills than it lists."""
    try:
        total_n = int(total or 0)
    except (TypeError, ValueError):
        total_n = 0
    if total_n <= listed:
        return ""
    return (
        f"Only the newest {listed:,} of {total_n:,} bills in this date range are listed, and "
        "the totals below cover only those. Narrow the dates to see the rest."
    )


def _online_read(fn, failures: list):
    """Run one Online read, remembering a failure instead of pretending it was empty.

    Every Online read on these pages was `except Exception: return {}`. The page
    then rendered a perfectly healthy screen with no rows, so a shop whose link
    to its store on the server had broken -- a renamed store, a rebuilt registry,
    a rejected re-pair -- saw a clean, working, completely EMPTY app and was
    given no reason for it. That is the "store cleared and there is no data"
    report; the ledger was on the server the whole time. Callers now put
    `server_error` in the payload so the screen can say so.
    """
    try:
        return fn() or {}
    except Exception as exc:
        failures.append(str(exc).strip() or exc.__class__.__name__)
        return {}


def _server_error_text(failures: list) -> str:
    """One sentence for the banner: what broke, not a stack trace."""
    if not failures:
        return ""
    first = failures[0]
    low = first.lower()
    if "not linked to any store" in low:
        return first
    if any(w in low for w in ("timed out", "timeout", "connection", "unreachable", "resolve", "refused")):
        return "Cannot reach the server. Showing nothing rather than out-of-date figures."
    # "Store name does not match key" is what the server actually answers a
    # rejected re-pair with (server-live auth middleware), and it carries no
    # status code in its text -- so match the sentence, not just 401/403.
    if (
        "401" in first
        or "403" in first
        or "unauthor" in low
        or "forbidden" in low
        or "does not match key" in low
        or "pair" in low
    ):
        return ("This PC is no longer paired with its store on the server. "
                "Open Settings and reconnect it.")
    return f"Could not read from the server: {first}"


def _table_exists(conn, name: str) -> bool:
    try:
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (name,),
            ).fetchone()
        )
    except Exception:
        return False


def _table_cols(conn, name: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info([{name}])")}
    except Exception:
        return set()


def _pick_col(cols: set[str], *candidates: str) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    return None


# How many names the sales/purchase dropdowns are given. They filter in the
# browser, so a name past the cap can never be found -- and real shops here run
# to a few thousand customers. Rows are small and the query is a single scan.
_CUSTOMER_LIST_LIMIT = 20000
_NAME_LIST_LIMIT = 5000


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v if v is not None else default)
    except Exception:
        return default


_online_supplier_maps_cache: dict[str, Any] = {
    "sig": None,
    "by_id": {},
    "by_nb": {},
    "by_name": {},
    "ts": 0.0,
}


def _norm_med_name(value: Any) -> str:
    return str(value or "").strip().upper()


def _norm_batch(value: Any) -> str:
    return str(value or "").strip().upper()


def _purchase_line_items(bill: dict[str, Any]) -> list[dict[str, Any]]:
    items = (
        bill.get("items")
        or bill.get("medicines")
        or bill.get("lines")
        or bill.get("purchase_items")
        or []
    )
    if isinstance(items, dict):
        items = list(items.values())
    return [it for it in items if isinstance(it, dict)]


def _purchase_supplier_name(bill: dict[str, Any]) -> str:
    name = str(bill.get("supplier_name") or bill.get("party") or "").strip()
    if name:
        return name
    try:
        sid = int(bill.get("supplier_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    if sid <= 0:
        return ""
    try:
        from core.online_catalog import find_supplier_by_id

        found = find_supplier_by_id(sid) or {}
        return str(found.get("name") or "").strip()
    except Exception:
        return ""


def _unwrap_purchase_doc(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    for key in ("purchase", "doc", "data", "row"):
        nested = raw.get(key)
        if isinstance(nested, dict) and (
            nested.get("items") or nested.get("medicines") or nested.get("id")
        ):
            return nested
    return raw


def online_latest_supplier_maps() -> tuple[
    dict[int, str], dict[tuple[str, str], str], dict[str, str]
]:
    """Latest purchase supplier per medicine id / name+batch / name (online)."""
    import time

    try:
        from core.store_manager import get_active_store_key

        store = (get_active_store_key() or "").strip()
    except Exception:
        store = ""
    try:
        from core.online_mutation_queue import pending_rows

        pend = len(pending_rows(collection="purchases") or [])
    except Exception:
        pend = 0
    sig = f"{store}:{pend}"
    now = time.time()
    cached = _online_supplier_maps_cache
    if (
        cached.get("sig") == sig
        and cached.get("by_id") is not None
        and now - float(cached.get("ts") or 0) < 45
    ):
        return cached["by_id"], cached["by_nb"], cached["by_name"]

    from core import store_query_client as sq
    from core.online_mutation_queue import merge_server_rows, overlay_purchase_dicts
    from core.server_crud import get_doc

    listed = list(
        (
            sq.list_purchases(
                from_date="2000-01-01", to_date="2099-12-31", limit=5000
            )
            or {}
        ).get("rows")
        or []
    )
    bills = merge_server_rows(
        listed, overlay_purchase_dicts(), collection="purchases"
    )
    by_id: dict[int, dict[str, Any]] = {}
    for bill in bills:
        if not isinstance(bill, dict) or bill.get("deleted"):
            continue
        try:
            pid = int(bill.get("id") or bill.get("local_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid:
            by_id[pid] = dict(bill)

    need_items = [
        b
        for b in by_id.values()
        if not _purchase_line_items(b)
    ]
    if need_items:
        try:
            from core import server_api as api

            token = api.store_token_for_active()
            docs, _ = api.pull_collection(
                token, "purchases", include_deleted=False, limit=5000
            )
            for doc in docs or []:
                if not isinstance(doc, dict):
                    continue
                try:
                    pid = int(doc.get("id") or doc.get("local_id") or 0)
                except (TypeError, ValueError):
                    pid = 0
                if pid <= 0:
                    continue
                cur = by_id.get(pid) or {}
                merged = dict(cur)
                merged.update(doc)
                if _purchase_line_items(doc) and not _purchase_line_items(merged):
                    merged["items"] = _purchase_line_items(doc)
                by_id[pid] = merged
        except Exception:
            pass

    still_missing = [
        pid
        for pid, bill in by_id.items()
        if pid > 0 and not _purchase_line_items(bill)
    ]
    for pid in still_missing[:250]:
        doc = {}
        try:
            doc = _unwrap_purchase_doc(get_doc("purchases", pid) or {})
        except Exception:
            doc = {}
        if not _purchase_line_items(doc):
            try:
                extra = _unwrap_purchase_doc(sq.get_purchase(pid) or {})
                if extra:
                    doc = extra or doc
            except Exception:
                pass
        if not isinstance(doc, dict) or not _purchase_line_items(doc):
            continue
        cur = by_id.get(pid) or {}
        merged = dict(cur)
        merged.update(doc)
        merged["items"] = _purchase_line_items(doc)
        by_id[pid] = merged

    dated: list[tuple[str, int, dict[str, Any]]] = []
    for pid, bill in by_id.items():
        dated.append((str(bill.get("purchase_date") or ""), pid, bill))
    for bill in bills:
        if not isinstance(bill, dict):
            continue
        try:
            pid = int(bill.get("id") or bill.get("local_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and pid in by_id:
            continue
        dated.append((str(bill.get("purchase_date") or ""), pid, bill))
    dated.sort()

    map_id: dict[int, str] = {}
    map_nb: dict[tuple[str, str], str] = {}
    map_name: dict[str, str] = {}
    for _dt, _pid, bill in dated:
        sname = _purchase_supplier_name(bill)
        if not sname:
            continue
        for it in _purchase_line_items(bill):
            try:
                mid = int(it.get("medicine_id") or 0)
            except (TypeError, ValueError):
                mid = 0
            if mid <= 0:
                try:
                    mid = int(it.get("id") or 0)
                except (TypeError, ValueError):
                    mid = 0
            nm = _norm_med_name(it.get("name") or it.get("medicine_name"))
            batch = _norm_batch(it.get("batch_no") or it.get("batch"))
            if mid > 0:
                map_id[mid] = sname
            if nm and batch:
                map_nb[(nm, batch)] = sname
            if nm:
                map_name[nm] = sname

    _online_supplier_maps_cache.update(
        {
            "sig": sig,
            "by_id": map_id,
            "by_nb": map_nb,
            "by_name": map_name,
            "ts": now,
        }
    )
    return map_id, map_nb, map_name


def latest_supplier_by_medicine_id(conn, medicine_ids) -> dict[int, str]:
    """The supplier name Inventory shows against a medicine.

    The medicine's own ``supplier_name`` first -- what the Opening Stock page,
    the loader app, the Inventory editor or the last purchase wrote on it -- and
    the purchase history behind it only where that note is empty. Derivation
    alone could never speak for opening stock, which has no purchase at all.
    """
    ids = []
    seen: set[int] = set()
    for raw in medicine_ids or []:
        try:
            mid = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if mid > 0 and mid not in seen:
            seen.add(mid)
            ids.append(mid)
    out: dict[int, str] = {}
    if ids and _table_exists(conn, "purchase_items") and _table_exists(
        conn, "purchases"
    ):
        pcols = _table_cols(conn, "purchases")
        icols = _table_cols(conn, "purchase_items")
        extra_p = []
        extra_i = []
        if "deleted" in pcols:
            extra_p.append("COALESCE(p.deleted,0)=0")
        if "is_autosave" in pcols:
            extra_p.append("COALESCE(p.is_autosave,0)=0")
        if "deleted" in icols:
            extra_i.append("COALESCE(pi.deleted,0)=0")
        extra_sql = ""
        if extra_p or extra_i:
            extra_sql = " AND " + " AND ".join(extra_i + extra_p)
        join_sup = ""
        name_expr = "''"
        if _table_exists(conn, "suppliers"):
            join_sup = " LEFT JOIN suppliers s ON s.id = p.supplier_id"
            name_expr = "COALESCE(s.name,'')"
        chunk = 400
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            ph = ",".join("?" * len(part))
            sql = (
                f"SELECT pi.medicine_id, {name_expr} "
                f"FROM purchase_items pi "
                f"JOIN purchases p ON p.id = pi.purchase_id "
                f"{join_sup} "
                f"WHERE pi.medicine_id IN ({ph}){extra_sql} "
                f"ORDER BY p.purchase_date ASC, p.id ASC"
            )
            try:
                for mid, name in conn.execute(sql, part):
                    try:
                        k = int(mid or 0)
                    except (TypeError, ValueError):
                        continue
                    if k > 0:
                        out[k] = str(name or "").strip()
            except Exception:
                continue
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            by_id, _by_nb, _by_name = online_latest_supplier_maps()
            for mid, name in by_id.items():
                if name:
                    out[mid] = name
    except Exception:
        pass
    # The note on the medicine is what a shop typed or what its last bill said,
    # so it is the answer wherever it exists.
    for mid, name in _stored_supplier_by_medicine_id(conn, ids).items():
        if name:
            out[mid] = name
    return out


def _stored_supplier_by_medicine_id(conn, ids) -> dict[int, str]:
    """medicines.supplier_name per id, from the shop's own catalogue."""
    out: dict[int, str] = {}
    if not ids:
        return out
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import medicines as _catalogue

            # One pass over the catalogue, not a lookup per row: Inventory can
            # ask about ten thousand medicines at once.
            wanted = {int(m) for m in ids}
            for doc in _catalogue() or []:
                try:
                    mid = int(doc.get("id") or doc.get("local_id") or 0)
                except (TypeError, ValueError):
                    continue
                if mid in wanted:
                    name = str(doc.get("supplier_name") or "").strip()
                    if name:
                        out[mid] = name
            return out
    except Exception:
        return out
    try:
        if "supplier_name" not in _table_cols(conn, "medicines"):
            return out
        chunk = 400
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            ph = ",".join("?" * len(part))
            rows = conn.execute(
                f"SELECT id, COALESCE(supplier_name,'') FROM medicines WHERE id IN ({ph})",
                part,
            ).fetchall()
            for mid, name in rows:
                text = str(name or "").strip()
                if text:
                    out[int(mid)] = text
    except Exception:
        pass
    return out


def _filter_by_column_visibility(
    page_key: str,
    columns: list[str],
    rows: list[list[Any]],
    force_include: list[str] | None = None,
) -> tuple[list[str], list[list[Any]]]:
    """Apply Settings → Layout column visibility (Status always kept if present)."""
    try:
        from core.column_config import get_visible_columns
    except Exception:
        return columns, rows
    # Ask about the real column list, Status included. Holding Status out and
    # then appending it unconditionally meant Purchase History's Status tick --
    # a genuine registry column there -- could never hide anything.
    visible = list(get_visible_columns(page_key, list(columns)))
    # ...but only a page that OFFERS a Status checkbox may hide it. On Inventory
    # Status is synthesised, has no registry entry and no tick, so it must not
    # be droppable by a stray saved value -- it is the row indicator.
    try:
        from core.column_config import TABLE_COLUMNS

        configurable = {name for name, _db in TABLE_COLUMNS.get(page_key) or []}
    except Exception:
        configurable = set()
    if (
        "Status" in columns
        and "Status" not in visible
        and "Status" not in configurable
    ):
        visible.append("Status")
    for name in force_include or []:
        if name in columns and name not in visible:
            # Keep forced columns just before Status (or at end).
            if "Status" in visible:
                visible.insert(visible.index("Status"), name)
            else:
                visible.append(name)
    idxs = [columns.index(c) for c in visible if c in columns]
    if not idxs:
        return columns, rows
    return (
        [columns[i] for i in idxs],
        [[row[i] if i < len(row) else "" for i in idxs] for row in rows],
    )


def _parse_expiry(raw: Any) -> Optional[date]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%Y", "%Y-%m", "%m-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt in ("%m/%Y", "%Y-%m", "%m-%Y"):
                # last day approx — use day 1 for days-left
                return date(dt.year, dt.month, 1)
            return dt.date()
        except ValueError:
            continue
    return None


def _days_left(expiry_raw: Any) -> Any:
    d = _parse_expiry(expiry_raw)
    if not d:
        return ""
    # Month-only expiries run to the month end -- the rule billing and the alert
    # lists use (batch_visibility.expiry_cutoff), so Inventory's Expired filter
    # and Alert & Monitoring count the same batches.
    try:
        from core.batch_visibility import expiry_cutoff

        d = expiry_cutoff(d.isoformat()) or d
    except Exception:
        pass
    return (d - date.today()).days


# Desktop lists use icon + Status column text (not full-row fill).
_STATUS_ICONS = {
    "due": "●",
    "partial": "◐",
    "cleared": "●",
    "credit": "●",
    "low_stock": "▼",
    "out_of_stock": "✖",
    "near_expiry": "◷",
    "expired": "◼",
    "purchase_return": "↩",
    "sales_return": "↩",
    "unfinished": "◍",
}

_STATUS_ICON_FILES = {
    "due": "status_due.png",
    "partial": "status_partial.png",
    "cleared": "status_cleared.png",
    "credit": "status_credit.png",
    "low_stock": "status_low_stock.png",
    "out_of_stock": "status_out_of_stock.png",
    "near_expiry": "status_near_expiry.png",
    "expired": "status_expired.png",
    "purchase_return": "status_purchase_return.png",
    "sales_return": "status_sales_return.png",
}


def _status_icon_file_url(status: str) -> Optional[str]:
    """Return API URL when a PNG exists under assets/status/."""
    try:
        import os

        from core.brand_assets import assets_dir

        base = _STATUS_ICON_FILES.get(status)
        if not base:
            return None
        folder = os.path.join(assets_dir(), "status")
        # Prefer dark variant when active theme is dark.
        dark_name = base.replace(".png", "_dark.png")
        try:
            from core.brand_assets import is_dark_theme

            dark = is_dark_theme()
        except Exception:
            dark = False
        candidates = []
        if dark:
            candidates.append(dark_name)
        candidates.append(base)
        for name in candidates:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return f"/api/brand/status-icon?status={status}"
        return None
    except Exception:
        return None


def _inventory_row_status(
    stock: Any,
    expiry: Any,
    name: str,
    med_type: str,
    unit: str,
    conn=None,
    low_thr: dict | None = None,
    near_thr: dict | None = None,
) -> Optional[str]:
    """Which flag an inventory row carries, in the classic priority order.

    Out of stock beats low stock, which beats expired, which beats near expiry --
    the same order the Tk Inventory uses, so a medicine reads the same in both.
    Thresholds come from the shop's own settings, per medicine type.

    Pass low_thr/near_thr to avoid a settings read per row; the caller loads
    them once outside its loop.
    """
    from core.alert_thresholds import (
        is_low_stock_qty as _is_low,
        is_near_expiry as _is_near,
        load_thresholds as _load_thr,
    )

    try:
        qty = float(stock or 0)
    except (TypeError, ValueError):
        qty = 0.0

    if qty <= 0:
        return "out_of_stock"

    # load_thresholds returns a TUPLE. This used to call .get("low_stock") on
    # it, which raises -- and the raise was swallowed, so every row in the shop
    # was judged at the built-in 10 units and 3 months no matter what had been
    # saved in Settings. Use the same two helpers the alert lists use, so the
    # Inventory screen and the Alert screen finally answer with one rule.
    if low_thr is None or near_thr is None:
        try:
            low_thr, near_thr = _load_thr(conn) if conn is not None else ({}, {})
        except Exception as exc:
            print(f"[inventory] thresholds: {exc}")
            low_thr, near_thr = {}, {}

    if _is_low(qty, str(med_type or ""), low_thr, None, unit=unit or None):
        return "low_stock"

    exp = _parse_expiry(expiry)
    if exp is not None:
        from datetime import date as _d

        try:
            from core.batch_visibility import expiry_cutoff

            # expiry_cutoff takes the raw TEXT. Handed the parsed date it raised,
            # the except below kept the raw 1st, and the row icon said Expired
            # for a batch billing still sells.
            cutoff = expiry_cutoff(exp.isoformat()) or exp
        except Exception:
            cutoff = exp
        today = _d.today()
        if cutoff < today:
            return "expired"
        if _is_near((cutoff - today).days, str(med_type or ""), near_thr):
            return "near_expiry"
    return None


def _indicator_style(status: Optional[str]) -> Optional[dict[str, Any]]:
    """Row style for React lists — respects Settings → Record Indicators."""
    if not status:
        return None
    try:
        from core.record_indicators import (
            STATUS_LABELS,
            get_status_color,
            load_record_indicator_prefs,
            resolve_effective_display,
        )

        prefs = load_record_indicator_prefs()
        style_name = str(prefs.get("display_style", "auto") or "auto")
        if style_name == "none":
            return None
        eff = resolve_effective_display(status, prefs)
        label = STATUS_LABELS.get(status, status)
        icon = _STATUS_ICONS.get(status, "●")
        color = get_status_color(status, prefs)
        icon_src = _status_icon_file_url(status)
        # Status column always labeled; icons used when PNG assets exist.
        badge_text = label if icon_src else f"{icon} {label}"
        return {
            "status": status,
            "label": label,
            "icon": icon,
            "icon_src": icon_src,
            "badge_text": badge_text,
            "color": color,
            "full_row": bool(eff.get("full_row")),
            "full_row_text": bool(eff.get("full_row_text")),
            "badge": True,
            "border": eff.get("border"),
            "display_style": style_name if style_name != "auto" else "auto",
        }
    except Exception:
        icon = _STATUS_ICONS.get(status, "●")
        return {
            "status": status,
            "label": status,
            "icon": icon,
            "badge_text": f"{icon} {status}",
            "color": "#9B0000",
            "full_row": False,
            "full_row_text": False,
            "badge": True,
            "display_style": "status_badge",
        }


def _status_cell(style: Optional[dict[str, Any]]) -> str:
    if not style:
        return ""
    return str(style.get("badge_text") or style.get("label") or "")


def _unfinished_sale_ids() -> set[int]:
    """Bills an autosave session still owns -- i.e. nobody ever finished them.

    Autosave writes a REAL bill from the first tick, so an abandoned form (a
    crash, a power cut, a tab nobody came back to) leaves a genuine charge on a
    genuine customer. It stays reclaimable for as long as it takes -- nothing
    here deletes a bill behind the operator's back -- but it must never look
    like a completed sale in history. This is the marker that stops it.
    """
    try:
        from core.autosave_session import open_sale_ids

        return open_sale_ids()
    except Exception as exc:
        print(f"[sales history] unfinished scan: {exc}")
        return set()


def _per_tab(value: Any, unit: Any) -> Any:
    """MRP/Tab or Rate/Tab when unit looks like pack of N."""
    try:
        v = float(value or 0)
    except Exception:
        return ""
    u = str(unit or "").strip().lower()
    import re

    m = re.search(r"(\d+)", u)
    if not m:
        return ""
    n = int(m.group(1))
    if n <= 1:
        return ""
    return round(v / n, 2)


def get_inventory_types(conn) -> list[str]:
    if not _table_exists(conn, "medicines"):
        return []
    cols = _table_cols(conn, "medicines")
    type_col = _pick_col(cols, "type", "medicine_type")
    if not type_col:
        return []
    try:
        hidden = " AND COALESCE(is_hidden,0)=0" if "is_hidden" in cols else ""
        rows = conn.execute(
            f"SELECT DISTINCT TRIM({type_col}) FROM medicines "
            f"WHERE TRIM(COALESCE({type_col},''))!=''{hidden} "
            f"ORDER BY 1 COLLATE NOCASE"
        ).fetchall()
        return [str(r[0]) for r in rows if r and r[0]]
    except Exception:
        return []


def get_schedules(conn) -> list[str]:
    """Full schedule list from Settings → Layout/List (includes blank)."""
    try:
        from core.layout_config import get_layout_schedules

        return list(get_layout_schedules())
    except Exception:
        return [""]


def schedule_filter_choices(_conn=None) -> list[str]:
    """Filter dropdowns: configured codes + Non-Scheduled (classic Tk)."""
    try:
        from core.layout_config import get_configured_schedules

        out = list(get_configured_schedules())
    except Exception:
        out = []
    if "Non-Scheduled" not in out:
        out.append("Non-Scheduled")
    return out


def _uniq_sorted_names(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        n = str(v or "").strip()
        if not n:
            continue
        key = n.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    out.sort(key=str.casefold)
    return out


def _distinct_item_medicine_names(conn, items_table: str) -> list[str]:
    if not _table_exists(conn, items_table):
        return []
    icols = _table_cols(conn, items_table)
    names: list[str] = []
    try:
        if _table_exists(conn, "medicines") and "medicine_id" in icols:
            rows = conn.execute(
                f"SELECT DISTINCT TRIM(m.name) FROM {items_table} i "
                "JOIN medicines m ON m.id=i.medicine_id "
                "WHERE TRIM(COALESCE(m.name,''))!='' "
                "LIMIT 800"
            ).fetchall()
            names.extend(r[0] for r in rows if r and r[0])
        name_col = next(
            (c for c in ("medicine_name", "name") if c in icols),
            None,
        )
        if name_col:
            rows = conn.execute(
                f"SELECT DISTINCT TRIM({name_col}) FROM {items_table} "
                f"WHERE TRIM(COALESCE({name_col},''))!='' LIMIT 800"
            ).fetchall()
            names.extend(r[0] for r in rows if r and r[0])
    except Exception:
        return names
    return names


def history_filter_choices(conn, page: str) -> dict[str, list[str]]:
    """Type-to-search lists for Sales/Purchase History filters."""
    medicines: list[str] = []
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import search_medicine_names

            medicines.extend(
                n.get("name") or ""
                for n in search_medicine_names("", limit=800, show_zero=True)
            )
        else:
            medicines.extend(
                m.get("name") or "" for m in search_medicines(conn, "", 500)
            )
    except Exception:
        pass
    try:
        if page == "sales":
            medicines.extend(_distinct_item_medicine_names(conn, "sales_items"))
        else:
            medicines.extend(_distinct_item_medicine_names(conn, "purchase_items"))
    except Exception:
        pass

    out: dict[str, list[str]] = {"medicines": _uniq_sorted_names(medicines)}
    if page == "sales":
        customers: list[str] = []
        try:
            from core.customer_service import get_customer_names

            customers = list(get_customer_names(conn) or [])
        except Exception:
            pass
        try:
            if _table_exists(conn, "sales"):
                scols = _table_cols(conn, "sales")
                if "customer_name" in scols:
                    rows = conn.execute(
                        "SELECT DISTINCT TRIM(customer_name) FROM sales "
                        "WHERE TRIM(COALESCE(customer_name,''))!='' LIMIT 800"
                    ).fetchall()
                    customers.extend(r[0] for r in rows if r and r[0])
                if _table_exists(conn, "customers") and "customer_id" in scols:
                    rows = conn.execute(
                        "SELECT DISTINCT TRIM(c.name) FROM sales s "
                        "JOIN customers c ON c.id=s.customer_id "
                        "WHERE TRIM(COALESCE(c.name,''))!='' LIMIT 800"
                    ).fetchall()
                    customers.extend(r[0] for r in rows if r and r[0])
        except Exception:
            pass
        out["customers"] = _uniq_sorted_names(customers)
        return out

    suppliers: list[str] = []
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import supplier_names

            suppliers = list(supplier_names() or [])
        elif _table_exists(conn, "suppliers") and "name" in _table_cols(
            conn, "suppliers"
        ):
            rows = conn.execute(
                "SELECT name FROM suppliers "
                "WHERE TRIM(COALESCE(name,''))!='' "
                "ORDER BY name COLLATE NOCASE LIMIT 800"
            ).fetchall()
            suppliers = [r[0] for r in rows]
    except Exception:
        pass
    try:
        if _table_exists(conn, "purchases"):
            pcols = _table_cols(conn, "purchases")
            if "supplier_name" in pcols:
                rows = conn.execute(
                    "SELECT DISTINCT TRIM(supplier_name) FROM purchases "
                    "WHERE TRIM(COALESCE(supplier_name,''))!='' LIMIT 800"
                ).fetchall()
                suppliers.extend(r[0] for r in rows if r and r[0])
            if _table_exists(conn, "suppliers") and "supplier_id" in pcols:
                rows = conn.execute(
                    "SELECT DISTINCT TRIM(s.name) FROM purchases p "
                    "JOIN suppliers s ON s.id=p.supplier_id "
                    "WHERE TRIM(COALESCE(s.name,''))!='' LIMIT 800"
                ).fetchall()
                suppliers.extend(r[0] for r in rows if r and r[0])
    except Exception:
        pass
    out["suppliers"] = _uniq_sorted_names(suppliers)
    return out


def search_medicines(conn, q: str = "", limit: int = 40) -> list[dict[str, Any]]:
    """Medicine suggestions for Sales/Purchase dropdowns."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import search_medicines_flat

            return search_medicines_flat(q, limit=limit)
    except Exception:
        pass
    if not _table_exists(conn, "medicines"):
        return []
    cols = _table_cols(conn, "medicines")
    if "name" not in cols:
        return []
    q = (q or "").strip()
    limit = max(1, min(int(limit or 40), 800))
    where = ["1=1"]
    params: list[Any] = []
    if "is_hidden" in cols:
        where.append("COALESCE(is_hidden,0)=0")
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    select = ["id", "name"]
    for c in (
        "batch_no",
        "stock_qty",
        "mrp",
        "rate",
        "expiry_date",
        "unit",
        "type",
        "schedule",
        "location",
        "gst_percent",
    ):
        if c in cols:
            select.append(c)
    # Names that START with what was typed come first, then names where a word
    # starts with it, then the rest -- alphabetical inside each group. Plain
    # alphabetical put AMOXY and BECOSULES above MECOVET when the shop typed
    # "m", which is never what they meant.
    from core.name_search_rank import order_by_sql

    order_sql, order_params = order_by_sql("name", q)
    sql = (
        f"SELECT {', '.join(select)} FROM medicines "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {order_sql} LIMIT ?"
    )
    params.extend(order_params)
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = {select[i]: r[i] for i in range(len(select))}
        out.append(
            {
                "id": d.get("id"),
                "name": d.get("name") or "",
                "batch": d.get("batch_no") or "",
                "stock": _safe_float(d.get("stock_qty")),
                "mrp": _safe_float(d.get("mrp")),
                "rate": _safe_float(d.get("rate")),
                "expiry": d.get("expiry_date") or "",
                "unit": d.get("unit") or "",
                "type": d.get("type") or "",
                "schedule": d.get("schedule") or "",
                "location": d.get("location") or "",
                "gst_percent": _safe_float(d.get("gst_percent")),
            }
        )
    return out


def list_inventory(
    conn,
    q: str = "",
    type_filter: str = "",
    stock_status: str = "",
    expiry_status: str = "",
    schedule: str = "",
    low_only: bool = False,
    sort: str = "",
    limit: int = 2000,
) -> dict[str, Any]:
    """Inventory rows matching Tk Inventory columns."""
    show_location = False
    try:
        row = conn.execute(
            "SELECT show_location FROM shelf_settings LIMIT 1"
        ).fetchone()
        show_location = bool(row and row[0])
    except Exception:
        show_location = False

    columns = [
        "Name",
        "Type",
        "Batch",
        "Expiry",
        "Days Left",
        "Stock",
        "Unit",
        "MRP",
        "MRP/Tab",
        "Rate",
        "Rate/Tab",
        "Manufacturer",
        "Supplier Name",
        "Schedule",
    ]
    if show_location:
        columns.append("Location")
    empty = {
        "columns": columns,
        "rows": [],
        "row_ids": [],
        "types": [],
        "schedules": schedule_filter_choices(),
        "sort_options": list(
            __import__("core.list_sort", fromlist=["INVENTORY_SORT_OPTIONS"]).INVENTORY_SORT_OPTIONS
        ),
        "summary": {
            "total_medicines": 0,
            "low_stock": 0,
            "out_of_stock": 0,
            "near_expiry": 0,
            "expired": 0,
            "total_value": 0,
        },
    }

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import store_query_client as sq
            from core.store_query_client import fetch_parallel
            from datetime import date as _date

            _read_failures: list = []

            def _safe(fn):
                return _online_read(fn, _read_failures)

            inv_limit = max(1, min(int(limit or 2000), 10000))
            # Once, outside the loop: the row-status helper would otherwise read
            # the settings table for every one of up to 10,000 rows.
            from core.alert_thresholds import (
                is_low_stock_qty as _is_low,
                is_near_expiry as _is_near,
                load_thresholds as _load_thr,
            )

            try:
                low_thr, near_thr = _load_thr(conn)
            except Exception as exc:
                print(f"[inventory] online thresholds: {exc}")
                low_thr, near_thr = {}, {}
            # Counted from the rows this page actually judged. The tiles used to
            # come straight from the server's own summary endpoint, which knows
            # nothing about the shop's thresholds -- so once the rows started
            # honouring them, a tile reading "Low Stock: 3" would have sat above
            # two hundred rows badged Low Stock.
            row_counts: dict[str, int] = {}
            data, rem = fetch_parallel(
                lambda: _safe(
                    lambda: sq.list_inventory(
                        q=q or "",
                        limit=inv_limit,
                        include_total=False,
                        # Narrowed by the SERVER, the way the Offline SQL does
                        # it. Filtering these in the loop below over one page
                        # meant a shop with more batches than the page held saw
                        # "no Syrups" while holding hundreds. The loop still
                        # applies them, which costs nothing and keeps the exact
                        # comparison Offline uses.
                        type_filter=type_filter or "",
                        schedule=schedule or "",
                    )
                ),
                lambda: _safe(sq.inventory_summary),
            )
            rem = rem if isinstance(rem, dict) else {}
            today = _date.today()
            src_rows = [
                r
                for r in ((data or {}).get("rows") or [])
                if isinstance(r, dict)
                and not r.get("deleted")
                and not r.get("is_hidden")
            ]
            # No medicine merge starts from here any more: it moved stock, deleted rows and
            # pushed whole bills again every 15 minutes with nobody choosing the row to keep.
            # Name-level stock totals: hide depleted batches when siblings still
            # have stock (import stubs / sold-out batches must not clutter list).
            name_totals: dict[str, float] = {}
            for r in src_rows:
                nm = str(r.get("name") or "").strip()
                if not nm:
                    continue
                name_totals[nm] = name_totals.get(nm, 0.0) + float(
                    r.get("stock_qty") or r.get("stock") or 0
                )
            # Depleted batches are hidden while a sibling batch still has stock,
            # so the list stays readable. A NEGATIVE row is a different thing: it
            # is an unsettled Add-No-Stock shortage, it keeps dragging the name
            # total down, and hiding it meant the medicine simply vanished from
            # Inventory while the shortage stayed on the books with nothing on
            # screen to explain it. Always show those.
            src_rows = [
                r
                for r in src_rows
                if float(r.get("stock_qty") or r.get("stock") or 0) != 0
                or name_totals.get(str(r.get("name") or "").strip(), 0.0) <= 0
            ]
            # Order the source list HERE, before src_ids is derived from it and
            # before the display rows are built, so rows, row_ids and row_styles
            # cannot drift apart. Online came back in the store server's own
            # order and nothing asked for another, so the shop's Sort did
            # nothing at all in the mode it actually runs in -- and the browser
            # then sorted the rows while the ids stayed put, which is how a
            # right-click opened a different medicine. Sorting below this line
            # would rebuild that same bug one level deeper.
            try:
                from core.list_sort import order_inventory_rows

                src_rows = order_inventory_rows(
                    src_rows,
                    sort,
                    name=lambda r: r.get("name"),
                    stock=lambda r: r.get("stock_qty") or r.get("stock") or 0,
                    expiry=lambda r: r.get("expiry_date") or r.get("expiry") or "",
                    query=q or "",
                )
            except Exception as exc:
                # The whole online block sits inside one except that falls
                # through to an empty :memory: database, so an unguarded raise
                # here would render a clean, error-free, EMPTY shop. The worst
                # this can do is leave the list unsorted.
                print(f"[inventory] online sort: {exc}")
            src_ids: list[int] = []
            for r in src_rows:
                try:
                    src_ids.append(
                        int(r.get("id") or r.get("local_id") or r.get("medicine_id") or 0)
                    )
                except (TypeError, ValueError):
                    src_ids.append(0)
            by_id, by_nb, by_name = {}, {}, {}
            try:
                by_id, by_nb, by_name = online_latest_supplier_maps()
            except Exception:
                try:
                    by_id = latest_supplier_by_medicine_id(conn, src_ids)
                except Exception:
                    by_id = {}
            rows: list[list[Any]] = []
            row_ids: list[int] = []
            row_styles: list = []
            types: set[str] = set()
            for r, mid in zip(src_rows, src_ids):
                name = str(r.get("name") or "")
                typ = str(r.get("type") or "")
                batch = str(r.get("batch_no") or r.get("batch") or "")
                expiry = str(r.get("expiry_date") or r.get("expiry") or "")[:10]
                stock = float(r.get("stock_qty") or r.get("stock") or 0)
                unit = str(r.get("unit") or "1")
                mrp = float(r.get("mrp") or 0)
                rate = float(r.get("rate") or 0)
                mfr = str(r.get("manufacturer") or "")
                sch = str(r.get("schedule") or "")
                loc = str(r.get("location") or "")
                if type_filter and typ.lower() != type_filter.lower():
                    continue
                if schedule:
                    if schedule == "Non-Scheduled":
                        if sch.strip():
                            continue
                    elif sch.lower() != schedule.lower():
                        continue
                days_left = ""
                try:
                    if expiry:
                        # Month-only expiries run to the month end, so count to the
                        # cutoff -- otherwise the days shown here disagree with whether
                        # the same row is treated as expired.
                        from core.batch_visibility import expiry_cutoff

                        _cut = expiry_cutoff(expiry) or _date.fromisoformat(expiry)
                        days_left = str((_cut - today).days)
                except Exception:
                    days_left = ""
                if stock_status:
                    ss = stock_status.lower()
                    if ss in ("in stock", "instock") and stock <= 0:
                        continue
                    if ss in ("out of stock", "oos") and stock > 0:
                        continue
                    # Was a hardcoded 0 < stock <= 10, so the shop's per-type
                    # minimums did nothing to this filter at all.
                    if ss in ("low stock", "low") and not _is_low(
                        stock, typ, low_thr, None, unit=unit or None
                    ):
                        continue
                if expiry_status:
                    es = expiry_status.lower()
                    try:
                        dleft = int(days_left) if str(days_left).lstrip("-").isdigit() else None
                    except Exception:
                        dleft = None
                    # Was a hardcoded 90 days, so "Near Expiry (months)" moved
                    # the Alert list and never this one.
                    if es in ("near expiry", "near") and not (
                        dleft is not None and _is_near(dleft, typ, near_thr)
                    ):
                        continue
                    if es == "expired" and not (dleft is not None and dleft < 0):
                        continue
                if low_only and not _is_low(stock, typ, low_thr, None, unit=unit or None):
                    continue
                if typ:
                    types.add(typ)
                sname = (
                    str(
                        r.get("supplier_name")
                        or r.get("last_supplier")
                        or r.get("supplier")
                        or ""
                    ).strip()
                    or by_id.get(mid, "")
                    or by_nb.get((_norm_med_name(name), _norm_batch(batch)), "")
                    or by_name.get(_norm_med_name(name), "")
                )
                row = [
                    name,
                    typ,
                    batch,
                    expiry,
                    days_left,
                    stock,
                    unit,
                    mrp,
                    mrp,  # MRP/Tab simplified
                    rate,
                    rate,
                    mfr,
                    sname,
                    sch,
                ]
                if show_location:
                    row.append(loc)
                rows.append(row)
                row_ids.append(mid)
                # Out of stock / Low stock / Expired / Near expiry, in the same
                # priority the classic Inventory uses. Online skipped this
                # entirely: the summary counted 27 out of stock and 4 expired,
                # but not one row was marked, so the shop could not see WHICH
                # medicines those were.
                _st = _inventory_row_status(
                    stock, expiry, name, typ, unit, conn, low_thr, near_thr
                )
                if _st:
                    row_counts[_st] = row_counts.get(_st, 0) + 1
                row_styles.append(_indicator_style(_st))
            out_columns = list(columns) + ["Status"]
            rows = [
                list(r) + [_status_cell(st)]
                for r, st in zip(rows, row_styles)
            ]
            out_columns, rows = _filter_by_column_visibility(
                "inventory",
                out_columns,
                rows,
                )
            return {
                "columns": out_columns,
                "rows": rows,
                "row_styles": row_styles,
                "row_ids": row_ids,
                "types": sorted(types),
                "schedules": schedule_filter_choices(),
                "sort_options": list(
                    __import__("core.list_sort", fromlist=["INVENTORY_SORT_OPTIONS"]).INVENTORY_SORT_OPTIONS
                ),
                "summary": {
                    "total_medicines": len(rows),
                    # These four are counted from the rows above, so the tile and
                    # the badges always agree. Only the money still comes from
                    # the server, which computes it strip-aware over the whole
                    # shelf rather than the page's window.
                    "low_stock": row_counts.get("low_stock", 0),
                    "out_of_stock": row_counts.get("out_of_stock", 0),
                    "near_expiry": row_counts.get("near_expiry", 0),
                    "expired": row_counts.get("expired", 0),
                    "total_value": float(rem.get("stock_value") or 0),
                },
                "server_error": _server_error_text(_read_failures),
            }
    except Exception as exc:
        print(f"[inventory] online list: {exc}")
        # _online_read catches the reads it wraps, but anything that raises
        # outside one landed here and then fell through to the SQL below --
        # which Online queries an empty :memory: database. The shop got a clean,
        # working, completely EMPTY Inventory and no reason for it: the same
        # "the store cleared itself" report that _online_read exists to prevent.
        # Say what broke instead of quietly showing a pharmacy with no stock.
        try:
            from core.sync_prefs import is_online_mode as _is_online

            if _is_online():
                out = dict(empty)
                out["server_error"] = _server_error_text(
                    [str(exc).strip() or exc.__class__.__name__]
                )
                return out
        except Exception:
            pass

    if not _table_exists(conn, "medicines"):
        return empty

    cols = _table_cols(conn, "medicines")
    if "name" not in cols:
        return empty

    where = ["1=1"]
    params: list[Any] = []
    if "is_hidden" in cols:
        where.append("COALESCE(is_hidden,0)=0")

    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        parts = ["name LIKE ?"]
        params.append(like)
        if "batch_no" in cols:
            parts.append("batch_no LIKE ?")
            params.append(like)
        if "manufacturer" in cols:
            parts.append("manufacturer LIKE ?")
            params.append(like)
        where.append("(" + " OR ".join(parts) + ")")

    type_filter = (type_filter or "").strip()
    if type_filter and "type" in cols:
        where.append("type=?")
        params.append(type_filter)

    schedule = (schedule or "").strip()
    if schedule and "schedule" in cols:
        if schedule == "Non-Scheduled":
            where.append("TRIM(COALESCE(schedule,''))=''")
        else:
            where.append("schedule=?")
            params.append(schedule)

    select = [
        "id",
        "name",
        "COALESCE(type,'')" if "type" in cols else "''",
        "COALESCE(batch_no,'')" if "batch_no" in cols else "''",
        "COALESCE(expiry_date,'')" if "expiry_date" in cols else "''",
        "COALESCE(stock_qty,0)" if "stock_qty" in cols else "0",
        "COALESCE(unit,'')" if "unit" in cols else "''",
        "COALESCE(mrp,0)" if "mrp" in cols else "0",
        "COALESCE(rate,0)" if "rate" in cols else "0",
        "COALESCE(manufacturer,'')" if "manufacturer" in cols else "''",
        "COALESCE(schedule,'')" if "schedule" in cols else "''",
        "COALESCE(location,'')" if "location" in cols else "''",
    ]
    limit = max(1, min(int(limit or 2000), 10000))
    # Names that START with what was typed come first, then names where a word
    # starts with it, then the rest -- alphabetical inside each group. Plain
    # alphabetical put AMOXY and BECOSULES above MECOVET when the shop typed
    # "m", which is never what they meant.
    from core.name_search_rank import order_by_sql

    order_sql, order_params = order_by_sql("name", q)
    sql = (
        f"SELECT {', '.join(select)} FROM medicines "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {order_sql} LIMIT ?"
    )
    params.extend(order_params)
    params.append(limit)

    try:
        raw = conn.execute(sql, params).fetchall()
    except Exception:
        return empty

    # An explicit choice from the Sort dropdown reorders what the query
    # returned; the empty default is left alone, because the ORDER BY above has
    # already put the best name matches first and re-sorting would throw that
    # away. Ordering the raw rows -- not the built ones -- keeps rows_out,
    # row_styles and row_ids in step, since all three are appended together in
    # the loop below. Tuple layout is fixed by the SELECT above: 1 name,
    # 4 expiry, 5 stock.
    if (sort or "").strip():
        from core.list_sort import order_inventory_rows

        raw = order_inventory_rows(
            list(raw),
            sort,
            name=lambda r: r[1],
            stock=lambda r: r[5],
            expiry=lambda r: r[4],
            query=q or "",
        )

    low_thr: dict[str, float] = {}
    near_thr: dict[str, float] = {}
    is_low_stock_qty = None
    is_near_expiry = None
    try:
        from core.alert_thresholds import (
            is_low_stock_qty as _is_low,
            is_near_expiry as _is_near,
            load_thresholds,
        )

        # That underscore was the whole bug on this side: the near-expiry map
        # was loaded and thrown away, so "Near Expiry (months)" moved the Alert
        # list and never the shelf.
        low_thr, near_thr = load_thresholds(conn)
        is_low_stock_qty = _is_low
        is_near_expiry = _is_near
    except Exception:
        pass

    stock_status = (stock_status or "").strip().lower()
    expiry_status = (expiry_status or "").strip().lower()
    if low_only and not stock_status:
        stock_status = "low stock"

    rows_out: list[list[Any]] = []
    row_styles: list[Optional[dict[str, Any]]] = []
    row_ids: list[int] = []
    sum_low = sum_out = sum_near = sum_exp = 0
    sum_value = 0.0
    sup_map = latest_supplier_by_medicine_id(
        conn, [r[0] for r in raw if r and r[0]]
    )

    for r in raw:
        mid, name, typ, batch, expiry, stock, unit, mrp, rate, mfg, sched, loc = r
        stock_f = _safe_float(stock)
        mrp_f = _safe_float(mrp)
        rate_f = _safe_float(rate)
        days = _days_left(expiry)
        is_expired = isinstance(days, int) and days < 0
        if is_near_expiry is not None:
            try:
                is_near = isinstance(days, int) and bool(
                    is_near_expiry(days, str(typ or ""), near_thr)
                )
            except Exception:
                is_near = isinstance(days, int) and 0 <= days <= 90
        else:
            is_near = isinstance(days, int) and 0 <= days <= 90
        is_out = stock_f <= 0
        is_low = False
        if is_low_stock_qty is not None:
            try:
                is_low = bool(
                    is_low_stock_qty(
                        stock_f, str(typ or ""), low_thr, conn.cursor(), unit=str(unit or "") or None
                    )
                )
            except Exception:
                is_low = 0 < stock_f < 10
        else:
            is_low = 0 < stock_f < 10

        if stock_status == "in stock" and is_out:
            continue
        if stock_status == "low stock" and not is_low:
            continue
        if stock_status == "out of stock" and not is_out:
            continue
        if expiry_status == "near expiry" and not is_near:
            continue
        if expiry_status == "expired" and not is_expired:
            continue

        if is_out:
            sum_out += 1
        if is_low:
            sum_low += 1
        if is_near:
            sum_near += 1
        if is_expired:
            sum_exp += 1
        # stock_f counts TABLETS for strip types while mrp_f is the price of a
        # whole strip, so multiplying them straight gave a figure inflated by the
        # strip size -- Inventory showed ten times what Home reported for the same
        # shop. stock_value_at_mrp divides the MRP down to one unit first, and is
        # the same helper the Home dashboard uses.
        from core.stock_utils import stock_value_at_mrp

        sum_value += stock_value_at_mrp(stock_f, mrp_f, typ, unit)

        # Status priority matches Tk inventory_status helpers
        status = None
        if is_out:
            status = "out_of_stock"
        elif is_low:
            status = "low_stock"
        elif is_expired:
            status = "expired"
        elif is_near:
            status = "near_expiry"

        row = [
            name,
            typ,
            batch,
            expiry,
            days,
            stock_f,
            unit,
            mrp_f,
            _per_tab(mrp_f, unit),
            rate_f,
            _per_tab(rate_f, unit),
            mfg,
            sup_map.get(int(mid or 0), ""),
            sched,
        ]
        if show_location:
            row.append(loc)
        rows_out.append(row)
        row_styles.append(_indicator_style(status))
        row_ids.append(int(mid or 0))

    # Always append Status column with icon + text for desktop lists.
    out_columns = list(columns) + ["Status"]
    for i, style in enumerate(row_styles):
        rows_out[i] = list(rows_out[i]) + [_status_cell(style)]

    out_columns, rows_out = _filter_by_column_visibility(
        "inventory",
        out_columns,
        rows_out,
    )

    return {
        "columns": out_columns,
        "rows": rows_out,
        "row_styles": row_styles,
        "row_ids": row_ids,
        "types": get_inventory_types(conn),
        "schedules": schedule_filter_choices(),
        "sort_options": empty["sort_options"],
        "summary": {
            "total_medicines": len(rows_out),
            "low_stock": sum_low,
            "out_of_stock": sum_out,
            "near_expiry": sum_near,
            "expired": sum_exp,
            "total_value": round(sum_value, 2),
        },
    }


def _party_phone(kind: str, party_id: Any, name: str = "") -> str:
    """Phone for a customer/supplier from the cached Online party list.

    The history screens fill this from a JOIN Offline and left it blank Online.
    online_catalog keeps both lists in memory, so this is a dict lookup, not a
    round trip -- id first, then the name the document carries, because a party
    created on another device may not be in this cache under the same id yet.
    """
    try:
        from core import online_catalog as cat

        finder_by_id = (
            cat.find_customer_by_id if kind == "customers" else cat.find_supplier_by_id
        )
        doc = finder_by_id(party_id) if party_id else None
        if not doc and str(name or "").strip():
            finder_by_name = (
                cat.find_customer_by_name
                if kind == "customers"
                else cat.find_supplier_by_name
            )
            doc = finder_by_name(str(name).strip())
        return str((doc or {}).get("phone") or "")
    except Exception:
        return ""


def _widened_note(fy_label: Any, existing: Any = "") -> str:
    """The line the screen shows when the financial-year default held nothing."""
    fy = str(fy_label or "").strip()
    msg = (f"No bills in FY {fy} — showing every date." if fy
           else "No bills in this financial year — showing every date.")
    tail = str(existing or "").strip()
    return f"{msg} {tail}".strip()


def _list_sales_history_range(
    conn,
    q: str = "",
    from_date: str = "",
    to_date: str = "",
    medicine: str = "",
    batch: str = "",
    customer: str = "",
    due: str = "",
    schedule: str = "",
    sort: str = "",
    limit: int | None = None,
    _scope_all: bool = False,
) -> dict[str, Any]:
    """Sales history — Tk filters + medicine/batch search for desktop.

    ``limit`` None lists every bill in the range up to SALES_HISTORY_ROW_CEILING; a range
    holding more says so in ``rows_note``.
    """
    from datetime import date as _date
    from core.history_prefs import (
        current_fy_label,
        load_history_scope,
        resolve_history_dates,
    )

    # history_filter_choices() serially fetches medicines(), customers() and the
    # bill-name set -- three server round trips that build not one row. It used to
    # run as this function's first statement, ahead of the row fetch. Resolve it
    # lazily instead: the online path below fetches it alongside the rows, and the
    # paths that still need it inline pay for it only when they are taken.
    _choices_memo: dict[str, dict] = {}

    def _choices() -> dict:
        if "v" not in _choices_memo:
            try:
                _choices_memo["v"] = history_filter_choices(conn, "sales")
            except Exception:
                _choices_memo["v"] = {}
        return _choices_memo["v"]

    columns = [
        "Bill No",
        "Date",
        "Customer",
        "Phone",
        "Doctor",
        "Total Amount",
        "Discount",
        "Amount Paid",
        "Cash Paid",
        "Online Paid",
        "Previous Due",
        "Due Amount",
        "Credit Amount",
        "Total Due",
    ]
    empty = {
        "columns": columns,
        "rows": [],
        "row_ids": [],
        "schedules": schedule_filter_choices(),
        "sort_options": list(
            __import__("core.list_sort", fromlist=["SORT_OPTIONS"]).SORT_OPTIONS
        ),
        "summary": {
            "bills": 0,
            "total": 0,
            "paid": 0,
            "due": 0,
            "discount": 0,
            "profit": 0,
            "returns": 0,
            "today_revenue": 0,
            "today_cash": 0,
            "today_online": 0,
            "month_revenue": 0,
            "customer_due": 0,
        },
        "history_scope": load_history_scope(),
        "fy_label": current_fy_label(),
        "filter_from": "",
        "filter_to": "",
        "filter_choices": _choices(),
    }

    if _scope_all:
        # the widening retry below: every date, whatever the saved scope says
        fd, td, applied_default = "", "", False
    else:
        fd, td, applied_default = resolve_history_dates(from_date, to_date)

    # Online: store-scoped server history (same FY window as Offline).
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import store_query_client as sq
            from core.fy_serial import display_sales_bill_no

            # Two filters, one server parameter.
            #
            # This used to be `" ".join((q, customer))` — the search box and the
            # customer filter glued into one string and sent as the single `q=`.
            # The server turns `q` into ONE wildcard tested against bill_no OR
            # customer_name OR doctor_name, so "SCB12 RAHUL" matched no row on
            # earth and the list came back empty while both bills sat right
            # there. Send one of them to the server and let the customer filter
            # below (`if customer and customer.lower() not in name.lower()`) do
            # the other, which is what it was always there for.
            server_q = _server_search_term(q, customer)
            from core.store_query_client import fetch_parallel

            row_cap = _sales_history_row_cap(limit)

            _read_failures: list = []

            def _safe(fn):
                return _online_read(fn, _read_failures)

            data, pays_raw, rets_raw, choices_raw = fetch_parallel(
                lambda: _safe(
                    lambda: _all_sales_history_rows(
                        sq,
                        row_cap,
                        from_date=fd or "",
                        to_date=td or "",
                        q=server_q,
                        schedule=(schedule or "").strip(),
                        medicine=(medicine or "").strip(),
                        batch=(batch or "").strip(),
                    )
                ),
                lambda: _safe(
                    lambda: sq.list_customer_payments(
                        limit=2000, from_date=fd or "", to_date=td or ""
                    )
                ),
                lambda: _safe(
                    lambda: sq.list_sales_returns(
                        limit=2000, from_date=fd or "", to_date=td or ""
                    )
                ),
                # Rides along with the rows instead of blocking ahead of them.
                lambda: _safe(lambda: history_filter_choices(conn, "sales")),
            )
            _choices_memo["v"] = choices_raw if isinstance(choices_raw, dict) else {}
            from core.due_fifo import fifo_sales_remaining_by_bill, sale_entry_paid
            from core.online_mutation_queue import (
                overlay_sales_dicts,
                overlay_customer_payment_dicts,
                overlay_sales_return_dicts,
                merge_server_rows,
            )

            merged_sales = merge_server_rows(
                list((data or {}).get("rows") or []),
                overlay_sales_dicts(),
                collection="sales",
            )
            pays: list[dict[str, Any]] = []
            try:
                pays = list((pays_raw or {}).get("rows") or [])
            except Exception:
                pays = []
            pays = merge_server_rows(
                pays,
                overlay_customer_payment_dicts(),
                collection="customer_payments",
            )
            rets: list[dict[str, Any]] = []
            try:
                rets = list((rets_raw or {}).get("rows") or [])
            except Exception:
                rets = []
            rets = merge_server_rows(
                rets,
                overlay_sales_return_dicts(),
                collection="sales_returns",
            )
            # The client FIFO pools payments oldest-bill-first, but this bill list
            # is date-windowed and capped -- so a payment made outside the window
            # got allocated against the wrong set and a fully paid bill kept
            # showing "Due". The server runs the same cascade over the WHOLE
            # ledger (partyDueCascade) and stores the answer on each row, so trust
            # that unless this device is holding payments or returns it has not
            # pushed yet; only then does the local overlay need re-allocating.
            _pending_overlay = bool(
                overlay_customer_payment_dicts() or overlay_sales_return_dicts()
            )
            fifo_sales = (
                fifo_sales_remaining_by_bill(merged_sales, pays, rets)
                if _pending_overlay
                else {}
            )
            fd = str((data or {}).get("filter_from") or fd or "")
            td = str((data or {}).get("filter_to") or td or "")
            applied_default = bool((data or {}).get("default_fy_applied") or applied_default)
            rows: list[list[Any]] = []
            row_styles: list[Optional[dict[str, Any]]] = []
            row_ids: list[int] = []
            fifo_due_total = 0.0
            fifo_paid_total = 0.0
            fifo_sales_total = 0.0
            fifo_disc_total = 0.0
            # Online reads come back in the server's own order, and nothing was
            # asking it for another one -- so the Sort dropdown did nothing at
            # all in Online mode, which is the mode the shops run in. Order the
            # source list here, BEFORE the display rows are built, so rows,
            # row_styles and row_ids stay lined up with each other.
            from core.list_sort import order_server_rows

            unfinished_ids = _unfinished_sale_ids()
            merged_sales = order_server_rows(
                merged_sales, sort, no_key="bill_no", date_key="bill_date",
                name_key="customer_name",
            )
            merged_sales = _search_history_dicts(merged_sales, q)
            for r in merged_sales:
                if not isinstance(r, dict):
                    continue
                name = str(r.get("customer_name") or "")
                if customer and customer.lower() not in name.lower():
                    continue
                try:
                    sid = int(r.get("id") or r.get("local_id") or 0)
                except (TypeError, ValueError):
                    sid = 0
                paid = sale_entry_paid(r)
                credit = _safe_float(r.get("credit_amount"))
                fifo_hit = fifo_sales.get(sid)
                if fifo_hit is not None:
                    due_amt, acct = fifo_hit
                else:
                    due_amt = _safe_float(r.get("due_amount"))
                    acct = 1 if r.get("account_cleared") else 0
                total_due = due_amt
                # sale_entry_paid() is only what was collected AT the counter, so a
                # bill later settled from the Payments screen showed "Cleared / Paid"
                # next to "Amount Paid 0.00" -- which reads exactly like the payment
                # never applied. Android already derives this (it labels the column
                # "PAID VIA PAYMENT"); derive it here too so the two agree.
                _total_for_paid = _safe_float(r.get("total_amount"))
                _settled = round(_total_for_paid - due_amt, 2)
                if _settled > paid + 0.01:
                    paid = _settled
                due_f = (due or "").strip().lower()
                if due_f in ("due", "due only") and not (total_due > 0.01 and not acct):
                    continue
                if due_f in ("credit", "credit only") and credit <= 0:
                    continue
                if due_f in ("paid", "cleared", "paid / cleared", "paid/cleared") and (
                    due_amt > 0.01 or not acct
                ):
                    continue
                if due_amt > 0.01:
                    status = "partial" if paid > 0.01 else "due"
                elif credit > 0:
                    status = "credit"
                else:
                    status = "cleared"
                if sid in unfinished_ids:
                    # An autosaved bill whose form was never finished. Paid or
                    # not, it is not a completed sale, and the shop has to be
                    # able to see that here.
                    status = "unfinished"
                style = _indicator_style(status)
                bno = display_sales_bill_no(str(r.get("bill_no") or ""))
                total_amt = _safe_float(r.get("total_amount"))
                disc_amt = _safe_float(r.get("discount"))
                fifo_due_total += due_amt
                fifo_paid_total += paid
                fifo_sales_total += total_amt
                fifo_disc_total += disc_amt
                rows.append(
                    [
                        bno,
                        r.get("bill_date") or "",
                        name,
                        # The Phone column was a hardcoded blank Online. It is
                        # the column a shop uses to ring a customer about an
                        # unpaid bill, and Offline it has always been filled --
                        # so the same screen was useful in one mode and not the
                        # other. The sale carries only the customer's name and
                        # id; the phone comes off the cached customer list, so
                        # this costs no extra call.
                        _party_phone("customers", r.get("customer_id"), name),
                        r.get("doctor_name") or "",
                        total_amt,
                        disc_amt,
                        paid,
                        _safe_float(r.get("cash_paid")),
                        _safe_float(r.get("online_paid")),
                        _safe_float(r.get("previous_due")),
                        due_amt,
                        credit,
                        total_due,
                        _status_cell(style),
                    ]
                )
                row_styles.append(style)
                row_ids.append(sid)
            out_columns = list(columns) + ["Status"]
            out_columns, rows = _filter_by_column_visibility(
                "sales_history", out_columns, rows
            )
            rem = {}
            try:
                # Asked in parts once the list holds more bills than one request can name.
                rem = _sales_summary_over(
                    sq,
                    row_ids,
                    from_date=fd or "",
                    to_date=td or "",
                    q=server_q,
                ) or {}
            except Exception as sexc:
                print(f"[sales history] online summary: {sexc}")
            rows_note = _rows_note(
                len((data or {}).get("rows") or []), (data or {}).get("rows_total")
            )
            due_live = round(fifo_due_total, 2)
            summary = {
                "bills": len(rows),
                "total": round(float(rem.get("total_sales") or fifo_sales_total or 0), 2),
                "paid": round(fifo_paid_total, 2),
                "due": due_live,
                "discount": round(float(rem.get("total_discount") or fifo_disc_total or 0), 2),
                "profit": round(float(rem.get("total_profit") or 0), 2),
                "returns": round(float(rem.get("total_returns") or 0), 2),
                "today_revenue": round(float(rem.get("today_revenue") or 0), 2),
                "today_cash": round(float(rem.get("today_cash") or 0), 2),
                "today_online": round(float(rem.get("today_online") or 0), 2),
                "month_revenue": round(float(rem.get("month_revenue") or 0), 2),
                "customer_due": due_live,
            }
            online_out = {
                "columns": out_columns,
                "rows": rows,
                "row_styles": row_styles,
                "row_ids": row_ids,
                "schedules": schedule_filter_choices(),
                "sort_options": empty["sort_options"],
                "summary": summary,
                "history_scope": load_history_scope(),
                "fy_label": current_fy_label(),
                "filter_from": fd,
                "filter_to": td,
                "default_fy_applied": applied_default,
                "filter_choices": _choices(),
                "server_error": _server_error_text(_read_failures),
            }
            if rows_note:
                online_out["rows_note"] = rows_note
            return online_out
    except Exception as exc:
        print(f"[sales history] online fetch: {exc}")

    if not _table_exists(conn, "sales"):
        return empty

    scols = _table_cols(conn, "sales")
    where = ["COALESCE(s.deleted,0)=0"]
    if "is_autosave" in scols:
        where.append("COALESCE(s.is_autosave,0)=0")
    params: list[Any] = []
    if fd:
        where.append("s.bill_date>=?")
        params.append(fd)
    if td:
        where.append("s.bill_date<=?")
        params.append(td)

    q = (q or "").strip()
    # The bill number the shop sees: "SCB2", not the stored "SCB2/FY2026-27" whose year
    # put a "2" in every bill (see history_search_rank).
    shown_no = ("CASE WHEN instr(COALESCE(s.bill_no,''),'/FY')>0 "
                "THEN substr(s.bill_no,1,instr(s.bill_no,'/FY')-1) ELSE COALESCE(s.bill_no,'') END")
    if q:
        like = f"%{q}%"
        where.append(
            f"({shown_no} LIKE ? OR c.name LIKE ? OR c.phone LIKE ? OR s.doctor_name LIKE ?)"
        )
        params.extend([like, like, like, like])

    customer = (customer or "").strip()
    if customer:
        where.append("c.name LIKE ?")
        params.append(f"%{customer}%")

    due_f = (due or "").strip().lower()
    if due_f in ("due", "due only"):
        where.append("COALESCE(s.total_due,0)>0 AND COALESCE(s.account_cleared,0)=0")
    elif due_f in ("credit", "credit only"):
        where.append("COALESCE(s.credit_amount,0)>0")
    elif due_f in ("paid", "cleared", "paid / cleared", "paid/cleared"):
        where.append("COALESCE(s.account_cleared,0)=1")

    medicine = (medicine or "").strip()
    batch = (batch or "").strip()
    schedule = (schedule or "").strip()
    if medicine or batch or schedule:
        # Three separate EXISTS, not one, and Non-Scheduled means EVERY line.
        #
        # This is the purchase-side bug (#6) on the sales side, and this filter
        # is the one behind Print All -- so it decides which bills get printed
        # in a batch, not just which are listed.
        #
        #   * Folding medicine, batch and schedule into ONE EXISTS asked for a
        #     single line satisfying all three at once. The store server asks
        #     three independent questions, so "medicine X" + "batch B" meant
        #     different bills offline and online on the same data.
        #   * "Non-Scheduled" as EXISTS(schedule='') is "this bill has at least
        #     one unscheduled item", which is nearly every bill a shop ever
        #     wrote. The server asks "are they ALL unscheduled". On the purchase
        #     side the same mistake returned 247 bills out of 367 where the
        #     server returned 61. The server's rule is the one kept.
        #
        # One divergence stays and cannot be closed here: local sales_items
        # stores only medicine_id, so the schedule, name and batch come from
        # today's medicines master, while the server reads what was recorded on
        # the line that day. Closing that needs columns on sales_items.
        #
        # The server now resolves the SAME WAY ROUND for the schedule --
        # COALESCE(line, medicines master) -- so the two branches agree on
        # every line whose own value is blank, which is every sale line the
        # Online desktop wrote before save_new_sale_online started sending one.
        # That fallback is what lets an H1 register print from history the shop
        # cannot re-enter. Where a line DOES carry its own value the server
        # prefers it, on purpose: it is what was dispensed that day.
        sched_expr = "TRIM(COALESCE(m.schedule,''))"
        if schedule:
            if schedule == "Non-Scheduled":
                where.append(
                    "(EXISTS (SELECT 1 FROM sales_items si "
                    "LEFT JOIN medicines m ON m.id=si.medicine_id "
                    f"WHERE si.sale_id=s.id AND {sched_expr}='') "
                    "AND NOT EXISTS (SELECT 1 FROM sales_items si "
                    "LEFT JOIN medicines m ON m.id=si.medicine_id "
                    f"WHERE si.sale_id=s.id AND {sched_expr}<>''))"
                )
            else:
                where.append(
                    "EXISTS (SELECT 1 FROM sales_items si "
                    "LEFT JOIN medicines m ON m.id=si.medicine_id "
                    f"WHERE si.sale_id=s.id AND {sched_expr}=?)"
                )
                params.append(schedule)
        if medicine:
            where.append(
                "EXISTS (SELECT 1 FROM sales_items si "
                "LEFT JOIN medicines m ON m.id=si.medicine_id "
                "WHERE si.sale_id=s.id AND COALESCE(m.name,'') LIKE ?)"
            )
            params.append(f"%{medicine}%")
        if batch:
            where.append(
                "EXISTS (SELECT 1 FROM sales_items si "
                "LEFT JOIN medicines m ON m.id=si.medicine_id "
                "WHERE si.sale_id=s.id AND COALESCE(m.batch_no,'') LIKE ?)"
            )
            params.append(f"%{batch}%")

    row_cap = _sales_history_row_cap(limit)
    sql = f"""
        SELECT s.id, s.bill_no, s.bill_date,
               COALESCE(c.name,''), COALESCE(c.phone,''),
               COALESCE(s.doctor_name,''),
               COALESCE(s.total_amount,0), COALESCE(s.discount,0),
               COALESCE(s.amount_paid,0), COALESCE(s.cash_paid,0),
               COALESCE(s.online_paid,0), COALESCE(s.previous_due,0),
               COALESCE(s.due_amount,0), COALESCE(s.credit_amount,0),
               COALESCE(s.total_due,0)
        FROM sales s
        LEFT JOIN customers c ON s.customer_id=c.id
        WHERE {' AND '.join(where)}
        ORDER BY ({shown_no} = ? COLLATE NOCASE OR COALESCE(s.fy_serial, -1) = ?) DESC,
                 s.bill_date DESC, COALESCE(s.fy_serial, s.id) DESC, s.id DESC
        LIMIT ?
    """
    count_params = list(params)
    params.append(q)            # the bill typed is never cut off by the row cap
    params.append(int(q) if q.isdigit() else -2)
    # One row past the cap says whether the range holds more than the list will show.
    params.append(row_cap + 1)
    rows_total = None
    try:
        raw = conn.execute(sql, params).fetchall()
        if len(raw) > row_cap:
            raw = raw[:row_cap]
            rows_total = conn.execute(
                "SELECT COUNT(*) FROM sales s LEFT JOIN customers c ON s.customer_id=c.id "
                f"WHERE {' AND '.join(where)}",
                count_params,
            ).fetchone()[0]
    except Exception:
        return empty

    # Sort (client-style options from Tk)
    #
    # The bill-number order is the one a shop files by, and it has to compare
    # the digits: 2, 9, 98, 100 -- not the text order 100, 2, 9, 98.
    from core.list_sort import bill_sort_key

    sort_key = (sort or "").strip()
    rows_raw = list(raw)
    if sort_key.startswith("Bill No"):
        rows_raw.sort(
            key=lambda r: bill_sort_key(r[1] if len(r) > 1 else ""),
            reverse="high to low" in sort_key,
        )
    elif sort_key.startswith("Oldest"):
        rows_raw.sort(
            key=lambda r: (str(r[2] or ""), bill_sort_key(r[1] if len(r) > 1 else ""))
        )
    elif "Z-A" in sort_key:
        rows_raw.sort(key=lambda r: str(r[3] or "").lower(), reverse=True)
    elif "A-Z" in sort_key:
        rows_raw.sort(key=lambda r: str(r[3] or "").lower())
    if q:
        # the bill whose number was typed comes first, whatever the sort (stable)
        rows_raw.sort(key=lambda r: -history_search_rank(q, r[1], r[3], r[4], r[5]))

    rows: list[list[Any]] = []
    row_styles: list[Optional[dict[str, Any]]] = []
    row_ids: list[int] = []
    sale_ids: list[int] = []
    unfinished_ids = _unfinished_sale_ids()
    for r in rows_raw:
        sid = int(r[0] or 0)
        due_amt = _safe_float(r[12])
        paid_amt = _safe_float(r[8])
        credit_amt = _safe_float(r[13])
        if due_amt > 0:
            status = "partial" if paid_amt > 0 else "due"
        elif credit_amt > 0:
            status = "credit"
        else:
            status = "cleared"
        if sid in unfinished_ids:
            status = "unfinished"
        style = _indicator_style(status)
        from core.fy_serial import display_sales_bill_no

        shown = list(r[1:])
        shown[0] = display_sales_bill_no(str(shown[0] or ""))
        rows.append(shown + [_status_cell(style)])
        row_styles.append(style)
        row_ids.append(sid)
        sale_ids.append(sid)

    out_columns = list(columns) + ["Status"]

    # Richer summary (same ideas as Tk sales_history.update_summary)
    today_s = _date.today().isoformat()
    month_prefix = today_s[:7]
    total_sales = round(sum(_safe_float(r[5]) for r in rows), 2)
    total_paid_bills = round(sum(_safe_float(r[7]) for r in rows), 2)
    total_disc = round(sum(_safe_float(r[6]) for r in rows), 2)
    total_due_bills = round(sum(_safe_float(r[11]) for r in rows), 2)
    today_rows = [r for r in rows if str(r[1] or "")[:10] == today_s]
    month_rows = [r for r in rows if str(r[1] or "")[:7] == month_prefix]

    customer_due = 0.0
    try:
        customer_due = _safe_float(
            conn.execute(
                "SELECT COALESCE(SUM(total_due),0) FROM customers WHERE total_due>0"
            ).fetchone()[0]
        )
    except Exception:
        pass

    payments_extra = 0.0
    try:
        pay_where = ["COALESCE(deleted,0)=0"]
        pay_params: list[Any] = []
        if fd:
            pay_where.append("payment_date>=?")
            pay_params.append(fd)
        if td:
            pay_where.append("payment_date<=?")
            pay_params.append(td)
        if _table_exists(conn, "customer_payments"):
            payments_extra = _safe_float(
                conn.execute(
                    f"SELECT COALESCE(SUM(amount),0) FROM customer_payments "
                    f"WHERE {' AND '.join(pay_where)}",
                    pay_params,
                ).fetchone()[0]
            )
    except Exception:
        pass

    item_disc = 0.0
    total_returns = 0.0
    total_profit = 0.0
    # In parts: an older SQLite (the Windows 7 build) allows 999 parameters per statement,
    # and the list now holds every bill in the range rather than 500.
    id_parts = [sale_ids[i : i + 900] for i in range(0, len(sale_ids), 900)]
    for part in id_parts:
        placeholders = ",".join("?" * len(part))
        try:
            item_disc += _safe_float(
                conn.execute(
                    f"SELECT COALESCE(SUM(item_discount),0) FROM sales_items "
                    f"WHERE sale_id IN ({placeholders})",
                    part,
                ).fetchone()[0]
            )
        except Exception:
            pass
        try:
            if _table_exists(conn, "sales_returns"):
                total_returns += _safe_float(
                    conn.execute(
                        f"SELECT COALESCE(SUM(refund_amount),0) FROM sales_returns "
                        f"WHERE COALESCE(deleted,0)=0 "
                        f"AND sale_id IN ({placeholders})",
                        part,
                    ).fetchone()[0]
                )
        except Exception:
            pass
    if sale_ids:
        try:
            from core.stock_utils import sale_line_profit

            profit_sum = 0.0
            for part in id_parts:
                placeholders = ",".join("?" * len(part))
                profit_rows = conn.execute(
                    f"""
                    SELECT si.amount, si.qty, si.cost_price,
                           m.type, COALESCE(m.unit,'1'), COALESCE(m.rate,0)
                    FROM sales_items si
                    JOIN medicines m ON si.medicine_id=m.id
                    WHERE si.sale_id IN ({placeholders})
                    """,
                    part,
                ).fetchall()
                profit_sum += sum(
                    sale_line_profit(row[0], row[1], row[2], row[5], row[3], row[4])
                    for row in profit_rows
                )
            total_profit = round(profit_sum, 2)
        except Exception:
            total_profit = 0.0

    total_disc = round(total_disc + item_disc, 2)
    summary = {
        "bills": len(rows),
        "total": total_sales,
        "paid": round(total_paid_bills + payments_extra, 2),
        "due": total_due_bills,
        "discount": total_disc,
        "profit": total_profit,
        "returns": total_returns,
        "today_revenue": round(sum(_safe_float(r[5]) for r in today_rows), 2),
        "today_cash": round(sum(_safe_float(r[8]) for r in today_rows), 2),
        "today_online": round(sum(_safe_float(r[9]) for r in today_rows), 2),
        "month_revenue": round(sum(_safe_float(r[5]) for r in month_rows), 2),
        "customer_due": round(customer_due, 2),
    }
    out_columns, rows = _filter_by_column_visibility(
        "sales_history", out_columns, rows
    )
    offline_out = {
        "columns": out_columns,
        "rows": rows,
        "row_styles": row_styles,
        "row_ids": row_ids,
        "schedules": schedule_filter_choices(),
        "sort_options": list(
            __import__("core.list_sort", fromlist=["SORT_OPTIONS"]).SORT_OPTIONS
        ),
        "summary": summary,
        "history_scope": load_history_scope(),
        "fy_label": current_fy_label(),
        "filter_from": fd,
        "filter_to": td,
        "default_fy_applied": applied_default,
        "filter_choices": _choices(),
    }
    note = _rows_note(len(raw), rows_total)
    if note:
        offline_out["rows_note"] = note
    return offline_out


def list_sales_history(
    conn,
    q: str = "",
    from_date: str = "",
    to_date: str = "",
    medicine: str = "",
    batch: str = "",
    customer: str = "",
    due: str = "",
    schedule: str = "",
    sort: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    """Sales history, with the owner's rule of 2026-09-16 on top of the range.

    The screen defaults to the current financial year. A shop whose bills all
    predate 1 April therefore opened on an EMPTY list and its data looked lost
    -- exactly what happened to Matoshree, whose imported bills end 31 Mar 2026
    while every other screen (purchases, inventory, customers) showed its rows.

    So: if the default window was applied by us (the user asked for no dates)
    and it holds no bill at all, the same query runs again over every date. A
    range the USER typed is never widened, and a window that holds even one
    bill is left alone.
    """
    out = _list_sales_history_range(
        conn, q=q, from_date=from_date, to_date=to_date, medicine=medicine,
        batch=batch, customer=customer, due=due, schedule=schedule, sort=sort,
        limit=limit,
    )
    if out.get("rows") or not out.get("default_fy_applied"):
        return out
    wider = _list_sales_history_range(
        conn, q=q, from_date="", to_date="", medicine=medicine,
        batch=batch, customer=customer, due=due, schedule=schedule, sort=sort,
        limit=limit, _scope_all=True,
    )
    if not wider.get("rows"):
        return out
    wider["history_scope_widened"] = True
    wider["rows_note"] = _widened_note(out.get("fy_label"), wider.get("rows_note"))
    return wider


def _list_purchase_history_range(
    conn,
    q: str = "",
    from_date: str = "",
    to_date: str = "",
    supplier: str = "",
    due: str = "",
    schedule: str = "",
    medicine: str = "",
    batch: str = "",
    sort: str = "",
    limit: int = 500,
    _scope_all: bool = False,
) -> dict[str, Any]:
    """Purchase history — Tk filters + optional medicine/batch search."""
    from core.history_prefs import (
        current_fy_label,
        load_history_scope,
        resolve_history_dates,
    )

    # Same lazy treatment as sales history: this prologue was two serial server
    # fetches ahead of the rows, building nothing the user sees first.
    _choices_memo: dict[str, dict] = {}

    def _choices() -> dict:
        if "v" not in _choices_memo:
            try:
                _choices_memo["v"] = history_filter_choices(conn, "purchase")
            except Exception:
                _choices_memo["v"] = {}
        return _choices_memo["v"]

    columns = [
        "Purchase No",
        "Bill No",
        "Date",
        "Supplier",
        "Phone",
        "Final Amount",
        "Paid at Entry",
        "Cash Paid",
        "Online Paid",
        "Entry Due",
        "Status",
    ]
    empty = {
        "columns": columns,
        "rows": [],
        "row_ids": [],
        "schedules": schedule_filter_choices(),
        "sort_options": list(
            __import__("core.list_sort", fromlist=["SORT_OPTIONS"]).SORT_OPTIONS
        ),
        "summary": {
            "bills": 0,
            "total": 0,
            "paid": 0,
            "due": 0,
            "supplier_due": 0,
            "supplier_credit": 0,
        },
        "history_scope": load_history_scope(),
        "fy_label": current_fy_label(),
        "filter_from": "",
        "filter_to": "",
        "filter_choices": _choices(),
    }

    if _scope_all:
        # the widening retry below: every date, whatever the saved scope says
        fd, td, applied_default = "", "", False
    else:
        fd, td, applied_default = resolve_history_dates(from_date, to_date)

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import store_query_client as sq
            from core.fy_serial import display_purchase_no

            # Same defect on the purchase side: the search box and the supplier
            # filter were glued into one string and sent as the single `q=`,
            # which the server tests against purchase_no OR supplier_name OR
            # bill_number. Send one; the supplier filter below narrows the rest.
            server_q = _server_search_term(q, supplier)
            from core.store_query_client import fetch_parallel

            hist_limit = max(1, min(int(limit or 500), 5000))

            _read_failures: list = []

            def _safe(fn):
                return _online_read(fn, _read_failures)

            data, pays_raw, choices_raw = fetch_parallel(
                lambda: _safe(
                    lambda: sq.list_purchases(
                        from_date=fd or "",
                        to_date=td or "",
                        q=server_q,
                        schedule=(schedule or "").strip(),
                        medicine=(medicine or "").strip(),
                        batch=(batch or "").strip(),
                        limit=hist_limit,
                        include_total=False,
                    )
                ),
                lambda: _safe(
                    lambda: sq.list_supplier_payments(
                        limit=2000, from_date=fd or "", to_date=td or ""
                    )
                ),
                # Rides along with the rows instead of blocking ahead of them.
                lambda: _safe(lambda: history_filter_choices(conn, "purchase")),
            )
            _choices_memo["v"] = choices_raw if isinstance(choices_raw, dict) else {}
            from core.due_fifo import (
                fifo_paid_via_by_bill,
                purchase_entry_paid,
                purchase_remaining_and_via,
            )
            from core.online_mutation_queue import (
                overlay_purchase_dicts,
                overlay_supplier_payment_dicts,
                merge_server_rows,
            )

            merged_purchases = merge_server_rows(
                list((data or {}).get("rows") or []),
                overlay_purchase_dicts(),
                collection="purchases",
            )
            pays: list[dict[str, Any]] = []
            try:
                pays = list((pays_raw or {}).get("rows") or [])
            except Exception:
                pays = []
            pays = merge_server_rows(
                pays,
                overlay_supplier_payment_dicts(),
                collection="supplier_payments",
            )
            # Same reasoning as sales history: this bill list is date-windowed and
            # capped, so pooling supplier payments over it allocated them against
            # the wrong bills and a settled purchase kept reading "Due". The server
            # cascades over the whole ledger and stores due_amount / bill_cleared
            # on each row, so only re-allocate locally when this device is holding
            # supplier payments it has not pushed yet.
            _pending_overlay = bool(overlay_supplier_payment_dicts())
            fifo_via = (
                fifo_paid_via_by_bill(merged_purchases, pays)
                if _pending_overlay
                else {}
            )
            fd = str((data or {}).get("filter_from") or fd or "")
            td = str((data or {}).get("filter_to") or td or "")
            applied_default = bool((data or {}).get("default_fy_applied") or applied_default)
            rows: list[list[Any]] = []
            row_styles: list[Optional[dict[str, Any]]] = []
            row_ids: list[int] = []
            fifo_due_total = 0.0
            fifo_paid_total = 0.0
            fifo_final_total = 0.0
            # Same as sales: order the source list before the display rows are
            # built, so the three parallel lists cannot drift apart.
            from core.list_sort import order_server_rows

            merged_purchases = order_server_rows(
                merged_purchases, sort, no_key="purchase_no", date_key="purchase_date",
                name_key="supplier_name",
            )
            for r in merged_purchases:
                if not isinstance(r, dict):
                    continue
                # A purchase still waiting in this PC's outbound queue is
                # prepended to the server's rows, and the server never saw it --
                # so it was never schedule-checked and turned up under EVERY
                # schedule. Better invisible for the duration of a filtered view
                # than wrong in all of them; it comes back the moment it syncs,
                # or when the filter is cleared.
                if schedule and r.get("pending"):
                    continue
                name = str(r.get("supplier_name") or "")
                if supplier and supplier.lower() not in name.lower():
                    continue
                try:
                    pid = int(r.get("id") or r.get("local_id") or 0)
                except (TypeError, ValueError):
                    pid = 0
                final_amt = _safe_float(r.get("final_amount") or r.get("total_amount"))
                entry_paid = purchase_entry_paid(r)
                # Pass None (not a fabricated 0) when this bill is outside the
                # pool, so purchase_remaining_and_via falls back to the server's
                # own cascaded due_amount / bill_cleared instead of assuming
                # nothing was ever paid against it.
                remaining, _via = purchase_remaining_and_via(
                    r, fifo_via.get(pid) if pid else None
                )
                due_amt = remaining
                due_f = (due or "").strip().lower()
                if due_f in ("due", "due only", "unpaid") and due_amt <= 0.01:
                    continue
                if due_f in ("paid", "cleared", "paid / cleared", "paid/cleared") and due_amt > 0.01:
                    continue
                if due_amt > 0.01:
                    status = "partial" if entry_paid > 0.01 or _via > 0.01 else "due"
                else:
                    status = "cleared"
                style = _indicator_style(status)
                pno = display_purchase_no(str(r.get("purchase_no") or ""))
                fifo_due_total += due_amt
                fifo_paid_total += entry_paid
                fifo_final_total += final_amt
                rows.append(
                    [
                        pno,
                        r.get("bill_number") or pno,
                        r.get("purchase_date") or "",
                        name,
                        # Same blank Phone column as Sales history had.
                        _party_phone("suppliers", r.get("supplier_id"), name),
                        final_amt,
                        entry_paid,
                        _safe_float(r.get("cash_paid_at_entry") or r.get("cash_paid")),
                        _safe_float(r.get("online_paid_at_entry") or r.get("online_paid")),
                        due_amt,
                        _status_cell(style),
                    ]
                )
                row_styles.append(style)
                row_ids.append(pid)
            out_columns = list(columns)
            out_columns, rows = _filter_by_column_visibility(
                "purchase_history", out_columns, rows
            )
            rem = {}
            try:
                rem = sq.purchases_summary(
                    from_date=fd or "",
                    to_date=td or "",
                    q=server_q,
                    ids=row_ids,
                ) or {}
            except Exception as pexc:
                print(f"[purchase history] online summary: {pexc}")
            due_live = round(fifo_due_total, 2)
            summary = {
                "bills": len(rows),
                "total": round(float(rem.get("final_amount") or fifo_final_total or 0), 2),
                "paid": round(fifo_paid_total, 2),
                "due": due_live,
                "supplier_due": due_live,
                "supplier_credit": round(float(rem.get("supplier_credit") or 0), 2),
            }
            return {
                "columns": out_columns,
                "rows": rows,
                "row_styles": row_styles,
                "row_ids": row_ids,
                "schedules": schedule_filter_choices(),
                "sort_options": empty["sort_options"],
                "summary": summary,
                "history_scope": load_history_scope(),
                "fy_label": current_fy_label(),
                "filter_from": fd,
                "filter_to": td,
                "default_fy_applied": applied_default,
                "filter_choices": _choices(),
                "server_error": _server_error_text(_read_failures),
            }
    except Exception as exc:
        print(f"[purchase history] online fetch: {exc}")
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                # Online must not fall through to empty local SQLite.
                return {
                    **empty,
                    "filter_from": fd or "",
                    "filter_to": td or "",
                    "default_fy_applied": applied_default,
                }
        except Exception:
            pass

    if not _table_exists(conn, "purchases"):
        return empty

    pcols = _table_cols(conn, "purchases")
    paid_col = (
        "amount_paid_at_entry"
        if "amount_paid_at_entry" in pcols
        else "amount_paid"
    )
    cash_col = (
        "cash_paid_at_entry" if "cash_paid_at_entry" in pcols else None
    )
    online_col = (
        "online_paid_at_entry" if "online_paid_at_entry" in pcols else None
    )

    where = ["COALESCE(p.deleted,0)=0"]
    if "is_autosave" in pcols:
        where.append("COALESCE(p.is_autosave,0)=0")
    params: list[Any] = []
    if fd:
        where.append("p.purchase_date>=?")
        params.append(fd)
    if td:
        where.append("p.purchase_date<=?")
        params.append(td)
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        where.append(
            "(CAST(p.purchase_no AS TEXT) LIKE ? OR CAST(p.bill_number AS TEXT) LIKE ? "
            "OR s.name LIKE ? OR s.phone LIKE ?)"
        )
        params.extend([like, like, like, like])

    supplier = (supplier or "").strip()
    if supplier:
        where.append("s.name LIKE ?")
        params.append(f"%{supplier}%")

    medicine = (medicine or "").strip()
    batch = (batch or "").strip()
    schedule = (schedule or "").strip()
    if medicine or batch or schedule:
        line_parts = ["pi.purchase_id=p.id"]
        line_params: list[Any] = []
        if medicine:
            line_parts.append("m.name LIKE ?")
            line_params.append(f"%{medicine}%")
        if batch:
            line_parts.append(
                "(COALESCE(pi.batch_no,'') LIKE ? OR COALESCE(m.batch_no,'') LIKE ?)"
            )
            line_params.extend([f"%{batch}%", f"%{batch}%"])
        # Schedule is deliberately NOT in line_parts with medicine and batch.
        #
        # Two shops running the same query got two different answers, because
        # offline and online disagreed about what the filter MEANS:
        #
        #   * "Non-Scheduled" — offline asked "is ANY line unscheduled", the
        #     server asks "are ALL of them". On a real store that is 247 bills
        #     against 61, out of 367. Any-line hands back two thirds of the
        #     whole history, which is the same uselessness that opened this
        #     report in the first place, so the server's rule is the one kept.
        #   * a named schedule — offline matched the line OR the medicines
        #     master, the server takes the LINE and falls back to the master.
        #     Line-wins is right: the schedule written on the line is what was
        #     actually bought that day; the master is today's opinion.
        #
        # The resolved expression below is the same one adminService.js uses.
        resolved = (
            "TRIM(COALESCE(NULLIF(TRIM(pi.schedule),''), "
            "NULLIF(TRIM(m.schedule),''), ''))"
        )
        line_sql = (
            "EXISTS ("
            "SELECT 1 FROM purchase_items pi "
            "JOIN medicines m ON m.id=pi.medicine_id "
            f"WHERE {' AND '.join(line_parts)}"
            ")"
        )
        if schedule:
            if schedule == "Non-Scheduled":
                sched_sql = (
                    "EXISTS (SELECT 1 FROM purchase_items pi "
                    "LEFT JOIN medicines m ON m.id=pi.medicine_id "
                    f"WHERE pi.purchase_id=p.id AND {resolved}='') "
                    "AND NOT EXISTS (SELECT 1 FROM purchase_items pi "
                    "LEFT JOIN medicines m ON m.id=pi.medicine_id "
                    f"WHERE pi.purchase_id=p.id AND {resolved}<>'')"
                )
                sched_params: list[Any] = []
            else:
                sched_sql = (
                    "EXISTS (SELECT 1 FROM purchase_items pi "
                    "LEFT JOIN medicines m ON m.id=pi.medicine_id "
                    f"WHERE pi.purchase_id=p.id AND {resolved}=?)"
                )
                sched_params = [schedule]
            where.append(f"({sched_sql})")
            params.extend(sched_params)
        if medicine or batch:
            where.append(line_sql)
            params.extend(line_params)

    cash_sql = f"COALESCE(p.{cash_col},0)" if cash_col else "0"
    online_sql = f"COALESCE(p.{online_col},0)" if online_col else "0"
    limit = max(1, min(int(limit or 500), 5000))
    sql = f"""
        SELECT p.id, p.purchase_no, COALESCE(p.bill_number,''), p.purchase_date,
               COALESCE(s.name,''), COALESCE(s.phone,''),
               COALESCE(p.final_amount, p.total_amount, 0),
               COALESCE(p.{paid_col},0),
               {cash_sql}, {online_sql}
        FROM purchases p
        LEFT JOIN suppliers s ON p.supplier_id=s.id
        WHERE {' AND '.join(where)}
        ORDER BY p.purchase_date DESC, COALESCE(p.fy_serial, p.id) DESC, p.id DESC
        LIMIT ?
    """
    params.append(limit)
    try:
        raw = conn.execute(sql, params).fetchall()
    except Exception:
        return empty

    from core.list_sort import bill_sort_key

    sort_key = (sort or "").strip()
    rows_raw = list(raw)
    if sort_key.startswith("Bill No"):
        rows_raw.sort(
            key=lambda r: bill_sort_key(r[1] if len(r) > 1 else ""),
            reverse="high to low" in sort_key,
        )
    elif sort_key.startswith("Oldest"):
        rows_raw.sort(
            key=lambda r: (str(r[3] or ""), bill_sort_key(r[1] if len(r) > 1 else ""))
        )
    elif "Z-A" in sort_key:
        rows_raw.sort(key=lambda r: str(r[4] or "").lower(), reverse=True)
    elif "A-Z" in sort_key:
        rows_raw.sort(key=lambda r: str(r[4] or "").lower())

    rows: list[list[Any]] = []
    row_styles: list[Optional[dict[str, Any]]] = []
    row_ids: list[int] = []
    for r in rows_raw:
        final_amt = _safe_float(r[6])
        paid = _safe_float(r[7])
        entry_due = max(0.0, round(final_amt - paid, 2))
        due_f = (due or "").strip().lower()
        if due_f in ("due", "due only") and entry_due <= 0.01:
            continue
        if due_f in ("paid", "cleared", "paid / cleared", "paid/cleared") and entry_due > 0.01:
            continue
        if entry_due <= 0.01:
            ri = "cleared"
        elif paid > 0:
            ri = "partial"
        else:
            ri = "due"
        style = _indicator_style(ri)
        from core.fy_serial import display_purchase_no

        rows.append(
            [
                display_purchase_no(str(r[1] or "")),
                r[2],
                r[3],
                r[4],
                r[5],
                final_amt,
                paid,
                _safe_float(r[8]),
                _safe_float(r[9]),
                entry_due,
                _status_cell(style) or ("✓ Cleared" if ri == "cleared" else "⚠ Due"),
            ]
        )
        row_styles.append(style)
        row_ids.append(int(r[0] or 0))

    supplier_due = supplier_credit = 0.0
    try:
        if _table_exists(conn, "suppliers"):
            scols = _table_cols(conn, "suppliers")
            if "total_due" in scols:
                supplier_due = _safe_float(
                    conn.execute(
                        "SELECT COALESCE(SUM(total_due),0) FROM suppliers "
                        "WHERE total_due>0"
                    ).fetchone()[0]
                )
            if "total_credit" in scols:
                supplier_credit = _safe_float(
                    conn.execute(
                        "SELECT COALESCE(SUM(total_credit),0) FROM suppliers "
                        "WHERE total_credit>0"
                    ).fetchone()[0]
                )
    except Exception:
        pass

    summary = {
        "bills": len(rows),
        "total": round(sum(_safe_float(r[5]) for r in rows), 2),
        "paid": round(sum(_safe_float(r[6]) for r in rows), 2),
        "due": round(sum(_safe_float(r[9]) for r in rows), 2),
        "supplier_due": round(supplier_due, 2),
        "supplier_credit": round(supplier_credit, 2),
    }
    out_columns, rows = _filter_by_column_visibility(
        "purchase_history", columns, rows
    )
    return {
        "columns": out_columns,
        "rows": rows,
        "row_styles": row_styles,
        "row_ids": row_ids,
        "schedules": schedule_filter_choices(),
        "sort_options": list(
            __import__("core.list_sort", fromlist=["SORT_OPTIONS"]).SORT_OPTIONS
        ),
        "summary": summary,
        "history_scope": load_history_scope(),
        "fy_label": current_fy_label(),
        "filter_from": fd,
        "filter_to": td,
        "default_fy_applied": applied_default,
        "filter_choices": _choices(),
    }


def list_purchase_history(
    conn,
    q: str = "",
    from_date: str = "",
    to_date: str = "",
    supplier: str = "",
    due: str = "",
    schedule: str = "",
    medicine: str = "",
    batch: str = "",
    sort: str = "",
    limit: int = 500,
) -> dict[str, Any]:
    """Purchase history — same rule as sales history (see list_sales_history)."""
    out = _list_purchase_history_range(
        conn, q=q, from_date=from_date, to_date=to_date, supplier=supplier,
        due=due, schedule=schedule, medicine=medicine, batch=batch, sort=sort,
        limit=limit,
    )
    if out.get("rows") or not out.get("default_fy_applied"):
        return out
    wider = _list_purchase_history_range(
        conn, q=q, from_date="", to_date="", supplier=supplier,
        due=due, schedule=schedule, medicine=medicine, batch=batch, sort=sort,
        limit=limit, _scope_all=True,
    )
    if not wider.get("rows"):
        return out
    wider["history_scope_widened"] = True
    wider["rows_note"] = _widened_note(out.get("fy_label"), wider.get("rows_note"))
    return wider


def _sales_village_lists(conn) -> tuple[list[str], str]:
    """Villages from Settings → Contact → Village (same list as the Sales address box)."""
    try:
        from core.village_service import get_default_village, load_villages

        villages = [
            str(v).strip() for v in (load_villages(conn) or []) if str(v).strip()
        ]
        default = (get_default_village(conn) or "").strip()
        return villages[:_NAME_LIST_LIMIT], default
    except Exception:
        return [], ""


def _sane_bill_date(bill_date: Any):
    """The Bill Date box as the owner types a year: 2 -> 20 -> 202 -> 2026. The
    browser reports every step ("0020-07-21"), and each one asked the server for
    that year's next number -- a 500 on the server, 118 times (2026-09-27). A date
    whose year is not 2000-2100 is treated as not given (today)."""
    if bill_date in (None, ""):
        return None
    if hasattr(bill_date, "year"):
        return bill_date if 2000 <= bill_date.year <= 2100 else None
    s = str(bill_date).strip()[:10]
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m or not (2000 <= int(m.group(1)) <= 2100):
        return None
    return s


def _next_sales_bill_hint(conn, bill_date: Any = None) -> str:
    """Next bill number for a bill dated ``bill_date`` (today when blank).

    The number belongs to the financial year of the bill's OWN date: a bill dated
    20 March shows the old year's next number. This always asked for today's, so a
    back-dated bill showed a number it was never going to get.
    """
    bill_date = _sane_bill_date(bill_date)
    try:
        from core.fy_serial import next_sales_bill_hint

        hint = (next_sales_bill_hint(conn, bill_date or None) or "").strip()
        if hint and hint not in ("—", "-", "---", "…"):
            return hint
    except Exception:
        pass
    try:
        from core.fy_serial import (
            display_sales_bill_no,
            fy_start_year_for_date,
            fy_start_year_in_code,
        )

        target_fy = fy_start_year_for_date(bill_date or date.today())
        rows = conn.execute(
            "SELECT bill_no FROM sales WHERE bill_no LIKE 'SCB%'"
        ).fetchall()
        max_num = 0
        for (bno,) in rows:
            held = fy_start_year_in_code(bno)
            if held is not None and held != target_fy:
                continue  # another year's series
            # Must strip /FY… or every post-migration bill looks non-numeric → SCB1
            raw = display_sales_bill_no(bno)
            suffix = raw[3:] if raw.upper().startswith("SCB") else ""
            if suffix.isdigit():
                max_num = max(max_num, int(suffix))
        return f"SCB{max_num + 1}"
    except Exception:
        return "SCB1"


def sales_form_defaults(conn) -> dict[str, Any]:
    customers: list[dict[str, Any]] = []
    doctors: list[str] = []
    # Doctor name -> number, so picking a doctor fills the phone box. The list
    # carried names only, and the number had to be typed on every bill.
    doctor_phones: dict[str, str] = {}
    villages, default_village = _sales_village_lists(conn)
    medicines: list[dict[str, Any]] = []

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import (
                customer_names,
                customers as oc_customers,
                doctor_names,
                doctors as oc_doctors,
            )

            for c in oc_customers():
                customers.append(
                    {
                        "id": c.get("id") or c.get("local_id"),
                        "name": c.get("name") or "",
                        "phone": c.get("phone") or "",
                        "address": c.get("address") or "",
                        "due": _safe_float(c.get("total_due")),
                        "credit": _safe_float(c.get("total_credit")),
                    }
                )
            # Include bill-only customer names so Sales matches Sales History.
            seen = {str(c.get("name") or "").strip().upper() for c in customers}
            for n in customer_names():
                if not n or n.upper() in seen:
                    continue
                seen.add(n.upper())
                customers.append(
                    {
                        "id": None,
                        "name": n,
                        "phone": "",
                        "address": "",
                        "due": 0.0,
                        "credit": 0.0,
                    }
                )
            doctors = doctor_names()[:_NAME_LIST_LIMIT]
            for d in oc_doctors():
                n = str(d.get("name") or "").strip()
                ph = str(d.get("phone") or "").strip()
                if n and ph:
                    doctor_phones[n.upper()] = ph
            medicines = search_medicines(conn, "", 80)
            payment_modes = ["Cash", "Due", "Online", "UPI", "Cheque"]
            try:
                from core.desktop_settings_service import get_options

                opts = get_options().get("payment_modes") or []
                if opts:
                    payment_modes = list(opts)
            except Exception:
                pass
            today = date.today().isoformat()
            cust_names = customer_names()
            return {
                "next_bill_hint": _next_sales_bill_hint(conn),
                "customers": cust_names,
                "customer_details": customers,
                "doctors": doctors,
                "doctor_phones": doctor_phones,
                "villages": villages,
                "default_village": default_village,
                "medicines": medicines,
                "payment_modes": payment_modes,
                "form": {"bill_date": today},
            }
    except Exception:
        pass

    if _table_exists(conn, "customers") and "name" in _table_cols(conn, "customers"):
        ccols = _table_cols(conn, "customers")
        deleted = " WHERE COALESCE(deleted,0)=0" if "deleted" in ccols else ""
        # total_credit comes out of the same row the loop is already reading, so
        # the per-customer lookup it used to do was one wasted query each. That
        # cost little at 500 rows; at the raised limit below it would have been
        # twenty thousand, which is what makes lifting the cap safe.
        credit_col = (
            "COALESCE(total_credit,0)" if "total_credit" in ccols else "0"
        )
        try:
            for r in conn.execute(
                f"SELECT id, name, COALESCE(phone,''), COALESCE(address,''), "
                f"COALESCE(total_due,0), {credit_col} FROM customers{deleted} "
                f"ORDER BY name COLLATE NOCASE LIMIT {_CUSTOMER_LIST_LIMIT}"
            ):
                customers.append(
                    {
                        "id": r[0],
                        "name": r[1] or "",
                        "phone": r[2] or "",
                        "address": r[3] or "",
                        "due": _safe_float(r[4]),
                        "credit": _safe_float(r[5]),
                    }
                )
        except Exception:
            customers = []

    if _table_exists(conn, "doctors") and "name" in _table_cols(conn, "doctors"):
        try:
            phone_col = (
                "COALESCE(phone,'')" if "phone" in _table_cols(conn, "doctors") else "''"
            )
            doctors = []
            for r in conn.execute(
                f"SELECT name, {phone_col} FROM doctors "
                f"ORDER BY name COLLATE NOCASE LIMIT {_NAME_LIST_LIMIT}"
            ):
                if not r or not r[0]:
                    continue
                doctors.append(str(r[0]))
                if str(r[1] or "").strip():
                    doctor_phones[str(r[0]).strip().upper()] = str(r[1]).strip()
        except Exception:
            doctors = []

    medicines = search_medicines(conn, "", 80)

    payment_modes = ["Cash", "Due", "Online", "UPI", "Cheque"]
    try:
        from core.desktop_settings_service import get_options

        opts = get_options().get("payment_modes") or []
        if opts:
            payment_modes = list(opts)
            if "Due" not in payment_modes:
                payment_modes = ["Cash", "Due"] + [
                    m for m in payment_modes if m not in ("Cash", "Due")
                ]
    except Exception:
        pass

    today = date.today().isoformat()
    return {
        "next_bill_hint": _next_sales_bill_hint(conn),
        "customers": [c["name"] for c in customers],
        "customer_details": customers,
        "doctors": doctors,
        "doctor_phones": doctor_phones,
        "villages": villages,
        "default_village": default_village,
        "medicines": medicines,
        "payment_modes": payment_modes,
        "form": {
            "customer": "",
            "phone": "",
            "doctor": "",
            "address": "",
            "bill_date": today,
            "payment_mode": "Cash",
            "items": [],
            "subtotal": 0,
            "discount": 0,
            "gst": 0,
            "total": 0,
            "cash": 0,
            "due": 0,
        },
    }


def purchase_form_defaults(conn) -> dict[str, Any]:
    suppliers: list[dict[str, Any]] = []
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import suppliers as oc_suppliers
            from core.layout_config import get_med_types

            for s in oc_suppliers()[:_NAME_LIST_LIMIT]:
                suppliers.append(
                    {
                        "id": s.get("id") or s.get("local_id"),
                        "name": s.get("name") or "",
                        "phone": s.get("phone") or "",
                        "address": s.get("address") or "",
                        "gstin": s.get("gstin") or "",
                        "dl": s.get("dl_numbers") or "",
                        "due": _safe_float(s.get("total_due")),
                        "credit": _safe_float(s.get("total_credit")),
                    }
                )
            today = date.today().isoformat()
            return {
                "suppliers": suppliers,
                "medicine_types": get_med_types(),
                "schedules": get_schedules(conn),
                "medicines": search_medicines(conn, "", 80),
                "form": {
                    "supplier": "",
                    "phone": "",
                    "address": "",
                    "gstin": "",
                    "dl": "",
                    "purchase_date": today,
                },
            }
    except Exception:
        pass

    if _table_exists(conn, "suppliers") and "name" in _table_cols(conn, "suppliers"):
        scols = _table_cols(conn, "suppliers")
        deleted = " WHERE COALESCE(deleted,0)=0" if "deleted" in scols else ""
        try:
            for r in conn.execute(
                f"SELECT id, name, COALESCE(phone,''), COALESCE(address,''), "
                f"COALESCE(gstin,''), COALESCE(dl_numbers,''), COALESCE(total_due,0), "
                f"COALESCE(total_credit,0) "
                f"FROM suppliers{deleted} ORDER BY name COLLATE NOCASE LIMIT {_NAME_LIST_LIMIT}"
            ):
                suppliers.append(
                    {
                        "id": r[0],
                        "name": r[1] or "",
                        "phone": r[2] or "",
                        "address": r[3] or "",
                        "gstin": r[4] or "",
                        "dl": r[5] or "",
                        "due": _safe_float(r[6]),
                        "credit": _safe_float(r[7]),
                    }
                )
        except Exception:
            suppliers = []

    today = date.today().isoformat()
    from core.layout_config import get_med_types

    return {
        "suppliers": suppliers,
        "medicine_types": get_med_types(),
        "schedules": get_schedules(conn),
        "medicines": search_medicines(conn, "", 80),
        "form": {
            "supplier": "",
            "phone": "",
            "address": "",
            "gstin": "",
            "dl": "",
            "purchase_date": today,
            "bill_number": "",
            "items": [],
            "subtotal": 0,
            "gst": 0,
            "discount": 0,
            "total": 0,
            "paid": 0,
            "due": 0,
        },
    }


def returns_bundle(conn) -> dict[str, Any]:
    from core.desktop_returns_service import _ensure_return_tables
    from core.sync_prefs import is_online_mode

    _ensure_return_tables(conn)
    out: dict[str, Any] = {
        "sales_returns": {"count": 0, "rows": []},
        "purchase_returns": {"count": 0, "rows": []},
        "writeoffs": {"count": 0, "rows": []},
    }
    sales_cols = [
        "Return No",
        "Date",
        "Bill No",
        "Customer",
        "Refund",
        "Reason",
    ]
    purch_cols = [
        "Return No",
        "Date",
        "Purchase No",
        "Supplier",
        "Credit",
        "Reason",
    ]

    def _count(table: str) -> int:
        if not _table_exists(conn, table):
            return 0
        cols = _table_cols(conn, table)
        where = "WHERE COALESCE(deleted,0)=0" if "deleted" in cols else ""
        try:
            return int(
                conn.execute(f"SELECT COUNT(*) FROM [{table}] {where}").fetchone()[0]
                or 0
            )
        except Exception:
            return 0

    def _recent(
        table: str, wanted: list[str], limit: int = 50
    ) -> tuple[list[str], list[list[Any]], list[int]]:
        if not _table_exists(conn, table):
            return wanted, [], []
        cols = _table_cols(conn, table)
        select = []
        headers = []
        for key in wanted:
            if key in cols:
                select.append(key)
                headers.append(key.replace("_", " ").title())
        if not select:
            return wanted, [], []
        where = "WHERE COALESCE(deleted,0)=0" if "deleted" in cols else ""
        id_expr = "id" if "id" in cols else "rowid"
        try:
            rows = conn.execute(
                f"SELECT {id_expr}, {', '.join(select)} FROM [{table}] {where} "
                f"ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            row_ids = [int(r[0] or 0) for r in rows]
            values = [list(r[1:]) for r in rows]
            return headers, values, row_ids
        except Exception:
            return headers, [], []

    if is_online_mode():
        try:
            from core import store_query_client as sq
            from core.online_mutation_queue import (
                merge_server_rows,
                overlay_purchase_return_dicts,
                overlay_sales_return_dicts,
            )

            # Both lists at once. These are two independent GETs to the store
            # server and they were waited on one after the other, so opening
            # Returns cost two full round trips before anything appeared -- and
            # the screen re-fetches on every sync tick, including while another
            # counter is billing. Every other online page already does this.
            # 200, not 500: the offline branch of this same function caps at
            # 200, so the extra 300 rows were downloaded on a narrow rural link
            # and then never shown.
            sales_data, purch_data = sq.fetch_parallel(
                lambda: sq.list_sales_returns(limit=200) or {},
                lambda: sq.list_purchase_returns(limit=200) or {},
            )

            sales_merged = merge_server_rows(
                list((sales_data or {}).get("rows") or []),
                overlay_sales_return_dicts(),
                collection="sales_returns",
            )
            sales_rows: list[list[Any]] = []
            sales_ids: list[int] = []
            for r in sales_merged:
                if not isinstance(r, dict) or r.get("deleted"):
                    continue
                try:
                    rid = int(r.get("id") or r.get("local_id") or 0)
                except (TypeError, ValueError):
                    rid = 0
                rno = str(r.get("return_no") or "")
                if r.get("pending") and rno and not str(rno).startswith("⏳"):
                    rno = f"⏳ {rno}"
                sales_ids.append(rid)
                sales_rows.append(
                    [
                        rno,
                        str(r.get("return_date") or "")[:10],
                        r.get("bill_no") or "",
                        r.get("customer_name") or "",
                        r.get("refund_amount") or 0,
                        r.get("reason") or "",
                    ]
                )
            out["sales_returns"] = {
                "count": len(sales_rows),
                "columns": sales_cols,
                "rows": sales_rows,
                "row_ids": sales_ids,
            }

            purch_merged = merge_server_rows(
                list((purch_data or {}).get("rows") or []),
                overlay_purchase_return_dicts(),
                collection="purchase_returns",
            )
            purch_rows: list[list[Any]] = []
            purch_ids: list[int] = []
            for r in purch_merged:
                if not isinstance(r, dict) or r.get("deleted"):
                    continue
                try:
                    rid = int(r.get("id") or r.get("local_id") or 0)
                except (TypeError, ValueError):
                    rid = 0
                rno = str(r.get("return_no") or "")
                if r.get("pending") and rno and not str(rno).startswith("⏳"):
                    rno = f"⏳ {rno}"
                purch_ids.append(rid)
                purch_rows.append(
                    [
                        rno,
                        str(r.get("return_date") or "")[:10],
                        r.get("purchase_no")
                        or r.get("bill_label")
                        or r.get("bill_number")
                        or "",
                        r.get("supplier_name") or "",
                        r.get("refund_amount") or 0,
                        r.get("reason") or "",
                    ]
                )
            out["purchase_returns"] = {
                "count": len(purch_rows),
                "columns": purch_cols,
                "rows": purch_rows,
                "row_ids": purch_ids,
            }
        except Exception as exc:
            print(f"[returns] online history: {exc}")

        # Write-offs came from _recent/_count, which read the LOCAL connection
        # -- and Online that connection is sqlite3.connect(":memory:"). The
        # Write-off tab therefore showed an empty history and a zero badge on a
        # shop with a drawer full of disposals. There is no /api/store route for
        # these, so the sync pull is the source.
        wanted = ["disposal_no", "disposal_date", "quantity", "reason", "notes"]
        try:
            from core.online_catalog import stock_disposals as _online_disposals

            docs = [d for d in (_online_disposals() or []) if isinstance(d, dict)]
            docs.sort(key=lambda d: int(_safe_float(d.get("id") or d.get("local_id"))), reverse=True)
            wo_rows = []
            wo_ids = []
            for d in docs[:50]:
                wo_ids.append(int(_safe_float(d.get("id") or d.get("local_id"))))
                wo_rows.append([
                    d.get("disposal_no") or "",
                    str(d.get("disposal_date") or "")[:10],
                    d.get("quantity") if d.get("quantity") is not None else d.get("qty") or 0,
                    d.get("reason") or "",
                    d.get("notes") or "",
                ])
            out["writeoffs"]["count"] = len(docs)
            out["writeoffs"]["columns"] = [w.replace("_", " ").title() for w in wanted]
            out["writeoffs"]["rows"] = wo_rows
            out["writeoffs"]["row_ids"] = wo_ids
        except Exception as exc:
            print(f"[returns] online write-off history: {exc}")
            h, rows, ids = _recent("stock_disposals", wanted)
            out["writeoffs"]["count"] = _count("stock_disposals")
            out["writeoffs"]["columns"] = h
            out["writeoffs"]["rows"] = rows
            out["writeoffs"]["row_ids"] = ids
        if out["sales_returns"].get("columns"):
            return out

    out["sales_returns"]["count"] = _count("sales_returns")
    try:
        sales_rows = conn.execute(
            """
            SELECT sr.id, sr.return_no, sr.return_date,
                   COALESCE(s.bill_no, ''), COALESCE(c.name, ''),
                   sr.refund_amount, COALESCE(sr.reason, '')
            FROM sales_returns sr
            LEFT JOIN sales s ON sr.sale_id = s.id
            LEFT JOIN customers c ON sr.customer_id = c.id
            WHERE COALESCE(sr.deleted, 0) = 0
            ORDER BY sr.id DESC LIMIT 200
            """
        ).fetchall()
        out["sales_returns"]["columns"] = [
            "Return No",
            "Date",
            "Bill No",
            "Customer",
            "Refund",
            "Reason",
        ]
        out["sales_returns"]["row_ids"] = [int(r[0] or 0) for r in sales_rows]
        out["sales_returns"]["rows"] = [
            [r[1], r[2], r[3], r[4], r[5], r[6]] for r in sales_rows
        ]
    except Exception:
        h, rows, ids = _recent(
            "sales_returns",
            ["return_no", "return_date", "refund_amount", "reason"],
        )
        out["sales_returns"]["columns"] = h
        out["sales_returns"]["rows"] = rows
        out["sales_returns"]["row_ids"] = ids

    out["purchase_returns"]["count"] = _count("purchase_returns")
    try:
        purch_rows = conn.execute(
            """
            SELECT pr.id, pr.return_no, pr.return_date,
                   COALESCE(p.purchase_no, ''), COALESCE(s.name, ''),
                   pr.refund_amount, COALESCE(pr.reason, '')
            FROM purchase_returns pr
            LEFT JOIN purchases p ON pr.purchase_id = p.id
            LEFT JOIN suppliers s ON pr.supplier_id = s.id
            WHERE COALESCE(pr.deleted, 0) = 0
            ORDER BY pr.id DESC LIMIT 200
            """
        ).fetchall()
        out["purchase_returns"]["columns"] = [
            "Return No",
            "Date",
            "Purchase No",
            "Supplier",
            "Credit",
            "Reason",
        ]
        out["purchase_returns"]["row_ids"] = [int(r[0] or 0) for r in purch_rows]
        out["purchase_returns"]["rows"] = [
            [r[1], r[2], r[3], r[4], r[5], r[6]] for r in purch_rows
        ]
    except Exception:
        h, rows, ids = _recent(
            "purchase_returns",
            ["return_no", "return_date", "refund_amount", "reason"],
        )
        out["purchase_returns"]["columns"] = h
        out["purchase_returns"]["rows"] = rows
        out["purchase_returns"]["row_ids"] = ids

    out["writeoffs"]["count"] = _count("stock_disposals")
    h, rows, ids = _recent(
        "stock_disposals",
        ["disposal_no", "disposal_date", "quantity", "reason", "notes"],
    )
    out["writeoffs"]["columns"] = h
    out["writeoffs"]["rows"] = rows
    out["writeoffs"]["row_ids"] = ids
    return out
