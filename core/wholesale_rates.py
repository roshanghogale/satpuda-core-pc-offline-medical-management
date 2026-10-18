"""Rate A / B / C of a wholesale purchase line, and where they go (WHOLESALE_DESIGN.md).

A wholesale purchase line keeps its purchase rate and also takes Rate A, B and C (per pack,
GST excluded). They are saved on the line (purchase_items.rate_a/b/c) and copied to the
medicine batch the line stocked (medicines.purchase_rate, rate_a, rate_b, rate_c): the batch's
sale rates for a wholesale bill. The next purchase of the same medicine starts from them.
"""
from __future__ import annotations

from typing import Any

RATE_KEYS = ("rate_a", "rate_b", "rate_c")
_ALIASES = {"rate_a": ("rate_a", "rateA", "rate_A"), "rate_b": ("rate_b", "rateB", "rate_B"),
            "rate_c": ("rate_c", "rateC", "rate_C")}


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        f = round(float(v), 2)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def line_rates(item: Any) -> dict[str, float]:
    """{rate_a, rate_b, rate_c} typed on a line (only the ones above zero); {} when none."""
    if not isinstance(item, dict):
        return {}
    out: dict[str, float] = {}
    for key, names in _ALIASES.items():
        for n in names:
            v = _num(item.get(n))
            if v is not None:
                out[key] = v
                break
    return out


def _cols(conn, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def rate_select_sql(conn, alias: str = "pi") -> str:
    """', pi.rate_a, pi.rate_b, pi.rate_c' (NULLs on a store file without the columns)."""
    have = _cols(conn, "purchase_items")
    return "".join(f", {alias}.{c}" if c in have else ", NULL" for c in RATE_KEYS)


def last_rates(conn, *, name: str = "", medicine_id: int = 0) -> dict[str, Any]:
    """The newest rates a medicine was given: that batch first, else any batch of the name."""
    have = _cols(conn, "medicines")
    if not {"rate_a", "rate_b", "rate_c", "purchase_rate"} <= have:
        return {"found": False}
    sel = "SELECT id, COALESCE(purchase_rate,0), rate_a, rate_b, rate_c FROM medicines "
    rows = []
    if medicine_id and int(medicine_id) > 0:
        rows = conn.execute(sel + "WHERE id=? AND COALESCE(rate_a,0) > 0", (int(medicine_id),)).fetchall()
    if not rows and str(name or "").strip():
        rows = conn.execute(
            sel + "WHERE UPPER(TRIM(name)) = UPPER(TRIM(?)) AND COALESCE(rate_a,0) > 0 "
            "ORDER BY id DESC LIMIT 1",
            (str(name),),
        ).fetchall()
    if not rows:
        return {"found": False}
    r = rows[0]
    return {"found": True, "medicine_id": int(r[0]), "purchase_rate": float(r[1] or 0),
            "rate_a": float(r[2] or 0), "rate_b": float(r[3] or 0), "rate_c": float(r[4] or 0)}


def apply_purchase_rates(conn, purchase_id: int, items: list, *, to_medicines: bool = True) -> int:
    """Write the typed Rate A / B / C of a saved purchase onto its lines and (to_medicines)
    onto the medicine batches. Lines are matched by medicine id, else by name + batch, each
    stored line once. Returns how many lines got rates. Commits."""
    want = [(it, line_rates(it)) for it in (items or []) if isinstance(it, dict)]
    if not any(r for _, r in want):
        return 0
    if not set(RATE_KEYS) <= _cols(conn, "purchase_items"):
        return 0
    stored = conn.execute(
        "SELECT pi.id, pi.medicine_id, UPPER(TRIM(COALESCE(m.name,''))), "
        "UPPER(TRIM(COALESCE(pi.batch_no,''))) "
        "FROM purchase_items pi LEFT JOIN medicines m ON m.id = pi.medicine_id "
        "WHERE pi.purchase_id=? ORDER BY pi.id",
        (int(purchase_id),),
    ).fetchall()
    free = list(stored)
    med_cols = _cols(conn, "medicines")
    med_ok = to_medicines and set(RATE_KEYS) | {"purchase_rate"} <= med_cols
    n = 0
    for it, rates in want:
        mid = 0
        try:
            mid = int(it.get("medicine_id") or it.get("id") or 0)
        except (TypeError, ValueError):
            mid = 0
        name = str(it.get("name") or it.get("medicine_name") or "").strip().upper()
        batch = str(it.get("batch") or it.get("batch_no") or "").strip().upper()
        hit = None
        if mid > 0:
            hit = next((r for r in free if int(r[1] or 0) == mid), None)
        if hit is None:
            hit = next((r for r in free if r[2] == name and r[3] == batch), None)
        if hit is None:
            continue
        free.remove(hit)
        if not rates:
            continue
        vals = [rates.get(k) for k in RATE_KEYS]
        conn.execute("UPDATE purchase_items SET rate_a=?, rate_b=?, rate_c=? WHERE id=?", (*vals, int(hit[0])))
        n += 1
        if med_ok and hit[1]:
            pr = _num(it.get("rate"))
            conn.execute(
                "UPDATE medicines SET purchase_rate=COALESCE(?, purchase_rate), "
                "rate_a=COALESCE(?, rate_a), rate_b=COALESCE(?, rate_b), rate_c=COALESCE(?, rate_c) "
                "WHERE id=?",
                (pr, *vals, int(hit[1])),
            )
    conn.commit()
    return n


def medicine_doc_rates(item: dict) -> dict[str, float]:
    """For an Online medicine document: the line's rates plus its purchase rate."""
    rates = line_rates(item)
    if not rates:
        return {}
    pr = _num(item.get("rate"))
    if pr is not None:
        rates["purchase_rate"] = pr
    return rates
