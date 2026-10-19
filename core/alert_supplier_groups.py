"""Alert lists grouped for export: by supplier, or (Customer Dues) by village / area.

Owner, 19 Oct 2026: "I want expired and near-expiry exports supplier-wise" -- the shop sends
its expiries back to whoever sold them -- and then "these options should be in each Alert &
Monitoring list according to their requirements":

  * Expired / Near Expiry: by the supplier the batch came from (the purchase bill that brought
    it, then the supplier written on the shelf row by opening stock / import, then the row's
    newest bill -- core/alert_monitoring_service._batch_source); count, qty, and what the
    batches are worth at the purchase rate and at MRP.
  * Low Stock / Out of Stock: by the supplier the medicine was LAST bought from (the newest
    purchase line of that medicine, else the shelf row's supplier -- _last_bought_from), so
    the shop can reorder supplier by supplier; count of medicines.
  * Customer Dues: by the customer's village / area (customers.address -- the field Contacts
    and Sales keep as the village); customers, bills and the total due.

A row nobody can place goes under "Unknown supplier" / "Unknown area", last. Names are
grouped without regard to case or extra spaces ("Shirpur" and "SHIRPUR " are one area).

Every figure here comes from the rows the alert lists already carry; nothing is re-read.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

UNKNOWN_SUPPLIER = "Unknown supplier"
UNKNOWN_AREA = "Unknown area"

# Column names (startup popup and Alert & Monitoring use the same ones).
SUPPLIER_COLUMNS = ("Supplier", "Supplier Name")
AREA_COLUMNS = ("Area", "Village / Area", "Village")
QTY_COLUMNS = ("Qty", "Quantity Expired", "Available Qty")
NAME_COLUMNS = ("Medicine", "Medicine Name", "Customer", "Customer Name")
BILL_COLUMNS = ("Bill Number", "Bill No")
# A list whose rows are batches on their way back (Expired / Near Expiry).
_BATCH_LIST_COLUMNS = ("Days Expired", "Days Left", "Remaining Days", "Quantity Expired")
DUE_COLUMN = "Due Amount"
VALUE_RATE = "Value (P.Rate)"
VALUE_MRP = "Value (MRP)"

# "Batch Count" (not "Batches"): the report TOTAL line adds a column whose name says count.
SUMMARY_COLUMNS = ["Supplier", "Batch Count", "Qty", VALUE_RATE, VALUE_MRP]

KINDS: Dict[str, Dict[str, Any]] = {
    "supplier": {"columns": SUPPLIER_COLUMNS, "unknown": UNKNOWN_SUPPLIER},
    "area": {"columns": AREA_COLUMNS, "unknown": UNKNOWN_AREA},
}


def _kind(kind: str) -> Dict[str, Any]:
    return KINDS.get(kind) or KINDS["supplier"]


def group_label(value: Any, kind: str = "supplier") -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text or _kind(kind)["unknown"]


def supplier_label(value: Any) -> str:
    return group_label(value, "supplier")


def _index(columns: Sequence[str], names: Sequence[str]) -> int:
    for n in names:
        if n in columns:
            return list(columns).index(n)
    return -1


def group_column(columns: Sequence[str], kind: str = "supplier", preferred: str = "") -> int:
    """Where a list's group column is (-1 when it has none)."""
    if preferred and preferred in columns:
        return list(columns).index(preferred)
    return _index(columns, _kind(kind)["columns"])


def supplier_column(columns: Sequence[str], preferred: str = "") -> int:
    """Where the supplier is in a list's columns (-1 when it has none)."""
    return group_column(columns, "supplier", preferred)


def _num(v: Any) -> float:
    try:
        return float(str(v if v is not None else "").replace(",", "").replace("₹", "").strip() or 0)
    except ValueError:
        return 0.0


def _sort_key(name: str, kind: str = "supplier") -> Tuple[int, str]:
    return (1 if name == _kind(kind)["unknown"] else 0, name.casefold())


def is_batch_list(columns: Sequence[str]) -> bool:
    """Expired / Near Expiry (rows are batches, counted as batches); else rows are medicines
    (Low / Out of Stock) or bills / customers (Dues)."""
    cols = list(columns)
    return VALUE_RATE in cols or VALUE_MRP in cols or any(c in cols for c in _BATCH_LIST_COLUMNS)


