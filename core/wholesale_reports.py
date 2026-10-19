"""Reports a store reads in wholesale mode (owner, 18 Oct 2026; D:\\projects\\WHOLESALE_DESIGN.md).

Retail reports stay as they are; these read the wholesale columns (core/wholesale_schema.py)
of this PC's store database:

  register     one row per wholesale tax invoice: party, GSTIN, taxable, CGST / SGST / IGST,
               total, due date, what is paid and what is still owed on it
  party        party-wise sales: invoices, qty, free qty, taxable, GST, amount per party
  items        item-wise sales by party: each medicine / batch a party bought
  outstanding  what each party still owes, aged 0-30 / 31-60 / 61-90 / 90+ days past the
               due date (bill date when the bill has none), the oldest overdue days and the
               credit limit; with the open bills under it
  price_list   medicines with batch, expiry, HSN, GST %, purchase rate, Rate A / B / C and
               stock; one tier's list to hand a party

What a bill still owes is the dues rule itself (core/bill_clearing.py): a payment pays the
bills it was given, the rest clears the oldest bills first. Nothing here writes.

Every report is {"title", "subtitle", "sections": [{title, columns, rows}], ...}; the same
sections go to CSV / Excel / PDF / the printer (core/desktop_export_service.export_alert_sections).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from core.bill_gst import ZERO, split_tax, to_paise
from core.wholesale_schema import RATE_TIERS

REPORTS = ("register", "party", "items", "outstanding", "price_list")
TITLES = {
    "register": "Wholesale sales register",
    "party": "Party-wise sales",
    "items": "Item-wise sales by party",
    "outstanding": "Outstanding and ageing",
    "price_list": "Price list",
}
BUCKETS = ("Not Due", "Due 0-30", "Due 31-60", "Due 61-90", "Due 90+")


def _f(v: Decimal) -> float:
    return float(v)


def _iso(value: Any) -> str:
    s = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _day(value: Any) -> Optional[date]:
    try:
        return datetime.strptime(_iso(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _dmy(iso: str) -> str:
    d = _day(iso)
    return d.strftime("%d-%m-%Y") if d else str(iso or "")


def _cols(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _live(conn, table: str, alias: str) -> str:
    have = _cols(conn, table)
    parts = ["1=1"]
    for col in ("deleted", "is_autosave"):
        if col in have:
            parts.append(f"COALESCE({alias}.{col},0)=0")
    return " AND ".join(parts)


def _shown_no(raw: Any) -> str:
    from core.fy_serial import display_sales_bill_no

    return display_sales_bill_no(str(raw or ""))


def _shop_state(conn) -> str:
    try:
        from core.gst_reports import _shop

        return str(_shop(conn).get("state") or "")
    except Exception:
        return ""


def _party_gstins(conn) -> dict:
    try:
        from core.customer_gst import all_customer_gst

        return all_customer_gst(conn)
    except Exception:
        return {}


def _range(start: str, end: str) -> tuple[str, str]:
    a, b = _iso(start), _iso(end)
    if not a or not b or a > b:
        raise ValueError("Pick a From date on or before the To date.")
    return a, b


def _section(title: str, columns: list, rows: list, total_row: Optional[list] = None) -> dict:
    """A titled table. ``total_row`` is kept apart from the rows: the screen and CSV / Excel
    show it under them; PDF and paper add their own TOTAL line (core/dot_matrix_print), so
    the column names are chosen for that rule (money words are added, "Days Late" is not)."""
    return {"title": title, "columns": columns, "rows": rows, "total_row": total_row if rows else None}


# ── the wholesale invoices of a period ─────────────────────────────────────────────────

def _invoices(conn, start: str, end: str, party_id: int = 0) -> list[dict]:
    """Wholesale invoices dated start..end, each with its GST heads and its lines."""
    q = ["COALESCE(s.invoice_mode,'')='wholesale'", _live(conn, "sales", "s"),
         "substr(s.bill_date,1,10) >= ?", "substr(s.bill_date,1,10) <= ?"]
    params: list[Any] = [start, end]
    if party_id:
        q.append("s.customer_id = ?")
        params.append(int(party_id))
    rows = conn.execute(
        "SELECT s.id, s.bill_no, s.bill_date, COALESCE(s.customer_id,0), "
        "COALESCE(NULLIF(TRIM(c.name),''), s.customer_name, ''), COALESCE(s.buyer_gstin,''), "
        "COALESCE(s.place_of_supply,''), s.taxable_total, s.cgst_total, s.sgst_total, s.igst_total, "
        "COALESCE(s.total_amount,0), COALESCE(s.amount_paid,0), COALESCE(s.due_date,''), "
        "COALESCE(s.rounding,0) "
        "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id "
        f"WHERE {' AND '.join(q)} ORDER BY substr(s.bill_date,1,10), s.id", params).fetchall()
    ids = [int(r[0]) for r in rows]
    lines = _lines(conn, ids)
    from core.customer_gst import gstin_for_bill

    gst_of = _party_gstins(conn)
    shop_state = _shop_state(conn)
    out = []
    for (sid, no, bdate, cid, party, gstin, pos, taxable, cgst, sgst, igst, total, paid, due,
         rounding) in rows:
        ls = lines.get(int(sid), [])
        gstin = str(gstin or "").strip().upper()
        if not gstin:
            gstin = gstin_for_bill(gst_of.get(int(cid)), _iso(bdate))
        if taxable is None:
            # An invoice saved before its footer figures were kept: the lines' own.
            taxable_d = sum((ln["taxable"] for ln in ls), ZERO)
            tax = sum((ln["gst"] for ln in ls), ZERO)
            state = str(pos or "").strip()[:2] or (gstin[:2] if gstin else "")
            if state and shop_state and state != shop_state:
                ig, cg, sg = tax, ZERO, ZERO
            else:
                cg, sg = split_tax(tax)
                ig = ZERO
        else:
            taxable_d, cg, sg, ig = to_paise(taxable), to_paise(cgst), to_paise(sgst), to_paise(igst)
        out.append({"id": int(sid), "bill_no": _shown_no(no), "date": _iso(bdate), "customer_id": int(cid),
                    "party": str(party or ""), "gstin": gstin, "taxable": taxable_d, "cgst": cg, "sgst": sg,
                    "igst": ig, "total": to_paise(total), "paid_at_counter": to_paise(paid),
                    "due_date": _iso(due) if due else "", "rounding": to_paise(rounding), "lines": ls})
    return out


def _lines(conn, sale_ids: list[int]) -> dict:
    out: dict = defaultdict(list)
    have = _cols(conn, "medicines")
    batch = "COALESCE(NULLIF(si.batch_no,''), m.batch_no, '')" if "batch_no" in have else "COALESCE(si.batch_no,'')"
    hsn = "COALESCE(NULLIF(si.hsn_code,''), m.hsn_code, '')" if "hsn_code" in have else "COALESCE(si.hsn_code,'')"
    for i in range(0, len(sale_ids), 500):
        chunk = sale_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT si.sale_id, COALESCE(si.medicine_id,0), COALESCE(m.name,''), {batch}, {hsn},
                       COALESCE(si.qty,0), COALESCE(si.free_qty,0), si.taxable, si.gst_amt,
                       COALESCE(si.amount,0), COALESCE(si.gst_percent, m.gst_percent, 0),
                       {"m.type" if "type" in have else "''"}, {"m.unit" if "unit" in have else "''"}
                FROM sales_items si LEFT JOIN medicines m ON m.id = si.medicine_id
                WHERE si.sale_id IN ({marks}) AND {_live(conn, 'sales_items', 'si')}
                ORDER BY si.sale_id, si.id""", chunk):
            if r[7] is not None:
                taxable = to_paise(r[7])
                gst = to_paise(r[8]) if r[8] is not None else to_paise(float(taxable) * float(r[10] or 0) / 100)
            else:
                # a line without its own figures reads as GST reports read it (core/gst_reports):
                # the tax inside the amount
                amount, rate = to_paise(r[9]), float(r[10] or 0)
                taxable = to_paise(float(amount) * 100 / (100 + rate)) if rate > 0 else amount
                gst = amount - taxable
            # A wholesale line keeps qty in stock units with the free packs inside it
            # (core/wholesale_sale): the report counts sold packs and free packs.
            qty, free = float(r[5] or 0), float(r[6] or 0)
            if r[7] is not None:
                from core.wholesale_sale import units_per_pack

                ups = max(1, int(units_per_pack(r[11], r[12]) or 1))
                qty = max(0.0, round(qty / ups - free, 3))
            out[int(r[0])].append({
                "medicine_id": int(r[1]), "name": str(r[2] or ""), "batch": str(r[3] or ""), "hsn": str(r[4] or ""),
                "qty": qty, "free_qty": free, "taxable": taxable, "gst": gst,
                "amount": taxable + gst,
            })
    return out


