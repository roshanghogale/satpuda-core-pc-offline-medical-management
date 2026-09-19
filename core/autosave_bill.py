"""Autosave that writes a REAL bill and keeps updating that same bill.

What changed and why
--------------------
Autosave used to insert an ``is_autosave=1`` draft with an ``ASV`` number.
Drafts are filtered out of history, the ledger, stock and every report, and in
Online mode they were written to the engine's ``:memory:`` sqlite and never
reached the server -- so a crash lost them anyway.

Now the first tick creates a real saved bill and every later tick UPDATES that
one bill. Two rules hold it together:

  * one bill per in-progress form -- the bill number is allocated once, on the
    first tick, and never again;
  * one counter-sale bill per day -- every counter sale that day lands in that
    bill, and a new local date starts a new one.

The hard part is the counter bill: it holds other people's sales too. So a
tick never "appends" the form. It rewrites the bill as

    what the bill holds now  -  what this form put there last time  +  the form

which is idempotent, survives another device adding lines between two ticks,
and lets two counter tabs run at once. ``core.autosave_session`` is what
remembers "what this form put there last time".
"""

from __future__ import annotations

from typing import Any

from core import autosave_session as _sess


def _f(value, default: float = 0.0) -> float:
    from core.billing_service import _as_float

    return _as_float(value, default)


def _is_counter_form(customer_name, medicines, doctor_name) -> bool:
    """Same gate as append_counter_sale_today: only plain walk-in lines merge."""
    from core.customer_service import is_counter_sale_name

    if not is_counter_sale_name(customer_name or ""):
        return False
    if any((m.get("schedule") or "").strip() for m in (medicines or [])):
        return False
    if (doctor_name or "").strip():
        return False
    return True


class AutosaveTargetUnavailable(RuntimeError):
    """The store could not say what the bill this form owns holds right now.

    Never silently start a second bill on this: a tick that cannot read its own
    bill is a network blip nine times in ten, and opening another one charges
    the customer twice.
    """


class AutosaveBillRefused(RuntimeError):
    """The server refused the bill row of the bill this form owns.

    The customer's balance and the stock of that bill are on the server; its row waits in
    Sales History as refused until the shop retries or discards it. No tick writes around
    it -- not an edit of a bill the server does not hold, and never a second bill.
    """


class AutosaveBillWaiting(RuntimeError):
    """The bill row of the bill this form owns has not reached the server yet.

    Its balance and stock are on the server; only the row is still to go, on its own. A change
    to the form cannot be written to that bill until the row lands -- queued behind it, the
    change posted the stock a second time whenever the row was refused and discarded.
    """


class AutosaveBillDiscarded(AutosaveBillRefused):
    """The shop discarded the refused bill row this form owned (Settings → Sync).

    That row will never exist. The customer's balance and the stock it took stay as the server
    holds them. Nothing more is written from this form: written again, the sale was saved as a
    new bill and posted a second time. Clearing the form lets its record go.
    """


def _discarded_message() -> str:
    return (
        "The refused bill from this form was discarded in Settings → Sync. The customer's "
        "balance and the stock it took stay as they are, and nothing more is written from "
        "this form. Clear the form, and check the customer's account before billing this "
        "sale again."
    )


#: The store could not answer. NOT "the bill is gone" -- the two used to be the
#: same answer here, and that is a double bill.
UNKNOWN = object()


