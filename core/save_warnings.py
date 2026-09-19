"""Checks at save that WARN and never stop the save.

The 2026-09-13 store audit found bills, purchases and payments saved exactly as typed that
were almost certainly mistakes: the same Rs 210 entered as both cash and online on a Rs 210
bill; one supplier bill entered twice a second apart; purchase lines rated above their MRP,
with MRP 0, already expired on the purchase date, with no expiry, or still carrying the
import's WITHOUT BATCH placeholder; Syrup lines on Tablet medicines, which count stock a
different way; payments dated a year ahead.

Each check only says what looks wrong. The save goes ahead either way -- billing is never
held up -- and an ordinary bill, purchase or payment produces no warning at all.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

PLACEHOLDER_BATCH = "WITHOUT BATCH"
PLACEHOLDER_EXPIRY = "WITHOUT EXP"
_MONEY = 0.01
_SHOWN = 5


def _f(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out == out else 0.0


def _day(raw: Any) -> Optional[date]:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip().replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _listed(names: Iterable[str]) -> str:
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    shown = ", ".join(seen[:_SHOWN])
    more = len(seen) - _SHOWN
    return f"{shown} and {more} more" if more > 0 else shown


def sale_warnings(*, total: Any, cash_paid: Any, online_paid: Any, previous_due: Any) -> list[str]:
    """Paid more than this bill and the customer's old due together."""
    paid = round(_f(cash_paid) + _f(online_paid), 2)
    bill = round(max(0.0, _f(total)), 2)
    old = round(max(0.0, _f(previous_due)), 2)
    if paid <= bill + old + _MONEY:
        return []
    extra = round(paid - bill - old, 2)
    return [
        f"Paid ₹{paid:.2f} is ₹{extra:.2f} more than this bill (₹{bill:.2f}) and the old due "
        f"(₹{old:.2f}) together. The extra is kept as the customer's credit. Check that the "
        "same money was not entered as both cash and online, and that the cash handed over "
        "was not typed in place of the bill amount."
    ]


def payment_warnings(payment_date: Any, *, today: Optional[date] = None) -> list[str]:
    """A payment dated after today."""
    day = _day(payment_date)
    now = today or date.today()
    if day is None or day <= now:
        return []
    return [
        f"This payment is dated {day.isoformat()}, after today ({now.isoformat()}). "
        "Check the date, especially the year."
    ]


def _expiry_month(raw: Any) -> Optional[tuple[int, int]]:
    text = str(raw or "").strip()
    if not text or text.upper() == PLACEHOLDER_EXPIRY:
        return None
    found = re.match(r"^(\d{4})-(\d{1,2})", text)
    if found:
        return int(found.group(1)), int(found.group(2))
    found = re.match(r"^(\d{1,2})\s*/\s*(\d{2}|\d{4})$", text)
    if found:
        year = int(found.group(2))
        return (year + 2000 if year < 100 else year), int(found.group(1))
    return None


