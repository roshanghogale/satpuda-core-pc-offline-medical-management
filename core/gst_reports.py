"""GST reports: GSTR-1, GSTR-3B, the sales GST register and the purchase ITC register.

Every sale figure is the printed bill's own. A bill's tax comes out of each line through
``core.bill_gst.printed_bill_gst`` -- the bill discount spread over the lines, half-up to
the paisa -- so a report row adds up to exactly what the customer's bill said, and the
rate-wise totals are sums of those paise, never a fresh calculation on a total.

A line's GST rate is the printed bill's: the rate kept on the sale line, else the
medicine's (``COALESCE(si.gst_percent, m.gst_percent, 0)``). Lines whose rate came from
the medicine, and lines with no HSN code, are listed in ``checks`` -- the report does not
guess for them, it says which bills to look at.

Nothing here writes: the reports read the store (SQLite Offline, the server's documents
Online) and the customers' GSTINs (core.customer_gst).

GSTR-1 tables:
  B2B     bills to a customer whose GSTIN was on file on the bill date
  B2CL    to an unregistered customer of another state, invoice value above 1,00,000
  B2CS    every other bill, rate-wise, NET of the credit notes on B2C bills
  CDNR    credit notes (sales returns) on B2B bills
  CDNUR   credit notes on B2CL bills
  HSN     Table 12, B2B and B2C apart, net of credit notes
  DOCS    Table 13: bill numbers issued, missing numbers counted as cancelled
GSTR-3B: 3.1(a) taxable, 3.1(c) nil-rated, 3.2 inter-state to unregistered,
         4 ITC from registered suppliers' bills, less supplier returns, 5 exempt inward.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Optional

from core.bill_gst import ZERO, printed_bill_gst, split_tax, to_paise
from core.customer_gst import STATES, clean_gstin, gstin_for_bill, state_of

SERIES_GAP = 100                         # numbers missing in a row before a series is split
B2CL_LIMIT = Decimal("100000")          # unregistered inter-state invoices above this are B2CL
CENT = Decimal("0.01")

HSN_DESC = {
    "3001": "Glands and organs for therapeutic uses", "3002": "Vaccines, sera, blood fractions",
    "3003": "Medicaments (not in measured doses)", "3004": "Medicaments (in measured doses)",
    "3005": "Wadding, gauze, bandages", "3006": "Pharmaceutical goods", "2309": "Animal feed preparations",
    "3401": "Soap", "3304": "Beauty or make-up preparations", "3306": "Oral or dental hygiene preparations",
    "3808": "Insecticides, disinfectants", "9018": "Medical instruments and appliances",
    "4015": "Gloves of rubber", "2106": "Food preparations n.e.c.", "3824": "Chemical preparations",
}
_TABLET_TYPES = {"tablet", "capsule", "bolus", "tablet pack", "tab", "cap"}


def _d(value) -> Decimal:
    return to_paise(value)


def _f(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _rate(value) -> Decimal:
    return Decimal(repr(round(float(value or 0), 2))).quantize(CENT)


def _rate_out(rate: Decimal) -> float:
    r = float(rate)
    return int(r) if r == int(r) else r


def _hsn(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _hsn_problem(code: str) -> str:
    if not code:
        return "HSN nahi"
    if len(code) < 4 or len(code) > 8:
        return f"HSN {code} chukla (4 te 8 ank)"
    return ""


def _uqc(kind: str) -> str:
    return "TBS" if str(kind or "").strip().lower() in _TABLET_TYPES else "NOS"


def _iso(value) -> str:
    s = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _dmy(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return iso


def _shown_no(raw) -> str:
    from core.fy_serial import display_sales_bill_no

    return display_sales_bill_no(str(raw or ""))


# ── the data the reports are made of ───────────────────────────────────────────────────

@dataclass
class SaleLine:
    medicine_id: int
    name: str
    hsn: str
    kind: str
    qty: Decimal
    amount: Decimal          # what the line sold for, tax included, before the bill discount
    rate: Decimal            # GST %
    rate_from: str           # "line" | "master" | "none"


@dataclass
class Bill:
    id: int
    no: str
    date: str
    customer_id: int
    customer: str
    discount: Decimal
    rounding: Decimal
    total: Decimal
    lines: list
    gstin: str = ""
    pos: str = ""


@dataclass
class Note:                  # a sales return = a credit note
    id: int
    no: str
    date: str
    bill: Optional[Bill]
    refund: Decimal
    lines: list              # [(SaleLine of the bill, qty returned, value before the return discount)]
    problem: str = ""


@dataclass
class PurchaseLine:
    hsn: str
    rate: Decimal
    qty: Decimal
    taxable: Decimal
    tax: Decimal
    known: bool


@dataclass
class Purchase:
    id: int
    no: str
    supplier: str
    gstin: str
    bill_number: str
    date: str
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    tax: Decimal
    value: Decimal
    lines: list
    lines_known: bool


@dataclass
class PurchaseReturn:
    id: int
    no: str
    date: str
    purchase: Optional[Purchase]
    taxable: Decimal
    tax: Decimal
    value: Decimal
    known: bool


@dataclass
class Period:
    start: str
    end: str
    shop: dict
    bills: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    purchases: list = field(default_factory=list)
    purchase_returns: list = field(default_factory=list)
    checks: list = field(default_factory=list)       # [(what, bill/record, detail)]


# ── reading the store ───────────────────────────────────────────────────────────────────

def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _shop(conn) -> dict:
    from core.pharmacy_profile_io import load_pharmacy_profile

    p = load_pharmacy_profile(conn) or {}
    g = clean_gstin(p.get("gstin"))
    st = state_of(g)
    flag = str(p.get("gst_enabled") if p.get("gst_enabled") is not None else "1").strip().lower()
    return {"name": str(p.get("name") or ""), "gstin": g, "state": st, "state_name": STATES.get(st, ""),
            "gst_enabled": flag not in ("0", "false", "no", "")}


_COLS: dict = {}


def _cols(conn, table: str) -> set:
    """The columns this store's table has. An old store lacks some (Matoshree's sales have
    no customer_name): a report reads what is there and never fails on what is not."""
    key = (id(conn), table)
    if key not in _COLS:
        try:
            _COLS[key] = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except Exception:
            _COLS[key] = set()
    return _COLS[key]


def _c(conn, table: str, alias: str, col: str, default: str = "0") -> str:
    """``COALESCE(alias.col, default)`` when the column exists, else the default itself."""
    if col in _cols(conn, table):
        return f"COALESCE({alias + '.' if alias else ''}{col}, {default})"
    return default


def _raw(conn, table: str, alias: str, col: str) -> str:
    """``alias.col`` when it exists, else NULL (a value that may be missing, kept missing)."""
    return f"{alias + '.' if alias else ''}{col}" if col in _cols(conn, table) else "NULL"


def _live(conn, table: str, alias: str = "") -> str:
    """The filter for rows that count: not deleted, not an autosave draft (where kept)."""
    parts = ["1=1"]
    for col in ("deleted", "is_autosave"):
        if col in _cols(conn, table):
            parts.append(f"COALESCE({alias + '.' if alias else ''}{col},0)=0")
    return " AND ".join(parts)


def _sale_lines_offline(conn, sale_ids: list) -> dict:
    out: dict = defaultdict(list)
    for i in range(0, len(sale_ids), 500):
        chunk = sale_ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT si.sale_id, si.medicine_id, {_c(conn, 'medicines', 'm', 'name', "''")},
                       {_c(conn, 'medicines', 'm', 'hsn_code', "''")}, {_c(conn, 'medicines', 'm', 'type', "''")},
                       COALESCE(si.qty,0), {_c(conn, 'sales_items', 'si', 'amount')},
                       {_raw(conn, 'sales_items', 'si', 'gst_percent')}, {_raw(conn, 'medicines', 'm', 'gst_percent')}
                FROM sales_items si LEFT JOIN medicines m ON m.id = si.medicine_id
                WHERE si.sale_id IN ({q})
                ORDER BY si.sale_id, si.id""", chunk):
            line_rate, master_rate = r[7], r[8]
            rate_from = "line" if line_rate is not None else ("master" if master_rate is not None else "none")
            rate = line_rate if line_rate is not None else (master_rate or 0)
            out[int(r[0])].append(SaleLine(int(r[1] or 0), str(r[2]), _hsn(r[3]), str(r[4]),
                                           Decimal(str(r[5] or 0)), _d(r[6]), _rate(rate), rate_from))
    return out


