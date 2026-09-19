"""Returns / write-off workflows for the Tauri desktop API."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional


def _ensure_return_tables(conn) -> None:
    """Create return / disposal tables on older store DBs (classic does this per page)."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_no TEXT UNIQUE,
            sale_id INTEGER,
            customer_id INTEGER,
            return_date DATE,
            refund_amount REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            reason TEXT,
            deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_return_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER,
            medicine_id INTEGER,
            qty REAL,
            rate REAL,
            amount REAL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_no TEXT UNIQUE,
            purchase_id INTEGER,
            supplier_id INTEGER,
            return_date DATE,
            refund_amount REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            reason TEXT,
            deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_return_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER,
            medicine_id INTEGER,
            qty REAL,
            rate REAL,
            amount REAL,
            stock_units REAL
        )
        """
    )
    # Existing installs: add the column that records how much stock the line
    # actually moved, so deleting the return can give back the same amount.
    try:
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(purchase_return_items)").fetchall()
        }
        if "stock_units" not in cols:
            conn.execute("ALTER TABLE purchase_return_items ADD COLUMN stock_units REAL")
    except Exception:
        pass
    conn.commit()


def purchase_return_stock_units(item: dict[str, Any], *, medicine: dict | None = None) -> float:
    """Stock units a purchase-return line moves, in the medicine's own unit.

    A purchase return is entered in STRIPS for tablet-type medicines, while
    stock is held in tablets. Saving used to multiply by tablets-per-strip while
    deleting, and every online path, used the raw strip count -- so cancelling a
    5-strip return of a 10s tablet gave back 5 tablets instead of 50, and the
    other 45 were lost from the shelf for good.

    Lines saved by this build carry `stock_units`; older ones are recomputed
    from the medicine's type and pack.
    """
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    stored = item.get("stock_units")
    if stored is None:
        stored = item.get("stock_deduction")
    if stored is not None:
        try:
            val = float(stored)
            if val != 0:
                return val
        except (TypeError, ValueError):
            pass

    qty = _safe_float(item.get("qty"))
    src = medicine if isinstance(medicine, dict) else item
    med_type = str(item.get("type") or src.get("type") or "")
    unit = str(
        item.get("unit")
        or item.get("pack_size")
        or src.get("unit")
        or src.get("pack_size")
        or "1"
    )
    is_tablet = bool(item.get("is_tablet")) or is_strip_count_type(med_type)
    if not is_tablet:
        return qty
    tps = _safe_int(item.get("tablets_per_stripe"), 0)
    if tps <= 0:
        tps = _safe_int(src.get("tablets_per_stripe"), 0)
    if tps <= 0:
        tps = parse_tablets_per_stripe(unit) or 1
    return qty * tps


def _guard_mutate() -> dict[str, Any] | None:
    try:
        from core.online_guard import OnlineUnavailableError, ensure_can_mutate

        ensure_can_mutate()
    except OnlineUnavailableError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc) or "Save blocked."}
    return None


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v if v is not None else default))
    except (TypeError, ValueError):
        return default


def _made_now() -> str:
    """The time a new return (or its refund payout) was made, as the server stores it."""
    from core.server_crud import _now

    return _now()


def _return_id_for(collection: str, client_uuid: str) -> tuple[int, bool]:
    """Real server id when we can get one, temp id only as a fallback.

    Returns were the ONLY collection using a negative temp id as their
    permanent local_id -- sales and purchases have always allocated properly.
    Nothing ever reconciled it, so a negative id was stored for good, and the
    server allocator (MAX(local_id) + 1) then handed out negative ids for
    every later return. Android refuses those ("Server did not allocate
    sales_returns id"), so one desktop return broke returns on both devices.
    """
    try:
        from core.server_crud import allocate_id

        rid = int(allocate_id(collection))
        if rid > 0:
            return rid, False
    except Exception:
        pass
    from core.online_mutation_queue import temp_id_from_uuid

    return temp_id_from_uuid(client_uuid), True


def _normalize_bill_search_q(q: str) -> str:
    """Turn combo label 'SCB101/FY2026-27 — AJAY (date)' into a search term."""
    s = (q or "").strip()
    if not s:
        return ""
    for sep in (" — ", " – ", " --- ", " -- ", " - "):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            break
    if s.endswith(")") and "(" in s:
        maybe = s[: s.rfind("(")].strip()
        if maybe:
            s = maybe
    return s


def _settle_customer_after_refund(
    due: float, credit: float, refund: float, settle: str
) -> tuple[float, float, float]:
    """Apply return to due/credit. Optional cash/online payout of new credit only."""
    due = round(float(due or 0), 2)
    credit = round(float(credit or 0), 2)
    refund = round(float(refund or 0), 2)
    settle = (settle or "ledger").strip().lower()
    due_cut = min(due, refund)
    new_credit = round(max(0.0, refund - due_cut), 2)
    due2 = round(due - due_cut, 2)
    credit2 = round(credit + new_credit, 2)
    payout = 0.0
    if settle in ("cash", "online") and new_credit > 0:
        payout = new_credit
        credit2 = round(credit2 - payout, 2)
    return due2, credit2, payout


def _sales_return_stock_docs(items: list[dict[str, Any]], client_uuid: str) -> list[dict[str, Any]]:
    """Local medicine docs with stock restored for this sales return."""
    from core.online_catalog import medicine_by_id
    from core.server_crud import _device_id, _meta

    cu = (client_uuid or "").strip()
    med_docs: list[dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        mid = _safe_int(it.get("medicine_id"))
        qty = _safe_float(it.get("qty"))
        if mid <= 0 or qty <= 0:
            continue
        mp = medicine_by_id(mid) or {
            "id": mid,
            "local_id": mid,
            "stock_qty": 0,
            "name": it.get("name") or "",
        }
        mp = _meta(mp)
        mp["id"] = mid
        mp["local_id"] = mid
        mp["stock_qty"] = float(mp.get("stock_qty") or 0) + qty
        mp["stock_ops"] = [
            {
                "op_uuid": f"sales_returns:{cu}:med:{mid}:v1",
                "op": "sale_return",
                "qty_delta": int(qty) if qty == int(qty) else qty,
                "medicine_id": mid,
                "ref_collection": "sales_returns",
                "device_id": _device_id(),
            }
        ]
        med_docs.append(mp)
    return med_docs


def _notify_return_saved(*, customer: bool = False) -> None:
    cols = ["medicines", "sales_returns"]
    if customer:
        cols.extend(["customers", "customer_payments", "sales"])
    try:
        from core.sync_status import note_collection_change

        for col in cols:
            note_collection_change(col)
    except Exception:
        pass
    try:
        from core.store_live_refresh import emit as live_emit

        live_emit(
            {
                "changes": [
                    {"collection": col, "operation": "upserted"} for col in cols
                ],
                "source": "sales_return",
            }
        )
    except Exception:
        pass


# The store's returns lists -- GET /api/store/returns/sales and /purchases,
# routes/storeQuery.js:148-194 on the live server -- read only `limit` (default
# 200, capped at 5000), `from` and `to` (inclusive, against the DATE column
# return_date, sent back as a plain YYYY-MM-DD), and answer ORDER BY
# return_date DESC, local_id DESC. There is no `q` and no offset.
_RETURNS_FIRST_PAGE = 500  # the one request the old code made
_RETURNS_MAX_PAGE = 5000  # storeQuery.js: Math.min(limit, 5000)
_RETURNS_MAX_PAGES = 200
# A return is stamped with the returning device's date, a bill with its own
# date, and a purchase date is typed from the supplier's invoice. Read back this
# far past the earlier of the bill's date and the day it was entered.
_RETURNS_FLOOR_SLACK_DAYS = 31


class ReturnsUnreadable(RuntimeError):
    """The store's returns could not be read to the end.

    Not the same thing as "nothing returned": counting an unread list as zero is
    exactly how an old bill comes up returnable a second time.
    """


_UNREADABLE_RETURNS = (
    "Could not read the earlier returns on this bill from the server, so what is "
    "left to return cannot be worked out. Try again in a moment."
)


def _iso_day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw or "").strip()[:10])
    except ValueError:
        return None


def _returns_floor(*raw_dates: Any) -> str:
    """The oldest day a bill's returns can carry: its earlier known date, less slack.
    "" (read the whole history) when neither date is readable."""
    days = [d for d in (_iso_day(r) for r in raw_dates) if d]
    if not days:
        return ""
    return (min(days) - timedelta(days=_RETURNS_FLOOR_SLACK_DAYS)).isoformat()


import threading as _threading  # noqa: E402

_returns_memo = _threading.local()


class _SharedReturnsListing:
    """Within this block, one complete returns listing serves every bill.

    The bulk purchase-return list opens up to 60 bills; each would otherwise walk
    the store's returns list for itself. Per thread, so two engine requests never
    share one."""

    def __enter__(self):
        self._prev = getattr(_returns_memo, "cache", None)
        if self._prev is None:
            _returns_memo.cache = {}
        return self

    def __exit__(self, *_exc):
        _returns_memo.cache = self._prev
        return False


def _list_returns_complete(collection: str, floor: str = "") -> list[dict]:
    """Every return on the store dated on or after `floor` ("" = all of them).

    The list used to be read as ONE page of 500. On a busy shop an old bill's
    earlier returns sat past row 500, were never counted, and the bill came up
    returnable again -- a second refund and a second stock-in.

    The walk is keyset on the date. A FULL page proves only that every row dated
    after its oldest day is in hand (the server sorts newest first); the next page
    asks again `to` that oldest day. A page that is not full is the whole rest of
    the range. A full page is never taken as the end. The first page is exactly
    the old request (500 rows, no range), so wherever the old cap was not hit the
    rows are the same as before. Raises ReturnsUnreadable when the list cannot be
    read to the end (more than 5000 returns on one day, a row with no date)."""
    floor_day = _iso_day(floor)
    cache = getattr(_returns_memo, "cache", None)
    if cache is not None:
        hit = cache.get(collection)
        if hit is not None:
            have_floor, rows = hit
            if have_floor is None or (floor_day is not None and have_floor <= floor_day):
                return rows

    from core import store_query_client as sq

    fetch = (
        sq.list_sales_returns if collection == "sales_returns" else sq.list_purchase_returns
    )
    out: dict[Any, dict] = {}
    to = ""
    limit = _RETURNS_FIRST_PAGE
    whole_history = False
    for _ in range(_RETURNS_MAX_PAGES):
        data = fetch(limit=limit, to_date=to) or {}
        rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
        for r in rows:
            rid = _safe_int(r.get("id") or r.get("local_id"))
            out.setdefault(rid if rid > 0 else f"row{len(out)}", r)
        if len(rows) < limit:
            whole_history = True
            break
        days = [_iso_day(r.get("return_date")) for r in rows]
        if any(d is None for d in days):
            raise ReturnsUnreadable("A store return has no readable date.")
        oldest = min(days)  # type: ignore[type-var]
        if floor_day is not None and oldest < floor_day:
            break  # the page already reaches back past the bill
        if to and oldest.isoformat() >= to:
            if limit < _RETURNS_MAX_PAGE:
                limit = _RETURNS_MAX_PAGE
                continue
            raise ReturnsUnreadable(
                f"More than {_RETURNS_MAX_PAGE} store returns on {oldest}; the list "
                "could not be read to the end."
            )
        to = oldest.isoformat()
        limit = _RETURNS_MAX_PAGE
    else:
        raise ReturnsUnreadable("The store's returns list did not come to an end.")
    rows_out = list(out.values())
    if cache is not None:
        cache[collection] = (None if whole_history else floor_day, rows_out)
    return rows_out


def _online_returned_map(
    collection: str, parent_key: str, parent_id: int, floor: str = ""
) -> dict[int, float]:
    """{medicine_id: already-returned qty} for one bill, fetched in ONE pass.

    This used to be resolved per line by _online_returned_qty, which re-downloaded
    the whole returns list inside the item loop. qty is summed per medicine across
    every return on the bill. The list is read back to `floor` (see
    _list_returns_complete), not one capped page. A list or a return document that
    cannot be read RAISES ReturnsUnreadable: it used to answer {}, which every
    caller took as "nothing returned yet". Queued-but-unsent returns are added by
    the caller through pending_return_line_qty, which is local and costs nothing.
    """
    out: dict[int, float] = {}
    try:
        from core.server_crud import get_doc

        seen: set[int] = set()
        for row in _list_returns_complete(collection, floor):
            if _safe_int(row.get(parent_key)) != int(parent_id):
                continue
            rid = _safe_int(row.get("id") or row.get("local_id"))
            if rid <= 0 or rid in seen:
                continue
            seen.add(rid)
            doc = get_doc(collection, rid)
            if not doc:
                raise ReturnsUnreadable(f"{collection} {rid} could not be read.")
            for it in doc.get("items") or []:
                if not isinstance(it, dict):
                    continue
                mid = _safe_int(it.get("medicine_id"))
                out[mid] = out.get(mid, 0.0) + _safe_float(it.get("qty"))
    except Exception as exc:
        raise ReturnsUnreadable(_UNREADABLE_RETURNS) from exc
    return out


def _bill_returned_map(
    collection: str,
    parent_key: str,
    parent_id: int,
    bill_date: Any,
    doc: dict | None,
    queued: bool = False,
) -> dict[int, float]:
    """_online_returned_map for one bill document, read back to its own dates.

    A bill still in the upload queue is not on the server yet, so the server can
    hold no return against it: for that one case an unreadable list costs nothing
    (its queued returns are added by the caller, from the local queue)."""
    try:
        return _online_returned_map(
            collection, parent_key, parent_id,
            floor=_returns_floor(bill_date, (doc or {}).get("created_at")),
        )
    except ReturnsUnreadable:
        if queued:
            return {}
        raise


def _pending_returned_qty(
    collection: str, parent_key: str, parent_id: int, medicine_id: int
) -> float:
    """Queued-but-unsent return qty for one line (local queue, no network)."""
    try:
        from core.online_mutation_queue import pending_return_line_qty

        return pending_return_line_qty(
            collection, parent_key, int(parent_id), int(medicine_id)
        )
    except Exception:
        return 0.0


def _online_returned_qty(collection: str, parent_key: str, parent_id: int, medicine_id: int) -> float:
    """Sum already-returned qty for a single sale/purchase line."""
    total = _online_returned_map(collection, parent_key, parent_id).get(
        int(medicine_id), 0.0
    )
    return total + _pending_returned_qty(
        collection, parent_key, parent_id, medicine_id
    )


def search_sales_bills(conn, *, q: str = "", medicine: str = "") -> dict[str, Any]:
    from core.sales_return_prefs import load_sales_return_lookup_days
    from core.sync_prefs import is_online_mode

    med = (medicine or "").strip()
    query = _normalize_bill_search_q(q)

    if is_online_mode():
        try:
            from core import store_query_client as sq
            from core.server_crud import get_doc
            from core.online_catalog import medicine_by_id
            from core.online_mutation_queue import merge_server_rows, overlay_sales_dicts

            days = load_sales_return_lookup_days()
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            # Ask the SERVER which bills contain this medicine.
            #
            # This used to pull the last 300 sales and then fetch each sale
            # DOCUMENT one at a time to look inside it -- up to 300 sequential
            # round trips for one press of Enter, which on a rural connection is
            # a minute or more of a screen doing nothing. The store server has
            # accepted a `medicine` filter all along (adminService.listSales),
            # and Sales History has been using it; Returns simply never did.
            #
            # One behaviour change worth naming: the server matches the item
            # name as a substring (ILIKE '%name%'), where the old loop compared
            # the whole name. That is the same match Sales History has always
            # used, and it is the friendlier one here -- a bill whose line reads
            # "AMOXY 250 CAP" used to be missed when the picker said
            # "AMOXY 250". The shop still picks a bill and sees its lines.
            server_rows = list(
                (sq.list_sales(
                    q=query if query and not med else "",
                    medicine=med,
                    # Without the date, LIMIT 300 takes the 300 most recent
                    # bills and the loop below then throws away everything
                    # older than the lookup window -- so on a busy shop the
                    # older half of that window was never searched at all.
                    from_date=cutoff if (not query or med) else "",
                    limit=300,
                ) or {}).get("rows") or []
            )
            # Rows the server matched are known to contain it; only bills that
            # exist only on this PC still have to be looked inside, and
            # those carry their own items, so it costs nothing.
            server_matched = {
                _safe_int(r.get("id") or r.get("local_id"))
                for r in server_rows
                if isinstance(r, dict)
            }
            listed = merge_server_rows(
                server_rows,
                overlay_sales_dicts(),
                collection="sales",
            )
            bills: list[dict[str, Any]] = []
            for r in listed:
                if not isinstance(r, dict):
                    continue
                bill_date = str(r.get("bill_date") or "")[:10]
                if (not query or med) and bill_date and bill_date < cutoff:
                    continue
                sale_id = _safe_int(r.get("id") or r.get("local_id"))
                if sale_id <= 0:
                    continue
                if med and sale_id not in server_matched:
                    try:
                        doc = r if r.get("items") else (get_doc("sales", sale_id) or {})
                        hit = False
                        for it in doc.get("items") or []:
                            if not isinstance(it, dict):
                                continue
                            mid = _safe_int(it.get("medicine_id"))
                            name = str(
                                it.get("medicine_name") or it.get("name") or ""
                            ).strip()
                            if not name and mid:
                                mp = medicine_by_id(mid) or {}
                                name = str(mp.get("name") or "").strip()
                            if name.lower() == med.lower():
                                hit = True
                                break
                        if not hit:
                            continue
                    except Exception:
                        continue
                cust = str(r.get("customer_name") or "")
                bill_no = str(r.get("bill_no") or "")
                bills.append(
                    {
                        "sale_id": sale_id,
                        "bill_no": bill_no,
                        "bill_date": bill_date,
                        "customer": cust,
                        "label": f"{bill_no} — {cust} ({bill_date})",
                    }
                )
                if len(bills) >= 80:
                    break
            return {"ok": True, "bills": bills}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "Online bill search failed."}

    _ensure_return_tables(conn)
    cur = conn.cursor()
    bills = []

    if med:
        days = load_sales_return_lookup_days()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        cur.execute(
            """
            SELECT s.id, s.bill_no, s.bill_date, c.name
            FROM sales s
            JOIN customers c ON s.customer_id = c.id
            JOIN sales_items si ON si.sale_id = s.id
            JOIN medicines m ON m.id = si.medicine_id
            WHERE COALESCE(s.deleted,0)=0 AND m.name = ?
              AND date(s.bill_date) >= date(?)
            GROUP BY s.id
            ORDER BY s.id DESC
            LIMIT 80
            """,
            (med, cutoff),
        )
    elif query:
        like = f"%{query}%"
        cur.execute(
            """
            SELECT s.id, s.bill_no, s.bill_date, c.name
            FROM sales s
            JOIN customers c ON s.customer_id = c.id
            WHERE COALESCE(s.deleted,0)=0
              AND (s.bill_no LIKE ? OR c.name LIKE ? OR s.bill_no = ?)
            ORDER BY s.id DESC
            LIMIT 80
            """,
            (like, like, query),
        )
    else:
        days = load_sales_return_lookup_days()
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        cur.execute(
            """
            SELECT s.id, s.bill_no, s.bill_date, c.name
            FROM sales s
            JOIN customers c ON s.customer_id = c.id
            WHERE COALESCE(s.deleted,0)=0 AND date(s.bill_date) >= date(?)
            ORDER BY s.id DESC
            LIMIT 80
            """,
            (cutoff,),
        )
    for r in cur.fetchall():
        bills.append(
            {
                "sale_id": int(r[0]),
                "bill_no": r[1],
                "bill_date": r[2],
                "customer": r[3],
                "label": f"{r[1]} — {r[3]} ({r[2]})",
            }
        )
    return {"ok": True, "bills": bills}


def load_sales_bill_for_return(conn, sale_id: int) -> dict[str, Any]:
    from core.customer_service import get_customer_due
    from core.layout_config import is_strip_count_type
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        try:
            from core.server_crud import get_doc
            from core.online_catalog import medicine_by_id, find_customer_by_id

            queued = False
            doc = get_doc("sales", int(sale_id)) or {}
            if not doc:
                try:
                    from core.online_mutation_queue import pending_by_local_id

                    pending = pending_by_local_id("sales", int(sale_id))
                    if pending:
                        queued = True
                        doc = dict(pending.get("payload") or {})
                        if not doc.get("items") and doc.get("medicines"):
                            doc["items"] = doc.get("medicines")
                except Exception:
                    doc = {}
            if not doc or _safe_int(doc.get("deleted")):
                return {"ok": False, "error": "Bill not found."}
            customer_id = _safe_int(doc.get("customer_id"))
            cust = find_customer_by_id(customer_id) if customer_id else {}
            cust = cust or {}
            customer_name = str(
                doc.get("customer_name") or cust.get("name") or ""
            )
            try:
                cust_due, cust_credit = get_customer_due(conn, customer_id)
            except Exception:
                cust_due = _safe_float(cust.get("total_due"))
                cust_credit = _safe_float(cust.get("total_credit"))

            # One returns pass for the whole bill, not one per line, read back to
            # the bill's own date -- never one capped page. Unreadable -> ok False.
            returned = _bill_returned_map(
                "sales_returns", "sale_id", int(sale_id), doc.get("bill_date"), doc, queued
            )
            items: list[dict[str, Any]] = []
            for it in doc.get("items") or []:
                if not isinstance(it, dict):
                    continue
                med_id = _safe_int(it.get("medicine_id"))
                mp = medicine_by_id(med_id) if med_id else {}
                mp = mp or {}
                med_name = str(
                    it.get("medicine_name") or it.get("name") or mp.get("name") or ""
                )
                batch = str(
                    it.get("batch_no") or it.get("batch") or mp.get("batch_no") or ""
                )
                med_type = str(it.get("type") or mp.get("type") or "")
                orig_qty = _safe_float(it.get("qty"))
                rate = _safe_float(it.get("rate"))
                amount = _safe_float(it.get("amount"))
                already = returned.get(med_id, 0.0) + _pending_returned_qty(
                    "sales_returns", "sale_id", int(sale_id), med_id
                )
                remaining = max(0.0, orig_qty - already)
                unit = str(it.get("unit") or mp.get("unit") or "1")
                items.append(
                    {
                        "medicine_id": med_id,
                        "name": med_name,
                        "batch": batch,
                        "orig_qty": orig_qty,
                        "remaining_qty": remaining,
                        "returned_qty": already,
                        "rate": rate,
                        "amount": amount,
                        "type": med_type,
                        "unit": unit,
                        "is_tablet": bool(is_strip_count_type(med_type)),
                        "tablets_per_stripe": _tablets_per_strip(med_type, unit),
                    }
                )
            items = _merge_repeated_lines(items)
            return {
                "ok": True,
                "sale_id": int(sale_id),
                "bill_no": str(doc.get("bill_no") or ""),
                "bill_date": str(doc.get("bill_date") or "")[:10],
                "discount": _safe_float(doc.get("discount")),
                "customer": customer_name,
                "customer_id": customer_id,
                "bill_total": _safe_float(doc.get("total_amount")),
                "bill_paid": _safe_float(doc.get("amount_paid")),
                "bill_due": _safe_float(doc.get("due_amount")),
                "previous_due": float(cust_due),
                "previous_credit": float(cust_credit),
                "items": items,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "Online bill load failed."}

    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, s.bill_no, s.bill_date, s.discount, c.name, c.id,
               COALESCE(s.total_amount,0), COALESCE(s.amount_paid,0),
               COALESCE(s.due_amount,0)
        FROM sales s JOIN customers c ON s.customer_id = c.id
        WHERE s.id=? AND COALESCE(s.deleted,0)=0
        """,
        (int(sale_id),),
    )
    row = cur.fetchone()
    if not row:
        return {"ok": False, "error": "Bill not found."}
    cust_due, cust_credit = get_customer_due(conn, int(row[5]))

    cur.execute(
        """
        SELECT m.name, COALESCE(m.batch_no,''), si.qty, si.rate, si.amount,
               COALESCE(m.type,''), si.medicine_id, COALESCE(m.unit,'1')
        FROM sales_items si
        JOIN medicines m ON si.medicine_id = m.id
        WHERE si.sale_id=?
        """,
        (int(sale_id),),
    )

    items = []
    for med_name, batch, orig_qty, rate, amount, med_type, med_id, unit in cur.fetchall():
        orig_qty = float(orig_qty or 0)
        cur.execute(
            """
            SELECT COALESCE(SUM(sri.qty), 0)
            FROM sales_return_items sri
            JOIN sales_returns sr ON sri.return_id = sr.id
            WHERE sr.sale_id=? AND sri.medicine_id=?
              AND COALESCE(sr.deleted,0)=0
            """,
            (int(sale_id), int(med_id)),
        )
        already = float(cur.fetchone()[0] or 0)
        remaining = max(0.0, orig_qty - already)
        items.append(
            {
                "medicine_id": int(med_id),
                "name": med_name,
                "batch": batch,
                "orig_qty": orig_qty,
                "remaining_qty": remaining,
                "returned_qty": already,
                "rate": float(rate or 0),
                "amount": float(amount or 0),
                "type": med_type,
                "unit": str(unit or "1"),
                "is_tablet": bool(is_strip_count_type(med_type)),
                "tablets_per_stripe": _tablets_per_strip(med_type, unit),
            }
        )
    items = _merge_repeated_lines(items)
    return {
        "ok": True,
        "sale_id": int(row[0]),
        "bill_no": row[1],
        "bill_date": row[2],
        "discount": float(row[3] or 0),
        "customer": row[4],
        "customer_id": int(row[5]),
        "bill_total": float(row[6] or 0),
        "bill_paid": float(row[7] or 0),
        "bill_due": float(row[8] or 0),
        "previous_due": float(cust_due),
        "previous_credit": float(cust_credit),
        "items": items,
    }


