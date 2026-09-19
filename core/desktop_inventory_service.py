"""Desktop inventory medicine get / update / delete — classic inventory_dialogs logic."""
from __future__ import annotations

from typing import Any, Optional


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _expiry_to_display(db_expiry: Any) -> str:
    s = str(db_expiry or "").strip()
    if not s:
        return ""
    # YYYY-MM-DD → MM/YY
    if "-" in s and len(s) >= 7:
        try:
            y, m = s.split("-")[0], s.split("-")[1]
            return f"{m}/{y[-2:]}"
        except Exception:
            return s
    return s


def _expiry_to_db(display: str) -> str:
    s = (display or "").strip()
    if not s:
        return ""
    if "/" in s:
        parts = s.split("/")
        mm = parts[0].zfill(2)
        yy = parts[1] if len(parts) > 1 else ""
        year = yy if len(yy) == 4 else ("20" + yy.zfill(2))
        return f"{year}-{mm}-01"
    return s


def _load_medicine_data(conn, medicine_id: int) -> dict[str, Any] | None:
    """Load medicine fields from Online catalog or local SQLite."""
    mid = int(medicine_id)
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import medicine_by_id
            from core.server_crud import get_doc

            mp = medicine_by_id(mid) or get_doc("medicines", mid) or {}
            if mp and not mp.get("deleted"):
                return {
                    "name": mp.get("name") or "",
                    "type": mp.get("type") or "",
                    "batch_no": mp.get("batch_no") or mp.get("batch") or "",
                    "expiry_date": mp.get("expiry_date") or "",
                    "stock_qty": mp.get("stock_qty") or 0,
                    "unit": mp.get("unit") or "1",
                    "mrp": mp.get("mrp") or 0,
                    "rate": mp.get("rate") or 0,
                    "manufacturer": mp.get("manufacturer") or "",
                    "schedule": mp.get("schedule") or "",
                    "content_drug": mp.get("content_drug") or "",
                    "hsn_code": mp.get("hsn_code") or "",
                    "location": mp.get("location") or "",
                }
            # Catalog miss: try store inventory list (same source as Inventory page).
            try:
                from core import store_query_client as sq

                data = sq.list_inventory(q="", limit=5000, include_total=False) or {}
                for r in data.get("rows") or []:
                    if not isinstance(r, dict):
                        continue
                    try:
                        rid = int(r.get("id") or r.get("local_id") or r.get("medicine_id") or 0)
                    except (TypeError, ValueError):
                        continue
                    if rid != mid or r.get("deleted"):
                        continue
                    return {
                        "name": r.get("name") or "",
                        "type": r.get("type") or "",
                        "batch_no": r.get("batch_no") or r.get("batch") or "",
                        "expiry_date": r.get("expiry_date") or r.get("expiry") or "",
                        "stock_qty": r.get("stock_qty") or r.get("stock") or 0,
                        "unit": r.get("unit") or "1",
                        "mrp": r.get("mrp") or 0,
                        "rate": r.get("rate") or 0,
                        "manufacturer": r.get("manufacturer") or "",
                        "schedule": r.get("schedule") or "",
                        "content_drug": r.get("content_drug") or "",
                        "hsn_code": r.get("hsn_code") or "",
                        "location": r.get("location") or "",
                    }
            except Exception:
                pass
    except Exception:
        pass

    cur = conn.cursor()
    cols = [c[1] for c in cur.execute("PRAGMA table_info(medicines)").fetchall()]
    need = [
        "name",
        "type",
        "batch_no",
        "expiry_date",
        "stock_qty",
        "unit",
        "mrp",
        "rate",
        "manufacturer",
        "schedule",
    ]
    for c in ("content_drug", "hsn_code", "location"):
        if c in cols:
            need.append(c)
    select = ", ".join(need)
    row = cur.execute(
        f"SELECT {select} FROM medicines WHERE id=?", (mid,)
    ).fetchone()
    if not row:
        return None
    return {need[i]: row[i] for i in range(len(need))}