def _sale_head(conn) -> str:
    return (f"SELECT id, bill_no, bill_date, {_c(conn, 'sales', '', 'customer_id')}, "
            f"{_c(conn, 'sales', '', 'customer_name', chr(39) * 2)}, {_c(conn, 'sales', '', 'discount')}, "
            f"{_c(conn, 'sales', '', 'rounding')}, {_c(conn, 'sales', '', 'total_amount')} FROM sales")


def _bills_offline(conn, start: str, end: str) -> list:
    rows = conn.execute(
        f"""{_sale_head(conn)} WHERE {_live(conn, 'sales')}
             AND bill_date >= ? AND bill_date <= ? ORDER BY bill_date, id""", (start, end)).fetchall()
    lines = _sale_lines_offline(conn, [int(r[0]) for r in rows])
    return [Bill(int(r[0]), _shown_no(r[1]), _iso(r[2]), int(r[3] or 0), str(r[4]), _d(r[5]), _d(r[6]),
                 _d(r[7]), lines.get(int(r[0]), [])) for r in rows]


def _bill_by_id_offline(conn, sale_id: int) -> Optional[Bill]:
    r = conn.execute(f"{_sale_head(conn)} WHERE id=?", (int(sale_id),)).fetchone()
    if not r:
        return None
    lines = _sale_lines_offline(conn, [int(r[0])])
    return Bill(int(r[0]), _shown_no(r[1]), _iso(r[2]), int(r[3] or 0), str(r[4]), _d(r[5]), _d(r[6]),
                _d(r[7]), lines.get(int(r[0]), []))


def _medicine_master_online(mid: int) -> dict:
    try:
        from core.online_catalog import medicine_by_id

        return medicine_by_id(int(mid)) or {}
    except Exception:
        return {}


def _bill_from_doc(doc: dict) -> Bill:
    lines = []
    for it in doc.get("items") or doc.get("medicines") or []:
        if not isinstance(it, dict):
            continue
        mid = int(it.get("medicine_id") or 0)
        master = _medicine_master_online(mid) if mid else {}
        line_rate = it.get("gst_percent")
        master_rate = master.get("gst_percent")
        rate_from = "line" if line_rate is not None else ("master" if master_rate is not None else "none")
        rate = line_rate if line_rate is not None else (master_rate or 0)
        lines.append(SaleLine(mid, str(it.get("name") or it.get("medicine_name") or master.get("name") or ""),
                              _hsn(it.get("hsn_code") or master.get("hsn_code")),
                              str(it.get("type") or master.get("type") or ""),
                              Decimal(str(it.get("qty") or 0)), _d(it.get("amount")), _rate(rate), rate_from))
    return Bill(int(doc.get("id") or doc.get("local_id") or 0), _shown_no(doc.get("bill_no")),
                _iso(doc.get("bill_date")), int(doc.get("customer_id") or 0), str(doc.get("customer_name") or ""),
                _d(doc.get("discount")), _d(doc.get("rounding")), _d(doc.get("total_amount")), lines)


def _bills_online(start: str, end: str) -> list:
    from core.desktop_export_service import _online_sale_docs

    return [_bill_from_doc(d) for d in _online_sale_docs(start, end)]


def _notes_offline(conn, start: str, end: str, bills_by_id: dict) -> list:
    out = []
    if not _cols(conn, "sales_returns") or not _cols(conn, "sales_return_items"):
        return out
    rows = conn.execute(
        f"""SELECT id, return_no, return_date, sale_id, {_c(conn, 'sales_returns', '', 'refund_amount')}
           FROM sales_returns WHERE {_live(conn, 'sales_returns')} AND return_date >= ? AND return_date <= ?
           ORDER BY return_date, id""", (start, end)).fetchall()
    for r in rows:
        items = conn.execute(
            "SELECT medicine_id, COALESCE(qty,0) FROM sales_return_items WHERE return_id=? ORDER BY id",
            (int(r[0]),)).fetchall()
        bill = bills_by_id.get(int(r[3] or 0)) or (_bill_by_id_offline(conn, int(r[3])) if r[3] else None)
        out.append(_note(int(r[0]), str(r[1] or ""), _iso(r[2]), bill, _d(r[4]),
                         [(int(m or 0), Decimal(str(q or 0))) for m, q in items]))
    return out


def _notes_online(start: str, end: str, bills_by_id: dict) -> list:
    from core import store_query_client as sq
    from core.server_crud import get_doc

    res = sq.list_sales_returns(limit=5000, from_date=start, to_date=end) or {}
    out = []
    for r in res.get("rows") or res.get("returns") or []:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        rd = _iso(r.get("return_date"))
        if not (start <= rd <= end):
            continue
        doc = get_doc("sales_returns", int(r.get("id"))) or r
        sid = int(doc.get("sale_id") or 0)
        bill = bills_by_id.get(sid)
        if bill is None and sid:
            try:
                bill = _bill_from_doc(get_doc("sales", sid) or {})
            except Exception:
                bill = None
        items = [(int(i.get("medicine_id") or 0), Decimal(str(i.get("qty") or 0)))
                 for i in doc.get("items") or [] if isinstance(i, dict)]
        out.append(_note(int(doc.get("id") or 0), str(doc.get("return_no") or ""), rd, bill,
                         _d(doc.get("refund_amount")), items))
    return out


