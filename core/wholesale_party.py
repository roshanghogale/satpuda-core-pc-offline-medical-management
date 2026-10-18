"""A customer as a wholesale party (WHOLESALE_DESIGN.md): Settings -> Contacts -> Customers.

In wholesale mode the customer form also takes Party type (Retail / Medical), GSTIN, drug
licence numbers, State, Rate tier (A / B / C), Credit limit and Credit days. They are kept on
the customer row (core/wholesale_schema.COLUMNS["customers"]) -- except the GSTIN, which stays
where it has always been, core/customer_gst.py (one ``customer_gst:<id>`` setting), so the GST
reports and the tax invoice read one place. The state is the GSTIN's first two digits when there
is a GSTIN; without one the state picked on the form is kept in ``state_code``.

A save that does not mention a field leaves it alone: the retail form (no such fields) never
wipes a party's wholesale details.
"""
from __future__ import annotations

from typing import Any

from core.customer_gst import STATES, clean_gstin, gstin_problem, state_of

PARTY_TYPES = ("retail", "medical")
TIERS = ("A", "B", "C")
FIELDS = ("party_type", "dl_numbers", "state_code", "rate_tier", "credit_limit", "credit_days")


def has_party_fields(data: Any) -> bool:
    return isinstance(data, dict) and any(k in data for k in (*FIELDS, "gstin"))


def clean_party(data: dict) -> dict[str, Any]:
    """The party fields present in ``data``, checked and normalised. Raises ValueError."""
    out: dict[str, Any] = {}
    if "party_type" in data:
        pt = str(data.get("party_type") or "retail").strip().lower()
        if pt not in PARTY_TYPES:
            raise ValueError("Party type must be Retail or Medical.")
        out["party_type"] = pt
    if "dl_numbers" in data:
        out["dl_numbers"] = " ".join(str(data.get("dl_numbers") or "").split())[:200]
    if "rate_tier" in data:
        t = str(data.get("rate_tier") or "A").strip().upper() or "A"
        if t not in TIERS:
            raise ValueError("Rate tier must be A, B or C.")
        out["rate_tier"] = t
    if "credit_limit" in data:
        raw = data.get("credit_limit")
        try:
            v = float(raw) if raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            raise ValueError("Credit limit must be a number.")
        if v < 0:
            raise ValueError("Credit limit cannot be negative.")
        out["credit_limit"] = round(v, 2)
    if "credit_days" in data:
        raw = data.get("credit_days")
        try:
            d = int(float(raw)) if raw not in (None, "") else 0
        except (TypeError, ValueError):
            raise ValueError("Credit days must be a whole number.")
        if d < 0 or d > 3650:
            raise ValueError("Credit days must be between 0 and 3650.")
        out["credit_days"] = d
    if "gstin" in data:
        g = clean_gstin(data.get("gstin"))
        if g:
            problem = gstin_problem(g)
            if problem:
                raise ValueError(problem)
        out["gstin"] = g
    if "state_code" in data or out.get("gstin"):
        st = state_of(out.get("gstin")) if out.get("gstin") else ""
        if not st:
            st = str(data.get("state_code") or "").strip()
            st = st.zfill(2) if st.isdigit() else st
            if st and st not in STATES:
                raise ValueError(f"Unknown state code {st}.")
        out["state_code"] = st
    return out


def save_party_local(conn, customer_id: int, party: dict[str, Any], *, legal_name: str = "") -> None:
    """Write cleaned party fields (clean_party) onto a local customer; GSTIN via customer_gst.
    Does not commit the row update (the caller's save does); the GSTIN setting commits."""
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(customers)").fetchall()}
    sets = {k: v for k, v in party.items() if k in FIELDS and k in cols}
    if sets:
        conn.execute(
            "UPDATE customers SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?",
            (*sets.values(), int(customer_id)),
        )
    if "gstin" in party:
        save_gstin(conn, customer_id, party["gstin"], legal_name=legal_name)


def save_gstin(conn, customer_id: int, gstin: str, *, legal_name: str = "") -> None:
    from core import customer_gst

    current = {}
    try:
        current = customer_gst.get_customer_gst(conn, customer_id)
    except Exception:
        current = {}
    if clean_gstin(current.get("gstin")) == clean_gstin(gstin):
        return  # unchanged: keep its "since" date
    customer_gst.save_customer_gst(conn, customer_id, gstin, legal_name or current.get("legal_name") or "")


def doc_party(party: dict[str, Any]) -> dict[str, Any]:
    """The party fields for an Online customer document (GSTIN is not part of it)."""
    return {k: v for k, v in party.items() if k in FIELDS}


def row_fields(row: dict[str, Any], gst: dict[str, Any] | None = None) -> dict[str, Any]:
    """The party fields for the contacts list, from a customer row / document."""
    gst = gst or {}
    g = clean_gstin(gst.get("gstin"))
    st = state_of(g) or str(row.get("state_code") or "")
    try:
        limit = float(row.get("credit_limit") or 0)
    except (TypeError, ValueError):
        limit = 0.0
    try:
        days = int(float(row.get("credit_days") or 0))
    except (TypeError, ValueError):
        days = 0
    pt = str(row.get("party_type") or "retail").lower()
    return {
        "party_type": pt if pt in PARTY_TYPES else "retail",
        "dl_numbers": str(row.get("dl_numbers") or ""),
        "state_code": st,
        "state_name": STATES.get(st, ""),
        "rate_tier": str(row.get("rate_tier") or "A").upper() if str(row.get("rate_tier") or "A").upper() in TIERS else "A",
        "credit_limit": limit,
        "credit_days": days,
        "gstin": g,
    }


def states_list() -> list[dict[str, str]]:
    return [{"code": c, "name": n} for c, n in sorted(STATES.items())]


def enrich_contacts(conn, customers: list[dict], docs: list[dict] | None = None) -> None:
    """Add the party fields to the contacts list rows in place. ``docs``: the Online catalog
    rows (the fields are in the documents); Offline they are read from the customers table.
    The GSTINs are read only when the store is wholesale (Online that is a server call)."""
    if not customers:
        return
    by_id: dict[int, dict] = {}
    if docs is not None:
        for d in docs:
            try:
                by_id[int(d.get("id") or d.get("local_id") or 0)] = d
            except (TypeError, ValueError, AttributeError):
                continue
    else:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(customers)").fetchall()}
        have = [c for c in FIELDS if c in cols]
        if have:
            for r in conn.execute(f"SELECT id, {', '.join(have)} FROM customers").fetchall():
                by_id[int(r[0])] = dict(zip(have, r[1:]))
    gst: dict[int, dict] = {}
    try:
        from core.wholesale_mode import is_wholesale

        if is_wholesale(conn):
            from core.customer_gst import all_customer_gst

            gst = all_customer_gst(conn)
    except Exception:
        gst = {}
    for c in customers:
        try:
            cid = int(c.get("id") or 0)
        except (TypeError, ValueError):
            continue
        c.update(row_fields(by_id.get(cid) or {}, gst.get(cid)))