def _norm_type(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def purchase_line_warnings(
    items: Iterable[dict],
    purchase_date: Any,
    *,
    medicine_type: Optional[Callable[[dict], str]] = None,
) -> list[str]:
    """Purchase lines that look mistyped: rate above MRP or MRP 0, expiry blank or before the
    purchase date, no batch, or a type other than the medicine's own."""
    bought = _day(purchase_date)
    above: list[str] = []
    no_mrp: list[str] = []
    expired: list[str] = []
    no_expiry: list[str] = []
    no_batch: list[str] = []
    other_type: list[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("medicine_name") or "").strip().upper() or "A LINE"
        rate, mrp = _f(it.get("rate")), _f(it.get("mrp"))
        if mrp <= 0:
            no_mrp.append(name)
        elif rate > mrp + _MONEY:
            above.append(f"{name} (rate ₹{rate:.2f}, MRP ₹{mrp:.2f})")
        raw_expiry = it.get("expiry") if it.get("expiry") not in (None, "") else it.get("expiry_date")
        expiry_text = str(raw_expiry or "").strip()
        if not expiry_text or expiry_text.upper() == PLACEHOLDER_EXPIRY:
            no_expiry.append(name)
        else:
            month = _expiry_month(expiry_text)
            if month and bought and month < (bought.year, bought.month):
                expired.append(f"{name} ({expiry_text})")
        batch = str(it.get("batch") or it.get("batch_no") or "").strip().upper()
        if not batch or batch == PLACEHOLDER_BATCH:
            no_batch.append(name)
        if medicine_type is not None:
            line_type = str(it.get("type") or "").strip()
            try:
                master = str(medicine_type(it) or "").strip()
            except Exception:
                master = ""
            if line_type and master and _norm_type(line_type) != _norm_type(master):
                other_type.append(f"{name} ({line_type} on this bill, {master} in stock)")
    out: list[str] = []
    if above:
        out.append(f"Rate is above MRP on: {_listed(above)}.")
    if no_mrp:
        out.append(f"MRP is 0 on: {_listed(no_mrp)}.")
    if expired:
        out.append(f"Already expired on the purchase date: {_listed(expired)}.")
    if no_expiry:
        out.append(f"No expiry date on: {_listed(no_expiry)}.")
    if no_batch:
        out.append(f"No batch number (WITHOUT BATCH) on: {_listed(no_batch)}.")
    if other_type:
        out.append(
            "Type differs from the medicine in stock, which counts its stock by its own type: "
            f"{_listed(other_type)}."
        )
    return out


def medicine_type_lookup(conn) -> Callable[[dict], str]:
    """How a purchase line's medicine type is read: Online from the catalogue, else the shop's table."""

    def lookup(item: dict) -> str:
        try:
            mid = int(item.get("medicine_id") or item.get("id") or 0)
        except (TypeError, ValueError):
            return ""
        if mid <= 0:
            return ""
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_catalog import medicine_by_id

                return str((medicine_by_id(mid) or {}).get("type") or "")
            if conn is None:
                return ""
            row = conn.execute(
                "SELECT type FROM medicines WHERE id=? AND COALESCE(deleted,0)=0", (mid,)
            ).fetchone()
            return str(row[0] or "") if row else ""
        except Exception:
            return ""

    return lookup


def duplicate_supplier_bill_warnings(
    conn,
    *,
    supplier_id: Any = 0,
    supplier_name: str = "",
    bill_number: str = "",
    exclude_purchase_id: Any = 0,
) -> list[str]:
    """The same supplier's bill number already saved on another purchase.

    Asked before a new purchase is saved (after it, the purchase would find itself). A store
    that cannot be asked gives no warning.
    """
    bill = str(bill_number or "").strip()
    if not bill:
        return []
    key = bill.upper()
    try:
        sid = int(supplier_id or 0)
        exclude = int(exclude_purchase_id or 0)
    except (TypeError, ValueError):
        sid, exclude = 0, 0
    sname = str(supplier_name or "").strip().upper()
    held: list[str] = []

    def same_supplier(row_sid: Any, row_name: Any) -> bool:
        try:
            if sid and int(row_sid or 0) == sid:
                return True
        except (TypeError, ValueError):
            pass
        return bool(sname) and str(row_name or "").strip().upper() == sname

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            from core import store_query_client as sq

            found = sq.list_purchases(
                q=bill, from_date="2000-01-01", to_date="2099-12-31", limit=200,
                include_total=False,
            ) or {}
            for row in found.get("rows") or []:
                if not isinstance(row, dict) or row.get("deleted") or row.get("is_autosave"):
                    continue
                if str(row.get("bill_number") or "").strip().upper() != key:
                    continue
                rid = int(row.get("id") or row.get("local_id") or 0)
                if exclude and rid == exclude:
                    continue
                if same_supplier(row.get("supplier_id"), row.get("supplier_name")):
                    held.append(str(row.get("purchase_no") or rid))
        elif conn is not None:
            rows = conn.execute(
                "SELECT p.id, COALESCE(p.purchase_no,''), p.supplier_id, COALESCE(s.name,'') "
                "FROM purchases p LEFT JOIN suppliers s ON s.id = p.supplier_id "
                "WHERE UPPER(TRIM(COALESCE(p.bill_number,''))) = ? "
                "AND COALESCE(p.deleted,0)=0 AND COALESCE(p.is_autosave,0)=0",
                (key,),
            ).fetchall()
            for rid, number, row_sid, row_name in rows:
                if exclude and int(rid) == exclude:
                    continue
                if same_supplier(row_sid, row_name):
                    held.append(str(number or rid))
    except Exception:
        return []
    if not held:
        return []
    return [
        f"Supplier bill {bill} from this supplier is already saved as purchase "
        f"{_listed(held)}. Check that the same bill is not being entered twice."
    ]