def _note(nid: int, no: str, when: str, bill: Optional[Bill], refund: Decimal, items: list) -> Note:
    """A return's lines priced as the refund was: the bill line's own price per unit
    (calc_engine.calc_return_refund, amount / qty sold), less the return discount."""
    if bill is None:
        return Note(nid, no, when, None, refund, [], "Mool bill sapadla nahi")
    lines = []
    left = {i: ln.qty for i, ln in enumerate(bill.lines)}
    for mid, qty in items:
        idx = next((i for i, ln in enumerate(bill.lines) if ln.medicine_id == mid and left[i] >= qty), None)
        if idx is None:
            idx = next((i for i, ln in enumerate(bill.lines) if ln.medicine_id == mid), None)
        if idx is None:
            return Note(nid, no, when, bill, refund, [], "Return chi goli mool bill madhe nahi")
        ln = bill.lines[idx]
        left[idx] = left[idx] - qty
        per = ln.amount / ln.qty if ln.qty else ZERO
        lines.append((ln, qty, _d(per * qty)))
    return Note(nid, no, when, bill, refund, lines)


def _purchases_offline(conn, start: str, end: str) -> list:
    q = chr(39) * 2
    rows = conn.execute(
        f"""SELECT p.id, {_c(conn, 'purchases', 'p', 'purchase_no', q)}, {_c(conn, 'suppliers', 's', 'name', q)},
                  {_c(conn, 'suppliers', 's', 'gstin', q)}, {_c(conn, 'purchases', 'p', 'bill_number', q)},
                  p.purchase_date, {_c(conn, 'purchases', 'p', 'subtotal')}, {_c(conn, 'purchases', 'p', 'cgst')},
                  {_c(conn, 'purchases', 'p', 'sgst')}, {_c(conn, 'purchases', 'p', 'total_gst')},
                  {_c(conn, 'purchases', 'p', 'total_amount')}
           FROM purchases p LEFT JOIN suppliers s ON s.id = p.supplier_id
           WHERE {_live(conn, 'purchases', 'p')}
             AND p.purchase_date >= ? AND p.purchase_date <= ? ORDER BY p.purchase_date, p.id""",
        (start, end)).fetchall()
    out = []
    for r in rows:
        items = conn.execute(
            f"""SELECT COALESCE({_raw(conn, 'purchase_items', 'pi', 'hsn_code')},
                               {_raw(conn, 'medicines', 'm', 'hsn_code')}, ''),
                      COALESCE({_raw(conn, 'purchase_items', 'pi', 'gst_pct')},
                               {_raw(conn, 'purchase_items', 'pi', 'gst_percent')}, 0),
                      COALESCE(pi.qty,0), {_raw(conn, 'purchase_items', 'pi', 'taxable')},
                      COALESCE({_raw(conn, 'purchase_items', 'pi', 'gst_amt')},
                               {_raw(conn, 'purchase_items', 'pi', 'gst_value')}),
                      COALESCE({_raw(conn, 'purchase_items', 'pi', 'item_amount')},
                               {_raw(conn, 'purchase_items', 'pi', 'amount')}, 0)
               FROM purchase_items pi LEFT JOIN medicines m ON m.id = pi.medicine_id
               WHERE pi.purchase_id=? ORDER BY pi.id""", (int(r[0]),)).fetchall()
        out.append(_purchase(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), _iso(r[5]), r[6], r[7],
                             r[8], r[9], r[10], [dict(hsn=i[0], rate=i[1], qty=i[2], taxable=i[3], tax=i[4],
                                                     amount=i[5]) for i in items]))
    return out


def _purchase_columns(conn) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(purchases)")}


def _purchases_online(start: str, end: str) -> list:
    from core import store_query_client as sq
    from core.server_crud import get_doc

    sup = {}
    try:
        for s in (sq.list_suppliers(limit=5000) or {}).get("rows") or []:
            sup[int(s.get("id") or 0)] = clean_gstin(s.get("gstin"))
    except Exception:
        pass
    out = []
    for r in (sq.list_purchases(from_date=start, to_date=end, limit=5000) or {}).get("rows") or []:
        if not isinstance(r, dict) or r.get("deleted") or int(r.get("is_autosave") or 0):
            continue
        doc = get_doc("purchases", int(r.get("id") or 0)) or r
        if doc.get("deleted") or int(doc.get("is_autosave") or 0):
            continue
        g = sup.get(int(doc.get("supplier_id") or 0)) or clean_gstin(doc.get("supplier_gstin"))
        items = [dict(hsn=i.get("hsn_code"), rate=i.get("gst_pct", i.get("gst_percent")), qty=i.get("qty"),
                      taxable=i.get("taxable"), tax=i.get("gst_amt"), amount=i.get("item_amount") or i.get("amount"))
                 for i in doc.get("items") or [] if isinstance(i, dict)]
        out.append(_purchase(int(doc.get("id") or 0), str(doc.get("purchase_no") or ""),
                             str(doc.get("supplier_name") or ""), g, str(doc.get("bill_number") or ""),
                             _iso(doc.get("purchase_date")), doc.get("subtotal"), doc.get("cgst"), doc.get("sgst"),
                             doc.get("total_gst"), doc.get("total_amount"), items))
    return out


def _purchase(pid, no, supplier, gstin, bill_number, when, subtotal, cgst, sgst, total_gst, total, items) -> Purchase:
    """A supplier bill as the supplier billed it: the header (its printed footer) is the ITC.

    Lines are used for the rate-wise split only when every line carries its taxable value;
    a bill synced without them is shown with its totals and marked, not estimated.
    """
    lines = []
    known = True
    for i in items:
        rate = _rate(i.get("rate"))
        taxable = i.get("taxable")
        tax = i.get("tax")
        ok = taxable is not None and float(taxable or 0) > 0
        if not ok and rate == 0 and float(i.get("amount") or 0) > 0:
            taxable, tax, ok = i.get("amount"), 0, True       # a 0% line: its amount is its value
        known = known and ok
        lines.append(PurchaseLine(_hsn(i.get("hsn")), rate, Decimal(str(i.get("qty") or 0)),
                                  _d(taxable) if ok else ZERO, _d(tax) if ok else ZERO, ok))
    tax = _d(total_gst)
    c, s = _d(cgst), _d(sgst)
    if c + s != tax:
        c, s = split_tax(tax)
    return Purchase(pid, no, supplier, clean_gstin(gstin), bill_number, when, _d(subtotal), c, s, tax,
                    _d(total), lines, known and bool(lines))


def _purchase_returns_offline(conn, start: str, end: str, purchases_by_id: dict) -> list:
    out = []
    if not _cols(conn, "purchase_returns") or not _cols(conn, "purchase_return_items"):
        return out
    rows = conn.execute(
        f"""SELECT id, return_no, return_date, purchase_id, {_c(conn, 'purchase_returns', '', 'refund_amount')}
           FROM purchase_returns WHERE {_live(conn, 'purchase_returns')} AND return_date >= ? AND return_date <= ?
           ORDER BY return_date, id""", (start, end)).fetchall()
    for r in rows:
        items = conn.execute(
            f"""SELECT pri.medicine_id, COALESCE(pri.qty,0), pi.qty, pi.taxable, pi.gst_amt
               FROM purchase_return_items pri
               LEFT JOIN (SELECT medicine_id, SUM(COALESCE(qty,0)) AS qty,
                                 CASE WHEN MIN({_c(conn, 'purchase_items', '', 'taxable')}) > 0
                                      THEN SUM({_c(conn, 'purchase_items', '', 'taxable')}) END AS taxable,
                                 SUM({_c(conn, 'purchase_items', '', 'gst_amt')}) AS gst_amt
                          FROM purchase_items WHERE purchase_id=? GROUP BY medicine_id) pi
                      ON pi.medicine_id = pri.medicine_id
               WHERE pri.return_id=?""", (int(r[3] or 0), int(r[0]))).fetchall()
        p = purchases_by_id.get(int(r[3] or 0))
        if p is None and r[3]:
            found = _purchases_by_ids_offline(conn, [int(r[3])])
            p = found[0] if found else None
        out.append(_preturn(int(r[0]), str(r[1] or ""), _iso(r[2]), p, r[4],
                            [(q, oq, t, g) for _, q, oq, t, g in items]))
    return out