def _sales_returnable(conn, sale_id: int, online: bool) -> dict[int, tuple[str, float]] | None:
    """{medicine_id: (name, qty still returnable)} for a whole bill, summed over
    EVERY line of a medicine, less what earlier returns (saved or still queued)
    took back. None when the bill cannot be read -- the caller then keeps the
    screen's own check rather than refusing a return it cannot judge."""
    sold: dict[int, float] = {}
    names: dict[int, str] = {}
    returned: dict[int, float] = {}
    if online:
        from core.server_crud import get_doc

        queued = False
        doc = get_doc("sales", int(sale_id)) or {}
        if not doc:
            try:
                from core.online_mutation_queue import pending_by_local_id

                pending = pending_by_local_id("sales", int(sale_id))
                if pending:
                    queued = True
                    doc = dict(pending.get("payload") or {})
                    if not doc.get("items") and doc.get("medicines"):
                        doc["items"] = doc.get("medicines")
            except Exception:
                doc = {}
        if not doc:
            # get_doc answers None for an unreachable server as well as a missing
            # bill. Letting the save through here let a stale Returns screen
            # return the same strips twice whenever the link was down.
            raise ReturnsUnreadable(_UNREADABLE_BILL)
        for it in doc.get("items") or []:
            if not isinstance(it, dict):
                continue
            mid = _safe_int(it.get("medicine_id"))
            sold[mid] = sold.get(mid, 0.0) + _safe_float(it.get("qty"))
            names.setdefault(mid, str(it.get("medicine_name") or it.get("name") or ""))
        # A copy: the queued quantities are added to it below.
        # Raises ReturnsUnreadable; _sales_return_request_error refuses on it.
        returned = dict(_bill_returned_map(
            "sales_returns", "sale_id", int(sale_id), doc.get("bill_date"), doc, queued
        ))
        for mid in sold:
            returned[mid] = returned.get(mid, 0.0) + _pending_returned_qty(
                "sales_returns", "sale_id", int(sale_id), mid
            )
    else:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT si.medicine_id, COALESCE(m.name,''), COALESCE(SUM(si.qty),0)
            FROM sales_items si LEFT JOIN medicines m ON m.id = si.medicine_id
            WHERE si.sale_id=? GROUP BY si.medicine_id
            """,
            (int(sale_id),),
        )
        for mid, name, qty in cur.fetchall():
            sold[int(mid)] = float(qty or 0)
            names[int(mid)] = str(name or "")
        cur.execute(
            """
            SELECT sri.medicine_id, COALESCE(SUM(sri.qty),0)
            FROM sales_return_items sri JOIN sales_returns sr ON sri.return_id = sr.id
            WHERE sr.sale_id=? AND COALESCE(sr.deleted,0)=0
            GROUP BY sri.medicine_id
            """,
            (int(sale_id),),
        )
        for mid, qty in cur.fetchall():
            returned[int(mid)] = float(qty or 0)
    return {
        mid: (names.get(mid, ""), max(0.0, round(qty - returned.get(mid, 0.0), 6)))
        for mid, qty in sold.items()
    }


def _sales_return_request_error(conn, sale_id: int, body: dict[str, Any], online: bool) -> str:
    """Why this sales return must not be saved, or "" when it may.

    Both return screens check the discount and the quantity, but only against
    the bill as it stood when they loaded it, and both stay loaded: Returns
    keeps its form while the shop goes to Sales, where the Sales Return popup
    (Alt+R) can return the same medicines. F5 back on Returns then saved that
    stock a second time and refunded it twice. So ask again here, at save time,
    from the store itself -- the server when Online.
    """
    raw = body.get("discount")
    try:
        disc = 0.0 if raw is None or str(raw).strip() == "" else float(raw)
    except (TypeError, ValueError):
        disc = float("nan")
    # calc_return_refund takes this as a percentage and clamps nothing: -50
    # refunded 150% of the goods, 150 saved a negative refund onto the due.
    if not (0.0 <= disc <= 100.0):
        return "Discount % must be between 0 and 100."
    wanted: dict[int, float] = {}
    labels: dict[int, str] = {}
    for it in body.get("items") or []:
        if not isinstance(it, dict):
            continue
        qty = _safe_float(it.get("qty"))
        if qty <= 0:
            continue
        mid = _safe_int(it.get("medicine_id"))
        wanted[mid] = wanted.get(mid, 0.0) + qty
        labels.setdefault(mid, str(it.get("name") or ""))
        from core.layout_config import is_strip_count_type

        # A sale counts a strip-counted medicine in tablets.
        part = _part_tablet_error(
            str(it.get("name") or ""),
            qty,
            is_tablet=bool(it.get("is_tablet")) or is_strip_count_type(str(it.get("type") or "")),
            tps=1,
            in_tablets=True,
        )
        if part:
            return part
    if not wanted:
        return ""
    try:
        left = _sales_returnable(conn, sale_id, online)
    except ReturnsUnreadable as exc:
        # The bill is there but its earlier returns could not be read: that is
        # not "none returned", so do not let the save through on it.
        return str(exc)
    except Exception:
        left = None
    if left is None:
        return ""
    for mid, qty in wanted.items():
        if mid not in left:
            return f"{labels.get(mid) or 'This medicine'} is not on this bill."
        name, rem = left[mid]
        if qty > rem + 1e-6:
            return (
                f"Cannot return more than {rem:g} for {name or labels.get(mid) or 'this medicine'}"
                " — the rest of this bill has already been returned. Load the bill again."
            )
    return ""


def save_sales_return(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.calc_engine import calc_return_refund
    from core.customer_service import recalculate_customer_due
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        import uuid as _uuid

        from core.online_catalog import (
            find_customer_by_id,
            patch_customer_cache,
            patch_docs,
        )

        sale_id = _safe_int(body.get("sale_id"))
        customer_id = _safe_int(body.get("customer_id"))
        if sale_id <= 0:
            return {"ok": False, "error": "Load a bill first."}
        why = _sales_return_request_error(conn, sale_id, body, online=True)
        if why:
            return {"ok": False, "error": why}
        return_items = []
        calc_items = []
        for it in body.get("items") or []:
            if not isinstance(it, dict):
                continue
            qty = _safe_float(it.get("qty"))
            if qty <= 0:
                continue
            rate = _safe_float(it.get("rate"))
            mid = _safe_int(it.get("medicine_id"))
            line = {
                "medicine_id": mid,
                "name": str(it.get("name") or ""),
                "qty": qty,
                "rate": rate,
                "amount": round(qty * rate, 2),
            }
            return_items.append(line)
            calc_line = dict(line)
            orig_qty = _safe_float(it.get("orig_qty"))
            orig_amount = _safe_float(it.get("orig_amount") or it.get("line_amount"))
            if orig_qty > 0 and orig_amount > 0:
                calc_line["orig_qty"] = orig_qty
                calc_line["amount"] = orig_amount
            calc_items.append(calc_line)
        if not return_items:
            return {"ok": False, "error": "Add at least one return line."}
        disc = _safe_float(body.get("discount"))
        result = calc_return_refund(calc_items, disc)
        refund = float(result.get("refund_amount") or 0)
        settle = str(body.get("settle_mode") or body.get("refund_settle") or "ledger").strip().lower()
        if settle not in ("ledger", "cash", "online"):
            settle = "ledger"
        customer_name = str(body.get("customer_name") or "").strip()
        bill_no = _normalize_bill_search_q(str(body.get("bill_no") or ""))
        reason = str(body.get("reason") or "").strip()
        cu = str(_uuid.uuid4())
        from core.online_mutation_queue import enqueue, temp_id_from_uuid

        rid_guess, _is_temp = _return_id_for("sales_returns", cu)
        return_no = f"SR{abs(rid_guess)}"
        med_docs = _sales_return_stock_docs(return_items, cu)
        party = []
        payout = 0.0
        due2 = 0.0
        credit2 = 0.0
        # A return whose customer_id is missing used to save happily and then
        # skip the ledger entirely -- stock came back but the customer kept
        # owing the refunded amount, silently. The bill being returned always
        # knows its customer, so fall back to that rather than writing 0.
        if customer_id <= 0 and sale_id > 0:
            try:
                from core.server_crud import get_doc

                _sale = get_doc("sales", sale_id) or {}
                customer_id = _safe_int(_sale.get("customer_id"))
            except Exception:
                # Leave customer_id at 0; the return still saves and stock still
                # returns, exactly as before this fallback existed.
                customer_id = 0
        cust = find_customer_by_id(customer_id) if customer_id > 0 else {}
        cust = cust or {}
        if not customer_name:
            customer_name = str(cust.get("name") or "")
        if customer_id > 0:
            due = round(float(cust.get("total_due") or 0), 2)
            credit = round(float(cust.get("total_credit") or 0), 2)
            due2, credit2, payout = _settle_customer_after_refund(
                due, credit, refund, settle
            )
            party.append(
                {
                    "id": customer_id,
                    "local_id": customer_id,
                    "name": customer_name or cust.get("name") or "",
                    "phone": cust.get("phone") or "",
                    "address": cust.get("address") or "",
                    "total_due": due2,
                    "total_credit": credit2,
                }
            )
        row = enqueue(
            collection="sales_returns",
            op="upsert",
            # Use the id we just allocated. Without this the queue minted its
            # own negative temp id and stored THAT as the permanent local_id.
            local_id=rid_guess,
            payload={
                "sale_id": sale_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "bill_no": bill_no,
                "return_no": return_no,
                "return_date": str(date.today()),
                "discount": disc,
                "refund_amount": refund,
                "reason": reason,
                "settle_mode": settle,
                "refund_payout": payout,
                "items": return_items,
                # When the return was made: the PC never sent it.
                "created_at": _made_now(),
                "_customers": party,
                "_medicines": med_docs,
            },
            client_uuid=cu,
        )
        rid = int(row.get("local_id") or rid_guess or 0)
        if payout > 0 and customer_id > 0:
            mode = "online" if settle == "online" else "cash"
            enqueue(
                collection="customer_payments",
                op="upsert",
                payload={
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "payment_date": str(date.today()),
                    "amount": -payout,
                    "payment_mode": mode,
                    "cash_amount": -payout if mode == "cash" else 0,
                    "online_amount": -payout if mode == "online" else 0,
                    "reference_no": return_no,
                    "note": f"Refund {return_no} {bill_no}".strip(),
                    "created_at": _made_now(),
                    "_fifo": "customer",
                    "_customers": party,
                },
            )
        try:
            if med_docs:
                patch_docs("medicines", med_docs)
        except Exception:
            pass
        if party:
            try:
                patch_customer_cache(party[0])
            except Exception:
                pass
        _notify_return_saved(customer=customer_id > 0)
        return {
            "ok": True,
            "return_id": rid,
            "return_no": return_no,
            "refund_amount": refund,
            "refund_payout": payout,
            "settle_mode": settle,
            "queued": True,
        }

    blocked = _guard_mutate()
    if blocked:
        return blocked

    if is_online_mode():
        from core.server_crud import upsert_return_online, allocate_id, get_doc, _meta, upsert_contact_online
        from core.online_catalog import find_customer_by_id, invalidate

        sale_id = _safe_int(body.get("sale_id"))
        customer_id = _safe_int(body.get("customer_id"))
        if sale_id <= 0:
            return {"ok": False, "error": "Load a bill first."}
        return_items = []
        med_docs = []
        for it in body.get("items") or []:
            if not isinstance(it, dict):
                continue
            qty = _safe_float(it.get("qty"))
            if qty <= 0:
                continue
            rate = _safe_float(it.get("rate"))
            mid = _safe_int(it.get("medicine_id"))
            return_items.append(
                {"medicine_id": mid, "qty": qty, "rate": rate, "amount": round(qty * rate, 2)}
            )
            if mid > 0:
                mp = get_doc("medicines", mid) or {"id": mid, "local_id": mid, "stock_qty": 0}
                mp = _meta(mp)
                mp["id"] = mid
                mp["local_id"] = mid
                try:
                    mp["stock_qty"] = float(mp.get("stock_qty") or 0) + qty
                except Exception:
                    pass
                med_docs.append(mp)
        if not return_items:
            return {"ok": False, "error": "Add at least one return line."}
        disc = _safe_float(body.get("discount"))
        result = calc_return_refund(return_items, disc)
        refund = float(result.get("refund_amount") or 0)
        rid = allocate_id("sales_returns")
        upsert_return_online(
            "sales_returns",
            {
                "id": rid,
                "local_id": rid,
                "return_no": f"SR{rid}",
                "sale_id": sale_id,
                "customer_id": customer_id,
                "customer_name": str(body.get("customer_name") or ""),
                "bill_no": str(body.get("bill_no") or ""),
                "discount": disc,
                "refund_amount": refund,
                "reason": str(body.get("reason") or ""),
                "items": return_items,
                "_medicines": med_docs,
            },
        )
        if customer_id > 0 and refund > 0:
            cust = find_customer_by_id(customer_id) or get_doc("customers", customer_id) or {}
            if cust:
                due = round(float(cust.get("total_due") or 0), 2)
                credit = round(float(cust.get("total_credit") or 0), 2)
                if due >= refund:
                    due = round(due - refund, 2)
                else:
                    credit = round(credit + (refund - due), 2)
                    due = 0.0
                upsert_contact_online(
                    "customers",
                    {
                        "id": customer_id,
                        "local_id": customer_id,
                        "name": cust.get("name") or body.get("customer_name") or "",
                        "phone": cust.get("phone") or "",
                        "address": cust.get("address") or "",
                        "total_due": due,
                        "total_credit": credit,
                    },
                )
                invalidate("customers")
        try:
            invalidate("sales_returns")
            invalidate("medicines")
        except Exception:
            pass
        return {"ok": True, "return_id": rid, "return_no": f"SR{rid}", "refund_amount": refund}

    _ensure_return_tables(conn)
    sale_id = _safe_int(body.get("sale_id"))
    customer_id = _safe_int(body.get("customer_id"))
    if sale_id <= 0:
        return {"ok": False, "error": "Load a bill first."}
    why = _sales_return_request_error(conn, sale_id, body, online=False)
    if why:
        return {"ok": False, "error": why}
    raw_items = body.get("items") or []
    return_items: list[dict[str, Any]] = []
    calc_items: list[dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        qty = _safe_float(it.get("qty"))
        if qty <= 0:
            continue
        rate = _safe_float(it.get("rate"))
        line = {
            "medicine_id": _safe_int(it.get("medicine_id")),
            "qty": qty,
            "rate": rate,
            "amount": round(qty * rate, 2),
        }
        return_items.append(line)
        calc_line = dict(line)
        orig_qty = _safe_float(it.get("orig_qty"))
        orig_amount = _safe_float(it.get("orig_amount") or it.get("line_amount"))
        if orig_qty > 0 and orig_amount > 0:
            calc_line["orig_qty"] = orig_qty
            calc_line["amount"] = orig_amount
        calc_items.append(calc_line)
    if not return_items:
        return {"ok": False, "error": "Add at least one return line."}

    disc = _safe_float(body.get("discount"))
    reason = str(body.get("reason") or "").strip()
    settle = str(body.get("settle_mode") or body.get("refund_settle") or "ledger").strip().lower()
    if settle not in ("ledger", "cash", "online"):
        settle = "ledger"
    result = calc_return_refund(calc_items, disc)
    refund = float(result.get("refund_amount") or 0)
    payout = 0.0
    if customer_id:
        from core.customer_service import get_customer_due

        due, credit = get_customer_due(conn, customer_id)
        _, _, payout = _settle_customer_after_refund(due, credit, refund, settle)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM sales_returns")
        return_no = f"SR{cur.fetchone()[0]}"
        cur.execute(
            """
            INSERT INTO sales_returns
                (return_no, sale_id, customer_id, return_date,
                 refund_amount, discount, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                return_no,
                sale_id,
                customer_id,
                str(date.today()),
                refund,
                disc,
                reason,
            ),
        )
        return_id = int(cur.lastrowid)
        for item in return_items:
            cur.execute(
                """
                INSERT INTO sales_return_items
                    (return_id, medicine_id, qty, rate, amount)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    return_id,
                    item["medicine_id"],
                    item["qty"],
                    item["rate"],
                    item["amount"],
                ),
            )
            cur.execute(
                "UPDATE medicines SET stock_qty = stock_qty + ? WHERE id = ?",
                (item["qty"], item["medicine_id"]),
            )
        if payout > 0 and customer_id:
            mode = "online" if settle == "online" else "cash"
            cur.execute(
                """
                INSERT INTO customer_payments
                    (customer_id, payment_date, amount, payment_mode,
                     cash_amount, online_amount, reference_no, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    str(date.today()),
                    -payout,
                    mode,
                    -payout if mode == "cash" else 0,
                    -payout if mode == "online" else 0,
                    return_no,
                    f"Refund {return_no}",
                ),
            )
        conn.commit()
        if customer_id:
            recalculate_customer_due(conn, customer_id)
        try:
            from core.sync_coordinator import after_sales_return_saved

            after_sales_return_saved(conn, return_id, customer_id)
        except Exception:
            pass
        return {
            "ok": True,
            "return_id": return_id,
            "return_no": return_no,
            "refund_amount": refund,
            "refund_payout": payout,
            "settle_mode": settle,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to save return: {exc}"}


def search_purchases_for_return(conn, q: str = "") -> dict[str, Any]:
    from core.sync_prefs import is_online_mode

    query = _normalize_bill_search_q(q)
    if is_online_mode():
        try:
            from core import store_query_client as sq
            from core.online_mutation_queue import (
                merge_server_rows,
                overlay_purchase_dicts,
            )

            data = sq.list_purchases(q=query, limit=300) or {}
            listed = merge_server_rows(
                list(data.get("rows") or []),
                overlay_purchase_dicts(),
                collection="purchases",
            )
            purchases = []
            for r in listed:
                if not isinstance(r, dict):
                    continue
                pid = _safe_int(r.get("id") or r.get("local_id"))
                if pid <= 0:
                    continue
                bill_label = str(
                    r.get("bill_number") or r.get("purchase_no") or ""
                )
                pdate = str(r.get("purchase_date") or "")[:10]
                supplier = str(r.get("supplier_name") or "")
                purchases.append(
                    {
                        "purchase_id": pid,
                        "bill_label": bill_label,
                        "purchase_date": pdate,
                        "supplier": supplier,
                        "label": f"{bill_label} — {supplier} ({pdate})",
                    }
                )
                if len(purchases) >= 80:
                    break
            return {"ok": True, "purchases": purchases}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "Online purchase search failed."}

    _ensure_return_tables(conn)
    cur = conn.cursor()
    if query:
        like = f"%{query}%"
        cur.execute(
            """
            SELECT p.id, COALESCE(p.bill_number, p.purchase_no), p.purchase_date, s.name
            FROM purchases p
            JOIN suppliers s ON p.supplier_id = s.id
            WHERE COALESCE(p.deleted,0)=0
              AND (p.purchase_no LIKE ? OR p.bill_number LIKE ? OR s.name LIKE ?)
            ORDER BY p.id DESC
            LIMIT 80
            """,
            (like, like, like),
        )
    else:
        cur.execute(
            """
            SELECT p.id, COALESCE(p.bill_number, p.purchase_no), p.purchase_date, s.name
            FROM purchases p
            JOIN suppliers s ON p.supplier_id = s.id
            WHERE COALESCE(p.deleted,0)=0
            ORDER BY p.id DESC
            LIMIT 80
            """
        )
    purchases = [
        {
            "purchase_id": int(r[0]),
            "bill_label": r[1],
            "purchase_date": r[2],
            "supplier": r[3],
            "label": f"{r[1]} — {r[3]} ({r[2]})",
        }
        for r in cur.fetchall()
    ]
    return {"ok": True, "purchases": purchases}