def get_medicine(conn, medicine_id: int) -> dict[str, Any]:
    from core.layout_config import (
        get_configured_schedules,
        get_med_types,
        is_strip_count_type,
        parse_tablets_per_stripe,
    )
    from core.stock_utils import decompose_strip_stock

    data = _load_medicine_data(conn, int(medicine_id))
    if not data:
        return {"ok": False, "error": "Medicine not found.", "code": "not_found"}

    name = data.get("name") or ""
    med_type = data.get("type") or ""
    unit = data.get("unit") or ""
    stock_qty = _safe_int(data.get("stock_qty"))
    mrp = _safe_float(data.get("mrp"))
    rate = _safe_float(data.get("rate"))
    strip = bool(is_strip_count_type(med_type, unit))
    tps = 1
    strips = stock_qty
    extra = 0
    if strip:
        try:
            tps = max(1, parse_tablets_per_stripe(unit))
        except Exception:
            tps = 1
        strips, extra = decompose_strip_stock(stock_qty, tps)

    schedules = list(get_configured_schedules())
    try:
        med_types = list(get_med_types() or [])
    except Exception:
        from core.layout_config import _DEFAULT_MED_TYPES
        med_types = list(_DEFAULT_MED_TYPES)

    history = _medicine_transaction_history(
        conn, int(medicine_id), strip, tps,
        name=name, batch=str(data.get("batch_no") or ""),
    )

    return {
        "ok": True,
        "medicine": {
            "id": int(medicine_id),
            "name": name,
            "type": med_type,
            "batch": data.get("batch_no") or "",
            "expiry": _expiry_to_display(data.get("expiry_date")),
            "stock_qty": stock_qty,
            "stock_strips": strips,
            "extra_tablets": extra,
            "unit": unit,
            "mrp": mrp,
            "rate": rate,
            "mrp_tab": round(mrp / tps, 4) if strip and tps else mrp,
            "rate_tab": round(rate / tps, 4) if strip and tps else rate,
            "manufacturer": data.get("manufacturer") or "",
            "schedule": data.get("schedule") or "",
            "content_drug": data.get("content_drug") or "",
            "hsn_code": data.get("hsn_code") or "",
            "location": data.get("location") or "",
            "is_strip": strip,
            "tablets_per_stripe": tps,
        },
        "medicine_types": med_types,
        "schedules": schedules,
        **history,
    }


def _item_matches_medicine(it: dict, medicine_id: int, name: str, batch: str) -> bool:
    try:
        mid = int(it.get("medicine_id") or it.get("local_id") or 0)
    except (TypeError, ValueError):
        mid = 0
    it_name = str(it.get("name") or it.get("medicine_name") or "").strip().upper()
    want_name = (name or "").strip().upper()
    if mid and mid == int(medicine_id):
        # Shared wrong medicine_id across different products — require name match.
        if want_name and it_name and it_name != want_name:
            return False
        return True
    if want_name and it_name == want_name:
        it_batch = str(it.get("batch_no") or it.get("batch") or "").strip()
        want_batch = str(batch or "").strip()
        if not want_batch or it_batch == want_batch:
            return True
    return False


def _summarize_history(
    purchases: list[dict[str, Any]],
    sales: list[dict[str, Any]],
    *,
    strip_type: bool,
    tps: int,
    purch_total: float,
    sold_total: float,
) -> dict[str, Any]:
    remain = max(0.0, purch_total - sold_total)
    if strip_type and tps > 0:
        purchase_summary = (
            f"{len(purchases)} bill(s), {purch_total:g} tablets "
            f"({purch_total / tps:g} strips)"
        )
        sales_summary = (
            f"{len(sales)} bill(s), sold {sold_total:g} tablets "
            f"({sold_total / tps:g} strips) | remain {remain:g} tablets "
            f"({remain / tps:g} strips)"
        )
        qty_label = "Tablets"
    else:
        purchase_summary = f"{len(purchases)} bill(s), qty {purch_total:g}"
        sales_summary = (
            f"{len(sales)} bill(s), sold {sold_total:g} | remain {remain:g}"
        )
        qty_label = "Qty"
    return {
        "purchase_history": purchases,
        "sales_history": sales,
        "purchase_summary": purchase_summary,
        "sales_summary": sales_summary,
        "qty_column_label": qty_label,
    }


