"""
core/billing_service.py
────────────────────────
All database write operations for the billing flow.
Used by ui/billing.py (generate_bill) and widgets/bill_edit.py (save_bill).
No UI code. No calculation code.
"""
from __future__ import annotations

import uuid
from datetime import datetime, date
from typing import Any, Optional


def _as_float(
    v: Any,
    default: float = 0.0,
    *,
    field: str = "",
    required: bool = False,
) -> float:
    """Parse money/qty fields safely. Empty → default; garbage → clear error if required."""
    if v is None:
        if required:
            raise ValueError(f"{field or 'Value'} is required.")
        return default
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            fv = default
        if fv != fv:  # NaN
            if required:
                raise ValueError(f"Invalid number for {field or 'value'}.")
            return default
        return fv
    s = str(v).strip()
    if not s:
        if required:
            raise ValueError(f"{field or 'Value'} is required.")
        return default
    # Allow "1,234.50" from catalog/UI without crashing.
    s = s.replace(",", "").replace("₹", "").replace("Rs.", "").replace("rs.", "")
    s = s.strip()
    if not s:
        if required:
            raise ValueError(f"{field or 'Value'} is required.")
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        if required:
            raise ValueError(f"Invalid number for {field or 'value'}: {v!r}")
        return default


def _allocate_bill_number(
    conn,
    prefix: str,
    *,
    bill_date: date | str | None = None,
    exclude_sale_id: int | None = None,
) -> str:
    """Next bill number — SCB restarts each FY (1 Apr); ASV drafts stay global."""
    from core.fy_serial import allocate_bill_number

    local_no = allocate_bill_number(
        conn,
        prefix,
        bill_date=bill_date,
        exclude_sale_id=exclude_sale_id,
    )
    if prefix != "SCB":
        return local_no

    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return local_no
        from core import server_api as api
        from core.fy_serial import encode_sales_bill_no, fy_start_year_for_date

        token = api.store_token_for_active()
        if not token:
            raise RuntimeError("Online mode requires Satpuda Core Server sign-in for bill numbers")
        raw = bill_date or date.today()
        date_s = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)[:10]
        data = api.allocate_fy(token, "sales", date_s) or {}
        bill_no = (data.get("bill_no") or "").strip()
        if bill_no:
            return bill_no
        server_serial = int(data.get("fy_serial") or 0)
        if server_serial > 0:
            fy = int(data.get("fy_start_year") or fy_start_year_for_date(bill_date))
            return encode_sales_bill_no(server_serial, fy)
        raise RuntimeError("Server did not return next sales bill number")
    except Exception as e:
        # Online must never fall back to empty local counter (would restart at SCB1).
        from core.sync_prefs import is_online_mode as _online

        if _online():
            raise
        print(f"[BILL] server FY allocate failed, using local: {e}")
        return local_no
    return local_no


def _enqueue_sale_upsert(
    *,
    customer_id,
    medicines,
    discount_pct,
    rounding,
    cash_paid,
    online_paid,
    doctor_name,
    doctor_phone,
    previous_due,
    discount_rs=None,
    bill_date=None,
    customer_name="",
    customer_phone="",
    sale_id=None,
    client_uuid=None,
    server_sale_id=0,
):
    """Queue an Online sale create/edit and return (bill_no, sale_id) immediately.

    ``server_sale_id`` is the id a failed direct send already gave this new sale; the replay
    asks the server for that sale before it takes a number.
    """
    from datetime import date as _date

    from core.calc_engine import calc_bill_summary, calc_payment_result
    from core.online_catalog import find_customer_by_id
    from core.online_mutation_queue import bill_row_sale_id, enqueue, pending_by_local_id

    summary = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    cust = find_customer_by_id(customer_id) or {}
    prev_due = round(
        _as_float(
            cust.get("total_due"),
            _as_float(previous_due, 0.0),
            field="previous due",
        ),
        2,
    )
    prev_credit = round(
        _as_float(cust.get("total_credit"), 0.0, field="previous credit"),
        2,
    )
    pay = calc_payment_result(
        summary["total_amount"],
        _as_float(cash_paid, 0.0, field="cash"),
        _as_float(online_paid, 0.0, field="online"),
        prev_due,
        prev_credit,
    )
    bill_date_val = bill_date or _date.today()
    date_s = (
        bill_date_val.isoformat()
        if hasattr(bill_date_val, "isoformat")
        else str(bill_date_val)[:10]
    )
    lid = int(sale_id or 0)
    cu = (client_uuid or "").strip()
    cid = int(customer_id or 0)
    if lid < 0:
        server_id = bill_row_sale_id(lid)
        if server_id > 0:
            # This bill's balance and stock are already on the server and only its row
            # waits in the queue. A change to it is an EDIT of that sale, sent once the
            # row lands -- merged into the queued row, the whole sale went again.
            # Every edit of it shares one queue key, so the next edit replaces the one
            # before. A fresh key each time left one queued edit per autosave tick, and
            # Sales History drew each of them as a bill of its own.
            lid, cu = server_id, f"sale-edit:{server_id}"
    # A new sale says when it was made, and a replay keeps the id a failed send gave it. Both
    # ride along in the queued create and survive later edits of it.
    made = ""
    known_id = int(server_sale_id or 0)
    if lid <= 0:
        from core.server_crud import _now

        made = _now()
    if lid < 0:
        pending = pending_by_local_id("sales", lid)
        if pending:
            earlier = pending.get("payload") or {}
            made = str(earlier.get("created_at") or made)
            known_id = known_id or int(earlier.get("_sale_id") or 0)
            if not cu:
                cu = str(pending.get("client_uuid") or "")
            if cid <= 0:
                try:
                    cid = int((pending.get("payload") or {}).get("customer_id") or 0)
                except (TypeError, ValueError):
                    cid = 0
    payload = {
        "customer_id": cid,
        "customer_name": customer_name or cust.get("name") or "",
        "customer_phone": customer_phone or cust.get("phone") or "",
        "medicines": list(medicines or []),
        "discount_pct": discount_pct,
        "rounding": rounding,
        "cash_paid": cash_paid,
        "online_paid": online_paid,
        "doctor_name": doctor_name or "",
        "doctor_phone": doctor_phone or "",
        "previous_due": prev_due,
        "discount_rs": discount_rs,
        "bill_date": date_s,
        "bill_no": "PENDING",
        "total_amount": summary["total_amount"],
        "discount": summary["discount_amount"],
        "amount_paid": pay["amount_paid"],
        "due_amount": pay["due_amount"],
        "credit_amount": pay["credit_amount"],
        "total_due": pay["total_due"],
        "bill_cleared": 1 if pay["due_amount"] == 0 else 0,
        "account_cleared": 0,
    }
    if made:
        payload["created_at"] = made
    if known_id > 0:
        payload["_sale_id"] = known_id
    row = enqueue(
        collection="sales",
        op="upsert",
        payload=payload,
        client_uuid=cu or None,
        local_id=lid if lid else None,
    )
    return "PENDING", int(row.get("local_id") or 0)