def _purchases_by_ids_offline(conn, ids: list) -> list:
    out = []
    for pid in ids:
        r = conn.execute("SELECT purchase_date FROM purchases WHERE id=?", (pid,)).fetchone()
        if r:
            out += [p for p in _purchases_offline(conn, _iso(r[0]), _iso(r[0])) if p.id == pid]
    return out


def _purchase_returns_online(start: str, end: str, purchases_by_id: dict) -> list:
    from core import store_query_client as sq
    from core.server_crud import get_doc

    out = []
    res = sq.list_purchase_returns(limit=5000, from_date=start, to_date=end) or {}
    for r in res.get("rows") or res.get("returns") or []:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        rd = _iso(r.get("return_date"))
        if not (start <= rd <= end):
            continue
        doc = get_doc("purchase_returns", int(r.get("id"))) or r
        pid = int(doc.get("purchase_id") or 0)
        p = purchases_by_id.get(pid)
        pdoc_items = {}
        try:
            pdoc = get_doc("purchases", pid) or {}
            for i in pdoc.get("items") or []:
                pdoc_items.setdefault(int(i.get("medicine_id") or 0), i)
            if p is None:
                p = _purchase(pid, str(pdoc.get("purchase_no") or ""), str(pdoc.get("supplier_name") or ""),
                              "", str(pdoc.get("bill_number") or ""), _iso(pdoc.get("purchase_date")),
                              pdoc.get("subtotal"), pdoc.get("cgst"), pdoc.get("sgst"), pdoc.get("total_gst"),
                              pdoc.get("total_amount"), [])
        except Exception:
            pass
        rows = []
        for i in doc.get("items") or []:
            o = pdoc_items.get(int(i.get("medicine_id") or 0)) or {}
            rows.append((i.get("qty"), o.get("qty"), o.get("taxable"), o.get("gst_amt")))
        out.append(_preturn(int(doc.get("id") or 0), str(doc.get("return_no") or ""), rd, p,
                            doc.get("refund_amount"), rows))
    return out


def _preturn(rid, no, when, purchase, refund, rows) -> PurchaseReturn:
    """GST on goods sent back: the original line's taxable and tax per unit, times the units returned."""
    taxable = tax = ZERO
    known = bool(rows)
    for qty, oqty, otax_able, otax in rows:
        q, oq = Decimal(str(qty or 0)), Decimal(str(oqty or 0))
        if not oq or otax_able is None or float(otax_able or 0) <= 0:
            known = False
            continue
        taxable += _d(Decimal(str(otax_able)) * q / oq)
        tax += _d(Decimal(str(otax or 0)) * q / oq)
    return PurchaseReturn(rid, no, when, purchase, taxable, tax, _d(refund), known)


def load_period(conn, start: str, end: str) -> Period:
    """Every bill, credit note, supplier bill and supplier return dated start..end (ISO dates)."""
    from core.customer_gst import all_customer_gst

    start, end = _iso(start), _iso(end)
    _COLS.clear()
    if not start or not end or start > end:
        raise ValueError("Kalavadhi chukla (pasun / paryant)")
    shop = _shop(conn)
    online = _online()
    period = Period(start, end, shop)
    period.bills = _bills_online(start, end) if online else _bills_offline(conn, start, end)
    by_id = {b.id: b for b in period.bills}
    period.notes = _notes_online(start, end, by_id) if online else _notes_offline(conn, start, end, by_id)
    period.purchases = _purchases_online(start, end) if online else _purchases_offline(conn, start, end)
    pby = {p.id: p for p in period.purchases}
    period.purchase_returns = (_purchase_returns_online(start, end, pby) if online
                               else _purchase_returns_offline(conn, start, end, pby))
    gst_of = all_customer_gst(conn)
    for b in period.bills + [n.bill for n in period.notes if n.bill]:
        entry = gst_of.get(b.customer_id)
        b.gstin = gstin_for_bill(entry, b.date)
        b.pos = (entry or {}).get("state") if b.gstin else shop["state"]
        b.pos = b.pos or shop["state"]
    _collect_checks(period)
    return period


def _collect_checks(p: Period) -> None:
    if not p.shop["gstin"]:
        p.checks.append(("Dukanacha GSTIN nahi", "Settings → Pharmacy", "GSTR-1 / 3B sathi aadhi bhara"))
    if not p.shop["gst_enabled"]:
        p.checks.append(("Bill var GST band aahe", "Settings → Pharmacy",
                         "bill var GST chhapla jaat nahi; report madhe MRP madhla GST mojla aahe"))
    for b in p.bills:
        if not b.lines and b.total != 0:
            # A bill with an amount and not one medicine line (an old due entered as a bill,
            # or lines that never synced). Its rate cannot be known: it is in no tax table,
            # and listed here with its amount so the shop / CA decides.
            p.checks.append(("Bill var aushadh lines nahit", b.no,
                             f"Rs {b.total}: GST rate mahit nahi -- GST tables madhe nahi, tapasa"))
        for ln in b.lines:
            if ln.medicine_id and not ln.name:
                p.checks.append(("Aushadh master madhe nahi", b.no,
                                 f"medicine #{ln.medicine_id}: chhapil bill chya GST madhe hi line nasel -- tapasa"))
            if ln.rate_from == "master":
                p.checks.append(("GST % bill var navhta", b.no,
                                 f"{ln.name}: aushadhacha aatacha {_rate_out(ln.rate)}% vaparla (chhapil bill pramane)"))
            elif ln.rate_from == "none":
                p.checks.append(("GST % kuthech nahi", b.no, f"{ln.name}: 0% dharla (chhapil bill pramane)"))
            hp = _hsn_problem(ln.hsn)
            if hp:
                p.checks.append((hp, b.no, ln.name))
    for n in p.notes:
        if n.problem:
            p.checks.append((n.problem, n.no, "credit note report madhe nahi -- tapasa"))
    for pu in p.purchases:
        if not pu.lines_known:
            p.checks.append(("Purchase rate-wise mahit nahi", pu.bill_number or pu.no,
                             "bill chi ekun GST barobar; rate-wise vibhagani nahi"))
        lt = sum((ln.tax for ln in pu.lines if ln.known), ZERO)
        if pu.lines_known and abs(lt - pu.tax) > Decimal("1.00"):
            p.checks.append(("Supplier bill GST ani lines madhe pharak", pu.bill_number or pu.no,
                             f"bill {pu.tax} / lines {lt}: ITC bill pramane"))
    for r in p.purchase_returns:
        if not r.known:
            p.checks.append(("Purchase return GST mahit nahi", r.no, "mool purchase line madhe taxable nahi"))


