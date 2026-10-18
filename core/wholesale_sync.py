"""The wholesale columns in sync documents, both ways (WHOLESALE_DESIGN.md, "Contract").

The server keeps documents as JSON, so a field the PC sends comes back to every device as it
was sent. Push: ``doc_fields(table, row)`` adds a row's wholesale columns to the document a
builder makes. Pull: ``apply_doc(cur, table, id, data, keep)`` writes the ones the document
carries back onto the local row. A pull that rewrites the whole row (INSERT OR REPLACE) takes a
``snapshot`` first and passes it as ``keep``: a document from an older device that has never
heard of these fields must not wipe them.

Columns: core/wholesale_schema.COLUMNS (customers, medicines, purchase_items, sales,
sales_items, customer_payments, supplier_payments).
"""
from __future__ import annotations

from typing import Any, Optional

from core.wholesale_schema import COLUMNS


def column_names(table: str) -> tuple[str, ...]:
    return tuple(name for name, _typ in COLUMNS.get(table, ()))


def doc_fields(table: str, row: Any) -> dict[str, Any]:
    """{column: value} for the wholesale columns the row has (a key absent = not sent)."""
    if not isinstance(row, dict):
        return {}
    return {c: row[c] for c in column_names(table) if c in row}


def _have(cur, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def snapshot(cur, table: str, row_id: Any) -> dict[str, Any]:
    """The row's wholesale values now ({} when the row or the columns are not there)."""
    cols = [c for c in column_names(table) if c in _have(cur, table)]
    if not cols:
        return {}
    try:
        row = cur.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id=?", (int(row_id),)).fetchone()
    except Exception:
        return {}
    return dict(zip(cols, row)) if row else {}


def apply_doc(cur, table: str, row_id: Any, data: Any, keep: Optional[dict] = None) -> int:
    """Write the wholesale fields of a pulled document onto the local row; a field the
    document does not carry keeps its ``keep`` value (when given). Returns columns written."""
    data = data if isinstance(data, dict) else {}
    keep = keep or {}
    have = _have(cur, table)
    sets: dict[str, Any] = {}
    for c in column_names(table):
        if c not in have:
            continue
        if c in data:
            sets[c] = data[c]
        elif c in keep:
            sets[c] = keep[c]
    if not sets:
        return 0
    try:
        cur.execute(
            f"UPDATE {table} SET " + ", ".join(f"{c}=?" for c in sets) + " WHERE id=?",
            (*sets.values(), int(row_id)),
        )
    except Exception:
        return 0
    return len(sets)