def _online_purchase_return_items(
    purchase_id: int, doc: dict, returned: dict[int, float]
) -> list[dict[str, Any]]:
    """The lines of a server purchase document, each with what is still
    returnable (`returned` from the server plus anything still queued).
    Shared by load_purchase_for_return and the online bulk list."""
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.online_catalog import medicine_by_id

    items: list[dict[str, Any]] = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        med_id = _safe_int(it.get("medicine_id"))
        mp = medicine_by_id(med_id) if med_id else {}
        mp = mp or {}
        med_type = str(it.get("type") or mp.get("type") or "")
        unit = str(it.get("unit") or mp.get("unit") or "1")
        is_tablet = is_strip_count_type(med_type)
        tps = parse_tablets_per_stripe(unit) if is_tablet else 1
        if tps <= 0:
            tps = 1
        orig_qty = _safe_float(it.get("qty"))
        already = returned.get(med_id, 0.0) + _pending_returned_qty(
            "purchase_returns", "purchase_id", int(purchase_id), med_id
        )
        remaining = max(0.0, orig_qty - already)
        items.append(
            {
                "medicine_id": med_id,
                "name": str(
                    it.get("medicine_name")
                    or it.get("name")
                    or mp.get("name")
                    or ""
                ),
                "batch": str(
                    it.get("batch_no")
                    or it.get("batch")
                    or mp.get("batch_no")
                    or ""
                ),
                "orig_qty": orig_qty,
                "remaining_qty": remaining,
                "returned_qty": already,
                "rate": _safe_float(it.get("rate")),
                "amount": _safe_float(it.get("amount")),
                "type": med_type,
                "tablets_per_stripe": tps,
                "is_tablet": is_tablet,
            }
        )
    return _merge_repeated_lines(items)