def save_new_bill(conn, customer_id, medicines, discount_pct,
                  rounding, cash_paid, online_paid,
                  doctor_name, doctor_phone, previous_due,
                  discount_rs=None, bill_date=None,
                  customer_name="", customer_phone="", sync=False):
    """
    Insert a new sale + items, update stock, update customer balance.
    Returns (bill_no, sale_id).
    Online: enqueue mutation and return immediately (background push).
    """
    from core.calc_engine import calc_bill_summary, calc_payment_result
    from core.online_guard import ensure_can_mutate, commit_after_cloud_push
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        # sync=True means the caller needs a REAL bill number and id right now
        # -- printing. The queue cannot serve that: it returns ("PENDING", a
        # negative temp id), the print lookup fails, and the counter presses F7
        # again and bills the customer twice. Write straight to the server
        # instead, and only fall back to the queue if the server is unreachable.
        # ONE identity for this bill across both routes below.
        #
        # The direct save can fail AFTER the server has partly applied the bundle
        # (the customer balance and the stock rows commit even when the sale row
        # is refused). Without a shared client_uuid the queued retry looked like a
        # brand-new bill, so the customer was charged twice. uq_sales_client_uuid
        # makes the retry resolve to the SAME row instead.
        cu = str(uuid.uuid4())
        sent_as = 0  # the id a direct send gave this sale before it failed
        if sync:
            try:
                from core.quick_sale_medicine import resolve_quick_sale_medicines
                from core.server_crud import save_new_sale_online

                # Quick-add ("Add No Stock") lines carry no medicine id yet.
                # save_new_sale_online rejects id-less lines, but the bill total is
                # computed from every line -- so the saved bill's items did not add
                # up to what the customer was charged. Inside the try on purpose:
                # if this fails the sale still falls through to the queue, which
                # resolves quick-add lines itself.
                resolve_quick_sale_medicines(None, medicines)
                return save_new_sale_online(
                    customer_id=customer_id,
                    medicines=medicines,
                    discount_pct=discount_pct,
                    rounding=rounding,
                    cash_paid=cash_paid,
                    online_paid=online_paid,
                    doctor_name=doctor_name or "",
                    doctor_phone=doctor_phone or "",
                    previous_due=previous_due,
                    discount_rs=discount_rs,
                    bill_date=bill_date,
                    client_uuid=cu,
                )
            except Exception as exc:
                from core.server_crud import BundleDocumentRejected, SaleRowNotSaved

                if isinstance(exc, SaleRowNotSaved):
                    # The customer's balance and the stock are on the server; only the
                    # bill row is not. Queue that row ALONE. The whole-sale fallback below
                    # posted the balance and the stock a second time when it replayed.
                    from core.online_mutation_queue import keep_bill_row

                    row = keep_bill_row(exc)
                    if exc.refused:
                        # Kept in Sales History as refused; the counter hears why now. The
                        # parked row's id goes up with the refusal, so autosave can pin its
                        # form to this bill instead of saving the sale again on the next tick.
                        exc.local_id = int(row.get("local_id") or 0)
                        print(f"[sale] server refused the bill row: {exc}", flush=True)
                        raise
                    print(f"[sale] bill row not saved yet, queued on its own: {exc}", flush=True)
                    return "PENDING", int(row.get("local_id") or 0)
                if isinstance(exc, BundleDocumentRejected):
                    # Part of this write is already on the server. Queueing the
                    # whole sale again would add the bill to the customer's due a
                    # second time, so stop here and let the counter see the real
                    # reason instead of a bill that quietly charges twice.
                    print(f"[sale] server rejected part of the bill: {exc}", flush=True)
                    raise
                # Server unreachable: nothing was written, so queue it -- the sale
                # is never lost and the caller shows "saved, print from History".
                # When the send itself went out with no answer, the server may hold the sale
                # already: the queued copy keeps its id so the replay can ask for it.
                try:
                    sent_as = int((getattr(exc, "sale", None) or {}).get("id") or 0)
                except (TypeError, ValueError, AttributeError):
                    sent_as = 0
                print(f"[sale] direct online save failed, queueing: {exc}", flush=True)
        return _enqueue_sale_upsert(
            customer_id=customer_id,
            medicines=medicines,
            discount_pct=discount_pct,
            rounding=rounding,
            cash_paid=cash_paid,
            online_paid=online_paid,
            doctor_name=doctor_name or "",
            doctor_phone=doctor_phone or "",
            previous_due=previous_due,
            discount_rs=discount_rs,
            bill_date=bill_date,
            customer_name=customer_name or "",
            customer_phone=customer_phone or "",
            client_uuid=cu,
            server_sale_id=sent_as,
        )

    ensure_can_mutate()

    cur = conn.cursor()

    bill_date_val = bill_date or date.today()
    bill_no = _allocate_bill_number(conn, "SCB", bill_date=bill_date_val)

    summary  = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    total    = summary['total_amount']
    disc_amt = summary['discount_amount']
    disc_pct = summary['discount_pct']

    # Read live customer credit too
    cur.execute(
        "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) FROM customers WHERE id=?",
        (customer_id,))
    cust_row     = cur.fetchone()
    prev_due     = (
        round(_as_float(cust_row[0], 0.0), 2)
        if cust_row
        else round(max(0.0, _as_float(previous_due, 0.0)), 2)
    )
    prev_credit  = round(_as_float(cust_row[1], 0.0), 2) if cust_row else 0.0

    pay = calc_payment_result(
        total,
        _as_float(cash_paid, 0.0, field="cash"),
        _as_float(online_paid, 0.0, field="online"),
        prev_due,
        prev_credit,
    )

    amount_paid   = pay['amount_paid']
    due_amount    = pay['due_amount']
    credit_amount = pay['credit_amount']
    total_due     = pay['total_due']
    bill_cleared  = 1 if due_amount == 0 else 0

    # Upsert doctor
    doc_upper = doctor_name.strip().upper() if doctor_name else ''
    if doc_upper:
        cur.execute("SELECT id FROM doctors WHERE UPPER(name)=?", (doc_upper,))
        existing = cur.fetchone()
        if not existing:
            cur.execute("INSERT INTO doctors (name, phone) VALUES (?,?)",
                        (doc_upper, doctor_phone))
        elif doctor_phone:
            cur.execute("UPDATE doctors SET phone=? WHERE UPPER(name)=?",
                        (doctor_phone, doc_upper))

    cur.execute("""
        INSERT INTO sales
            (bill_no, customer_id, bill_date, total_amount, discount, discount_pct, rounding,
             amount_paid, cash_paid, online_paid, doctor_name,
             previous_due, previous_credit, due_amount, credit_amount, total_due,
             bill_cleared, account_cleared)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
    """, (bill_no, customer_id, bill_date_val, total, disc_amt, disc_pct, rounding,
          amount_paid, cash_paid, online_paid, doc_upper,
          prev_due, prev_credit, due_amount, credit_amount, total_due, bill_cleared))
    sale_id = cur.lastrowid
    from core.fy_serial import display_sales_bill_no, patch_sale_fy_fields

    patch_sale_fy_fields(cur, sale_id, bill_no, bill_date_val)

    from core.quick_sale_medicine import resolve_quick_sale_medicines
    resolve_quick_sale_medicines(conn, medicines)
    _insert_items_and_update_stock(cur, sale_id, medicines)

    from core.customer_service import recalculate_customer_due
    med_ids = [m['id'] for m in medicines if m.get('id')]

    if is_online_mode():
        recalculate_customer_due(conn, customer_id, commit=False, sync=False)

        def _push():
            from core.sync_coordinator import push_sale_now
            return push_sale_now(conn, sale_id, med_ids, commit_meta=False)

        def _push_conn(c):
            from core.sync_coordinator import push_sale_now
            return push_sale_now(c, sale_id, med_ids, commit_meta=False)

        commit_after_cloud_push(conn, _push, push_with_conn=_push_conn)
        try:
            from core.sync_coordinator import after_sale_saved
            after_sale_saved(conn, sale_id, med_ids, already_pushed=True)
        except Exception:
            pass
    else:
        from core.sync_coordinator import stamp_sale_meta
        stamp_sale_meta(conn, sale_id, med_ids, commit=False)
        conn.commit()
        recalculate_customer_due(conn, customer_id)
        try:
            from core.sync_coordinator import after_sale_saved
            after_sale_saved(conn, sale_id, med_ids)
        except Exception:
            pass

    # Debug
    print(f"[BILL] {display_sales_bill_no(bill_no)} total={total:.2f} paid={amount_paid:.2f} "
          f"prev_due={prev_due:.2f} prev_credit={prev_credit:.2f} "
          f"-> due={due_amount:.2f} credit={credit_amount:.2f}")

    return display_sales_bill_no(bill_no), sale_id