def _medicine_transaction_history_online(
    medicine_id: int,
    name: str,
    batch: str,
    *,
    strip_type: bool,
    tps: int,
) -> dict[str, Any] | None:
    """Online: pull purchase/sales bills that contain this medicine (supplier + patient)."""
    from core import store_query_client as sq

    common = {
        "from_date": "2000-04-01",
        "to_date": "2099-03-31",
        "medicine": (name or "").strip(),
        "batch": (batch or "").strip(),
        "limit": 300,
    }
    purch_bills = list((sq.list_purchases(**common) or {}).get("rows") or [])
    sales_bills = list((sq.list_sales(**common) or {}).get("rows") or [])

    purchases: list[dict[str, Any]] = []
    purch_total = 0.0
    for b in purch_bills[:80]:
        try:
            detail = sq.get_purchase(int(b.get("id") or 0)) or {}
        except Exception:
            continue
        supplier = (
            detail.get("supplier_name")
            or b.get("supplier_name")
            or "(no supplier)"
        )
        pdate = detail.get("purchase_date") or b.get("purchase_date") or ""
        bill = detail.get("bill_number") or b.get("bill_number") or ""
        for it in detail.get("items") or []:
            if not isinstance(it, dict) or it.get("deleted"):
                continue
            if not _item_matches_medicine(it, medicine_id, name, batch):
                continue
            qty_s = float(it.get("qty") or 0)
            free_s = float(it.get("free_qty") or 0)
            total_s = qty_s + free_s
            tabs = total_s * tps if strip_type else total_s
            purch_total += tabs
            rate = float(it.get("rate") or 0)
            amount = it.get("item_amount")
            if amount is None:
                amount = it.get("amount") or 0
            if strip_type:
                total_disp = f"{total_s:g} strip = {int(round(tabs))} tab"
            else:
                total_disp = f"{total_s:g}"
            purchases.append({
                "date": pdate,
                "bill": bill,
                "party": supplier,
                "qty": qty_s,
                "free_qty": free_s,
                "total_display": total_disp,
                "rate": rate,
                "amount": float(amount or 0),
            })

    sales: list[dict[str, Any]] = []
    sold_total = 0.0
    for b in sales_bills[:80]:
        try:
            detail = sq.get_sale(int(b.get("id") or 0)) or {}
        except Exception:
            continue
        if detail.get("is_autosave") or detail.get("deleted"):
            continue
        customer = (
            detail.get("customer_name")
            or b.get("customer_name")
            or "(no customer)"
        )
        sdate = detail.get("bill_date") or b.get("bill_date") or ""
        bill = detail.get("bill_no") or b.get("bill_no") or ""
        try:
            from core.fy_serial import display_sales_bill_no
            bill_label = display_sales_bill_no(str(bill or ""))
        except Exception:
            bill_label = str(bill or "")
        for it in detail.get("items") or []:
            if not isinstance(it, dict) or it.get("deleted"):
                continue
            if not _item_matches_medicine(it, medicine_id, name, batch):
                continue
            q = float(it.get("qty") or 0)
            sold_total += q
            qty_disp = f"{q:g}"
            if strip_type and tps > 1:
                qty_disp += f" ({q / tps:g} strip)"
            sales.append({
                "date": sdate,
                "bill": bill_label,
                "party": customer,
                "qty_display": qty_disp,
                "qty": q,
                "rate": float(it.get("rate") or 0),
                "amount": float(it.get("amount") or 0),
            })

    return _summarize_history(
        purchases,
        sales,
        strip_type=strip_type,
        tps=tps,
        purch_total=purch_total,
        sold_total=sold_total,
    )


