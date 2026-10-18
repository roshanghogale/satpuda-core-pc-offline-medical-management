"""Which bill each payment cleared, and which payments paid each bill (owner, 18 Oct 2026).

"Each payment in the history must show the bills it cleared, with a View box (date, no,
party, mode, amount, the bills it cleared, credit left; for a refund the return it refunded).
In the Ledger, on a payment row 'Cleared: S2-55 ₹500 · S2-61 ₹200', on a bill row 'Paid by:
PAY-12 ₹300, return SR1-3 ₹50'."

Everything comes from core/bill_clearing.party_ledger's ``applied`` list (the same rule the
dues use), so what is shown here is what the dues were worked out with. Offline /
offline-first only; Online stores answer supported=False (the Online ledger and payment list
read the server and are left as they were).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from core.bill_clearing import _total, clean_allocations, party_ledger, r2


def _kind(kind: Any) -> str:
    return "customer" if str(kind or "").strip().lower().startswith("c") else "supplier"


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _rs(v: float) -> str:
    return f"₹{r2(v):.2f}"


def _bill_label(kind: str, bill: dict) -> str:
    from core.fy_serial import display_purchase_no, display_sales_bill_no

    no = str(bill.get("bill_no") or "")
    if kind == "customer":
        return display_sales_bill_no(no) or f"#{bill.get('id')}"
    if no and no == str(bill.get("purchase_no") or ""):
        return display_purchase_no(no) or f"#{bill.get('id')}"
    return no or f"#{bill.get('id')}"


def _payment_labels(conn: sqlite3.Connection, kind: str, party_id: int) -> dict[int, str]:
    """payment id -> how a bill row names it: customer receipts "Payment #12", supplier
    payments by their number (PAY1-12)."""
    if kind == "customer":
        return {int(i): f"Payment #{int(i)}" for (i,) in conn.execute(
            "SELECT id FROM customer_payments WHERE customer_id=? AND COALESCE(deleted,0)=0",
            (int(party_id),)).fetchall()}
    return {int(i): (str(no or "").strip() or f"Payment #{int(i)}") for i, no in conn.execute(
        "SELECT id, payment_no FROM supplier_payments WHERE supplier_id=? AND COALESCE(deleted,0)=0",
        (int(party_id),)).fetchall()}


def _return_labels(conn: sqlite3.Connection, kind: str, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    table = "sales_returns" if kind == "customer" else "purchase_returns"
    lst = sorted(ids)
    return {int(i): (str(no or "").strip() or f"#{int(i)}") for i, no in conn.execute(
        f"SELECT id, return_no FROM {table} WHERE id IN ({','.join('?' * len(lst))})", lst).fetchall()}


def ledger_links(conn: sqlite3.Connection, kind: str, party_id: int) -> dict[str, Any]:
    """{"credits": {("payment"|"return", id): [{bill_id, bill_no, amount, how}]},
        "bills": {bill_id: [{source, id, label, amount, how}]},
        "credit_amounts": {(kind, id): amount}}
    source = "counter" (paid at the bill), "payment", "return" or "overpaid_bill"."""
    k = _kind(kind)
    led = party_ledger(conn, k, int(party_id))
    bills = {b["id"]: b for b in led["bills"]}
    pay_labels = _payment_labels(conn, k, int(party_id))
    ret_ids = {int(c["id"]) for c in led["credits"] if c["kind"] == "return"}
    ret_labels = _return_labels(conn, k, ret_ids)
    credits: dict[tuple, list[dict]] = {}
    by_bill: dict[int, list[dict]] = {}
    for b in led["bills"]:
        counter = r2(min(float(b.get("paid") or 0), float(b.get("total") or 0)))
        if counter > 0.009:
            by_bill.setdefault(b["id"], []).append(
                {"source": "counter", "id": b["id"], "label": "at the counter", "amount": counter,
                 "how": "counter"})
    for a in led["result"]["applied"]:
        bill = bills.get(a["bill_id"]) or {"id": a["bill_id"]}
        ck, cid = a["credit_kind"], a["credit_id"]
        if ck in ("payment", "return"):
            credits.setdefault((ck, int(cid)), []).append(
                {"bill_id": a["bill_id"], "bill_no": _bill_label(k, bill), "amount": a["amount"],
                 "how": a["how"]})
        if ck == "payment":
            label = pay_labels.get(int(cid), f"Payment #{cid}")
        elif ck == "return":
            label = f"return {ret_labels.get(int(cid), f'#{cid}')}"
        else:
            label = f"overpaid on {_bill_label(k, bills.get(cid) or {'id': cid})}"
        by_bill.setdefault(a["bill_id"], []).append(
            {"source": ck, "id": cid, "label": label, "amount": a["amount"], "how": a["how"]})
    amounts = {(c["kind"], int(c["id"])): r2(c["amount"]) for c in led["credits"]}
    return {"credits": credits, "bills": by_bill, "credit_amounts": amounts}


def cleared_text(lines: list[dict], left: float = 0.0, extra: list[str] | None = None) -> str:
    """"Cleared: S2-55 ₹500.00 ✓ · S2-61 ₹200.00 · ₹50.00 left as credit" (✓ = chosen)."""
    parts = [f"{x['bill_no']} {_rs(x['amount'])}{' ✓' if x.get('how') == 'chosen' else ''}"
             for x in lines or []]
    parts += list(extra or [])
    if left > 0.009:
        parts.append(f"{_rs(left)} left as credit")
    return ("Cleared: " + " · ".join(parts)) if parts else ""


def paid_by_text(lines: list[dict]) -> str:
    """"Paid by: at the counter ₹100.00 · PAY1-12 ₹300.00 ✓ · return SR1-3 ₹50.00"."""
    parts = [f"{x['label']} {_rs(x['amount'])}{' ✓' if x.get('how') == 'chosen' else ''}"
             for x in lines or []]
    return ("Paid by: " + " · ".join(parts)) if parts else ""


def attach_ledger_links(conn: sqlite3.Connection, kind: str, party_id: int, rows: list[dict]) -> None:
    """Give the Settings -> Ledger rows their second line, in place: a payment / return row
    ``cleared`` (bills it cleared) and a bill row ``paid_by`` (what paid it), each with
    ``links`` (the text: also the CSV column). A refund row says which return it refunded.
    Rows carry ``ref_kind`` ("bill" | "payment" | "return") and ``ref_id``; others are left
    alone. A failure leaves the statement as it was (the figures never depend on this)."""
    from core.return_refunds import refund_marks

    k = _kind(kind)
    try:
        links = ledger_links(conn, k, int(party_id))
        pays = [(int(r["ref_id"]), int(party_id), float(r.get("credit") or 0), str(r.get("reference") or ""))
                for r in rows if r.get("ref_kind") == "payment"]
        refunds = refund_marks(conn, k, pays)
        paid_out: dict[str, float] = {}
        open_left: dict[int, float] = {}
        if any(r.get("ref_kind") == "return" for r in rows):
            from core.return_credit import open_returns, refunds_paid_out

            paid_out = refunds_paid_out(conn, k, int(party_id))
            # the credit still open on a return, exactly as the bill popup and the refund
            # screen offer it
            open_left = {int(x["return_id"]): float(x["left"]) for x in open_returns(conn, k, int(party_id))}
    except Exception as exc:  # noqa: BLE001 - the statement still shows
        print(f"[ledger] links: {exc}")
        return
    for r in rows:
        rk, rid = r.get("ref_kind"), r.get("ref_id")
        if rk is None or rid is None:
            continue
        rid = int(rid)
        if rk == "bill":
            lines = links["bills"].get(rid) or []
            r["paid_by"] = lines
            r["links"] = paid_by_text(lines)
        elif rk == "payment":
            hit = refunds.get(rid)
            if hit:
                r["refund_of"] = hit
                r["tag"] = "refund"
                # "Payment (cash)" -> "Refund SR1-3 (cash)"; the figures stay as they were
                r["particulars"] = str(r.get("particulars") or "Payment").replace(
                    "Payment", f"Refund {hit['return_no']}", 1)
                r["links"] = f"Refund of {hit['return_no']}" + (
                    f" (bill {hit['bill_no']})" if hit.get("bill_no") else "")
                continue
            lines = links["credits"].get(("payment", rid)) or []
            amount = links["credit_amounts"].get(("payment", rid), 0.0)
            left = r2(amount - _total(x["amount"] for x in lines)) if amount > 0 else 0.0
            r["cleared"] = lines
            r["links"] = cleared_text(lines, left)
        elif rk == "return":
            lines = links["credits"].get(("return", rid)) or []
            refunded = r2(paid_out.get(str(r.get("reference") or ""), 0.0))
            extra = [f"refunded {_rs(refunded)}"] if refunded > 0.009 else []
            r["cleared"] = lines
            r["links"] = cleared_text(lines, open_left.get(rid, 0.0), extra)


def payment_details(conn: sqlite3.Connection, kind: str, payment_id: Any) -> dict[str, Any]:
    """The View box of a payment row: {ok, supported, payment: {id, payment_no, date, party,
    party_id, mode, amount, cash, online, reference, note, refund, chosen (its allocations),
    cleared: [{bill_id, bill_no, date, bill_total, amount, how}], credit_left,
    refunded: [{return_id, return_no, return_date, bill_no, refund, amount}]}}."""
    from core.return_refunds import refund_marks

    k = _kind(kind)
    if _online():
        return {"ok": True, "supported": False, "kind": k, "payment": None}
    try:
        pid = int(payment_id or 0)
    except (TypeError, ValueError):
        pid = 0
    if k == "customer":
        row = conn.execute(
            "SELECT cp.id, '', cp.payment_date, cp.customer_id, COALESCE(c.name,''), COALESCE(cp.amount,0), "
            "COALESCE(cp.payment_mode,''), COALESCE(cp.cash_amount,0), COALESCE(cp.online_amount,0), "
            "COALESCE(cp.reference_no,''), COALESCE(cp.note,''), cp.allocations "
            "FROM customer_payments cp LEFT JOIN customers c ON c.id = cp.customer_id "
            "WHERE cp.id=? AND COALESCE(cp.deleted,0)=0",
            (pid,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT sp.id, COALESCE(sp.payment_no,''), sp.payment_date, sp.supplier_id, COALESCE(s.name,''), "
            "COALESCE(sp.amount,0), COALESCE(sp.mode,''), 0, 0, COALESCE(sp.reference,''), '', sp.allocations "
            "FROM supplier_payments sp LEFT JOIN suppliers s ON s.id = sp.supplier_id "
            "WHERE sp.id=? AND COALESCE(sp.deleted,0)=0",
            (pid,),
        ).fetchone()
    if not row:
        raise ValueError("Payment not found.")
    pay_id, pay_no, pdate, party_id, party, amount, mode, cash, online, ref, note, alloc = row
    amount = r2(amount)
    out = {"id": int(pay_id), "payment_no": str(pay_no or "") or f"#{int(pay_id)}",
           "date": str(pdate or "")[:10], "party": str(party), "party_id": int(party_id or 0),
           "mode": str(mode), "amount": amount, "cash": r2(cash), "online": r2(online),
           "reference": str(ref), "note": str(note), "refund": False,
           "chosen": clean_allocations(alloc), "cleared": [], "credit_left": 0.0, "refunded": []}
    hit = refund_marks(conn, k, [(int(pay_id), int(party_id or 0), amount, str(ref))]).get(int(pay_id))
    if hit:
        out["refund"] = True
        out["refunded"] = [{**hit, "amount": r2(-amount)}]
        return {"ok": True, "supported": True, "kind": k, "payment": out}
    if int(party_id or 0) > 0 and amount > 0:
        led = party_ledger(conn, k, int(party_id))
        bills = {b["id"]: b for b in led["bills"]}
        for a in led["result"]["applied"]:
            if a["credit_kind"] != "payment" or int(a["credit_id"]) != int(pay_id):
                continue
            b = bills.get(a["bill_id"]) or {"id": a["bill_id"]}
            out["cleared"].append({"bill_id": a["bill_id"], "bill_no": _bill_label(k, b),
                                   "date": str(b.get("date") or "")[:10],
                                   "bill_total": r2(b.get("total") or 0),
                                   "amount": a["amount"], "how": a["how"]})
        left = r2(amount - _total(c["amount"] for c in out["cleared"]))
        out["credit_left"] = left if left > 0.009 else 0.0
    return {"ok": True, "supported": True, "kind": k, "payment": out}