def _pending_items_as_doc(payload: dict) -> dict:
    """The queued create's own lines, shaped like a server sale document.

    A queued bill has no server document to read, so a second counter tab that
    attached to it read NOTHING back and rewrote the day bill as its own lines
    alone -- the first form's sale simply vanished before it was ever pushed.
    The queue payload is the only record of those lines, so it is what the
    merge has to subtract from.
    """
    items = []
    for med in payload.get("medicines") or []:
        if not isinstance(med, dict):
            continue
        try:
            mid = int(med.get("id") or med.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        items.append(
            {
                "medicine_id": mid,
                "qty": _f(med.get("qty")),
                "rate": _f(med.get("rate")),
                "amount": _f(med.get("amount")),
                "item_discount": _f(med.get("medicine_discount") or med.get("item_discount")),
                "medicine_name": med.get("name") or med.get("medicine_name") or "",
                "batch_no": med.get("batch") or med.get("batch_no") or "",
                "expiry_date": med.get("expiry") or med.get("expiry_date") or "",
                "type": med.get("type") or "",
                "schedule": med.get("schedule") or "",
                # Blank stays blank, as load_sale_medicines keeps it: 0.0 here was pushed
                # as an exempt 0% by the next counter tick.
                "gst_percent": (
                    None if med.get("gst_percent") in (None, "") else _f(med.get("gst_percent"))
                ),
                "unit": med.get("unit") or "1",
                "mrp": _f(med.get("mrp")),
            }
        )
    return {"items": items}


def _sale_snapshot(conn, sale_id) -> dict | None:
    """(bill_no, cash_paid, online_paid) for a live bill.

    None means the bill is positively gone. ``UNKNOWN`` means the store could
    not be asked -- Online, ``server_crud.get_doc`` answers None both for a
    deleted bill and for an unreachable server, and treating that as "gone"
    dropped the session and opened a SECOND real bill on one dropped packet.

    Online reads the server document -- the local sqlite is ``:memory:`` there
    and knows nothing. A negative id is a create still sitting in the mutation
    queue; it is a real bill-to-be, so it counts as live, and its queued lines
    come back as the document.
    """
    try:
        sid = int(sale_id or 0)
    except (TypeError, ValueError):
        return None
    if sid == 0:
        return None

    from core.sync_prefs import is_online_mode

    if sid < 0:
        try:
            from core.online_mutation_queue import pending_by_local_id

            pending = pending_by_local_id("sales", sid)
        except Exception as exc:
            print(f"[autosave] queue lookup failed: {exc}")
            return UNKNOWN
        if not pending:
            # The queue only hands back rows still waiting. A create that has
            # flushed has a real server id this device does not know -- which
            # is emphatically not "the bill is gone".
            return UNKNOWN
        payload = pending.get("payload") or {}
        return {
            "bill_no": str(payload.get("bill_no") or "PENDING"),
            "cash_paid": _f(payload.get("cash_paid")),
            "online_paid": _f(payload.get("online_paid")),
            "total_amount": payload.get("total_amount"),
            "discount": _f(payload.get("discount")),
            "rounding": _f(payload.get("rounding")),
            "doc": _pending_items_as_doc(payload),
            "queued": True,
        }

    if is_online_mode():
        try:
            from core.server_crud import get_doc

            doc = get_doc("sales", sid) or {}
        except Exception as exc:
            print(f"[autosave] online sale lookup failed: {exc}")
            return UNKNOWN
        if not doc:
            # get_doc returns None for a missing row AND for a network failure.
            # Unreadable is not deleted.
            return UNKNOWN
        if doc.get("deleted"):
            return None
        return {
            "bill_no": str(doc.get("bill_no") or ""),
            "cash_paid": _f(doc.get("cash_paid")),
            "online_paid": _f(doc.get("online_paid")),
            "total_amount": doc.get("total_amount"),
            "discount": _f(doc.get("discount")),
            "rounding": _f(doc.get("rounding")),
            "doc": doc,
        }

    row = conn.execute(
        "SELECT COALESCE(bill_no,''), COALESCE(cash_paid,0), COALESCE(online_paid,0), "
        "total_amount, COALESCE(discount,0), COALESCE(rounding,0) "
        "FROM sales WHERE id=? AND COALESCE(deleted,0)=0",
        (sid,),
    ).fetchone()
    if not row:
        return None
    return {
        "bill_no": str(row[0] or ""),
        "cash_paid": _f(row[1]),
        "online_paid": _f(row[2]),
        "total_amount": row[3],
        "discount": _f(row[4]),
        "rounding": _f(row[5]),
        "doc": None,
    }


def _bill_money(snap: dict, lines: list[dict]) -> tuple[float, float]:
    """(total, discount) the bill holds now. An old queued create may carry no total."""
    total = snap.get("total_amount")
    if total is None:
        from core.calc_engine import calc_bill_summary

        total = calc_bill_summary(
            lines, 0, _f(snap.get("rounding")), discount_rs=_f(snap.get("discount"))
        )["total_amount"]
    return _f(total), _f(snap.get("discount"))


def _form_money(rec: dict | None) -> tuple[float, float]:
    """(total, discount) this form's last write put into the bill.

    Recorded on every write. A record from a build before that is worked out from the lines
    and figures it does keep.
    """
    if not rec:
        return 0.0, 0.0
    if rec.get("contrib_total") is not None:
        return _f(rec.get("contrib_total")), _f(rec.get("contrib_discount"))
    from core.calc_engine import calc_bill_summary

    lines = [
        {"amount": round(_f(m.get("qty")) * _f(m.get("rate")) - _f(m.get("medicine_discount")), 2)}
        for m in (rec.get("items") or [])
        if isinstance(m, dict)
    ]
    if not lines:
        return 0.0, 0.0
    form = calc_bill_summary(lines, _f(rec.get("discount_pct")), _f(rec.get("rounding")))
    return _f(form["total_amount"]), _f(form["discount_amount"])


def _stored_base_money(rec: dict | None, base_lines: list[dict]) -> tuple[float, float]:
    """(total, discount) of the bill without this form, as recorded at its last write."""
    if rec and rec.get("base_total") is not None:
        return _f(rec.get("base_total")), _f(rec.get("base_discount"))
    from core.calc_engine import calc_bill_summary

    return _f(calc_bill_summary(base_lines or [], 0, 0)["total_amount"]), 0.0


def _queued_bill_row_of(sale_id) -> dict | None:
    """The bill row (waiting or refused) that a bill id stands for, else None.

    Only a bill that exists nowhere but the queue (a negative id) can be one. A queue that
    cannot be read answers None here; the snapshot read that follows then says UNKNOWN,
    and that refuses the tick too.
    """
    try:
        sid = int(sale_id or 0)
    except (TypeError, ValueError):
        return None
    if sid >= 0:
        return None
    try:
        from core.online_mutation_queue import queued_bill_row

        return queued_bill_row(sid)
    except Exception as exc:
        print(f"[autosave] queue lookup failed: {exc}")
        return None


def _bill_date_holds_back(conn, medicines, date_s: str, wait: bool) -> list[str]:
    """Why this form may not be written on its Bill Date yet, or [] when it may.

    ``wait=False`` is for a Tk thread: with no answer for that date yet, the question is
    started behind the tick and nothing is written until the answer is in. A check that fails
    outright hides nothing, as at save.
    """
    try:
        from core import sale_availability as sa

        if not wait and not sa.answer_ready(date_s):
            sa.batches_missing_on_online(date_s, wait=False)
            return [
                f"Checking which batches the shop had on {date_s}. This form is kept and is "
                "saved once that is known."
            ]
        return sa.lines_unavailable_on(conn, medicines, date_s, wait=wait)
    except Exception as exc:
        print(f"[autosave] bill date check failed: {exc}")
        return []


def _keep_the_form_only(
    rec, token, problems, *, counter, customer_id, date_s, form
) -> dict[str, Any]:
    """Keep the form of a tick that may not write, and write no bill.

    A form that already owns a bill only refreshes its snapshot; the bill keeps its last
    write. A form that never wrote is kept as a held record, which a restart offers back.
    """
    tok = str((rec or {}).get("token") or token or "").strip() or _sess.new_token()
    sale_id = int((rec or {}).get("sale_id") or 0)
    try:
        if sale_id:
            _sess.save_session(tok, form=form())
        else:
            _sess.save_session(
                tok,
                sale_id=0,
                held=True,
                bill_no="",
                bill_date=date_s,
                counter=bool(counter),
                customer_id=int(customer_id or 0),
                closed=False,
                form=form(),
            )
    except Exception as exc:
        print(f"[autosave] form snapshot of a held tick: {exc}")
    return {
        "ok": True,
        "held": True,
        "problems": list(problems),
        "sale_id": sale_id,
        "bill_no": str((rec or {}).get("bill_no") or ""),
        "token": tok,
        "counter": bool(counter),
        "created": False,
    }


def _row_refused(row) -> bool:
    return str((row or {}).get("status") or "") != "pending"


def _refused_on_the_prompt(row) -> str:
    """What the recovery prompt says about a bill whose row the server refused."""
    reason = str((row or {}).get("last_error") or "").strip() or "The server refused this bill."
    return (
        f"{reason} It cannot be reopened or taken back from here: retry or discard it in "
        "Settings → Sync."
    )


def _write_nothing_to_a_waiting_bill(
    rec, row, *, token, final, medicines, discount_pct, rounding, cash_paid, online_paid, form
) -> dict[str, Any]:
    """Answer a tick or F7 for a form whose bill row has not landed; the bill is not written.

    The form snapshot is still kept, so nothing typed is lost. A refused row says why. A row
    that is only waiting stays quiet while the form is unchanged, and says so once it is not;
    F7 on an unchanged form closes the record, as a finished save does.
    """
    tok = str(rec.get("token") or token or "")
    try:
        _sess.save_session(tok, form=form())
    except Exception as exc:
        print(f"[autosave] form snapshot while the bill row waits: {exc}")
    if _row_refused(row):
        reason = str(row.get("last_error") or "").strip() or "The server refused this bill."
        raise AutosaveBillRefused(f"{reason} Nothing more was written to it from this form.")
    unchanged = _sess.snapshot_lines(medicines) == list(rec.get("items") or []) and all(
        abs(_f(now) - _f(rec.get(key))) < 0.005
        for now, key in (
            (cash_paid, "cash_paid"),
            (online_paid, "online_paid"),
            (discount_pct, "discount_pct"),
            (rounding, "rounding"),
        )
    )
    if not unchanged:
        raise AutosaveBillWaiting(
            "This bill has not reached the server yet. It is sent on its own once the "
            "connection is back; nothing more was written to it, and the changes stay on "
            "this form until it lands."
        )
    if final:
        _sess.save_session(tok, closed=True)
    return {
        "ok": True,
        "sale_id": int(rec.get("sale_id") or 0),
        "bill_no": str(rec.get("bill_no") or "PENDING"),
        "token": tok,
        "counter": bool(rec.get("counter")),
        "created": False,
        "closed": bool(final),
        "waiting": True,
    }


def _subtract_lines(current: list[dict], mine: list[dict]) -> tuple[list[dict], bool]:
    """`current` minus `mine`, quantity-wise.

    Returns (lines, short). `short` is True when `current` did not actually
    contain everything `mine` claims to have put there -- Online, that means
    the last tick's write is still sitting in the mutation queue and the
    document we just read is one revision behind. The caller then uses the
    base it stored last time instead of subtracting from a stale read, which
    is what stops a queued day bill from losing the other sales in it.
    """
    mine_qty: dict[int, float] = {}
    mine_disc: dict[int, float] = {}
    for med in mine or []:
        try:
            mid = int(med.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        mine_qty[mid] = mine_qty.get(mid, 0.0) + _f(med.get("qty"))
        mine_disc[mid] = mine_disc.get(mid, 0.0) + _f(med.get("medicine_discount"))

    out: list[dict] = []
    for med in current or []:
        line = dict(med)
        try:
            mid = int(line.get("id") or 0)
        except (TypeError, ValueError):
            mid = 0
        take = mine_qty.get(mid, 0.0)
        if mid > 0 and take:
            qty = round(_f(line.get("qty")) - take, 4)
            if qty <= 0:
                mine_qty[mid] = round(take - _f(line.get("qty")), 4)
                mine_disc[mid] = max(
                    0.0, mine_disc.get(mid, 0.0) - _f(line.get("medicine_discount"))
                )
                continue
            disc = max(
                0.0, round(_f(line.get("medicine_discount")) - mine_disc.get(mid, 0.0), 2)
            )
            base = qty * _f(line.get("rate"))
            line["qty"] = qty
            line["medicine_discount"] = disc
            line["amount"] = round(base - min(disc, base), 2)
            mine_qty[mid] = 0.0
            mine_disc[mid] = 0.0
        out.append(line)
    short = any(round(v, 4) > 0 for v in mine_qty.values())
    return out, short


def _counter_target(conn, customer_id, date_s: str) -> int:
    """Today's counter bill id -- this device's pointer first, then the store.

    The pointer is what a second tab, a restart, or an Online create that is
    still queued (the server cannot answer for it yet) all resolve through. It
    is only trusted while the bill it names is still live.
    """
    pointer = _sess.get_counter_pointer(date_s)
    if pointer:
        snap = _sale_snapshot(conn, pointer)
        if snap is UNKNOWN:
            # The store could not answer. Keep the pointer -- it is this
            # device's own record of today's bill -- and hand it back so the
            # caller refuses the tick instead of opening a second day bill.
            return pointer
        if snap:
            return pointer
        _sess.clear_counter_pointer(date_s, pointer)

    from core.billing_service import find_todays_counter_sale_id

    try:
        found = find_todays_counter_sale_id(conn, customer_id, date_s)
    except Exception as exc:
        print(f"[autosave] counter bill lookup failed: {exc}")
        found = None
    return int(found or 0)


def _delete_bill(conn, sale_id) -> None:
    from core.desktop_sales_service import delete_saved_sale

    delete_saved_sale(conn, {"sale_id": int(sale_id)})


def release_session(conn, rec: dict) -> None:
    """Take this form's lines back out of the bill it was writing into.

    Counter: subtract only this form's share -- the rest of the day's sales
    stay. If nothing is left the day bill itself goes.
    Normal: the whole bill was this form's, so the bill goes.
    """
    if not rec:
        return
    sale_id = int(rec.get("sale_id") or 0)
    if not sale_id:
        return
    snap = _sale_snapshot(conn, sale_id)
    if snap is UNKNOWN:
        raise AutosaveTargetUnavailable(
            "Could not reach the saved bill to take these lines back out. "
            "Check the server connection and try again."
        )
    if not snap:
        return

    if not rec.get("counter"):
        _delete_bill(conn, sale_id)
        return

    from core.billing_service import counter_merge_money, load_sale_medicines, update_existing_bill
    from core.customer_service import COUNTER_SALE

    date_s = _sess.normalize_date(rec.get("bill_date"))
    current = load_sale_medicines(conn, sale_id, doc=snap.get("doc"))
    remaining, short = _subtract_lines(current, rec.get("items") or [])
    if short:
        remaining = list(rec.get("base_items") or [])
    if not remaining:
        _delete_bill(conn, sale_id)
        _sess.clear_counter_pointer(date_s, sale_id)
        return
    # What the bill held without this form: its total and discount less this form's own. The
    # remaining lines used to be written with THIS form's rounding and discount, so the day
    # bill kept a few paise the earlier sales never paid.
    if short:
        left_total, left_discount = _stored_base_money(rec, remaining)
    else:
        held_total, held_discount = _bill_money(snap, current)
        mine_total, mine_discount = _form_money(rec)
        left_total = round(held_total - mine_total, 2)
        left_discount = max(0.0, round(held_discount - mine_discount, 2))
    left_discount, left_rounding = counter_merge_money(
        left_total, left_discount, remaining, [], 0, 0, 0.0
    )
    update_existing_bill(
        conn,
        sale_id,
        remaining,
        existing_doc=snap.get("doc"),
        discount_pct=0.0,
        rounding=left_rounding,
        cash_paid=(
            _f(rec.get("base_cash"))
            if short
            else max(0.0, snap["cash_paid"] - _f(rec.get("cash_paid")))
        ),
        online_paid=(
            _f(rec.get("base_online"))
            if short
            else max(0.0, snap["online_paid"] - _f(rec.get("online_paid")))
        ),
        customer_name=COUNTER_SALE,
        customer_phone="",
        doctor_name="",
        previous_due=0.0,
        discount_rs=left_discount,
        bill_date=date_s,
    )


def write_autosave_bill(
    conn,
    *,
    token: str = "",
    sale_id: int = 0,
    customer_id: int,
    customer_name: str,
    customer_phone: str = "",
    customer_address: str = "",
    medicines: list[dict],
    discount_pct: float = 0.0,
    discount_rs: float | None = None,
    rounding: float = 0.0,
    cash_paid: float = 0.0,
    online_paid: float = 0.0,
    doctor_name: str = "",
    doctor_phone: str = "",
    previous_due: float = 0.0,
    bill_date=None,
    final: bool = False,
    availability_wait: bool = True,
    bill_date_problems: list[str] | None = None,
) -> dict[str, Any]:
    """Create-or-update the one real bill this form owns.

    `final` is the F7/F8 save: same write, then the session is closed so no
    later tick can touch the bill again.
    `bill_date_problems` is the Bill Date check already asked by a caller that must not wait
    on the store while it holds a lock; None asks it here.
    Returns {"ok", "sale_id", "bill_no", "token", "counter", "created"}.
    """
    from core.billing_service import (
        _merge_medicine_lines,
        load_sale_medicines,
        save_new_bill,
        update_existing_bill,
    )
    from core.customer_service import COUNTER_SALE

    date_s = _sess.normalize_date(bill_date)
    counter = _is_counter_form(customer_name, medicines, doctor_name)

    # A stale token (its record dropped by a switch whose create then failed)
    # owns nothing here: it must NOT be matched to another tab's record on the
    # same counter bill by id -- that released the other tab's paid lines.
    def _form_now() -> dict:
        # The whole form as a reopened tab needs it back. Built only where a record of the
        # bill already exists: it must never stand between a create and that record.
        return _sess.snapshot_form(
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_address=customer_address,
            doctor_name=doctor_name,
            doctor_phone=doctor_phone,
            bill_date=date_s,
            discount_pct=discount_pct,
            discount_rs=discount_rs if discount_rs is not None else 0.0,
            rounding=rounding,
            cash_paid=cash_paid,
            online_paid=online_paid,
            previous_due=previous_due,
            medicines=medicines,
        )

    rec = _sess.own_session(token, sale_id)
    discarded = rec if rec is not None and rec.get("discarded") else None
    if rec is None:
        discarded = _sess.discarded_record_for(sale_id)
    if discarded is not None:
        # The shop discarded this form's refused bill row in Settings → Sync, and the form is
        # still open. Its balance and stock stay on the server; the row will never exist.
        # With no record left, this tick or F7 saved the sale as a new bill and posted both a
        # second time. Write nothing. Touching the record keeps it while the form still ticks.
        try:
            _sess.save_session(str(discarded.get("token") or ""))
        except Exception as exc:
            print(f"[autosave] discarded bill record: {exc}")
        raise AutosaveBillDiscarded(_discarded_message())
    if rec is not None and _sale_snapshot(conn, rec.get("sale_id")) is None:
        # The bill it named was deleted elsewhere -- start clean rather than
        # resurrect a number. `is None` on purpose: UNKNOWN means the store
        # could not be asked, and dropping the session on that opened a second
        # real bill (with a second number, a second stock movement and a second
        # charge) every time a packet went missing between two ticks.
        _sess.drop_session(rec.get("token"))
        rec = None
    if rec is not None and rec.get("closed") and not final:
        # The bill was saved (F7/F8) and this is a timer tick that was already
        # in flight. Touching the bill now would either re-edit a printed bill
        # or, worse, open a second one. Report the bill and write nothing.
        return {
            "ok": True,
            "sale_id": int(rec.get("sale_id") or 0),
            "bill_no": rec.get("bill_no") or "",
            "token": str(rec.get("token") or token or ""),
            "counter": bool(rec.get("counter")),
            "created": False,
            "closed": True,
        }

    if rec is not None:
        waiting = _queued_bill_row_of(rec.get("sale_id"))
        if waiting is not None:
            # This form's bill row has not landed: waiting to be sent, or refused by a rule.
            # Its balance and stock are on the server and only the row may go. Only a
            # refused row used to stop the tick. Once the shop's Retry put the row back to
            # waiting, the tick queued an edit of the sale behind it; when the row was
            # refused again and discarded, that edit ran against nothing, took the stock off
            # a second time and made another refused bill.
            return _write_nothing_to_a_waiting_bill(
                rec,
                waiting,
                token=token,
                final=final,
                medicines=medicines,
                discount_pct=discount_pct,
                rounding=rounding,
                cash_paid=cash_paid,
                online_paid=online_paid,
                form=_form_now,
            )

    if not final:
        held_back = (
            list(bill_date_problems)
            if bill_date_problems is not None
            else _bill_date_holds_back(conn, medicines, date_s, availability_wait)
        )
        if held_back:
            # A back-dated form holding a batch that had not come in by its Bill Date, or had
            # expired by then. F7 refuses it, and the tick used to write it anyway: a real
            # bill with that batch, the stock off the shelf, all left behind when F7 said no.
            # Write nothing -- the last write stays, checked for its own date and paid for its
            # own lines -- and keep the form so nothing typed is lost. Writing only the allowed
            # lines would turn the payment typed for the whole form into customer credit.
            return _keep_the_form_only(
                rec,
                token,
                held_back,
                counter=counter,
                customer_id=customer_id,
                date_s=date_s,
                form=_form_now,
            )

    if rec is not None and (
        bool(rec.get("counter")) != counter
        or (counter and _sess.normalize_date(rec.get("bill_date")) != date_s)
    ):
        # The form stopped being a counter sale (or crossed midnight). Take our
        # lines back out of the old bill before attaching to the new target --
        # otherwise they are billed twice.
        try:
            release_session(conn, rec)
        except AutosaveTargetUnavailable:
            # Could not take the lines out of the old bill. Attaching to the new
            # target now would bill them in both, so do nothing and let the next
            # tick try again.
            raise
        except Exception as exc:
            print(f"[autosave] could not release previous target: {exc}")
        _sess.drop_session(rec.get("token"))
        rec = None

    token = str(token or "").strip() or (rec or {}).get("token") or _sess.new_token()

    target = int((rec or {}).get("sale_id") or 0)
    if counter and not target:
        target = _counter_target(conn, customer_id, date_s)
        day_row = _queued_bill_row_of(target)
        if day_row is not None:
            # Today's counter bill is itself a bill row that has not landed. Adding this
            # form's lines would queue an edit of it -- the same second post as above.
            if _row_refused(day_row):
                raise AutosaveBillRefused(_refused_on_the_prompt(day_row))
            raise AutosaveBillWaiting(
                "Today's counter bill has not reached the server yet. Nothing was written "
                "to it; this sale is kept on the form and is saved once that bill lands."
            )

    snap = _sale_snapshot(conn, target) if target else None
    if snap is UNKNOWN and counter and target < 0:
        # A queued create that has since been pushed: the day bill is real on
        # the server now, under an id this device never saw. Find today's bill
        # again rather than open a second one.
        _sess.clear_counter_pointer(date_s, target)
        found = _counter_target(conn, customer_id, date_s)
        found_snap = _sale_snapshot(conn, found) if found else None
        if found and found_snap and found_snap is not UNKNOWN:
            target, snap = found, found_snap
            rec = dict(rec or {})
            rec["sale_id"] = target
    if snap is UNKNOWN:
        # Never turn "I could not read the bill" into a second bill.
        raise AutosaveTargetUnavailable(
            "Could not read the bill this form is writing into. Nothing was "
            "changed -- check the server connection; autosave retries on its own."
        )
    if not snap:
        target = 0

    created = False
    base: list[dict] = []
    base_cash = 0.0
    base_online = 0.0
    base_total = 0.0
    base_discount = 0.0
    # What this form itself comes to; recorded so a later tick can take exactly it back out.
    from core.calc_engine import calc_bill_summary

    _mine_now = calc_bill_summary(medicines or [], discount_pct, rounding, discount_rs=discount_rs)
    form_total = _f(_mine_now["total_amount"])
    form_discount = _f(_mine_now["discount_amount"])
    if target:
        mine = (rec or {}).get("items") or []
        mine_cash = _f((rec or {}).get("cash_paid"))
        mine_online = _f((rec or {}).get("online_paid"))
        rounding_out = rounding
        discount_rs_out = discount_rs
        if counter:
            from core.billing_service import counter_merge_money

            current = load_sale_medicines(conn, target, doc=snap.get("doc"))
            base, short = _subtract_lines(current, mine)
            if short and rec is not None:
                base = list(rec.get("base_items") or [])
                base_cash = _f(rec.get("base_cash"))
                base_online = _f(rec.get("base_online"))
                base_total, base_discount = _stored_base_money(rec, base)
            else:
                base_cash = max(0.0, snap["cash_paid"] - mine_cash)
                base_online = max(0.0, snap["online_paid"] - mine_online)
                held_total, held_discount = _bill_money(snap, current)
                mine_total, mine_discount = _form_money(rec)
                base_total = round(held_total - mine_total, 2)
                base_discount = max(0.0, round(held_discount - mine_discount, 2))
            lines = _merge_medicine_lines(base, medicines)
            cash_out = base_cash + _f(cash_paid)
            online_out = base_online + _f(online_paid)
            name_out, phone_out, doc_out = COUNTER_SALE, "", ""
            # The day bill is what the other sales put there plus this form's own total, so
            # the cash each form paid adds up to it. Written with this form's rounding and
            # discount over all the day's lines, it was a few paise off its cash every merge.
            discount_rs_out, rounding_out = counter_merge_money(
                base_total, base_discount, lines, medicines, discount_pct, rounding, discount_rs
            )
        else:
            lines = list(medicines or [])
            cash_out = _f(cash_paid)
            online_out = _f(online_paid)
            name_out, phone_out, doc_out = customer_name, customer_phone, doctor_name
        update_existing_bill(
            conn,
            target,
            lines,
            existing_doc=snap.get("doc"),
            discount_pct=discount_pct,
            rounding=rounding_out,
            cash_paid=cash_out,
            online_paid=online_out,
            customer_name=name_out,
            customer_phone=phone_out,
            doctor_name=doc_out,
            previous_due=previous_due,
            discount_rs=discount_rs_out,
            bill_date=date_s,
        )
        bill_no = snap["bill_no"]
        refreshed = _sale_snapshot(conn, target)
        if refreshed and refreshed is not UNKNOWN and refreshed.get("bill_no"):
            bill_no = refreshed["bill_no"]
        # save_new_bill hands back the DISPLAY number ("SCB1"); the row stores
        # "SCB1/FY2026-27". Without this the tab title and the note flip
        # between the two forms on every tick and look like two bills.
        try:
            from core.fy_serial import display_sales_bill_no

            bill_no = display_sales_bill_no(bill_no)
        except Exception:
            pass
    else:
        # sync=True: the bill number must be real from the first tick, so the
        # counter never sees "PENDING" and a later tick has an id to update.
        try:
            bill_no, target = save_new_bill(
                conn,
                customer_id,
                list(medicines or []),
                discount_pct,
                rounding,
                cash_paid,
                online_paid,
                doctor_name,
                doctor_phone,
                previous_due,
                discount_rs=discount_rs,
                bill_date=date_s,
                customer_name=customer_name,
                customer_phone=customer_phone,
                sync=True,
            )
        except Exception as exc:
            from core.server_crud import SaleRowNotSaved

            if isinstance(exc, SaleRowNotSaved) and exc.local_id:
                # The customer's balance and the stock committed and the server refused
                # the bill row, now parked in the queue under this id. Pin the form to it
                # before the refusal goes up. With no record the next tick or F7 saved
                # the sale again: balance and stock once more, one more refused row each.
                _sess.save_session(
                    token,
                    sale_id=exc.local_id,
                    bill_no="REFUSED",
                    bill_date=date_s,
                    counter=bool(counter),
                    customer_id=int(customer_id or 0),
                    items=_sess.snapshot_lines(medicines),
                    cash_paid=_f(cash_paid),
                    online_paid=_f(online_paid),
                    discount_pct=_f(discount_pct),
                    rounding=_f(rounding),
                    base_items=[],
                    base_cash=0.0,
                    base_online=0.0,
                    closed=False,
                )
                try:
                    # After the pin: what recovery shows (the customer, the total) can
                    # never cost the form its record of the bill.
                    _sess.save_session(token, form=_form_now())
                except Exception as snap_exc:
                    print(f"[autosave] refused bill form snapshot: {snap_exc}")
            raise
        target = int(target or 0)
        created = True
        if target:
            # Pin the bill to this form the INSTANT it exists, before anything
            # else can throw. A created bill with no session pointing at it is
            # invisible: recovery cannot offer it, History reads it as a
            # completed sale, and the next tick -- finding no session -- creates
            # another real bill, and the one after that another. Everything
            # below here (the counter pointer, the queue flush, the form
            # snapshot) can fail; none of it may cost us the bill.
            _sess.save_session(
                token,
                sale_id=target,
                bill_no=bill_no,
                bill_date=date_s,
                counter=bool(counter),
                customer_id=int(customer_id or 0),
                # `items` is not optional here: without it a second tick on a
                # counter bill would subtract nothing and merge this form's
                # lines into a bill that already holds them -- billed twice.
                items=_sess.snapshot_lines(medicines),
                cash_paid=_f(cash_paid),
                online_paid=_f(online_paid),
                discount_pct=_f(discount_pct),
                rounding=_f(rounding),
                base_items=[],
                base_cash=0.0,
                base_online=0.0,
                contrib_total=form_total,
                contrib_discount=form_discount,
                base_total=0.0,
                base_discount=0.0,
                closed=False,
            )

    if counter and target:
        _sess.set_counter_pointer(date_s, target)

    if final and not created:
        # The F7/F8 save. Online, that last write is an UPDATE, and an update
        # only goes into the mutation queue -- while the printer reads the
        # server document straight (core/bill_output.py:209, no queue overlay).
        # Print it before the queue drains and the slip is the PREVIOUS tick's
        # bill: the last line the counter typed is on the customer's copy but
        # not on the server's, or the other way round. A create does not need
        # this -- save_new_bill(sync=True) already wrote to the server itself.
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_mutation_queue import flush_now

                flush_now(wait_sec=10.0)
        except Exception as exc:
            print(f"[autosave] final save flush: {exc}")

    # A finished bill leaves a tombstone rather than nothing. A timer tick that
    # was already in flight when F7 landed would otherwise find no session, and
    # happily open a SECOND bill for the same form.
    _sess.save_session(
        token,
        sale_id=int(target),
        bill_no=bill_no,
        bill_date=date_s,
        counter=bool(counter),
        customer_id=int(customer_id or 0),
        items=_sess.snapshot_lines(medicines),
        cash_paid=_f(cash_paid),
        online_paid=_f(online_paid),
        discount_pct=_f(discount_pct),
        rounding=_f(rounding),
        base_items=base,
        base_cash=base_cash,
        base_online=base_online,
        # What this form and the rest of the bill came to, so the next tick or a discard
        # takes back exactly this form's share of the total and discount.
        contrib_total=form_total,
        contrib_discount=form_discount,
        base_total=base_total,
        base_discount=base_discount,
        closed=bool(final),
        # What a reopened tab is handed back. Without it the durable record can
        # only say THAT a bill is in progress, not what was on the screen -- and
        # the operator retypes the sale next to the one already saved.
        form=_form_now(),
    )

    return {
        "ok": True,
        "sale_id": int(target),
        "bill_no": bill_no,
        "token": token,
        "counter": bool(counter),
        "created": created,
    }


def _form_total(form: dict) -> float:
    total = 0.0
    for med in (form or {}).get("medicines") or []:
        if not isinstance(med, dict):
            continue
        amount = med.get("amount")
        if amount in (None, ""):
            amount = _f(med.get("qty")) * _f(med.get("rate")) - _f(
                med.get("medicine_discount")
            )
        total += _f(amount)
    return round(max(0.0, total), 2)


def list_recoverable() -> list[dict[str, Any]]:
    """The in-progress bills a reopened form can claim back.

    Read straight off the durable record -- no store call. A restart must be
    able to answer "is there money already on the books from a form nobody
    finished?" instantly and with the server down, which is exactly the state
    the app is in after the power cut that caused the question.
    """
    today = _sess.normalize_date(None)
    out: list[dict[str, Any]] = []
    for rec in _sess.list_open_sessions():
        form = rec.get("form") or {}
        meds = form.get("medicines") or rec.get("items") or []
        bill_date = _sess.normalize_date(rec.get("bill_date"))
        out.append(
            {
                "token": str(rec.get("token") or ""),
                "sale_id": int(rec.get("sale_id") or 0),
                "bill_no": str(rec.get("bill_no") or ""),
                "bill_date": bill_date,
                "counter": bool(rec.get("counter")),
                "customer_name": str(form.get("customer_name") or ""),
                "items": len(meds),
                "total": _form_total(form),
                "updated_at": float(rec.get("updated_at") or 0),
                # An unfinished bill from an earlier day. It is still claimable
                # -- nothing here is ever deleted behind the operator's back --
                # but it is shown first and said plainly.
                "stale": bool(bill_date < today),
                "restorable": bool(form.get("medicines")),
                # Only a form was kept: its Bill Date refused a line, so no bill was written.
                "held": bool(rec.get("held")) and not int(rec.get("sale_id") or 0),
            }
        )
    return out


def resume_autosave_bill(conn, *, token: str = "", sale_id: int = 0) -> dict[str, Any]:
    """Hand an in-progress bill back to a form that reopened.

    Nothing is written here. The form comes back holding the SAME token, so the
    next tick is an update of the bill that already exists -- and for a counter
    sale the token is what lets the tick subtract this form's own lines out of
    the day bill before putting the current ones in. Claiming without the token
    is what bills the day twice.
    """
    rec = _sess.own_session(token, sale_id)
    if rec is not None and rec.get("discarded"):
        return {"ok": False, "code": "discarded", "error": _discarded_message()}
    if rec is None:
        return {"ok": False, "code": "gone", "error": "That sale is no longer in progress."}
    if rec.get("closed"):
        return {
            "ok": False,
            "code": "already_saved",
            "error": "That bill was already saved.",
            "sale_id": int(rec.get("sale_id") or 0),
        }

    if rec.get("held") and not int(rec.get("sale_id") or 0):
        # A form held back from writing: there is no bill, only what was typed. Hand it back.
        held_form = dict(rec.get("form") or {})
        if not held_form.get("medicines"):
            _sess.drop_session(rec.get("token") or token)
            return {"ok": False, "code": "gone", "error": "Nothing of that sale was kept."}
        return {
            "ok": True,
            "token": str(rec.get("token") or token or ""),
            "sale_id": 0,
            "bill_no": "",
            "counter": bool(rec.get("counter")),
            "bill_date": _sess.normalize_date(rec.get("bill_date")),
            "form": held_form,
        }

    waiting = _queued_bill_row_of(rec.get("sale_id"))
    if waiting is not None and _row_refused(waiting):
        # Not a connection problem, and reopening it here would only write around a row
        # the shop still has to retry or discard. Say what it is and where to act.
        return {"ok": False, "code": "refused", "error": _refused_on_the_prompt(waiting)}

    snap = _sale_snapshot(conn, rec.get("sale_id"))
    if snap is UNKNOWN:
        # Same rule as every other read here: unreadable is not deleted. Say so
        # and keep the record, rather than let the operator conclude the bill
        # never happened and type it again.
        return {
            "ok": False,
            "code": "unavailable",
            "error": (
                "Could not reach the saved bill to reopen it. Check the server "
                "connection and try again -- the sale is still recorded."
            ),
        }
    if not snap:
        # Positively gone (deleted from History). Nothing to reclaim, and the
        # record must go or it is offered again on every restart.
        _sess.drop_session(rec.get("token") or token)
        return {"ok": False, "code": "gone", "error": "That bill was deleted."}

    form = dict(rec.get("form") or {})
    if not form.get("medicines"):
        # Written by a build from before the form snapshot existed. A normal
        # bill is entirely this form's, so it can be read back off the bill; a
        # counter bill is the whole day's and cannot.
        if rec.get("counter"):
            return {
                "ok": False,
                "code": "no_snapshot",
                "error": (
                    "This counter sale was started by an older version and "
                    "cannot be reopened. Its lines are in today's counter bill."
                ),
                "sale_id": int(rec.get("sale_id") or 0),
            }
        from core.billing_service import load_sale_medicines

        form.setdefault("customer_name", "")
        form["medicines"] = load_sale_medicines(
            conn, int(rec.get("sale_id") or 0), doc=snap.get("doc")
        )

    bill_no = snap.get("bill_no") or rec.get("bill_no") or ""
    try:
        from core.fy_serial import display_sales_bill_no

        bill_no = display_sales_bill_no(bill_no)
    except Exception:
        pass

    return {
        "ok": True,
        "token": str(rec.get("token") or token or ""),
        "sale_id": int(rec.get("sale_id") or 0),
        "bill_no": bill_no,
        "counter": bool(rec.get("counter")),
        "bill_date": _sess.normalize_date(rec.get("bill_date")),
        "form": form,
    }


def discard_autosave_bill(conn, *, token: str = "", sale_id: int = 0) -> dict[str, Any]:
    """Abandon an in-progress autosave bill.

    Counter: only this form's lines come back out; the day's other sales are
    untouched. Normal: the bill is removed through the same audited path as
    History's delete, so stock and the customer's balance are restored.
    Anything else (an old ASV draft from before this change) still goes through
    the legacy draft delete.
    """
    rec = _sess.own_session(token, sale_id)
    discarded = rec if rec is not None and rec.get("discarded") else None
    if rec is None:
        discarded = _sess.discarded_record_for(sale_id)
    if discarded is not None:
        # Its refused bill row was discarded in Settings → Sync: there is no bill here to take
        # back, and the balance and stock it took are not reversed from a form. Clearing the
        # form is what lets its record go.
        _sess.drop_session(discarded.get("token") or token)
        return {
            "ok": True,
            "deleted": False,
            "reason": "discarded",
            "sale_id": int(discarded.get("sale_id") or 0),
        }
    if rec is None:
        # delete_autosave_bill only ever removes an is_autosave=1 ASV draft; a
        # real bill id (a stale token's day bill) is refused there.
        from core.billing_service import delete_autosave_bill

        try:
            deleted = delete_autosave_bill(conn, int(sale_id)) if sale_id else False
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "deleted": bool(deleted), "legacy": True}

    if rec.get("closed"):
        # This bill was SAVED. Clearing the form afterwards must not delete it.
        return {
            "ok": True,
            "deleted": False,
            "reason": "already_saved",
            "sale_id": int(rec.get("sale_id") or 0),
        }

    waiting = _queued_bill_row_of(rec.get("sale_id"))
    if waiting is not None and _row_refused(waiting):
        # Taking it back from here would queue a delete behind a row the shop may discard;
        # the refused row itself is retried or discarded in Settings → Sync.
        return {"ok": False, "code": "refused", "error": _refused_on_the_prompt(waiting)}

    try:
        release_session(conn, rec)
    except Exception as exc:
        return {"ok": False, "error": f"Could not discard autosaved bill: {exc}"}
    _sess.drop_session(rec.get("token") or token)
    return {"ok": True, "deleted": True, "sale_id": int(rec.get("sale_id") or 0)}