def load_purchase_for_return(conn, purchase_id: int) -> dict[str, Any]:
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.purchase_service import get_supplier_due
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        try:
            from core.server_crud import get_doc
            from core.online_catalog import medicine_by_id

            doc = get_doc("purchases", int(purchase_id)) or {}
            if not doc or _safe_int(doc.get("deleted")):
                return {"ok": False, "error": "Purchase not found."}
            supplier_id = _safe_int(doc.get("supplier_id"))
            supplier_name = str(doc.get("supplier_name") or "")
            if not supplier_name and supplier_id:
                from core.online_catalog import find_supplier_by_id
                found = find_supplier_by_id(supplier_id)
                if found:
                    supplier_name = str(found.get("name") or "")
            try:
                supp_due, supp_credit = get_supplier_due(conn, supplier_name)
            except Exception:
                supp_due, supp_credit = 0.0, 0.0

            # One returns pass for the whole bill, not one per line.
            returned = _online_returned_map(
                "purchase_returns", "purchase_id", int(purchase_id),
                floor=_returns_floor(doc.get("purchase_date"), doc.get("created_at")),
            )
            items = _online_purchase_return_items(int(purchase_id), doc, returned)
            bill_label = str(
                doc.get("bill_number") or doc.get("purchase_no") or ""
            )
            return {
                "ok": True,
                "purchase_id": int(purchase_id),
                "bill_label": bill_label,
                "purchase_date": str(doc.get("purchase_date") or "")[:10],
                "supplier": supplier_name,
                "supplier_id": supplier_id,
                "bill_total": _safe_float(
                    doc.get("final_amount") or doc.get("total_amount")
                ),
                "bill_paid": _safe_float(
                    doc.get("amount_paid_at_entry") or doc.get("amount_paid")
                ),
                "bill_due": _safe_float(doc.get("due_amount") or doc.get("due")),
                "previous_due": float(supp_due),
                "previous_credit": float(supp_credit),
                "items": items,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "Online purchase load failed."}

    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, COALESCE(p.bill_number, p.purchase_no), p.purchase_date,
               s.name, s.id,
               COALESCE(p.final_amount, p.total_amount, 0),
               COALESCE(p.amount_paid_at_entry, p.amount_paid, 0),
               COALESCE(p.due_amount, p.due, 0)
        FROM purchases p
        JOIN suppliers s ON p.supplier_id = s.id
        WHERE p.id=? AND COALESCE(p.deleted,0)=0
        """,
        (int(purchase_id),),
    )
    row = cur.fetchone()
    if not row:
        return {"ok": False, "error": "Purchase not found."}
    supp_due, supp_credit = get_supplier_due(conn, str(row[3] or ""))

    cur.execute(
        """
        SELECT pi.medicine_id, m.name, COALESCE(m.batch_no,''), pi.qty,
               pi.rate, pi.amount, COALESCE(m.type,''), COALESCE(m.unit,'1')
        FROM purchase_items pi
        JOIN medicines m ON pi.medicine_id = m.id
        WHERE pi.purchase_id=?
        """,
        (int(purchase_id),),
    )
    items = []
    for med_id, name, batch, qty, rate, amount, med_type, unit in cur.fetchall():
        is_tablet = is_strip_count_type(med_type or "")
        tps = parse_tablets_per_stripe(unit or "1") if is_tablet else 1
        if tps <= 0:
            tps = 1
        orig_qty = float(qty or 0)
        cur.execute(
            """
            SELECT COALESCE(SUM(pri.qty), 0)
            FROM purchase_return_items pri
            JOIN purchase_returns pr ON pri.return_id = pr.id
            WHERE pr.purchase_id=? AND pri.medicine_id=?
              AND COALESCE(pr.deleted,0)=0
            """,
            (int(purchase_id), int(med_id)),
        )
        already = float(cur.fetchone()[0] or 0)
        remaining = max(0.0, orig_qty - already)
        items.append(
            {
                "medicine_id": int(med_id),
                "name": name,
                "batch": batch,
                "orig_qty": orig_qty,
                "remaining_qty": remaining,
                "returned_qty": already,
                "rate": float(rate or 0),
                "amount": float(amount or 0),
                "type": med_type,
                "tablets_per_stripe": tps,
                "is_tablet": is_tablet,
            }
        )
    items = _merge_repeated_lines(items)
    return {
        "ok": True,
        "purchase_id": int(row[0]),
        "bill_label": row[1],
        "purchase_date": row[2],
        "supplier": row[3],
        "supplier_id": int(row[4]),
        "bill_total": float(row[5] or 0),
        "bill_paid": float(row[6] or 0),
        "bill_due": float(row[7] or 0),
        "previous_due": float(supp_due),
        "previous_credit": float(supp_credit),
        "items": items,
    }


_UNREADABLE_BILL = (
    "Could not read this bill from the server, so what is left to return on it "
    "cannot be worked out. Try again in a moment."
)


def _purchase_returnable(
    conn, purchase_id: int, online: bool
) -> dict[int, tuple[str, float]] | None:
    """{medicine_id: (name, qty still returnable)} for a whole purchase bill, in
    the bill's own units, summed over every line of a medicine, less every
    earlier return (saved, or still queued Online). Offline None when the bill is
    not in the database. Online RAISES ReturnsUnreadable when the bill or its
    earlier returns cannot be read: an unreachable server is not "nothing
    returned"."""
    bought: dict[int, float] = {}
    names: dict[int, str] = {}
    returned: dict[int, float] = {}
    pid = int(purchase_id)
    if online:
        from core.server_crud import get_doc

        queued = False
        doc = get_doc("purchases", pid) or {}
        if not doc:
            try:
                from core.online_mutation_queue import pending_by_local_id

                pending = pending_by_local_id("purchases", pid)
                if pending:
                    queued = True
                    doc = dict(pending.get("payload") or {})
                    if not doc.get("items") and doc.get("medicines"):
                        doc["items"] = doc.get("medicines")
            except Exception:
                doc = {}
        if not doc:
            raise ReturnsUnreadable(_UNREADABLE_BILL)
        for it in doc.get("items") or []:
            if not isinstance(it, dict):
                continue
            mid = _safe_int(it.get("medicine_id"))
            bought[mid] = bought.get(mid, 0.0) + _safe_float(it.get("qty"))
            names.setdefault(mid, str(it.get("medicine_name") or it.get("name") or ""))
        returned = dict(_bill_returned_map(
            "purchase_returns", "purchase_id", pid, doc.get("purchase_date"), doc, queued
        ))
        for mid in bought:
            returned[mid] = returned.get(mid, 0.0) + _pending_returned_qty(
                "purchase_returns", "purchase_id", pid, mid
            )
    else:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM purchases WHERE id=? AND COALESCE(deleted,0)=0", (pid,)
            )
            if not cur.fetchone():
                return None
            cur.execute(
                """
                SELECT pi.medicine_id, COALESCE(m.name,''), COALESCE(SUM(pi.qty),0)
                FROM purchase_items pi LEFT JOIN medicines m ON m.id = pi.medicine_id
                WHERE pi.purchase_id=? GROUP BY pi.medicine_id
                """,
                (pid,),
            )
            for mid, name, qty in cur.fetchall():
                bought[int(mid)] = float(qty or 0)
                names[int(mid)] = str(name or "")
            cur.execute(
                """
                SELECT pri.medicine_id, COALESCE(SUM(pri.qty),0)
                FROM purchase_return_items pri
                JOIN purchase_returns pr ON pri.return_id = pr.id
                WHERE pr.purchase_id=? AND COALESCE(pr.deleted,0)=0
                GROUP BY pri.medicine_id
                """,
                (pid,),
            )
            for mid, qty in cur.fetchall():
                returned[int(mid)] = float(qty or 0)
        except Exception:
            return None
    return {
        mid: (names.get(mid, ""), max(0.0, round(qty - returned.get(mid, 0.0), 6)))
        for mid, qty in bought.items()
    }


def _tablets_per_strip(med_type: Any, unit: Any) -> int:
    """Tablets in one strip of a strip-counted type; 1 for anything else."""
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    if not is_strip_count_type(str(med_type or "")):
        return 1
    try:
        tps = int(parse_tablets_per_stripe(str(unit or "1")))
    except Exception:
        tps = 1
    return tps if tps > 0 else 1


def _merge_repeated_lines(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per medicine on a return screen (owner, 2026-09-11).

    A bill saved before lines merged can carry the same medicine twice. Each
    copy used to show its OWN qty less the WHOLE medicine's returns, so 5 + 5
    with 3 back showed 2 + 2 returnable (the shop was owed 7), and the return
    list refused the second copy as a duplicate. The bill itself is history and
    is not rewritten; the screen gets one line: the total qty, what is left of
    that total, and the refund at the amount-weighted average rate."""
    merged: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for it in items:
        mid = _safe_int(it.get("medicine_id"))
        qty = _safe_float(it.get("orig_qty"))
        cur = merged.get(mid)
        if cur is None:
            cur = dict(it)
            cur["_lines"] = 1
            cur["_rate_qty"] = _safe_float(it.get("rate")) * qty
            merged[mid] = cur
            order.append(mid)
            continue
        cur["_lines"] += 1
        cur["_rate_qty"] += _safe_float(it.get("rate")) * qty
        cur["orig_qty"] = _safe_float(cur.get("orig_qty")) + qty
        cur["amount"] = _safe_float(cur.get("amount")) + _safe_float(it.get("amount"))
    out: list[dict[str, Any]] = []
    for mid in order:
        it = merged[mid]
        lines = it.pop("_lines")
        rate_qty = it.pop("_rate_qty")
        if lines > 1:
            total = round(_safe_float(it.get("orig_qty")), 6)
            amount = round(_safe_float(it.get("amount")), 2)
            # `rate` keeps what one line's rate means: the bill rate, weighted by
            # qty. A PURCHASE refund is qty x rate, so rate = amount/qty credited
            # whatever the stored amount carries (GST, discount) -- more than one
            # line of the same bill would. A SALES refund goes through
            # amount/orig_qty (calc_return_refund), which is the amount-weighted
            # average the owner asked for. Android's ReturnLines.merge does both.
            if total > 0:
                it["rate"] = round(rate_qty / total, 4)
            it["orig_qty"] = total
            it["amount"] = amount
            it["remaining_qty"] = max(
                0.0, round(total - _safe_float(it.get("returned_qty")), 6)
            )
            it["merged_lines"] = lines
        out.append(it)
    return out


_BILL_KINDS = {
    # kind: (bill collection, returns table, parent key, date field, return items table)
    "sale": ("sales", "sales_returns", "sale_id", "bill_date", "sales_return_items"),
    "purchase": (
        "purchases", "purchase_returns", "purchase_id", "purchase_date",
        "purchase_return_items",
    ),
}

_UNREADABLE_RETURNS_FOR_EDIT = (
    "Could not read the returns against this bill, so this edit cannot be "
    "checked against them. Try again in a moment."
)


def returned_on_bill(
    conn, kind: str, bill_id: int, *, online: bool | None = None
) -> dict[int, tuple[str, float]]:
    """{medicine_id: (name, qty already returned)} against one saved bill.

    Online the returns are read to the end (and what is still queued is added);
    RAISES ReturnsUnreadable when the bill or its returns cannot be read."""
    bills, coll, key, date_key, ret_items = _BILL_KINDS[kind]
    bid = int(bill_id)
    if online is None:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    out: dict[int, tuple[str, float]] = {}
    if online:
        from core.server_crud import get_doc

        queued = False
        doc = get_doc(bills, bid) or {}
        if not doc:
            try:
                from core.online_mutation_queue import pending_by_local_id

                pending = pending_by_local_id(bills, bid)
                if pending:
                    queued = True
                    doc = dict(pending.get("payload") or {})
                    if not doc.get("items") and doc.get("medicines"):
                        doc["items"] = doc.get("medicines")
            except Exception:
                doc = {}
        if not doc:
            raise ReturnsUnreadable(_UNREADABLE_BILL)
        names: dict[int, str] = {}
        for it in doc.get("items") or []:
            if isinstance(it, dict):
                names.setdefault(
                    _safe_int(it.get("medicine_id")),
                    str(it.get("medicine_name") or it.get("name") or ""),
                )
        returned = dict(_bill_returned_map(coll, key, bid, doc.get(date_key), doc, queued))
        for mid in set(names) | set(returned):
            qty = returned.get(mid, 0.0) + _pending_returned_qty(coll, key, bid, mid)
            if qty > 1e-9:
                out[mid] = (names.get(mid, ""), round(qty, 6))
        return out
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT ri.medicine_id, COALESCE(m.name,''), COALESCE(SUM(ri.qty),0)
            FROM {ret_items} ri JOIN {coll} r ON ri.return_id = r.id
            LEFT JOIN medicines m ON m.id = ri.medicine_id
            WHERE r.{key}=? AND COALESCE(r.deleted,0)=0
            GROUP BY ri.medicine_id
            """,
            (bid,),
        )
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return {}  # no return was ever saved on this PC
        raise
    for mid, name, qty in cur.fetchall():
        if _safe_float(qty) > 1e-9:
            out[int(mid)] = (str(name or ""), round(_safe_float(qty), 6))
    return out


