"""Which bills a party's payments and returns clear (owner, 17 Oct 2026: "when collecting or
paying, choose which bills the payment clears, and show it in a payment report").

One rule, written the same way on the server (server-live src/services/billClearing.js, the
reference), the PC (this file) and the phone (BillClearing.kt);
tests/fixtures/bill_clearing_vectors.json holds the cases all three must agree on.

 1. A bill's own unpaid part is total - paid at the counter (never below 0).
 2. Credits (payments, later returns) are taken in date order. Each one first pays the bills
    it was given (``allocations``), never more than that bill still owes and never more than
    the credit itself. A bill that is gone (deleted, another party's) is skipped and that
    money stays with the credit.
 3. What every credit has left, plus (sales only) whatever was overpaid at the counter on a
    bill, clears the remaining bills oldest first -- exactly the rule there was before
    allocations (due_fifo.cascade_*), so a party with no allocations gets the same figures.
    A refund paid out (negative payment) only lowers that pool.

Every value is rounded as JavaScript's Math.round(x * 100) / 100 does -- floor(x*100 + 0.5)
/ 100 -- never with Python's round(), or the PC and the server would part on a half paisa.

The second half reads one party's (or many parties') bills and credits from the store
database, as the server's partyDueCascade.js does, for the dues, the bill picker and the
payment report.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime
from typing import Any, Iterable

_KIND_ORDER = {"payment": 0, "return": 1, "overpaid_bill": 2}


def _num(v: Any) -> float:
    """JavaScript's Number(v) for what this rule meets: numbers, numeric text, None."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return math.nan


def _id(v: Any) -> Any:
    """An id as a number, int when whole (so it prints and compares as the server's)."""
    n = _num(v)
    if math.isfinite(n) and n == int(n):
        return int(n)
    return n


def r2(v: Any) -> float:
    x = _num(v)
    if not math.isfinite(x):
        x = 0.0
    return math.floor(x * 100 + 0.5) / 100


def clean_allocations(raw: Any) -> list[dict]:
    """Allocations as stored: [{bill_id, bill_no, amount}], bad entries dropped."""
    lst = raw
    if isinstance(lst, (bytes, str)):
        try:
            lst = json.loads(lst)
        except (TypeError, ValueError):
            return []
    if not isinstance(lst, list):
        return []
    out: list[dict] = []
    for a in lst[:500]:
        if not isinstance(a, dict):
            continue
        bill_id = _num(a.get("bill_id"))
        amount = r2(a.get("amount"))
        if not math.isfinite(bill_id) or bill_id <= 0 or not amount > 0:
            continue
        bill_no = a.get("bill_no")
        if bill_no is not None:
            if isinstance(bill_no, float) and bill_no.is_integer():
                bill_no = int(bill_no)
            bill_no = str(bill_no)[:60]
        out.append({"bill_id": _id(bill_id), "bill_no": bill_no, "amount": amount})
    return out


def allocations_json(raw: Any) -> str | None:
    """What the allocations column holds: the cleaned list as JSON, or NULL when none."""
    lst = clean_allocations(raw)
    return json.dumps(lst) if lst else None


def _date_key(d: Any) -> str:
    if isinstance(d, (datetime, date)):
        return d.isoformat()[:10]
    return str(d or "")[:10]


def _by_date_then_id(b: dict) -> tuple:
    return (_date_key(b.get("date")), _num(b.get("id")))


def _by_date_kind_id(c: dict) -> tuple:
    return (_date_key(c.get("date")), _KIND_ORDER.get(c.get("kind"), 9), _num(c.get("id")))


def _total(values: Iterable[float]) -> float:
    # left to right, as JavaScript's reduce: Python's sum() of floats is compensated (3.12+)
    s = 0.0
    for v in values:
        s = s + v
    return s


