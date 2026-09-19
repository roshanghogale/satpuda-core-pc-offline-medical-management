"""An Online sale edit the store SKIPPED: put it over the store's copy, or say why not.

The twin of purchase_service.land_skipped_purchase_edit, for sales.

The store takes a sale only past the version it holds (at the same version, only with a strictly
newer updated_at: upsertHelper.shouldAcceptIncoming), and a customer receipt's cascade
(partyDueCascade.cascadeCustomerAfterLedgerChange) writes every bill whose due it changes at
version + 1 with updated_at NOW(). An edit read before a receipt and arriving after it was skipped
whole while the stock movements in its bundle were applied: staging 14 Sep, d5race OPP0 on store 4,
sale 36478 at v5 answered sales ['skipped'], medicines ['upserted']; total 251, NIMICA PLUS still 2,
stock 0 -> 1.
"""
from __future__ import annotations

# Every sale column but what the customer cascade writes -- total_due, due_amount and the two
# cleared flags. A store copy that differs from an edit's base in none of these was moved on by a
# receipt, a return or another bill of the customer, and never edited.
_SALE_EDIT_HEADER_FIELDS = (
    "customer_id", "bill_date", "bill_no", "fy_start_year", "fy_serial", "total_amount",
    "discount", "discount_pct", "rounding", "amount_paid", "cash_paid", "online_paid",
    "previous_due", "previous_credit", "credit_amount", "paid_due", "doctor_name",
    "customer_name", "customer_phone", "customer_address", "is_autosave", "deleted",
)
_SALE_EDIT_LINE_FIELDS = (
    "medicine_id", "name", "type", "qty", "rate", "amount", "item_discount", "gst_percent",
    "batch_no", "expiry_date", "hsn_code", "schedule", "manufacturer", "cost_price",
)
# "Is this edit already what the store holds?" -- only what makes the edit: its lines, its money,
# its customer, date and doctor. The store files the number itself (a date moved into another
# year takes that year's next), and the other columns are copies a device may fill differently.
_SALE_LANDED_HEADER_FIELDS = (
    "customer_id", "bill_date", "total_amount", "discount", "discount_pct", "rounding",
    "amount_paid", "cash_paid", "online_paid", "previous_due", "previous_credit", "doctor_name",
    "is_autosave", "deleted",
)
_SALE_LANDED_LINE_FIELDS = ("medicine_id", "qty", "rate", "amount", "item_discount")
_FLAG_FIELDS = frozenset({"is_autosave", "deleted"})
_DATE_FIELDS = frozenset({"bill_date", "expiry_date"})
# How often a skipped edit is put again over a store copy that customer cascades keep moving.
_EDIT_LANDINGS = 4


def _field(key: str, value):
    """One field as two readings can agree on it: numbers to the paisa, dates to the day."""
    if key in _FLAG_FIELDS:
        return str(value).strip().lower() not in ("", "0", "0.0", "false", "f", "none")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (bool, int, float)):
        return round(float(value), 2)
    text = str(value).strip()
    if key in _DATE_FIELDS:
        return text[:10]
    try:
        return round(float(text), 2)
    except ValueError:
        return text


def sale_content(doc, header_fields=_SALE_EDIT_HEADER_FIELDS, line_fields=_SALE_EDIT_LINE_FIELDS):
    """A sale's header and lines, in a form two readings of it can be compared in."""
    doc = doc if isinstance(doc, dict) else {}
    header = tuple(_field(k, doc.get(k)) for k in header_fields)
    lines = sorted(
        (
            tuple(_field(k, it.get(k)) for k in line_fields)
            for it in doc.get("items") or []
            if isinstance(it, dict)
        ),
        key=repr,
    )
    return header, tuple(lines)


