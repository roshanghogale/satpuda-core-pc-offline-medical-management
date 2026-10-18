"""Refund a return from the Payment screen (owner, 18 Oct 2026).

"In Settings -> Payment add a payment kind 'Refund a return': pick the party, list that party's
returns with credit still open, tick one or more, an amount per return, cash or online, date,
note. Saving records the money going out."

- Customer: we hand the money back. One NEGATIVE customer_payments row per ticked return
  (amount = -refund, cash/online split, reference_no = return no, note "Refund SRx"), exactly
  the row a sales return settled in cash / online already writes
  (desktop_returns_service.save_sales_return), so core/return_credit.open_returns, the ledger,
  the dues and deleting the return all understand it already.
- Supplier: the agency pays us back. One NEGATIVE supplier_payments row per ticked return
  (amount = -refund, reference = return no, payment_no from the usual PAY numbering).
  recalculate_supplier_due sums payments with their sign and core/bill_clearing takes a
  negative payment as money that lowers the credit pool, as for customers.

Offline / offline-first only: Online stores answer supported=False (their returns and
payments live on the server; the server's webStore.openReturns would need the supplier half
too).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from core.bill_clearing import r2

MODES = ("cash", "online")


def _kind(kind: Any) -> str:
    return "customer" if str(kind or "").strip().lower().startswith("c") else "supplier"


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _party_id(conn: sqlite3.Connection, kind: str, party: str = "", party_id: Any = 0) -> int:
    try:
        pid = int(party_id or 0)
    except (TypeError, ValueError):
        pid = 0
    table = "customers" if kind == "customer" else "suppliers"
    if pid > 0:
        if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (pid,)).fetchone():
            return pid
        pid = 0
    name = str(party or "").strip()
    if name:
        row = conn.execute(
            f"SELECT id FROM {table} WHERE UPPER(TRIM(name))=UPPER(?) ORDER BY id LIMIT 1", (name,)
        ).fetchone()
        pid = int(row[0]) if row else 0
    return pid


def refund_row(conn: sqlite3.Connection, kind: str, payment_id: int) -> dict | None:
    """The refund a payment row is ({payment_id, party_id, amount (> 0), return_no, mode,
    date}), or None when the row is an ordinary payment."""
    k = _kind(kind)
    if k == "customer":
        row = conn.execute(
            "SELECT id, customer_id, COALESCE(amount,0), COALESCE(reference_no,''), "
            "COALESCE(payment_mode,''), payment_date, COALESCE(note,'') "
            "FROM customer_payments WHERE id=? AND COALESCE(deleted,0)=0",
            (int(payment_id),),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, supplier_id, COALESCE(amount,0), COALESCE(reference,''), "
            "COALESCE(mode,''), payment_date, '' "
            "FROM supplier_payments WHERE id=? AND COALESCE(deleted,0)=0",
            (int(payment_id),),
        ).fetchone()
    if not row or float(row[2] or 0) >= 0:
        return None
    pay_id, pid, amount, ref, mode, pdate, note = row
    ret = refund_marks(conn, k, [(int(pay_id), int(pid or 0), float(amount), str(ref))]).get(int(pay_id))
    if not ret:
        return None
    return {"payment_id": int(pay_id), "party_id": int(pid or 0), "amount": r2(-float(amount)),
            "return_no": str(ref), "return_id": ret["return_id"], "mode": str(mode),
            "date": str(pdate or "")[:10], "note": str(note)}


def refund_marks(conn: sqlite3.Connection, kind: str,
                 rows: list[tuple[int, int, float, str]]) -> dict[int, dict]:
    """payment id -> the return it refunds ({return_id, return_no, return_date, bill_no,
    refund}) for the rows that are refunds: (payment id, party id, amount, reference) with
    amount < 0 and a reference that is one of that party's returns."""
    from core.return_credit import _show_no

    k = _kind(kind)
    want: dict[tuple[int, str], list[int]] = {}
    for pay_id, pid, amount, ref in rows or []:
        ref = str(ref or "").strip()
        try:
            amt = float(amount or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt < 0 and ref and int(pid or 0) > 0:
            want.setdefault((int(pid), ref), []).append(int(pay_id))
    if not want:
        return {}
    party_ids = sorted({p for p, _ in want})
    marks = ",".join("?" * len(party_ids))
    if k == "customer":
        sql = (
            "SELECT r.customer_id, r.id, COALESCE(r.return_no,''), r.return_date, "
            "COALESCE(r.refund_amount,0), b.bill_no FROM sales_returns r "
            "LEFT JOIN sales b ON b.id = r.sale_id "
            f"WHERE r.customer_id IN ({marks}) AND COALESCE(r.deleted,0)=0"
        )
        params: list = party_ids
    else:
        # a purchase return belongs to the supplier it was booked to, or to its purchase's
        sql = (
            "SELECT COALESCE(NULLIF(r.supplier_id,0), b.supplier_id), r.id, COALESCE(r.return_no,''), "
            "r.return_date, COALESCE(r.refund_amount,0), b.purchase_no FROM purchase_returns r "
            "LEFT JOIN purchases b ON b.id = r.purchase_id "
            f"WHERE COALESCE(r.deleted,0)=0 AND (r.supplier_id IN ({marks}) OR b.supplier_id IN ({marks}))"
        )
        params = party_ids + party_ids
    out: dict[int, dict] = {}
    for pid, rid, rno, rdate, refund, bno in conn.execute(sql, params).fetchall():
        hit = want.get((int(pid or 0), str(rno)))
        if not hit:
            continue
        info = {"return_id": int(rid), "return_no": str(rno), "return_date": str(rdate or "")[:10],
                "bill_no": _show_no(k, bno), "refund": r2(refund)}
        for pay_id in hit:
            out.setdefault(pay_id, info)
    return out


def refund_returns(conn: sqlite3.Connection, kind: str, party: str = "", party_id: Any = 0,
                   payment_id: Any = 0) -> dict:
    """The party's returns with credit still open, for "Refund a return": [{return_id,
    return_no, return_date, bill_no, amount, left, refunded, items}]. payment_id = the refund
    being edited (left out, so its return shows what it had before it)."""
    from core.return_credit import open_returns

    k = _kind(kind)
    if _online():
        return {"ok": True, "supported": False, "kind": k, "party_id": 0, "returns": []}
    pid = _party_id(conn, k, party, party_id)
    try:
        edit_id = int(payment_id or 0)
    except (TypeError, ValueError):
        edit_id = 0
    editing = refund_row(conn, k, edit_id) if edit_id > 0 else None
    if editing and pid <= 0:
        pid = editing["party_id"]
    rows = open_returns(conn, k, pid, leave_out_payment=edit_id if editing else None) if pid > 0 else []
    return {"ok": True, "supported": True, "kind": k, "party_id": pid, "returns": rows,
            "editing": editing}


def _wanted(raw: Any) -> list[dict]:
    """[{return_id, amount}] from the request, once each, amounts > 0 only."""
    out: list[dict] = []
    seen: set[int] = set()
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        try:
            rid = int(x.get("return_id") or 0)
        except (TypeError, ValueError):
            continue
        amount = r2(x.get("amount"))
        if rid <= 0 or not amount > 0:
            continue
        if rid in seen:
            raise ValueError("A return is ticked twice.")
        seen.add(rid)
        out.append({"return_id": rid, "amount": amount})
    return out


def save_refund(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Save "Refund a return": one negative payment per ticked return.

    data: {kind, party, party_id?, date, mode: cash|online, note, refunds: [{return_id,
    amount}], id?: the refund being edited (then exactly one return)}. Each amount is above
    0 and never more than that return's open credit. ValueError says why not; nothing is
    written then. Returns the payment list (get_payments) with payment_ids.
    """
    from core.desktop_settings_service import _notify_payments_changed, get_payments
    from core.return_credit import open_returns

    k = _kind((data or {}).get("kind"))
    if _online():
        raise ValueError("Refunds from the Payment screen work in Offline / offline-first. "
                         "Online, refund the return from the Returns page.")
    try:
        edit_id = int((data or {}).get("id") or 0)
    except (TypeError, ValueError):
        edit_id = 0
    editing = refund_row(conn, k, edit_id) if edit_id > 0 else None
    if edit_id > 0 and not editing:
        raise ValueError("Refund not found for edit.")
    pid = _party_id(conn, k, data.get("party") or "", data.get("party_id") or 0)
    if editing:
        pid = pid or editing["party_id"]
        if pid != editing["party_id"]:
            raise ValueError("A refund stays with its party; delete it and refund again for another.")
    if pid <= 0:
        raise ValueError("Please select a customer." if k == "customer" else "Please select a supplier.")
    mode = str(data.get("mode") or "cash").strip().lower()
    if mode not in MODES:
        raise ValueError("Pick Cash or Online for the refund.")
    pdate = str(data.get("date") or date.today().strftime("%Y-%m-%d"))[:10]
    note = str(data.get("note") or "").strip()
    wanted = _wanted(data.get("refunds"))
    if not wanted:
        raise ValueError("Tick a return and type the amount to refund.")
    if editing and len(wanted) != 1:
        raise ValueError("An edited refund pays back one return.")
    open_by_id = {r["return_id"]: r for r in open_returns(
        conn, k, pid, leave_out_payment=edit_id if editing else None)}
    plan = []
    for w in wanted:
        hit = open_by_id.get(w["return_id"])
        if hit is None:
            raise ValueError("A return you ticked has no credit left. Pick the party again.")
        if w["amount"] > hit["left"] + 0.009:
            raise ValueError(f"{hit['return_no']} has ₹{hit['left']:.2f} credit left; "
                             f"₹{w['amount']:.2f} cannot be refunded.")
        plan.append({**w, "return_no": hit["return_no"]})
    if editing and plan[0]["return_id"] != editing["return_id"]:
        raise ValueError("An edited refund keeps its return.")

    ids: list[int] = []
    if k == "customer":
        for p in plan:
            a = p["amount"]
            text = f"Refund {p['return_no']}" + (f" · {note}" if note else "")
            vals = (pdate, -a, mode, -a if mode == "cash" else 0.0, -a if mode == "online" else 0.0,
                    p["return_no"], text)
            if editing:
                conn.execute(
                    "UPDATE customer_payments SET payment_date=?, amount=?, payment_mode=?, "
                    "cash_amount=?, online_amount=?, reference_no=?, note=?, allocations=NULL WHERE id=?",
                    (*vals, edit_id))
                ids.append(edit_id)
            else:
                cur = conn.execute(
                    "INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, "
                    "cash_amount, online_amount, reference_no, note) VALUES (?,?,?,?,?,?,?,?)",
                    (pid, *vals))
                ids.append(int(cur.lastrowid))
        conn.commit()
        from core.customer_service import recalculate_customer_due

        recalculate_customer_due(conn, pid)
        for pay_id in ids:
            try:
                from core.sync_coordinator import after_customer_payment_saved

                after_customer_payment_saved(conn, pay_id, pid)
            except Exception:
                pass
    else:
        from core.offline_first.numbers import maybe_doc_number
        from core.purchase_service import recalculate_supplier_due

        mode_word = "Cash" if mode == "cash" else "Online"
        for p in plan:
            a = p["amount"]
            # The balance before and after THIS row, as an ordinary payment stores them; a
            # refund raises what we owe the supplier (or lowers their credit with us).
            due, credit = _supplier_net(conn, pid)
            base = r2(due - credit + (-editing["amount"] if editing else 0.0))
            before, after = r2(max(0.0, base)), r2(max(0.0, base + a))
            if editing:
                conn.execute(
                    "UPDATE supplier_payments SET payment_date=?, amount=?, mode=?, reference=?, "
                    "due_before=?, due_after=?, allocations=NULL WHERE id=?",
                    (pdate, -a, mode_word, p["return_no"], before, after, edit_id))
                ids.append(edit_id)
            else:
                next_id = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM supplier_payments").fetchone()[0]
                pay_no = maybe_doc_number(conn, "PAY") or f"PAY{int(next_id):04d}"
                cur = conn.execute(
                    "INSERT INTO supplier_payments (payment_no, supplier_id, payment_date, amount, mode, "
                    "reference, due_before, due_after) VALUES (?,?,?,?,?,?,?,?)",
                    (pay_no, pid, pdate, -a, mode_word, p["return_no"], before, after))
                ids.append(int(cur.lastrowid))
            conn.commit()
            recalculate_supplier_due(conn, pid)
        for pay_id in ids:
            try:
                from core.sync_coordinator import after_supplier_payment_saved

                after_supplier_payment_saved(conn, pay_id, pid)
            except Exception:
                pass
    out = get_payments(conn, k)
    out["ok"] = True
    out["updated"] = bool(editing)
    out["payment_ids"] = ids
    out["refunded"] = r2(sum(p["amount"] for p in plan))
    _notify_payments_changed(k)
    return out


def _supplier_net(conn: sqlite3.Connection, supplier_id: int) -> tuple[float, float]:
    row = conn.execute(
        "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) FROM suppliers WHERE id=?",
        (int(supplier_id),),
    ).fetchone()
    return (float(row[0] or 0), float(row[1] or 0)) if row else (0.0, 0.0)