def clear_bills(bills: list[dict], credits: list[dict], *, overpay_joins: bool = True) -> dict:
    """bills:   [{id, date, total, paid}] -- any order; cleared oldest (date, id) first.
    credits: [{kind, id, date, amount, allocations}] -- payments and returns.
    overpay_joins: True for sales (a counter overpayment on one bill pays older ones),
                   False for purchases (as before).

    Returns {"bills": [{id, remaining, cleared}], "applied": [{credit_kind, credit_id,
    bill_id, amount, how}]}, how = "chosen" (rule 2) or "oldest" (rule 3).
    """
    ordered = sorted(bills or [], key=_by_date_then_id)
    unpaid: dict[Any, float] = {}
    for b in ordered:
        unpaid[_id(b.get("id"))] = max(0.0, r2(_num(b.get("total")) - _num(b.get("paid"))))

    applied: list[dict] = []
    sources: list[dict] = []
    for c in sorted(credits or [], key=_by_date_kind_id):
        amount = r2(c.get("amount"))
        if amount <= 0:
            if amount < 0:
                sources.append({"kind": c.get("kind"), "id": c.get("id"), "date": c.get("date"), "left": amount})
            continue
        avail = amount
        for a in clean_allocations(c.get("allocations")):
            if avail <= 0.009:
                break
            bid = a["bill_id"]
            if bid not in unpaid:
                continue
            take = r2(min(a["amount"], unpaid[bid], avail))
            if take <= 0.009:
                continue
            unpaid[bid] = r2(unpaid[bid] - take)
            avail = r2(avail - take)
            applied.append({"credit_kind": c.get("kind"), "credit_id": c.get("id"), "bill_id": bid,
                            "amount": take, "how": "chosen"})
        if avail > 0.009:
            sources.append({"kind": c.get("kind"), "id": c.get("id"), "date": c.get("date"), "left": avail})
    if overpay_joins:
        for b in ordered:
            over = r2(_num(b.get("paid")) - _num(b.get("total")))
            if over > 0.009:
                sources.append({"kind": "overpaid_bill", "id": _id(b.get("id")), "date": b.get("date"),
                                "left": over})

    # Rule 3, exactly as the old pooled loop (the per-bill figures come from the pool total).
    pool = r2(_total(s["left"] for s in sources))
    result: list[dict] = []
    for b in ordered:
        bid = _id(b.get("id"))
        owed = unpaid[bid]
        if owed <= 0.01:
            result.append({"id": bid, "remaining": 0, "cleared": True})
            continue
        if pool + 0.01 >= owed:
            pool = r2(pool - owed)
            result.append({"id": bid, "remaining": 0, "cleared": True})
            continue
        rem = r2(owed - pool)
        pool = 0
        result.append({"id": bid, "remaining": rem, "cleared": rem <= 0.01})

    # Who paid what under rule 3, for the report only: sources in date order, a refund taken
    # off the credit it was paid out of (latest positive one before it, else the next one).
    queue = _attribute_queue(sources)
    qi = 0
    for b in ordered:
        bid = _id(b.get("id"))
        owed = unpaid[bid]
        while owed > 0.01 and qi < len(queue):
            s = queue[qi]
            take = r2(min(owed, s["left"]))
            if take > 0.009:
                applied.append({"credit_kind": s["kind"], "credit_id": s["id"], "bill_id": bid,
                                "amount": take, "how": "oldest"})
                owed = r2(owed - take)
                s["left"] = r2(s["left"] - take)
            if s["left"] <= 0.009:
                qi += 1
    return {"bills": result, "applied": applied}


def _attribute_queue(sources: list[dict]) -> list[dict]:
    lst = [dict(s) for s in sorted(sources, key=_by_date_kind_id)]
    pos = [s for s in lst if s["left"] > 0]
    for neg in [s for s in lst if s["left"] < 0]:
        owe = -neg["left"]
        nd = _date_key(neg["date"])
        before = [p for p in pos if _date_key(p["date"]) <= nd][::-1]
        after = [p for p in pos if _date_key(p["date"]) > nd]
        for p in before + after:
            if owe <= 0.009:
                break
            take = min(owe, p["left"])
            p["left"] = r2(p["left"] - take)
            owe = r2(owe - take)
    return [p for p in pos if p["left"] > 0.009]


# ── The store database ──────────────────────────────────────────────────────────────────

def _kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k in ("customer", "customers", "sale", "sales"):
        return "customer"
    if k in ("supplier", "suppliers", "purchase", "purchases"):
        return "supplier"
    raise ValueError("kind must be customer or supplier")


def _in(ids: list[int]) -> str:
    return ",".join("?" * len(ids))


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
    except sqlite3.Error:
        return False


