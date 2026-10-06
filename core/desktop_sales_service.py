"""Desktop API wrappers for Sales — reuses classic billing/calc logic (no duplicate math)."""
from __future__ import annotations

import os
import threading
from datetime import date, datetime
from typing import Any, Optional

# The engine serves requests on a ThreadingHTTPServer, so an autosave tick can
# arrive while F7/F8 is saving or printing the same bill. Autosave now writes a
# REAL bill, so an interleave would print a half-finished one. Every writer of
# an in-progress sale takes this first.
_SALE_WRITE_LOCK = threading.RLock()

# Clear (discard_autosave) takes the sale write lock at once, but a Tauri tick asks about its
# Bill Date before it takes the lock, which can wait on the store for 15 s. A Clear that landed
# in that wait deleted the bill and dropped the form's record, and the tick then saved the
# cleared sale as a new bill: the same number again, the stock off again, and a record for the
# recovery prompt. Every tick and every Clear takes a number from one sequence; a tick whose
# form was cleared after it took its number writes nothing. Memory only: no tick outlives the
# engine it runs in.
_FORM_SEQ_LOCK = threading.Lock()
_FORM_SEQ = 0
_FORMS_CLEARED: dict[str, int] = {}
_FORMS_CLEARED_KEEP = 256


def _next_form_seq() -> int:
    global _FORM_SEQ
    with _FORM_SEQ_LOCK:
        _FORM_SEQ += 1
        return _FORM_SEQ


def _note_form_cleared(token: str) -> None:
    tok = str(token or "").strip()
    if not tok:
        return
    seq = _next_form_seq()
    with _FORM_SEQ_LOCK:
        _FORMS_CLEARED[tok] = seq
        extra = len(_FORMS_CLEARED) - _FORMS_CLEARED_KEEP
        if extra > 0:
            for old in sorted(_FORMS_CLEARED, key=_FORMS_CLEARED.__getitem__)[:extra]:
                _FORMS_CLEARED.pop(old, None)


def _form_cleared_since(token: str, seq: int) -> bool:
    tok = str(token or "").strip()
    if not tok:
        return False
    with _FORM_SEQ_LOCK:
        return _FORMS_CLEARED.get(tok, 0) > seq


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


def _parse_bill_date(raw: Any) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    s = str(raw or "").strip()
    if not s:
        return date.today()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "")[:19]).date()
    except Exception:
        return date.today()


def _sale_rounding_amount(
    medicines: list,
    disc_pct: float,
    disc_rs: float | None,
    body: dict[str, Any],
    *,
    default_auto: bool = True,
) -> float:
    """Nearest-rupee adjustment, same as classic billing.calculate_total."""
    from core.calc_engine import auto_round, calc_bill_summary

    auto = bool(body.get("auto_rounding", default_auto))
    if auto:
        pre = calc_bill_summary(medicines, disc_pct, 0, discount_rs=disc_rs)
        return round(auto_round(pre["pre_round_total"]), 2)
    return round(_safe_float(body.get("rounding")), 2)


def _fmt_location(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip()


def _has_pharmacy_profile(conn) -> bool:
    try:
        from core.pharmacy_profile_io import has_pharmacy_profile

        return has_pharmacy_profile(conn)
    except Exception:
        try:
            row = conn.execute("SELECT 1 FROM pharmacy_profile LIMIT 1").fetchone()
            return bool(row)
        except Exception:
            return False


def _medicine_row(conn, medicine_id: int) -> Optional[dict[str, Any]]:
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import medicine_by_id

            m = medicine_by_id(medicine_id)
            if m:
                return {
                    "id": int(m.get("id") or m.get("local_id") or medicine_id),
                    "name": str(m.get("name") or ""),
                    "batch": str(m.get("batch_no") or m.get("batch") or ""),
                    "expiry": str(m.get("expiry_date") or m.get("expiry") or ""),
                    "stock": _safe_float(m.get("stock_qty") or m.get("stock")),
                    "type": str(m.get("type") or ""),
                    "unit": str(m.get("unit") or "1"),
                    "mrp": _safe_float(m.get("mrp")),
                    "rate": _safe_float(m.get("rate")),
                    "gst_percent": _safe_float(m.get("gst_percent")),
                    "schedule": str(m.get("schedule") or ""),
                    "location": _fmt_location(m.get("location")),
                    "created_at": str(m.get("created_at") or ""),
                }
    except Exception:
        pass

    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(medicines)").fetchall()
    }
    select = [
        "id",
        "name",
        "COALESCE(batch_no,'')",
        "COALESCE(expiry_date,'')",
        "COALESCE(stock_qty,0)",
        "COALESCE(type,'')",
        "COALESCE(unit,'1')",
        "COALESCE(mrp,0)",
        "COALESCE(rate,0)",
    ]
    keys = ["id", "name", "batch", "expiry", "stock", "type", "unit", "mrp", "rate"]
    if "gst_percent" in cols:
        select.append("COALESCE(gst_percent,0)")
        keys.append("gst_percent")
    if "schedule" in cols:
        select.append("COALESCE(schedule,'')")
        keys.append("schedule")
    if "location" in cols:
        select.append("COALESCE(location,'')")
        keys.append("location")
    if "created_at" in cols:
        select.append("COALESCE(created_at,'')")
        keys.append("created_at")
    row = conn.execute(
        f"SELECT {', '.join(select)} FROM medicines WHERE id=?",
        (int(medicine_id),),
    ).fetchone()
    if not row:
        return None
    out = {keys[i]: row[i] for i in range(len(keys))}
    out["id"] = int(out["id"])
    out["stock"] = _safe_float(out.get("stock"))
    out["mrp"] = _safe_float(out.get("mrp"))
    out["rate"] = _safe_float(out.get("rate"))
    out["gst_percent"] = _safe_float(out.get("gst_percent"))
    out["schedule"] = str(out.get("schedule") or "")
    out["location"] = _fmt_location(out.get("location"))
    return out


def list_medicine_names(
    conn,
    q: str = "",
    *,
    limit: int = 40,
    bill_date: Any = None,
    reserved: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """
    Step-1 medicine names (aggregated stock) for two-step picker.
    Mirrors classic TwoStepMedicineCombo name list.
    ``reserved`` maps medicine id → qty already on this bill (stock shown is reduced).
    Out-of-stock and expired batches are excluded from the Sales dropdown.
    """
    from core.batch_visibility import is_expired_as_of, medicine_existed_as_of, medicine_existed_sql

    as_of = _parse_bill_date(bill_date)
    reserved = reserved or {}
    # Sales picker: never list zero/negative available or expired stock.
    show_zero = False

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import search_medicine_names

            # Online Sales: type-to-filter from warm catalog (not a 10k UI dump).
            qn = (q or "").strip()
            lim = max(int(limit or 40), 400 if qn else 250)
            return {
                "names": search_medicine_names(
                    q,
                    limit=lim,
                    show_zero=False,
                    as_of=as_of,
                    reserved=reserved,
                )
            }
    except Exception:
        pass

    q = (q or "").strip()
    limit = max(1, min(int(limit or 40), 100))

    def _reserved_for(mid: int) -> float:
        return _safe_float(reserved.get(str(mid), reserved.get(mid, 0)))

    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(medicines)").fetchall()
    }
    where = ["1=1"]
    params: list[Any] = []
    if "is_hidden" in cols:
        where.append("COALESCE(is_hidden,0)=0")
    if "created_at" in cols:
        where.append(medicine_existed_sql())
        params.append(as_of.isoformat())
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")

    select = [
        "id",
        "name",
        "COALESCE(batch_no,'')",
        "COALESCE(expiry_date,'')",
        "COALESCE(stock_qty,0)",
        "COALESCE(unit,'1')",
        "COALESCE(mrp,0)",
        "COALESCE(type,'')",
    ]
    keys = ["id", "name", "batch", "expiry", "stock", "unit", "mrp", "type"]
    if "schedule" in cols:
        select.append("COALESCE(schedule,'')")
        keys.append("schedule")

    # Names that START with what was typed first, then a word starting with it,
    # then the rest -- alphabetical inside each group. Plain alphabetical put
    # AMOXY and BECOSULES above MECOVET when the shop typed "m".
    from core.name_search_rank import order_by_sql

    _order_sql, _order_params = order_by_sql("name", q)
    sql = (
        f"SELECT {', '.join(select)} FROM medicines "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {_order_sql}, id ASC LIMIT ?"
    )
    params.extend(_order_params)
    params.append(limit * 8)  # fetch more rows then aggregate
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return {"names": []}

    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = {keys[i]: row[i] for i in range(len(keys))}
        name = (d.get("name") or "").strip()
        if not name:
            continue
        exp = str(d.get("expiry") or "")
        try:
            if exp and is_expired_as_of(exp, as_of):
                continue
        except Exception:
            pass
        mid = int(d["id"])
        stock = _safe_float(d.get("stock"))
        avail = max(0.0, stock - _reserved_for(mid))
        if avail <= 0:
            continue
        cur = by_name.get(name)
        if not cur:
            by_name[name] = {
                "name": name,
                "stock": avail,
                "mrp": _safe_float(d.get("mrp")),
                "unit": d.get("unit") or "1",
                "type": d.get("type") or "",
                "schedule": str(d.get("schedule") or ""),
                "batch_count": 1,
            }
        else:
            cur["stock"] = _safe_float(cur["stock"]) + avail
            cur["batch_count"] = int(cur.get("batch_count") or 0) + 1
            if _safe_float(d.get("mrp")) > 0 and not cur.get("mrp"):
                cur["mrp"] = _safe_float(d.get("mrp"))

    names = list(by_name.values())
    if not show_zero:
        names = [n for n in names if _safe_float(n.get("stock")) > 0]
    names.sort(key=lambda n: str(n.get("name") or "").upper())
    return {"names": names[:limit]}


