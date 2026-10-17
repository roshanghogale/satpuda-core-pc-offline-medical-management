"""Returns as party credit (owner, 17 Oct 2026: "returns are saved as credit; when billing a
party with return credit not used yet, show those returns to pick; the bill shows a Return row
with the medicines that came back, then Total, Return total and Net to pay").

The server and the web are the reference (server-live webStore.js ``openReturns``,
``returnsForBill``, ``returnsInBill``). A return's ``allocations`` are the bills its credit was
used in, the same JSON as a payment's; core/bill_clearing.py pays them first, so the party's
dues follow by themselves once a return is given to a bill.

- open_returns:  a party's returns whose credit is not used yet, for the popup.
- plan_returns:  the ticked ones checked BEFORE the bill is saved (once saved, the clearing
                 rule spends the open credit on the new bill oldest-first, and it would no
                 longer read as open).
- settle_returns_in_bill: after the save, each ticked return gives what it has left, never
                 more than the bill leaves unpaid; the bill is added to its allocations (no
                 stock moves).
- returns_in_bill: the returns settled in a bill, with their medicines, for the bill screen
                 and the print.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from core.bill_clearing import _kind, clean_allocations, party_ledger, r2


def _tables(kind: str) -> tuple[str, str, str, str]:
    """(returns table, items table, bill table, bill key) for customer / supplier."""
    if _kind(kind) == "customer":
        return "sales_returns", "sales_return_items", "sales", "sale_id"
    return "purchase_returns", "purchase_return_items", "purchases", "purchase_id"


def _show_no(kind: str, no: Any) -> str:
    from core.fy_serial import display_purchase_no, display_sales_bill_no

    raw = str(no or "")
    return display_sales_bill_no(raw) if _kind(kind) == "customer" else display_purchase_no(raw)


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
    except sqlite3.Error:
        return False


def _return_rows(conn: sqlite3.Connection, kind: str, ids: list[int]) -> dict[int, dict]:
    """id -> {return_id, return_no, return_date, bill_no, refund, settle_mode, allocations,
    items: [{name, batch_no, expiry, mrp, qty, rate, amount}]} for these returns (deleted ones left out)."""
    table, items_t, bill_t, key = _tables(kind)
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    no_col = "bill_no" if bill_t == "sales" else "purchase_no"
    settle = "r.settle_mode" if _has_column(conn, table, "settle_mode") else "NULL"
    alloc = "r.allocations" if _has_column(conn, table, "allocations") else "NULL"
    out: dict[int, dict] = {}
    for rid, rno, rdate, bno, refund, mode, allocations in conn.execute(
        f"SELECT r.id, COALESCE(r.return_no,''), r.return_date, b.{no_col}, "
        f"COALESCE(r.refund_amount,0), {settle}, {alloc} "
        f"FROM {table} r LEFT JOIN {bill_t} b ON b.id = r.{key} "
        f"WHERE r.id IN ({marks}) AND COALESCE(r.deleted,0)=0",
        ids,
    ).fetchall():
        out[int(rid)] = {
            "return_id": int(rid), "return_no": str(rno), "return_date": str(rdate or "")[:10],
            "bill_no": _show_no(kind, bno), "refund": r2(refund),
            "settle_mode": str(mode or "ledger").strip().lower() or "ledger",
            "allocations": clean_allocations(allocations), "items": [],
        }
    for rid, name, batch, expiry, mrp, qty, rate, amount in conn.execute(
        f"SELECT ri.return_id, COALESCE(m.name,''), COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''), "
        f"COALESCE(m.mrp,0), COALESCE(ri.qty,0), "
        f"COALESCE(ri.rate,0), COALESCE(ri.amount,0) FROM {items_t} ri "
        f"LEFT JOIN medicines m ON m.id = ri.medicine_id "
        f"WHERE ri.return_id IN ({marks}) ORDER BY ri.return_id, ri.id",
        ids,
    ).fetchall():
        hit = out.get(int(rid))
        if hit is not None:
            q = float(qty or 0)
            hit["items"].append({"name": str(name), "batch_no": str(batch),
                                 "expiry": str(expiry or ""), "mrp": float(mrp or 0),
                                 "qty": int(q) if q == int(q) else q,
                                 "rate": float(rate or 0), "amount": r2(amount)})
    return out


def _by_date(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["return_date"], r["return_id"]))


def open_returns(conn: sqlite3.Connection, kind: str, party_id: int, *,
                 leave_out_bill: int | None = None) -> list[dict]:
    """The party's returns whose credit is not used yet, oldest first:
    [{return_id, return_no, return_date, bill_no, amount, left, items}].

    left = refund - what the clearing rule spent of it on any bill (chosen or oldest first)
    - cash/online handed back for it (negative receipts with the return number as reference).
    A return settled in cash or online is never offered, nor the counter sale's returns.
    leave_out_bill: the bill being finished (an autosaved one is already in the ledger).
    """
    k = _kind(kind)
    try:
        pid = int(party_id or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return []
    if k == "customer":
        from core.customer_service import COUNTER_SALE

        row = conn.execute("SELECT COALESCE(name,'') FROM customers WHERE id=?", (pid,)).fetchone()
        if row and str(row[0]).strip().upper() == COUNTER_SALE:
            return []
    led = party_ledger(conn, k, pid, leave_out_bill=leave_out_bill)
    used: dict[int, float] = {}
    for a in led["result"]["applied"]:
        if a["credit_kind"] == "return":
            used[int(a["credit_id"])] = r2(used.get(int(a["credit_id"]), 0) + a["amount"])
    left = {}
    for c in led["credits"]:
        if c["kind"] == "return" and c["amount"] > 0.009:
            rest = r2(c["amount"] - used.get(int(c["id"]), 0))
            if rest > 0.009:
                left[int(c["id"])] = rest
    if not left:
        return []
    paid_out: dict[str, float] = {}
    if k == "customer":
        for ref, paid in conn.execute(
            "SELECT COALESCE(reference_no,''), SUM(-amount) FROM customer_payments "
            "WHERE customer_id=? AND COALESCE(deleted,0)=0 AND amount < 0 GROUP BY reference_no",
            (pid,),
        ).fetchall():
            paid_out[str(ref)] = float(paid or 0)
    out = []
    for rid, row in _return_rows(conn, k, list(left)).items():
        if row["settle_mode"] in ("cash", "online"):
            continue
        rest = r2(min(left[rid], row["refund"] - paid_out.get(row["return_no"], 0)))
        if rest <= 0.009:
            continue
        out.append({"return_id": rid, "return_no": row["return_no"], "return_date": row["return_date"],
                    "bill_no": row["bill_no"], "amount": row["refund"], "left": rest,
                    "items": [{"name": it["name"], "qty": it["qty"], "amount": it["amount"]}
                              for it in row["items"]]})
    return _by_date(out)


def open_returns_for_party(conn: sqlite3.Connection, kind: str, party: str = "",
                           party_id: int = 0, bill_id: int = 0) -> dict:
    """The engine route behind the Sales / Purchase popup: the party by id, else by name;
    bill_id = the tab's autosaved bill, left out. Online stores answer supported=False (their
    bills and returns live on the server)."""
    from core.sync_prefs import is_online_mode

    k = "customer" if str(kind or "").lower().startswith("c") else "supplier"
    if is_online_mode():
        return {"ok": True, "supported": False, "kind": k, "party_id": 0, "returns": []}
    try:
        pid = int(party_id or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0 and str(party or "").strip():
        table = "customers" if k == "customer" else "suppliers"
        row = conn.execute(
            f"SELECT id FROM {table} WHERE UPPER(TRIM(name))=UPPER(TRIM(?)) LIMIT 1", (str(party),)
        ).fetchone()
        pid = int(row[0]) if row else 0
    try:
        leave_out = int(bill_id or 0) or None
    except (TypeError, ValueError):
        leave_out = None
    rows = open_returns(conn, k, pid, leave_out_bill=leave_out) if pid > 0 else []
    return {"ok": True, "supported": True, "kind": k, "party_id": pid, "returns": rows}


def wanted_ids(raw: Any) -> list[int]:
    """The ticked returns as ids, in order, once each ([12, 15] or [{return_id: 12}, ...])."""
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except (TypeError, ValueError):
            raw = []
    out: list[int] = []
    for x in raw if isinstance(raw, list) else []:
        v = x.get("return_id") if isinstance(x, dict) else x
        try:
            rid = int(v)
        except (TypeError, ValueError):
            continue
        if rid > 0 and rid not in out:
            out.append(rid)
    return out


def plan_returns(conn: sqlite3.Connection, kind: str, party_id: int, wanted: Any, *,
                 leave_out_bill: int | None = None) -> list[dict]:
    """The ticked returns with their open credit ({return_id, return_no, left}), checked
    before the bill is saved. ValueError when one has no credit left any more."""
    ids = wanted_ids(wanted)
    if not ids:
        return []
    open_by_id = {r["return_id"]: r for r in open_returns(conn, kind, party_id,
                                                          leave_out_bill=leave_out_bill)}
    plan = []
    for rid in ids:
        hit = open_by_id.get(rid)
        if hit is None:
            raise ValueError("A return you ticked has no credit left. Pick the party again.")
        plan.append({"return_id": rid, "return_no": hit["return_no"], "left": hit["left"]})
    return plan


def plan_total(plan: list[dict]) -> float:
    return r2(sum(float(p.get("left") or 0) for p in plan or []))


def settle_returns_in_bill(conn: sqlite3.Connection, kind: str, plan: list[dict], bill_id: int,
                           bill_no: str, unpaid: float, *, commit: bool = True) -> dict:
    """Give the planned returns to this bill: in order, amount = min(open credit, what the
    bill still leaves unpaid). Appends {bill_id, bill_no, amount} to each return's allocations
    and saves the return (no stock movement). {"list": [{return_id, return_no, amount}],
    "total"}."""
    from core.sync_meta_bump import bump_row_meta

    table = _tables(kind)[0]
    room = r2(unpaid)
    done: list[dict] = []
    for p in plan or []:
        if room <= 0.009:
            break
        amount = r2(min(float(p.get("left") or 0), room))
        if amount <= 0.009:
            continue
        rid = int(p["return_id"])
        row = conn.execute(f"SELECT allocations FROM {table} WHERE id=?", (rid,)).fetchone()
        if row is None:
            continue
        lst = clean_allocations(row[0])
        lst.append({"bill_id": int(bill_id), "bill_no": _show_no(kind, bill_no), "amount": amount})
        conn.execute(f"UPDATE {table} SET allocations=? WHERE id=?", (json.dumps(lst), rid))
        bump_row_meta(conn, table, rid, commit=False)
        room = r2(room - amount)
        done.append({"return_id": rid, "return_no": p.get("return_no") or "", "amount": amount})
    if commit:
        conn.commit()
    return {"list": done, "total": r2(sum(d["amount"] for d in done))}


def sale_returns_room(total: float, paid: float, plan: list[dict]) -> float:
    """What the ticked returns will settle in a sale of ``total`` with ``paid`` at the counter
    (the Return total on the screen and the bill): never more than the bill leaves unpaid."""
    return r2(min(plan_total(plan), max(0.0, r2(total) - r2(paid))))


def settle_sale(conn: sqlite3.Connection, customer_id: int, plan: list[dict], sale_id: int) -> dict:
    """After a new sale is saved: give it the planned returns, then its own figures follow
    (due = total - paid - returns used, as the web's paymentResult(total - R, ..., prevCredit -
    R): the credit those returns were is not "Credit Used" a second time) and the customer's
    dues are worked out again."""
    from core.calc_engine import calc_payment_result
    from core.customer_service import recalculate_customer_due
    from core.sync_meta_bump import bump_row_meta

    if not plan or int(sale_id or 0) <= 0:
        return {"list": [], "total": 0.0}
    row = conn.execute(
        "SELECT bill_no, COALESCE(total_amount,0), COALESCE(amount_paid,0), COALESCE(cash_paid,0), "
        "COALESCE(online_paid,0), COALESCE(previous_due,0), COALESCE(previous_credit,0) "
        "FROM sales WHERE id=?",
        (int(sale_id),),
    ).fetchone()
    if not row:
        return {"list": [], "total": 0.0}
    bill_no, total, paid, cash, online, prev_due, prev_credit = row
    done = settle_returns_in_bill(conn, "customer", plan, int(sale_id), bill_no,
                                  max(0.0, r2(float(total) - float(paid))), commit=False)
    used = done["total"]
    if used > 0:
        credit_left = r2(max(0.0, float(prev_credit) - used))
        pay = calc_payment_result(r2(float(total) - used), float(cash), float(online),
                                  float(prev_due), credit_left)
        conn.execute(
            "UPDATE sales SET due_amount=?, credit_amount=?, total_due=?, previous_credit=?, "
            "bill_cleared=? WHERE id=?",
            (pay["due_amount"], pay["credit_amount"], pay["total_due"], credit_left,
             1 if pay["due_amount"] == 0 else 0, int(sale_id)),
        )
        bump_row_meta(conn, "sales", int(sale_id), commit=False)
    conn.commit()
    if used > 0 and int(customer_id or 0) > 0:
        recalculate_customer_due(conn, int(customer_id))
    return done


def settle_purchase(conn: sqlite3.Connection, supplier_id: int, plan: list[dict],
                    purchase_id: int) -> dict:
    """After a new purchase is saved: give it the planned purchase returns (the agency settles
    them inside this bill), then the supplier's dues are worked out again."""
    from core.due_fifo import PURCHASE_ENTRY_PAID_SQL
    from core.purchase_service import recalculate_supplier_due
    from core.sync_meta_bump import bump_row_meta

    if not plan or int(purchase_id or 0) <= 0:
        return {"list": [], "total": 0.0}
    row = conn.execute(
        f"SELECT purchase_no, COALESCE(final_amount, total_amount, 0), {PURCHASE_ENTRY_PAID_SQL}, "
        "COALESCE(previous_credit,0) FROM purchases WHERE id=?",
        (int(purchase_id),),
    ).fetchone()
    if not row:
        return {"list": [], "total": 0.0}
    purchase_no, final, paid, prev_credit = row
    done = settle_returns_in_bill(conn, "supplier", plan, int(purchase_id), purchase_no,
                                  max(0.0, r2(float(final or 0) - float(paid or 0))), commit=False)
    if done["total"] > 0:
        # The supplier's credit these returns were is used here, not carried on as credit.
        conn.execute("UPDATE purchases SET previous_credit=? WHERE id=?",
                     (r2(max(0.0, float(prev_credit or 0) - done["total"])), int(purchase_id)))
        bump_row_meta(conn, "purchases", int(purchase_id), commit=False)
    conn.commit()
    if done["total"] > 0 and int(supplier_id or 0) > 0:
        recalculate_supplier_due(conn, int(supplier_id))
    return done


