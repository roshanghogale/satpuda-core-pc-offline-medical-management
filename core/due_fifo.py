"""Oldest-first account clearing for sales / purchases status.

Sales: pool = standalone payments + returns + excess paid on later bills
       (amount_paid - total_amount), which is how previous dues are paid.
Purchases: pool = supplier payments + returns (entry paid is already on each bill).
"""
from __future__ import annotations

# SQLite / Postgres: never treat a stored 0 in amount_paid_at_entry as
# authoritative when cash/online at entry was filled (COALESCE would stick on 0).
PURCHASE_ENTRY_PAID_SQL = (
    "COALESCE("
    "NULLIF(COALESCE(cash_paid_at_entry,0)+COALESCE(online_paid_at_entry,0),0),"
    "NULLIF(amount_paid_at_entry,0),"
    "amount_paid,0)"
)


def _r2(v: float) -> float:
    return round(float(v or 0), 2)


def purchase_entry_paid(row: dict | None) -> float:
    """Amount paid when the purchase was entered (cash + online, then legacy fields)."""
    if not isinstance(row, dict):
        return 0.0
    split = _r2(
        float(row.get("cash_paid_at_entry") or 0)
        + float(row.get("online_paid_at_entry") or 0)
    )
    if split > 0.01:
        return split
    entry = _r2(row.get("amount_paid_at_entry") or 0)
    if entry > 0.01:
        return entry
    return _r2(row.get("amount_paid") or 0)