# ── computing ───────────────────────────────────────────────────────────────────────────

def bill_tax(b: Bill):
    """The printed bill's own figures for each line."""
    return printed_bill_gst([(ln.amount, ln.rate) for ln in b.lines], b.discount)


def note_tax(n: Note):
    """A credit note's lines with the return discount spread as a bill's discount is."""
    gross = sum((v for _, _, v in n.lines), ZERO)
    disc = max(ZERO, gross - n.refund)
    return printed_bill_gst([(v, ln.rate) for ln, _, v in n.lines], disc)


def _inter(state: str, shop_state: str) -> bool:
    return bool(state and shop_state and state != shop_state)


def _split(tax: Decimal, inter: bool) -> tuple:
    if inter:
        return tax, ZERO, ZERO
    c, s = split_tax(tax)
    return ZERO, c, s


def _bill_kind(b: Bill, shop_state: str) -> str:
    if b.gstin:
        return "b2b"
    if _inter(b.pos, shop_state) and b.total > B2CL_LIMIT:
        return "b2cl"
    return "b2cs"


def sales_register(p: Period) -> list:
    """One row per bill and GST rate: the figures printed on the bill."""
    rows = []
    for b in p.bills:
        g = bill_tax(b)
        by = defaultdict(lambda: [ZERO, ZERO])
        for line in g.lines:
            by[line.rate][0] += line.taxable
            by[line.rate][1] += line.tax
        inter = _inter(b.pos, p.shop["state"])
        for rate in sorted(by):
            taxable, tax = by[rate]
            i, c, s = _split(tax, inter)
            rows.append({"date": b.date, "bill_no": b.no, "customer": b.customer, "gstin": b.gstin,
                         "pos": b.pos, "type": _bill_kind(b, p.shop["state"]).upper(), "rate": _rate_out(rate),
                         "taxable": _f(taxable), "igst": _f(i), "cgst": _f(c), "sgst": _f(s),
                         "bill_total": _f(b.total)})
    return rows


def gstr1(p: Period) -> dict:
    shop_state = p.shop["state"]
    out = {"b2b": [], "b2cl": [], "b2cs": [], "cdnr": [], "cdnur": [], "hsn_b2b": [], "hsn_b2c": [], "docs": []}
    b2cs = defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO])          # (pos, rate) -> taxable, igst, cgst, sgst
    hsn = {"hsn_b2b": defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO, ZERO, ZERO, "", ""]),
           "hsn_b2c": defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO, ZERO, ZERO, "", ""])}

    def add_hsn(table, ln, line_gst, inter, sign):
        key = (ln.hsn or "", ln.rate, _uqc(ln.kind))
        row = hsn[table][key]
        i, c, s = _split(line_gst.tax, inter)
        row[0] += sign * ln.qty if isinstance(ln.qty, Decimal) else ZERO
        row[1] += sign * line_gst.net
        row[2] += sign * line_gst.taxable
        row[3] += sign * i
        row[4] += sign * c
        row[5] += sign * s
        row[6] = row[6] or HSN_DESC.get((ln.hsn or "")[:4], "") or ln.name

    for b in p.bills:
        g = bill_tax(b)
        kind = _bill_kind(b, shop_state)
        inter = _inter(b.pos, shop_state)
        by = defaultdict(lambda: [ZERO, ZERO])
        for ln, lg in zip(b.lines, g.lines):
            by[lg.rate][0] += lg.taxable
            by[lg.rate][1] += lg.tax
            add_hsn("hsn_b2b" if kind == "b2b" else "hsn_b2c", ln, lg, inter, 1)
        if kind == "b2cs":
            for rate, (taxable, tax) in by.items():
                i, c, s = _split(tax, inter)
                row = b2cs[(b.pos, rate)]
                row[0] += taxable
                row[1] += i
                row[2] += c
                row[3] += s
            continue
        for rate in sorted(by):
            taxable, tax = by[rate]
            i, c, s = _split(tax, inter)
            out[kind].append({"gstin": b.gstin, "receiver": b.customer, "invoice_no": b.no, "date": b.date,
                              "value": _f(b.total), "pos": b.pos, "reverse_charge": "N",
                              "invoice_type": "Regular B2B" if kind == "b2b" else "", "rate": _rate_out(rate),
                              "taxable": _f(taxable), "igst": _f(i), "cgst": _f(c), "sgst": _f(s), "cess": 0.0})

    for n in p.notes:
        if n.problem or not n.bill:
            continue
        g = note_tax(n)
        kind = _bill_kind(n.bill, shop_state)
        inter = _inter(n.bill.pos, shop_state)
        by = defaultdict(lambda: [ZERO, ZERO])
        for (ln, qty, _), lg in zip(n.lines, g.lines):
            by[lg.rate][0] += lg.taxable
            by[lg.rate][1] += lg.tax
            ret = SaleLine(ln.medicine_id, ln.name, ln.hsn, ln.kind, qty, lg.amount, ln.rate, ln.rate_from)
            add_hsn("hsn_b2b" if kind == "b2b" else "hsn_b2c", ret, lg, inter, -1)
        if kind == "b2cs":
            # A return on a B2C bill is not a credit note in GSTR-1: it lowers B2CS.
            for rate, (taxable, tax) in by.items():
                i, c, s = _split(tax, inter)
                row = b2cs[(n.bill.pos, rate)]
                row[0] -= taxable
                row[1] -= i
                row[2] -= c
                row[3] -= s
            continue
        table = "cdnr" if kind == "b2b" else "cdnur"
        for rate in sorted(by):
            taxable, tax = by[rate]
            i, c, s = _split(tax, inter)
            out[table].append({"gstin": n.bill.gstin, "receiver": n.bill.customer, "note_no": n.no, "date": n.date,
                               "note_type": "C", "invoice_no": n.bill.no, "invoice_date": n.bill.date,
                               "pos": n.bill.pos, "value": _f(n.refund), "rate": _rate_out(rate),
                               "taxable": _f(taxable), "igst": _f(i), "cgst": _f(c), "sgst": _f(s), "cess": 0.0,
                               "ur_type": "B2CL" if table == "cdnur" else ""})

    for (pos, rate) in sorted(b2cs, key=lambda k: (k[0], k[1])):
        taxable, i, c, s = b2cs[(pos, rate)]
        if taxable == 0 and i == 0 and c == 0 and s == 0:
            continue
        # One split of the row's whole tax: summing each bill's half-up CGST ran ahead of
        # SGST by a paisa a bill (759.98 against 759.51 over a quarter at one shop); the
        # portal expects CGST = SGST. The tax itself is the bills' own, to the paisa.
        c, s = split_tax(c + s)
        out["b2cs"].append({"type": "OE", "pos": pos, "supply": "INTER" if _inter(pos, shop_state) else "INTRA",
                            "rate": _rate_out(rate), "taxable": _f(taxable), "igst": _f(i), "cgst": _f(c),
                            "sgst": _f(s), "cess": 0.0})
    for table in ("hsn_b2b", "hsn_b2c"):
        for (code, rate, uqc), r in sorted(hsn[table].items(), key=lambda kv: (kv[0][0], kv[0][1])):
            c, s = split_tax(r[4] + r[5])
            out[table].append({"hsn": code, "description": r[6], "uqc": uqc, "qty": float(r[0]),
                               "value": _f(r[1]), "rate": _rate_out(rate), "taxable": _f(r[2]), "igst": _f(r[3]),
                               "cgst": _f(c), "sgst": _f(s), "cess": 0.0,
                               "problem": _hsn_problem(code)})
    out["docs"] = documents(p)
    return out