def list_batches_for_name(
    conn,
    name: str,
    *,
    bill_date: Any = None,
    reserved: Optional[dict[str, float]] = None,
    include_zero_stock: bool = False,
) -> dict[str, Any]:
    """Batches for a medicine name (classic step-2 picker data).

    Expired and out-of-stock (after reserved) batches are hidden unless
    ``include_zero_stock`` is explicitly True.
    """
    from core.batch_visibility import is_expired_as_of, medicine_existed_sql

    name = (name or "").strip()
    if not name:
        return {"name": "", "batches": []}

    # Sales dropdown: hide OOS by default (ignore prefs that would show zeros).
    show_zero = bool(include_zero_stock)
    as_of = _parse_bill_date(bill_date)
    reserved = reserved or {}

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_catalog import batches_for_name

            # Catalog returns raw stock; apply reserved + expiry like offline SQLite path.
            raw = batches_for_name(name, include_zero=True, as_of=as_of)
            batches: list[dict[str, Any]] = []
            for b in raw:
                mid = int(b.get("id") or 0)
                stock = _safe_float(b.get("stock"))
                avail = stock - _safe_float(
                    reserved.get(str(mid), reserved.get(mid, 0))
                )
                exp = str(b.get("expiry") or "")
                try:
                    if exp and is_expired_as_of(exp, as_of):
                        continue
                except Exception:
                    pass
                if avail <= 0 and not show_zero:
                    continue
                row = dict(b)
                row["stock"] = stock
                row["available"] = max(0.0, avail)
                batches.append(row)
            return {
                "name": name,
                "batches": batches,
                "as_of": as_of.isoformat(),
            }
    except Exception as exc:
        try:
            import logging

            logging.getLogger(__name__).warning(
                "online list_batches_for_name(%r): %s", name, exc
            )
        except Exception:
            pass

    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(medicines)").fetchall()
    }
    where = ["UPPER(TRIM(name))=UPPER(TRIM(?))"]
    params: list[Any] = [name]
    if "is_hidden" in cols:
        where.append("COALESCE(is_hidden,0)=0")
    if "created_at" in cols:
        where.append(medicine_existed_sql())
        params.append(as_of.isoformat())

    select = [
        "id",
        "name",
        "COALESCE(batch_no,'')",
        "COALESCE(expiry_date,'')",
        "COALESCE(stock_qty,0)",
        "COALESCE(type,'')",
        "COALESCE(unit,'1')",
        "COALESCE(mrp,0)",
        "COALESCE(rate,0)",
    ]
    keys = ["id", "name", "batch", "expiry", "stock", "type", "unit", "mrp", "rate"]
    if "gst_percent" in cols:
        select.append("COALESCE(gst_percent,0)")
        keys.append("gst_percent")
    if "schedule" in cols:
        select.append("COALESCE(schedule,'')")
        keys.append("schedule")
    if "location" in cols:
        select.append("COALESCE(location,'')")
        keys.append("location")

    order = "expiry_date ASC, id ASC"
    try:
        from core.sales_medicine_prefs import BATCH_NEWEST_FIRST, load_batch_sort_order

        if load_batch_sort_order() == BATCH_NEWEST_FIRST:
            order = "expiry_date DESC, id DESC"
    except Exception:
        pass

    rows = conn.execute(
        f"SELECT {', '.join(select)} FROM medicines "
        f"WHERE {' AND '.join(where)} ORDER BY {order}",
        params,
    ).fetchall()

    batches: list[dict[str, Any]] = []
    for row in rows:
        d = {keys[i]: row[i] for i in range(len(keys))}
        mid = int(d["id"])
        stock = _safe_float(d.get("stock"))
        avail = stock - _safe_float(reserved.get(str(mid), reserved.get(mid, 0)))
        exp = str(d.get("expiry") or "")
        expired = False
        try:
            expired = bool(exp and is_expired_as_of(exp, as_of))
        except Exception:
            expired = False
        if expired:
            continue
        if avail <= 0 and not show_zero:
            continue
        batches.append(
            {
                "id": mid,
                "name": d.get("name") or name,
                "batch": d.get("batch") or "",
                "expiry": exp,
                "stock": stock,
                "available": max(0.0, avail),
                "type": d.get("type") or "",
                "unit": d.get("unit") or "1",
                "mrp": _safe_float(d.get("mrp")),
                "rate": _safe_float(d.get("rate")),
                "gst_percent": _safe_float(d.get("gst_percent")),
                "schedule": str(d.get("schedule") or ""),
                "location": _fmt_location(d.get("location")),
            }
        )
    return {"name": name, "batches": batches, "as_of": as_of.isoformat()}


def build_line(conn, body: dict[str, Any]) -> dict[str, Any]:
    """
    Build one sales line using classic rate/stock/expiry rules.
    Body: medicine_id, qty, medicine_discount, bill_date, reserved (optional),
          doctor_name, customer_name (for schedule checks).
    """
    from core.batch_visibility import is_expired_as_of, medicine_existed_as_of
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    from core.margin_utils import (
        check_item_discount_loss,
        enrich_medicine_margin_fields,
        line_net_margin,
        margin_loss_warning_enabled,
    )

    medicine_id = _safe_int(body.get("medicine_id"))
    if medicine_id <= 0:
        return {"ok": False, "error": "Select a medicine batch."}

    qty = _safe_int(body.get("qty"))
    if qty <= 0:
        return {"ok": False, "error": "Please enter a valid quantity."}

    med = _medicine_row(conn, medicine_id)
    if not med:
        return {"ok": False, "error": "Medicine not found."}

    doctor = str(body.get("doctor_name") or "").strip()
    customer = str(body.get("customer_name") or "").strip()
    if med.get("schedule") and not doctor:
        try:
            from core.billing_layout_prefs import schedule_requires_doctor
            needs_doc = schedule_requires_doctor(med.get("schedule"))
        except Exception:
            needs_doc = True
        if needs_doc:
            return {
                "ok": False,
                "error": "Please select a doctor for scheduled medicines.",
                "code": "doctor_required",
            }
    if med.get("schedule") and not customer:
        return {
            "ok": False,
            "error": "Scheduled medicine requires a customer name.",
            "code": "customer_required",
        }

    bill_date = _parse_bill_date(body.get("bill_date"))
    from core.sale_availability import batch_existed_on

    # Online too: the catalogue rows carry no created_at, so this used to pass every
    # batch there. core.sale_availability asks the store instead.
    if not batch_existed_on(conn, medicine_id, bill_date, created_at=med.get("created_at")):
        return {
            "ok": False,
            "error": (
                f"{med['name']} was added after {bill_date.isoformat()}. "
                "It cannot be sold on this bill date."
            ),
            "code": "not_available_yet",
        }
    try:
        if med.get("expiry") and is_expired_as_of(str(med["expiry"]), bill_date):
            return {
                "ok": False,
                "error": f"{med['name']} expired on {med['expiry']}.",
                "code": "expired",
            }
    except Exception:
        pass

    reserved_map = body.get("reserved") or {}
    reserved_qty = _safe_float(
        reserved_map.get(str(medicine_id), reserved_map.get(medicine_id, 0))
    )
    available = med["stock"] - reserved_qty
    if available <= 0:
        return {
            "ok": False,
            "error": f"{med['name']} is fully reserved in this bill.",
            "code": "out_of_stock",
        }
    if qty > available:
        return {
            "ok": False,
            "error": f"Only {int(available)} units available.",
            "code": "insufficient_stock",
        }

    med_type = med.get("type") or ""
    unit_value = med.get("unit") or "1"
    list_mrp = _safe_float(med.get("mrp"))
    purchase_rate = _safe_float(med.get("rate"))

    if is_strip_count_type(med_type):
        try:
            ups = parse_tablets_per_stripe(unit_value)
            rate = list_mrp / ups
        except (ValueError, ZeroDivisionError):
            rate = list_mrp
    else:
        rate = list_mrp

    base = round(qty * rate, 2)
    from core.billing_layout_prefs import (
        ITEM_DISCOUNT_PERCENT,
        convert_item_discount_input_to_rupees,
        load_item_discount_mode,
    )

    mode = load_item_discount_mode()
    if body.get("disc_pct") is not None and _safe_float(body.get("disc_pct")) > 0:
        med_disc_rs = convert_item_discount_input_to_rupees(
            _safe_float(body.get("disc_pct")), base, mode=ITEM_DISCOUNT_PERCENT
        )
    else:
        med_disc_rs = convert_item_discount_input_to_rupees(
            _safe_float(body.get("medicine_discount")), base, mode=mode
        )
    amount = round(base - med_disc_rs, 2)

    # A qty / discount edit on a line already on the bill is rebuilt here, and the
    # Sales page sends that line's gst_percent so the rate it was sold at stays. A
    # new line, or one moved to another batch, takes the medicine's rate today.
    kept_gst = body.get("gst_percent")
    kept_gst = _safe_float(kept_gst) if kept_gst not in (None, "") else None

    line = {
        "id": med["id"],
        "name": med["name"],
        "batch": med.get("batch") or "",
        "expiry": med.get("expiry") or "",
        "qty": qty,
        "rate": round(rate, 4),
        "amount": amount,
        "original_amount": base,
        "medicine_discount": med_disc_rs,
        "schedule": med.get("schedule") or "",
        "type": med_type,
        "display_type": med_type or "N/A",
        "gst_percent": kept_gst if kept_gst is not None else (med.get("gst_percent") or 0),
        "location": med.get("location") or "",
        "unit": unit_value,
        "mrp": list_mrp,
    }
    enrich_medicine_margin_fields(line, list_mrp, purchase_rate, med_type, unit_value)
    line["margin"] = round(line_net_margin(line), 2)
    line["margin_pct"] = round(float(line.get("margin_pct") or 0), 2)

    warnings: list[str] = []
    if margin_loss_warning_enabled():
        bad, msg = check_item_discount_loss(line)
        if bad and msg:
            warnings.append(msg)

    return {"ok": True, "line": line, "warnings": warnings, "available": available}


