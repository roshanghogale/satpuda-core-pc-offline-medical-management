"""Financial-year serial numbers for sales (SCB) and purchases (1, 2, 3…).

Indian FY: 1 April – 31 March. Serials restart at 1 each FY.

Stored codes include an FY suffix for global uniqueness (``SCB1/FY2526``).
UI and printed bills show the short form (``SCB1``).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

_SALES_PREFIX = "SCB"
_FY_SEP = "/FY"
_MIGRATION_KEY = "fy_serial_migrated_v1"


def fy_start_year_for_date(value: date | str | None) -> int:
    """Calendar year when the FY containing ``value`` begins (Apr 1)."""
    if value is None or value == "":
        d = date.today()
    elif isinstance(value, str):
        raw = value.strip()[:10]
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            d = date.today()
    else:
        d = value
    return d.year if d.month >= 4 else d.year - 1


def fy_label(fy_start_year: int) -> str:
    y2 = fy_start_year + 1
    return f"{fy_start_year}-{str(y2)[2:]}"


def fy_tag(fy_start_year: int) -> str:
    return f"{_FY_SEP}{fy_label(fy_start_year)}"


def fy_date_bounds(fy_start_year: int) -> tuple[str, str]:
    return f"{fy_start_year}-04-01", f"{fy_start_year + 1}-03-31"


def display_sales_bill_no(bill_no: str | None) -> str:
    """Short bill number for UI and printing (``SCB1`` not ``SCB1/FY2526``)."""
    raw = (bill_no or "").strip()
    if not raw:
        return ""
    if _FY_SEP in raw:
        return raw.split(_FY_SEP, 1)[0]
    return raw


def display_purchase_no(purchase_no: str | None) -> str:
    raw = (purchase_no or "").strip()
    if not raw:
        return ""
    if _FY_SEP in raw:
        return raw.split(_FY_SEP, 1)[0]
    if raw.upper().startswith("APU"):
        return raw
    return raw


def encode_sales_bill_no(serial: int, fy_start_year: int, *, prefix: str = _SALES_PREFIX) -> str:
    return f"{prefix}{int(serial)}{fy_tag(fy_start_year)}"


def encode_purchase_no(serial: int, fy_start_year: int, *, prefix: str = "") -> str:
    body = f"{prefix}{int(serial)}" if prefix else str(int(serial))
    return f"{body}{fy_tag(fy_start_year)}"


def _parse_serial(code: str | None, prefix: str) -> int | None:
    raw = display_sales_bill_no(code) if prefix == _SALES_PREFIX else display_purchase_no(code)
    if not raw:
        return None
    if prefix and raw.upper().startswith(prefix.upper()):
        suffix = raw[len(prefix) :].lstrip("-_").strip()
    elif raw.isdigit():
        suffix = raw
    elif raw.upper().startswith("APU"):
        suffix = raw[3:].lstrip("-_")
    else:
        return None
    return int(suffix) if suffix.isdigit() else None


def _table_cols(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {str(r[1]) for r in cur.fetchall()}


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    )
    return cur.fetchone() is not None


def _has_fy_columns(cur: sqlite3.Cursor, table: str) -> bool:
    cols = _table_cols(cur, table)
    return "fy_start_year" in cols and "fy_serial" in cols


def _ensure_fy_columns(cur: sqlite3.Cursor, table: str) -> None:
    if not _table_exists(cur, table):
        return
    cols = _table_cols(cur, table)
    if "fy_start_year" not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN fy_start_year INTEGER")
    if "fy_serial" not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN fy_serial INTEGER")


def _saved_sales_filter(cur: sqlite3.Cursor) -> str:
    cols = _table_cols(cur, "sales")
    parts = ["COALESCE(is_autosave,0)=0"]
    if "deleted" in cols:
        parts.append("COALESCE(deleted,0)=0")
    return " AND ".join(parts)


def _saved_purchases_filter(cur: sqlite3.Cursor) -> str:
    cols = _table_cols(cur, "purchases")
    parts = ["COALESCE(is_autosave,0)=0"]
    if "deleted" in cols:
        parts.append("COALESCE(deleted,0)=0")
    return " AND ".join(parts)


def allocate_sales_bill_no(
    conn: sqlite3.Connection,
    bill_date: date | str | None = None,
    *,
    prefix: str = _SALES_PREFIX,
    exclude_sale_id: int | None = None,
) -> str:
    """Next SCB number for the FY of ``bill_date`` (default today)."""
    fy = fy_start_year_for_date(bill_date)
    fd, td = fy_date_bounds(fy)
    cur = conn.cursor()
    filt = _saved_sales_filter(cur)
    has_fy = _has_fy_columns(cur, "sales")
    if has_fy:
        where = f"{filt} AND (fy_start_year=? OR (bill_date>=? AND bill_date<=?))"
        params: list = [fy, fd, td]
    else:
        where = f"{filt} AND bill_date>=? AND bill_date<=?"
        params = [fd, td]
    extra = ""
    if exclude_sale_id is not None:
        extra = " AND id!=?"
        params.append(int(exclude_sale_id))

    max_serial = 0
    if has_fy:
        cur.execute(
            f"SELECT COALESCE(MAX(fy_serial),0) FROM sales WHERE {where}{extra}",
            params,
        )
        max_serial = int(cur.fetchone()[0] or 0)

    cur.execute(
        f"SELECT bill_no FROM sales WHERE {where}{extra}",
        params,
    )
    for (bno,) in cur.fetchall():
        n = _parse_serial(bno, prefix)
        if n is not None:
            max_serial = max(max_serial, n)

    return encode_sales_bill_no(max_serial + 1, fy, prefix=prefix)


def allocate_purchase_number(
    conn: sqlite3.Connection,
    purchase_date: date | str | None = None,
    *,
    prefix: str = "",
    exclude_purchase_id: int | None = None,
) -> str:
    """Next purchase serial for the FY of ``purchase_date`` (drafts: APU… global)."""
    prefix_u = (prefix or "").upper()
    if prefix_u == "APU":
        return _allocate_global_purchase(conn, prefix, exclude_purchase_id)

    fy = fy_start_year_for_date(purchase_date)
    fd, td = fy_date_bounds(fy)
    cur = conn.cursor()
    filt = _saved_purchases_filter(cur)
    has_fy = _has_fy_columns(cur, "purchases")
    if has_fy:
        where = f"{filt} AND (fy_start_year=? OR (purchase_date>=? AND purchase_date<=?))"
        params: list = [fy, fd, td]
    else:
        where = f"{filt} AND purchase_date>=? AND purchase_date<=?"
        params = [fd, td]
    extra = ""
    if exclude_purchase_id is not None:
        extra = " AND id!=?"
        params.append(int(exclude_purchase_id))

    max_serial = 0
    if has_fy:
        cur.execute(
            f"SELECT COALESCE(MAX(fy_serial),0) FROM purchases WHERE {where}{extra}",
            params,
        )
        max_serial = int(cur.fetchone()[0] or 0)

    cur.execute(
        f"SELECT purchase_no FROM purchases WHERE {where}{extra}",
        params,
    )
    for (pno,) in cur.fetchall():
        n = _parse_serial(pno, prefix)
        if n is not None:
            max_serial = max(max_serial, n)

    return encode_purchase_no(max_serial + 1, fy, prefix=prefix)


def _allocate_global_purchase(
    conn: sqlite3.Connection,
    prefix: str,
    exclude_purchase_id: int | None,
) -> str:
    cur = conn.cursor()
    cur.execute("SELECT id, purchase_no FROM purchases")
    max_num = 0
    prefix_u = prefix.upper()
    plen = len(prefix)
    for pid, purchase_no in cur.fetchall():
        if exclude_purchase_id is not None and int(pid) == int(exclude_purchase_id):
            continue
        raw = (purchase_no or "").strip()
        if not raw.upper().startswith(prefix_u):
            continue
        suffix = raw[plen:].lstrip("-_").split(_FY_SEP, 1)[0]
        if suffix.isdigit():
            max_num = max(max_num, int(suffix))
    return f"{prefix}{max_num + 1}"


def _allocate_global_bill(conn: sqlite3.Connection, prefix: str, exclude_id: int | None) -> str:
    cur = conn.cursor()
    cur.execute("SELECT id, bill_no FROM sales WHERE bill_no LIKE ?", (f"{prefix}%",))
    max_num = 0
    plen = len(prefix)
    for sid, bno in cur.fetchall():
        if exclude_id is not None and sid == exclude_id:
            continue
        raw = display_sales_bill_no(bno)
        if not raw.upper().startswith(prefix.upper()):
            continue
        suffix = raw[plen:].lstrip("-_")
        if suffix.isdigit():
            max_num = max(max_num, int(suffix))
    return f"{prefix}{max_num + 1}"


def allocate_bill_number(
    conn: sqlite3.Connection,
    prefix: str,
    *,
    bill_date: date | str | None = None,
    exclude_sale_id: int | None = None,
) -> str:
    if prefix == _SALES_PREFIX:
        return allocate_sales_bill_no(
            conn, bill_date, prefix=prefix, exclude_sale_id=exclude_sale_id
        )
    return _allocate_global_bill(conn, prefix, exclude_sale_id)


def migrate_fy_serial_numbers(conn: sqlite3.Connection) -> dict[str, int]:
    """One-time renumber saved sales/purchases per FY (safe — no table rebuild)."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE name=?", (_MIGRATION_KEY,))
    row = cur.fetchone()
    if row and str(row[0]) == "1":
        return {"sales": 0, "purchases": 0}

    if not _table_exists(cur, "settings"):
        return {"sales": 0, "purchases": 0}

    _ensure_fy_columns(cur, "sales")
    _ensure_fy_columns(cur, "purchases")

    sales_n = _renumber_sales(cur) if _table_exists(cur, "sales") else 0
    purch_n = _renumber_purchases(cur) if _table_exists(cur, "purchases") else 0

    cur.execute(
        "INSERT OR REPLACE INTO settings (name, value) VALUES (?, '1')",
        (_MIGRATION_KEY,),
    )
    conn.commit()
    if sales_n or purch_n:
        print(
            f"[MIGRATION] FY serial numbers: {sales_n} sales, {purch_n} purchases "
            f"(restart each 1 April)."
        )
    return {"sales": sales_n, "purchases": purch_n}