def documents(p: Period) -> list:
    """Table 13: each number series from its first to its last number; a missing number
    (a bill deleted after it was made) counts as cancelled."""
    def series(nos: Iterable[str], nature: str) -> list:
        groups: dict = defaultdict(set)
        odd = []
        for no in nos:
            m = re.match(r"^(.*?)(\d+)$", str(no or "").strip())
            if m:
                groups[m.group(1)].add(int(m.group(2)))
            elif no:
                odd.append(no)
        rows = []
        for prefix, nums in sorted(groups.items()):
            # A run breaks where more than SERIES_GAP numbers are missing at once: that is not
            # a few deleted bills but another run (a queued return's temporary number such as
            # SR1117777925 made one series "SR1 to SR1117777925", a billion cancelled).
            runs: list = []
            for n in sorted(nums):
                if runs and n - runs[-1][-1] <= SERIES_GAP:
                    runs[-1].append(n)
                else:
                    runs.append([n])
            for run in runs:
                lo, hi = run[0], run[-1]
                total = hi - lo + 1
                rows.append({"nature": nature, "from": f"{prefix}{lo}", "to": f"{prefix}{hi}", "total": total,
                             "cancelled": total - len(run), "net_issued": len(run)})
        for no in odd:
            rows.append({"nature": nature, "from": no, "to": no, "total": 1, "cancelled": 0, "net_issued": 1})
        return rows
    return (series([b.no for b in p.bills], "Invoices for outward supply")
            + series([n.no for n in p.notes], "Credit Note"))


def purchase_register(p: Period) -> list:
    """One row per supplier bill: the supplier's own footer (its GST is the ITC)."""
    shop_state = p.shop["state"]
    rows = []
    for pu in p.purchases:
        inter = _inter(state_of(pu.gstin), shop_state)
        i, c, s = (pu.tax, ZERO, ZERO) if inter else (ZERO, pu.cgst, pu.sgst)
        rates = sorted({_rate_out(ln.rate) for ln in pu.lines})
        rows.append({"date": pu.date, "supplier": pu.supplier, "gstin": pu.gstin, "bill_no": pu.bill_number,
                     "entry_no": pu.no, "rates": ", ".join(f"{r}%" for r in rates), "taxable": _f(pu.taxable),
                     "igst": _f(i), "cgst": _f(c), "sgst": _f(s), "total_gst": _f(pu.tax), "value": _f(pu.value),
                     "itc": "Ho" if pu.gstin else "Nahi (supplier GSTIN nahi)"})
    return rows


def purchase_rate_summary(p: Period) -> list:
    shop_state = p.shop["state"]
    by = defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO])
    for pu in p.purchases:
        if not pu.lines_known:
            continue
        inter = _inter(state_of(pu.gstin), shop_state)
        for ln in pu.lines:
            i, c, s = _split(ln.tax, inter)
            row = by[ln.rate]
            row[0] += ln.taxable
            row[1] += i
            row[2] += c
            row[3] += s
    return [{"rate": _rate_out(r), "taxable": _f(v[0]), "igst": _f(v[1]), "cgst": _f(v[2]), "sgst": _f(v[3])}
            for r, v in sorted(by.items())]


def gstr3b(p: Period, g1: Optional[dict] = None) -> dict:
    g1 = g1 or gstr1(p)
    out_tax = [ZERO] * 4        # taxable, igst, cgst, sgst  (rate > 0)
    nil = ZERO
    inter_unreg = defaultdict(lambda: [ZERO, ZERO])               # pos -> taxable, igst
    for table in ("b2b", "b2cl", "b2cs"):
        for r in g1[table]:
            vals = [Decimal(str(r["taxable"])), Decimal(str(r["igst"])), Decimal(str(r["cgst"])),
                    Decimal(str(r["sgst"]))]
            if Decimal(str(r["rate"])) == 0:
                nil += vals[0]
                continue
            out_tax = [a + v for a, v in zip(out_tax, vals)]
            if table != "b2b" and r.get("pos") and _inter(r["pos"], p.shop["state"]):
                inter_unreg[r["pos"]][0] += vals[0]
                inter_unreg[r["pos"]][1] += vals[1]
    for table in ("cdnr", "cdnur"):
        for r in g1[table]:
            vals = [Decimal(str(r["taxable"])), Decimal(str(r["igst"])), Decimal(str(r["cgst"])),
                    Decimal(str(r["sgst"]))]
            if Decimal(str(r["rate"])) == 0:
                nil -= vals[0]
                continue
            out_tax = [a - v for a, v in zip(out_tax, vals)]
    itc = [ZERO] * 3
    no_itc = ZERO
    exempt_in = ZERO
    for pu in p.purchases:
        if not pu.gstin:
            no_itc += pu.tax
            continue
        inter = _inter(state_of(pu.gstin), p.shop["state"])
        if inter:
            itc[0] += pu.tax
        else:
            itc[1] += pu.cgst
            itc[2] += pu.sgst
        if pu.lines_known:
            exempt_in += sum((ln.taxable for ln in pu.lines if ln.rate == 0), ZERO)
    rev = [ZERO] * 3
    for r in p.purchase_returns:
        if not r.purchase or not r.purchase.gstin:
            continue
        inter = _inter(state_of(r.purchase.gstin), p.shop["state"])
        i, c, s = _split(r.tax, inter)
        rev = [rev[0] + i, rev[1] + c, rev[2] + s]
    net = [a - b for a, b in zip(itc, rev)]
    return {
        "3.1(a) Outward taxable supplies": {"taxable": _f(out_tax[0]), "igst": _f(out_tax[1]),
                                            "cgst": _f(out_tax[2]), "sgst": _f(out_tax[3]), "cess": 0.0},
        "3.1(c) Nil rated / exempted": {"taxable": _f(nil)},
        "3.2 Inter-state to unregistered": [{"pos": pos, "taxable": _f(v[0]), "igst": _f(v[1])}
                                            for pos, v in sorted(inter_unreg.items())],
        "4(A)(5) All other ITC": {"igst": _f(itc[0]), "cgst": _f(itc[1]), "sgst": _f(itc[2]), "cess": 0.0},
        "4(B)(2) Reversed: goods returned to suppliers": {"igst": _f(rev[0]), "cgst": _f(rev[1]),
                                                          "sgst": _f(rev[2])},
        "4(C) Net ITC available": {"igst": _f(net[0]), "cgst": _f(net[1]), "sgst": _f(net[2]), "cess": 0.0},
        "5 Exempt / nil-rated inward (intra-state)": {"value": _f(exempt_in)},
        "GST on bills from unregistered suppliers (no ITC)": {"tax": _f(no_itc)},
        "Tax payable before ITC": {"igst": _f(out_tax[1]), "cgst": _f(out_tax[2]), "sgst": _f(out_tax[3])},
    }