def _line_medicine_id(it: dict[str, Any], id_key: str) -> int:
    mid = _safe_int(it.get(id_key))
    if not mid and id_key != "id":
        mid = _safe_int(it.get("id"))
    return mid


def edit_returns_info(
    conn, kind: str, bill_id: int, items: list[dict[str, Any]], *, id_key: str
) -> dict[str, Any]:
    """What a saved bill opened for EDIT must show about returns against it:
    a note for the bill and `returned_qty` on every affected line (added to
    `items` in place). Never fails the load: unreadable returns are said so."""
    try:
        returned = returned_on_bill(conn, kind, bill_id)
    except Exception:
        return {
            "returned_by_medicine": {},
            "returns_unread": True,
            "returns_note": _UNREADABLE_RETURNS_FOR_EDIT,
        }
    line_names: dict[int, str] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        mid = _line_medicine_id(it, id_key)
        line_names.setdefault(mid, str(it.get("name") or it.get("medicine") or ""))
        if mid in returned:
            it["returned_qty"] = returned[mid][1]
    if not returned:
        return {"returned_by_medicine": {}, "returns_unread": False, "returns_note": ""}
    parts = [
        f"{line_names.get(mid) or name or f'medicine {mid}'} {qty:g}"
        for mid, (name, qty) in sorted(returned.items())
    ]
    return {
        "returned_by_medicine": {str(mid): qty for mid, (_n, qty) in returned.items()},
        "returns_unread": False,
        "returns_note": (
            "Returns were saved against this bill (returned: " + ", ".join(parts)
            + "). A line cannot go below what was returned, or be removed."
        ),
    }


def edit_below_returned_error(
    conn, kind: str, bill_id: int, lines: list[dict[str, Any]], *, id_key: str
) -> str:
    """Why this edit of a saved bill must not be saved, or "" when it may: no
    medicine may end up below what was already returned against the bill (or
    be taken off it). The refund was paid on those units; cutting the line
    under them would charge the customer / supplier for goods already back."""
    try:
        returned = returned_on_bill(conn, kind, bill_id)
    except ReturnsUnreadable:
        return _UNREADABLE_RETURNS_FOR_EDIT
    except Exception:
        return _UNREADABLE_RETURNS_FOR_EDIT
    if not returned:
        return ""
    kept: dict[int, float] = {}
    names: dict[int, str] = {}
    for it in lines or []:
        if not isinstance(it, dict):
            continue
        mid = _line_medicine_id(it, id_key)
        if mid <= 0:
            continue
        kept[mid] = kept.get(mid, 0.0) + _safe_float(it.get("qty"))
        names.setdefault(mid, str(it.get("name") or it.get("medicine") or ""))
    for mid, (name, qty) in sorted(returned.items()):
        left = kept.get(mid, 0.0)
        if left + 1e-6 >= qty:
            continue
        label = names.get(mid) or name or f"Medicine {mid}"
        if left <= 0:
            return (
                f"{label}: {qty:g} already returned against this bill, so its line "
                "cannot be removed. Delete that return first if the bill really changed."
            )
        return (
            f"{label}: {qty:g} already returned against this bill, so the line "
            f"cannot go below {qty:g} (it is now {left:g})."
        )
    return ""


def _part_tablet_error(name: str, qty: float, *, is_tablet: bool, tps: int, in_tablets: bool) -> str:
    """One tablet is the smallest unit a return may carry (owner, 2026-09-11):
    2.5 strips of 10 is fine (25 tablets), 2.55 strips is not."""
    if not is_tablet or qty <= 0:
        return ""
    tablets = qty if in_tablets else qty * max(1, int(tps or 1))
    if abs(tablets - round(tablets)) <= 1e-6:
        return ""
    shown = f"{qty:g} tablets" if in_tablets else f"{qty:g} strips = {tablets:g} tablets"
    return (
        f"{name or 'This medicine'}: {shown}. One tablet is the smallest unit "
        "-- return whole tablets only."
    )


def _purchase_return_request_error(
    conn, purchase_id: int, items: list[dict[str, Any]], online: bool
) -> str:
    """Why this purchase return must not be saved, or "" when it may.

    Every screen that saves one (Returns -> Purchase, the bulk tab, the alert
    popup's Return, Inventory's Return expired) checked the quantity only against
    the bill as it stood when it loaded, and all of them stay loaded. A second
    save against the same bill -- another tab, another PC, the same popup before
    its reload came back -- sent the same strips to the supplier twice: a second
    credit on the ledger and a second stock-out. So ask the store again here.
    (replace_purchase_return runs its own check and marks its bodies
    _replace_checked: the queue does not net out its queued delete of the old
    return.)"""
    wanted: dict[int, float] = {}
    labels: dict[int, str] = {}
    for it in items:
        mid = _safe_int(it.get("medicine_id"))
        wanted[mid] = wanted.get(mid, 0.0) + _safe_float(it.get("qty"))
        labels.setdefault(mid, str(it.get("name") or ""))
    if not wanted:
        return ""
    try:
        left = _purchase_returnable(conn, purchase_id, online)
    except ReturnsUnreadable as exc:
        return str(exc)
    except Exception:
        if online:
            return _UNREADABLE_BILL
        left = None
    if left is None:
        return ""
    for mid, qty in wanted.items():
        if mid not in left:
            return f"{labels.get(mid) or 'This medicine'} is not on this bill."
        name, rem = left[mid]
        if qty > rem + 1e-6:
            return (
                f"Cannot return more than {rem:g} for {name or labels.get(mid) or 'this medicine'}"
                " — the rest of this bill has already been returned. Load the bill again."
            )
    return ""


