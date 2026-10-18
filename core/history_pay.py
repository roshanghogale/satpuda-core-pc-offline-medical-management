"""Pay a due straight from Sales / Purchase History (owner, 12 Oct 2026).

The shop clicks a bill's Due / Partial status and takes the payment there. It is saved as an
ordinary customer / supplier payment (the same save as the Payments screen, so it lands in the
ledger, the party's due and every bill's status), with the bill number as its reference.

Since 17 Oct 2026 a payment can name the bills it pays (core/bill_clearing.py, the same rule
on the server and the phone). A payment taken here pays THIS bill first -- it is saved with
the allocation [{this bill, min(amount, its due)}] -- and anything more clears the party's
oldest open bills, as every payment did before. ``to_clear`` is this bill's own due.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any


def _r2(v) -> float:
    return round(float(v or 0), 2)


_KINDS = {
    # kind: (table, party table, party id column, date column, number column, party kind)
    "sale": ("sales", "customers", "customer_id", "bill_date", "bill_no", "customer"),
    "purchase": ("purchases", "suppliers", "supplier_id", "purchase_date", "purchase_no", "supplier"),
}


def _kind(kind: str) -> tuple:
    k = str(kind or "").strip().lower()
    if k in ("sales", "sale"):
        k = "sale"
    elif k in ("purchases", "purchase"):
        k = "purchase"
    if k not in _KINDS:
        raise ValueError("kind must be sale or purchase")
    return k, _KINDS[k]


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def bill_due_info(conn: sqlite3.Connection | None, kind: str, bill_id: int) -> dict[str, Any]:
    """What is open on this bill, on the party's older bills, and what clears this bill."""
    k, (table, ptable, pcol, dcol, ncol, pkind) = _kind(kind)
    bill_id = int(bill_id or 0)
    if bill_id == 0:
        raise ValueError("No bill selected.")
    if _online() or conn is None:
        return _online_info(k, bill_id)
    row = conn.execute(
        f"SELECT id, {ncol}, {dcol}, {pcol}, COALESCE(total_due,0), COALESCE(deleted,0) "
        f"FROM {table} WHERE id=?",
        (bill_id,),
    ).fetchone()
    if not row:
        raise ValueError("Bill not found.")
    if int(row[5] or 0):
        raise ValueError("This bill is deleted.")
    party_id = int(row[3] or 0)
    party = conn.execute(
        f"SELECT name, COALESCE(total_due,0), COALESCE(total_credit,0) FROM {ptable} WHERE id=?",
        (party_id,),
    ).fetchone()
    if not party:
        raise ValueError("This bill has no customer / supplier to take a payment from.")
    # What each bill owes by the rule the dues are stored with (equal to the stored total_due
    # once the party has been recalculated, and right even when it has not).
    from core.bill_clearing import open_bills

    open_ = open_bills(conn, pkind, party_id)
    owes = {b["bill_id"]: b["owes"] for b in open_}
    bill_due = _r2(owes.get(bill_id, 0.0))
    key = (str(row[2] or "")[:10], bill_id)
    older_due = 0.0
    for b in open_:
        if (b["date"], b["bill_id"]) < key:
            older_due = _r2(older_due + b["owes"])
    return {
        "ok": True,
        "kind": k,
        "party_kind": pkind,
        "bill_id": bill_id,
        "bill_no": str(row[1] or ""),
        "bill_date": str(row[2] or "")[:10],
        "party_id": party_id,
        "party": str(party[0] or ""),
        "party_due": _r2(party[1]),
        "party_credit": _r2(party[2]),
        "bill_due": bill_due,
        "older_due": older_due,
        "to_clear": bill_due if bill_due > 0.009 else 0.0,
        "oldest_first_note": (
            "Clears this bill; any more clears the oldest bills"
            + (f" (₹{older_due:.2f} is still due on older bills)." if older_due > 0.009 else ".")
            if bill_due > 0.009
            else ""
        ),
    }