def find_todays_counter_sale_id(conn, customer_id, bill_date=None):
    """Return today's counter-sale bill id for merging walk-in lines, or None."""
    from core.customer_service import counter_sale_customer_ids, is_counter_sale_name

    bdate = bill_date or date.today()
    if hasattr(bdate, 'strftime'):
        bdate = bdate.strftime('%Y-%m-%d')
    else:
        bdate = str(bdate).strip().split(' ')[0]

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            name = None
            try:
                from core.online_catalog import find_customer_by_id

                cust = find_customer_by_id(customer_id)
                if cust:
                    name = cust.get("name")
            except Exception:
                pass
            if not name:
                cur = conn.cursor()
                cur.execute("SELECT name FROM customers WHERE id=?", (customer_id,))
                row = cur.fetchone()
                name = row[0] if row else None
            if not name or not is_counter_sale_name(name):
                return None
            try:
                from core import store_query_client as sq

                raw = (
                    sq.list_sales(
                        from_date=bdate, to_date=bdate, q="COUNTER", limit=200
                    )
                    or {}
                ).get("rows") or []
                for r in raw:
                    if int(r.get("is_autosave") or 0) or int(r.get("deleted") or 0):
                        continue
                    cn = str(r.get("customer_name") or "")
                    if not is_counter_sale_name(cn):
                        continue
                    bd = str(r.get("bill_date") or "").strip().split(" ")[0]
                    if bd and bd != bdate:
                        continue
                    sid = int(r.get("id") or r.get("local_id") or 0)
                    if sid > 0:
                        return sid
            except Exception as exc:
                print(f"[billing] online find_todays_counter_sale_id: {exc}")
            return None
    except Exception:
        pass

    cur = conn.cursor()
    cur.execute("SELECT UPPER(name) FROM customers WHERE id=?", (customer_id,))
    row = cur.fetchone()
    if not row or not is_counter_sale_name(row[0]):
        return None
    cust_ids = counter_sale_customer_ids(conn) or [customer_id]
    if customer_id not in cust_ids:
        cust_ids.append(customer_id)
    placeholders = ','.join('?' * len(cust_ids))
    cur.execute(
        f"SELECT id FROM sales WHERE customer_id IN ({placeholders}) "
        f"AND bill_date=? AND COALESCE(is_autosave,0)=0 "
        f"AND COALESCE(deleted,0)=0 ORDER BY id DESC LIMIT 1",
        (*cust_ids, bdate),
    )
    found = cur.fetchone()
    return found[0] if found else None


def load_sale_medicines(conn, sale_id, *, doc=None):
    """Load sales_items as billing medicine dicts (for counter-sale merge).

    `doc` lets a caller that has already fetched the sale document pass it in;
    the counter-sale merge used to fetch the same document three times over.
    Scope is deliberately this one call -- sale docs are mutated by the queue
    flusher and by other devices, so nothing here may be cached process-wide.
    """
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.server_crud import _line_gst_percent, get_doc
            from core.online_catalog import medicine_by_id

            doc = doc if doc is not None else (get_doc("sales", int(sale_id)) or {})
            items = []
            for it in doc.get("items") or []:
                if not isinstance(it, dict):
                    continue
                mid = int(it.get("medicine_id") or it.get("id") or 0)
                mp = medicine_by_id(mid) if mid else {}
                mp = mp or {}
                items.append(
                    {
                        "id": mid,
                        "qty": _as_float(it.get("qty"), 0.0, field="qty"),
                        "rate": _as_float(it.get("rate"), 0.0, field="rate"),
                        "amount": _as_float(it.get("amount"), 0.0, field="amount"),
                        "medicine_discount": _as_float(
                            it.get("item_discount") or it.get("medicine_discount"),
                            0.0,
                            field="discount",
                        ),
                        "name": it.get("medicine_name")
                        or it.get("name")
                        or mp.get("name")
                        or "",
                        "batch": it.get("batch_no")
                        or it.get("batch")
                        or mp.get("batch_no")
                        or "",
                        "expiry": it.get("expiry_date")
                        or it.get("expiry")
                        or mp.get("expiry_date")
                        or "",
                        "type": it.get("type") or mp.get("type") or "",
                        "display_type": it.get("type") or mp.get("type") or "N/A",
                        "schedule": it.get("schedule") or mp.get("schedule") or "",
                        # The rate the line was SOLD at, even 0%: `or` sent a stored
                        # 0 to the medicine's rate today, and the edit saves it back.
                        # None when neither the line nor the medicine has one, so the
                        # edit pushes NULL again instead of an exempt 0% nobody sold
                        # it at. A NULL line whose medicine HAS a rate loads that rate,
                        # and saving the edit stores it on the line: an old Online bill
                        # that never kept its rate takes the medicine's rate on the day
                        # it is edited (nothing can recover the one it was sold at).
                        "gst_percent": _line_gst_percent(it, mp),
                        "unit": it.get("unit") or mp.get("unit") or "1",
                        "mrp": _as_float(
                            it.get("mrp") or mp.get("mrp"), 0.0, field="mrp"
                        ),
                    }
                )
            return items
    except Exception as exc:
        print(f"[billing] online load_sale_medicines: {exc}")

    cur = conn.cursor()
    # GST % as SOLD (sales_items.gst_percent), not the medicine's rate today: an
    # edit saves these lines back, so reading today's rate rewrote an old bill's
    # GST. Only a line that never stored a rate (NULL) falls back to the medicine,
    # and one whose medicine has no rate either stays NULL: COALESCE(..., 0) made
    # the edit store an exempt 0% nobody sold it at.
    cur.execute("""
        SELECT si.medicine_id, si.qty, si.rate, si.amount,
               COALESCE(si.item_discount, 0),
               m.name, m.batch_no, m.expiry_date, m.type, m.schedule,
               COALESCE(si.gst_percent, m.gst_percent),
               COALESCE(m.unit, '1'), COALESCE(m.mrp, 0)
        FROM sales_items si JOIN medicines m ON si.medicine_id=m.id
        WHERE si.sale_id=?
    """, (sale_id,))
    items = []
    for row in cur.fetchall():
        items.append({
            'id': row[0],
            'qty': row[1],
            'rate': row[2],
            'amount': row[3],
            'medicine_discount': row[4],
            'name': row[5],
            'batch': row[6],
            'expiry': row[7],
            'type': row[8] or '',
            'display_type': row[8] or 'N/A',
            'schedule': row[9] or '',
            'gst_percent': row[10] if row[10] not in (None, '') else None,
            'unit': row[11],
            'mrp': row[12],
        })
    return items


def _merge_medicine_lines(existing, new_items):
    merged = {m['id']: dict(m) for m in existing}
    for med in new_items:
        mid = med['id']
        if mid in merged:
            old = merged[mid]
            old['qty'] = _as_float(old.get('qty'), 0.0) + _as_float(med.get('qty'), 0.0)
            old['medicine_discount'] = _as_float(old.get('medicine_discount'), 0.0) + _as_float(
                med.get('medicine_discount'), 0.0,
            )
            base = old['qty'] * _as_float(old.get('rate'), 0.0)
            old['amount'] = round(base - min(old['medicine_discount'], base), 2)
        else:
            merged[mid] = dict(med)
    return list(merged.values())


def counter_merge_money(
    base_total,
    base_discount,
    merged_lines,
    form_lines,
    discount_pct,
    rounding,
    discount_rs=None,
):
    """(discount_rs, rounding) that make a merged counter bill come to base + this form.

    The day's COUNTER SALE bill holds several walk-in sales. Each form is rounded to the rupee
    on its own, and F7 takes that rounded amount in cash, so the bill's cash is the sum of every
    form's own total. Writing the merged bill with only the latest form's rounding (and its
    discount applied to all the day's lines) left the bill a few paise off its cash on every
    merge: store 4 SCB1412 ended 18.08 total / 18 paid and COUNTER SALE drifted 715.24 ->
    715.32; store 127 picked up a phantom 0.44 credit.

    ``base_total`` / ``base_discount`` are what the bill held before this form. The discounts
    add up in rupees, and the rounding is whatever makes the merged lines come to
    base_total + this form's total. Pass no form lines to take a form back out.
    """
    from core.calc_engine import calc_bill_summary

    form = calc_bill_summary(form_lines or [], discount_pct or 0, rounding or 0,
                             discount_rs=discount_rs)
    disc_out = round(max(0.0, _as_float(base_discount, 0.0)) + form["discount_amount"], 2)
    pre = calc_bill_summary(merged_lines or [], 0, 0, discount_rs=disc_out)["pre_round_total"]
    target = round(_as_float(base_total, 0.0) + form["total_amount"], 2)
    return disc_out, round(target - pre, 2)