def group_rows(columns: Sequence[str], rows: Sequence[Sequence[Any]], kind: str = "supplier",
               column: str = "") -> List[Dict[str, Any]]:
    """The rows cut by supplier / area, A-Z with the unknown group last.

    Each group: name (and "supplier", the same, for the expiry callers), kind, rows (in list
    order), count (rows), names (distinct medicines / customers), bills (distinct bill
    numbers), qty, value_rate, value_mrp, due."""
    cols = [str(c) for c in columns]
    at = group_column(cols, kind, column)
    qty_at = _index(cols, QTY_COLUMNS)
    name_at = _index(cols, NAME_COLUMNS)
    bill_at = _index(cols, BILL_COLUMNS)
    vr_at = cols.index(VALUE_RATE) if VALUE_RATE in cols else -1
    vm_at = cols.index(VALUE_MRP) if VALUE_MRP in cols else -1
    due_at = cols.index(DUE_COLUMN) if DUE_COLUMN in cols else -1
    groups: Dict[str, Dict[str, Any]] = {}
    seen_names: Dict[str, set] = {}
    seen_bills: Dict[str, set] = {}
    for r in rows or []:
        row = list(r)
        label = group_label(row[at] if 0 <= at < len(row) else "", kind)
        key = label.casefold()
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"name": label, "supplier": label, "kind": kind, "rows": [], "count": 0,
                               "names": 0, "bills": 0, "qty": 0.0, "value_rate": 0.0,
                               "value_mrp": 0.0, "due": 0.0}
            seen_names[key] = set()
            seen_bills[key] = set()
        g["rows"].append(row)
        g["count"] += 1
        nm = str(row[name_at] if 0 <= name_at < len(row) else "").strip().casefold()
        if nm and nm not in seen_names[key]:
            seen_names[key].add(nm)
            g["names"] += 1
        bill = str(row[bill_at] if 0 <= bill_at < len(row) else "").strip()
        if bill and bill not in seen_bills[key]:
            seen_bills[key].add(bill)
            g["bills"] += 1
        if 0 <= qty_at < len(row):
            g["qty"] += _num(row[qty_at])
        if 0 <= vr_at < len(row):
            g["value_rate"] += _num(row[vr_at])
        if 0 <= vm_at < len(row):
            g["value_mrp"] += _num(row[vm_at])
        if 0 <= due_at < len(row):
            g["due"] += _num(row[due_at])
    out = sorted(groups.values(), key=lambda g: _sort_key(g["name"], kind))
    for g in out:
        g["qty"] = int(g["qty"]) if g["qty"] == int(g["qty"]) else round(g["qty"], 2)
        g["value_rate"] = round(g["value_rate"], 2)
        g["value_mrp"] = round(g["value_mrp"], 2)
        g["due"] = round(g["due"], 2)
    return out


def group_by_supplier(columns: Sequence[str], rows: Sequence[Sequence[Any]],
                      column: str = "") -> List[Dict[str, Any]]:
    """The rows cut by supplier, A-Z with "Unknown supplier" last (group_rows)."""
    return group_rows(columns, rows, "supplier", column)


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def group_heading(title: str, group: Dict[str, Any], has_values: bool,
                  columns: Sequence[str] = ()) -> str:
    """"Expired -- CIPLA DISTRIBUTORS: 4 batches, Rs 1,240.00 at purchase rate, Rs 1,980.00 MRP",
    "Low Stock -- CIPLA DISTRIBUTORS: 6 medicines", "Customer Dues -- SHIRPUR: 3 customers,
    5 bills, Rs 2,300.00 due"."""
    kind = group.get("kind") or "supplier"
    name = group.get("name") or group.get("supplier")
    if kind == "area":
        text = f"{title} — {name}: {_plural(group['names'] or group['count'], 'customer')}"
        if group.get("bills"):
            text += f", {_plural(group['bills'], 'bill')}"
        return text + f", ₹{_money(group['due'])} due"
    if not columns or is_batch_list(columns):
        n = group["count"]
        text = f"{title} — {name}: {_plural(n, 'batch', 'batches')}"
        if has_values:
            text += (f", ₹{_money(group['value_rate'])} at purchase rate,"
                     f" ₹{_money(group['value_mrp'])} at MRP")
        return text
    return f"{title} — {name}: {_plural(group['names'] or group['count'], 'medicine')}"


