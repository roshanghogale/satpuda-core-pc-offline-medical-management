"""Rules for showing/hiding medicine batches across inventory, sales, and alerts."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple

Row = Sequence[Any]


def _stock_qty(row: Row) -> float:
    return float(row[4] if len(row) > 4 else 0)


def _medicine_name(row: Row) -> str:
    return str(row[0] or "")


def _medicine_id(row: Row) -> int:
    return int(row[-1])


def compute_name_stock_totals(rows: Iterable[Row]) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for row in rows:
        name = _medicine_name(row)
        totals[name] = totals.get(name, 0.0) + _stock_qty(row)
    return totals


def compute_oos_anchor_ids(rows: Iterable[Row]) -> Dict[str, int]:
    """
    When every batch of a medicine name has zero stock, keep the newest batch
    (highest id) visible in inventory until the user removes it or restocks.
    """
    by_name: Dict[str, int] = {}
    name_totals = compute_name_stock_totals(rows)
    for row in rows:
        name = _medicine_name(row)
        if name_totals.get(name, 0) > 0:
            continue
        med_id = _medicine_id(row)
        prev = by_name.get(name)
        if prev is None or med_id > prev:
            by_name[name] = med_id
    return by_name


def should_hide_depleted_batch(
    row: Row,
    name_totals: Mapping[str, float],
    anchor_ids: Mapping[str, int],
) -> bool:
    """
    Hide a zero-stock batch when:
    - another batch of the same medicine still has stock, or
    - all batches are zero but this is not the anchor (newest) batch.
    """
    if _stock_qty(row) > 0:
        return False
    name = _medicine_name(row)
    if float(name_totals.get(name, 0) or 0) > 0:
        return True
    anchor_id = anchor_ids.get(name)
    if anchor_id is None:
        return False
    return _medicine_id(row) != int(anchor_id)


def medicine_names_fully_out_of_stock(name_totals: Mapping[str, float]) -> Set[str]:
    return {name for name, total in name_totals.items() if float(total or 0) <= 0}


def parse_expiry_as_date(raw: str) -> Optional[date]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        from datetime import datetime

        from core.alert_thresholds import parse_expiry

        dt = parse_expiry(text)
        if dt is None:
            return None
        if isinstance(dt, datetime):
            return dt.date()
        if isinstance(dt, date):
            return dt
        return None
    except Exception:
        return None


def expiry_cutoff(expiry_raw: str) -> Optional[date]:
    """Last day a batch may still be sold.

    Pharmacy expiry is written per MONTH ("09/26"), and a 09/26 batch is good
    until the 30th of September, not the 1st. parse_expiry lands such a value on
    day 01, so comparing against it directly wrote off the whole final month:
    stock vanished from the picker and from Inventory up to 30 days early, and
    the same batch could not be billed on a back-dated bill either.

    parse_expiry itself is left alone on purpose -- near-expiry "days left"
    arithmetic feeds off it, and shifting every date a month later would quietly
    change how many alerts a store sees.
    """
    exp = parse_expiry_as_date(expiry_raw)
    if not exp:
        return None
    if exp.day != 1:
        # A full date was given; take it at face value.
        return exp
    from calendar import monthrange

    return exp.replace(day=monthrange(exp.year, exp.month)[1])


def is_expired_as_of(expiry_raw: str, as_of: date) -> bool:
    cutoff = expiry_cutoff(expiry_raw)
    if not cutoff:
        return False
    # Strictly greater: the batch is saleable ON its cutoff day.
    return as_of > cutoff


def parse_bill_as_of(raw: Any) -> date:
    """Normalize bill date for sales-as-of filters."""
    from datetime import datetime

    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    s = str(raw or "").strip()
    if not s:
        return date.today()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "")[:19]).date()
    except Exception:
        return date.today()


def _parse_created_date(raw: Any) -> Optional[date]:
    from datetime import datetime

    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "")[:19]).date()
    except Exception:
        return None


def _medicine_added_date(
    created_at_raw: Any = None,
    *,
    first_purchase_raw: Any = None,
) -> Optional[date]:
    """
    Earliest known date a batch entered inventory.
    Prefer first purchase date over created_at (sync backfill often stamps today).
    """
    dates: list[date] = []
    for raw in (first_purchase_raw, created_at_raw):
        d = _parse_created_date(raw)
        if d is not None:
            dates.append(d)
    if not dates:
        return None
    return min(dates)


def medicine_existed_as_of(
    created_at_raw: Any,
    as_of: date,
    *,
    first_purchase_raw: Any = None,
) -> bool:
    """
    True when this medicine batch existed on or before the bill date.
    Uses first purchase date when available; legacy rows with no dates pass.
    """
    added = _medicine_added_date(
        created_at_raw,
        first_purchase_raw=first_purchase_raw,
    )
    if added is None:
        return True
    return added <= as_of


def first_purchase_date_sql(alias: str = "") -> str:
    """SQL expression: the earliest live purchase date of a batch, or NULL.

    A deleted purchase or an unfinished purchase draft never brought the batch in.
    """
    id_col = f"{alias}.id" if alias else "medicines.id"
    return f"""(SELECT MIN(p.purchase_date)
         FROM purchase_items pi
         JOIN purchases p ON p.id = pi.purchase_id
         WHERE pi.medicine_id = {id_col}
           AND COALESCE(p.deleted, 0) = 0
           AND COALESCE(p.is_autosave, 0) = 0)"""


def medicine_added_date_sql(alias: str = "") -> str:
    """SQL expression for the earliest date a batch entered inventory.

    The EARLIER of its first purchase and its created_at, exactly as
    _medicine_added_date takes it. COALESCE(first purchase, created_at) preferred
    the purchase, so a batch taken in as opening stock in January and restocked
    in May was hidden from a March bill. min() of SQLite is NULL when either side
    is, so each side is tried on its own after it.
    """
    created_col = f"{alias}.created_at" if alias else "medicines.created_at"
    first = f"date({first_purchase_date_sql(alias)})"
    created = f"date(NULLIF(TRIM(CAST({created_col} AS TEXT)), ''))"
    return f"""COALESCE(
        min({first}, {created}),
        {first},
        {created},
        '1970-01-01'
    )"""


def medicine_existed_sql(alias: str = "") -> str:
    """SQL fragment; bind one bill-date param (YYYY-MM-DD)."""
    return f"{medicine_added_date_sql(alias)} <= date(?)"


def fetch_first_purchase_date(conn, medicine_id: int) -> Any:
    """Earliest live purchase date for a medicine batch, or None."""
    try:
        row = conn.execute(
            """
            SELECT MIN(p.purchase_date)
            FROM purchase_items pi
            JOIN purchases p ON p.id = pi.purchase_id
            WHERE pi.medicine_id = ?
              AND COALESCE(p.deleted, 0) = 0
              AND COALESCE(p.is_autosave, 0) = 0
            """,
            (int(medicine_id),),
        ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