def save_purchase_return(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.calc_engine import calc_return_refund
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.purchase_service import recalculate_supplier_due
    from core.sync_prefs import is_online_mode

    # replace_purchase_return has already checked this bill (and the queue does
    # not net out its queued delete of the old return), so it opts out.
    check_returnable = not body.get("_replace_checked")
    if is_online_mode():
        import uuid as _uuid

        from core.online_catalog import find_supplier_by_id
        from core.online_mutation_queue import enqueue, temp_id_from_uuid

        purchase_id = _safe_int(body.get("purchase_id"))
        supplier_id = _safe_int(body.get("supplier_id"))
        if purchase_id <= 0:
            return {"ok": False, "error": "Load a purchase first."}
        return_items = []
        for it in body.get("items") or []:
            if not isinstance(it, dict):
                continue
            qty = _safe_float(it.get("qty"))
            if qty <= 0:
                continue
            from core.layout_config import is_strip_count_type as _strip_type

            part = _part_tablet_error(
                str(it.get("name") or ""),
                qty,
                is_tablet=bool(it.get("is_tablet")) or _strip_type(str(it.get("type") or "")),
                tps=_safe_int(it.get("tablets_per_stripe"), 1),
                in_tablets=False,
            )
            if part:
                return {"ok": False, "error": part}
            mid = _safe_int(it.get("medicine_id"))
            rate = _safe_float(it.get("rate"))
            return_items.append(
                {
                    "medicine_id": mid,
                    "name": str(it.get("name") or ""),
                    "qty": qty,
                    "rate": rate,
                    "amount": round(qty * rate, 2),
                }
            )
        if not return_items:
            return {"ok": False, "error": "Add at least one return line."}
        if check_returnable:
            why = _purchase_return_request_error(conn, purchase_id, return_items, True)
            if why:
                return {"ok": False, "error": why}
        disc = _safe_float(body.get("discount"))
        result = calc_return_refund(return_items, disc)
        refund = float(result.get("refund_amount") or 0)
        cu = str(_uuid.uuid4())
        rid_guess, _is_temp = _return_id_for("purchase_returns", cu)
        return_no = f"PR{abs(rid_guess)}"
        party = []
        if supplier_id > 0 and refund > 0:
            sup = find_supplier_by_id(supplier_id) or {}
            if sup:
                due = round(float(sup.get("total_due") or 0), 2)
                credit = round(float(sup.get("total_credit") or 0), 2)
                if due >= refund:
                    due = round(due - refund, 2)
                else:
                    credit = round(credit + (refund - due), 2)
                    due = 0.0
                party.append({
                    "id": supplier_id,
                    "local_id": supplier_id,
                    "name": sup.get("name") or body.get("supplier_name") or "",
                    "phone": sup.get("phone") or "",
                    "address": sup.get("address") or "",
                    "gstin": sup.get("gstin") or "",
                    "total_due": due,
                    "total_credit": credit,
                })
        row = enqueue(
            collection="purchase_returns",
            op="upsert",
            local_id=rid_guess,
            payload={
                "purchase_id": purchase_id,
                "supplier_id": supplier_id,
                "supplier_name": str(body.get("supplier_name") or (party[0].get("name") if party else "")),
                "purchase_no": str(body.get("bill_label") or body.get("purchase_no") or ""),
                "return_no": return_no,
                "return_date": str(date.today()),
                "discount": disc,
                "refund_amount": refund,
                "reason": str(body.get("reason") or ""),
                "items": return_items,
                # When the return was made: the PC never sent it.
                "created_at": _made_now(),
                "_suppliers": party,
            },
            client_uuid=cu,
        )
        rid = int(row.get("local_id") or rid_guess or 0)
        return {"ok": True, "return_id": rid, "return_no": return_no, "refund_amount": refund, "queued": True}

    blocked = _guard_mutate()
    if blocked:
        return blocked

    # NOTE: the Online path for a purchase return is handled entirely by the
    # is_online_mode() branch at the top of this function, which enqueues the
    # return and returns. A second Online block used to sit here and could
    # never run; it was removed because it read as the live Online path and
    # hid the fact that the strips-to-tablets conversion actually happens in
    # core.online_mutation_queue._flush_return_payload.

    _ensure_return_tables(conn)
    purchase_id = _safe_int(body.get("purchase_id"))
    supplier_id = _safe_int(body.get("supplier_id"))
    if purchase_id <= 0:
        return {"ok": False, "error": "Load a purchase first."}

    return_items: list[dict[str, Any]] = []
    for it in body.get("items") or []:
        if not isinstance(it, dict):
            continue
        qty = _safe_float(it.get("qty"))
        if qty <= 0:
            continue
        med_type = str(it.get("type") or "")
        unit = str(it.get("unit") or it.get("pack_size") or "1")
        is_tablet = bool(it.get("is_tablet")) or is_strip_count_type(med_type)
        tps = _safe_int(it.get("tablets_per_stripe"), 1)
        if is_tablet and tps <= 0:
            tps = parse_tablets_per_stripe(unit) or 1
        part = _part_tablet_error(
            str(it.get("name") or ""), qty, is_tablet=is_tablet, tps=tps, in_tablets=False
        )
        if part:
            return {"ok": False, "error": part}
        stock_deduction = qty * tps if is_tablet else qty
        rate = _safe_float(it.get("rate"))
        return_items.append(
            {
                "medicine_id": _safe_int(it.get("medicine_id")),
                "qty": qty,
                "rate": rate,
                "amount": round(qty * rate, 2),
                "stock_deduction": stock_deduction,
            }
        )
    if not return_items:
        return {"ok": False, "error": "Add at least one return line."}
    if check_returnable:
        why = _purchase_return_request_error(conn, purchase_id, return_items, False)
        if why:
            return {"ok": False, "error": why}

    disc = _safe_float(body.get("discount"))
    reason = str(body.get("reason") or "").strip()
    result = calc_return_refund(return_items, disc)
    refund = float(result.get("refund_amount") or 0)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM purchase_returns")
        return_no = f"PR{cur.fetchone()[0]}"
        cur.execute(
            """
            INSERT INTO purchase_returns
                (return_no, purchase_id, supplier_id, return_date,
                 refund_amount, discount, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                return_no,
                purchase_id,
                supplier_id,
                str(date.today()),
                refund,
                disc,
                reason,
            ),
        )
        return_id = int(cur.lastrowid)
        for item in return_items:
            cur.execute(
                """
                INSERT INTO purchase_return_items
                    (return_id, medicine_id, qty, rate, amount, stock_units)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    return_id,
                    item["medicine_id"],
                    item["qty"],
                    item["rate"],
                    item["amount"],
                    item["stock_deduction"],
                ),
            )
            # No MAX(0, ...): sending 10 back to the supplier while the row
            # shows 3 must leave -7, not 0. The clamp made the medicine row
            # disagree with the return itself and with the qty_delta the
            # online path logs, so 7 units quietly disappeared.
            cur.execute(
                """
                UPDATE medicines SET stock_qty = stock_qty - ? WHERE id = ?
                """,
                (item["stock_deduction"], item["medicine_id"]),
            )
        from core.medicine_visibility import hide_medicines_after_return

        hide_medicines_after_return(
            conn,
            [item["medicine_id"] for item in return_items],
            reason=reason,
        )
        conn.commit()
        if supplier_id:
            recalculate_supplier_due(conn, supplier_id)
        try:
            from core.sync_coordinator import after_purchase_return_saved

            after_purchase_return_saved(conn, return_id, supplier_id)
        except Exception:
            pass
        return {
            "ok": True,
            "return_id": return_id,
            "return_no": return_no,
            "refund_amount": refund,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to save return: {exc}"}


def _return_discount(conn, return_id: int) -> float:
    try:
        row = conn.execute(
            "SELECT COALESCE(discount,0) FROM purchase_returns WHERE id=?",
            (int(return_id),),
        ).fetchone()
        return float(row[0] or 0) if row else 0.0
    except Exception:
        return 0.0


def replace_purchase_return(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Edit a saved purchase return.

    Saved returns were read-only ("Saved returns cannot be changed"), and there
    is still no way to rewrite one in place that keeps the ledger honest in both
    modes. So an edit is done as the two operations the shop already trusts: the
    old return is taken back in full -- stock back on the shelf, supplier due
    restored -- and the corrected lines are saved as a fresh return against the
    SAME purchase bill. The new return gets its own number; the old one stays in
    history as deleted.

    Order is what makes this safe. Everything that can be checked is checked
    BEFORE anything is reversed. If the corrected save still fails after the
    reversal, the original lines are saved back, so the shop is never left with
    the goods counted twice or the return silently gone.
    """
    blocked = _guard_mutate()
    if blocked:
        return blocked
    _ensure_return_tables(conn)
    return_id = _safe_int(body.get("return_id") or body.get("id"))
    if return_id == 0:
        return {"ok": False, "error": "Select a return to edit."}
    if return_id < 0:
        # Saved Online moments ago and still in the upload queue: taking it
        # back now would race the upload itself. Nothing is changed.
        return {
            "ok": False,
            "error": "This return is still being sent to the server. Try Edit again in a moment.",
        }

    old = get_purchase_return_details(conn, return_id)
    if not old.get("ok"):
        return {"ok": False, "error": old.get("error") or "Return not found."}
    purchase_id = int(old.get("purchase_id") or 0)
    if purchase_id <= 0:
        return {"ok": False, "error": "This return is not linked to a purchase bill."}
    asked_pid = _safe_int(body.get("purchase_id"))
    if asked_pid and asked_pid != purchase_id:
        return {"ok": False, "error": "An edited return must stay on the same purchase bill."}

    new_items = [
        it for it in (body.get("items") or [])
        if isinstance(it, dict) and _safe_float(it.get("qty")) > 0
    ]
    if not new_items:
        return {
            "ok": False,
            "error": "Add at least one return line. To cancel the whole return, delete it instead.",
        }

    loaded = load_purchase_for_return(conn, purchase_id)
    if not loaded.get("ok"):
        return {
            "ok": False,
            "error": (loaded.get("error") or "Could not read the purchase bill")
            + " -- nothing was changed.",
        }
    bill_items = {_safe_int(it.get("medicine_id")): it for it in (loaded.get("items") or [])}
    old_qty: dict[int, float] = {}
    for it in old.get("items") or []:
        mid = _safe_int(it.get("medicine_id"))
        old_qty[mid] = old_qty.get(mid, 0.0) + _safe_float(it.get("qty"))
    want: dict[int, float] = {}
    for it in new_items:
        mid = _safe_int(it.get("medicine_id"))
        want[mid] = want.get(mid, 0.0) + _safe_float(it.get("qty"))
    for mid, qty in want.items():
        src = bill_items.get(mid)
        if not src:
            return {"ok": False, "error": "A line is not on this purchase bill -- nothing was changed."}
        # remaining_qty already has THIS return taken off; the edit gives it back first.
        allowed = _safe_float(src.get("remaining_qty")) + old_qty.get(mid, 0.0)
        if qty > allowed + 1e-6:
            return {
                "ok": False,
                "error": f"{src.get('name') or '#' + str(mid)}: {qty:g} is more than can be "
                f"returned ({allowed:g}) -- nothing was changed.",
            }

    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    def _pack_of(src: dict, it: dict) -> dict:
        # Pack facts come from the BILL, not from the screen: a line reloaded
        # from history arrives as type '' and 1 per strip, and trusting that
        # would take a strip of 10 off the shelf as a single tablet.
        med_type = src.get("type") or it.get("type") or ""
        unit = src.get("unit") or it.get("unit") or ""
        tps = _safe_int(src.get("tablets_per_stripe"), 0)
        if tps <= 0 and is_strip_count_type(med_type):
            tps = parse_tablets_per_stripe(unit) or 0
        if tps <= 0:
            tps = _safe_int(it.get("tablets_per_stripe"), 1) or 1
        if src.get("is_tablet") is not None:
            is_tab = bool(src.get("is_tablet"))
        else:
            is_tab = bool(is_strip_count_type(med_type) or it.get("is_tablet"))
        return {"type": med_type, "unit": unit, "is_tablet": is_tab, "tablets_per_stripe": tps}

    def _with_pack(items: list) -> list:
        out = []
        for it in items:
            mid = _safe_int(it.get("medicine_id"))
            src = bill_items.get(mid) or {}
            out.append(
                {
                    "medicine_id": mid,
                    "name": it.get("name") or src.get("name") or "",
                    "qty": _safe_float(it.get("qty")),
                    "rate": _safe_float(it.get("rate") if it.get("rate") is not None else src.get("rate")),
                    **_pack_of(src, it),
                }
            )
        return out

    # Online the details carry the discount; offline they come from the table.
    old_discount = (
        _safe_float(old.get("discount"))
        if old.get("discount") is not None
        else _return_discount(conn, return_id)
    )
    supplier_id = _safe_int(body.get("supplier_id")) or int(old.get("supplier_id") or 0)
    reason = body.get("reason")
    if reason is None:
        reason = old.get("reason") or ""

    taken_back = delete_purchase_return(conn, {"id": return_id})
    if not taken_back.get("ok"):
        return {
            "ok": False,
            "error": "Could not take the old return back, so nothing was changed: "
            + str(taken_back.get("error") or ""),
        }

    saved = save_purchase_return(
        conn,
        {
            "purchase_id": purchase_id,
            "supplier_id": supplier_id,
            "supplier_name": body.get("supplier_name") or old.get("supplier") or "",
            "bill_label": body.get("bill_label") or old.get("purchase_no") or "",
            "items": _with_pack(new_items),
            "discount": _safe_float(body.get("discount"))
            if body.get("discount") is not None
            else old_discount,
            "reason": str(reason),
            "_replace_checked": True,
        },
    )
    if saved.get("ok"):
        saved["replaced_return_id"] = return_id
        saved["replaced_return_no"] = old.get("return_no") or ""
        return saved

    restored = save_purchase_return(
        conn,
        {
            "purchase_id": purchase_id,
            "supplier_id": int(old.get("supplier_id") or 0),
            "items": _with_pack(old.get("items") or []),
            "discount": old_discount,
            "reason": old.get("reason") or "",
            "_replace_checked": True,
        },
    )
    if restored.get("ok"):
        return {
            "ok": False,
            "restored": True,
            "return_no": restored.get("return_no"),
            "error": "The corrected return could not be saved ("
            + str(saved.get("error") or "")
            + "). The original lines were saved back as "
            + str(restored.get("return_no") or "a new return")
            + ", so stock and the supplier balance are as they were.",
        }
    return {
        "ok": False,
        "restored": False,
        "error": "The old return was taken back but could not be saved again ("
        + str(saved.get("error") or "")
        + "). The goods are back in stock and the supplier balance no longer includes that "
        "return. Enter the return again from Returns -> Purchase.",
    }


def lookup_disposal_medicine(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.stock_disposal_service import lookup_batch_purchase
    from core.sync_prefs import is_online_mode

    name = str(body.get("name") or body.get("medicine") or "").strip()
    batch = str(body.get("batch") or body.get("batch_no") or "").strip()
    if not name:
        return {"ok": False, "error": "Medicine name required."}

    if is_online_mode():
        try:
            from core.online_catalog import batches_for_name, medicines_for_name

            match = None
            if batch:
                for b in batches_for_name(name, include_zero=False) or []:
                    if str(b.get("batch_no") or b.get("batch") or "").strip().lower() == batch.lower():
                        match = b
                        break
            if match is None:
                for m in medicines_for_name(name) or []:
                    mb = str(m.get("batch_no") or "").strip()
                    if batch and mb.lower() != batch.lower():
                        continue
                    try:
                        stock = float(m.get("stock_qty") or 0)
                    except (TypeError, ValueError):
                        stock = 0.0
                    if stock <= 0:
                        continue
                    match = m
                    break
            if not match:
                return {"ok": False, "error": "Medicine not found."}
            med_id = _safe_int(match.get("id") or match.get("local_id"))
            med_batch = str(match.get("batch_no") or match.get("batch") or batch)
            try:
                # NOT lookup_batch_purchase(conn, ...): conn is the empty
                # :memory: shell online, so it always answered "no purchase" and
                # a return to the supplier silently became a write-off with no
                # credit claimed.
                from core.stock_disposal_service import lookup_batch_purchase_online

                pinfo = lookup_batch_purchase_online(
                    med_id, str(match.get("name") or name), med_batch
                )
            except Exception:
                pinfo = {}
            return {
                "ok": True,
                "medicine_id": med_id,
                "name": str(match.get("name") or name),
                "batch": med_batch,
                "stock_qty": _safe_float(match.get("stock_qty")),
                "type": str(match.get("type") or ""),
                "purchase": pinfo or {},
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "Online medicine lookup failed."}

    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, COALESCE(batch_no,''), COALESCE(stock_qty,0), COALESCE(type,'')
        FROM medicines
        WHERE name=? COLLATE NOCASE AND COALESCE(batch_no,'')=?
          AND COALESCE(deleted,0)=0
        ORDER BY stock_qty DESC
        LIMIT 1
        """,
        (name, batch),
    )
    row = cur.fetchone()
    if not row and batch:
        cur.execute(
            """
            SELECT id, name, COALESCE(batch_no,''), COALESCE(stock_qty,0), COALESCE(type,'')
            FROM medicines
            WHERE name=? COLLATE NOCASE AND COALESCE(deleted,0)=0
            ORDER BY stock_qty DESC
            LIMIT 1
            """,
            (name,),
        )
        row = cur.fetchone()
    if not row:
        return {"ok": False, "error": "Medicine not found."}
    med_id = int(row[0])
    pinfo = lookup_batch_purchase(conn, med_id, batch or row[2] or "")
    return {
        "ok": True,
        "medicine_id": med_id,
        "name": row[1],
        "batch": row[2] or batch,
        "stock_qty": float(row[3] or 0),
        "type": row[4],
        "purchase": pinfo or {},
    }


def submit_disposal(conn, body: dict[str, Any]) -> dict[str, Any]:
    # Both modes now work online: submit_writeoff has had its own online branch
    # for a while, and submit_return has one as of this change. Neither can use
    # `conn` on an online shop -- it is an empty :memory: shell -- so both read
    # the medicine from the server, write its stock back and file the documents
    # through the mutation queue. The blanket refusal that briefly stood here
    # was blocking the write-off path, which already worked.
    from core.stock_disposal_service import submit_return, submit_writeoff

    blocked = _guard_mutate()
    if blocked:
        return blocked

    lines = body.get("lines") or []
    if not lines:
        return {"ok": False, "error": "Add at least one line."}
    results: list[str] = []
    errors: list[str] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        med_id = _safe_int(line.get("medicine_id"))
        qty = _safe_float(line.get("qty"))
        reason = str(line.get("reason") or "Write-off").strip()
        batch = str(line.get("batch") or "").strip()
        mode = str(line.get("mode") or "writeoff").strip().lower()
        try:
            if mode == "return":
                pinfo = line.get("purchase") or {}
                no = submit_return(
                    conn,
                    med_id,
                    qty,
                    reason,
                    batch_no=batch,
                    supplier_id=_safe_int(pinfo.get("supplier_id")) or None,
                    purchase_id=_safe_int(pinfo.get("purchase_id")) or None,
                    bill_number=str(pinfo.get("bill_number") or ""),
                    original_purchase_qty=_safe_float(pinfo.get("qty")),
                    expected_credit_note=bool(pinfo.get("purchase_id")),
                    # The screen already looked the purchase line up; online
                    # there is no local purchases table to look it up again in.
                    rate_hint=_safe_float(pinfo.get("rate")),
                )
            else:
                no = submit_writeoff(
                    conn,
                    med_id,
                    qty,
                    reason,
                    batch_no=batch,
                    notes=str(line.get("notes") or ""),
                )
            results.append(no)
        except Exception as exc:
            errors.append(str(exc))
    if errors and not results:
        return {"ok": False, "error": "; ".join(errors)}
    return {"ok": True, "disposal_nos": results, "errors": errors}


def _shelf_in_bill_units(stock_qty: float, line: dict) -> float:
    """Shelf stock in the purchase line's units -- the units a return qty is in.

    stock_qty counts tablets for strip-counted types (a purchase adds qty x
    tablets per strip) while the bill line, and so the return, counts strips.
    Capping a strip count by a tablet count offered 15 strips back for a shelf of
    50 tablets: the save took 150 tablets off (the shelf went to -100) and
    credited the supplier for 10 strips that never left the shop."""
    if line.get("is_tablet"):
        tps = _safe_int(line.get("tablets_per_stripe"), 1)
        if tps > 1:
            return stock_qty / tps
    return stock_qty


def _bulk_purchase_prefill_online(
    conn,
    *,
    include_expired: bool = True,
    include_near_expiry: bool = True,
    items: list | None = None,
) -> dict[str, Any]:
    """The same list, built from the server instead of the local database.

    The offline builder walks the local medicines table for expired and
    near-expiry stock and then looks each batch up in the local purchases table.
    Online neither table exists on this PC, so the tab came back empty -- "Load
    stock" found nothing to return, on a shop that had plenty.

    The medicines come from the online catalog, which is already cached and
    already carries expiry and stock. The purchase behind each batch costs two
    server calls, so bills are looked up once per batch and capped: a bulk
    return is worked through a screenful at a time, not a thousand rows.
    """
    from datetime import date, timedelta

    from core.alert_thresholds import parse_expiry
    from core.online_catalog import medicines as catalog_medicines
    from core.stock_disposal_service import lookup_batch_purchase_online

    LOOKUP_CAP = 60

    today = date.today()
    # The shop's own near-expiry months, per medicine type, exactly as the
    # shelf and the alerts use them. Online the settings are restored into the
    # in-memory database at startup, so this is the same table either way.
    near_by_type: dict[str, float] = {}
    try:
        from core.alert_thresholds import load_thresholds

        _low, near_by_type = load_thresholds(conn)
    except Exception:
        near_by_type = {}

    def _horizon_for(med_type: str):
        months = near_by_type.get(str(med_type or "").strip().lower(), 3.0)
        try:
            months = float(months)
        except (TypeError, ValueError):
            months = 3.0
        return today + timedelta(days=int(max(0.0, months) * 30))

    candidates: list[dict[str, Any]] = []
    if items:
        for it in items:
            if isinstance(it, dict):
                candidates.append(dict(it))
    else:
        for m in catalog_medicines() or []:
            if m.get("is_hidden"):
                continue
            try:
                stock = float(m.get("stock_qty") or 0)
            except (TypeError, ValueError):
                stock = 0.0
            if stock <= 0:
                continue
            exp = parse_expiry(str(m.get("expiry_date") or ""))
            if not exp:
                continue
            expired = exp < today
            near = not expired and exp <= _horizon_for(m.get("type"))
            if expired and not include_expired:
                continue
            if near and not include_near_expiry:
                continue
            if not expired and not near:
                continue
            candidates.append(
                {
                    "medicine_id": int(m.get("id") or m.get("local_id") or 0),
                    "name": str(m.get("name") or ""),
                    "batch_no": str(m.get("batch_no") or ""),
                    "expiry_date": str(m.get("expiry_date") or ""),
                    "quantity": stock,
                    "type": str(m.get("type") or ""),
                    "unit": str(m.get("unit") or ""),
                    "reason_tag": "Expired" if expired else "Near expiry",
                }
            )

    def _name(c: dict) -> str:
        # Alert rows (collect_return_candidates_online) spell it medicine_name.
        return str(c.get("name") or c.get("medicine_name") or "")

    candidates.sort(key=lambda c: (str(c.get("expiry_date") or ""), _name(c)))
    looked_up = 0
    groups: dict[int, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []
    truncated = False

    for c in candidates:
        name = _name(c)
        # Both spellings: the bulk tab's write-off table reads medicine_name.
        c = {**c, "name": name, "medicine_name": name}
        med_id = int(c.get("medicine_id") or 0)
        pinfo = None
        if med_id and looked_up < LOOKUP_CAP:
            looked_up += 1
            try:
                pinfo = lookup_batch_purchase_online(
                    med_id, name, str(c.get("batch_no") or "")
                )
            except Exception:
                pinfo = None
        elif med_id:
            truncated = True
        if pinfo and pinfo.get("purchase_id"):
            pid = int(pinfo["purchase_id"])
            grp = groups.setdefault(
                pid,
                {
                    "purchase_id": pid,
                    "supplier_id": pinfo.get("supplier_id"),
                    "supplier_name": pinfo.get("supplier_name") or "",
                    "bill_number": pinfo.get("bill_number") or "",
                    "purchase_date": pinfo.get("purchase_date") or "",
                    "reason": str(c.get("reason_tag") or "")
                    or "Near expiry / Expired stock",
                    "lines": [],
                },
            )
            grp["lines"].append(c)
        else:
            orphans.append(c)

    # Cap each line by what its bill still allows -- the offline builder does it
    # through load_purchase_for_return's remaining_qty. This list never did: it
    # offered the whole shelf against a bill that had already gone back, and the
    # save then credited the supplier a second time. One returns listing serves
    # every bill (oldest bill first, so it is read back far enough once).
    from core.server_crud import get_doc

    enriched: list[dict[str, Any]] = []
    unread = 0
    with _SharedReturnsListing():
        for grp in sorted(
            groups.values(),
            key=lambda g: (str(g.get("purchase_date") or ""), g["purchase_id"]),
        ):
            pid = grp["purchase_id"]
            try:
                doc = get_doc("purchases", pid) or {}
                if not doc or _safe_int(doc.get("deleted")):
                    raise LookupError(f"purchase {pid} could not be read")
                returned = _bill_returned_map(
                    "purchase_returns", "purchase_id", pid, doc.get("purchase_date"), doc
                )
            except Exception:
                unread += 1
                continue
            by_med = {
                int(it["medicine_id"]): it
                for it in _online_purchase_return_items(pid, doc, returned)
            }
            items: list[dict[str, Any]] = []
            for line in grp["lines"]:
                src = by_med.get(_safe_int(line.get("medicine_id")))
                if not src:
                    continue
                want = _shelf_in_bill_units(_safe_float(line.get("quantity")), src)
                remaining = _safe_float(src.get("remaining_qty"))
                if remaining <= 0:
                    continue
                use_qty = min(want, remaining) if want > 0 else remaining
                if use_qty <= 0:
                    continue
                items.append(
                    {
                        **line,
                        **src,
                        "qty": use_qty,
                        "reason_tag": str(line.get("reason_tag") or ""),
                        "original_purchase_qty": src.get("orig_qty") or 0,
                    }
                )
            if not items:
                continue
            enriched.append(
                {
                    "purchase_id": pid,
                    "bill_number": grp["bill_number"]
                    or str(doc.get("bill_number") or doc.get("purchase_no") or ""),
                    "supplier_name": grp["supplier_name"]
                    or str(doc.get("supplier_name") or ""),
                    "supplier_id": grp["supplier_id"]
                    or (_safe_int(doc.get("supplier_id")) or None),
                    "purchase_date": grp["purchase_date"]
                    or str(doc.get("purchase_date") or "")[:10],
                    "reason": grp["reason"],
                    "items": items,
                }
            )
    # The offline builder's order.
    enriched.sort(key=lambda g: (str(g.get("bill_number") or ""), g["purchase_id"]))

    if unread and not enriched and not orphans:
        return {
            "ok": False,
            "error": (
                "Could not read the purchase bills (or their earlier returns) from "
                "the server. Try again in a moment."
            ),
        }
    notes = []
    if truncated:
        notes.append(
            f"Only the first {LOOKUP_CAP} batches were matched to their purchase "
            "bills. Save these, then load again for the rest."
        )
    if unread:
        notes.append(
            f"{unread} purchase bill(s) could not be read from the server and were "
            "left out. Load again in a moment."
        )
    return {
        "ok": True,
        "purchase_groups": enriched,
        "writeoff_lines": orphans,
        "empty": not enriched and not orphans,
        "truncated": truncated,
        "unread_bills": unread,
        "message": " ".join(notes),
    }


def bulk_purchase_prefill(
    conn,
    *,
    include_expired: bool = True,
    include_near_expiry: bool = True,
    items: list | None = None,
) -> dict[str, Any]:
    """Near/expiry stock grouped by purchase bill — ready for bulk return UI."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return _bulk_purchase_prefill_online(
                conn,
                include_expired=include_expired,
                include_near_expiry=include_near_expiry,
                items=items,
            )
    except Exception as exc:
        return {"ok": False, "error": f"Could not read stock from the server: {exc}"}
    from core.stock_disposal_service import build_bulk_return_by_purchase

    raw = build_bulk_return_by_purchase(
        conn,
        items=items,
        include_expired=include_expired,
        include_near_expiry=include_near_expiry,
    )
    enriched: list[dict[str, Any]] = []
    for grp in raw.get("purchase_groups") or []:
        pid = _safe_int(grp.get("purchase_id"))
        if pid <= 0:
            continue
        loaded = load_purchase_for_return(conn, pid)
        if not loaded.get("ok"):
            continue
        by_med = {int(it["medicine_id"]): it for it in loaded.get("items") or []}
        items: list[dict[str, Any]] = []
        for line in grp.get("lines") or []:
            med_id = _safe_int(line.get("medicine_id"))
            src = by_med.get(med_id)
            if not src:
                continue
            want = _shelf_in_bill_units(_safe_float(line.get("quantity")), src)
            remaining = _safe_float(src.get("remaining_qty"))
            if remaining <= 0:
                continue
            use_qty = min(want, remaining) if want > 0 else remaining
            if use_qty <= 0:
                continue
            items.append(
                {
                    **src,
                    "qty": use_qty,
                    "reason_tag": str(line.get("reason_tag") or ""),
                }
            )
        if not items:
            continue
        enriched.append(
            {
                "purchase_id": pid,
                "bill_number": grp.get("bill_number") or loaded.get("bill_label"),
                "supplier_name": grp.get("supplier_name") or loaded.get("supplier"),
                "supplier_id": loaded.get("supplier_id"),
                "reason": grp.get("reason") or "Near expiry / Expired stock",
                "items": items,
            }
        )
    writeoff = raw.get("writeoff_lines") or []
    return {
        "ok": True,
        "purchase_groups": enriched,
        "writeoff_lines": writeoff,
        "empty": not enriched and not writeoff,
    }


def _soft_delete_return_row(conn, table: str, return_id: int) -> None:
    cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info([{table}])")}
    sets = ["deleted=1"]
    if "updated_at" in cols:
        sets.append("updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
    if "version" in cols:
        sets.append("version=COALESCE(version,1)+1")
    if "sync_status" in cols:
        sets.append("sync_status='pending'")
    conn.execute(
        f"UPDATE [{table}] SET {', '.join(sets)} WHERE id=?",
        (int(return_id),),
    )


def _online_refund_payment_ids(
    body: dict[str, Any], return_id: int
) -> tuple[list[int], bool]:
    """local_ids of the negative customer_payments rows this return wrote.

    Returns (ids, lookup_failed). lookup_failed is True when the server could
    not be asked -- the caller must not then claim the refund was cleared.
    A return settled as CREDIT wrote no payment row, so an empty list with
    lookup_failed False is the normal, correct answer for those.
    """
    return_no = str(body.get("return_no") or "").strip()
    customer_id = _safe_int(body.get("customer_id"))
    return_date = str(body.get("return_date") or "").strip()
    try:
        from core import store_query_client as sq
        from core.server_crud import get_doc

        if not return_no or customer_id <= 0 or not return_date:
            doc = get_doc("sales_returns", int(return_id)) or {}
            return_no = str(doc.get("return_no") or return_no).strip()
            customer_id = _safe_int(doc.get("customer_id")) or customer_id
            return_date = str(doc.get("return_date") or return_date).strip()[:10]
        if not return_no or customer_id <= 0:
            # Nothing to match on. A walk-in return has no customer and so no
            # payment row -- that is an answer, not a failure.
            return [], customer_id > 0 and not return_no

        # Both rows are written in one go with str(date.today()), so the
        # payment carries the return's own date. A few days either side
        # absorbs any clock or timezone skew and keeps the reply small --
        # an unbounded list would silently truncate at the limit on a busy
        # shop and drop exactly the old refund we came for.
        from_date = to_date = ""
        if len(return_date) == 10:
            try:
                day = date.fromisoformat(return_date)
                from_date = str(day - timedelta(days=3))
                to_date = str(day + timedelta(days=3))
            except ValueError:
                from_date = to_date = ""
        data = sq.list_customer_payments(
            limit=5000, from_date=from_date, to_date=to_date
        ) or {}
        out: list[int] = []
        for row in data.get("rows") or []:
            if not isinstance(row, dict):
                continue
            if _safe_int(row.get("customer_id")) != customer_id:
                continue
            if str(row.get("reference_no") or "").strip() != return_no:
                continue
            if _safe_float(row.get("amount")) >= 0:
                continue
            pid = _safe_int(row.get("id") or row.get("local_id"))
            if pid > 0 and pid not in out:
                out.append(pid)
        return out, False
    except Exception:
        return [], True


def delete_sales_return(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Delete a sales return (soft-delete + reverse stock restore). Matches Classic."""
    blocked = _guard_mutate()
    if blocked:
        return blocked
    _ensure_return_tables(conn)
    try:
        return_id = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return_id = 0
    if return_id <= 0:
        return {"ok": False, "error": "Select a return to delete."}

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_mutation_queue import enqueue

            # A cash-settled return also wrote a customer_payments row of
            # -payout. The server's softDeleteDoc removes ONLY the row it was
            # asked for and then recomputes the balance from what is left, so
            # deleting the return online left that negative payment standing
            # and the customer's due came back inflated by a refund the shop
            # had already handed over. The offline branch below has taken the
            # payout row out with the return for a while; online never did.
            # Nothing local can find it -- in online mode the connection is an
            # empty :memory: shell -- so ask the server.
            refund_ids, lookup_failed = _online_refund_payment_ids(body, return_id)

            enqueue(
                collection="sales_returns",
                op="delete",
                payload={"id": int(return_id)},
                local_id=int(return_id),
            )
            for pay_id in refund_ids:
                enqueue(
                    collection="customer_payments",
                    op="delete",
                    payload={"id": int(pay_id)},
                    local_id=int(pay_id),
                )
            # Optimistic local hide (same tables Classic history reads).
            try:
                _soft_delete_return_row(conn, "sales_returns", return_id)
                for pay_id in refund_ids:
                    _soft_delete_return_row(conn, "customer_payments", pay_id)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            out = {"ok": True, "queued": True, "id": return_id}
            if lookup_failed:
                # Do not report a clean delete when the refund row may still be
                # sitting on the customer's account.
                out["warning"] = (
                    "Return deleted, but the refund entry could not be checked "
                    "on the server. Open the customer's ledger and confirm."
                )
            return out
    except Exception as exc:
        return {"ok": False, "error": f"Failed to delete return: {exc}"}

    try:
        row = conn.execute(
            "SELECT customer_id, COALESCE(return_no,'') FROM sales_returns "
            "WHERE id=? AND COALESCE(deleted,0)=0",
            (return_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Return not found or already deleted."}
        customer_id = int(row[0]) if row[0] else None
        return_no = str(row[1] or return_id)
        lines = conn.execute(
            "SELECT medicine_id, qty FROM sales_return_items WHERE return_id=?",
            (return_id,),
        ).fetchall()
        medicine_ids = [int(m) for m, _ in lines if m]
        for med_id, qty in lines:
            conn.execute(
                # Undo exactly what the return added, negatives included --
                # same reasoning as the purchase-return deduction above.
                "UPDATE medicines SET stock_qty = stock_qty - ? WHERE id=?",
                # float, not int: a half-bottle or 2.5 ml return was truncated
                # to 2 on the way back, leaving phantom stock on the shelf.
                (float(qty or 0), med_id),
            )
        _soft_delete_return_row(conn, "sales_returns", return_id)

        # A return settled in CASH also wrote a customer_payments row of
        # -payout, tagged with the return number, to record the money that left
        # the drawer. Deleting the return reversed the stock and the return row
        # and left that negative payment behind -- so the customer's due came
        # back INFLATED by the refund, every time, and the shop chased money it
        # had already handed over. Take the payout row out with the return.
        refund_payments = []
        if customer_id:
            try:
                refund_payments = [
                    int(r[0])
                    for r in conn.execute(
                        "SELECT id FROM customer_payments "
                        "WHERE customer_id=? AND reference_no=? AND amount<0 "
                        "AND COALESCE(deleted,0)=0",
                        (customer_id, return_no),
                    ).fetchall()
                ]
            except Exception:
                refund_payments = []
            for pay_id in refund_payments:
                try:
                    _soft_delete_return_row(conn, "customer_payments", pay_id)
                except Exception:
                    pass
        conn.commit()
        if customer_id:
            try:
                from core.customer_service import recalculate_customer_due

                recalculate_customer_due(conn, customer_id)
            except Exception:
                pass
            for pay_id in refund_payments:
                try:
                    from core.sync_coordinator import after_customer_payment_deleted

                    after_customer_payment_deleted(conn, pay_id, customer_id)
                except Exception:
                    pass
        try:
            from core.sync_coordinator import after_sales_return_deleted

            after_sales_return_deleted(conn, return_id, customer_id, medicine_ids)
        except Exception:
            pass
        return {"ok": True, "id": return_id, "return_no": return_no}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to delete return: {exc}"}


def delete_purchase_return(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Delete a purchase return (soft-delete + restore stock). Matches Classic."""
    blocked = _guard_mutate()
    if blocked:
        return blocked
    _ensure_return_tables(conn)
    try:
        return_id = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return_id = 0
    if return_id <= 0:
        return {"ok": False, "error": "Select a return to delete."}

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_mutation_queue import enqueue

            enqueue(
                collection="purchase_returns",
                op="delete",
                payload={"id": int(return_id)},
                local_id=int(return_id),
            )
            try:
                _soft_delete_return_row(conn, "purchase_returns", return_id)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"ok": True, "queued": True, "id": return_id}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to delete return: {exc}"}

    try:
        row = conn.execute(
            "SELECT supplier_id, COALESCE(return_no,'') FROM purchase_returns "
            "WHERE id=? AND COALESCE(deleted,0)=0",
            (return_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Return not found or already deleted."}
        supplier_id = int(row[0]) if row[0] else None
        return_no = str(row[1] or return_id)
        lines = conn.execute(
            """
            SELECT pri.medicine_id, pri.qty, pri.stock_units,
                   COALESCE(m.type,''), COALESCE(m.unit,'')
            FROM purchase_return_items pri
            LEFT JOIN medicines m ON m.id = pri.medicine_id
            WHERE pri.return_id=?
            """,
            (return_id,),
        ).fetchall()
        medicine_ids = [int(r[0]) for r in lines if r[0]]
        # medicines has no tablets_per_stripe column -- the pack lives in `unit`
        # ("1x10"), which purchase_return_stock_units parses.
        for med_id, qty, stock_units, m_type, m_unit in lines:
            # Give back what the return actually took off the shelf. Tablet
            # medicines are returned in strips, so restoring the raw strip count
            # left the shelf short by (strip size - 1) times the quantity.
            units = purchase_return_stock_units(
                {"qty": qty, "stock_units": stock_units},
                medicine={"type": m_type, "unit": m_unit},
            )
            conn.execute(
                "UPDATE medicines SET stock_qty = stock_qty + ? WHERE id=?",
                (float(units or 0), med_id),
            )
        _soft_delete_return_row(conn, "purchase_returns", return_id)
        conn.commit()
        if supplier_id:
            try:
                from core.purchase_service import recalculate_supplier_due

                recalculate_supplier_due(conn, supplier_id)
            except Exception:
                pass
        try:
            from core.sync_coordinator import after_purchase_return_deleted

            after_purchase_return_deleted(conn, return_id, supplier_id, medicine_ids)
        except Exception:
            pass
        return {"ok": True, "id": return_id, "return_no": return_no}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to delete return: {exc}"}


def _purchase_return_details_online(rid: int) -> dict[str, Any]:
    """Online: the engine's sqlite is an empty :memory: shell, so a purchase
    return lives only on the server -- or, just after Save, in the upload queue.

    Details used to be read with local SQL alone, so at an Online shop View
    Details, Load Purchase and Save PDF all answered "Return not found", and an
    edit could never start.
    """
    import json as _json

    doc = None
    try:
        from core.online_mutation_queue import pending_rows

        for row in pending_rows(collection="purchase_returns") or []:
            if int(row.get("local_id") or 0) != int(rid):
                continue
            if str(row.get("op") or "").lower() == "delete":
                return {"ok": False, "error": "Return not found (it is being deleted)."}
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = _json.loads(payload)
                except Exception:
                    payload = None
            if isinstance(payload, dict):
                doc = dict(payload)
    except Exception:
        doc = None
    if doc is None:
        try:
            from core.server_crud import get_doc

            doc = get_doc("purchase_returns", rid)
        except Exception as exc:
            return {"ok": False, "error": f"Could not read the return from the server: {exc}"}
    if not doc:
        return {"ok": False, "error": "Return not found (or the server could not be reached)."}
    if doc.get("deleted"):
        return {"ok": False, "error": "Return not found."}
    items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        qty = _safe_float(it.get("qty"))
        rate = _safe_float(it.get("rate"))
        items.append(
            {
                "medicine_id": _safe_int(it.get("medicine_id")),
                "name": str(it.get("name") or it.get("medicine_name") or ""),
                "batch": str(it.get("batch") or it.get("batch_no") or ""),
                "qty": qty,
                "rate": rate,
                "amount": _safe_float(it.get("amount")) or round(qty * rate, 2),
            }
        )
    return {
        "ok": True,
        "id": int(rid),
        "return_no": str(doc.get("return_no") or f"PR{abs(int(rid))}"),
        "return_date": str(doc.get("return_date") or "")[:10],
        "purchase_no": str(doc.get("purchase_no") or doc.get("bill_number") or ""),
        "supplier": str(doc.get("supplier_name") or ""),
        "refund_amount": _safe_float(doc.get("refund_amount")),
        "discount": _safe_float(doc.get("discount")),
        "reason": str(doc.get("reason") or ""),
        "purchase_id": _safe_int(doc.get("purchase_id")),
        "supplier_id": _safe_int(doc.get("supplier_id")),
        "items": items,
        "online": True,
    }


def get_purchase_return_details(conn, return_id: int) -> dict[str, Any]:
    """Header + lines for View Details / Load from history (Classic parity)."""
    _ensure_return_tables(conn)
    try:
        rid = int(return_id or 0)
    except (TypeError, ValueError):
        rid = 0
    if rid == 0:
        return {"ok": False, "error": "return_id required"}
    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        # A return saved Online and still in the upload queue carries a
        # NEGATIVE temporary id until the server hands it a real one.
        return _purchase_return_details_online(rid)
    if rid < 0:
        return {"ok": False, "error": "return_id required"}
    try:
        header = conn.execute(
            """
            SELECT pr.return_no, pr.return_date,
                   COALESCE(p.bill_number, p.purchase_no, ''),
                   COALESCE(s.name, ''),
                   pr.refund_amount, COALESCE(pr.reason, ''),
                   pr.purchase_id, pr.supplier_id
            FROM purchase_returns pr
            LEFT JOIN purchases p ON pr.purchase_id = p.id
            LEFT JOIN suppliers s ON pr.supplier_id = s.id
            WHERE pr.id = ? AND COALESCE(pr.deleted, 0) = 0
            """,
            (rid,),
        ).fetchone()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not header:
        return {"ok": False, "error": "Return not found."}
    lines = conn.execute(
        """
        SELECT pri.medicine_id, COALESCE(m.name, ''), COALESCE(m.batch_no, ''),
               pri.qty, pri.rate, pri.amount
        FROM purchase_return_items pri
        LEFT JOIN medicines m ON pri.medicine_id = m.id
        WHERE pri.return_id = ?
        ORDER BY pri.id
        """,
        (rid,),
    ).fetchall()
    return {
        "ok": True,
        "id": rid,
        "return_no": header[0] or "",
        "return_date": str(header[1] or "")[:10],
        "purchase_no": header[2] or "",
        "supplier": header[3] or "",
        "refund_amount": float(header[4] or 0),
        "reason": header[5] or "",
        "purchase_id": int(header[6] or 0),
        "supplier_id": int(header[7] or 0),
        "items": [
            {
                "medicine_id": int(r[0] or 0),
                "name": r[1] or "",
                "batch": r[2] or "",
                "qty": float(r[3] or 0),
                "rate": float(r[4] or 0),
                "amount": float(r[5] or 0),
            }
            for r in lines
        ],
    }


def _save_purchase_return_pdf_online(conn, rid: int) -> dict[str, Any]:
    """Online: build the PDF from the server's copy of the return.

    The Offline path renders from local SQL, which Online is an empty
    :memory: shell -- so Save PDF answered "Return not found" at every Online
    shop. Same renderer, fed from the server doc plus the catalog.
    """
    d = get_purchase_return_details(conn, rid)
    if not d.get("ok"):
        return {"ok": False, "error": d.get("error") or "Return not found."}
    from core.document_output import _purchase_return_html_from, save_supplier_document
    from core.online_catalog import find_supplier_by_id, medicine_by_id
    from core.pharmacy_profile_io import fetch_pharmacy_profile_row

    try:
        sup = find_supplier_by_id(d.get("supplier_id")) or {}
    except Exception:
        sup = {}
    try:
        profile = fetch_pharmacy_profile_row(conn)
    except Exception:
        profile = None
    head = {
        "return_no": d.get("return_no"),
        "return_date": d.get("return_date"),
        "refund": d.get("refund_amount"),
        "reason": d.get("reason"),
        "supplier": d.get("supplier") or sup.get("name") or "",
        "phone": sup.get("phone") or "",
        "address": sup.get("address") or "",
        "purchase_no": d.get("purchase_no") or "",
        "bill_no": "",
    }
    lines = []
    for it in d.get("items") or []:
        try:
            med = medicine_by_id(it.get("medicine_id")) or {}
        except Exception:
            med = {}
        rate = _safe_float(it.get("rate"))
        lines.append(
            {
                "name": it.get("name") or med.get("name") or "",
                "batch": it.get("batch") or med.get("batch_no") or "",
                "expiry": med.get("expiry_date") or "",
                "qty": _safe_float(it.get("qty")),
                "rate": rate,
                "amount": _safe_float(it.get("amount")),
                "mrp": _safe_float(med.get("mrp")) or rate,
            }
        )
    html = _purchase_return_html_from(profile, head, lines)
    pdf_path, html_path = save_supplier_document(html, str(d.get("return_no") or f"PR{abs(rid)}"))
    target = pdf_path or html_path
    if not target:
        return {"ok": False, "error": "PDF could not be created. Install Edge or Chrome."}
    return {
        "ok": True,
        "pdf_path": pdf_path or "",
        "html_path": html_path or "",
        "path": target,
        "return_no": d.get("return_no") or "",
    }


def save_purchase_return_pdf(conn, return_id: int) -> dict[str, Any]:
    """Save purchase-return PDF via document_output (headless)."""
    try:
        rid = int(return_id or 0)
    except (TypeError, ValueError):
        rid = 0
    if rid == 0:
        return {"ok": False, "error": "return_id required"}
    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        try:
            return _save_purchase_return_pdf_online(conn, rid)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if rid < 0:
        return {"ok": False, "error": "return_id required"}
    try:
        from core.document_output import (
            render_purchase_return_html,
            save_supplier_document,
        )

        row = conn.execute(
            "SELECT return_no FROM purchase_returns WHERE id=?", (rid,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Return not found."}
        html = render_purchase_return_html(conn, rid)
        pdf_path, html_path = save_supplier_document(html, str(row[0] or f"PR{rid}"))
        target = pdf_path or html_path
        if not target:
            return {
                "ok": False,
                "error": "PDF could not be created. Install Edge or Chrome.",
            }
        return {
            "ok": True,
            "pdf_path": pdf_path or "",
            "html_path": html_path or "",
            "path": target,
            "return_no": row[0] or "",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def bulk_purchase_save(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Save one or more purchase returns plus optional write-offs.

    Works in both modes without a branch of its own: every group goes through
    save_purchase_return, which has had an online branch for a while, and the
    write-off lines go through submit_disposal, which now has one too. The
    blanket refusal that briefly stood here was the only thing stopping it.
    """
    blocked = _guard_mutate()
    if blocked:
        return blocked
    _ensure_return_tables(conn)

    saved: list[dict[str, Any]] = []
    errors: list[str] = []
    for grp in body.get("purchase_groups") or []:
        if not isinstance(grp, dict):
            continue
        items = []
        for it in grp.get("items") or []:
            if not isinstance(it, dict):
                continue
            qty = _safe_float(it.get("qty"))
            if qty <= 0:
                continue
            items.append(
                {
                    "medicine_id": it.get("medicine_id"),
                    "qty": qty,
                    "rate": it.get("rate"),
                    "type": it.get("type"),
                    "unit": it.get("unit"),
                    "pack_size": it.get("pack_size") or it.get("unit"),
                    "is_tablet": it.get("is_tablet"),
                    "tablets_per_stripe": it.get("tablets_per_stripe"),
                }
            )
        if not items:
            continue
        result = save_purchase_return(
            conn,
            {
                "purchase_id": grp.get("purchase_id"),
                "supplier_id": grp.get("supplier_id"),
                "items": items,
                "discount": 0,
                "reason": grp.get("reason") or "Near expiry / Expired stock",
            },
        )
        if result.get("ok"):
            saved.append({**(result), "return_id": result.get("return_id"), "supplier_name": grp.get("supplier_name") or "", "bill_number": grp.get("bill_number") or "", "refund_amount": result.get("refund_amount")})
        else:
            errors.append(str(result.get("error") or "Purchase return failed"))

    writeoff_lines = body.get("writeoff_lines") or []
    writeoff_result: dict[str, Any] | None = None
    if writeoff_lines:
        wo_reason = str(body.get("writeoff_reason") or "No purchase record").strip()
        lines = []
        for line in writeoff_lines:
            if not isinstance(line, dict):
                continue
            lines.append(
                {
                    "medicine_id": line.get("medicine_id"),
                    "qty": line.get("quantity") or line.get("qty"),
                    "batch": line.get("batch_no") or line.get("batch"),
                    "reason": wo_reason,
                    "mode": "writeoff",
                    "notes": line.get("reason_tag") or "",
                }
            )
        if lines:
            writeoff_result = submit_disposal(conn, {"lines": lines})
            if not writeoff_result.get("ok"):
                errors.append(str(writeoff_result.get("error") or "Write-off failed"))

    if not saved and not (writeoff_result and writeoff_result.get("ok")):
        return {
            "ok": False,
            "error": errors[0] if errors else "Nothing to save.",
            "saved": saved,
            "errors": errors,
        }
    return {
        "ok": True,
        "saved": saved,
        "writeoff": writeoff_result,
        "errors": errors,
    }