def _clearing(conn, invoices: list[dict]) -> dict[int, Decimal]:
    """sale id -> what is still owed on it, by the dues rule (core/bill_clearing.py)."""
    from core.bill_clearing import party_ledgers

    left: dict[int, Decimal] = {}
    ids = sorted({inv["customer_id"] for inv in invoices if inv["customer_id"]})
    if ids:
        for led in party_ledgers(conn, "customer", ids).values():
            for b in led["result"]["bills"]:
                left[int(b["id"])] = to_paise(b["remaining"])
    for inv in invoices:
        if inv["id"] not in left:      # a walk-in invoice: only what was paid on it
            left[inv["id"]] = max(ZERO, inv["total"] - inv["paid_at_counter"])
    return left


# ── 1. sales register ──────────────────────────────────────────────────────────────────

REGISTER_COLUMNS = ["Invoice No", "Date", "Party", "GSTIN", "Taxable Value", "CGST", "SGST", "IGST", "Total",
                    "Due Date", "Paid", "Balance"]


def sales_register(conn, start: str, end: str, party_id: int = 0) -> dict:
    start, end = _range(start, end)
    invoices = _invoices(conn, start, end, party_id)
    left = _clearing(conn, invoices)
    rows = []
    tot = defaultdict(lambda: ZERO)
    for inv in invoices:
        bal = left.get(inv["id"], ZERO)
        paid = inv["total"] - bal
        rows.append([inv["bill_no"], _dmy(inv["date"]), inv["party"], inv["gstin"], _f(inv["taxable"]),
                     _f(inv["cgst"]), _f(inv["sgst"]), _f(inv["igst"]), _f(inv["total"]),
                     _dmy(inv["due_date"]) if inv["due_date"] else "", _f(paid), _f(bal)])
        for k, v in (("taxable", inv["taxable"]), ("cgst", inv["cgst"]), ("sgst", inv["sgst"]),
                     ("igst", inv["igst"]), ("total", inv["total"]), ("paid", paid), ("balance", bal)):
            tot[k] += v
    totals = {k: _f(tot[k]) for k in ("taxable", "cgst", "sgst", "igst", "total", "paid", "balance")}
    total_row = ["TOTAL", f"{len(invoices)} invoices", "", "", totals["taxable"], totals["cgst"],
                 totals["sgst"], totals["igst"], totals["total"], "", totals["paid"], totals["balance"]]
    return {"report": "register", "title": TITLES["register"], "subtitle": f"{_dmy(start)} to {_dmy(end)}",
            "count": len(invoices), "totals": totals,
            "sections": [_section(TITLES["register"], REGISTER_COLUMNS, rows, total_row)]}