def append_counter_sale_today(
    conn,
    customer_id,
    customer_name,
    medicines,
    discount_pct,
    rounding,
    cash_paid,
    online_paid,
    doctor_name,
    doctor_phone,
    previous_due,
    discount_rs=None,
    bill_date=None,
):
    """
    Merge non-scheduled counter-sale lines into today's single counter bill.
    Returns (bill_no, sale_id) when merged, else None.
    """
    from core.customer_service import COUNTER_SALE, is_counter_sale_name

    if not is_counter_sale_name(customer_name):
        return None
    if any((m.get('schedule') or '').strip() for m in medicines):
        return None
    if (doctor_name or '').strip():
        return None

    sale_id = find_todays_counter_sale_id(conn, customer_id, bill_date)
    if not sale_id:
        return None

    bill_no = None
    old_cash = 0.0
    old_online = 0.0
    old_total = None
    old_discount = 0.0
    old_rounding = 0.0
    doc = None
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core.server_crud import get_doc

            doc = get_doc("sales", int(sale_id)) or {}
            if not doc:
                return None
            bill_no = doc.get("bill_no")
            old_cash = _as_float(doc.get("cash_paid"), 0.0)
            old_online = _as_float(doc.get("online_paid"), 0.0)
            old_total = doc.get("total_amount")
            old_discount = _as_float(doc.get("discount"), 0.0)
            old_rounding = _as_float(doc.get("rounding"), 0.0)
        else:
            cur = conn.cursor()
            cur.execute(
                "SELECT bill_no, COALESCE(cash_paid,0), COALESCE(online_paid,0), "
                "total_amount, COALESCE(discount,0), COALESCE(rounding,0) "
                "FROM sales WHERE id=?",
                (sale_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            bill_no, old_cash, old_online, old_total, old_discount, old_rounding = row
    except Exception as exc:
        print(f"[billing] append_counter_sale_today load: {exc}")
        return None

    # Reuse the document fetched above rather than pulling it again.
    existing = load_sale_medicines(conn, sale_id, doc=doc)
    merged = _merge_medicine_lines(existing, medicines)

    # The bill is what it already held plus this form's own total, so the cash each form paid
    # adds up to it (see counter_merge_money).
    if old_total is None:
        from core.calc_engine import calc_bill_summary

        old_total = calc_bill_summary(
            existing, 0, old_rounding, discount_rs=_as_float(old_discount, 0.0)
        )["total_amount"]
    disc_out, rounding_out = counter_merge_money(
        old_total, old_discount, merged, medicines, discount_pct, rounding, discount_rs
    )

    update_existing_bill(
        conn,
        sale_id,
        merged,
        existing_doc=doc,
        discount_pct=discount_pct,
        rounding=rounding_out,
        cash_paid=_as_float(old_cash, 0.0) + _as_float(cash_paid, 0.0),
        online_paid=_as_float(old_online, 0.0) + _as_float(online_paid, 0.0),
        customer_name=COUNTER_SALE,
        customer_phone='',
        doctor_name='',
        previous_due=previous_due,
        discount_rs=disc_out,
        bill_date=bill_date,
    )
    return bill_no, sale_id


def update_existing_bill_online_now(
    sale_id,
    medicines,
    discount_pct,
    rounding,
    cash_paid,
    online_paid,
    customer_name,
    customer_phone,
    doctor_name,
    previous_due,
    discount_rs=None,
    bill_date=None,
):
    """Flush worker: apply an Online sale edit to the server now."""
    from datetime import date

    from core.calc_engine import calc_bill_summary, calc_payment_result
    from core.online_catalog import find_customer_by_id, medicine_by_id, patch_docs
    from core.server_crud import (
        get_doc, push_bundle, _meta, bump_meta, _device_id, _line_field,
        _line_gst_percent,
    )

    existing = get_doc("sales", int(sale_id)) or {}
    # The store's copy this edit is worked out from, as read. A sale the store then skips is
    # compared with it (sale_edit_landing.land_skipped_sale_edit), so nothing below may change it.
    import copy as _copy

    edit_base = _copy.deepcopy(existing) if existing else None
    cu = str(existing.get("client_uuid") or "")
    summary = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    total = summary["total_amount"]
    disc_amt = summary["discount_amount"]
    disc_pct = summary["discount_pct"]
    prev_due = _as_float(
        existing.get("previous_due"), _as_float(previous_due, 0.0)
    )
    prev_credit = _as_float(existing.get("previous_credit"), 0.0)
    pay = calc_payment_result(
        total,
        _as_float(cash_paid, 0.0),
        _as_float(online_paid, 0.0),
        prev_due,
        prev_credit,
    )
    date_s = str(bill_date or existing.get("bill_date") or date.today())[:10]

    # A number whose year is not the year of the bill's date takes that year's
    # next number -- the year is part of the number. The test is the number's own
    # /FY tag against the date, not the old date against the new one, so an edit
    # that leaves the date alone still re-files a bill that an edit before the fix
    # moved across 1 April with its old-year number (its serial keeps pushing the
    # new year's next number up until it moves). Deliberate: the offline edit
    # (resync_sale_fy_number), the Online purchase edit and the phone
    # (FySerial.movesToAnotherFy) all apply this same test. Here it was left to
    # the server, which re-files such a bill while this PC kept listing and
    # printing the old number from its cache.
    from core.fy_serial import encode_sales_bill_no, fy_start_year_for_date, fy_start_year_in_code

    renumbered: dict = {}
    held_fy = fy_start_year_in_code(existing.get("bill_no"))
    if held_fy is not None and held_fy != fy_start_year_for_date(date_s):
        from core import server_api as api

        alloc = api.allocate_fy(api.store_token_for_active(), "sales", date_s) or {}
        serial = int(alloc.get("fy_serial") or 0)
        if serial <= 0:
            # An answer without a number used to push the edit under the OLD number,
            # filed in the year the date left. Refuse before anything is written, as
            # the phone does (FySerial.allocateSalesBillNo).
            raise RuntimeError(
                f"Bill {existing.get('bill_no') or sale_id} needs the next number for "
                f"{date_s}, and the server did not give one. Nothing was saved -- try again."
            )
        new_fy = int(alloc.get("fy_start_year") or fy_start_year_for_date(date_s))
        renumbered = {
            "bill_no": (alloc.get("bill_no") or "").strip()
            or encode_sales_bill_no(serial, new_fy),
            "fy_start_year": new_fy,
            "fy_serial": serial,
        }

    old_items = existing.get("items") or []
    old_by_med: dict[int, float] = {}
    for it in old_items:
        if not isinstance(it, dict):
            continue
        mid = int(it.get("medicine_id") or it.get("id") or 0)
        if mid <= 0:
            continue
        old_by_med[mid] = old_by_med.get(mid, 0.0) + _as_float(it.get("qty"), 0.0)
    # Missing items on GET must not be treated as qty 0 (would deduct new qty twice).
    skip_stock_ops = bool(existing) and not old_items

    from core.stock_utils import snapshot_sale_cost_price

    items = []
    new_by_med: dict[int, float] = {}
    try:
        from core.quick_sale_medicine import resolve_quick_sale_medicines

        resolve_quick_sale_medicines(None, medicines)
    except Exception:
        pass
    for m in medicines or []:
        mid = int(m.get("id") or 0)
        if mid <= 0:
            # Same rule as the create path: a line with no id must not be dropped
            # while its amount stays in the total.
            raise ValueError(
                f"Sale line '{m.get('medicine_name') or m.get('name') or '?'}' "
                "has no medicine id"
            )
        qty = _as_float(m.get("qty"), 0.0)
        new_by_med[mid] = new_by_med.get(mid, 0.0) + qty
        mp = medicine_by_id(mid) or get_doc("medicines", mid) or {}
        mtype = str(mp.get("type") or m.get("type") or "")
        unit = mp.get("unit") if mp.get("unit") is not None else m.get("unit")
        purchase_rate = _as_float(mp.get("rate"), 0.0)
        items.append({
            "medicine_id": mid,
            "qty": qty,
            "rate": _as_float(m.get("rate"), 0.0),
            "amount": _as_float(m.get("amount"), 0.0),
            "item_discount": _as_float(m.get("medicine_discount"), 0.0),
            "medicine_name": m.get("name") or mp.get("name") or "",
            "name": m.get("name") or mp.get("name") or "",
            "type": mtype,
            "batch_no": m.get("batch") or m.get("batch_no") or "",
            "expiry_date": m.get("expiry") or m.get("expiry_date") or "",
            # Editing a bill rewrites sales_items wholesale on the server
            # (upsertSale DELETEs then re-inserts), so omitting these here
            # would strip the schedule back off a bill that already had one.
            "schedule": _line_field(m, mp, "schedule"),
            "hsn_code": _line_field(m, mp, "hsn_code"),
            "manufacturer": _line_field(m, mp, "manufacturer"),
            # The same re-insert wrote gst_percent NULL on every edit, so the
            # rate a bill was sold at was lost the first time it was edited.
            "gst_percent": _line_gst_percent(m, mp),
            "cost_price": snapshot_sale_cost_price(purchase_rate, mtype, unit),
        })

    med_docs = []
    touched = set(old_by_med) | set(new_by_med)
    ver = int(existing.get("version") or 1) + 1
    for mid in touched:
        mp = medicine_by_id(mid) or get_doc("medicines", mid) or {
            "id": mid, "local_id": mid, "stock_qty": 0,
        }
        mp = dict(mp)
        mp["id"] = mid
        mp["local_id"] = mid
        qty_delta = int(round(-(_as_float(new_by_med.get(mid, 0.0), 0.0) - _as_float(old_by_med.get(mid, 0.0), 0.0))))
        if skip_stock_ops:
            qty_delta = 0
        try:
            mp["stock_qty"] = _as_float(mp.get("stock_qty"), 0.0) + qty_delta
        except Exception:
            pass
        if qty_delta != 0:
            mp["stock_ops"] = [{
                "op_uuid": f"sale:{cu or sale_id}:med:{mid}:edit:v{ver}",
                "op": "sale_edit",
                "qty_delta": qty_delta,
                "medicine_id": mid,
                "ref_collection": "sales",
                "ref_id": int(sale_id),
                "device_id": _device_id(),
            }]
        med_docs.append(bump_meta(mp) if qty_delta != 0 else _meta(mp))
    sale = dict(existing)
    sale.update({
        "id": int(sale_id),
        "local_id": int(sale_id),
        "bill_date": date_s,
        "total_amount": total,
        "discount": disc_amt,
        "discount_pct": disc_pct,
        "rounding": _as_float(rounding, 0.0),
        "amount_paid": pay["amount_paid"],
        "cash_paid": _as_float(cash_paid, 0.0),
        "online_paid": _as_float(online_paid, 0.0),
        "doctor_name": (doctor_name or "").strip().upper(),
        "due_amount": pay["due_amount"],
        "credit_amount": pay["credit_amount"],
        "total_due": pay["total_due"],
        "bill_cleared": 1 if pay["due_amount"] == 0 else 0,
        "items": items,
        "customer_name": customer_name or sale.get("customer_name") or "",
        **renumbered,
    })
    sale = bump_meta(sale)
    if cu:
        sale["client_uuid"] = cu
    customer_id = int(existing.get("customer_id") or 0)
    bundle = {"sales": [sale]}
    if med_docs:
        bundle["medicines"] = med_docs
    if customer_id:
        try:
            cust = find_customer_by_id(customer_id) or {}
            if not cust:
                cust = get_doc("customers", customer_id) or {}
            old_contrib = (
                _as_float(existing.get("total_amount"), 0.0)
                - _as_float(existing.get("amount_paid"), 0.0)
            )
            new_contrib = _as_float(total, 0.0) - _as_float(pay.get("amount_paid"), 0.0)
            delta = round(new_contrib - old_contrib, 2)
            cat_due = _as_float(cust.get("total_due"), 0.0)
            cat_credit = _as_float(cust.get("total_credit"), 0.0)
            net = round(cat_due - cat_credit + delta, 2)
            cust_out = bump_meta(dict(cust))
            cust_out["id"] = customer_id
            cust_out["local_id"] = customer_id
            if customer_name:
                cust_out["name"] = str(customer_name).strip().upper()
            if customer_phone is not None:
                cust_out["phone"] = str(customer_phone or "").strip()
            cust_out["total_due"] = max(0.0, net)
            cust_out["total_credit"] = max(0.0, -net)
            bundle["customers"] = [cust_out]
        except Exception:
            pass
    from core.server_crud import (
        BundleDocumentRejected, SaleRowNotSaved, push_sale_under_free_number, pushed_status,
    )

    answer = None
    try:
        answer = push_bundle(bundle)
    except BundleDocumentRejected as exc:
        if not exc.only_rows_of("sales"):
            raise
        # Only the bill row was refused: this bundle's medicine and customer documents
        # already committed, and re-sending the edit would move stock and balance twice.
        # From here on only the bill row goes -- now, from the queue, or on Retry.
        if not renumbered or not exc.only_number_taken("sales"):
            raise SaleRowNotSaved(sale, exc, renumber=bool(renumbered)) from exc
        # The new year's number went to another device first: the next free number.
        sale = push_sale_under_free_number(sale, date_s, int(renumbered["fy_serial"]), exc)
    if edit_base and answer is not None and pushed_status(answer, "sales", int(sale_id)) == "skipped":
        # The store skipped the sale and applied this bundle's stock movements: a customer
        # receipt's cascade, or another device, moved the bill past this edit's version. The edit
        # goes over the store's copy, or raises SaleEditNotSaved saying why it cannot.
        from core.sale_edit_landing import land_skipped_sale_edit

        land_skipped_sale_edit(sale, edit_base, med_docs)
    try:
        patch_docs("sales", [sale])
        if med_docs:
            patch_docs("medicines", med_docs)
        if bundle.get("customers"):
            patch_docs("customers", bundle["customers"])
    except Exception:
        pass


def update_existing_bill(conn, sale_id, medicines, discount_pct,
                         rounding, cash_paid, online_paid,
                         customer_name, customer_phone, doctor_name,
                         previous_due, discount_rs=None, bill_date=None,
                         *, existing_doc=None):
    """
    Update an existing sale: restore old stock, replace items, recalculate.
    Online: enqueue mutation and return immediately (background push).
    """
    from core.calc_engine import calc_bill_summary, calc_payment_result
    from datetime import date
    from core.online_guard import ensure_can_mutate
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        cid = 0
        cu = ""
        try:
            from core.online_catalog import find_customer_by_name
            from core.online_mutation_queue import pending_by_local_id
            from core.server_crud import get_doc

            sid = int(sale_id or 0)
            if sid > 0:
                # `is not None` on purpose: an empty dict from the caller means
                # "already looked, found nothing" and must not trigger a refetch.
                existing = (
                    existing_doc
                    if existing_doc is not None
                    else (get_doc("sales", sid) or {})
                )
                cid = int(existing.get("customer_id") or 0)
                cu = str(existing.get("client_uuid") or "")
            elif sid < 0:
                pending = pending_by_local_id("sales", sid)
                if pending:
                    cu = str(pending.get("client_uuid") or "")
                    try:
                        cid = int((pending.get("payload") or {}).get("customer_id") or 0)
                    except (TypeError, ValueError):
                        cid = 0
            if cid <= 0 and customer_name:
                found = find_customer_by_name(customer_name) or {}
                cid = int(found.get("id") or 0)
        except Exception:
            cid = 0
            cu = ""
        _enqueue_sale_upsert(
            customer_id=cid,
            medicines=medicines,
            discount_pct=discount_pct,
            rounding=rounding,
            cash_paid=cash_paid,
            online_paid=online_paid,
            doctor_name=doctor_name or "",
            doctor_phone="",
            previous_due=previous_due,
            discount_rs=discount_rs,
            bill_date=bill_date,
            customer_name=customer_name or "",
            customer_phone=customer_phone or "",
            sale_id=sale_id,
            client_uuid=cu or None,
        )
        return

    ensure_can_mutate()

    cur = conn.cursor()

    summary  = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    total    = summary['total_amount']
    disc_amt = summary['discount_amount']
    disc_pct = summary['discount_pct']

    # Read stored previous_due/credit snapshots for this bill (display only)
    cur.execute(
        "SELECT COALESCE(previous_due,0), COALESCE(previous_credit,0) FROM sales WHERE id=?",
        (sale_id,))
    snap = cur.fetchone()
    prev_due    = _as_float(snap[0], 0.0) if snap else 0.0
    prev_credit = _as_float(snap[1], 0.0) if snap else 0.0

    pay = calc_payment_result(total, cash_paid, online_paid, prev_due, prev_credit)
    amount_paid   = pay['amount_paid']
    due_amount    = pay['due_amount']
    credit_amount = pay['credit_amount']
    total_due     = pay['total_due']
    bill_cleared  = 1 if due_amount == 0 else 0
    account_cleared = bill_cleared

    bill_date_str = str(bill_date or "").strip()
    if bill_date_str and " " in bill_date_str:
        bill_date_str = bill_date_str.split(" ")[0]
    if not bill_date_str:
        bill_date_str = date.today().strftime("%Y-%m-%d")

    # Point the bill at the right customer -- do NOT rename the one it has.
    #
    # This used to UPDATE customers SET name=... for whoever the bill belonged
    # to, so correcting the name on one bill renamed that customer everywhere:
    # every other bill of theirs, their ledger and their outstanding due all
    # changed identity. Typing a different name here means "this bill is that
    # customer's", so find or create them and re-point the sale.
    new_name = customer_name.strip().upper()
    if new_name:
        row = cur.execute(
            "SELECT c.id, COALESCE(c.name,''), COALESCE(c.phone,'') "
            "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id "
            "WHERE s.id=?",
            (sale_id,),
        ).fetchone()
        cur_cust_id = int(row[0]) if row and row[0] else 0
        cur_name = str(row[1] or "").strip().upper() if row else ""
        if cur_name and cur_name != new_name:
            match = cur.execute(
                "SELECT id FROM customers WHERE UPPER(name)=? "
                "AND COALESCE(deleted,0)=0 LIMIT 1",
                (new_name,),
            ).fetchone()
            if match:
                target_id = int(match[0])
            else:
                cur.execute(
                    "INSERT INTO customers (name, phone) VALUES (?, ?)",
                    (new_name, customer_phone.strip()),
                )
                target_id = int(cur.lastrowid)
            cur.execute(
                "UPDATE sales SET customer_id=? WHERE id=?", (target_id, sale_id)
            )
            # Both parties' balances move: the old one loses this bill, the new
            # one gains it.
            for cid in {cur_cust_id, target_id}:
                if cid:
                    try:
                        from core.customer_service import recalculate_customer_due

                        recalculate_customer_due(conn, cid)
                    except Exception:
                        pass
        elif cur_cust_id and customer_phone.strip():
            # Same customer, only a phone correction.
            cur.execute(
                "UPDATE customers SET phone=? WHERE id=?",
                (customer_phone.strip(), cur_cust_id),
            )

    cur.execute("""
        UPDATE sales SET
            bill_date=?, total_amount=?, discount=?, discount_pct=?, rounding=?,
            amount_paid=?, cash_paid=?, online_paid=?, doctor_name=?,
            due_amount=?, credit_amount=?, total_due=?,
            bill_cleared=?, account_cleared=?,
            deleted=0
        WHERE id=?
    """, (bill_date_str, total, disc_amt, disc_pct, rounding,
          amount_paid, cash_paid, online_paid,
          doctor_name.strip().upper() if doctor_name else '',
          due_amount, credit_amount, total_due, bill_cleared, account_cleared, sale_id))

    # The bill's date was just changed. If it crossed 1 April it now belongs to
    # another year's series, so it needs that year's next number and a matching
    # FY stamp -- otherwise the new year inherits a serial like 150 and the next
    # bill jumps to 151. This is the real edit path; the autosave finaliser has
    # the same two calls.
    try:
        from core.fy_serial import patch_sale_fy_fields, resync_sale_fy_number

        row_no = cur.execute(
            "SELECT COALESCE(bill_no,'') FROM sales WHERE id=?", (sale_id,)
        ).fetchone()
        current_no = str(row_no[0]) if row_no else ""
        if current_no:
            current_no = resync_sale_fy_number(
                cur, conn, sale_id, current_no, bill_date_str
            )
            patch_sale_fy_fields(cur, sale_id, current_no, bill_date_str)
    except Exception as exc:
        print(f"[fy] sale edit resync: {exc}")

    cur.execute("SELECT customer_id FROM sales WHERE id=?", (sale_id,))
    customer_id = cur.fetchone()[0]

    # Restore old stock
    cur.execute("SELECT medicine_id, qty FROM sales_items WHERE sale_id=?", (sale_id,))
    for med_id, qty in cur.fetchall():
        restore_qty = abs(_as_float(qty, 0.0))
        cur.execute("UPDATE medicines SET stock_qty=stock_qty+? WHERE id=?", (restore_qty, med_id))

    cur.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
    from core.quick_sale_medicine import resolve_quick_sale_medicines
    resolve_quick_sale_medicines(conn, medicines)
    _insert_items_and_update_stock(cur, sale_id, medicines)

    from core.customer_service import recalculate_customer_due
    med_ids = [m['id'] for m in medicines if m.get('id')]

    # Online sale edits are handled above (server-first). Never recalculate from
    # empty local SQLite and push wiped customer dues.
    from core.sync_coordinator import stamp_sale_meta
    stamp_sale_meta(conn, sale_id, med_ids, commit=False)
    conn.commit()
    recalculate_customer_due(conn, customer_id)
    try:
        from core.sync_coordinator import after_sale_saved
        after_sale_saved(conn, sale_id, med_ids)
    except Exception:
        pass

    print(f"[EDIT] sale_id={sale_id} total={total:.2f} paid={amount_paid:.2f} "
          f"-> due={due_amount:.2f} credit={credit_amount:.2f}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_items_and_update_stock(cur, sale_id, medicines):
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    for med in medicines:
        # Snapshot cost_price at sale time from latest purchase
        med_id = med['id']
        cur.execute("""
            SELECT pi.rate, m.type, COALESCE(m.unit, '1')
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            WHERE pi.medicine_id = ?
            ORDER BY pi.id DESC LIMIT 1
        """, (med_id,))
        cp_row = cur.fetchone()
        if cp_row and cp_row[0] is not None:
            pi_rate, mtype, unit = cp_row
            if is_strip_count_type(mtype or ''):
                tps = parse_tablets_per_stripe(unit)
                rate_f = _as_float(pi_rate, 0.0, field="purchase rate")
                cost_price = round(rate_f / tps, 4) if tps else round(rate_f, 4)
            else:
                cost_price = round(_as_float(pi_rate, 0.0, field="purchase rate"), 4)
        else:
            cost_price = 0.0

        cur.execute("""
            INSERT INTO sales_items
                (sale_id, medicine_id, qty, rate, gst_percent, amount, item_discount, cost_price)
            VALUES (?,?,?,?,?,?,?,?)
        """, (sale_id, med_id, med['qty'], med['rate'],
              med.get('gst_percent', 0), med['amount'],
              med.get('medicine_discount', 0), cost_price))
        cur.execute(
            "UPDATE medicines SET stock_qty=stock_qty-? WHERE id=?",
            (med['qty'], med_id))


def _insert_items_no_stock(cur, sale_id, medicines):
    """Insert sales_items without decrementing stock (autosave drafts)."""
    from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

    for med in medicines:
        med_id = med['id']
        cur.execute("""
            SELECT pi.rate, m.type, COALESCE(m.unit, '1')
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            WHERE pi.medicine_id = ?
            ORDER BY pi.id DESC LIMIT 1
        """, (med_id,))
        cp_row = cur.fetchone()
        if cp_row and cp_row[0] is not None:
            pi_rate, mtype, unit = cp_row
            if is_strip_count_type(mtype or ''):
                tps = parse_tablets_per_stripe(unit)
                rate_f = _as_float(pi_rate, 0.0, field="purchase rate")
                cost_price = round(rate_f / tps, 4) if tps else round(rate_f, 4)
            else:
                cost_price = round(_as_float(pi_rate, 0.0, field="purchase rate"), 4)
        else:
            cost_price = 0.0

        cur.execute("""
            INSERT INTO sales_items
                (sale_id, medicine_id, qty, rate, gst_percent, amount, item_discount, cost_price)
            VALUES (?,?,?,?,?,?,?,?)
        """, (sale_id, med_id, med['qty'], med['rate'],
              med.get('gst_percent', 0), med['amount'],
              med.get('medicine_discount', 0), cost_price))


def _sale_payment_fields(conn, customer_id, medicines, discount_pct, rounding,
                         cash_paid, online_paid, previous_due, discount_rs=None):
    from core.calc_engine import calc_bill_summary, calc_payment_result

    cur = conn.cursor()
    summary = calc_bill_summary(medicines, discount_pct, rounding, discount_rs=discount_rs)
    total = summary['total_amount']
    disc_amt = summary['discount_amount']
    disc_pct = summary['discount_pct']

    cur.execute(
        "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) FROM customers WHERE id=?",
        (customer_id,))
    cust_row = cur.fetchone()
    prev_due = round(_as_float(cust_row[0], 0.0), 2) if cust_row else round(max(0.0, _as_float(previous_due, 0.0)), 2)
    prev_credit = round(_as_float(cust_row[1], 0.0), 2) if cust_row else 0.0

    pay = calc_payment_result(total, cash_paid, online_paid, prev_due, prev_credit)
    return summary, total, disc_amt, disc_pct, prev_due, prev_credit, pay


def save_autosave_bill(conn, customer_id, medicines, discount_pct, rounding,
                       cash_paid, online_paid, doctor_name, doctor_phone,
                       previous_due, discount_rs=None, bill_date=None):
    """
    Insert an autosave draft sale — no stock change, is_autosave=1.
    Returns (bill_no, sale_id).

    Online: drafts stay device-local only (crash recovery); never pushed to server.
    If local DB is wiped / write fails Online, no-op gracefully (finalize uses form data).
    """
    try:
        cur = conn.cursor()
        bill_no = _allocate_bill_number(conn, "ASV")

        summary, total, disc_amt, disc_pct, prev_due, prev_credit, pay = _sale_payment_fields(
            conn, customer_id, medicines, discount_pct, rounding,
            cash_paid, online_paid, previous_due, discount_rs=discount_rs,
        )
        amount_paid = pay['amount_paid']
        due_amount = pay['due_amount']
        credit_amount = pay['credit_amount']
        total_due = pay['total_due']
        bill_cleared = 1 if due_amount == 0 else 0
        doc_upper = doctor_name.strip().upper() if doctor_name else ''

        cur.execute("""
            INSERT INTO sales
                (bill_no, customer_id, bill_date, total_amount, discount, discount_pct, rounding,
                 amount_paid, cash_paid, online_paid, doctor_name,
                 previous_due, previous_credit, due_amount, credit_amount, total_due,
                 bill_cleared, account_cleared, is_autosave)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1)
        """, (bill_no, customer_id, bill_date or date.today(), total, disc_amt, disc_pct, rounding,
              amount_paid, cash_paid, online_paid, doc_upper,
              prev_due, prev_credit, due_amount, credit_amount, total_due, bill_cleared))
        sale_id = cur.lastrowid
        _insert_items_no_stock(cur, sale_id, medicines)
        conn.commit()
        return bill_no, sale_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                return "", 0
        except Exception:
            pass
        raise


def update_autosave_bill(conn, sale_id, customer_id, medicines, discount_pct, rounding,
                         cash_paid, online_paid, doctor_name, customer_name, customer_phone,
                         previous_due, discount_rs=None, bill_date=None):
    """Update autosave draft — replace items without stock changes.

    Online: drafts stay device-local only; missing row / wipe → no-op.
    """
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(previous_due,0), COALESCE(previous_credit,0), COALESCE(is_autosave,0) "
            "FROM sales WHERE id=?",
            (sale_id,),
        )
        snap = cur.fetchone()
        if not snap:
            return
        # Refuse to overwrite a real (non-draft) sale — that was eating F10/F11 edits.
        if not int(snap[2] or 0):
            return
        prev_due = _as_float(snap[0], 0.0)
        prev_credit = _as_float(snap[1], 0.0)

        summary, total, disc_amt, disc_pct, _, _, pay = _sale_payment_fields(
            conn, customer_id, medicines, discount_pct, rounding,
            cash_paid, online_paid, previous_due, discount_rs=discount_rs,
        )
        amount_paid = pay['amount_paid']
        due_amount = pay['due_amount']
        credit_amount = pay['credit_amount']
        total_due = pay['total_due']
        bill_cleared = 1 if due_amount == 0 else 0
        doc_upper = doctor_name.strip().upper() if doctor_name else ''

        cur.execute(
            "UPDATE customers SET name=?, phone=? WHERE id=?",
            (customer_name.strip().upper(), customer_phone.strip(), customer_id),
        )
        cur.execute("""
            UPDATE sales SET
                customer_id=?, bill_date=?, total_amount=?, discount=?, discount_pct=?, rounding=?,
                amount_paid=?, cash_paid=?, online_paid=?, doctor_name=?,
                due_amount=?, credit_amount=?, total_due=?, bill_cleared=?
            WHERE id=? AND COALESCE(is_autosave,0)=1
        """, (customer_id, bill_date or date.today(), total, disc_amt, disc_pct, rounding,
              amount_paid, cash_paid, online_paid, doc_upper,
              due_amount, credit_amount, total_due, bill_cleared, sale_id))

        cur.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
        _insert_items_no_stock(cur, sale_id, medicines)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                return
        except Exception:
            pass
        raise


def finalize_autosave_bill(conn, sale_id, customer_id, medicines, discount_pct, rounding,
                           cash_paid, online_paid, doctor_name, doctor_phone,
                           customer_name, customer_phone, previous_due,
                           discount_rs=None, bill_date=None):
    """
    Convert autosave draft to a real sale: apply stock, assign SCB bill_no, clear is_autosave.
    Counter sales fold into today's single counter bill when one already exists.
    Returns (bill_no, sale_id).

    Online: drafts are local-only — always create a real server bill from form data
    (counter merge or save_new_bill), even when the local draft row is missing.
    """
    from core.customer_service import COUNTER_SALE, is_counter_sale_name
    from core.sync_prefs import is_online_mode

    def _finalize_as_new():
        if (
            is_counter_sale_name(customer_name)
            and not any((m.get('schedule') or '').strip() for m in medicines)
            and not (doctor_name or '').strip()
        ):
            merged = append_counter_sale_today(
                conn, customer_id, customer_name, medicines, discount_pct,
                rounding, cash_paid, online_paid, doctor_name, doctor_phone,
                previous_due, discount_rs=discount_rs, bill_date=bill_date,
            )
            if merged:
                return merged
        return save_new_bill(
            conn, customer_id, medicines, discount_pct, rounding,
            cash_paid, online_paid, doctor_name, doctor_phone, previous_due,
            discount_rs=discount_rs, bill_date=bill_date,
        )

    if is_online_mode():
        # Drop local draft if present; real bill is always written Online.
        if sale_id:
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
                cur.execute(
                    "DELETE FROM sales WHERE id=? AND COALESCE(is_autosave,0)=1",
                    (sale_id,),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        return _finalize_as_new()

    cur = conn.cursor()
    cur.execute(
        "SELECT bill_no, COALESCE(is_autosave,0), COALESCE(deleted,0) FROM sales WHERE id=?",
        (sale_id,),
    )
    existing_row = cur.fetchone()

    # Draft row missing (often deleted when opening a second sale tab with the
    # old clear_form path) — medicines are still in the UI. Same rules as a
    # normal counter save: merge into today's counter bill if one exists,
    # otherwise create a new bill.
    if not existing_row:
        return _finalize_as_new()

    old_bill_no, was_autosave, was_deleted = existing_row
    # Already a real bill (re-save after a failed PDF, etc.) — update in place
    # with proper stock restore, do not double-reduce stock.
    if not int(was_autosave or 0) and not int(was_deleted or 0):
        update_existing_bill(
            conn, sale_id, medicines,
            discount_pct=discount_pct, rounding=rounding,
            cash_paid=cash_paid, online_paid=online_paid,
            customer_name=customer_name, customer_phone=customer_phone,
            doctor_name=doctor_name, previous_due=previous_due,
            discount_rs=discount_rs, bill_date=bill_date,
        )
        cur.execute("SELECT bill_no FROM sales WHERE id=?", (sale_id,))
        row = cur.fetchone()
        return (row[0] if row else str(sale_id)), sale_id

    # Autosave used to bypass append_counter_sale_today — merge here so one
    # counter bill per day still holds when autosave is on.
    if (
        is_counter_sale_name(customer_name)
        and not any((m.get('schedule') or '').strip() for m in medicines)
        and not (doctor_name or '').strip()
    ):
        target = find_todays_counter_sale_id(conn, customer_id, bill_date)
        if target and int(target) != int(sale_id):
            bill_no = None
            old_cash = 0.0
            old_online = 0.0
            target_ok = False
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    from core.server_crud import get_doc

                    doc = get_doc("sales", int(target)) or {}
                    if doc:
                        bill_no = doc.get("bill_no")
                        old_cash = _as_float(doc.get("cash_paid"), 0.0)
                        old_online = _as_float(doc.get("online_paid"), 0.0)
                        target_ok = True
                else:
                    cur.execute(
                        "SELECT bill_no, COALESCE(cash_paid,0), COALESCE(online_paid,0) "
                        "FROM sales WHERE id=?",
                        (target,),
                    )
                    row = cur.fetchone()
                    if row:
                        bill_no, old_cash, old_online = row
                        target_ok = True
            except Exception as exc:
                print(f"[billing] finalize counter merge load: {exc}")
                target_ok = False
            if target_ok:
                existing = load_sale_medicines(conn, target)
                merged = _merge_medicine_lines(existing, medicines)
                # Keep draft until target update succeeds (same transaction).
                cur.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
                cur.execute(
                    "DELETE FROM sales WHERE id=? AND COALESCE(is_autosave,0)=1",
                    (sale_id,),
                )
                update_existing_bill(
                    conn,
                    target,
                    merged,
                    discount_pct=discount_pct,
                    rounding=rounding,
                    cash_paid=_as_float(old_cash, 0.0) + _as_float(cash_paid, 0.0),
                    online_paid=_as_float(old_online, 0.0) + _as_float(online_paid, 0.0),
                    customer_name=COUNTER_SALE,
                    customer_phone='',
                    doctor_name='',
                    previous_due=previous_due,
                    discount_rs=discount_rs,
                    bill_date=bill_date,
                )
                return bill_no, target

    if (old_bill_no or '').startswith('ASV') or int(was_autosave or 0):
        bill_no = _allocate_bill_number(
            conn, "SCB", bill_date=bill_date, exclude_sale_id=sale_id,
        )
    else:
        bill_no = old_bill_no or _allocate_bill_number(
            conn, "SCB", bill_date=bill_date, exclude_sale_id=sale_id,
        )

    doc_upper = doctor_name.strip().upper() if doctor_name else ''
    if doc_upper:
        cur.execute("SELECT id FROM doctors WHERE UPPER(name)=?", (doc_upper,))
        existing = cur.fetchone()
        if not existing:
            cur.execute("INSERT INTO doctors (name, phone) VALUES (?,?)",
                        (doc_upper, doctor_phone))
        elif doctor_phone:
            cur.execute("UPDATE doctors SET phone=? WHERE UPPER(name)=?",
                        (doctor_phone, doc_upper))

    summary, total, disc_amt, disc_pct, prev_due, prev_credit, pay = _sale_payment_fields(
        conn, customer_id, medicines, discount_pct, rounding,
        cash_paid, online_paid, previous_due, discount_rs=discount_rs,
    )
    amount_paid = pay['amount_paid']
    due_amount = pay['due_amount']
    credit_amount = pay['credit_amount']
    total_due = pay['total_due']
    bill_cleared = 1 if due_amount == 0 else 0

    cur.execute(
        "UPDATE customers SET name=?, phone=? WHERE id=?",
        (customer_name.strip().upper(), customer_phone.strip(), customer_id),
    )
    cur.execute("""
        UPDATE sales SET
            bill_no=?, customer_id=?, bill_date=?, total_amount=?, discount=?, discount_pct=?,
            rounding=?, amount_paid=?, cash_paid=?, online_paid=?, doctor_name=?,
            previous_due=?, previous_credit=?, due_amount=?, credit_amount=?, total_due=?,
            bill_cleared=?, is_autosave=0, deleted=0
        WHERE id=?
    """, (bill_no, customer_id, bill_date or date.today(), total, disc_amt, disc_pct, rounding,
          amount_paid, cash_paid, online_paid, doc_upper,
          prev_due, prev_credit, due_amount, credit_amount, total_due, bill_cleared, sale_id))
    if cur.rowcount != 1:
        conn.rollback()
        return save_new_bill(
            conn, customer_id, medicines, discount_pct, rounding,
            cash_paid, online_paid, doctor_name, doctor_phone, previous_due,
            discount_rs=discount_rs, bill_date=bill_date,
        )

    from core.fy_serial import patch_sale_fy_fields, resync_sale_fy_number

    # A date edit that crosses 1 April moves the bill into another year's
    # series, so it needs that year's next number -- keeping the old one made
    # the new year inherit a serial of 150 and the next bill jump to 151.
    bill_no = resync_sale_fy_number(
        cur, conn, sale_id, bill_no, bill_date or date.today()
    )
    patch_sale_fy_fields(cur, sale_id, bill_no, bill_date or date.today())

    cur.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
    from core.quick_sale_medicine import resolve_quick_sale_medicines
    resolve_quick_sale_medicines(conn, medicines)
    _insert_items_and_update_stock(cur, sale_id, medicines)

    from core.customer_service import recalculate_customer_due
    from core.sync_prefs import is_online_mode
    from core.online_guard import commit_after_cloud_push
    med_ids = [m['id'] for m in medicines if m.get('id')]

    if is_online_mode():
        recalculate_customer_due(conn, customer_id, commit=False, sync=False)

        def _push():
            from core.sync_coordinator import push_sale_now
            return push_sale_now(conn, sale_id, med_ids, commit_meta=False)

        def _push_conn(c):
            from core.sync_coordinator import push_sale_now
            return push_sale_now(c, sale_id, med_ids, commit_meta=False)

        commit_after_cloud_push(conn, _push, push_with_conn=_push_conn)
        try:
            from core.sync_coordinator import after_sale_saved
            after_sale_saved(conn, sale_id, med_ids, already_pushed=True)
        except Exception:
            pass
    else:
        from core.sync_coordinator import stamp_sale_meta
        stamp_sale_meta(conn, sale_id, med_ids, commit=False)
        conn.commit()
        recalculate_customer_due(conn, customer_id)
        try:
            from core.sync_coordinator import after_sale_saved
            after_sale_saved(conn, sale_id, med_ids)
        except Exception:
            pass

    from core.fy_serial import display_sales_bill_no

    return display_sales_bill_no(bill_no), sale_id


def fetch_recent_sales(conn, limit=5):
    """Return list of (id, bill_no, bill_date, customer_name, total_amount) — saved bills only."""
    cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.bill_no, s.bill_date, c.name, s.total_amount
        FROM sales s JOIN customers c ON s.customer_id=c.id
        WHERE COALESCE(s.is_autosave, 0) = 0 AND COALESCE(s.deleted, 0) = 0
        ORDER BY s.bill_date DESC, COALESCE(s.fy_serial, s.id) DESC, s.id DESC
        LIMIT ?
    """, (limit,))
    return cur.fetchall()


def fetch_last_sale_id(conn):
    """Most recent saved (non-autosave) sale — same order as Sales History."""
    cur = conn.cursor()
    cur.execute("""
        SELECT s.id FROM sales s
        WHERE COALESCE(s.is_autosave, 0) = 0 AND COALESCE(s.deleted, 0) = 0
        ORDER BY s.bill_date DESC, COALESCE(s.fy_serial, s.id) DESC, s.id DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    return row[0] if row else None


def delete_autosave_bill(conn, sale_id: int) -> bool:
    """Remove an autosave draft sale and its lines. Returns True if deleted."""
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(is_autosave, 0) FROM sales WHERE id=?",
        (sale_id,),
    )
    row = cur.fetchone()
    if not row or not int(row[0] or 0):
        return False
    cur.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
    cur.execute("DELETE FROM sales WHERE id=?", (sale_id,))
    conn.commit()
    return True


def repair_sales_due_fields(conn):
    """
    Recalculate due_amount, total_due, credit_amount, and bill_cleared on every sale.
    Also refreshes each affected customer's live balance.
    Returns number of sales updated.
    """
    from core.calc_engine import calc_payment_result
    from core.customer_service import recalculate_customer_due

    cur = conn.cursor()
    cur.execute("""
        SELECT id, customer_id,
               COALESCE(total_amount, 0), COALESCE(cash_paid, 0), COALESCE(online_paid, 0),
               COALESCE(amount_paid, 0), COALESCE(previous_due, 0), COALESCE(previous_credit, 0)
        FROM sales
        WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0
    """)
    customer_ids = set()
    fixed = 0
    for row in cur.fetchall():
        sale_id, cust_id, total, cash, online, amount_paid, prev_due, prev_credit = row
        if not cash and not online and amount_paid:
            cash, online = _as_float(amount_paid, 0.0), 0.0
        pay = calc_payment_result(total, cash, online, prev_due, prev_credit)
        bill_cleared = 1 if pay['due_amount'] < 0.01 else 0
        cur.execute("""
            UPDATE sales
            SET due_amount=?, credit_amount=?, total_due=?, bill_cleared=?,
                account_cleared=CASE WHEN ? < 0.01 THEN 1 ELSE 0 END
            WHERE id=?
        """, (pay['due_amount'], pay['credit_amount'], pay['total_due'], bill_cleared,
              pay['total_due'], sale_id))
        if cust_id:
            customer_ids.add(cust_id)
        fixed += 1
    conn.commit()

    for cid in customer_ids:
        try:
            recalculate_customer_due(conn, cid)
        except Exception:
            pass
    return fixed
