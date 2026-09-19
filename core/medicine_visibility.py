"""Soft-hide medicines from inventory lists and alert dashboards."""
from __future__ import annotations

from datetime import date

from core.alert_thresholds import parse_expiry

HIDDEN_FILTER_SQL = "COALESCE(is_hidden, 0) = 0"


def hide_medicine(conn, medicine_id: int) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE medicines SET is_hidden=1 WHERE id=?", (int(medicine_id),))
    conn.commit()


def hide_zero_stock_medicines(conn) -> int:
    cur = conn.cursor()
    cur.execute(
        "UPDATE medicines SET is_hidden=1 "
        "WHERE COALESCE(stock_qty, 0) <= 0 AND COALESCE(is_hidden, 0) = 0"
    )
    count = int(cur.rowcount or 0)
    conn.commit()
    return count


def hide_medicines_by_name(conn, name: str) -> int:
    """Hide every visible batch for a medicine name (low-stock alert dismiss)."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE medicines SET is_hidden=1 "
        "WHERE TRIM(name)=TRIM(?) AND COALESCE(is_hidden, 0) = 0",
        (str(name or "").strip(),),
    )
    count = int(cur.rowcount or 0)
    conn.commit()
    return count


def hide_medicines_by_name_and_pack(conn, name: str, pack: str) -> int:
    """Hide visible batches matching name + pack size (out-of-stock dismiss)."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE medicines SET is_hidden=1 "
        "WHERE TRIM(name)=TRIM(?) AND TRIM(COALESCE(unit, ''))=TRIM(?) "
        "AND COALESCE(is_hidden, 0) = 0",
        (str(name or "").strip(), str(pack or "").strip()),
    )
    count = int(cur.rowcount or 0)
    conn.commit()
    return count


def hide_medicines_by_name_and_batch(conn, name: str, batch: str) -> int:
    """Hide a single visible batch (expired / near-expiry dismiss)."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE medicines SET is_hidden=1 "
        "WHERE TRIM(name)=TRIM(?) AND TRIM(COALESCE(batch_no, ''))=TRIM(?) "
        "AND COALESCE(is_hidden, 0) = 0",
        (str(name or "").strip(), str(batch or "").strip()),
    )
    count = int(cur.rowcount or 0)
    conn.commit()
    return count


def hide_all_expired_medicines(conn) -> int:
    """Hide every visible batch that is expired and still has stock on hand."""
    today = date.today()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, COALESCE(expiry_date, '') FROM medicines "
        f"WHERE {HIDDEN_FILTER_SQL} AND COALESCE(stock_qty, 0) > 0"
    )
    ids = []
    for med_id, expiry_raw in cur.fetchall():
        # Month-only expiries run to the month end; see expiry_cutoff.
        from core.batch_visibility import expiry_cutoff

        cutoff = expiry_cutoff(expiry_raw)
        if cutoff and cutoff < today:
            ids.append(int(med_id))
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur.execute(f"UPDATE medicines SET is_hidden=1 WHERE id IN ({placeholders})", ids)
    count = int(cur.rowcount or 0)
    conn.commit()
    return count


def hide_all_out_of_stock_medicines(conn) -> int:
    """Hide every visible batch for medicine names that are fully out of stock."""
    from core.alert_monitoring_service import medicine_names_fully_out_of_stock

    cur = conn.cursor()
    oos_names = medicine_names_fully_out_of_stock(conn)
    count = 0
    for name in oos_names:
        cur.execute(
            "UPDATE medicines SET is_hidden=1 "
            "WHERE UPPER(TRIM(name))=UPPER(TRIM(?)) AND COALESCE(is_hidden, 0) = 0",
            (str(name or "").strip(),),
        )
        count += int(cur.rowcount or 0)
    if count:
        conn.commit()
    return count


def hide_medicines_with_zero_stock(conn, medicine_ids) -> int:
    """Hide medicine batches whose stock reached zero."""
    ids = [int(x) for x in medicine_ids if x]
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur = conn.cursor()
    cur.execute(
        f"UPDATE medicines SET is_hidden=1 "
        f"WHERE id IN ({placeholders}) AND COALESCE(stock_qty, 0) <= 0 "
        f"AND COALESCE(is_hidden, 0) = 0",
        ids,
    )
    count = int(cur.rowcount or 0)
    if count:
        conn.commit()
    return count


def hide_medicines_after_return(conn, medicine_ids, *, reason: str = "") -> int:
    """
    Hide medicines returned to supplier / written off.
    Always hides zero-stock batches; hides all returned batches for expired returns.
    """
    ids = [int(x) for x in medicine_ids if x]
    if not ids:
        return 0
    reason_l = (reason or "").lower()
    expired_return = any(
        token in reason_l
        for token in ("expired", "near expiry", "near-expiry", "expiry")
    )
    cur = conn.cursor()
    if expired_return:
        placeholders = ",".join("?" * len(ids))
        cur.execute(
            f"UPDATE medicines SET is_hidden=1 "
            f"WHERE id IN ({placeholders}) AND COALESCE(is_hidden, 0) = 0",
            ids,
        )
    else:
        placeholders = ",".join("?" * len(ids))
        cur.execute(
            f"UPDATE medicines SET is_hidden=1 "
            f"WHERE id IN ({placeholders}) AND COALESCE(stock_qty, 0) <= 0 "
            f"AND COALESCE(is_hidden, 0) = 0",
            ids,
        )
    count = int(cur.rowcount or 0)
    if count:
        conn.commit()
    return count


def unhide_medicines_by_name_on_restock(conn, name: str) -> int:
    """Show hidden batches again when the same medicine name is purchased."""
    name = str(name or "").strip()
    if not name:
        return 0
    cur = conn.cursor()
    cur.execute(
        "UPDATE medicines SET is_hidden=0 "
        "WHERE TRIM(name)=TRIM(?) AND COALESCE(is_hidden, 0) = 1",
        (name,),
    )
    count = int(cur.rowcount or 0)
    if count:
        conn.commit()
    return count