# ── 2. party-wise and item-wise sales ──────────────────────────────────────────────────

PARTY_COLUMNS = ["Party", "GSTIN", "Bills", "Qty", "Free Qty", "Taxable Value", "GST", "Amount", "Invoice Total"]
ITEM_COLUMNS = ["Party", "Medicine", "Batch", "HSN", "Qty", "Free Qty", "Taxable Value", "GST", "Amount"]


def _qty(v: float) -> Any:
    return int(v) if float(v).is_integer() else round(float(v), 3)


def party_sales(conn, start: str, end: str) -> dict:
    start, end = _range(start, end)
    invoices = _invoices(conn, start, end)
    by: dict = {}
    for inv in invoices:
        key = inv["customer_id"] or inv["party"].upper()
        p = by.setdefault(key, {"party": inv["party"], "gstin": inv["gstin"], "n": 0, "qty": 0.0, "free": 0.0,
                                "taxable": ZERO, "gst": ZERO, "amount": ZERO, "total": ZERO})
        p["gstin"] = p["gstin"] or inv["gstin"]
        p["n"] += 1
        p["total"] += inv["total"]
        for ln in inv["lines"]:
            p["qty"] += ln["qty"]
            p["free"] += ln["free_qty"]
            p["taxable"] += ln["taxable"]
            p["gst"] += ln["gst"]
            p["amount"] += ln["amount"]
    rows = []
    t = {"n": 0, "qty": 0.0, "free": 0.0, "taxable": ZERO, "gst": ZERO, "amount": ZERO, "total": ZERO}
    for p in sorted(by.values(), key=lambda x: (-x["total"], x["party"].upper())):
        rows.append([p["party"], p["gstin"], p["n"], _qty(p["qty"]), _qty(p["free"]), _f(p["taxable"]),
                     _f(p["gst"]), _f(p["amount"]), _f(p["total"])])
        for k in t:
            t[k] += p[k]
    total_row = ["TOTAL", f"{len(by)} parties", t["n"], _qty(t["qty"]), _qty(t["free"]), _f(t["taxable"]),
                 _f(t["gst"]), _f(t["amount"]), _f(t["total"])]
    return {"report": "party", "title": TITLES["party"], "subtitle": f"{_dmy(start)} to {_dmy(end)}",
            "count": len(by), "totals": {"invoices": t["n"], "taxable": _f(t["taxable"]), "gst": _f(t["gst"]),
                                         "amount": _f(t["amount"]), "total": _f(t["total"])},
            "sections": [_section(TITLES["party"], PARTY_COLUMNS, rows, total_row)]}