def summary_columns(columns: Sequence[str], kind: str = "supplier") -> List[str]:
    """The columns of a grouped list's first table, one row per supplier / area."""
    cols = list(columns)
    if kind == "area":
        out = ["Area", "Customer Count"]
        if _index(cols, BILL_COLUMNS) >= 0:
            out.append("Bill Count")
        return out + [DUE_COLUMN]
    if is_batch_list(cols):
        return list(SUMMARY_COLUMNS)
    return ["Supplier", "Medicine Count"]


def summary_rows(groups: Sequence[Dict[str, Any]], columns: Sequence[str] = (),
                 kind: str = "supplier") -> List[List[Any]]:
    if kind == "area":
        has_bills = _index(list(columns), BILL_COLUMNS) >= 0
        return [[g["name"], g["names"] or g["count"]] + ([g["bills"]] if has_bills else []) + [g["due"]]
                for g in groups]
    if not columns or is_batch_list(columns):
        return [[g["supplier"], g["count"], g["qty"], g["value_rate"], g["value_mrp"]] for g in groups]
    return [[g["name"], g["names"] or g["count"]] for g in groups]


def split_section(title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]],
                  column: str = "", summary: bool = True, kind: str = "supplier"
                  ) -> List[Tuple[str, List[str], List[List[Any]]]]:
    """One alert list -> [summary by supplier / area] + one section per group.

    The group column is left out of each group's own table (its heading names it)."""
    cols = [str(c) for c in columns]
    at = group_column(cols, kind, column)
    if at < 0:
        return [(title, cols, [list(r) for r in rows])]
    groups = group_rows(cols, rows, kind, column)
    has_values = VALUE_RATE in cols or VALUE_MRP in cols
    keep = [i for i in range(len(cols)) if i != at]
    out: List[Tuple[str, List[str], List[List[Any]]]] = []
    if summary and groups:
        out.append((f"{title}{summary_suffix(kind)}", summary_columns(cols, kind),
                    summary_rows(groups, cols, kind)))
    for g in groups:
        out.append((group_heading(title, g, has_values, cols), [cols[i] for i in keep],
                    [[r[i] if i < len(r) else "" for i in keep] for r in g["rows"]]))
    return out


def summary_suffix(kind: str) -> str:
    """What a grouped list's summary title ends in: " — by supplier" / " — by area"."""
    return f" — by {'area' if kind == 'area' else 'supplier'}"


def file_slug(text: str, default: str = "supplier") -> str:
    """A supplier / area / list name as part of a file name."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    return slug[:60] or default


def group_files(title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]],
                kind: str = "supplier", column: str = ""
                ) -> List[Tuple[str, str, str, List[str], List[List[Any]]]]:
    """One file per supplier / area: (file stem, document title, heading, columns, rows) --
    the group's own list only, no summary."""
    out: List[Tuple[str, str, str, List[str], List[List[Any]]]] = []
    cols = [str(c) for c in columns]
    if group_column(cols, kind, column) < 0:
        return out
    for g in group_rows(cols, rows, kind, column):
        heading, sec_cols, sec_rows = split_section(title, cols, g["rows"], column, summary=False,
                                                    kind=kind)[0]
        out.append((f"{file_slug(title, 'alerts')}_{file_slug(g['name'], kind)}",
                    f"{title} — {g['name']}", heading, sec_cols, sec_rows))
    return out


def supplier_files(title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]],
                   column: str = "") -> List[Tuple[str, str, str, List[str], List[List[Any]]]]:
    return group_files(title, columns, rows, "supplier", column)


def group_for(section: Dict[str, Any], default: Optional[str] = None) -> str:
    """What a section asks to be grouped by: "supplier", "area" or "" (not grouped)."""
    g = section.get("group_by") if isinstance(section, dict) else None
    if g is None:
        g = default
    if g is True or str(g or "").strip().lower() == "supplier":
        return "supplier"
    if str(g or "").strip().lower() in ("area", "village", "village / area"):
        return "area"
    return ""