def _medicine_transaction_history(
    conn,
    medicine_id: int,
    strip_type: bool,
    tps: int,
    *,
    name: str = "",
    batch: str = "",
) -> dict[str, Any]:
    """Purchase and sales lines for inventory detail view (matches Tk open_view_dialog)."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            online = _medicine_transaction_history_online(
                int(medicine_id),
                name,
                batch,
                strip_type=strip_type,
                tps=tps,
            )
            if online is not None:
                return online
    except Exception:
        pass

    cur = conn.cursor()
    purchases: list[dict[str, Any]] = []
    purch_total = 0.0
    for row in cur.execute(
        """
        SELECT p.purchase_date, COALESCE(p.bill_number, ''),
               COALESCE(s.name, '(no supplier)'),
               COALESCE(pi.qty, 0), COALESCE(pi.free_qty, 0), pi.rate,
               COALESCE(pi.item_amount, pi.amount, 0)
        FROM purchase_items pi
        JOIN purchases p ON pi.purchase_id = p.id
        LEFT JOIN suppliers s ON p.supplier_id = s.id
        WHERE pi.medicine_id = ?
          AND COALESCE(pi.deleted, 0) = 0 AND COALESCE(p.deleted, 0) = 0
        ORDER BY p.purchase_date DESC
        """,
        (medicine_id,),
    ):
        qty_s = float(row[3] or 0)
        free_s = float(row[4] or 0)
        total_s = qty_s + free_s
        tabs = total_s * tps if strip_type else total_s
        purch_total += tabs
        if strip_type:
            total_disp = f"{total_s:g} strip = {int(round(tabs))} tab"
        else:
            total_disp = f"{total_s:g}"
        purchases.append({
            "date": row[0] or "",
            "bill": row[1] or "",
            "party": row[2] or "",
            "qty": qty_s,
            "free_qty": free_s,
            "total_display": total_disp,
            "rate": float(row[5] or 0),
            "amount": float(row[6] or 0),
        })

    sales: list[dict[str, Any]] = []
    sold_total = 0.0
    for row in cur.execute(
        """
        SELECT s.bill_date, COALESCE(s.bill_no, ''),
               COALESCE(c.name, '(no customer)'), si.qty, si.rate, si.amount
        FROM sales_items si
        JOIN sales s ON si.sale_id = s.id
        LEFT JOIN customers c ON s.customer_id = c.id
        WHERE si.medicine_id = ?
          AND COALESCE(si.deleted, 0) = 0 AND COALESCE(s.deleted, 0) = 0
          AND COALESCE(s.is_autosave, 0) = 0
        ORDER BY s.bill_date DESC, s.id DESC
        """,
        (medicine_id,),
    ):
        q = float(row[3] or 0)
        sold_total += q
        qty_disp = f"{q:g}"
        if strip_type and tps > 1:
            qty_disp += f" ({q / tps:g} strip)"
        try:
            from core.fy_serial import display_sales_bill_no
            bill_label = display_sales_bill_no(str(row[1] or ""))
        except Exception:
            bill_label = str(row[1] or "")
        sales.append({
            "date": row[0] or "",
            "bill": bill_label,
            "party": row[2] or "",
            "qty_display": qty_disp,
            "qty": q,
            "rate": float(row[4] or 0),
            "amount": float(row[5] or 0),
        })

    return _summarize_history(
        purchases,
        sales,
        strip_type=strip_type,
        tps=tps,
        purch_total=purch_total,
        sold_total=sold_total,
    )

def update_medicine(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.layout_config import is_strip_count_type
    from core.stock_utils import inventory_save_stock_qty

    medicine_id = _safe_int(body.get("id") or body.get("medicine_id"))
    if medicine_id <= 0:
        return {"ok": False, "error": "Medicine id required."}

    name = str(body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Name is required."}

    med_type = str(body.get("type") or "").strip()
    unit = str(body.get("unit") or "").strip() or "1"
    batch = str(body.get("batch") or body.get("batch_no") or "").strip()
    # Accept BOTH spellings, and leave the value alone when neither is sent.
    # This read only "expiry", so a caller using the canonical "expiry_date"
    # (the name used everywhere else, including the sync payload) silently
    # blanked the medicine's expiry date on an unrelated edit.
    has_expiry = ("expiry" in body) or ("expiry_date" in body)
    expiry = _expiry_to_db(str(body.get("expiry") or body.get("expiry_date") or ""))
    mrp = _safe_float(body.get("mrp"))
    rate = _safe_float(body.get("rate"))
    manufacturer = str(body.get("manufacturer") or "").strip()
    schedule = str(body.get("schedule") or "").strip()
    content = str(body.get("content_drug") or body.get("content") or "").strip()
    hsn = str(body.get("hsn_code") or body.get("hsn") or "").strip()

    location = str(body.get("location") or body.get("shelf") or "").strip()
    has_location = "location" in body or "shelf" in body

    # An edit that does not mention stock must LEAVE STOCK ALONE. Defaulting a
    # missing key to 0 meant any caller updating, say, the HSN code silently
    # zeroed the medicine -- stock 9 became 0 with no warning. The purchase
    # ledger owns this number; only an explicit value may change it.
    has_stock = ("stock_strips" in body) or ("stock_qty" in body)
    strips_or_qty = _safe_int(body.get("stock_strips", body.get("stock_qty")))
    extra = _safe_int(body.get("extra_tablets"))
    try:
        stock_saved = inventory_save_stock_qty(
            med_type, unit, strips_or_qty, extra
        )
    except Exception:
        stock_saved = strips_or_qty

    try:
        from core.online_guard import ensure_can_mutate

        ensure_can_mutate()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "online_blocked"}

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.server_crud import upsert_medicine_online, get_doc
            from core.online_catalog import medicine_by_id, invalidate

            existing = medicine_by_id(medicine_id) or get_doc("medicines", medicine_id) or {}
            doc = dict(existing)
            if not has_stock:
                stock_saved = _safe_int(existing.get("stock_qty"))
            if not has_expiry:
                expiry = existing.get("expiry_date") or ""
            if has_location:
                doc["location"] = location
            doc.update({
                "id": medicine_id,
                "local_id": medicine_id,
                "name": name,
                "type": med_type,
                "batch_no": batch,
                "expiry_date": expiry,
                "stock_qty": stock_saved,
                "unit": unit,
                "mrp": mrp,
                "rate": rate,
                "manufacturer": manufacturer,
                "schedule": schedule,
                "content_drug": content,
                "hsn_code": hsn,
            })
            upsert_medicine_online(doc)
            invalidate("medicines")
            return {
                "ok": True,
                "medicine_id": medicine_id,
                "is_strip": is_strip_count_type(med_type, unit),
                "stock_qty": stock_saved,
            }
    except Exception as exc:
        return {"ok": False, "error": f"Failed to update medicine: {exc}"}

    cur = conn.cursor()
    cols = [c[1] for c in cur.execute("PRAGMA table_info(medicines)").fetchall()]

    sets = [
        "name=?",
        "type=?",
        "batch_no=?",
        "expiry_date=?",
        "stock_qty=?",
        "unit=?",
        "mrp=?",
        "rate=?",
        "manufacturer=?",
        "schedule=?",
    ]
    params: list[Any] = [
        name,
        med_type,
        batch,
        expiry,
        stock_saved,
        unit,
        mrp,
        rate,
        manufacturer,
        schedule,
    ]
    if "content_drug" in cols:
        sets.append("content_drug=?")
        params.append(content)
    if "hsn_code" in cols:
        sets.append("hsn_code=?")
        params.append(hsn)
    if "synced_at" in cols:
        sets.append("synced_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
    params.append(medicine_id)

    try:
        cur.execute(
            f"UPDATE medicines SET {', '.join(sets)} WHERE id=?",
            params,
        )
        conn.commit()
        try:
            from core.sync_coordinator import after_medicine_saved

            after_medicine_saved(conn, medicine_id)
        except Exception:
            pass
        return {
            "ok": True,
            "medicine_id": medicine_id,
            "is_strip": is_strip_count_type(med_type, unit),
            "stock_qty": stock_saved,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to update medicine: {exc}"}


def _hide_medicine_online(medicine_id: int) -> bool:
    from core.server_crud import upsert_medicine_online, bump_meta, get_doc
    from core.online_catalog import medicine_by_id, invalidate

    mid = int(medicine_id)
    # Read the AUTHORITATIVE server doc first, not the local catalog cache.
    #
    # The server resolves conflicts on version alone:
    #     if (incoming.version < existing.version) return 'skip'
    # and a newer timestamp does NOT rescue a lower version. The cache can easily
    # lag — e.g. Android bumps a medicine to v5 through a sale's stock operation
    # while this PC still has v2 cached. bump_meta() then sends v3, the server
    # silently skips it, and the push still returns HTTP 200, so the UI reports
    # "deleted" while nothing changed. That is why hidden/deleted medicines
    # appear to come back: the change never landed in the first place.
    existing = get_doc("medicines", mid) or medicine_by_id(mid) or {}
    if not existing:
        return False
    doc = bump_meta(dict(existing))
    doc["id"] = mid
    doc["local_id"] = mid
    doc["is_hidden"] = True
    upsert_medicine_online(doc)
    invalidate("medicines")
    return True


def delete_medicine(conn, body: dict[str, Any]) -> dict[str, Any]:
    medicine_id = _safe_int(body.get("id") or body.get("medicine_id"))
    if medicine_id <= 0:
        return {"ok": False, "error": "Medicine id required."}
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            if not _hide_medicine_online(medicine_id):
                return {"ok": False, "error": "Medicine not found."}
            return {"ok": True, "deleted": True, "medicine_id": medicine_id}

        from core.medicine_visibility import hide_medicine
        from core.sync_coordinator import after_medicines_hidden

        hide_medicine(conn, medicine_id)
        after_medicines_hidden(conn, [medicine_id])
        return {"ok": True, "deleted": True, "medicine_id": medicine_id}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to delete medicine: {exc}"}


def _delete_zero_stock_online() -> dict[str, Any]:
    """Hide every visible Online medicine batch with stock_qty <= 0."""
    from core.online_catalog import medicines as oc_medicines, invalidate
    from core.server_crud import upsert_medicine_online, bump_meta
    from core import store_query_client as sq

    # The CATALOGUE, not the inventory listing.
    #
    # list_inventory returns display rows, and it already drops batches that the
    # batch-visibility rules collapse (when every batch of a name is empty it
    # shows only the newest). Those rows never came back, so they never got
    # hidden -- which is why "Remove out of stock" always left some behind. The
    # catalogue holds every batch, and holds whole documents rather than the
    # handful of columns a list needs.
    rows: list[dict] = []
    try:
        # force=True: the cached catalogue can be minutes old and hold only part
        # of the shelf. Hiding from a stale list leaves empty batches behind,
        # which is exactly what the shop kept seeing after "Remove out of stock".
        for m in oc_medicines(force=True) or []:
            if not isinstance(m, dict):
                continue
            if m.get("is_hidden") or m.get("deleted"):
                continue
            if float(m.get("stock_qty") or 0) > 0:
                continue
            rows.append(m)
    except Exception:
        rows = []

    if not rows:
        # Fallback: page the server listing.
        try:
            offset = 0
            while offset < 50000:
                data = sq.list_inventory(
                    q="", limit=500, offset=offset, include_total=False,
                ) or {}
                raw = data.get("rows") or []
                if not raw:
                    break
                for r in raw:
                    if not isinstance(r, dict):
                        continue
                    if float(r.get("stock_qty") or r.get("stock") or 0) > 0:
                        continue
                    if r.get("is_hidden") or r.get("deleted"):
                        continue
                    rows.append(r)
                if len(raw) < 500:
                    break
                offset += 500
        except Exception:
            pass

    if not rows:
        return {"ok": True, "hidden": 0, "message": "No out-of-stock medicines to hide."}

    # One request for the whole lot, not two per medicine.
    #
    # Reading each medicine back and pushing it separately meant 2 x N round
    # trips: a shop with a few hundred empty batches sat on a spinner for a
    # minute or more, which read as "it hung". The catalogue row already carries
    # the medicine's fields and its version, so it can be stamped and sent as
    # part of a single bulk write.
    from core.server_crud import upsert_docs

    docs: list[dict] = []
    seen: set[int] = set()
    for m in rows:
        try:
            mid = int(m.get("id") or m.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or mid in seen:
            continue
        seen.add(mid)
        doc = bump_meta(dict(m))
        doc["id"] = mid
        doc["local_id"] = mid
        doc["is_hidden"] = True
        docs.append(doc)

    if not docs:
        return {"ok": True, "hidden": 0, "message": "No out-of-stock medicines to hide."}

    hidden = 0
    # In batches, so one oversized request cannot time out the whole action.
    for start in range(0, len(docs), 200):
        chunk = docs[start:start + 200]
        try:
            upsert_docs("medicines", chunk)
            hidden += len(chunk)
        except Exception:
            # Fall back to one at a time for this chunk only, so a single bad
            # row cannot lose the rest.
            for d in chunk:
                try:
                    upsert_medicine_online(d)
                    hidden += 1
                except Exception:
                    continue
    try:
        from core.online_catalog import patch_docs

        patch_docs("medicines", docs)
    except Exception:
        pass
    try:
        invalidate("medicines")
    except Exception:
        pass
    return {
        "ok": True,
        "hidden": int(hidden),
        "message": f"{int(hidden)} out-of-stock medicine(s) hidden.",
    }


def delete_zero_stock(conn) -> dict[str, Any]:
    """Hide all visible zero-stock batches (including import stubs never saved)."""
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return _delete_zero_stock_online()
    except Exception as exc:
        return {"ok": False, "error": f"Failed to hide zero-stock medicines: {exc}"}

    from core.medicine_visibility import hide_zero_stock_medicines
    from core.sync_coordinator import after_medicines_hidden

    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM medicines
        WHERE COALESCE(is_hidden, 0) = 0
          AND COALESCE(stock_qty, 0) <= 0
        """
    )
    sync_ids = [int(r[0]) for r in cur.fetchall()]
    if not sync_ids:
        return {"ok": True, "hidden": 0, "message": "No out-of-stock medicines to hide."}
    hidden = hide_zero_stock_medicines(conn)
    after_medicines_hidden(conn, sync_ids)
    return {"ok": True, "hidden": int(hidden or 0)}