def item_sales(conn, start: str, end: str, party_id: int = 0) -> dict:
    start, end = _range(start, end)
    invoices = _invoices(conn, start, end, party_id)
    by: dict = {}
    for inv in invoices:
        pkey = inv["customer_id"] or inv["party"].upper()
        for ln in inv["lines"]:
            key = (pkey, ln["medicine_id"] or ln["name"].upper(), ln["batch"])
            it = by.setdefault(key, {"party": inv["party"], "name": ln["name"], "batch": ln["batch"],
                                     "hsn": ln["hsn"], "qty": 0.0, "free": 0.0, "taxable": ZERO, "gst": ZERO,
                                     "amount": ZERO})
            it["qty"] += ln["qty"]
            it["free"] += ln["free_qty"]
            it["taxable"] += ln["taxable"]
            it["gst"] += ln["gst"]
            it["amount"] += ln["amount"]
    rows = []
    t = {"qty": 0.0, "free": 0.0, "taxable": ZERO, "gst": ZERO, "amount": ZERO}
    for it in sorted(by.values(), key=lambda x: (x["party"].upper(), x["name"].upper(), x["batch"])):
        rows.append([it["party"], it["name"], it["batch"], it["hsn"], _qty(it["qty"]), _qty(it["free"]),
                     _f(it["taxable"]), _f(it["gst"]), _f(it["amount"])])
        for k in t:
            t[k] += it[k]
    total_row = ["TOTAL", f"{len(by)} lines", "", "", _qty(t["qty"]), _qty(t["free"]), _f(t["taxable"]),
                 _f(t["gst"]), _f(t["amount"])]
    return {"report": "items", "title": TITLES["items"], "subtitle": f"{_dmy(start)} to {_dmy(end)}",
            "count": len(by), "totals": {"taxable": _f(t["taxable"]), "gst": _f(t["gst"]), "amount": _f(t["amount"])},
            "sections": [_section(TITLES["items"], ITEM_COLUMNS, rows, total_row)]}


# ── 3. outstanding and ageing ──────────────────────────────────────────────────────────

AGEING_COLUMNS = ["Party", "GSTIN", "Phone", "Bills", "Balance", *BUCKETS, "Days Late (oldest)",
                  "Limit", "Limit Crossed"]
OPEN_BILL_COLUMNS = ["Party", "Bill No", "Bill Date", "Due Date", "Bill Total", "Balance", "Days Late", "Bucket"]


def bucket(overdue_days: int) -> str:
    if overdue_days <= 0:
        return BUCKETS[0]
    if overdue_days <= 30:
        return BUCKETS[1]
    if overdue_days <= 60:
        return BUCKETS[2]
    if overdue_days <= 90:
        return BUCKETS[3]
    return BUCKETS[4]