def party_ledgers(
    conn: sqlite3.Connection,
    kind: str,
    party_ids: Iterable[int],
    *,
    leave_out_payment: int | None = None,
) -> dict[int, dict]:
    """party id -> {"bills", "credits", "result"} for each of these parties.

    bills:   [{id, bill_no, date, total, paid}] -- sales: total_amount / amount_paid;
             purchases: COALESCE(final_amount,total_amount) / the paid-at-entry figure,
             bill_no = the supplier's bill number when it has one, else the purchase no.
    credits: payments (with their allocations) and returns, as clear_bills takes them.
    result:  clear_bills(bills, credits).
    leave_out_payment: a payment id left out (the one being edited), so the bill picker and
             the save check see what each bill owes without it.
    """
    from core.due_fifo import PURCHASE_ENTRY_PAID_SQL

    k = _kind(kind)
    ids = sorted({int(p) for p in party_ids if p})
    out: dict[int, dict] = {pid: {"bills": [], "credits": []} for pid in ids}
    if not ids:
        return {}
    marks = _in(ids)
    if k == "customer":
        rows = conn.execute(
            "SELECT customer_id, id, bill_no, bill_date, COALESCE(total_amount,0), COALESCE(amount_paid,0) "
            f"FROM sales WHERE customer_id IN ({marks}) "
            "AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0",
            ids,
        ).fetchall()
        for pid, bid, no, d, total, paid in rows:
            out[int(pid)]["bills"].append(
                {"id": int(bid), "bill_no": str(no or ""), "date": str(d or ""),
                 "total": float(total or 0), "paid": float(paid or 0)})
        alloc = "allocations" if _has_column(conn, "customer_payments", "allocations") else "NULL"
        pays = conn.execute(
            f"SELECT customer_id, id, payment_date, COALESCE(amount,0), {alloc} FROM customer_payments "
            f"WHERE customer_id IN ({marks}) AND COALESCE(deleted,0)=0",
            ids,
        ).fetchall()
        rets = conn.execute(
            "SELECT customer_id, id, return_date, COALESCE(refund_amount,0) FROM sales_returns "
            f"WHERE customer_id IN ({marks}) AND COALESCE(deleted,0)=0",
            ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT supplier_id, id, purchase_no, COALESCE(bill_number,''), purchase_date, "
            f"COALESCE(final_amount, total_amount), {PURCHASE_ENTRY_PAID_SQL} "
            f"FROM purchases WHERE supplier_id IN ({marks}) "
            "AND COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0",
            ids,
        ).fetchall()
        for pid, bid, pno, bnum, d, total, paid in rows:
            out[int(pid)]["bills"].append(
                {"id": int(bid), "bill_no": str(bnum or "").strip() or str(pno or ""),
                 "purchase_no": str(pno or ""), "date": str(d or ""),
                 "total": float(total or 0), "paid": float(paid or 0)})
        alloc = "allocations" if _has_column(conn, "supplier_payments", "allocations") else "NULL"
        pays = conn.execute(
            f"SELECT supplier_id, id, payment_date, COALESCE(amount,0), {alloc} FROM supplier_payments "
            f"WHERE supplier_id IN ({marks}) AND COALESCE(deleted,0)=0",
            ids,
        ).fetchall()
        # As recalculate_supplier_due always counted them: a return booked to the supplier,
        # or against one of the supplier's (live) purchases.
        rets = []
        for own, via_bill, ret_id, d, amount in conn.execute(
            "SELECT pr.supplier_id, CASE WHEN COALESCE(p.deleted,0)=0 THEN p.supplier_id END, "
            "pr.id, pr.return_date, COALESCE(pr.refund_amount,0) "
            "FROM purchase_returns pr LEFT JOIN purchases p ON pr.purchase_id=p.id "
            "WHERE COALESCE(pr.deleted,0)=0 AND ("
            f"pr.supplier_id IN ({marks}) OR (p.id IS NOT NULL AND p.supplier_id IN ({marks}) "
            "AND COALESCE(p.deleted,0)=0))",
            ids + ids,
        ).fetchall():
            for pid in {int(own or 0), int(via_bill or 0)}:
                rets.append((pid, ret_id, d, amount))
    for pid, pay_id, d, amount, allocations in pays:
        if leave_out_payment is not None and int(pay_id) == int(leave_out_payment):
            continue
        if int(pid or 0) in out:
            out[int(pid)]["credits"].append(
                {"kind": "payment", "id": int(pay_id), "date": str(d or ""), "amount": float(amount or 0),
                 "allocations": clean_allocations(allocations)})
    for pid, ret_id, d, amount in rets:
        if int(pid or 0) in out:
            out[int(pid)]["credits"].append(
                {"kind": "return", "id": int(ret_id), "date": str(d or ""), "amount": float(amount or 0),
                 "allocations": []})
    for pid, led in out.items():
        led["result"] = clear_bills(led["bills"], led["credits"], overpay_joins=(k == "customer"))
    return out