def _renumber_sales(cur: sqlite3.Cursor) -> int:
    filt = _saved_sales_filter(cur)
    cur.execute(
        f"""
        SELECT id, bill_date FROM sales
        WHERE {filt}
        ORDER BY bill_date ASC, id ASC
        """
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    cur.execute("UPDATE sales SET bill_no = 'TMP_' || id WHERE COALESCE(is_autosave,0)=0")

    by_fy: dict[int, list[int]] = {}
    for sid, bdate in rows:
        sid = int(sid)
        fy = fy_start_year_for_date(str(bdate or "")[:10] or None)
        by_fy.setdefault(fy, []).append(sid)

    count = 0
    for fy in sorted(by_fy):
        for seq, sid in enumerate(by_fy[fy], start=1):
            cur.execute(
                """
                UPDATE sales SET bill_no=?, fy_start_year=?, fy_serial=?
                WHERE id=?
                """,
                (encode_sales_bill_no(seq, fy), fy, seq, sid),
            )
            count += 1
    return count


def _renumber_purchases(cur: sqlite3.Cursor) -> int:
    filt = _saved_purchases_filter(cur)
    cur.execute(
        f"""
        SELECT id, purchase_date FROM purchases
        WHERE {filt}
        ORDER BY purchase_date ASC, id ASC
        """
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    cur.execute(
        "UPDATE purchases SET purchase_no = 'TMP_' || id WHERE COALESCE(is_autosave,0)=0"
    )

    by_fy: dict[int, list[int]] = {}
    for pid, pdate in rows:
        pid = int(pid)
        fy = fy_start_year_for_date(str(pdate or "")[:10] or None)
        by_fy.setdefault(fy, []).append(pid)

    count = 0
    for fy in sorted(by_fy):
        for seq, pid in enumerate(by_fy[fy], start=1):
            cur.execute(
                """
                UPDATE purchases SET purchase_no=?, fy_start_year=?, fy_serial=?
                WHERE id=?
                """,
                (encode_purchase_no(seq, fy), fy, seq, pid),
            )
            count += 1
    return count


def fy_start_year_in_code(code: str | None) -> int | None:
    """FY a bill/purchase number was issued under, read from its /FY tag."""
    raw = (code or "").strip()
    if _FY_SEP not in raw:
        return None
    tag = raw.split(_FY_SEP, 1)[1].strip()
    head = tag.split("-", 1)[0].strip()
    if not head.isdigit():
        return None
    year = int(head)
    if year < 100:            # "2526" style short tags
        return None
    return year


def resync_sale_fy_number(
    cur: sqlite3.Cursor,
    conn: sqlite3.Connection,
    sale_id: int,
    bill_no: str,
    bill_date: date | str | None,
) -> str:
    """Bill number for ``sale_id`` after its date may have moved to another FY.

    Editing a bill's date across 1 April used to keep the old number while
    stamping the new financial year on the row. FY2026-27 then contained a bill
    numbered 150, so the next new bill jumped from 4 to 151 and the year's
    series was ruined. Moving the bill to a new year now moves its number too,
    which is the only way both years stay contiguous.
    """
    target_fy = fy_start_year_for_date(bill_date)
    issued_fy = fy_start_year_in_code(bill_no)
    if issued_fy is None or issued_fy == target_fy:
        return bill_no
    fresh = allocate_sales_bill_no(conn, bill_date, exclude_sale_id=int(sale_id))
    cur.execute("UPDATE sales SET bill_no=? WHERE id=?", (fresh, int(sale_id)))
    return fresh


def resync_purchase_fy_number(
    cur: sqlite3.Cursor,
    conn: sqlite3.Connection,
    purchase_id: int,
    purchase_no: str,
    purchase_date: date | str | None,
) -> str:
    """Purchase number after its date may have moved to another FY. See above."""
    target_fy = fy_start_year_for_date(purchase_date)
    issued_fy = fy_start_year_in_code(purchase_no)
    if issued_fy is None or issued_fy == target_fy:
        return purchase_no
    fresh = allocate_purchase_number(
        conn, purchase_date, exclude_purchase_id=int(purchase_id)
    )
    cur.execute("UPDATE purchases SET purchase_no=? WHERE id=?", (fresh, int(purchase_id)))
    return fresh


def patch_sale_fy_fields(
    cur: sqlite3.Cursor,
    sale_id: int,
    bill_no: str,
    bill_date: date | str | None,
) -> None:
    if not _has_fy_columns(cur, "sales"):
        return
    fy = fy_start_year_for_date(bill_date)
    serial = _parse_serial(bill_no, _SALES_PREFIX) or 0
    cur.execute(
        "UPDATE sales SET fy_start_year=?, fy_serial=? WHERE id=?",
        (fy, serial, sale_id),
    )


def patch_purchase_fy_fields(
    cur: sqlite3.Cursor,
    purchase_id: int,
    purchase_no: str,
    purchase_date: date | str | None,
) -> None:
    if not _has_fy_columns(cur, "purchases"):
        return
    fy = fy_start_year_for_date(purchase_date)
    serial = _parse_serial(purchase_no, "") or 0
    cur.execute(
        "UPDATE purchases SET fy_start_year=?, fy_serial=? WHERE id=?",
        (fy, serial, purchase_id),
    )


def _hint_from_peek_payload(data: dict | None) -> str:
    if not isinstance(data, dict):
        return ""
    hint = display_sales_bill_no(data.get("display_bill_no"))
    if hint:
        return hint
    hint = display_sales_bill_no(data.get("bill_no"))
    if hint:
        return hint
    for key in ("fy_serial", "serial", "next_serial"):
        try:
            n = int(data.get(key) or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            return f"{_SALES_PREFIX}{n}"
    return ""


def next_sales_bill_hint(
    conn: sqlite3.Connection, bill_date: date | str | None = None
) -> str:
    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import server_api as api

            token = api.store_token_for_active()
            if token:
                raw = bill_date or date.today()
                date_s = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)[:10]
                if not (date_s[:4].isdigit() and 2000 <= int(date_s[:4]) <= 2100):
                    date_s = date.today().isoformat()     # a year still being typed ("0020")
                data = api.peek_fy(token, "sales", date_s, timeout=4.0) or {}
                hint = _hint_from_peek_payload(data)
                if hint:
                    return hint
    except Exception:
        pass
    try:
        stored = allocate_sales_bill_no(conn, bill_date)
        hint = display_sales_bill_no(stored)
        if hint:
            return hint
    except Exception:
        pass
    return f"{_SALES_PREFIX}1"