def outstanding(conn, as_of: str = "", party_id: int = 0) -> dict:
    """Every party that still owes, its open bills aged from their due dates on ``as_of``."""
    from core.bill_clearing import party_ledgers

    when = _day(as_of) or date.today()
    have = _cols(conn, "customers")
    q = ["1=1"]
    if "deleted" in have:
        q.append("COALESCE(deleted,0)=0")
    params: list[Any] = []
    if party_id:
        q.append("id = ?")
        params.append(int(party_id))
    parties = {int(r[0]): r for r in conn.execute(
        "SELECT id, COALESCE(name,''), COALESCE(phone,''), "
        f"{'COALESCE(credit_limit,0)' if 'credit_limit' in have else '0'}, "
        f"{'COALESCE(credit_days,0)' if 'credit_days' in have else '0'} "
        f"FROM customers WHERE {' AND '.join(q)}", params).fetchall()}
    # the due date each bill was given; else the bill date (the owner's rule: credit days
    # count from the due date the invoice printed, a bill without one is due on its date)
    due_of = {int(r[0]): _iso(r[1]) for r in conn.execute(
        "SELECT id, due_date FROM sales WHERE COALESCE(due_date,'') <> ''"
        + (" AND customer_id = ?" if party_id else ""), params).fetchall()}
    gst_of = _party_gstins(conn)
    # a party's GSTIN as its latest wholesale invoice printed it, when its GSTIN list is empty
    invoice_gstin = {int(r[0]): str(r[1]).strip().upper() for r in conn.execute(
        "SELECT customer_id, buyer_gstin FROM sales WHERE COALESCE(buyer_gstin,'') <> '' "
        "AND COALESCE(customer_id,0) > 0 ORDER BY substr(bill_date,1,10), id").fetchall()}
    ledgers = party_ledgers(conn, "customer", list(parties))
    summary, detail = [], []
    tot = {b: ZERO for b in BUCKETS}
    tot_bal = ZERO
    n_bills = 0
    for pid, led in ledgers.items():
        rem = {int(b["id"]): to_paise(b["remaining"]) for b in led["result"]["bills"]}
        bills = sorted(led["bills"], key=lambda b: (str(b["date"])[:10], b["id"]))
        open_rows = []
        for b in bills:
            owed = rem.get(int(b["id"]), ZERO)
            if owed <= ZERO:
                continue
            bdate = _iso(b["date"])
            due = due_of.get(int(b["id"])) or bdate
            dd = _day(due)
            days = (when - dd).days if dd else 0
            open_rows.append((b, bdate, due, owed, days))
        if not open_rows:
            continue
        pid_row = parties.get(pid)
        name, phone, limit = (str(pid_row[1]), str(pid_row[2]), to_paise(pid_row[3])) if pid_row else ("", "", ZERO)
        gstin = str((gst_of.get(pid) or {}).get("gstin") or "") or invoice_gstin.get(pid, "")
        buckets = {b: ZERO for b in BUCKETS}
        bal = ZERO
        for b, bdate, due, owed, days in open_rows:
            bk = bucket(days)
            buckets[bk] += owed
            bal += owed
            detail.append([name, _shown_no(b["bill_no"]), _dmy(bdate), _dmy(due), _f(to_paise(b["total"])),
                           _f(owed), max(days, 0), bk])
        oldest = max((days for *_, days in open_rows), default=0)
        crossed = limit > 0 and bal > limit
        summary.append({"party": name, "gstin": gstin, "phone": phone, "bills": len(open_rows), "balance": bal,
                        "buckets": buckets, "oldest": max(oldest, 0), "limit": limit, "crossed": crossed})
        for k in BUCKETS:
            tot[k] += buckets[k]
        tot_bal += bal
        n_bills += len(open_rows)
    summary.sort(key=lambda s: (-s["oldest"], -s["balance"], s["party"].upper()))
    rows = [[s["party"], s["gstin"], s["phone"], s["bills"], _f(s["balance"]),
             *[_f(s["buckets"][b]) for b in BUCKETS], s["oldest"],
             _f(s["limit"]) if s["limit"] > 0 else "", ("Yes" if s["crossed"] else "No") if s["limit"] > 0 else ""]
            for s in summary]
    total_row = ["TOTAL", f"{len(summary)} parties", "", n_bills, _f(tot_bal), *[_f(tot[b]) for b in BUCKETS],
                 "", "", f"{sum(1 for s in summary if s['crossed'])} crossed"]
    detail_total = ["TOTAL", f"{n_bills} bills", "", "", "", _f(tot_bal), "", ""]
    detail.sort(key=lambda r: (r[0].upper(), r[2][6:] + r[2][3:5] + r[2][:2]))
    return {"report": "outstanding", "title": TITLES["outstanding"], "subtitle": f"as on {when.strftime('%d-%m-%Y')}",
            "as_of": when.isoformat(), "count": len(summary),
            "totals": {"balance": _f(tot_bal), **{b: _f(tot[b]) for b in BUCKETS}, "bills": n_bills,
                       "crossed": sum(1 for s in summary if s["crossed"])},
            "sections": [_section("Outstanding by party", AGEING_COLUMNS, rows, total_row),
                         _section("Open bills", OPEN_BILL_COLUMNS, detail, detail_total)]}