def gstr1_json(p: Period, g1: Optional[dict] = None) -> dict:
    """GSTR-1 in the GST portal's JSON layout (to be checked in the offline tool before filing)."""
    g1 = g1 or gstr1(p)
    if not p.shop["gstin"]:
        raise ValueError("Dukanacha GSTIN nahi -- Settings → Pharmacy madhe bhara")
    fp = datetime.strptime(p.end, "%Y-%m-%d").strftime("%m%Y")

    def det(r):
        d = {"txval": r["taxable"], "rt": r["rate"], "csamt": 0}
        if r["igst"]:
            d["iamt"] = r["igst"]
        else:
            d.update(camt=r["cgst"], samt=r["sgst"])
        return d

    out: dict = {"gstin": p.shop["gstin"], "fp": fp}
    b2b: dict = defaultdict(lambda: defaultdict(list))
    vals: dict = {}
    for r in g1["b2b"]:
        b2b[r["gstin"]][r["invoice_no"]].append(r)
        vals[r["invoice_no"]] = r
    if b2b:
        out["b2b"] = [{"ctin": g, "inv": [{"inum": no, "idt": _dmy(vals[no]["date"]), "val": vals[no]["value"],
                                          "pos": vals[no]["pos"], "rchrg": "N", "inv_typ": "R",
                                          "itms": [{"num": k + 1, "itm_det": det(r)} for k, r in enumerate(rs)]}
                                         for no, rs in invs.items()]} for g, invs in b2b.items()]
    if g1["b2cl"]:
        by_pos: dict = defaultdict(lambda: defaultdict(list))
        for r in g1["b2cl"]:
            by_pos[r["pos"]][r["invoice_no"]].append(r)
        out["b2cl"] = [{"pos": pos, "inv": [{"inum": no, "idt": _dmy(rs[0]["date"]), "val": rs[0]["value"],
                                            "itms": [{"num": k + 1, "itm_det": {"txval": r["taxable"], "rt": r["rate"],
                                                                                "iamt": r["igst"], "csamt": 0}}
                                                     for k, r in enumerate(rs)]} for no, rs in invs.items()]}
                       for pos, invs in by_pos.items()]
    if g1["b2cs"]:
        out["b2cs"] = []
        for r in g1["b2cs"]:
            row = {"sply_ty": r["supply"], "rt": r["rate"], "typ": "OE", "pos": r["pos"], "txval": r["taxable"],
                   "csamt": 0}
            if r["supply"] == "INTER":
                row["iamt"] = r["igst"]
            else:
                row.update(camt=r["cgst"], samt=r["sgst"])
            out["b2cs"].append(row)
    if g1["cdnr"]:
        by: dict = defaultdict(lambda: defaultdict(list))
        for r in g1["cdnr"]:
            by[r["gstin"]][r["note_no"]].append(r)
        out["cdnr"] = [{"ctin": g, "nt": [{"ntty": "C", "nt_num": no, "nt_dt": _dmy(rs[0]["date"]),
                                          "val": rs[0]["value"], "pos": rs[0]["pos"], "rchrg": "N", "inv_typ": "R",
                                          "itms": [{"num": k + 1, "itm_det": det(r)} for k, r in enumerate(rs)]}
                                         for no, rs in notes.items()]} for g, notes in by.items()]
    if g1["cdnur"]:
        by = defaultdict(list)
        for r in g1["cdnur"]:
            by[r["note_no"]].append(r)
        out["cdnur"] = [{"typ": "B2CL", "ntty": "C", "nt_num": no, "nt_dt": _dmy(rs[0]["date"]), "val": rs[0]["value"],
                         "pos": rs[0]["pos"], "itms": [{"num": k + 1, "itm_det": {"txval": r["taxable"], "rt": r["rate"],
                                                                                  "iamt": r["igst"], "csamt": 0}}
                                                       for k, r in enumerate(rs)]} for no, rs in by.items()]

    def hsn_rows(rows):
        return [{"num": k + 1, "hsn_sc": r["hsn"], "desc": r["description"][:30], "uqc": r["uqc"],
                 "qty": r["qty"], "rt": r["rate"], "txval": r["taxable"], "iamt": r["igst"], "camt": r["cgst"],
                 "samt": r["sgst"], "csamt": 0} for k, r in enumerate(x for x in rows if not x["problem"])]
    out["hsn"] = {"hsn_b2b": hsn_rows(g1["hsn_b2b"]), "hsn_b2c": hsn_rows(g1["hsn_b2c"])}
    docs = []
    for num, nature in ((1, "Invoices for outward supply"), (5, "Credit Note")):
        rows = [d for d in g1["docs"] if d["nature"] == nature]
        if rows:
            docs.append({"doc_num": num, "docs": [{"num": k + 1, "from": d["from"], "to": d["to"],
                                                    "totnum": d["total"], "cancel": d["cancelled"],
                                                    "net_issue": d["net_issued"]} for k, d in enumerate(rows)]})
    if docs:
        out["doc_issue"] = {"doc_det": docs}
    return out


def month_range(year: int, month: int) -> tuple:
    import calendar

    return date(year, month, 1).isoformat(), date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def build(conn, start: str, end: str) -> dict:
    """Everything the GST Reports window shows, for one period."""
    p = load_period(conn, start, end)
    g1 = gstr1(p)
    report = {
        "period": {"from": p.start, "to": p.end},
        "shop": p.shop,
        "sales_register": sales_register(p),
        "gstr1": g1,
        "gstr3b": gstr3b(p, g1),
        "purchase_register": purchase_register(p),
        "purchase_rates": purchase_rate_summary(p),
        "checks": [{"what": a, "where": b, "detail": c} for a, b, c in p.checks],
        "counts": {"bills": len(p.bills), "credit_notes": len(p.notes), "purchases": len(p.purchases),
                   "purchase_returns": len(p.purchase_returns)},
    }
    report["sections"] = sections(report)
    return report


# ── on paper / in a file ────────────────────────────────────────────────────────────────

def _pos(code: str) -> str:
    return f"{code}-{STATES.get(code, '')}" if code else ""