def _online_info(k: str, bill_id: int) -> dict[str, Any]:
    """Online (server-only) store: the bill and the party read from the server."""
    from core import server_crud

    table, _ptable, pcol, dcol, ncol, pkind = _KINDS[k]
    doc = server_crud.get_doc(table, str(bill_id)) or {}
    if not doc:
        raise ValueError("Bill not found on the server (check the connection).")
    party_id = int(doc.get(pcol) or 0)
    party: dict = {}
    try:
        from core import online_catalog

        if pkind == "customer":
            party = online_catalog.find_customer_by_id(party_id) or {}
        else:
            party = next(
                (s for s in online_catalog.suppliers()
                 if int(s.get("id") or s.get("local_id") or 0) == party_id),
                {},
            )
    except Exception:
        party = {}
    name = str(party.get("name") or doc.get("customer_name" if pkind == "customer" else "supplier_name") or "")
    if not name:
        raise ValueError("This bill has no customer / supplier to take a payment from.")
    bill_due = _r2(doc.get("total_due") if doc.get("total_due") is not None else doc.get("due_amount"))
    party_due = _r2(party.get("total_due"))
    return {
        "ok": True,
        "kind": k,
        "party_kind": pkind,
        "bill_id": bill_id,
        "bill_no": str(doc.get(ncol) or ""),
        "bill_date": str(doc.get(dcol) or "")[:10],
        "party_id": party_id,
        "party": name,
        "party_due": party_due,
        "party_credit": _r2(party.get("total_credit")),
        "bill_due": bill_due,
        # The older bills are not read Online; the party's total due bounds them.
        "older_due": _r2(max(0.0, party_due - bill_due)) if party_due > bill_due else 0.0,
        "to_clear": bill_due,
        # Online the payment goes to the server without a choice of bills, so it still
        # clears the oldest bill first there.
        "oldest_first_note": (
            (f"Payments clear the oldest bill first; this customer owes ₹{party_due:.2f} in all."
             if pkind == "customer"
             else f"Payments clear the oldest bill first; you owe this supplier ₹{party_due:.2f} in all.")
            if party_due > bill_due + 0.009 else ""
        ),
    }


def pay_bill(conn: sqlite3.Connection | None, data: dict[str, Any]) -> dict[str, Any]:
    """Save the payment taken on a bill's row; answer the bill's new figures."""
    from core.desktop_settings_service import save_payment

    info = bill_due_info(conn, str(data.get("kind") or ""), int(data.get("id") or 0))
    amount = _r2(data.get("amount"))
    if amount <= 0:
        raise ValueError("Enter the amount received.")
    mode = str(data.get("mode") or "Cash").strip().lower()
    online = mode == "online"
    pdate = str(data.get("date") or date.today().strftime("%Y-%m-%d"))[:10]
    reference = info["bill_no"]
    if info["party_kind"] == "customer":
        body = {
            "kind": "customer",
            "party": info["party"],
            "cash": 0.0 if online else amount,
            "online": amount if online else 0.0,
            "date": pdate,
            "reference": reference,
            "note": f"Paid from Sales History ({reference})",
        }
    else:
        body = {
            "kind": "supplier",
            "party": info["party"],
            "amount": amount,
            "mode": "Online" if online else "Cash",
            "date": pdate,
            "reference": reference,
        }
    if not _online() and conn is not None and info["bill_due"] > 0.009:
        # This bill first; anything more clears the oldest bills.
        body["allocations"] = [{
            "bill_id": info["bill_id"],
            "bill_no": info["bill_no"],
            "amount": _r2(min(amount, info["bill_due"])),
        }]
    if str(data.get("receipt_no") or "").strip():
        body["receipt_no"] = str(data.get("receipt_no"))  # the paper receipt's number
    saved = save_payment(conn, body)
    after = bill_due_info(conn, info["kind"], info["bill_id"])
    return {
        "ok": True,
        "payment_id": saved.get("payment_id") if isinstance(saved, dict) else None,
        "before": info,
        "after": after,
        "message": (
            (f"₹{amount:.2f} received from {info['party']} against {reference}. "
             if info["party_kind"] == "customer"
             else f"₹{amount:.2f} paid to {info['party']} against {reference}. ")
            + ("Bill cleared." if after["bill_due"] <= 0.009 else f"₹{after['bill_due']:.2f} still due on this bill.")
        ),
    }
