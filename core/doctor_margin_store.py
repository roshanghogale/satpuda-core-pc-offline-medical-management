"""The doctor report's own saved lines (owner, 22 Oct 2026).

"the purchase rate free is only for saving that item sale where the doctor margin is set the sale
is not saving it it is being saved in doctor report": the bill tables stay exactly as they are.
When a bill with a doctor is saved, the margin each of its lines gives (rules in
core/doctor_margin_rules.py) is written HERE -- doctor, bill, medicine, batch, qty, MRP, both
purchase rates of the batch, the rate used, margin per unit, value, the rule that matched -- and
the Doctor Margin Report reads these rows. So a bill keeps the figures it was saved with when the
rules change later; only "Recalculate" (a period, on purpose) works them out again.

  doctor_margin_bills  one row per bill looked at: its fingerprint (doctor, date, patient, the
                       lines' medicine + qty) and how many margin lines it gave (0 is a row too,
                       so a bill without a margin is not looked at again and again)
  doctor_margin_lines  only the lines a margin is given on

Kept right by:
  sync_sale(conn, sale_id)       after a bill is saved / edited / deleted (desktop_api); an edit
                                 that leaves the lines, doctor and date alone keeps the figures
  refresh_returns(conn, ids)     after a sales return is saved / deleted: the returned qty
                                 comes off the lines (rates and rule stay as saved)
  reconcile(conn, from, to)      when the report opens: bills that arrived by sync (another PC,
                                 the phone) or were changed / deleted elsewhere are brought in
  recalculate(conn, from, to)    the owner's button: every bill of the period again, today's rules

These tables are this PC's working copy, not part of the books: no sync trigger reads them, and
any device can rebuild them from the bills. Nothing here writes a sale, a purchase or stock.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from core import doctor_margin_rules as dmr

log = logging.getLogger(__name__)

_DDL = (
    """CREATE TABLE IF NOT EXISTS doctor_margin_bills (
        sale_id     INTEGER PRIMARY KEY,
        doctor      TEXT,
        bill_date   TEXT,
        fingerprint TEXT,
        lines       INTEGER DEFAULT 0,
        rules_at    TEXT,
        saved_at    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS doctor_margin_lines (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id       INTEGER NOT NULL,
        sale_item_id  INTEGER,
        line_no       INTEGER,
        bill_no       TEXT,
        bill_date     TEXT,
        patient       TEXT,
        doctor        TEXT,
        medicine_id   INTEGER,
        name          TEXT,
        type          TEXT,
        batch         TEXT,
        qty_sold      REAL DEFAULT 0,
        qty_returned  REAL DEFAULT 0,
        qty           REAL DEFAULT 0,
        mrp           REAL DEFAULT 0,
        rate_purchase REAL DEFAULT 0,
        rate_free     REAL DEFAULT 0,
        rate_basis    TEXT,
        rate          REAL DEFAULT 0,
        kind          TEXT,
        kind_value    REAL DEFAULT 0,
        margin_unit   REAL DEFAULT 0,
        value         REAL DEFAULT 0,
        rule          TEXT,
        flags         TEXT,
        saved_at      TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_dml_date ON doctor_margin_lines (bill_date, doctor)",
    "CREATE INDEX IF NOT EXISTS ix_dml_sale ON doctor_margin_lines (sale_id)",
    "CREATE INDEX IF NOT EXISTS ix_dmb_date ON doctor_margin_bills (bill_date)",
)
_LINE_COLS = (
    "sale_id", "sale_item_id", "line_no", "bill_no", "bill_date", "patient", "doctor", "medicine_id",
    "name", "type", "batch", "qty_sold", "qty_returned", "qty", "mrp", "rate_purchase", "rate_free",
    "rate_basis", "rate", "kind", "kind_value", "margin_unit", "value", "rule", "flags", "saved_at",
)
_CHUNK = 400


def ensure_tables(conn) -> None:
    for stmt in _DDL:
        conn.execute(stmt)


def _cols(conn, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _live(conn, table: str, alias: str, *, autosave: bool = False) -> list[str]:
    have = _cols(conn, table)
    out = []
    if "deleted" in have:
        out.append(f"COALESCE({alias}.deleted,0)=0")
    if autosave and "is_autosave" in have:
        out.append(f"COALESCE({alias}.is_autosave,0)=0")
    return out


def _chunks(ids: Iterable[int]) -> Iterable[list[int]]:
    ids = list(ids)
    for i in range(0, len(ids), _CHUNK):
        yield ids[i:i + _CHUNK]


def _next_day(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── reading bills (for these sale ids only) ─────────────────────────────────────────────────

def _divisor_fn():
    """(type, unit, name) -> sale units in one pack: the Sales screen's own divisor, so the MRP
    per unit here is the MRP per unit the bill shows (tablets for strip medicines)."""
    from core.margin_utils import margin_unit_divisor

    cache: dict[tuple, int] = {}

    def div(mtype: str, unit: str, name: str) -> int:
        k = (mtype, unit, name)
        if k not in cache:
            try:
                cache[k] = max(1, int(margin_unit_divisor({"type": mtype, "unit": unit, "name": name})))
            except Exception:
                cache[k] = 1
        return cache[k]

    return div


def read_bills(conn, sale_ids: Iterable[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(sales, purchases) in the shape doctor_margin_rules.bill_lines() takes. Live bills only
    (a deleted bill or an autosave draft is simply absent)."""
    from core.doctor_margin_report import _units_per_pack
    from core.fy_serial import display_sales_bill_no

    ids = sorted({int(i) for i in sale_ids if int(i or 0) > 0})
    if not ids:
        return [], []
    live = " AND ".join([""] + _live(conn, "sales", "s", autosave=True))
    sales: dict[int, dict[str, Any]] = {}
    for part in _chunks(ids):
        q = ",".join("?" * len(part))
        for sid, bno, bdate, patient, doctor in conn.execute(
            f"""
            SELECT s.id, COALESCE(s.bill_no,''), COALESCE(s.bill_date,''),
                   COALESCE(NULLIF(TRIM(COALESCE(s.customer_name,'')),''), c.name, ''),
                   COALESCE(s.doctor_name,'')
            FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
            WHERE s.id IN ({q}){live}
            """,
            part,
        ):
            sales[int(sid)] = {
                "id": int(sid), "bill_no": display_sales_bill_no(str(bno)), "date": str(bdate)[:10],
                "patient": str(patient or ""), "doctor": str(doctor or ""), "items": [], "returns": [],
            }
    if not sales:
        return [], []
    div = _divisor_fn()
    item_live = " AND ".join([""] + _live(conn, "sales_items", "si"))
    med_ids: set[int] = set()
    for part in _chunks(sales):
        q = ",".join("?" * len(part))
        for iid, sid, mid, name, mtype, batch, unit, mrp, mrate, qty, cost in conn.execute(
            f"""
            SELECT si.id, si.sale_id, si.medicine_id, COALESCE(m.name,''), COALESCE(m.type,''),
                   COALESCE(m.batch_no,''), COALESCE(m.unit,'1'), COALESCE(m.mrp,0), COALESCE(m.rate,0),
                   COALESCE(si.qty,0), COALESCE(si.cost_price,0)
            FROM sales_items si LEFT JOIN medicines m ON m.id = si.medicine_id
            WHERE si.sale_id IN ({q}){item_live}
            ORDER BY si.sale_id, si.id
            """,
            part,
        ):
            d = div(str(mtype), str(unit or "1"), str(name))
            sales[int(sid)]["items"].append({
                "id": int(iid), "medicine_id": int(mid or 0), "name": str(name), "type": str(mtype),
                "batch": str(batch), "qty": float(qty or 0), "mrp_unit": float(mrp or 0) / d,
                "med_rate_unit": float(mrate or 0) / d, "cost_price": float(cost or 0),
            })
            med_ids.add(int(mid or 0))
    for sid, rets in _returns(conn, list(sales)).items():
        sales[sid]["returns"] = rets

    purchases: list[dict[str, Any]] = []
    pi_cols = _cols(conn, "purchase_items")
    logged = "pi.stock_units" if "stock_units" in pi_cols else "NULL"
    p_live = " AND ".join([""] + _live(conn, "purchases", "p", autosave=True) + _live(conn, "purchase_items", "pi"))
    strip_cache: dict[str, bool] = {}
    for part in _chunks(sorted(med_ids - {0})):
        q = ",".join("?" * len(part))
        for mid, qty, free, rate, mtype, unit, stocked in conn.execute(
            f"""
            SELECT pi.medicine_id, COALESCE(pi.qty,0), COALESCE(pi.free_qty,0), COALESCE(pi.rate,0),
                   COALESCE(NULLIF(TRIM(pi.type),''), m.type, ''), COALESCE(m.unit,'1'), {logged}
            FROM purchase_items pi
            JOIN purchases p ON p.id = pi.purchase_id
            LEFT JOIN medicines m ON m.id = pi.medicine_id
            WHERE pi.medicine_id IN ({q}){p_live}
            ORDER BY pi.id
            """,
            part,
        ):
            qf, ff = float(qty or 0), float(free or 0)
            purchases.append({
                "medicine_id": int(mid or 0), "qty": qf, "free_qty": ff, "rate": float(rate or 0),
                "units": _units_per_pack(str(mtype or ""), str(unit or "1"), qf, ff, stocked, strip_cache),
            })
    return [sales[i] for i in ids if i in sales], purchases


def _returns(conn, sale_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """sale id -> [{medicine_id, qty}] returned (returns not deleted)."""
    out: dict[int, list[dict[str, Any]]] = {}
    if not sale_ids or not _cols(conn, "sales_returns") or not _cols(conn, "sales_return_items"):
        return out
    live = " AND ".join([""] + _live(conn, "sales_returns", "sr") + _live(conn, "sales_return_items", "sri"))
    for part in _chunks(sale_ids):
        q = ",".join("?" * len(part))
        for sid, mid, qty in conn.execute(
            f"""
            SELECT sr.sale_id, sri.medicine_id, SUM(COALESCE(sri.qty,0))
            FROM sales_return_items sri JOIN sales_returns sr ON sr.id = sri.return_id
            WHERE sr.sale_id IN ({q}){live}
            GROUP BY sr.sale_id, sri.medicine_id
            ORDER BY sr.sale_id, sri.medicine_id
            """,
            part,
        ):
            out.setdefault(int(sid or 0), []).append({"medicine_id": int(mid or 0), "qty": float(qty or 0)})
    return out


def fingerprint(sale: dict[str, Any]) -> str:
    """What the saved figures depend on in a bill: doctor, date, patient and its lines."""
    parts = [dmr.norm_name(sale.get("doctor")), str(sale.get("date") or "")[:10],
             " ".join(str(sale.get("patient") or "").split()), str(sale.get("bill_no") or "")]
    parts += [f"{int(it.get('medicine_id') or 0)}:{float(it.get('qty') or 0):g}" for it in sale.get("items") or []]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


# ── writing ─────────────────────────────────────────────────────────────────────────────────

def _forget(conn, sale_ids: Iterable[int]) -> None:
    for part in _chunks(sale_ids):
        q = ",".join("?" * len(part))
        conn.execute(f"DELETE FROM doctor_margin_lines WHERE sale_id IN ({q})", part)
        conn.execute(f"DELETE FROM doctor_margin_bills WHERE sale_id IN ({q})", part)


def _write(conn, rules: dict[str, Any], sales: list[dict[str, Any]], purchases: list[dict[str, Any]]) -> int:
    """Save the margin lines of these bills (replacing what they had). Returns lines written."""
    res = dmr.Resolver(rules)
    rates = dmr.batch_rates(purchases)
    now = _now()
    n = 0
    _forget(conn, [s["id"] for s in sales])
    for s in sales:
        lines = dmr.bill_lines(res, s, rates)
        for ln in lines:
            row = dict(ln, bill_date=ln["date"], flags=json.dumps(ln["flags"]), saved_at=now)
            conn.execute(
                f"INSERT INTO doctor_margin_lines ({','.join(_LINE_COLS)}) VALUES ({','.join('?' * len(_LINE_COLS))})",
                [row.get(c) for c in _LINE_COLS],
            )
        conn.execute(
            "INSERT OR REPLACE INTO doctor_margin_bills (sale_id, doctor, bill_date, fingerprint, lines, rules_at, "
            "saved_at) VALUES (?,?,?,?,?,?,?)",
            (s["id"], dmr.norm_name(s.get("doctor")), s["date"], fingerprint(s), len(lines),
             rules.get("updated") or "", now),
        )
        n += len(lines)
    return n


def _local_books() -> bool:
    """Offline / offline-first: the bills are in this PC's database. Online: on the server."""
    try:
        from core.sync_prefs import is_online_mode

        return not is_online_mode()
    except Exception:
        return True


def sync_sale(conn, sale_id: Any, *, force: bool = False) -> dict[str, Any]:
    """Bring one bill's saved lines in line with the bill (after save / edit / delete).

    A live bill with a doctor whose fingerprint is unchanged keeps its figures (an edit of the
    payment, say); a changed one is worked out again with today's rules; a deleted bill or one
    without a doctor loses its lines. Never raises: a bill save must not fail over this."""
    try:
        sid = int(sale_id or 0)
        if sid <= 0 or not _local_books():
            return {"ok": True, "skipped": True}
        ensure_tables(conn)
        sales, purchases = read_bills(conn, [sid])
        sale = sales[0] if sales else None
        if sale is None or not dmr.norm_name(sale.get("doctor")):
            _forget(conn, [sid])
            conn.commit()
            return {"ok": True, "lines": 0}
        if not force:
            row = conn.execute("SELECT fingerprint FROM doctor_margin_bills WHERE sale_id=?", (sid,)).fetchone()
            if row and row[0] == fingerprint(sale):
                refresh_returns(conn, [sid])
                return {"ok": True, "kept": True}
        rules = dmr.load_rules(conn)
        if not dmr.has_rules(rules):
            if force:
                _forget(conn, [sid])
                conn.commit()
            return {"ok": True, "lines": 0, "no_rules": True}
        n = _write(conn, rules, [sale], purchases)
        conn.commit()
        return {"ok": True, "lines": n}
    except Exception as exc:  # pragma: no cover - logged, never in the bill's way
        log.warning("doctor margin lines for sale %s: %s", sale_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}


def refresh_returns(conn, sale_ids: Iterable[int] | None = None) -> int:
    """Returned qty off the saved lines again (rates and rule stay as saved). None = every
    saved bill. Returns lines changed."""
    ensure_tables(conn)
    if sale_ids is None:
        ids = [int(r[0]) for r in conn.execute("SELECT DISTINCT sale_id FROM doctor_margin_lines")]
    else:
        ids = sorted({int(i) for i in sale_ids if int(i or 0) > 0})
    if not ids:
        return 0
    back = _returns(conn, ids)
    changed = 0
    for part in _chunks(ids):
        q = ",".join("?" * len(part))
        by_sale: dict[int, list[dict[str, Any]]] = {}
        for lid, sid, mid, sold, ret, unit, item_id, line_no in conn.execute(
            f"SELECT id, sale_id, medicine_id, qty_sold, qty_returned, margin_unit, sale_item_id, line_no "
            f"FROM doctor_margin_lines WHERE sale_id IN ({q}) ORDER BY sale_id, line_no, id",
            part,
        ):
            by_sale.setdefault(int(sid), []).append({
                "id": lid, "medicine_id": mid, "qty_sold": sold, "old": ret, "margin_unit": unit,
                "sale_item_id": item_id, "line_no": line_no,
            })
        for sid, saved in by_sale.items():
            # A return is shared over ALL the bill's lines of that medicine in bill order, the
            # ones without a margin too (they are not saved), as the bill was when saved.
            lines = _all_lines_for_returns(conn, sid, saved)
            dmr.apply_returns(lines, back.get(sid))
            for ln in lines:
                if ln.get("id") is None:
                    continue
                if abs(float(ln["qty_returned"]) - float(ln["old"] or 0)) > 1e-9:
                    conn.execute(
                        "UPDATE doctor_margin_lines SET qty_returned=?, qty=?, value=? WHERE id=?",
                        (ln["qty_returned"], ln["qty"], ln["value"], ln["id"]),
                    )
                    changed += 1
    conn.commit()
    return changed


def _all_lines_for_returns(conn, sale_id: int, saved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The bill's lines in order: the saved ones with their figures, the others with qty only.

    Matched by place in the bill (line_no), not by line id: an edit may write the same lines
    again under new ids, and the fingerprint has already said they are the same lines."""
    item_live = " AND ".join([""] + _live(conn, "sales_items", "si"))
    have = {int(s["line_no"] or 0): s for s in saved}
    out: list[dict[str, Any]] = []
    for n, (mid, qty) in enumerate(conn.execute(
        f"SELECT si.medicine_id, COALESCE(si.qty,0) FROM sales_items si "
        f"WHERE si.sale_id=?{item_live} ORDER BY si.id",
        (sale_id,),
    ), 1):
        s = have.get(n)
        if s is not None and int(s["medicine_id"] or 0) == int(mid or 0):
            have.pop(n)
            out.append(s)
        else:
            out.append({"id": None, "medicine_id": mid, "qty_sold": qty, "margin_unit": 0.0})
    out.extend(have.values())  # a saved line whose bill line is gone: still netted
    return out


def _bills_in_period(conn, from_date: str, to_date: str) -> list[int]:
    live = " AND ".join(_live(conn, "sales", "s", autosave=True)
                        + ["s.bill_date >= ?", "s.bill_date < ?", "TRIM(COALESCE(s.doctor_name,''))<>''"])
    return [int(r[0]) for r in conn.execute(f"SELECT s.id FROM sales s WHERE {live}", (from_date, _next_day(to_date)))]


def reconcile(conn, from_date: str, to_date: str) -> dict[str, int]:
    """Before the report: the period's saved lines made to match its bills.

    New doctor bills (synced from another device, or saved before rules existed) are worked
    out with today's rules; changed ones again; deleted ones dropped; returns re-netted.
    Bills whose fingerprint is unchanged keep their saved figures."""
    ensure_tables(conn)
    want = _bills_in_period(conn, from_date, to_date)
    span = (from_date, _next_day(to_date))
    saved = {int(r[0]): r[1] for r in conn.execute(
        "SELECT sale_id, fingerprint FROM doctor_margin_bills WHERE bill_date >= ? AND bill_date < ?", span)}
    for part in _chunks(want):  # a bill whose date moved INTO the period is known under its old date
        q = ",".join("?" * len(part))
        for sid, fp in conn.execute(f"SELECT sale_id, fingerprint FROM doctor_margin_bills WHERE sale_id IN ({q})", part):
            saved.setdefault(int(sid), fp)
    want_set = set(want)
    # Deleted, lost its doctor, or moved out of the period: each is synced on its own.
    moved = [sid for sid in saved if sid not in want_set]
    sales, purchases = read_bills(conn, want) if want else ([], [])
    stale = [s for s in sales if saved.get(s["id"]) != fingerprint(s)]
    rules = dmr.load_rules(conn)
    wrote = 0
    if stale and dmr.has_rules(rules):
        need = {it["medicine_id"] for s in stale for it in s["items"]}
        wrote = _write(conn, rules, stale, [p for p in purchases if p["medicine_id"] in need])
    conn.commit()
    for sid in moved:
        sync_sale(conn, sid, force=True)
    refresh_returns(conn, want)
    return {"bills": len(want), "worked_out": len(stale) if dmr.has_rules(rules) else 0,
            "dropped": len(moved), "lines_written": wrote}


def recalculate(conn, from_date: str, to_date: str) -> dict[str, int]:
    """The owner's "Recalculate": every bill of the period worked out again with today's rules."""
    ensure_tables(conn)
    want = _bills_in_period(conn, from_date, to_date)
    span = (from_date, _next_day(to_date))
    old = [int(r[0]) for r in conn.execute(
        "SELECT sale_id FROM doctor_margin_bills WHERE bill_date >= ? AND bill_date < ?", span)]
    _forget(conn, set(old) | set(want))
    rules = dmr.load_rules(conn)
    wrote = 0
    if want and dmr.has_rules(rules):
        sales, purchases = read_bills(conn, want)
        wrote = _write(conn, rules, sales, purchases)
    conn.commit()
    return {"bills": len(want), "lines_written": wrote}


def read_lines(conn, from_date: str, to_date: str, doctors: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The saved margin lines of the period (net qty above 0), in the shape assemble() takes."""
    ensure_tables(conn)
    want = [dmr.norm_name(d) for d in doctors if dmr.norm_name(d)]
    where = "bill_date >= ? AND bill_date < ? AND qty > 0"
    args: list[Any] = [from_date, _next_day(to_date)]
    if want:
        where += f" AND doctor IN ({','.join('?' * len(want))})"
        args += want
    cols = ("sale_id", "sale_item_id", "line_no", "bill_no", "bill_date", "patient", "doctor", "medicine_id",
            "name", "type", "batch", "qty_sold", "qty_returned", "qty", "mrp", "rate_purchase", "rate_free",
            "rate_basis", "rate", "kind", "kind_value", "margin_unit", "value", "rule", "flags", "saved_at")
    out = []
    for r in conn.execute(f"SELECT {','.join(cols)} FROM doctor_margin_lines WHERE {where} "
                          f"ORDER BY doctor, name, batch, bill_date, bill_no, line_no", args):
        d = dict(zip(cols, r))
        d["date"] = d.pop("bill_date")
        try:
            d["flags"] = json.loads(d["flags"] or "[]")
        except ValueError:
            d["flags"] = []
        out.append(d)
    return out
