"""Expired / Near Expiry grouped by supplier (owner, 19 Oct 2026).

"I want expired and near-expiry exports supplier-wise" -- the shop sends its expiries back
to whoever sold them, so the list is cut into one block per supplier, each with its count
and what the batches are worth at the purchase rate and at MRP.

A batch's supplier is the one on the purchase bill that brought it (then the supplier
written on the shelf row by opening stock / import, then the row's newest bill -- see
core/alert_monitoring_service._batch_source). A batch nobody can trace goes under
"Unknown supplier", last.

Every figure here comes from the rows the alert lists already carry; nothing is re-read.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

UNKNOWN_SUPPLIER = "Unknown supplier"

# Column names (startup popup and Alert & Monitoring use the same ones).
SUPPLIER_COLUMNS = ("Supplier", "Supplier Name")
QTY_COLUMNS = ("Qty", "Quantity Expired", "Available Qty")
VALUE_RATE = "Value (P.Rate)"
VALUE_MRP = "Value (MRP)"

# "Batch Count" (not "Batches"): the report TOTAL line adds a column whose name says count.
SUMMARY_COLUMNS = ["Supplier", "Batch Count", "Qty", VALUE_RATE, VALUE_MRP]


def supplier_label(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return text or UNKNOWN_SUPPLIER


def _index(columns: Sequence[str], names: Sequence[str]) -> int:
    for n in names:
        if n in columns:
            return list(columns).index(n)
    return -1


def supplier_column(columns: Sequence[str], preferred: str = "") -> int:
    """Where the supplier is in a list's columns (-1 when it has none)."""
    if preferred and preferred in columns:
        return list(columns).index(preferred)
    return _index(columns, SUPPLIER_COLUMNS)


def _num(v: Any) -> float:
    try:
        return float(str(v if v is not None else "").replace(",", "").replace("₹", "").strip() or 0)
    except ValueError:
        return 0.0


def _sort_key(name: str) -> Tuple[int, str]:
    return (1 if name == UNKNOWN_SUPPLIER else 0, name.casefold())


def group_by_supplier(columns: Sequence[str], rows: Sequence[Sequence[Any]],
                      column: str = "") -> List[Dict[str, Any]]:
    """The rows cut by supplier, A-Z with "Unknown supplier" last.

    Each group: supplier, rows (in list order), count, qty, value_rate, value_mrp."""
    cols = [str(c) for c in columns]
    at = supplier_column(cols, column)
    qty_at = _index(cols, QTY_COLUMNS)
    vr_at = cols.index(VALUE_RATE) if VALUE_RATE in cols else -1
    vm_at = cols.index(VALUE_MRP) if VALUE_MRP in cols else -1
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows or []:
        row = list(r)
        name = supplier_label(row[at] if 0 <= at < len(row) else "")
        g = groups.setdefault(name, {"supplier": name, "rows": [], "count": 0, "qty": 0.0,
                                     "value_rate": 0.0, "value_mrp": 0.0})
        g["rows"].append(row)
        g["count"] += 1
        if 0 <= qty_at < len(row):
            g["qty"] += _num(row[qty_at])
        if 0 <= vr_at < len(row):
            g["value_rate"] += _num(row[vr_at])
        if 0 <= vm_at < len(row):
            g["value_mrp"] += _num(row[vm_at])
    out = sorted(groups.values(), key=lambda g: _sort_key(g["supplier"]))
    for g in out:
        g["qty"] = int(g["qty"]) if g["qty"] == int(g["qty"]) else round(g["qty"], 2)
        g["value_rate"] = round(g["value_rate"], 2)
        g["value_mrp"] = round(g["value_mrp"], 2)
    return out


def _money(v: float) -> str:
    return f"{v:,.2f}"


def group_heading(title: str, group: Dict[str, Any], has_values: bool) -> str:
    """"Expired -- CIPLA DISTRIBUTORS: 4 batches, Rs 1,240.00 at purchase rate, Rs 1,980.00 MRP"."""
    n = group["count"]
    text = f"{title} — {group['supplier']}: {n} batch{'es' if n != 1 else ''}"
    if has_values:
        text += (f", ₹{_money(group['value_rate'])} at purchase rate,"
                 f" ₹{_money(group['value_mrp'])} at MRP")
    return text


def summary_rows(groups: Sequence[Dict[str, Any]]) -> List[List[Any]]:
    return [[g["supplier"], g["count"], g["qty"], g["value_rate"], g["value_mrp"]] for g in groups]


def split_section(title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]],
                  column: str = "", summary: bool = True
                  ) -> List[Tuple[str, List[str], List[List[Any]]]]:
    """One alert list -> [summary by supplier] + one section per supplier.

    The supplier column is left out of each supplier's own table (its heading names it)."""
    cols = [str(c) for c in columns]
    at = supplier_column(cols, column)
    if at < 0:
        return [(title, cols, [list(r) for r in rows])]
    groups = group_by_supplier(cols, rows, column)
    has_values = VALUE_RATE in cols or VALUE_MRP in cols
    keep = [i for i in range(len(cols)) if i != at]
    out: List[Tuple[str, List[str], List[List[Any]]]] = []
    if summary and groups:
        out.append((f"{title} — by supplier", list(SUMMARY_COLUMNS), summary_rows(groups)))
    for g in groups:
        out.append((group_heading(title, g, has_values), [cols[i] for i in keep],
                    [[r[i] if i < len(r) else "" for i in keep] for r in g["rows"]]))
    return out


def file_slug(text: str, default: str = "supplier") -> str:
    """A supplier / list name as part of a file name."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    return slug[:60] or default


def supplier_files(title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]],
                   column: str = "") -> List[Tuple[str, str, str, List[str], List[List[Any]]]]:
    """One file per supplier: (file stem, document title, heading, columns, rows) -- the
    supplier's own list only, no summary."""
    out: List[Tuple[str, str, str, List[str], List[List[Any]]]] = []
    cols = [str(c) for c in columns]
    if supplier_column(cols, column) < 0:
        return out
    for g in group_by_supplier(cols, rows, column):
        heading, sec_cols, sec_rows = split_section(title, cols, g["rows"], column, summary=False)[0]
        out.append((f"{file_slug(title, 'alerts')}_{file_slug(g['supplier'])}",
                    f"{title} — {g['supplier']}", heading, sec_cols, sec_rows))
    return out


def group_for(section: Dict[str, Any], default: Optional[str] = None) -> str:
    """The column a section asks to be grouped by ('' = not grouped)."""
    g = section.get("group_by") if isinstance(section, dict) else None
    if g is None:
        g = default
    if g in (True, "supplier", "Supplier"):
        return "supplier"
    return str(g or "")