def fifo_paid_via_by_bill(
    purchases: list[dict],
    payments: list[dict],
) -> dict[int, float]:
    """Allocate supplier_payments oldest-bill-first. Returns purchase_id -> via amount."""
    fill_fk_from_name(
        payments,
        id_key="supplier_id",
        name_key="supplier_name",
        lookup=name_id_lookup(
            purchases, id_key="supplier_id", name_key="supplier_name"
        ),
    )
    bills_by_sid: dict[int, list[tuple[str, int, float]]] = {}
    for r in purchases or []:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        try:
            pid = int(r.get("id") or r.get("local_id") or 0)
            sid = int(r.get("supplier_id") or 0)
        except (TypeError, ValueError):
            continue
        if pid == 0 or sid <= 0:
            continue
        final = _r2(r.get("final_amount") or r.get("total_amount") or 0)
        returns = _r2(
            r.get("returns_amount")
            or r.get("refund_amount")
            or r.get("returns")
            or 0
        )
        unpaid = _r2(max(0.0, final - purchase_entry_paid(r) - returns))
        bills_by_sid.setdefault(sid, []).append(
            (str(r.get("purchase_date") or ""), pid, unpaid)
        )
    for sid in bills_by_sid:
        bills_by_sid[sid].sort(key=lambda t: (t[0], t[1]))

    pool: dict[int, float] = {}
    for p in payments or []:
        if not isinstance(p, dict) or p.get("deleted"):
            continue
        try:
            sid = int(p.get("supplier_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid <= 0:
            continue
        pool[sid] = _r2(pool.get(sid, 0.0) + float(p.get("amount") or 0))

    paid_map: dict[int, float] = {}
    for sid, bills in bills_by_sid.items():
        remaining_pool = pool.get(sid, 0.0)
        for _date, pid, unpaid in bills:
            if remaining_pool <= 0.01 or unpaid <= 0.01:
                paid_map[pid] = 0.0
                continue
            applied = min(remaining_pool, unpaid)
            paid_map[pid] = _r2(applied)
            remaining_pool = _r2(remaining_pool - applied)
    return paid_map


def sale_entry_paid(row: dict | None) -> float:
    """Amount paid when the sale was entered (cash + online, then amount_paid)."""
    if not isinstance(row, dict):
        return 0.0
    split = _r2(
        float(row.get("cash_paid") or 0) + float(row.get("online_paid") or 0)
    )
    if split > 0.01:
        return split
    return _r2(row.get("amount_paid") or 0)


def name_id_lookup(
    rows: list[dict] | None, *, id_key: str, name_key: str
) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            rid = int(r.get(id_key) or 0)
        except (TypeError, ValueError):
            rid = 0
        n = str(r.get(name_key) or "").strip().upper()
        if rid > 0 and n:
            out[n] = rid
    return out


def fill_fk_from_name(
    rows: list[dict] | None,
    *,
    id_key: str,
    name_key: str,
    lookup: dict[str, int],
) -> None:
    """Fill missing party ids on payment/return rows from a name→id map."""
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            if int(r.get(id_key) or 0) > 0:
                continue
        except (TypeError, ValueError):
            pass
        n = str(r.get(name_key) or r.get("party") or "").strip().upper()
        if n and n in lookup:
            r[id_key] = lookup[n]


def fifo_sales_remaining_by_bill(
    sales: list[dict],
    payments: list[dict],
    returns: list[dict] | None = None,
) -> dict[int, tuple[float, int]]:
    """Allocate customer payments + returns oldest-bill-first.

    Returns sale_id -> (remaining_due, account_cleared 0/1).
    """
    lookup = name_id_lookup(sales, id_key="customer_id", name_key="customer_name")
    fill_fk_from_name(
        sales, id_key="customer_id", name_key="customer_name", lookup=lookup
    )
    fill_fk_from_name(
        payments,
        id_key="customer_id",
        name_key="customer_name",
        lookup=lookup,
    )
    fill_fk_from_name(
        returns,
        id_key="customer_id",
        name_key="customer_name",
        lookup=lookup,
    )

    bills_by_cid: dict[int, list[tuple[str, int, float, float]]] = {}
    for r in sales or []:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        if int(r.get("is_autosave") or 0):
            continue
        try:
            sid = int(r.get("id") or r.get("local_id") or 0)
            cid = int(r.get("customer_id") or 0)
        except (TypeError, ValueError):
            continue
        if sid == 0 or cid <= 0:
            continue
        total = _r2(r.get("total_amount") or 0)
        paid = sale_entry_paid(r)
        bills_by_cid.setdefault(cid, []).append(
            (str(r.get("bill_date") or ""), sid, total, paid)
        )
    for cid in bills_by_cid:
        bills_by_cid[cid].sort(key=lambda t: (t[0], t[1]))

    pay_pool: dict[int, float] = {}
    for p in payments or []:
        if not isinstance(p, dict) or p.get("deleted"):
            continue
        try:
            cid = int(p.get("customer_id") or 0)
        except (TypeError, ValueError):
            cid = 0
        if cid <= 0:
            continue
        pay_pool[cid] = _r2(pay_pool.get(cid, 0.0) + float(p.get("amount") or 0))

    ret_pool: dict[int, float] = {}
    for ret in returns or []:
        if not isinstance(ret, dict) or ret.get("deleted"):
            continue
        try:
            cid = int(ret.get("customer_id") or 0)
        except (TypeError, ValueError):
            cid = 0
        if cid <= 0:
            continue
        ret_pool[cid] = _r2(
            ret_pool.get(cid, 0.0)
            + float(ret.get("refund_amount") or ret.get("amount") or 0)
        )

    out: dict[int, tuple[float, int]] = {}
    for cid, bills in bills_by_cid.items():
        cascade = cascade_sales_fifo(
            [(bid, total, paid) for _d, bid, total, paid in bills],
            pay_pool.get(cid, 0.0),
            ret_pool.get(cid, 0.0),
        )
        for bid, remaining, cleared in cascade:
            out[int(bid)] = (_r2(remaining), int(cleared))
    return out


def cascade_sales_fifo(
    bills_oldest_first: list[tuple[int, float, float]],
    standalone_payments: float,
    return_refunds: float,
) -> list[tuple[int, float, int]]:
    """
    bills: (id, total_amount, amount_paid) oldest first.
    Returns: (id, remaining_due, account_cleared 0/1)
    """
    pool = _r2(standalone_payments) + _r2(return_refunds)
    for _bid, total, paid in bills_oldest_first:
        excess = max(0.0, _r2(paid) - _r2(total))
        pool = _r2(pool + excess)

    out: list[tuple[int, float, int]] = []
    for bid, total, paid in bills_oldest_first:
        unpaid = max(0.0, _r2(total) - _r2(paid))
        if unpaid <= 0.01:
            out.append((int(bid), 0.0, 1))
            continue
        if pool + 0.01 >= unpaid:
            pool = _r2(pool - unpaid)
            out.append((int(bid), 0.0, 1))
        else:
            rem = _r2(unpaid - pool)
            pool = 0.0
            out.append((int(bid), rem, 1 if rem <= 0.01 else 0))
    return out


def cascade_purchases_fifo(
    bills_oldest_first: list[tuple[int, float, float]],
    standalone_payments: float,
    return_refunds: float,
) -> list[tuple[int, float, int]]:
    """
    bills: (id, final_amount, amount_paid_at_entry) oldest first.
    Returns: (id, remaining_due, account_cleared 0/1)
    """
    pool = _r2(standalone_payments) + _r2(return_refunds)
    out: list[tuple[int, float, int]] = []
    for bid, total, paid in bills_oldest_first:
        unpaid = max(0.0, _r2(total) - _r2(paid))
        if unpaid <= 0.01:
            out.append((int(bid), 0.0, 1))
            continue
        if pool + 0.01 >= unpaid:
            pool = _r2(pool - unpaid)
            out.append((int(bid), 0.0, 1))
        else:
            rem = _r2(unpaid - pool)
            pool = 0.0
            out.append((int(bid), rem, 1 if rem <= 0.01 else 0))
    return out


def purchase_remaining_and_via(row: dict, via_from_fifo: float | None = None) -> tuple[float, float]:
    """Online purchase row: remaining due after payments, and amount covered via payments.

    When via_from_fifo is set (client FIFO of supplier_payments), that is the source of
    truth. Otherwise prefers cascaded due_amount / bill_cleared from the server.
    Entry paid uses cash+online when amount_paid_at_entry is stored as 0.
    """
    if not isinstance(row, dict):
        return 0.0, 0.0
    final_amt = _r2(row.get("final_amount") or row.get("total_amount") or 0)
    entry_paid = purchase_entry_paid(row)
    returns = _r2(
        row.get("returns_amount")
        or row.get("refund_amount")
        or row.get("returns")
        or 0
    )
    unpaid = _r2(max(0.0, final_amt - entry_paid - returns))
    if via_from_fifo is not None:
        via = _r2(max(0.0, min(unpaid, float(via_from_fifo or 0))))
        rem = _r2(max(0.0, unpaid - via))
        return rem, via
    cleared = bool(row.get("bill_cleared") or row.get("account_cleared"))
    if cleared or unpaid <= 0.01:
        return 0.0, unpaid
    rem = None
    if row.get("due_amount") is not None:
        rem = _r2(max(0.0, float(row.get("due_amount") or 0)))
    elif row.get("total_due") is not None:
        rem = _r2(max(0.0, float(row.get("total_due") or 0)))
    elif row.get("due") is not None:
        rem = _r2(max(0.0, float(row.get("due") or 0)))
    if rem is None:
        rem = unpaid
    rem = min(rem, unpaid)
    via = _r2(max(0.0, unpaid - rem))
    return rem, via
