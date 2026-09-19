"""
Centralized alert threshold settings and comparison helpers.

Settings -> Layout & Lists -> Thresholds stores values in the settings table.
Alert and Monitoring and the startup alert popup both read from here.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Tuple


def parse_expiry(raw: Any):
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%y", "%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            if fmt in ("%m/%y", "%m/%Y"):
                if parsed.month == 12:
                    return parsed.replace(day=31)
                nxt = parsed.replace(month=parsed.month + 1, day=1)
                return nxt.fromordinal(nxt.toordinal() - 1)
            return parsed
        except Exception:
            continue
    return None


def load_thresholds(conn) -> Tuple[Dict[str, float], Dict[str, float]]:
    low_defaults: Dict[str, float] = {}
    near_defaults: Dict[str, float] = {}
    cur = conn.cursor()
    try:
        cur.execute("SELECT name, value FROM settings")
        for name, value in cur.fetchall():
            if name.startswith("low_stock_"):
                med_type = name.replace("low_stock_", "", 1).lower()
                try:
                    low_defaults[med_type] = float(value)
                except Exception:
                    low_defaults[med_type] = 10.0
            elif name.startswith("near_expiry_"):
                med_type = name.replace("near_expiry_", "", 1).lower()
                try:
                    near_defaults[med_type] = float(value)
                except Exception:
                    near_defaults[med_type] = 3.0
    except Exception:
        pass
    return low_defaults, near_defaults


def _read_setting(conn, name: str, default: float) -> float:
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM settings WHERE name=?", (name,))
        row = cur.fetchone()
        if row and row[0] is not None and str(row[0]).strip() != "":
            return float(row[0])
    except Exception:
        pass
    return default


def load_due_alert_settings(conn) -> Dict[str, float]:
    return {
        "min_amount": max(0.0, _read_setting(conn, "customer_due_min_amount", 0.0)),
        "min_days": max(0.0, _read_setting(conn, "customer_due_min_days", 0.0)),
    }


def alert_expiry_date(raw: Any):
    """The last day a batch may be sold -- the date every alert judges expiry by.

    A month-only expiry (MM/YY, or the YYYY-MM-01 the phone stores for it) runs to
    the month end: batch_visibility.expiry_cutoff, which billing and "Remove All
    Expired" already use. Judging the alerts on the raw 1st listed a batch as
    Expired for the last weeks it was still on sale, and Remove All Expired then
    left those very rows sitting in the Expired list.
    """
    from core.batch_visibility import expiry_cutoff

    return expiry_cutoff(str(raw or ""))


def near_expiry_window_days(med_type: str, near_thr: Dict[str, float]) -> int:
    # Same type normalisation is_low_stock_qty applies: a "Drop" batch is judged
    # by the shop's Drops months, not the 3-month default.
    from core.layout_config import normalize_med_type_name

    months = near_thr.get(normalize_med_type_name(med_type or "").lower(), 3.0)
    return int(months * 30)


def is_near_expiry(days_left: int, med_type: str, near_thr: Dict[str, float]) -> bool:
    if days_left < 0:
        return False
    return days_left <= near_expiry_window_days(med_type, near_thr)


def is_low_stock_qty(
    qty: float,
    med_type: str,
    low_thr: Dict[str, float],
    cur,
    unit: str | None = None,
) -> bool:
    """True when stock is above zero and below the type threshold (same units as inventory stock column)."""
    from core.layout_config import normalize_med_type_name

    mtype = normalize_med_type_name(med_type or "").lower()
    threshold = low_thr.get(mtype, 10.0)
    qty = float(qty or 0)
    return 0 < qty < threshold

def bill_due_days(bill_date: Any, today: date | None = None) -> int:
    today = today or date.today()
    try:
        if bill_date:
            bd = date.fromisoformat(str(bill_date)[:10])
            return max(0, (today - bd).days)
    except Exception:
        pass
    return 0


def passes_due_alert_filter(due_amount: float, due_days: int, settings: Dict[str, float]) -> bool:
    if float(due_amount or 0) < settings.get("min_amount", 0.0):
        return False
    if int(due_days or 0) < int(settings.get("min_days", 0)):
        return False
    return float(due_amount or 0) > 0