# ── 4. price list ──────────────────────────────────────────────────────────────────────

def price_list(conn, tier: str = "", in_stock_only: bool = True, search: str = "") -> dict:
    """Every medicine batch with its rates. ``tier`` A / B / C: that rate alone, without the
    purchase rate -- the list a party of that tier is given."""
    tier = str(tier or "").strip().upper()[:1]
    if tier and tier not in RATE_TIERS:
        raise ValueError("Tier is A, B or C.")
    have = _cols(conn, "medicines")

    def col(name: str, default: str = "NULL") -> str:
        return f"m.{name}" if name in have else default

    q = ["1=1"]
    for c in ("deleted", "is_hidden"):
        if c in have:
            q.append(f"COALESCE(m.{c},0)=0")
    params: list[Any] = []
    if in_stock_only:
        q.append("COALESCE(m.stock_qty,0) > 0")
    if str(search or "").strip():
        q.append("UPPER(m.name) LIKE ?")
        params.append(f"%{str(search).strip().upper()}%")
    e = "''"
    rows_db = conn.execute(
        f"SELECT COALESCE(m.name,''), COALESCE({col('batch_no', e)},''), COALESCE({col('expiry_date', e)},''), "
        f"COALESCE({col('hsn_code', e)},''), COALESCE({col('gst_percent', '0')},0), "
        f"COALESCE({col('purchase_rate')}, {col('rate')}), "
        f"{col('rate_a')}, {col('rate_b')}, {col('rate_c')}, COALESCE({col('stock_qty', '0')},0), "
        f"COALESCE({col('unit', e)},''), COALESCE({col('type', e)},'') "
        f"FROM medicines m WHERE {' AND '.join(q)} ORDER BY UPPER(m.name), {col('expiry_date', e)}",
        params).fetchall()

    def money(v: Any) -> Any:
        return "" if v is None or v == "" else _f(to_paise(v))

    def gst(v: Any) -> str:
        # "12%" as text: a rate is never added up under the printed list
        f = float(v or 0)
        return f"{int(f) if f.is_integer() else f}%"

    from core.wholesale_sale import units_per_pack

    def strips(r) -> Any:
        # Wholesale counts strips (packs), never tablets, and has no MRP (owner, 18 Oct 2026).
        return _qty(round(float(r[9] or 0) / max(1, units_per_pack(r[11], r[10])), 2))

    if tier:
        idx = {"A": 6, "B": 7, "C": 8}[tier]
        columns = ["Medicine", "Type", "Batch", "Expiry", "HSN", "GST %", f"Rate {tier}", "Stock (strips)"]
        rows = [[r[0], r[11], r[1], r[2], r[3], gst(r[4]), money(r[idx]), strips(r)] for r in rows_db]
        title = f"Price list - Rate {tier}"
    else:
        columns = ["Medicine", "Type", "Batch", "Expiry", "HSN", "GST %", "Purchase Rate", "Rate A", "Rate B",
                   "Rate C", "Stock (strips)"]
        rows = [[r[0], r[11], r[1], r[2], r[3], gst(r[4]), money(r[5]), money(r[6]), money(r[7]), money(r[8]),
                 strips(r)] for r in rows_db]
        title = TITLES["price_list"]
    missing = sum(1 for r in rows_db if (r[{"A": 6, "B": 7, "C": 8}[tier]] if tier else r[6]) in (None, 0, 0.0))
    return {"report": "price_list", "title": title,
            "subtitle": f"{date.today().strftime('%d-%m-%Y')} · rates exclude GST"
                        + (" · in stock only" if in_stock_only else ""),
            "count": len(rows), "tier": tier, "without_rate": missing,
            "sections": [_section(title, columns, rows)]}