def party_ledger(conn: sqlite3.Connection, kind: str, party_id: int, **kw) -> dict:
    return party_ledgers(conn, kind, [party_id], **kw).get(
        int(party_id), {"bills": [], "credits": [], "result": {"bills": [], "applied": []}})


def open_bills(conn: sqlite3.Connection, kind: str, party_id: int,
               *, leave_out_payment: int | None = None) -> list[dict]:
    """The party's bills that still owe, oldest first: {bill_id, bill_no, date, total, owes}.

    "owes" is what is left on the bill after every other payment and return (the payment
    being edited left out), i.e. the most a payment may be put on it.
    """
    led = party_ledger(conn, kind, party_id, leave_out_payment=leave_out_payment)
    rem = {r["id"]: r["remaining"] for r in led["result"]["bills"]}
    out = []
    for b in sorted(led["bills"], key=_by_date_then_id):
        owes = float(rem.get(b["id"], 0) or 0)
        if owes > 0.009:
            out.append({"bill_id": b["id"], "bill_no": b["bill_no"], "date": b["date"][:10],
                        "total": r2(b["total"]), "owes": r2(owes)})
    return out


def check_allocations(conn: sqlite3.Connection, kind: str, party_id: int, amount: float, raw: Any,
                      *, leave_out_payment: int | None = None) -> list[dict]:
    """The allocations a payment of ``amount`` may be saved with, or ValueError saying why not.

    Only this party's bills that still owe, an amount above 0 on each, never more than the
    bill owes at this moment (without the payment being edited), and together never more than
    the payment.
    """
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except (TypeError, ValueError):
            raise ValueError("The bills chosen for this payment could not be read.")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("The bills chosen for this payment could not be read.")
    if not raw:
        return []
    owes = {b["bill_id"]: b for b in open_bills(conn, kind, party_id, leave_out_payment=leave_out_payment)}
    word = "bill" if _kind(kind) == "customer" else "purchase"
    out: list[dict] = []
    seen: set = set()
    for a in raw:
        if not isinstance(a, dict):
            continue
        bid = _id(a.get("bill_id"))
        amt = r2(a.get("amount"))
        label = str(a.get("bill_no") or bid)
        if not amt > 0:
            continue  # an unticked or zero line is simply not a choice
        if bid in seen:
            raise ValueError(f"{label} is chosen twice.")
        seen.add(bid)
        hit = owes.get(bid)
        if hit is None:
            raise ValueError(f"{label} is not an open {word} of this party (already cleared, deleted or another party's).")
        if amt > hit["owes"] + 0.009:
            raise ValueError(f"{hit['bill_no'] or label} owes ₹{hit['owes']:.2f}; ₹{amt:.2f} cannot be put on it.")
        out.append({"bill_id": bid, "bill_no": hit["bill_no"], "amount": amt})
    ticked = r2(_total(a["amount"] for a in out))
    if ticked > r2(amount) + 0.009:
        raise ValueError(f"The bills you ticked need ₹{ticked:.2f}; the amount is ₹{r2(amount):.2f}.")
    return out


def payment_clearing(conn: sqlite3.Connection, kind: str, payments: list[dict]) -> dict[int, dict]:
    """payment id -> {"cleared": [{bill_id, bill_no, date, amount, how}], "credit_left"} for
    these payments ({id, party_id, amount} each), for the history and the payment report."""
    by_party: dict[int, list[dict]] = {}
    for p in payments or []:
        try:
            pid = int(p.get("party_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            by_party.setdefault(pid, []).append(p)
    ledgers = party_ledgers(conn, kind, list(by_party))
    out: dict[int, dict] = {}
    for pid, pays in by_party.items():
        led = ledgers.get(pid) or {}
        bills = {b["id"]: b for b in led.get("bills") or []}
        per: dict[int, list[dict]] = {}
        for a in (led.get("result") or {}).get("applied") or []:
            if a["credit_kind"] != "payment":
                continue
            b = bills.get(a["bill_id"]) or {}
            per.setdefault(int(a["credit_id"]), []).append(
                {"bill_id": a["bill_id"], "bill_no": b.get("bill_no") or "", "date": str(b.get("date") or "")[:10],
                 "amount": a["amount"], "how": a["how"]})
        for p in pays:
            pay_id = int(p.get("id") or 0)
            cleared = per.get(pay_id, [])
            amount = r2(p.get("amount"))
            left = r2(amount - _total(c["amount"] for c in cleared)) if amount > 0 else 0.0
            out[pay_id] = {"cleared": cleared, "credit_left": left if left > 0.009 else 0.0}
    return out
