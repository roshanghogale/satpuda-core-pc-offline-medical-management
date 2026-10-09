"""A medicine's details flow into its old bills (owner, 9 Oct 2026).

Changing a medicine to Schedule H1 must put its OLD sales in the schedule register, and the
same for its HSN code, name, type and company. Never prices: rate, MRP, GST %, discount and
amounts stay exactly as billed (printed bills, filed GST returns). Batch and expiry belong to
the stock line and are not touched.

On this PC:
  - sales_items, sales_return_items and purchase_return_items keep no copy of these fields;
    they read the medicine, so an edit already shows on old sales here.
  - purchase_items keep type, hsn_code, schedule and manufacturer: those are updated, one
    UPDATE by medicine id, only for the fields that actually changed.
On the server the same happens to its own copies (server-live services/medicineLines.js), and
a PC on offline-first does it again here when the edited medicine arrives by pull.
"""
from __future__ import annotations

import sqlite3
from typing import Any

#: medicine field -> purchase_items column on this PC
PURCHASE_LINE_FIELDS = {
    "type": "type",
    "hsn_code": "hsn_code",
    "schedule": "schedule",
    "manufacturer": "manufacturer",
}
#: every descriptive field that flows (the server also carries name)
DESCRIPTIVE = ("name", "type", "hsn_code", "schedule", "manufacturer")

_LABELS = {"name": "Name", "type": "Type", "hsn_code": "HSN", "schedule": "Schedule", "manufacturer": "Company"}


def _norm(v: Any) -> str:
    return "" if v is None else str(v).strip()


def snapshot(conn: sqlite3.Connection, medicine_id: int) -> dict[str, Any] | None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(medicines)").fetchall()}
    pick = [c for c in DESCRIPTIVE if c in cols]
    if not pick:
        return None
    row = conn.execute(
        f"SELECT {', '.join(pick)} FROM medicines WHERE id=?", (int(medicine_id),)
    ).fetchone()
    return dict(zip(pick, row)) if row else None


def changed_fields(before: dict | None, after: dict | None) -> dict[str, str]:
    """Descriptive fields that differ; only fields `after` actually carries."""
    if not before or not after:
        return {}
    out: dict[str, str] = {}
    for f in DESCRIPTIVE:
        if f not in after:
            continue
        a, b = _norm(before.get(f)), _norm(after.get(f))
        if f == "name":
            a, b = a.upper(), b.upper()
        if a != b:
            out[f] = _norm(after.get(f))
    return out


def propagate_local(conn: sqlite3.Connection, medicine_id: int, changes: dict[str, str]) -> dict[str, Any]:
    """Write the changed fields into this medicine's purchase lines; count the bills it reaches."""
    counts: dict[str, Any] = {"sales": 0, "purchases": 0, "fields": sorted(changes)}
    if not changes:
        return counts
    mid = int(medicine_id)
    try:
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(purchase_items)").fetchall()}
    except sqlite3.Error:
        pcols = set()
    sets, differs, params = [], [], []
    for f, v in changes.items():
        col = PURCHASE_LINE_FIELDS.get(f)
        if col and col in pcols:
            sets.append(f"{col}=?")
            differs.append(f"COALESCE({col},'') <> ?")
            params.append(v)
    if sets:
        # On offline-first the server makes this same change to its own copy, so it is not
        # this PC's edit of those purchases: change capture is paused, or every purchase of
        # the medicine would be sent up again.
        paused = None
        try:
            r = conn.execute("SELECT v FROM of_flags WHERE k='capture'").fetchone()
            if r is not None:
                paused = r[0]
                conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
        except sqlite3.Error:
            paused = None
        rows = conn.execute(
            f"SELECT DISTINCT purchase_id FROM purchase_items WHERE medicine_id=? AND ({' OR '.join(differs)})",
            [mid, *params],
        ).fetchall()
        conn.execute(
            f"UPDATE purchase_items SET {', '.join(sets)} WHERE medicine_id=? AND ({' OR '.join(differs)})",
            [*params, mid, *params],
        )
        if paused is not None:
            conn.execute("UPDATE of_flags SET v=? WHERE k='capture'", (paused,))
        counts["purchases"] = len(rows)
    try:
        scols = {r[1] for r in conn.execute("PRAGMA table_info(sales)").fetchall()}
        live = " AND COALESCE(s.deleted,0)=0" if "deleted" in scols else ""
        row = conn.execute(
            f"SELECT COUNT(DISTINCT si.sale_id) FROM sales_items si JOIN sales s ON s.id=si.sale_id "
            f"WHERE si.medicine_id=?{live}",
            (mid,),
        ).fetchone()
        counts["sales"] = int(row[0] or 0) if row else 0
    except sqlite3.Error:
        pass
    return counts


def note(counts: dict | None) -> str:
    """'Schedule changed: 37 old sales, 4 purchases updated' -- '' when nothing old is touched."""
    if not counts or not counts.get("fields"):
        return ""
    parts = []
    n = int(counts.get("sales") or 0)
    if n:
        parts.append(f"{n} old sale{'' if n == 1 else 's'}")
    n = int(counts.get("purchases") or 0)
    if n:
        parts.append(f"{n} purchase{'' if n == 1 else 's'}")
    n = int(counts.get("sales_returns") or 0) + int(counts.get("purchase_returns") or 0)
    if n:
        parts.append(f"{n} return{'' if n == 1 else 's'}")
    if not parts:
        return ""
    what = ", ".join(_LABELS.get(f, f) for f in counts["fields"])
    return f"{what} changed: {', '.join(parts)} updated"


def note_from_push(result: Any) -> str:
    """The server's own note (lines_note) out of a medicines push answer, if it sent one."""
    stack = [result]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("lines_note"):
                return str(cur["lines_note"])
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return ""


def after_pulled_medicine(conn: sqlite3.Connection, before: dict | None, doc: dict) -> None:
    """A medicine edited on another device arrived: carry it into this PC's purchase lines."""
    try:
        changes = changed_fields(before, doc)
        if changes:
            propagate_local(conn, int(doc.get("id") or doc.get("local_id") or 0), changes)
    except Exception as exc:  # never let a pull page fail over this
        print(f"[medicine_lines] pulled medicine: {exc}")