# ── one entry for the API ──────────────────────────────────────────────────────────────

def refusal(conn) -> str:
    """Why these reports cannot be read now, or "" when they can."""
    from core.wholesale_schema import WHOLESALE, get_business_mode

    try:
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            return ("Wholesale reports read this PC's own books; an Online store's bills are on "
                    "the server. Use Offline-first.")
    except Exception:
        pass
    if get_business_mode(conn) != WHOLESALE:
        return "Wholesale reports are for a store switched to wholesale."
    return ""


def _month_start() -> str:
    return date.today().replace(day=1).isoformat()


def build(conn, report: str, params: dict) -> dict:
    report = str(report or "").strip().lower()
    start = str(params.get("from") or "") or _month_start()
    end = str(params.get("to") or "") or date.today().isoformat()
    try:
        party_id = int(params.get("party_id") or 0)
    except (TypeError, ValueError):
        party_id = 0
    if report == "register":
        return sales_register(conn, start, end, party_id)
    if report == "party":
        return party_sales(conn, start, end)
    if report == "items":
        return item_sales(conn, start, end, party_id)
    if report == "outstanding":
        return outstanding(conn, str(params.get("as_of") or ""), party_id)
    if report == "price_list":
        flag = str(params.get("in_stock") if params.get("in_stock") is not None else "1").strip().lower()
        return price_list(conn, str(params.get("tier") or ""), flag not in ("0", "false", "no"),
                          str(params.get("search") or ""))
    raise ValueError(f"Unknown wholesale report: {report or '(none)'}")


def parties(conn) -> list[dict]:
    """The parties for the filter: every medical party and anyone with a wholesale bill."""
    have = _cols(conn, "customers")
    kind = "COALESCE(c.party_type,'')" if "party_type" in have else "''"
    live = "COALESCE(c.deleted,0)=0" if "deleted" in have else "1=1"
    rows = conn.execute(
        f"SELECT c.id, COALESCE(c.name,''), {kind} FROM customers c WHERE {live} AND ({kind}='medical' OR EXISTS "
        "(SELECT 1 FROM sales s WHERE s.customer_id=c.id AND COALESCE(s.invoice_mode,'')='wholesale')) "
        "ORDER BY UPPER(c.name)").fetchall()
    return [{"id": int(r[0]), "name": str(r[1])} for r in rows]


def export(conn, body: dict) -> dict:
    """A report as CSV / Excel / PDF, or on paper: body {report, from, to, ..., format |
    print_to, page_layout} -- the same files and printing as every other report."""
    from core.desktop_export_service import export_alert_sections

    rep = build(conn, str(body.get("report") or ""), body)
    secs = [s for s in rep["sections"] if s["rows"]]
    if not secs:
        return {"ok": False, "error": "Nothing to export in this report."}
    fmt = str(body.get("format") or "csv").strip().lower()
    if not body.get("print_to") and fmt in ("csv", "xlsx", "excel"):
        # a file the shop works on takes the report's own TOTAL line; PDF and paper add theirs
        secs = [{**s, "rows": s["rows"] + ([s["total_row"]] if s.get("total_row") else [])} for s in secs]
    title = f"{rep['title']} {rep['subtitle']}".strip()
    layout = body.get("page_layout") or ("portrait" if rep["report"] in ("party",) else "landscape")
    res = export_alert_sections({**body, "title": title, "sections": secs, "page_layout": layout})
    if res.get("ok") and res.get("filename"):
        stem = rep["title"].lower().replace(" - ", "_").replace(" ", "_").replace("-", "_")
        if not stem.startswith("wholesale"):
            stem = f"wholesale_{stem}"
        # no date here: the save adds the date and time (core/saved_files.dated_name)
        res["filename"] = f"{stem}.{res.get('format') or 'csv'}"
    return res