def calc_sale(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Preview bill + payment totals using calc_engine."""
    from core.calc_engine import calc_bill_summary, calc_payment_result
    from core.margin_utils import (
        check_overall_discount_loss,
        margin_loss_warning_enabled,
    )

    items = body.get("items") or []
    if not isinstance(items, list):
        items = []

    medicines = []
    for it in items:
        if not isinstance(it, dict):
            continue
        medicines.append(
            {
                "id": it.get("id"),
                "amount": _safe_float(it.get("amount")),
                "medicine_discount": _safe_float(
                    it.get("medicine_discount", it.get("disc", 0))
                ),
                "qty": _safe_float(it.get("qty")),
                "rate": _safe_float(it.get("rate")),
                "original_amount": _safe_float(
                    it.get("original_amount", _safe_float(it.get("qty")) * _safe_float(it.get("rate")))
                ),
                # list_mrp / purchase_rate / margin_div: without them the
                # overall-discount loss check read every line's margin as
                # MRP x qty (purchase rate 0) and never warned.
                **{
                    k: it.get(k)
                    for k in (
                        "name", "batch", "type", "unit", "mrp",
                        "list_mrp", "purchase_rate", "margin_div",
                    )
                    if k in it
                },
            }
        )

    disc_pct = _safe_float(body.get("discount_pct"))
    disc_rs_raw = body.get("discount_rs")
    disc_rs = None if disc_rs_raw is None or disc_rs_raw == "" else _safe_float(disc_rs_raw)

    rounding = _sale_rounding_amount(
        medicines, disc_pct, disc_rs, body, default_auto=True
    )

    summary = calc_bill_summary(medicines, disc_pct, rounding, discount_rs=disc_rs)

    payment_mode = str(body.get("payment_mode") or "Cash").strip().lower()
    is_due = payment_mode == "due"
    cash = 0.0 if is_due else _safe_float(body.get("cash_paid"))
    online = 0.0 if is_due else _safe_float(body.get("online_paid"))

    prev_due = _safe_float(body.get("previous_due"))
    prev_credit = _safe_float(body.get("previous_credit"))
    customer_id = _safe_int(body.get("customer_id"))
    customer_name = str(body.get("customer_name") or body.get("customer") or "").strip()
    # Prefer live party balance (online catalog / SQLite) so Previous Due matches
    # Classic -- but NOT on the live totals preview.
    #
    # Resolving it Online re-pulls the customer table past its cache and then
    # reads thousands of sales, receipts and returns to run a FIFO allocation,
    # over the network, twice per call. On every keystroke that is seconds, and
    # it is why the totals trailed behind the row the shop had just edited. The
    # balance does not change with what is being typed: the page resolves it when
    # the customer is picked and sends it as previous_due, and the save path
    # recomputes it for real.
    want_party_due = not bool(body.get("skip_party_due"))
    try:
        from core.customer_service import get_customer_by_name
        from core.sync_prefs import is_online_mode

        found = None
        if not want_party_due:
            found = None
        elif customer_name:
            found = get_customer_by_name(
                conn, customer_name, force_refresh=bool(is_online_mode())
            )
        elif customer_id > 0:
            try:
                from core.sync_prefs import is_online_mode as _online

                if _online():
                    from core.online_catalog import find_customer_by_id

                    found = find_customer_by_id(customer_id)
            except Exception:
                found = None
            if not found:
                row = conn.execute(
                    "SELECT COALESCE(total_due,0), COALESCE(total_credit,0), "
                    "COALESCE(name,''), COALESCE(phone,''), COALESCE(address,'') "
                    "FROM customers WHERE id=?",
                    (customer_id,),
                ).fetchone()
                if row:
                    found = {
                        "total_due": row[0],
                        "total_credit": row[1],
                        "id": customer_id,
                        "name": row[2],
                        "phone": row[3],
                        "address": row[4],
                    }
        if found:
            prev_due = _safe_float(found.get("total_due", found.get("due")))
            prev_credit = _safe_float(
                found.get("total_credit", found.get("credit"))
            )
            try:
                # Only when the caller actually wants the live figure. This is a
                # second full pass over the customer's sales, receipts and
                # returns on top of the catalog pull just above -- six server
                # queries where the preview needs none.
                if want_party_due and is_online_mode():
                    from core.desktop_settings_service import (
                        online_customer_remaining_due,
                    )

                    live = online_customer_remaining_due(
                        _safe_int(found.get("id") or customer_id),
                        str(found.get("name") or customer_name or ""),
                    )
                    if live is not None:
                        prev_due = _safe_float(live)
                        if prev_due <= 0.01:
                            prev_credit = 0.0
            except Exception:
                pass
            if not customer_id:
                customer_id = _safe_int(found.get("id"))
    except Exception:
        if customer_id > 0:
            try:
                row = conn.execute(
                    "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) "
                    "FROM customers WHERE id=?",
                    (customer_id,),
                ).fetchone()
                if row:
                    prev_due = _safe_float(row[0])
                    prev_credit = _safe_float(row[1])
            except Exception:
                pass

    pay = calc_payment_result(
        summary["total_amount"], cash, online, prev_due, prev_credit
    )

    warnings: list[str] = []
    if margin_loss_warning_enabled() and medicines:
        bad, msg = check_overall_discount_loss(
            medicines, summary["discount_amount"]
        )
        if bad and msg:
            warnings.append(msg)

    return {
        "ok": True,
        "summary": summary,
        "payment": pay,
        "cash_paid": cash,
        "online_paid": online,
        "previous_due": prev_due,
        "previous_credit": prev_credit,
        "rounding": rounding,
        "is_due": is_due,
        "warnings": warnings,
        "gst_label": "Included in MRP",
    }


def _normalize_medicines_for_save(items: list) -> list[dict[str, Any]]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        mid = _safe_int(it.get("id"))
        if mid <= 0 and not it.get("quick_add"):
            continue
        qty = _safe_int(it.get("qty"))
        rate = _safe_float(it.get("rate"))
        disc = _safe_float(it.get("medicine_discount", it.get("disc", 0)))
        amount = _safe_float(it.get("amount"))
        if amount <= 0 and qty > 0:
            amount = round(qty * rate - disc, 2)
        out.append(
            {
                "id": mid or None,
                "name": str(it.get("name") or it.get("medicine") or ""),
                "batch": str(it.get("batch") or ""),
                "expiry": str(it.get("expiry") or ""),
                "qty": qty,
                "rate": rate,
                "amount": amount,
                "original_amount": _safe_float(
                    it.get("original_amount", qty * rate)
                ),
                "medicine_discount": disc,
                "schedule": str(it.get("schedule") or ""),
                "type": str(it.get("type") or ""),
                "display_type": str(it.get("display_type") or it.get("type") or "N/A"),
                # A line with no rate stays blank (None -> NULL): an old bill's NULL
                # line used to be saved back as an exempt 0%.
                "gst_percent": (
                    None if it.get("gst_percent") in (None, "")
                    else _safe_float(it.get("gst_percent"))
                ),
                "location": str(it.get("location") or ""),
                "unit": str(it.get("unit") or "1"),
                "quick_add": bool(it.get("quick_add")),
                "mrp": _safe_float(it.get("mrp")),
            }
        )
    return out


def _discount_loss_messages(medicines: list, overall_discount: float) -> list[str]:
    from core.margin_utils import (
        check_item_discount_loss,
        check_overall_discount_loss,
        margin_loss_warning_enabled,
    )

    if not margin_loss_warning_enabled():
        return []
    msgs = []
    for med in medicines:
        bad, msg = check_item_discount_loss(med)
        if bad and msg:
            msgs.append(msg)
    bad, msg = check_overall_discount_loss(medicines, overall_discount)
    if bad and msg:
        msgs.append(msg)
    return msgs


def _save_discount_loss_messages(raw_items: list, overall_discount: float) -> list[str]:
    """Classic's validate_bill_discounts for a Tauri save, on the lines' real cost.

    _normalize_medicines_for_save keeps only what is stored, so list_mrp and
    purchase_rate were gone by the time the save checked for a loss: every
    line's margin read as MRP x qty and the warning never fired. The check runs
    on copies that keep the cost the screen holds. A line with no known cost (a
    resumed draft) is left out, as the screen's margin leaves it out."""
    lines = []
    for it in raw_items or []:
        if not isinstance(it, dict) or it.get("purchase_rate") in (None, ""):
            continue
        norm = _normalize_medicines_for_save([it])
        if not norm:
            continue
        line = dict(norm[0])
        for k in ("list_mrp", "purchase_rate", "margin_div"):
            if it.get(k) not in (None, ""):
                line[k] = it.get(k)
        lines.append(line)
    if not lines:
        return []
    return _discount_loss_messages(lines, overall_discount)


def _remember_doctor_online(name: str, phone: str) -> None:
    """Keep the doctor a sale names, with the number typed for them, on the server.

    Offline the bill save already does this (billing_service upserts the doctors
    table). Online the engine's database is an empty shell, and the sale carried
    the doctor's name on the bill row only: a new doctor never joined the list,
    and the number typed beside them was thrown away altogether. Best effort and
    off the counter's path -- a slow link must not hold up the bill.
    """
    doc_name = str(name or "").strip().upper()
    doc_phone = str(phone or "").strip()
    if not doc_name:
        return
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return
    except Exception:
        return

    def _work():
        try:
            from core.online_catalog import doctors, patch_docs
            from core.server_crud import bump_meta, upsert_contact_online

            existing = next(
                (
                    d
                    for d in doctors() or []
                    if str(d.get("name") or "").strip().upper() == doc_name
                ),
                None,
            )
            if existing:
                if not doc_phone or str(existing.get("phone") or "").strip() == doc_phone:
                    return
                doc = bump_meta(dict(existing))
                doc["phone"] = doc_phone
            else:
                doc = {"name": doc_name, "phone": doc_phone, "registration_number": ""}
            did = upsert_contact_online("doctors", doc)
            doc["id"] = doc["local_id"] = did
            patch_docs("doctors", [doc])
        except Exception as exc:
            print(f"[sale] doctor {doc_name!r} not saved to the list: {exc}", flush=True)

    threading.Thread(target=_work, name="sale-doctor", daemon=True).start()


def save_sale(conn, body: dict[str, Any]) -> dict[str, Any]:
    """
    Persist a sale using classic billing_service (counter merge + stock).
    """
    from core.billing_service import append_counter_sale_today, save_new_bill
    from core.customer_service import COUNTER_SALE, get_or_create_customer
    from core.sales_form_prefs import load_payment_mode_enabled

    if not _has_pharmacy_profile(conn):
        return {
            "ok": False,
            "error": "Please set up pharmacy profile in Settings first.",
            "code": "profile_required",
        }

    medicines = _normalize_medicines_for_save(body.get("items") or [])
    if not medicines:
        return {
            "ok": False,
            "error": "Please add medicines to the bill.",
            "code": "no_medicines",
        }

    # An edit may not take a line below what was already returned against the
    # bill, nor remove it: that refund was paid on those units.
    guard_id = _safe_int(body.get("editing_sale_id"))
    if guard_id > 0 and not _safe_int(body.get("autosave_sale_id")):
        from core.desktop_returns_service import edit_below_returned_error

        why = edit_below_returned_error(conn, "sale", guard_id, medicines, id_key="id")
        if why:
            return {"ok": False, "error": why, "code": "below_returned"}

    customer_name = str(body.get("customer_name") or "").strip()
    if not customer_name:
        customer_name = COUNTER_SALE
    phone = str(body.get("customer_phone") or "").strip()
    address = str(body.get("customer_address") or "").strip()
    doctor_name = str(body.get("doctor_name") or "").strip()
    doctor_phone = str(body.get("doctor_phone") or "").strip()

    has_scheduled = any(str(m.get("schedule") or "").strip() for m in medicines)
    if has_scheduled and customer_name.upper() == COUNTER_SALE:
        # empty → counter is ok only if they typed nothing; scheduled needs real name
        if not str(body.get("customer_name") or "").strip():
            return {
                "ok": False,
                "error": "Scheduled medicine requires a customer name.",
                "code": "customer_required",
            }
    try:
        from core.billing_layout_prefs import sale_requires_doctor
        needs_doc = sale_requires_doctor(medicines)
    except Exception:
        needs_doc = has_scheduled
    if needs_doc and not doctor_name:
        return {
            "ok": False,
            "error": "Scheduled medicine requires a doctor name.",
            "code": "doctor_required",
        }

    payment_mode = str(body.get("payment_mode") or "Cash").strip()
    mode_l = payment_mode.lower()
    if load_payment_mode_enabled() and mode_l not in ("cash", "due", ""):
        # Classic only allows Cash/Due when the field is enabled
        if mode_l not in ("cash", "due"):
            # Treat Online/UPI as Cash payment semantics (paid via cash/online fields)
            pass

    is_due = mode_l == "due"
    cash = 0.0 if is_due else _safe_float(body.get("cash_paid"))
    online = 0.0 if is_due else _safe_float(body.get("online_paid"))

    disc_pct_pre = _safe_float(body.get("discount_pct"))
    disc_rs_pre = _safe_float(body.get("discount_rs"))
    if not is_due and (cash + online) <= 0 and bool(body.get("pay_full")):
        # The counter finished the bill with Enter and never split the payment,
        # so the whole amount was taken in cash. The figure has to be worked out
        # HERE rather than sent from the screen: the screen's total comes from a
        # debounced preview, and a rounding or discount typed a moment before
        # Enter would not be in it yet -- the bill would then be banked for the
        # amount it had a fraction of a second earlier, leaving a phantom credit
        # or a due against someone who had paid in full.
        from core.calc_engine import calc_bill_summary

        _pre = calc_bill_summary(medicines, disc_pct_pre, 0, discount_rs=disc_rs_pre)
        _round = _sale_rounding_amount(
            medicines, disc_pct_pre, disc_rs_pre, body, default_auto=True
        )
        cash = round(_safe_float(_pre.get("pre_round_total")) + _round, 2)

    if not is_due and (cash + online) <= 0:
        return {
            "ok": False,
            "error": "Cash or Online amount must be greater than zero.",
            "code": "payment_required",
        }

    disc_pct = _safe_float(body.get("discount_pct"))
    disc_rs = _safe_float(body.get("discount_rs"))
    rounding = _sale_rounding_amount(
        medicines, disc_pct, disc_rs, body, default_auto=True
    )
    bill_date = _parse_bill_date(body.get("bill_date"))
    previous_due = _safe_float(body.get("previous_due"))

    if not (guard_id > 0 and not _safe_int(body.get("autosave_sale_id"))):
        # A back-dated bill may only carry batches the shop had on that day and that had
        # not expired by then. The picker checks a line when it is added, but the Bill
        # Date can be changed afterwards, so the save checks again. An edit of an
        # existing bill is left as it was sold.
        from core.sale_availability import lines_unavailable_on

        problems = lines_unavailable_on(conn, medicines, bill_date)
        if problems:
            return {
                "ok": False,
                "error": " ".join(problems),
                "code": "not_available_on_date",
                "messages": problems,
            }

    loss_msgs = _save_discount_loss_messages(body.get("items") or [], disc_rs)
    if loss_msgs and not body.get("confirm_discount_loss"):
        return {
            "ok": False,
            "need_confirm": True,
            "code": "discount_loss",
            "error": "Discount may cause a loss. Confirm to continue.",
            "messages": loss_msgs,
        }

    # The bill is saved as typed. Paid more than the bill and the old due together is only
    # pointed out (store 4 SCB1061: Rs 210 cash plus Rs 210 online on a Rs 210 bill).
    try:
        from core.calc_engine import calc_bill_summary
        from core.save_warnings import sale_warnings

        old_due = previous_due
        if (
            guard_id > 0
            and not _safe_int(body.get("autosave_sale_id"))
            and body.get("edit_previous_due") not in (None, "")
        ):
            old_due = _safe_float(body.get("edit_previous_due"))
        warnings = sale_warnings(
            total=calc_bill_summary(medicines, disc_pct, rounding, discount_rs=disc_rs)[
                "total_amount"
            ],
            cash_paid=cash,
            online_paid=online,
            previous_due=old_due,
        )
    except Exception:
        warnings = []

    try:
        with _SALE_WRITE_LOCK:
            customer_id = get_or_create_customer(conn, customer_name, phone, address)
            editing_id = _safe_int(body.get("editing_sale_id"))
            autosave_id = _safe_int(body.get("autosave_sale_id"))
            merged_flag = False

            if editing_id > 0 and not autosave_id:
                from core.billing_service import update_existing_bill

                snap_due = body.get("edit_previous_due")
                prev_for_edit = (
                    _safe_float(snap_due)
                    if snap_due is not None and snap_due != ""
                    else previous_due
                )
                # The number the edit answers with. Online the engine's SQLite is an
                # empty :memory: database, so reading it there found nothing and the
                # answer was the sale id ("Bill 37635 saved." for SCB1378). The server's
                # copy is read once and handed to the edit, which would read it anyway.
                bill_no = ""
                edit_doc = None
                try:
                    from core.sync_prefs import is_online_mode as _online_edit

                    online_edit = bool(_online_edit())
                except Exception:
                    online_edit = False
                if online_edit and editing_id < 0:
                    bill_no = "PENDING"
                elif online_edit:
                    try:
                        from core.server_crud import get_doc

                        edit_doc = get_doc("sales", int(editing_id)) or None
                    except Exception:
                        edit_doc = None
                    if edit_doc and str(edit_doc.get("bill_no") or "").strip():
                        from core.fy_serial import display_sales_bill_no

                        bill_no = display_sales_bill_no(str(edit_doc.get("bill_no")))
                if not bill_no:
                    row = conn.execute(
                        "SELECT bill_no FROM sales WHERE id=?", (editing_id,)
                    ).fetchone()
                    bill_no = row[0] if row else str(editing_id)
                update_existing_bill(
                    conn,
                    editing_id,
                    medicines,
                    discount_pct=disc_pct,
                    rounding=rounding,
                    cash_paid=cash,
                    online_paid=online,
                    customer_name=customer_name,
                    customer_phone=phone,
                    doctor_name=doctor_name,
                    previous_due=prev_for_edit,
                    discount_rs=disc_rs,
                    bill_date=bill_date,
                    existing_doc=edit_doc,
                )
                sale_id = editing_id
            elif autosave_id > 0 or body.get("autosave_token"):
                from core.autosave_bill import write_autosave_bill
                from core.autosave_session import own_session

                token = str(body.get("autosave_token") or "")
                live = own_session(token, autosave_id)
                # A token always finishes through the engine, live record or
                # not: a stale token must not reach finalize_autosave_bill,
                # which rewrites a real bill id (the day's counter bill) with
                # this form's lines; and a token whose first tick is still in
                # flight must leave a tombstone, or that tick opens bill two.
                if live is not None or token:
                    # The autosaved bill is already REAL. Saving is the last
                    # update of that same bill -- it must not take a second
                    # number, and a counter sale must not be added to the day
                    # bill a second time.
                    res = write_autosave_bill(
                        conn,
                        token=token or str(live.get("token") or ""),
                        sale_id=autosave_id,
                        customer_id=customer_id,
                        customer_name=customer_name,
                        customer_phone=phone,
                        customer_address=address,
                        medicines=medicines,
                        discount_pct=disc_pct,
                        discount_rs=disc_rs,
                        rounding=rounding,
                        cash_paid=cash,
                        online_paid=online,
                        doctor_name=doctor_name,
                        doctor_phone=doctor_phone,
                        previous_due=previous_due,
                        bill_date=bill_date,
                        final=True,
                    )
                    bill_no = res.get("bill_no") or ""
                    sale_id = int(res.get("sale_id") or 0)
                    merged_flag = bool(res.get("counter")) and not res.get("created")
                    autosave_id = 0
                else:
                    # An ASV draft written by a build from before autosave made
                    # real bills. Still finalise it the old way.
                    from core.billing_service import finalize_autosave_bill

                    bill_no, sale_id = finalize_autosave_bill(
                        conn,
                        autosave_id,
                        customer_id,
                        medicines,
                        disc_pct,
                        rounding,
                        cash,
                        online,
                        doctor_name,
                        doctor_phone,
                        customer_name,
                        phone,
                        previous_due,
                        discount_rs=disc_rs,
                        bill_date=bill_date,
                    )
            else:
                merged = append_counter_sale_today(
                    conn=conn,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    medicines=medicines,
                    discount_pct=disc_pct,
                    discount_rs=disc_rs,
                    rounding=rounding,
                    cash_paid=cash,
                    online_paid=online,
                    doctor_name=doctor_name,
                    doctor_phone=doctor_phone,
                    previous_due=previous_due,
                    bill_date=bill_date,
                )
                if merged:
                    bill_no, sale_id = merged
                    merged_flag = True
                else:
                    bill_no, sale_id = save_new_bill(
                        conn=conn,
                        customer_id=customer_id,
                        medicines=medicines,
                        discount_pct=disc_pct,
                        discount_rs=disc_rs,
                        rounding=rounding,
                        cash_paid=cash,
                        online_paid=online,
                        doctor_name=doctor_name,
                        doctor_phone=doctor_phone,
                        previous_due=previous_due,
                        bill_date=bill_date,
                        # F7/F8 need a real bill number and id to print. Without this the
                        # Online path queues the sale and returns ("PENDING", a negative
                        # id), which the print step cannot resolve.
                        # Always try the direct server write in Online mode. It
                        # returns the REAL bill number, so the counter sees
                        # "Saved SCB9" instead of "Saved PENDING" and can tell the
                        # customer their bill number. It falls back to the queue by
                        # itself if the server is unreachable, so nothing is lost.
                        sync=True,
                    )

        try:
            from core.sync_prefs import is_online_mode

            online = bool(is_online_mode())
        except Exception:
            online = False
        if not online:
            row = conn.execute(
                "SELECT id FROM sales WHERE id=?", (int(sale_id),)
            ).fetchone()
            if not row:
                return {
                    "ok": False,
                    "error": f"Sale id {sale_id} was not written — try Save again.",
                }

        # Fresh customer balance for UI
        due = previous_due
        credit = 0.0
        try:
            crow = conn.execute(
                "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) "
                "FROM customers WHERE id=?",
                (customer_id,),
            ).fetchone()
            if crow:
                due = _safe_float(crow[0])
                credit = _safe_float(crow[1])
        except Exception:
            pass

        sid = int(sale_id)
        try:
            import threading

            threading.Thread(
                target=lambda: _try_save_sale_pdf(conn, sid),
                daemon=True,
                name=f"sale-pdf-{sid}",
            ).start()
        except Exception:
            pass

        # Where the bill is being written. The PDF itself is produced on a
        # daemon thread whose return value nobody keeps, so the exact filename
        # cannot be promised here -- a merged or edited bill can change the
        # stem, and a long bill is split across _p1.._pN. The FOLDER is exact,
        # and it is the thing the counter actually asks: "where did it go?".
        # It is the RESOLVED folder, not the Settings string: a path from
        # another machine, or one that cannot be created, silently falls back
        # to Downloads, and pointing at a folder the bill is not in is worse
        # than saying nothing.
        # ...but only when there is going to BE a file. When the link to the
        # server is down, an online save falls back to the queue and hands back
        # bill_no "PENDING" with a negative id; the PDF thread then has nothing
        # to render from and quietly gives up. Naming a folder there sends the
        # counter to hunt for a bill that was never written -- worse than the
        # silence this replaces.
        pdf_dir = ""
        if int(sale_id) > 0 and str(bill_no).strip().upper() != "PENDING":
            try:
                from core.bill_save_prefs import resolve_sales_bill_save_dir

                pdf_dir = resolve_sales_bill_save_dir()
            except Exception:
                pdf_dir = ""

        _remember_doctor_online(doctor_name, doctor_phone)
        return {
            "ok": True,
            "bill_no": bill_no,
            "sale_id": int(sale_id),
            "customer_id": int(customer_id),
            "merged_counter": merged_flag,
            "customer_due": due,
            "customer_credit": credit,
            "next_bill_hint": _next_hint(conn),
            # Reserved: it would mean "confirmed on disk", and nothing can set
            # it until the PDF thread reports back. Use pdf_dir instead.
            "pdf_path": None,
            "pdf_dir": pdf_dir,
            # Things that look mistyped. The bill is saved regardless.
            "warnings": warnings,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to save sale: {exc}"}


def _next_hint(conn) -> str:
    try:
        from core.desktop_pages_service import _next_sales_bill_hint

        return _next_sales_bill_hint(conn)
    except Exception:
        return ""


def recent_sales(conn, limit: int = 5) -> dict[str, Any]:
    from core.billing_service import fetch_recent_sales

    rows = fetch_recent_sales(conn, limit=limit) or []
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append(r)
            continue
        try:
            out.append(
                {
                    "id": r[0],
                    "bill_no": r[1],
                    "bill_date": r[2],
                    "customer": r[3] if len(r) > 3 else "",
                    "total": _safe_float(r[4]) if len(r) > 4 else 0,
                }
            )
        except Exception:
            continue
    return {"ok": True, "sales": out}


def last_sale(conn) -> dict[str, Any]:
    from core.billing_service import fetch_last_sale_id

    sale_id = fetch_last_sale_id(conn)
    if not sale_id:
        return {"ok": False, "error": "No previous sale found.", "code": "not_found"}
    return load_sale(conn, int(sale_id))


def _returns_on_edit(conn, sale_id: int, medicines: list) -> dict[str, Any]:
    """The returns note and each line's returned qty for a bill opened to edit."""
    from core.desktop_returns_service import edit_returns_info

    return edit_returns_info(conn, "sale", int(sale_id), medicines, id_key="id")


def load_sale(conn, sale_id: int) -> dict[str, Any]:
    """JSON form of load_sale_into_billing_page for Tauri edit/F10/F11."""
    from core.billing_service import load_sale_medicines
    from core.margin_utils import line_net_margin
    from core.sales_form_io import _enrich_med_from_db

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return _load_sale_online_json(conn, int(sale_id))
    except Exception as exc:
        print(f"[desktop_sales] online load failed: {exc}")

    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, s.bill_no, s.customer_id, s.bill_date, s.total_amount,
               s.discount, s.amount_paid,
               COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
               s.previous_due, s.total_due, s.due_amount, s.credit_amount,
               s.doctor_name, c.name, c.phone,
               COALESCE(s.previous_credit,0), COALESCE(c.address, ''),
               COALESCE(s.discount_pct,0), COALESCE(s.rounding,0),
               COALESCE(s.is_autosave,0)
        FROM sales s JOIN customers c ON s.customer_id=c.id
        WHERE s.id=?
        """,
        (int(sale_id),),
    )
    row = cur.fetchone()
    if not row:
        return {"ok": False, "error": "Sale not found.", "code": "not_found"}

    (
        sid,
        bill_no,
        customer_id,
        bill_date,
        _total,
        discount,
        _paid,
        cash_paid,
        online_paid,
        previous_due,
        _tdue,
        _due,
        _credit,
        doctor_name,
        customer_name,
        customer_phone,
        previous_credit,
        customer_address,
        discount_pct,
        rounding,
        is_autosave,
    ) = row

    medicines = load_sale_medicines(conn, sid)
    for med in medicines:
        _enrich_med_from_db(cur, med)
        try:
            from core.margin_utils import line_margin_percent

            med["margin"] = round(line_net_margin(med), 2)
            med["margin_pct"] = round(line_margin_percent(med), 2)
        except Exception:
            med["margin"] = 0.0
            med["margin_pct"] = 0.0
        if "original_amount" not in med:
            med["original_amount"] = round(
                _safe_float(med.get("qty")) * _safe_float(med.get("rate")), 2
            )

    cash_f = _safe_float(cash_paid)
    online_f = _safe_float(online_paid)
    payment_mode = "Due" if cash_f == 0 and online_f == 0 else "Cash"
    bill_dt = bill_date or ""
    if bill_dt and " " in str(bill_dt):
        bill_dt = str(bill_dt).split(" ")[0]

    return {
        "ok": True,
        "sale_id": int(sid),
        "bill_no": bill_no,
        "is_autosave": bool(is_autosave),
        "editing_sale_id": None if is_autosave else int(sid),
        "autosave_sale_id": int(sid) if is_autosave else None,
        **({} if is_autosave else _returns_on_edit(conn, int(sid), medicines)),
        "form": {
            "customer_name": customer_name or "",
            "customer_id": int(customer_id) if customer_id else None,
            "customer_phone": customer_phone or "",
            "customer_address": customer_address or "",
            "doctor_name": doctor_name or "",
            "doctor_phone": "",
            "bill_date": str(bill_dt or ""),
            "payment_mode": payment_mode,
            "cash_paid": cash_f,
            "online_paid": online_f,
            "discount_pct": _safe_float(discount_pct),
            "discount_rs": _safe_float(discount),
            "rounding": _safe_float(rounding),
            "previous_due": _safe_float(previous_due),
            "previous_credit": _safe_float(previous_credit),
            "items": medicines,
        },
        "edit_payment_snapshot": {
            "previous_due": _safe_float(previous_due),
            "previous_credit": _safe_float(previous_credit),
        },
    }


def _load_sale_online_json(conn, sale_id: int) -> dict[str, Any]:
    from core.billing_service import load_sale_medicines
    from core.margin_utils import enrich_medicine_margin_fields, line_net_margin, line_margin_percent
    from core.server_crud import get_doc
    from core.online_catalog import find_customer_by_id, medicine_by_id

    doc = get_doc("sales", int(sale_id)) or {}
    if not doc or doc.get("deleted"):
        return {"ok": False, "error": "Sale not found.", "code": "not_found"}

    customer_id = int(doc.get("customer_id") or 0)
    cust = find_customer_by_id(customer_id) or {}
    if not cust and customer_id:
        cust = get_doc("customers", customer_id) or {}

    medicines = load_sale_medicines(conn, sale_id)
    for med in medicines:
        mid = int(med.get("id") or 0)
        mp = medicine_by_id(mid) if mid else None
        if mp:
            list_mrp = float(mp.get("mrp") or med.get("rate") or 0)
            purchase_rate = float(mp.get("rate") or 0)
            enrich_medicine_margin_fields(
                med,
                list_mrp,
                purchase_rate,
                mp.get("type") or med.get("type"),
                mp.get("unit") or med.get("unit") or "1",
            )
            med["location"] = str(mp.get("location") or "").strip()
        try:
            med["margin"] = round(line_net_margin(med), 2)
            med["margin_pct"] = round(line_margin_percent(med), 2)
        except Exception:
            med["margin"] = 0.0
            med["margin_pct"] = 0.0
        if "original_amount" not in med:
            med["original_amount"] = round(
                _safe_float(med.get("qty")) * _safe_float(med.get("rate")), 2
            )

    cash_f = _safe_float(doc.get("cash_paid"))
    online_f = _safe_float(doc.get("online_paid"))
    payment_mode = "Due" if cash_f == 0 and online_f == 0 else "Cash"
    bill_dt = str(doc.get("bill_date") or "")
    if " " in bill_dt:
        bill_dt = bill_dt.split(" ")[0]
    is_autosave = bool(doc.get("is_autosave") or 0)
    sid = int(doc.get("id") or doc.get("local_id") or sale_id)
    previous_due = _safe_float(doc.get("previous_due"))
    previous_credit = _safe_float(doc.get("previous_credit"))

    return {
        "ok": True,
        "sale_id": sid,
        "bill_no": doc.get("bill_no") or "",
        "is_autosave": is_autosave,
        "editing_sale_id": None if is_autosave else sid,
        "autosave_sale_id": sid if is_autosave else None,
        **({} if is_autosave else _returns_on_edit(conn, sid, medicines)),
        "form": {
            "customer_name": cust.get("name") or doc.get("customer_name") or "",
            "customer_id": customer_id or None,
            "customer_phone": cust.get("phone") or doc.get("customer_phone") or "",
            "customer_address": cust.get("address") or doc.get("customer_address") or "",
            "doctor_name": doc.get("doctor_name") or "",
            "doctor_phone": "",
            "bill_date": bill_dt,
            "payment_mode": payment_mode,
            "cash_paid": cash_f,
            "online_paid": online_f,
            "discount_pct": _safe_float(doc.get("discount_pct")),
            "discount_rs": _safe_float(doc.get("discount")),
            "rounding": _safe_float(doc.get("rounding")),
            "previous_due": previous_due,
            "previous_credit": previous_credit,
            "items": medicines,
        },
        "edit_payment_snapshot": {
            "previous_due": previous_due,
            "previous_credit": previous_credit,
        },
    }


def sales_runtime_prefs(conn=None) -> dict[str, Any]:
    """Autosave + print button labels/keys from Settings (same files as Tk)."""
    from core.autosave_prefs import (
        load_autosave_enabled,
        load_autosave_interval_seconds,
    )
    from core.bill_config import get_print_slot_key, load_bill_print_settings
    from core.billing_layout_prefs import load_billing_layout_prefs
    from core.sales_form_prefs import load_payment_mode_enabled, load_payment_mode_position
    from core.upi_prefs import (
        load_upi_id,
        load_upi_qr_amount_mode,
        load_upi_qr_enabled,
    )

    settings = load_bill_print_settings()
    slot1 = settings.get("print_slot_1") or {}
    slot2 = settings.get("print_slot_2") or {}
    billing_prefs = load_billing_layout_prefs()
    show_location = False
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT show_location FROM shelf_settings LIMIT 1"
            ).fetchone()
            show_location = bool(row and row[0])
        except Exception:
            show_location = False
    out = {
        "ok": True,
        "autosave_enabled": load_autosave_enabled(),  # default False
        "autosave_interval_seconds": load_autosave_interval_seconds(),
        "payment_mode_enabled": load_payment_mode_enabled(),
        "payment_mode_position": load_payment_mode_position(),
        "item_discount_mode": str(billing_prefs.get("item_discount_mode") or "rupees"),
        "billing_show_item_discount": bool(
            billing_prefs.get("billing_show_item_discount", True)
        ),
        "billing_show_margin_column": bool(
            billing_prefs.get("billing_show_margin_column", True)
        ),
        "billing_show_total_margin": bool(
            billing_prefs.get("billing_show_total_margin", True)
        ),
        "billing_margin_loss_warning": bool(
            billing_prefs.get("billing_margin_loss_warning", True)
        ),
        "billing_margin_display_mode": str(
            billing_prefs.get("billing_margin_display_mode") or "rupees"
        ),
        "show_location": show_location,
        "column_visibility": {},
        "upi_qr_enabled": load_upi_qr_enabled(),
        "upi_id": load_upi_id(),
        "upi_qr_amount_mode": load_upi_qr_amount_mode(),
        "print_slot_1": {
            "label": (slot1.get("label") if isinstance(slot1, dict) else None)
            or "Print Sales 1",
            "key": get_print_slot_key(settings, 1) or "F7",
            "paper_size": (slot1.get("paper_size") if isinstance(slot1, dict) else None)
            or "A5",
            "copies": (slot1.get("copies") if isinstance(slot1, dict) else None) or 2,
        },
        "print_slot_2": {
            "label": (slot2.get("label") if isinstance(slot2, dict) else None)
            or "Print Sales 2",
            "key": get_print_slot_key(settings, 2) or "F8",
            "paper_size": (slot2.get("paper_size") if isinstance(slot2, dict) else None)
            or "A6",
            "copies": (slot2.get("copies") if isinstance(slot2, dict) else None) or 1,
        },
        "medicine_types": _medicine_types(),
        "medicine_type_pack_defaults": _medicine_type_pack_defaults(),
        "cash_online_enter_action": str(
            settings.get("cash_online_enter_action") or "save_bill"
        ),
        "due_rounding_enter_action": str(
            settings.get("due_rounding_enter_action") or "save_bill"
        ),
    }
    try:
        from core.column_config import get_column_visibility, TABLE_COLUMNS

        vis = dict(get_column_visibility("billing") or {})
        for name, _ in TABLE_COLUMNS.get("billing") or []:
            if name not in vis:
                vis[name] = True
        out["column_visibility"] = vis
    except Exception:
        pass
    return out


def _medicine_type_pack_defaults() -> dict[str, str]:
    """Per-type tablets-per-strip for the Quick Sale form's pack box."""
    try:
        from core.quick_sale_medicine import default_pack_for_type

        return {t: default_pack_for_type(t) for t in _medicine_types()}
    except Exception:
        return {}


def _medicine_types() -> list[str]:
    # The key is med_types. Reading "medicine_types" -- which load_layout has
    # never emitted -- meant Quick Sale always fell through to this literal, so
    # a shop that had added its own types could not pick one here. The fallback
    # also said "Other", which is not one of the app's types at all: a medicine
    # saved with it carries a value nothing else recognises.
    try:
        from core.layout_config import get_med_types

        types = list(get_med_types())
        if types:
            return [str(t) for t in types]
    except Exception:
        pass
    return ["Tablet", "Capsule", "Syrup", "Injection", "Powder", "Others"]


def build_quick_sale_line(body: dict[str, Any]) -> dict[str, Any]:
    from core.quick_sale_medicine import (
        build_quick_sale_row,
        prepare_quick_sale_row_for_display,
    )

    name = str(body.get("name") or "").strip()
    batch = str(body.get("batch") or "").strip()
    med_type = str(body.get("type") or "Tablet").strip()
    qty = _safe_int(body.get("qty"))
    if not name:
        return {"ok": False, "error": "Medicine name is required.", "code": "name_required"}
    if not batch:
        return {"ok": False, "error": "Batch is required.", "code": "batch_required"}
    if qty <= 0:
        return {"ok": False, "error": "Please enter a valid quantity.", "code": "qty"}
    # A quick-sale line is a real sale line, so it goes through the same gate as
    # build_line. The rule lived only in the browser before, which meant it knew
    # nothing about the shop's setting and nothing about H1/X being different.
    # Fails closed: an unreadable preference means "doctor required".
    schedule = str(body.get("schedule") or "").strip()
    doctor = str(body.get("doctor_name") or "").strip()
    if schedule and not doctor:
        try:
            from core.billing_layout_prefs import schedule_requires_doctor

            needs_doc = schedule_requires_doctor(schedule)
        except Exception:
            needs_doc = True
        if needs_doc:
            return {
                "ok": False,
                "error": "Please select a doctor for scheduled medicines.",
                "code": "doctor_required",
            }
    rate = body.get("rate")
    mrp = _safe_float(body.get("mrp"))
    try:
        row = build_quick_sale_row(
            name,
            med_type,
            batch,
            qty,
            body.get("pack_size") or body.get("unit") or 1,
            mrp,
            rate=rate if rate not in (None, "") else None,
            schedule=schedule,
        )
        row = prepare_quick_sale_row_for_display(row)
        from core.margin_utils import line_margin_percent, line_net_margin

        row["margin"] = round(line_net_margin(row), 2)
        row["margin_pct"] = round(line_margin_percent(row), 2)
        return {"ok": True, "line": row}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def autosave_sale(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Autosave a REAL bill and keep updating that same one.

    The first tick creates a saved bill; every later tick updates it, so no
    second bill and no second bill number. A counter sale goes into the day's
    single counter bill instead. Respects the settings enable flag.
    """
    from core.autosave_bill import write_autosave_bill
    from core.autosave_prefs import load_autosave_enabled
    from core.customer_service import COUNTER_SALE, get_or_create_customer

    # Taken before anything can wait: a Clear of this form after this point stops this tick.
    started = _next_form_seq()
    token = str(body.get("autosave_token") or "").strip()

    if not load_autosave_enabled() and not body.get("force"):
        return {"ok": True, "skipped": True, "reason": "autosave_disabled"}

    medicines = _normalize_medicines_for_save(body.get("items") or [])
    # Skip drafts that only have unresolved quick-add lines (need id)
    medicines = [m for m in medicines if m.get("id")]
    if not medicines:
        return {"ok": True, "skipped": True, "reason": "nothing_to_save"}

    # Don't autosave while editing a real bill
    if _safe_int(body.get("editing_sale_id")) > 0 and not _safe_int(
        body.get("autosave_sale_id")
    ):
        return {"ok": True, "skipped": True, "reason": "editing_real_sale"}

    customer_name = str(body.get("customer_name") or "").strip() or COUNTER_SALE
    phone = str(body.get("customer_phone") or "").strip()
    address = str(body.get("customer_address") or "").strip()
    doctor_name = str(body.get("doctor_name") or "").strip()
    doctor_phone = str(body.get("doctor_phone") or "").strip()
    disc_pct = _safe_float(body.get("discount_pct"))
    disc_rs = _safe_float(body.get("discount_rs"))
    rounding = _sale_rounding_amount(
        medicines, disc_pct, disc_rs, body, default_auto=True
    )
    cash = _safe_float(body.get("cash_paid"))
    online = _safe_float(body.get("online_paid"))
    previous_due = _safe_float(body.get("previous_due"))
    bill_date = _parse_bill_date(body.get("bill_date"))

    # Asked before the sale write lock is taken, as save_sale does. For a past date the answer
    # can wait on the store (15 s, and again a minute later when the store failed); asked under
    # the lock, F7 on every other tab -- today's bills too -- waited behind this tick.
    try:
        from core.sale_availability import lines_unavailable_on

        date_problems = lines_unavailable_on(conn, medicines, bill_date)
    except Exception as exc:
        # A check that fails outright hides nothing, as at save.
        print(f"[autosave] bill date check failed: {exc}")
        date_problems = []

    try:
        with _SALE_WRITE_LOCK:
            if _form_cleared_since(token, started):
                # The form was cleared (Clear, or its tab closed) while this tick waited on the
                # date check. Its bill and its record went on purpose: write nothing, and hand
                # back no bill or token for the blank form to take up.
                return {"ok": True, "skipped": True, "reason": "form_cleared"}
            customer_id = get_or_create_customer(conn, customer_name, phone, address)
            res = write_autosave_bill(
                conn,
                token=token,
                sale_id=_safe_int(body.get("autosave_sale_id")),
                customer_id=customer_id,
                customer_name=customer_name,
                customer_phone=phone,
                customer_address=address,
                medicines=medicines,
                discount_pct=disc_pct,
                discount_rs=disc_rs,
                rounding=rounding,
                cash_paid=cash,
                online_paid=online,
                doctor_name=doctor_name,
                doctor_phone=doctor_phone,
                previous_due=previous_due,
                bill_date=bill_date,
                bill_date_problems=date_problems,
            )
        if res.get("held"):
            # Nothing was written: a line on this form cannot be sold on its Bill Date. The
            # form itself is kept, and F7 names the lines as before.
            return {
                "ok": True,
                "skipped": True,
                "reason": "not_available_on_date",
                "messages": list(res.get("problems") or []),
                "autosave_token": res.get("token") or "",
            }
        return {
            "ok": True,
            "autosave_sale_id": int(res.get("sale_id") or 0),
            "autosave_token": res.get("token") or "",
            "bill_no": res.get("bill_no") or "",
            "counter": bool(res.get("counter")),
            "updated": not res.get("created"),
            "real_bill": True,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        from core.autosave_bill import AutosaveBillDiscarded

        if isinstance(exc, AutosaveBillDiscarded):
            # Not a failure to retry: the tab says why nothing is saved from it any more.
            return {"ok": False, "code": "bill_discarded", "error": str(exc)}
        return {"ok": False, "error": f"Autosave failed: {exc}"}


def list_autosave_sessions(conn, body: dict[str, Any]) -> dict[str, Any]:
    """In-progress bills this device owns, for a form that just reopened.

    The engine answers, not the browser. Autosave writes a REAL bill on the
    first tick, so "which bill does this tab own" is money, and money cannot
    live only in a React state variable that a crash takes with it -- nor only
    in localStorage, which a cleared profile or a different UI (the Tk page
    shares this engine) cannot read. The durable record is already on disk;
    this hands it out.
    """
    from core.autosave_bill import list_recoverable

    try:
        return {"ok": True, "sessions": list_recoverable()}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "sessions": []}


def resume_autosave(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Give a reopened tab its in-progress bill back, token and all."""
    from core.autosave_bill import resume_autosave_bill

    token = str(body.get("autosave_token") or body.get("token") or "")
    sale_id = _safe_int(body.get("autosave_sale_id") or body.get("sale_id"))
    if not token and sale_id <= 0:
        return {"ok": False, "code": "gone", "error": "No sale to resume."}
    try:
        with _SALE_WRITE_LOCK:
            res = resume_autosave_bill(conn, token=token, sale_id=sale_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not res.get("ok"):
        return res
    form = res.get("form") or {}
    return {
        "ok": True,
        "autosave_token": res.get("token") or "",
        "autosave_sale_id": int(res.get("sale_id") or 0),
        "bill_no": res.get("bill_no") or "",
        "counter": bool(res.get("counter")),
        "form": {
            "customer_name": form.get("customer_name") or "",
            "customer_phone": form.get("customer_phone") or "",
            "customer_address": form.get("customer_address") or "",
            "doctor_name": form.get("doctor_name") or "",
            "doctor_phone": form.get("doctor_phone") or "",
            "bill_date": form.get("bill_date") or res.get("bill_date") or "",
            "payment_mode": "Cash",
            "cash_paid": _safe_float(form.get("cash_paid")),
            "online_paid": _safe_float(form.get("online_paid")),
            "discount_pct": _safe_float(form.get("discount_pct")),
            "discount_rs": _safe_float(form.get("discount_rs")),
            "rounding": _safe_float(form.get("rounding")),
            "previous_due": _safe_float(form.get("previous_due")),
            "previous_credit": 0.0,
            "items": _normalize_medicines_for_save(form.get("medicines") or []),
        },
    }


def discard_autosave(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Abandon the in-progress autosaved bill.

    A counter sale only gives back the lines THIS form put in the day bill --
    the rest of the day's counter sales stay. A normal sale's bill is removed
    through the same path History's delete uses, so stock and the customer's
    balance come back.
    """
    from core.autosave_bill import discard_autosave_bill

    sale_id = _safe_int(body.get("autosave_sale_id") or body.get("sale_id"))
    token = str(body.get("autosave_token") or "")
    if sale_id <= 0 and not token:
        return {"ok": True, "deleted": False}
    try:
        with _SALE_WRITE_LOCK:
            # Under the lock: a tick that already holds it finishes first and this Clear removes
            # what it wrote; a tick still waiting on its date check writes nothing afterwards.
            _note_form_cleared(token)
            return discard_autosave_bill(conn, token=token, sale_id=sale_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def print_sale(conn, body: dict[str, Any], *, db_path: str | None = None) -> dict[str, Any]:
    """
    Print using the same Python path as Tk BillingPage:
      F7/F8 → print_bill_with_slot  (settings: silent / dialog / dot-matrix)
      F9    → print_bill_silent_with_slot
    UPI QR and slot paper/copies/printers come from bill_print + printer settings.
    """
    from core.bill_output import (
        _is_dot_matrix_printer_mode,
        print_bill_silent_with_slot,
        print_bill_with_slot,
    )
    from core.store_manager import get_active_db_path

    slot = _safe_int(body.get("slot") or 1)
    if slot not in (1, 2):
        slot = 1
    # mode: "slot" = classic F7/F8; "silent" = classic F9 reprint
    mode = str(body.get("mode") or "").strip().lower()
    if not mode:
        mode = "silent" if body.get("silent") else "slot"
    sale_id = _safe_int(body.get("sale_id"))
    if sale_id <= 0:
        # The Sales page carries the id of a bill open for Edit under its own
        # name. Without this, printing an unchanged edited bill had nothing to
        # print by and answered "No sale to print."
        sale_id = _safe_int(body.get("editing_sale_id"))
    bill_no = str(body.get("bill_no") or "")

    # Optional: save first then print (F7/F8) — same as Tk _print_sales_slot
    warnings: list = []
    if body.get("save_first") or (sale_id <= 0 and body.get("items")):
        saved = save_sale(conn, {**body, "sync_save": True})
        if saved.get("need_confirm"):
            return saved
        if not saved.get("ok"):
            return saved
        sale_id = int(saved["sale_id"])
        bill_no = str(saved.get("bill_no") or bill_no)
        warnings = list(saved.get("warnings") or [])

    if sale_id <= 0:
        return {"ok": False, "error": "No sale to print.", "code": "no_sale"}

    path = db_path
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            path = None
        elif not path:
            path = get_active_db_path()
    except Exception:
        path = db_path or get_active_db_path()
    try:
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass
        try:
            conn.commit()
        except Exception:
            pass
        row = conn.execute(
            "SELECT id FROM sales WHERE id=?", (int(sale_id),)
        ).fetchone()
        if not row:
            try:
                from core.sync_prefs import is_online_mode
                from core.server_crud import get_doc

                if not (is_online_mode() and get_doc("sales", int(sale_id))):
                    return {
                        "ok": False,
                        "error": f"Sale id {sale_id} not found after save.",
                        "code": "not_found",
                    }
            except Exception:
                return {
                    "ok": False,
                    "error": f"Sale id {sale_id} not found after save.",
                    "code": "not_found",
                }

        used_dot_matrix = bool(_is_dot_matrix_printer_mode())
        if mode == "silent":
            # Classic F9 — still routes to dot-matrix when Settings say so
            html_path, pdf_path = print_bill_silent_with_slot(
                conn, sale_id, slot, db_path=path
            )
        else:
            # Classic F7/F8 — PrinterManager + bill_print_settings decide everything
            html_path, pdf_path = print_bill_with_slot(
                conn, sale_id, slot, hwnd_owner=0, db_path=path
            )

        return {
            "ok": True,
            "sale_id": sale_id,
            "bill_no": bill_no,
            "slot": slot,
            "mode": mode,
            "dot_matrix": used_dot_matrix,
            "html_path": html_path,
            "pdf_path": pdf_path,
            # A bill long enough to split writes Bill_X_p1.pdf .. _pN.pdf and
            # pdf_path is the LAST sheet, so naming it alone told the counter
            # the first pages had not been saved. Send the folder as well and
            # let the screen show that instead when there is more than one.
            "pdf_dir": os.path.dirname(pdf_path) if pdf_path else "",
            "next_bill_hint": _next_hint(conn),
            "cleared": bool(body.get("save_first")),
            # What the save pointed out (F7/F8 save first); the bill is saved and printed.
            "warnings": warnings,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Bill {bill_no or sale_id} print failed: {exc}",
            "sale_id": sale_id,
            "bill_no": bill_no,
            "code": "print_failed",
        }


def _forget_autosave_session_for(sale_id: int) -> None:
    """A deleted bill can no longer be resumed -- stop offering it.

    Without this, deleting an unfinished bill from History leaves its session
    record behind, and every restart asks the operator about a bill that is not
    there any more. The reverse mistake (dropping the record while the bill
    lives) is the one that orphans money, so this only ever runs on a delete
    that actually happened.
    """
    try:
        from core.autosave_session import drop_session, list_open_sessions

        for rec in list_open_sessions():
            if int(rec.get("sale_id") or 0) == int(sale_id):
                drop_session(rec.get("token"))
    except Exception as exc:
        print(f"[autosave] session cleanup after delete: {exc}")


def delete_saved_sale(conn, body: dict[str, Any]) -> dict[str, Any]:
    """Permanently delete a saved sales bill and restore stock."""
    sale_id = _safe_int(body.get("sale_id"))
    if sale_id == 0:
        return {"ok": False, "error": "Invalid sale id."}
    _forget_autosave_session_for(sale_id)

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.online_mutation_queue import enqueue, cancel_matching

            if sale_id < 0:
                from core.online_mutation_queue import bill_row_sale_id

                server_id = bill_row_sale_id(sale_id)
                if server_id <= 0:
                    cancel_matching(collection="sales", local_id=sale_id)
                    return {"ok": True, "sale_id": sale_id, "queued": True}
                # This bill's balance and stock are on the server; only its row waits in
                # the queue. Cancelling that row left both there with no bill. The row
                # lands first, then a real delete takes the balance and stock back.
                sale_id = server_id
            enqueue(
                collection="sales",
                op="delete",
                payload={"sale_id": sale_id, "id": sale_id},
                local_id=sale_id,
            )
            return {"ok": True, "sale_id": sale_id, "queued": True}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to delete bill: {exc}"}

    try:
        from core.online_guard import ensure_can_mutate
        ensure_can_mutate()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "online_unavailable"}

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT customer_id FROM sales WHERE id=? AND COALESCE(deleted,0)=0",
            (sale_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {"ok": False, "error": "Bill not found or already deleted."}
        customer_id = row[0]

        cursor.execute(
            "SELECT medicine_id, qty FROM sales_items WHERE sale_id=?",
            (sale_id,),
        )
        sale_lines = cursor.fetchall()
        medicine_ids = [int(med_id) for med_id, _ in sale_lines if med_id]
        for med_id, qty in sale_lines:
            restore_qty = abs(float(qty or 0))
            cursor.execute(
                "UPDATE medicines SET stock_qty=stock_qty+?, is_hidden=0 WHERE id=?",
                (restore_qty, med_id),
            )

        cursor.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
        cursor.execute("DELETE FROM sales WHERE id=?", (sale_id,))
        conn.commit()

        if customer_id:
            from core.customer_service import recalculate_customer_due

            recalculate_customer_due(conn, customer_id)

        from core.sync_coordinator import after_sale_deleted

        after_sale_deleted(conn, sale_id, customer_id, medicine_ids)
        return {"ok": True, "sale_id": sale_id}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to delete bill: {exc}"}


def list_print_all_candidates(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.history_print import fetch_sales_for_print

    from_iso = str(body.get("from") or body.get("from_date") or "").strip()
    to_iso = str(body.get("to") or body.get("to_date") or "").strip()
    if not from_iso or not to_iso:
        return {"ok": False, "error": "From and To dates are required."}
    try:
        bills = fetch_sales_for_print(
            conn,
            from_iso,
            to_iso,
            str(body.get("schedule") or "").strip(),
            q=str(body.get("q") or "").strip(),
            customer=str(body.get("customer") or "").strip(),
        )
    except Exception as exc:
        # "0 bills" and "could not ask" are different answers, and the shop was
        # only ever shown the first one.
        return {"ok": False, "error": str(exc)}
    # Medicine, Batch and Due narrow by LINE, which this query does not read --
    # say so rather than quietly offering more bills than the list shows.
    unapplied = [
        label for key, label in (
            ("medicine", "Medicine"), ("batch", "Batch"), ("due", "Due status"),
        )
        if str(body.get(key) or "").strip()
    ]
    try:
        from core.bill_output import _is_dot_matrix_printer_mode

        dot_matrix = bool(_is_dot_matrix_printer_mode())
    except Exception:
        dot_matrix = False
    return {
        "ok": True,
        "bills": bills,
        "count": len(bills),
        "unapplied_filters": unapplied,
        # The dialog shows "one bill at a time" instead of the bills-per-sheet choice.
        "dot_matrix": dot_matrix,
    }


def print_all_sales(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.history_print import PRINT_ALL_SLOT, fetch_sales_for_print, print_bills_batch

    from_iso = str(body.get("from") or body.get("from_date") or "").strip()
    to_iso = str(body.get("to") or body.get("to_date") or "").strip()
    paper = str(body.get("paper") or "A6").strip().upper() or "A6"
    slot = _safe_int(body.get("slot") or PRINT_ALL_SLOT)
    if slot not in (1, 2):
        slot = PRINT_ALL_SLOT

    raw_ids = body.get("sale_ids")
    if isinstance(raw_ids, list) and raw_ids:
        sale_ids = [_safe_int(x) for x in raw_ids if _safe_int(x) > 0]
        dropped = len(raw_ids) - len(sale_ids)
        if dropped:
            # Silently printing fewer bills than the shop ticked is how a day's
            # bills go missing without anyone noticing.
            return {
                "ok": False,
                "error": (
                    f"{dropped} of the {len(raw_ids)} selected bill(s) have not "
                    "reached the server yet and cannot be printed. Sync first."
                ),
            }
    elif from_iso and to_iso:
        try:
            sale_ids = [
                b["sale_id"] for b in fetch_sales_for_print(conn, from_iso, to_iso)
            ]
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    else:
        return {"ok": False, "error": "Provide sale_ids or a date range."}

    if not sale_ids:
        return {"ok": False, "error": "No bills to print in the selected range."}

    dot_matrix = False
    db_path = None
    try:
        from core.bill_output import _is_dot_matrix_printer_mode
        from core.store_manager import get_active_db_path
        from core.sync_prefs import is_online_mode

        dot_matrix = bool(_is_dot_matrix_printer_mode())
        # The same store file a single bill's Print reads (print_sale).
        db_path = None if is_online_mode() else get_active_db_path()
    except Exception:
        pass

    pages, failures = print_bills_batch(
        conn, sale_ids, paper=paper, slot=slot, db_path=db_path
    )
    ok = not failures
    if dot_matrix:
        msg = (
            f"Printed {pages} of {len(sale_ids)} bill(s) on dot matrix, one by one "
            "(printer reset after each bill)."
        )
        if failures:
            msg += " Stopped on a problem."
    else:
        msg = f"Printed {pages} page(s) for {len(sale_ids)} bill(s)."
        if failures:
            msg += f" {len(failures)} failed."
    out = {
        "ok": ok,
        "pages": pages,
        "total": len(sale_ids),
        "failures": failures,
        "message": msg,
        "dot_matrix": dot_matrix,
    }
    if failures:
        # An ok:False with no "error" reaches the page as a bare "HTTP 400",
        # taking the count and every failure line with it -- so a half-printed
        # batch told the shop nothing about which bills to chase.
        out["error"] = msg
        out["code"] = "print_partial"
    return out


def _try_save_sale_pdf(conn, sale_id: int) -> str | None:
    try:
        from core.bill_output import save_bill_pdf_only
        from core.store_manager import get_active_db_path

        _, pdf_path = save_bill_pdf_only(
            conn, sale_id, db_path=get_active_db_path()
        )
        return pdf_path or None
    except Exception:
        return None


def save_bill_pdf(conn, sale_id: int) -> dict[str, Any]:
    sid = _safe_int(sale_id)
    if sid <= 0:
        return {"ok": False, "error": "sale_id required"}
    try:
        from core.bill_output import save_bill_pdf_a6

        _, pdf_path = save_bill_pdf_a6(conn, sid)
        if not pdf_path:
            return {
                "ok": False,
                "error": "PDF could not be created. Install Edge or Chrome.",
            }
        return {"ok": True, "pdf_path": pdf_path}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_bill_preview(conn, sale_id: int) -> dict[str, Any]:
    sid = _safe_int(sale_id)
    if sid <= 0:
        return {"ok": False, "error": "sale_id required"}
    try:
        from core.bill_config import (
            apply_a5_portrait_bill_layout,
            load_bill_print_settings,
            render_bill_html,
        )
        from core.bill_output import _load_sale_data

        # Preview what Print Sales 1 will actually do, not a fixed A5 portrait:
        # the slot decides paper, halves and copies, so the preview and the
        # paper disagreed for any shop that had changed a slot.
        from core.bill_config import (
            apply_print_bill_layout,
            get_print_slot_settings,
        )

        slot_settings = get_print_slot_settings(load_bill_print_settings(), 1)
        # bill_copies, NOT copies. get_print_slot_settings deliberately leaves
        # "copies" as the PAGE copies and puts the slot's bill copies in
        # "bill_copies" -- reading the wrong one made the preview show one
        # half-sheet while F7 printed two copies on the sheet.
        try:
            slot_copies = int(slot_settings.get("bill_copies") or 1)
        except (TypeError, ValueError):
            slot_copies = 1
        settings = apply_print_bill_layout(
            slot_settings, print_slot_copies=slot_copies
        )
        bill_no, ctx, _items = _load_sale_data(conn, sid, settings_override=settings)
        html = render_bill_html(ctx, settings)
        return {
            "ok": True,
            "bill_no": bill_no or ctx.bill_no,
            "html": html,
            "template": settings.get("template", "classic"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_bill_details(conn, sale_id: int) -> dict[str, Any]:
    sid = _safe_int(sale_id)
    if sid <= 0:
        return {"ok": False, "error": "sale_id required"}

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.billing_service import load_sale_medicines
            from core.fy_serial import display_sales_bill_no
            from core.online_catalog import find_customer_by_id
            from core.server_crud import get_doc

            doc = get_doc("sales", sid) or {}
            if not doc or doc.get("deleted"):
                return {"ok": False, "error": "Bill not found."}
            customer_id = int(doc.get("customer_id") or 0)
            cust = find_customer_by_id(customer_id) if customer_id else {}
            if not cust and customer_id:
                cust = get_doc("customers", customer_id) or {}
            medicines = load_sale_medicines(conn, sid) or []
            items = [
                {
                    "name": m.get("name") or "",
                    "batch": m.get("batch") or "",
                    "type": m.get("type") or m.get("display_type") or "",
                    "qty": float(m.get("qty") or 0),
                    "rate": float(m.get("rate") or 0),
                    "gst_percent": float(m.get("gst_percent") or 0),
                    "amount": float(m.get("amount") or 0),
                }
                for m in medicines
            ]
            bill_no = display_sales_bill_no(doc.get("bill_no") or "") or (
                doc.get("bill_no") or ""
            )
            return {
                "ok": True,
                "sale_id": sid,
                "bill_no": bill_no,
                "bill_date": doc.get("bill_date") or "",
                "customer": (cust or {}).get("name")
                or doc.get("customer_name")
                or "",
                "phone": (cust or {}).get("phone") or "",
                "doctor": doc.get("doctor_name") or "",
                "total_amount": float(doc.get("total_amount") or 0),
                "discount": float(doc.get("discount") or 0),
                "amount_paid": float(doc.get("amount_paid") or 0),
                "cash_paid": float(doc.get("cash_paid") or 0),
                "online_paid": float(doc.get("online_paid") or 0),
                "previous_due": float(doc.get("previous_due") or 0),
                "due_amount": float(doc.get("due_amount") or 0),
                "credit_amount": float(doc.get("credit_amount") or 0),
                "total_due": float(doc.get("total_due") or 0),
                "items": items,
            }
    except Exception as exc:
        print(f"[desktop sales] online bill details: {exc}")

    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.bill_no, s.bill_date, c.name, COALESCE(c.phone,''),
               COALESCE(s.doctor_name,''), s.total_amount, s.discount,
               s.amount_paid, COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
               COALESCE(s.previous_due,0), COALESCE(s.due_amount,0),
               COALESCE(s.credit_amount,0), COALESCE(s.total_due,0)
        FROM sales s
        JOIN customers c ON s.customer_id = c.id
        WHERE s.id=? AND COALESCE(s.deleted,0)=0
        """,
        (sid,),
    )
    row = cur.fetchone()
    if not row:
        return {"ok": False, "error": "Bill not found."}
    cur.execute(
        """
        SELECT COALESCE(m.name, ''), COALESCE(m.batch_no,''), COALESCE(m.type,''),
               si.qty, si.rate, COALESCE(si.gst_percent,0), si.amount
        FROM sales_items si
        LEFT JOIN medicines m ON si.medicine_id = m.id
        WHERE si.sale_id=?
        """,
        (sid,),
    )
    items = [
        {
            "name": r[0],
            "batch": r[1],
            "type": r[2],
            "qty": float(r[3] or 0),
            "rate": float(r[4] or 0),
            "gst_percent": float(r[5] or 0),
            "amount": float(r[6] or 0),
        }
        for r in cur.fetchall()
    ]
    return {
        "ok": True,
        "sale_id": sid,
        "bill_no": row[0],
        "bill_date": row[1],
        "customer": row[2],
        "phone": row[3],
        "doctor": row[4],
        "total_amount": float(row[5] or 0),
        "discount": float(row[6] or 0),
        "amount_paid": float(row[7] or 0),
        "cash_paid": float(row[8] or 0),
        "online_paid": float(row[9] or 0),
        "previous_due": float(row[10] or 0),
        "due_amount": float(row[11] or 0),
        "credit_amount": float(row[12] or 0),
        "total_due": float(row[13] or 0),
        "items": items,
    }