def returns_in_bill(conn: sqlite3.Connection, kind: str, bill_id: int) -> list[dict]:
    """Returns settled inside this bill: [{return_id, return_no, return_date, bill_no, refund,
    amount (used in this bill), items: [{name, batch_no, qty, rate, amount}]}], oldest first."""
    table = _tables(kind)[0]
    try:
        bid = int(bill_id or 0)
    except (TypeError, ValueError):
        return []
    if bid <= 0 or not _has_column(conn, table, "allocations"):
        return []
    ids = []
    for rid, allocations in conn.execute(
        f"SELECT id, allocations FROM {table} WHERE COALESCE(deleted,0)=0 "
        "AND allocations IS NOT NULL AND allocations LIKE ?",
        (f"%{bid}%",),
    ).fetchall():
        if any(a["bill_id"] == bid for a in clean_allocations(allocations)):
            ids.append(int(rid))
    out = []
    for rid, row in _return_rows(conn, kind, ids).items():
        used = r2(sum(a["amount"] for a in row["allocations"] if a["bill_id"] == bid))
        if used <= 0.009:
            continue
        out.append({"return_id": rid, "return_no": row["return_no"], "return_date": row["return_date"],
                    "bill_no": row["bill_no"], "refund": row["refund"], "amount": used,
                    "items": row["items"]})
    return _by_date(out)


def returns_total(rows: list[dict]) -> float:
    return r2(sum(float(r.get("amount") or 0) for r in rows or []))
