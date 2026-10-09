"""A medicine's details flow into its old bills (owner, 9 Oct 2026).

Changing a medicine to Schedule H1 must put its OLD sales in the schedule register, and the
same for its HSN code, name, type and company -- and (owner, 9 Oct 2026) its GST %, batch and
expiry. Never prices: rate, MRP, discount and every amount stay exactly as billed. GST %, batch
and expiry are never blanked on old lines by an empty new value.

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
    "gst_percent": "gst_pct",
    "batch_no": "batch_no",
    "expiry_date": "expiry_date",
}
#: medicine field -> sales_items column on this PC (sale lines keep only the GST rate)
SALE_LINE_FIELDS = {"gst_percent": "gst_percent"}
#: every field that flows (the server also carries name)
DESCRIPTIVE = ("name", "type", "hsn_code", "schedule", "manufacturer", "gst_percent", "batch_no", "expiry_date")
#: never blanked on old lines by an empty new value
_NO_BLANK = ("gst_percent", "batch_no", "expiry_date")

_LABELS = {
    "name": "Name", "type": "Type", "hsn_code": "HSN", "schedule": "Schedule", "manufacturer": "Company",
    "gst_percent": "GST %", "batch_no": "Batch", "expiry_date": "Expiry",
}


def _norm(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _key(f: str, v: Any) -> str:
    if f == "gst_percent":
        try:
            return "" if v in (None, "") else repr(float(v))
        except (TypeError, ValueError):
            return ""
    if f == "expiry_date":
        return _norm(v)[:10]
    if f == "name":
        return _norm(v).upper()
    return _norm(v)


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
        a, b = _key(f, before.get(f)), _key(f, after.get(f))
        if a == b or (f in _NO_BLANK and b == ""):
            continue
        out[f] = float(after.get(f)) if f == "gst_percent" else _norm(after.get(f))
    return out


def _pause_capture(conn: sqlite3.Connection):
    """On offline-first the server makes this same change to its own copy, so it is not this
    PC's edit of those bills: change capture is paused, or every bill of the medicine would be
    sent up again. Returns the value to put back (None when not on offline-first)."""
    try:
        r = conn.execute("SELECT v FROM of_flags WHERE k='capture'").fetchone()
    except sqlite3.Error:
        return None
    if r is None:
        return None
    conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
    return r[0]


def propagate_local(conn: sqlite3.Connection, medicine_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Write the changed fields into this medicine's bill lines; count the bills it reaches."""
    counts: dict[str, Any] = {"sales": 0, "purchases": 0, "fields": sorted(changes)}
    if not changes:
        return counts
    mid = int(medicine_id)

    def cols(table: str) -> set:
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.Error:
            return set()

    pcols, sicols = cols("purchase_items"), cols("sales_items")
    sets, differs, params = [], [], []
    for f, v in changes.items():
        col = PURCHASE_LINE_FIELDS.get(f)
        if col and col in pcols:
            sets.append(f"{col}=?")
            differs.append(f"COALESCE({col},'') <> ?")
            params.append(v)
    restore = _pause_capture(conn)
    try:
        if sets:
            rows = conn.execute(
                f"SELECT DISTINCT purchase_id FROM purchase_items WHERE medicine_id=? AND ({' OR '.join(differs)})",
                [mid, *params],
            ).fetchall()
            conn.execute(
                f"UPDATE purchase_items SET {', '.join(sets)} WHERE medicine_id=? AND ({' OR '.join(differs)})",
                [*params, mid, *params],
            )
            counts["purchases"] = len(rows)
        # Sale lines keep their GST rate: it follows too (the amount does not).
        for f, col in SALE_LINE_FIELDS.items():
            if f in changes and col in sicols:
                conn.execute(
                    f"UPDATE sales_items SET {col}=? WHERE medicine_id=? AND COALESCE({col},-1) <> ?",
                    (changes[f], mid, changes[f]),
                )
    finally:
        if restore is not None:
            conn.execute("UPDATE of_flags SET v=? WHERE k='capture'", (restore,))
    try:
        scols = cols("sales")
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