def sections(report: dict) -> list:
    """The report as titled tables, named and laid out as the GSTR-1 offline-tool Excel sheets
    (plus the tax amounts a CA checks), for Excel / CSV / PDF / printing."""
    g1 = report["gstr1"]
    t3b = report["gstr3b"]
    out = []
    rows3b = []
    for name, v in t3b.items():
        if isinstance(v, list):
            for r in v:
                rows3b.append([f"{name} ({_pos(r['pos'])})", r["taxable"], r["igst"], "", "", ""])
            continue
        rows3b.append([name, v.get("taxable", v.get("value", v.get("tax", ""))), v.get("igst", ""),
                       v.get("cgst", ""), v.get("sgst", ""), v.get("cess", "")])
    out.append({"title": "3B Summary", "columns": ["Table", "Taxable / Value", "IGST", "CGST", "SGST", "Cess"],
                "rows": rows3b})
    out.append({"title": "Sales GST Register",
                "columns": ["Date", "Bill No", "Customer", "GSTIN", "Place Of Supply", "Type", "Rate",
                            "Taxable Value", "IGST", "CGST", "SGST", "Bill Total"],
                "rows": [[_dmy(r["date"]), r["bill_no"], r["customer"], r["gstin"], _pos(r["pos"]), r["type"],
                          r["rate"], r["taxable"], r["igst"], r["cgst"], r["sgst"], r["bill_total"]]
                         for r in report["sales_register"]]})
    out.append({"title": "b2b", "columns": ["GSTIN/UIN of Recipient", "Receiver Name", "Invoice Number",
                                             "Invoice date", "Invoice Value", "Place Of Supply", "Reverse Charge",
                                             "Applicable % of Tax Rate", "Invoice Type", "E-Commerce GSTIN", "Rate",
                                             "Taxable Value", "Cess Amount", "IGST", "CGST", "SGST"],
                "rows": [[r["gstin"], r["receiver"], r["invoice_no"], _dmy(r["date"]), r["value"], _pos(r["pos"]),
                          "N", "", "Regular B2B", "", r["rate"], r["taxable"], 0, r["igst"], r["cgst"], r["sgst"]]
                         for r in g1["b2b"]]})
    out.append({"title": "b2cl", "columns": ["Invoice Number", "Invoice date", "Invoice Value", "Place Of Supply",
                                              "Applicable % of Tax Rate", "Rate", "Taxable Value", "Cess Amount",
                                              "E-Commerce GSTIN", "IGST"],
                "rows": [[r["invoice_no"], _dmy(r["date"]), r["value"], _pos(r["pos"]), "", r["rate"], r["taxable"],
                          0, "", r["igst"]] for r in g1["b2cl"]]})
    out.append({"title": "b2cs", "columns": ["Type", "Place Of Supply", "Applicable % of Tax Rate", "Rate",
                                              "Taxable Value", "Cess Amount", "E-Commerce GSTIN", "IGST", "CGST",
                                              "SGST"],
                "rows": [[r["type"], _pos(r["pos"]), "", r["rate"], r["taxable"], 0, "", r["igst"], r["cgst"],
                          r["sgst"]] for r in g1["b2cs"]]})
    out.append({"title": "cdnr", "columns": ["GSTIN/UIN of Recipient", "Receiver Name", "Note Number", "Note Date",
                                              "Note Type", "Place Of Supply", "Reverse Charge", "Note Supply Type",
                                              "Note Value", "Applicable % of Tax Rate", "Rate", "Taxable Value",
                                              "Cess Amount", "Against Invoice", "IGST", "CGST", "SGST"],
                "rows": [[r["gstin"], r["receiver"], r["note_no"], _dmy(r["date"]), "C", _pos(r["pos"]), "N",
                          "Regular B2B", r["value"], "", r["rate"], r["taxable"], 0,
                          f"{r['invoice_no']} ({_dmy(r['invoice_date'])})", r["igst"], r["cgst"], r["sgst"]]
                         for r in g1["cdnr"]]})
    out.append({"title": "cdnur", "columns": ["UR Type", "Note Number", "Note Date", "Note Type", "Place Of Supply",
                                               "Note Value", "Applicable % of Tax Rate", "Rate", "Taxable Value",
                                               "Cess Amount", "IGST"],
                "rows": [["B2CL", r["note_no"], _dmy(r["date"]), "C", _pos(r["pos"]), r["value"], "", r["rate"],
                          r["taxable"], 0, r["igst"]] for r in g1["cdnur"]]})
    hsn_cols = ["HSN", "Description", "UQC", "Total Quantity", "Total Value", "Rate", "Taxable Value",
                "Integrated Tax Amount", "Central Tax Amount", "State/UT Tax Amount", "Cess Amount", "Check"]
    for table, title in (("hsn_b2b", "hsn(b2b)"), ("hsn_b2c", "hsn(b2c)")):
        out.append({"title": title, "columns": hsn_cols,
                     "rows": [[r["hsn"], r["description"], r["uqc"], r["qty"], r["value"], r["rate"], r["taxable"],
                               r["igst"], r["cgst"], r["sgst"], 0, r["problem"]] for r in g1[table]]})
    out.append({"title": "docs", "columns": ["Nature of Document", "Sr. No. From", "Sr. No. To", "Total Number",
                                              "Cancelled"],
                "rows": [[d["nature"], d["from"], d["to"], d["total"], d["cancelled"]] for d in g1["docs"]]})
    out.append({"title": "Purchase ITC Register",
                "columns": ["Date", "Supplier", "Supplier GSTIN", "Supplier Bill No", "Entry No", "Rates",
                            "Taxable Value", "IGST", "CGST", "SGST", "Total GST", "Bill Value", "ITC"],
                "rows": [[_dmy(r["date"]), r["supplier"], r["gstin"], r["bill_no"], r["entry_no"], r["rates"],
                          r["taxable"], r["igst"], r["cgst"], r["sgst"], r["total_gst"], r["value"], r["itc"]]
                         for r in report["purchase_register"]]})
    out.append({"title": "Purchase Rate-wise", "columns": ["Rate", "Taxable Value", "IGST", "CGST", "SGST"],
                "rows": [[r["rate"], r["taxable"], r["igst"], r["cgst"], r["sgst"]]
                         for r in report["purchase_rates"]]})
    out.append({"title": "Checks", "columns": ["What", "Bill / Record", "Detail"],
                "rows": [[c["what"], c["where"], c["detail"]] for c in report["checks"]]})
    return out


def export(conn, body: dict) -> dict:
    """GST Reports window -> Excel / CSV / PDF / paper (every table, or the ones picked),
    or the GSTR-1 JSON for the portal's offline tool."""
    import base64
    import json

    start, end = str(body.get("from") or ""), str(body.get("to") or "")
    fmt = str(body.get("format") or "xlsx").lower()
    shop = (_shop(conn).get("gstin") or "GST")
    stem = f"GST_{shop}_{_iso(start)}_{_iso(end)}"
    if fmt == "json":
        p = load_period(conn, start, end)
        data = json.dumps(gstr1_json(p), ensure_ascii=False, indent=1).encode("utf-8")
        return {"ok": True, "filename": f"GSTR1_{stem}.json", "mime": "application/json", "format": "json",
                "content_base64": base64.b64encode(data).decode("ascii")}
    report = build(conn, start, end)
    want = {str(t) for t in body.get("tables") or []}
    secs = [s for s in sections(report) if not want or s["title"] in want]
    from core.desktop_export_service import export_alert_sections

    res = export_alert_sections({**body, "title": f"GST Reports {_dmy(_iso(start))} - {_dmy(_iso(end))}",
                                 "sections": secs})
    if res.get("ok") and res.get("filename"):
        res["filename"] = f"{stem}.{res.get('format') or fmt}"
    return res