def _units_by_medicine(items) -> dict[int, float]:
    """What each medicine's lines took off the shelf: a sale line's qty is in shelf units."""
    out: dict[int, float] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            mid = int(it.get("medicine_id") or 0)
            qty = float(it.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if mid > 0:
            out[mid] = out.get(mid, 0.0) + qty
    return out


def _certainly_this_edits(base: dict, held, moves: list[dict]) -> list[dict]:
    """The movements of ``moves`` the store certainly holds for THIS edit, against its copy ``held``.

    Two edits worked out from one version write their movements under one op_uuid: the store
    applied the other device's and skipped this one. Only a medicine whose lines the other change
    left as they were certainly carries THIS edit's movement. Lines not read: nothing is certain.
    """
    if not (isinstance(held, dict) and isinstance(held.get("items"), list)):
        return []
    before = _units_by_medicine((base or {}).get("items"))
    now = _units_by_medicine(held.get("items"))
    out = []
    for m in moves:
        mid = int(m["medicine_id"])
        if mid in before and mid in now and abs(before[mid] - now[mid]) < 1e-6:
            out.append(m)
    return out


def _refuse_skipped_sale_edit(doc: dict, base: dict, held, moves: list[dict], why: str, detail: str = ""):
    """Raise SaleEditNotSaved, after putting back the stock this edit certainly moved."""
    from core.fy_serial import display_sales_bill_no
    from core.purchase_service import _edit_medicine_names, _names_of, _put_back_edit_moves
    from core.server_crud import SaleEditNotSaved

    sid = int(doc.get("id") or doc.get("local_id") or 0)
    number = display_sales_bill_no(str(doc.get("bill_no") or (base or {}).get("bill_no") or "")) or str(sid)
    names = _edit_medicine_names(doc, base)

    if why in ("unread", "unanswered"):
        # Whether the edit is on the store cannot be told: nothing is taken back on a guess.
        certain: list[dict] = []
    elif why in ("busy", "refused") or (why == "deleted" and not isinstance(held, dict)):
        # Only customer cascades moved the bill on (or the store refused the push over a copy that
        # was its base in all else), or it is gone: no other edit can have spent these op_uuids.
        certain = list(moves)
    else:
        certain = _certainly_this_edits(base, held, moves)
    unsure = [m for m in moves if m not in certain]
    failed = _put_back_edit_moves(certain)

    if why == "deleted":
        text = (f"Bill {number} was deleted on another device while this edit was being saved, "
                "so the edit was not saved.")
    elif why == "changed":
        text = (f"Bill {number} was changed on another device while this edit was being saved, "
                "so this edit was not saved over that change. Open the bill again, check it and "
                "make this edit again.")
    elif why == "busy":
        text = (f"Bill {number} kept changing on the server (receipts or returns for this "
                "customer) while this edit was being saved, so the edit was not saved. Save it "
                "again.")
    elif why == "refused":
        text = f"Bill {number} was not saved: the server refused the edit ({detail})."
    elif why == "unanswered":
        text = (f"Bill {number} may not be saved: the server did not answer while the edit was "
                f"being saved ({detail}), and the bill read back does not show the edit. Open the "
                "bill again and check it.")
    else:
        text = (f"Bill {number} may not be saved: the server did not take the edit and the bill "
                f"could not be read back ({detail}). Open the bill again and check it.")
    if certain and not failed:
        text += " The stock this edit moved has been put back."
    elif certain:
        text += (f" The stock this edit moved could not be put back ({failed}): check the stock "
                 f"of {_names_of(certain, names)}.")
    if unsure:
        text += f" Check the stock of {_names_of(unsure, names)}."
    print(f"[SALE] edit of sale {sid} not saved ({why}): {text}")
    raise SaleEditNotSaved(text, sid)


def land_skipped_sale_edit(doc: dict, base: dict, medicines: list) -> None:
    """An Online sale edit the store skipped: put it over the store's copy, or say why not.

    ``doc`` is the sale as pushed, ``base`` the store's copy the edit was worked out from,
    ``medicines`` the bundle's medicine documents, whose stock_ops are this edit's movements.

    Every attempt starts by reading the store's copy, and that copy decides:

    * The store already holds this edit (the same change arrived from elsewhere, or a push of it
      landed without an answer): done.
    * The store's copy differs from ``base`` only in what the customer cascade writes: the edit
      goes again over that copy's version, and the server's cascade after an accepted sale sets
      the dues from the ledger. ``doc`` takes the version it landed under.
    * Anything else -- another device's edit, a delete, a store that keeps moving on, a push the
      store refuses, a store that cannot be read -- raises server_crud.SaleEditNotSaved: the edit
      is not forced over a change it never saw, and the stock it moved is put back wherever it
      certainly moved and named for a check wherever it may have.
    """
    from core import server_api as api
    from core.purchase_service import _edit_medicine_names, _edit_moves, _refusal_reason
    from core.server_crud import (
        BundleDocumentRejected,
        _device_id,
        _now,
        _token,
        push_bundle,
        pushed_status,
    )

    sid = int(doc.get("id") or doc.get("local_id") or 0)
    moves = _edit_moves(medicines, sid)
    mine = sale_content(doc, _SALE_LANDED_HEADER_FIELDS, _SALE_LANDED_LINE_FIELDS)
    before = sale_content(base)
    held = None
    unanswered = ""  # the error of the last push, when it got no answer
    for attempt in range(_EDIT_LANDINGS + 1):
        try:
            # Straight from the store: server_crud.get_doc answers None for a network error too.
            held = api.pull_doc(_token(), "sales", sid)
        except Exception as exc:
            _refuse_skipped_sale_edit(doc, base, None, moves, "unread", f"{type(exc).__name__}: {exc}")
        if isinstance(held, dict) and (
            sale_content(held, _SALE_LANDED_HEADER_FIELDS, _SALE_LANDED_LINE_FIELDS) == mine
        ):
            for key in ("version", "updated_at"):
                if held.get(key) is not None:
                    doc[key] = held[key]
            return
        if not isinstance(held, dict) or _field("deleted", held.get("deleted")):
            _refuse_skipped_sale_edit(doc, base, held, moves, "deleted")
        if sale_content(held) != before:
            _refuse_skipped_sale_edit(doc, base, held, moves, "changed")
        if attempt >= _EDIT_LANDINGS:
            break
        again = dict(doc)
        again["version"] = max(int(held.get("version") or 1), int(doc.get("version") or 1)) + 1
        again["updated_at"] = _now()
        again["device_id"] = _device_id()
        try:
            answer = push_bundle({"sales": [again]})
        except BundleDocumentRejected as exc:
            _refuse_skipped_sale_edit(
                doc, base, held, moves, "refused",
                _refusal_reason(exc.failures, _edit_medicine_names(doc, base)) or str(exc),
            )
        except Exception as exc:
            unanswered = f"{type(exc).__name__}: {exc}"
            print(f"[SALE] edit of sale {sid}: no answer to the push over the store's version "
                  f"{held.get('version')} ({unanswered}); reading the store again")
            continue
        unanswered = ""
        if pushed_status(answer, "sales", sid) != "skipped":
            print(f"[SALE] edit of sale {sid} was skipped behind the store's version "
                  f"{held.get('version')} (a customer cascade); saved over it as version "
                  f"{again['version']}")
            for key in ("version", "updated_at", "device_id"):
                doc[key] = again[key]
            return
    _refuse_skipped_sale_edit(doc, base, held, moves, "unanswered" if unanswered else "busy", unanswered)