def delete_expired(conn) -> dict[str, Any]:
    from datetime import date

    from core.alert_thresholds import parse_expiry
    from core.medicine_visibility import hide_all_expired_medicines
    from core.sync_coordinator import after_medicines_hidden

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import medicines as oc_medicines, invalidate
            from core.server_crud import upsert_medicine_online, bump_meta
            from core import store_query_client as sq

            today = date.today()
            rows: list[dict] = []
            try:
                offset = 0
                while offset < 50000:
                    data = sq.list_inventory(
                        q="", limit=500, offset=offset, include_total=False,
                    ) or {}
                    raw = data.get("rows") or []
                    if not raw:
                        break
                    rows.extend([r for r in raw if isinstance(r, dict)])
                    if len(raw) < 500:
                        break
                    offset += 500
            except Exception:
                rows = [m for m in (oc_medicines() or []) if isinstance(m, dict)]

            expired = []
            for m in rows:
                if m.get("is_hidden") or m.get("deleted"):
                    continue
                if float(m.get("stock_qty") or m.get("stock") or 0) <= 0:
                    continue
                # A month-only expiry ("09/26") runs to the END of that month.
                # Comparing against parse_expiry's day-01 date wrote batches off
                # up to a month early, so "Remove Expired" swept away stock the
                # pharmacy could still legally sell.
                from core.batch_visibility import expiry_cutoff

                cutoff = expiry_cutoff(
                    str(m.get("expiry_date") or m.get("expiry") or "")
                )
                if cutoff and cutoff < today:
                    expired.append(m)
            if not expired:
                return {"ok": True, "hidden": 0, "message": "No expired medicines to hide."}
            hidden = 0
            for m in expired:
                try:
                    mid = int(m.get("id") or m.get("local_id") or 0)
                except (TypeError, ValueError):
                    continue
                if mid <= 0:
                    continue
                try:
                    doc = bump_meta(dict(m))
                    doc["id"] = mid
                    doc["local_id"] = mid
                    doc["is_hidden"] = True
                    upsert_medicine_online(doc)
                    hidden += 1
                except Exception:
                    continue
            try:
                invalidate("medicines")
            except Exception:
                pass
            return {"ok": True, "hidden": int(hidden)}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to hide expired medicines: {exc}"}

    cur = conn.cursor()
    today = date.today()
    cur.execute(
        "SELECT id, COALESCE(expiry_date, '') FROM medicines "
        "WHERE COALESCE(is_hidden, 0) = 0 AND COALESCE(stock_qty, 0) > 0"
    )
    expired_ids = []
    for med_id, exp in cur.fetchall():
        try:
            exp_date = parse_expiry(str(exp or ""))
            if exp_date and exp_date <= today:
                expired_ids.append(int(med_id))
        except Exception:
            continue
    if not expired_ids:
        return {"ok": True, "hidden": 0, "message": "No expired medicines to hide."}
    hidden = hide_all_expired_medicines(conn)
    after_medicines_hidden(conn, expired_ids)
    return {"ok": True, "hidden": int(hidden or 0)}


def reorder_medicine(conn, medicine_id: int) -> dict[str, Any]:
    from core.reorder_service import (
        current_stock_for_medicine,
        min_stock_level,
        suggest_order_quantity,
    )

    mid = _safe_int(medicine_id)
    if mid <= 0:
        return {"ok": False, "error": "medicine_id required"}

    data = _load_medicine_data(conn, mid)
    if not data:
        return {"ok": False, "error": "Medicine not found."}
    if float(data.get("stock_qty") or 0) < 0:
        # still allow reorder of zero-stock
        pass

    name = str(data.get("name") or "")
    pack = str(data.get("unit") or "")
    med_type = str(data.get("type") or "Others") or "Others"
    rate = float(data.get("rate") or 0)
    stock = current_stock_for_medicine(conn, name, pack)
    return {
        "ok": True,
        "prefill": {
            "medicine_name": name,
            "pack_size": pack,
            "quantity": suggest_order_quantity(conn, name, med_type, stock, pack),
            "unit_price": rate,
            "current_stock": stock,
            "min_stock": min_stock_level(conn, med_type),
        },
    }
