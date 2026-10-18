"""The wholesale sale: a B2B tax invoice to another medical shop (owner, 18 Oct 2026).

Only a wholesale store (core/wholesale_schema.get_business_mode: the open store's kind) makes these; a
retail bill is saved, stored and printed exactly as before. See
D:\\projects\\WHOLESALE_DESIGN.md.

A line is sold in whole packs at a rate that EXCLUDES GST, with GST added on top:

    gross   = packs x rate
    disc    = gross x disc% / 100
    scheme  = (gross - disc) x scheme% / 100
    taxable = gross - disc - scheme
    GST     = taxable x GST% / 100          (CGST = half rounded up, SGST the rest
    amount  = taxable + GST                  when the buyer is in the shop's state, else IGST)

all in Decimal, half-up to the paisa. The bill's grand total is the lines' amounts rounded
to the rupee; the difference is the round off.

How a line is stored, so stock, returns and every retail report stay right:

    sales_items.qty            units leaving stock = (packs + free packs) x units per pack,
                               the same units a retail line uses (tablets of a strip type)
    sales_items.rate           what one of those units cost the buyer, GST included
                               (amount / qty) -- what retail reports and returns read
    sales_items.amount         taxable + GST
    sales_items.item_discount  disc + scheme in rupees
    sales_items.free_qty       free PACKS
    sales_items.scheme_pct, taxable, gst_amt, rate_tier, batch_no, expiry_date, hsn_code

The pack rate and disc % printed on the invoice are worked back from these
(``line_from_row``). The sale row carries invoice_mode='wholesale' and the invoice header
(buyer GSTIN / DL, place of supply, tax totals, due date, transport / LR / e-way bill no).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Optional

from core.wholesale_schema import RATE_TIERS, WHOLESALE, get_business_mode

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

SALE_FIELDS = (
    "invoice_mode", "buyer_gstin", "buyer_dl", "place_of_supply", "taxable_total",
    "cgst_total", "sgst_total", "igst_total", "due_date", "transport", "lr_no", "eway_bill_no",
)
LINE_FIELDS = (
    "rate_tier", "free_qty", "scheme_pct", "taxable", "gst_amt", "batch_no", "expiry_date",
    "hsn_code",
)


# ── numbers ───────────────────────────────────────────────────────────────────

def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        x = float(str(v).replace(",", "").strip()) if isinstance(v, str) else float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def _d(v: Any, places: int = 2) -> Decimal:
    q = Decimal(1).scaleb(-places)
    return Decimal(repr(round(_f(v), places))).quantize(q, rounding=ROUND_HALF_UP)


def _paise(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def _money(v: Decimal) -> float:
    return float(_paise(v))


def _int(v: Any) -> int:
    try:
        return int(round(_f(v)))
    except (TypeError, ValueError, OverflowError):
        return 0


def split_half(tax: Decimal) -> tuple[Decimal, Decimal]:
    """(CGST, SGST): half rounded up, and the rest (core/bill_gst.split_tax)."""
    from core.bill_gst import split_tax

    return split_tax(_paise(tax))


# ── GST state ─────────────────────────────────────────────────────────────────

def state_from_gstin(gstin: Any) -> str:
    from core.customer_gst import state_of

    return state_of(gstin)


def state_name(code: Any) -> str:
    from core.customer_gst import STATES

    return STATES.get(str(code or "").strip().zfill(2) if str(code or "").strip() else "", "")


def clean_state(code: Any) -> str:
    s = "".join(ch for ch in str(code or "") if ch.isdigit())[:2]
    if len(s) == 1:
        s = "0" + s
    return s if state_name(s) else ""


def place_of_supply(buyer_gstin: Any, buyer_state: Any, shop_state: Any) -> str:
    """Buyer GSTIN's state first, else the party's state code, else the shop's state."""
    return state_from_gstin(buyer_gstin) or clean_state(buyer_state) or clean_state(shop_state)


def is_intra_state(pos: str, shop_state: str) -> bool:
    """Same state as the shop (or the shop's state unknown) -> CGST + SGST, else IGST."""
    return not shop_state or not pos or pos == shop_state


# ── the sum ───────────────────────────────────────────────────────────────────

def calc_line(raw: dict, *, intra: bool = True) -> dict[str, Any]:
    """One invoice line's figures. ``raw``: qty (packs), free, rate (per pack, GST excluded),
    disc_pct, scheme_pct, gst_percent."""
    packs = Decimal(max(0, _int(raw.get("qty"))))
    rate = _d(raw.get("rate"), 4)
    disc_pct = _d(raw.get("disc_pct"), 2)
    scheme_pct = _d(raw.get("scheme_pct"), 2)
    gst_pct = _d(raw.get("gst_percent"), 2)
    gross = _paise(packs * rate)
    disc = _paise(gross * disc_pct / HUNDRED)
    scheme = _paise((gross - disc) * scheme_pct / HUNDRED)
    taxable = gross - disc - scheme
    gst = _paise(taxable * gst_pct / HUNDRED)
    if intra:
        cgst, sgst = split_half(gst)
        igst = ZERO
    else:
        cgst = sgst = ZERO
        igst = gst
    return {
        "qty": int(packs),
        "free": max(0, _int(raw.get("free", raw.get("free_qty")))),
        "rate": float(rate),
        "disc_pct": float(disc_pct),
        "scheme_pct": float(scheme_pct),
        "gst_percent": float(gst_pct),
        "gross": float(gross),
        "disc_amt": float(disc),
        "scheme_amt": float(scheme),
        "taxable": float(taxable),
        "gst_amt": float(gst),
        "cgst": float(cgst),
        "sgst": float(sgst),
        "igst": float(igst),
        "amount": float(taxable + gst),
    }


def calc_invoice(lines: Iterable[dict], *, shop_state: str = "", pos: str = "") -> dict[str, Any]:
    """Every line, the HSN-wise tax summary and the totals of one invoice."""
    shop_state = clean_state(shop_state)
    pos = clean_state(pos) or shop_state
    intra = is_intra_state(pos, shop_state)
    out_lines = []
    hsn: dict[tuple[str, float], dict[str, Decimal]] = {}
    tot = {k: ZERO for k in ("gross", "disc", "taxable", "cgst", "sgst", "igst", "amount")}
    for raw in lines:
        line = dict(raw)
        line.update(calc_line(raw, intra=intra))
        out_lines.append(line)
        tot["gross"] += _d(line["gross"])
        tot["disc"] += _d(line["disc_amt"]) + _d(line["scheme_amt"])
        tot["taxable"] += _d(line["taxable"])
        tot["cgst"] += _d(line["cgst"])
        tot["sgst"] += _d(line["sgst"])
        tot["igst"] += _d(line["igst"])
        tot["amount"] += _d(line["amount"])
        key = (str(line.get("hsn_code") or "").strip(), float(line["gst_percent"]))
        h = hsn.setdefault(key, {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO})
        h["taxable"] += _d(line["taxable"])
        h["cgst"] += _d(line["cgst"])
        h["sgst"] += _d(line["sgst"])
        h["igst"] += _d(line["igst"])
    pre_round = tot["taxable"] + tot["cgst"] + tot["sgst"] + tot["igst"]
    grand = pre_round.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    round_off = grand - pre_round
    summary = []
    for (code, pct), h in sorted(hsn.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        summary.append({
            "hsn_code": code,
            "gst_percent": pct,
            "taxable": float(h["taxable"]),
            "cgst_rate": pct / 2 if intra else 0.0,
            "cgst": float(h["cgst"]),
            "sgst_rate": pct / 2 if intra else 0.0,
            "sgst": float(h["sgst"]),
            "igst_rate": 0.0 if intra else pct,
            "igst": float(h["igst"]),
            "tax": float(h["cgst"] + h["sgst"] + h["igst"]),
        })
    return {
        "lines": out_lines,
        "hsn_summary": summary,
        "intra_state": intra,
        "shop_state": shop_state,
        "place_of_supply": pos,
        "place_of_supply_name": state_name(pos),
        "gross_total": float(tot["gross"]),
        "discount_total": float(tot["disc"]),
        "taxable_total": float(tot["taxable"]),
        "cgst_total": float(tot["cgst"]),
        "sgst_total": float(tot["sgst"]),
        "igst_total": float(tot["igst"]),
        "gst_total": float(tot["cgst"] + tot["sgst"] + tot["igst"]),
        "pre_round_total": float(pre_round),
        "round_off": float(round_off),
        "grand_total": float(grand),
        "amount_in_words": amount_in_words(float(grand)),
    }


_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _three(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        parts.append(_two(n))
    return " ".join(parts)


def _indian_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(_indian_words(crore) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def amount_in_words(amount: Any) -> str:
    """'Rupees One Thousand Two Hundred Five and Fifty Paise Only' (Indian numbering)."""
    value = _d(abs(_f(amount)))
    rupees = int(value)
    paise = int((value - rupees) * 100)
    text = "Rupees " + _indian_words(rupees)
    if paise:
        text += " and " + _two(paise) + " Paise"
    return ("Minus " if _f(amount) < 0 else "") + text + " Only"


# ── the store ─────────────────────────────────────────────────────────────────

def _cols(conn, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _row_dict(cur, row) -> dict[str, Any]:
    return {d[0]: row[i] for i, d in enumerate(cur.description)} if row else {}


def shop_profile(conn) -> dict[str, Any]:
    """The pharmacy profile as a dict, with its GST state and any bank details it has."""
    try:
        cur = conn.execute("SELECT * FROM pharmacy_profile ORDER BY id LIMIT 1")
        prof = _row_dict(cur, cur.fetchone())
    except Exception:
        prof = {}
    gstin = str(prof.get("gstin") or "").strip().upper()
    bank = []
    for label, keys in (
        ("Bank", ("bank_name",)),
        ("A/c No", ("bank_account_no", "bank_account", "account_no")),
        ("IFSC", ("bank_ifsc", "ifsc_code", "ifsc")),
        ("Branch", ("bank_branch", "branch")),
    ):
        for k in keys:
            v = str(prof.get(k) or "").strip()
            if v:
                bank.append((label, v))
                break
    if not bank:
        # A free-text setting a shop can fill without a profile column (one line per row).
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE name='invoice_bank_details'"
            ).fetchone()
            text = str(row[0] if row else "").strip()
        except Exception:
            text = ""
        for line in text.splitlines():
            if line.strip():
                k, _, v = line.partition(":")
                bank.append((k.strip(), v.strip()) if v else ("", line.strip()))
    return {
        "name": str(prof.get("name") or ""),
        "address": str(prof.get("address") or ""),
        "phone": str(prof.get("phone") or ""),
        "email": str(prof.get("email") or ""),
        "gstin": gstin,
        "dl_numbers": str(prof.get("dl_number") or ""),
        "state": state_from_gstin(gstin),
        "state_name": state_name(state_from_gstin(gstin)),
        "bank": bank,
    }


def party(conn, customer_id: Any) -> Optional[dict[str, Any]]:
    """The buyer as the wholesale screen and invoice show it, or None."""
    try:
        cid = int(customer_id or 0)
    except (TypeError, ValueError):
        return None
    if cid <= 0:
        return None
    cur = conn.execute("SELECT * FROM customers WHERE id=?", (cid,))
    c = _row_dict(cur, cur.fetchone())
    if not c or int(c.get("deleted") or 0):
        return None
    gst = {}
    try:
        from core.customer_gst import get_customer_gst

        gst = get_customer_gst(conn, cid) or {}
    except Exception:
        gst = {}
    gstin = str(gst.get("gstin") or "")
    state = state_from_gstin(gstin) or clean_state(c.get("state_code"))
    tier = str(c.get("rate_tier") or "").strip().upper()
    return {
        "id": cid,
        "name": str(c.get("name") or ""),
        "phone": str(c.get("phone") or ""),
        "address": str(c.get("address") or ""),
        "party_type": str(c.get("party_type") or "retail"),
        "gstin": gstin,
        "legal_name": str(gst.get("legal_name") or ""),
        "dl_numbers": str(c.get("dl_numbers") or ""),
        "state_code": state,
        "state_name": state_name(state),
        "rate_tier": tier if tier in RATE_TIERS else "A",
        "credit_limit": round(_f(c.get("credit_limit")), 2),
        "credit_days": max(0, _int(c.get("credit_days"))),
        "total_due": round(_f(c.get("total_due")), 2),
        "total_credit": round(_f(c.get("total_credit")), 2),
    }


def list_parties(conn, q: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Customers for the wholesale party box: medicals first, then the rest by name."""
    from core.customer_service import COUNTER_SALE

    cols = _cols(conn, "customers")
    where = ["UPPER(COALESCE(name,'')) <> ?"]
    params: list[Any] = [COUNTER_SALE]
    if "deleted" in cols:
        where.append("COALESCE(deleted,0)=0")
    q = (q or "").strip()
    if q:
        where.append("(name LIKE ? OR COALESCE(phone,'') LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    order = "name"
    if "party_type" in cols:
        order = "CASE WHEN LOWER(COALESCE(party_type,''))='medical' THEN 0 ELSE 1 END, name"
    params.append(max(1, min(int(limit or 50), 200)))
    ptype = "COALESCE(party_type,'')" if "party_type" in cols else "''"
    rows = conn.execute(
        f"SELECT id, name, COALESCE(phone,''), {ptype} "
        f"FROM customers WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?",
        params,
    ).fetchall()
    return [{"id": int(r[0]), "name": str(r[1] or ""), "phone": str(r[2] or ""),
             "party_type": str(r[3] or "") or "retail"} for r in rows]


def units_per_pack(med_type: Any, unit: Any) -> int:
    """How many stock units one pack is: tablets per strip for a strip type, else 1 --
    the same rule a retail line uses to sell loose (desktop_sales_service.build_line)."""
    try:
        from core.layout_config import is_strip_count_type, parse_tablets_per_stripe

        if is_strip_count_type(str(med_type or "")):
            return max(1, int(parse_tablets_per_stripe(unit or "1")))
    except Exception:
        pass
    return 1


def tier_rate(med: dict, tier: str, conn=None) -> tuple[float, str]:
    """(rate per strip / pack, the tier it came from): the party's own tier rate, else Rate A,
    else 0 -- no price is made up (owner, 18 Oct 2026: at least Rate A is required; the
    medicine's stored rate is the PURCHASE rate per strip and selling at it gave stock away
    at cost)."""
    t = str(tier or "A").strip().upper()
    if t not in RATE_TIERS:
        t = "A"
    r = _f(med.get(f"rate_{t.lower()}"))
    if r > 0:
        return round(r, 4), t
    a = _f(med.get("rate_a"))
    if a > 0:
        return round(a, 4), "A"
    return 0.0, ""


def medicine(conn, medicine_id: Any) -> Optional[dict[str, Any]]:
    try:
        mid = int(medicine_id or 0)
    except (TypeError, ValueError):
        return None
    if mid <= 0:
        return None
    cur = conn.execute("SELECT * FROM medicines WHERE id=?", (mid,))
    m = _row_dict(cur, cur.fetchone())
    if not m:
        return None
    ups = units_per_pack(m.get("type"), m.get("unit"))
    m["units_per_pack"] = ups
    m["stock_packs"] = round(_f(m.get("stock_qty")) / ups, 2)
    return m


def build_line(conn, body: dict) -> dict[str, Any]:
    """A new invoice line for a picked batch: the party's tier rate, HSN, GST %, stock."""
    m = medicine(conn, body.get("medicine_id"))
    if not m:
        return {"ok": False, "error": "Medicine not found."}
    rate, used = tier_rate(m, str(body.get("rate_tier") or "A"), conn)
    line = {
        "id": int(m["id"]),
        "name": str(m.get("name") or ""),
        "batch": str(m.get("batch_no") or ""),
        "expiry": str(m.get("expiry_date") or ""),
        "hsn_code": str(m.get("hsn_code") or ""),
        "type": str(m.get("type") or ""),
        "unit": str(m.get("unit") or "1"),
        "units_per_pack": int(m["units_per_pack"]),
        "stock_packs": m["stock_packs"],
        "qty": max(1, _int(body.get("qty") or 1)),
        "free": max(0, _int(body.get("free"))),
        "rate": rate,
        "rate_tier": used,
        "tier_rates": {t: round(_f(m.get(f"rate_{t.lower()}")), 4) for t in RATE_TIERS},
        "base_rate": round(_f(m.get("rate")), 4),
        "disc_pct": round(_f(body.get("disc_pct")), 2),
        "scheme_pct": round(_f(body.get("scheme_pct")), 2),
        "gst_percent": round(_f(m.get("gst_percent")), 2),
        "mrp": round(_f(m.get("mrp")), 2),
    }
    want = str(body.get("rate_tier") or "A").strip().upper()
    if rate <= 0:
        line["warning"] = (f"{line['name']}: no Rate A for this batch. Rate A is required -- set it "
                           "in Purchase (Rate A / B / C) or type the rate here.")
        line["rate_missing"] = True
    elif used and used != want:
        line["warning"] = f"{line['name']}: no Rate {want} for this batch -- Rate {used} used."
    return {"ok": True, "line": line}


def due_date_for(bill_date: Any, credit_days: Any) -> str:
    from core.desktop_sales_service import _parse_bill_date

    d = _parse_bill_date(bill_date)
    return (d + timedelta(days=max(0, _int(credit_days)))).isoformat()


def credit_warning(p: Optional[dict], bill_total: float, paid_now: float = 0.0,
                   *, this_bill_due: float = 0.0) -> str:
    """A warning (never a refusal) when the party's due plus this bill passes its limit."""
    if not p:
        return ""
    limit = _f(p.get("credit_limit"))
    if limit <= 0:
        return ""
    due = max(0.0, _f(p.get("total_due")) - _f(this_bill_due))
    after = round(due + max(0.0, _f(bill_total) - _f(paid_now)), 2)
    if after <= limit + 0.005:
        return ""
    return (f"Credit limit crossed: {p.get('name') or 'party'} owes Rs {due:.2f}; with this "
            f"bill Rs {after:.2f}, limit Rs {limit:.2f}.")


def _invoice_for_body(conn, body: dict, p: dict) -> dict[str, Any]:
    shop = shop_profile(conn)
    buyer_gstin = str(body.get("buyer_gstin") or p.get("gstin") or "").strip().upper()
    pos = place_of_supply(buyer_gstin, p.get("state_code"), shop["state"])
    lines = [it for it in (body.get("items") or []) if isinstance(it, dict)]
    inv = calc_invoice(lines, shop_state=shop["state"], pos=pos)
    inv["shop"] = shop
    inv["buyer_gstin"] = buyer_gstin
    return inv


def calc_sale(conn, body: dict) -> dict[str, Any]:
    """The live totals of the wholesale screen, with the place of supply and credit check."""
    p = party(conn, body.get("customer_id")) or {
        "state_code": "", "gstin": "", "credit_limit": 0, "total_due": 0, "credit_days": 0,
    }
    inv = _invoice_for_body(conn, body, p)
    paid = 0.0
    if str(body.get("payment_mode") or "Due").strip().lower() != "due":
        paid = _f(body.get("cash_paid")) + _f(body.get("online_paid"))
    this_due = 0.0
    sid = _int(body.get("editing_sale_id"))
    if sid > 0:
        row = conn.execute("SELECT COALESCE(due_amount,0) FROM sales WHERE id=?", (sid,)).fetchone()
        this_due = _f(row[0]) if row else 0.0
    warn = credit_warning(p if p.get("id") else None, inv["grand_total"], paid, this_bill_due=this_due)
    inv["due_date"] = due_date_for(body.get("bill_date"), p.get("credit_days"))
    inv["warnings"] = [warn] if warn else []
    inv["ok"] = True
    return inv


def write_sale_header(cur, sale_id: int, header: dict) -> None:
    """The tax-invoice columns of a sale (billing_service calls this inside its save)."""
    vals = [header.get(k) for k in SALE_FIELDS]
    cur.execute(
        f"UPDATE sales SET {', '.join(f'{k}=?' for k in SALE_FIELDS)} WHERE id=?",
        (*vals, int(sale_id)),
    )


def write_line_fields(cur, line_row_id: int, fields: dict) -> None:
    vals = [fields.get(k) for k in LINE_FIELDS]
    cur.execute(
        f"UPDATE sales_items SET {', '.join(f'{k}=?' for k in LINE_FIELDS)} WHERE id=?",
        (*vals, int(line_row_id)),
    )


# ── sync: the server keeps documents as JSON, so the fields ride along ───────────

def line_select_sql(conn, alias: str = "si") -> str:
    """', si.rate_tier AS ws_rate_tier, ...' for a sales_items SELECT, or '' on a store
    that has not got the columns yet."""
    have = _cols(conn, "sales_items")
    if not set(LINE_FIELDS) <= have:
        return ""
    return "".join(f", {alias}.{k} AS ws_{k}" for k in LINE_FIELDS)


_TEXT_SALE = {"invoice_mode", "buyer_gstin", "buyer_dl", "place_of_supply", "due_date",
              "transport", "lr_no", "eway_bill_no"}
_TEXT_LINE = {"rate_tier", "batch_no", "expiry_date", "hsn_code"}


def _sync_value(key: str, value: Any, text_keys: set[str]) -> Any:
    # Every key is sent, never None: the push drops None keys, and the server keeps a key
    # that is not sent -- a cleared LR no. would come back. "" / 0 clear it instead.
    if value is None:
        return "" if key in text_keys else 0
    return value


def line_sync_fields(row: dict) -> dict[str, Any]:
    """A wholesale line's own fields for its sync document, by their column names (the
    server keeps them on the line); {} for a retail line (it has no taxable value), whose
    document stays exactly as it was. Read from a SELECT with ``line_select_sql``."""
    if row.get("ws_taxable") is None:
        return {}
    return {k: _sync_value(k, row.get(f"ws_{k}"), _TEXT_LINE) for k in LINE_FIELDS}


def sale_sync_fields(sale: dict) -> dict[str, Any]:
    """A wholesale sale's invoice header as top-level keys of its sync document; {} for
    any other sale."""
    if str(sale.get("invoice_mode") or "").lower() != WHOLESALE:
        return {}
    return {k: _sync_value(k, sale.get(k), _TEXT_SALE) for k in SALE_FIELDS}


def _blank_none(v: Any) -> Any:
    return None if v == "" else v


def apply_synced_sale(cur, sale_id: int, data: dict) -> None:
    """A pulled sale: put back its invoice header (INSERT OR REPLACE just blanked it)."""
    if str((data or {}).get("invoice_mode") or "").lower() != WHOLESALE:
        return
    write_sale_header(cur, int(sale_id), {k: _blank_none(data.get(k)) for k in SALE_FIELDS})


def apply_synced_line(cur, line_row_id: int, item: dict) -> None:
    """A pulled line: its wholesale fields, when the server sent them (taxable marks one)."""
    if not isinstance(item, dict) or item.get("taxable") in (None, ""):
        return
    write_line_fields(cur, int(line_row_id), {k: _blank_none(item.get(k)) for k in LINE_FIELDS})


def is_wholesale_sale(conn, sale_id: Any) -> bool:
    try:
        row = conn.execute("SELECT invoice_mode FROM sales WHERE id=?", (int(sale_id),)).fetchone()
    except Exception:
        return False
    return bool(row) and str(row[0] or "").strip().lower() == WHOLESALE


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def remember_contact(conn, p: dict, body: dict) -> bool:
    """The phone / address typed on the bill go onto the party, as on the retail bill
    (core/customer_service.get_or_create_customer): an empty box never wipes one."""
    phone = str(body.get("customer_phone") or "").strip()
    address = str(body.get("customer_address") or "").strip()
    phone = phone if phone != str(p.get("phone") or "").strip() else ""
    address = address if address != str(p.get("address") or "").strip() else ""
    if not (phone or address):
        return False
    from core.customer_service import _update_customer_contact

    _update_customer_contact(conn, int(p["id"]), phone, address)
    return True


def save_sale(conn, body: dict) -> dict[str, Any]:
    """Save (or, with editing_sale_id, re-save) a wholesale tax invoice."""
    from core import desktop_sales_service as dss
    from core.billing_service import save_new_bill, update_existing_bill
    from core.customer_service import COUNTER_SALE

    if _online():
        return {"ok": False, "code": "online_mode",
                "error": "Wholesale bills are made on an Offline or Offline-first PC."}
    if get_business_mode(conn) != WHOLESALE:
        return {"ok": False, "code": "not_wholesale",
                "error": "This store is not in wholesale mode."}
    if not dss._has_pharmacy_profile(conn):
        return {"ok": False, "code": "profile_required",
                "error": "Please set up pharmacy profile in Settings first."}
    p = party(conn, body.get("customer_id"))
    if not p or p["name"].strip().upper() == COUNTER_SALE:
        return {"ok": False, "code": "party_required",
                "error": "Pick the party (a customer) for a wholesale bill."}

    bill_date = dss._parse_bill_date(body.get("bill_date"))
    editing_id = _int(body.get("editing_sale_id"))
    if editing_id > 0:
        row = conn.execute(
            "SELECT invoice_mode, COALESCE(due_amount,0) FROM sales WHERE id=? "
            "AND COALESCE(deleted,0)=0", (editing_id,)).fetchone()
        if not row:
            return {"ok": False, "code": "not_found", "error": "Bill not found."}
        if str(row[0] or "").lower() != WHOLESALE:
            return {"ok": False, "code": "retail_bill",
                    "error": "This is a retail bill: edit it in retail mode."}

    # The lines, checked, with what each medicine is.
    lines: list[dict] = []
    for it in body.get("items") or []:
        if not isinstance(it, dict):
            continue
        m = medicine(conn, it.get("id") or it.get("medicine_id"))
        if not m:
            return {"ok": False, "code": "no_medicine",
                    "error": f"Medicine {it.get('name') or it.get('id')} not found."}
        if _int(it.get("qty")) <= 0:
            return {"ok": False, "code": "bad_qty",
                    "error": f"{m['name']}: quantity must be at least one pack."}
        if _f(it.get("rate")) <= 0:
            return {"ok": False, "code": "bad_rate", "error": f"{m['name']}: no rate. Rate A is required -- type the rate."}
        line = dict(it)
        line["id"] = int(m["id"])
        line["hsn_code"] = str(it.get("hsn_code") or m.get("hsn_code") or "")
        if it.get("gst_percent") in (None, ""):
            line["gst_percent"] = _f(m.get("gst_percent"))
        line["_med"] = m
        lines.append(line)
    if not lines:
        return {"ok": False, "code": "no_medicines", "error": "Please add medicines to the bill."}

    inv = _invoice_for_body(conn, {**body, "items": lines}, p)

    # Stock, in units: (packs + free) x units per pack, against what is there plus what
    # this bill already holds when it is an edit.
    need: dict[int, int] = {}
    for line in inv["lines"]:
        ups = int(line["_med"]["units_per_pack"])
        need[line["id"]] = need.get(line["id"], 0) + (line["qty"] + line["free"]) * ups
    held: dict[int, float] = {}
    if editing_id > 0:
        for mid, q in conn.execute(
                "SELECT medicine_id, COALESCE(qty,0) FROM sales_items WHERE sale_id=?",
                (editing_id,)).fetchall():
            held[int(mid)] = held.get(int(mid), 0.0) + _f(q)
    for line in inv["lines"]:
        m = line["_med"]
        mid = line["id"]
        have = _f(m.get("stock_qty")) + held.get(mid, 0.0)
        if need.get(mid, 0) > have + 1e-6:
            ups = int(m["units_per_pack"])
            return {"ok": False, "code": "insufficient_stock",
                    "error": f"{m['name']} ({m.get('batch_no') or ''}): only "
                             f"{have / ups:g} pack(s) in stock."}

    medicines = []
    for line in inv["lines"]:
        m = line["_med"]
        ups = int(m["units_per_pack"])
        units = (line["qty"] + line["free"]) * ups
        amount = line["amount"]
        medicines.append({
            "id": line["id"],
            "name": str(m.get("name") or ""),
            "batch": str(m.get("batch_no") or ""),
            "expiry": str(m.get("expiry_date") or ""),
            "qty": units,
            "rate": round(amount / units, 4) if units else 0.0,
            "amount": amount,
            "original_amount": line["gross"],
            "medicine_discount": round(line["disc_amt"] + line["scheme_amt"], 2),
            "schedule": str(m.get("schedule") or ""),
            "type": str(m.get("type") or ""),
            "display_type": str(m.get("type") or "N/A"),
            "gst_percent": line["gst_percent"],
            "location": "",
            "unit": str(m.get("unit") or "1"),
            "quick_add": False,
            "mrp": _f(m.get("mrp")),
            "wholesale": {
                "rate_tier": str(line.get("rate_tier") or "").strip().upper()[:1] or None,
                "free_qty": line["free"],
                "scheme_pct": line["scheme_pct"],
                "taxable": line["taxable"],
                "gst_amt": line["gst_amt"],
                "batch_no": str(m.get("batch_no") or ""),
                "expiry_date": str(m.get("expiry_date") or ""),
                "hsn_code": line["hsn_code"],
            },
        })

    if editing_id <= 0:
        from core.sale_availability import lines_unavailable_on

        problems = lines_unavailable_on(conn, medicines, bill_date)
        if problems:
            return {"ok": False, "code": "not_available_on_date", "error": " ".join(problems),
                    "messages": problems}
    else:
        from core.desktop_returns_service import edit_below_returned_error

        why = edit_below_returned_error(conn, "sale", editing_id, medicines, id_key="id")
        if why:
            return {"ok": False, "error": why, "code": "below_returned"}

    grand = inv["grand_total"]
    mode = str(body.get("payment_mode") or "Due").strip().lower()
    is_due = mode == "due"
    cash = 0.0 if is_due else round(_f(body.get("cash_paid")), 2)
    online = 0.0 if is_due else round(_f(body.get("online_paid")), 2)
    if not is_due and cash + online <= 0:
        if body.get("pay_full"):
            cash = grand
        else:
            return {"ok": False, "code": "payment_required",
                    "error": "Cash or Online amount must be greater than zero (or choose Due)."}

    header = {
        "invoice_mode": WHOLESALE,
        "buyer_gstin": inv["buyer_gstin"] or None,
        "buyer_dl": str(body.get("buyer_dl") or p.get("dl_numbers") or "").strip() or None,
        "place_of_supply": inv["place_of_supply"] or None,
        "taxable_total": inv["taxable_total"],
        "cgst_total": inv["cgst_total"],
        "sgst_total": inv["sgst_total"],
        "igst_total": inv["igst_total"],
        "due_date": due_date_for(bill_date, p.get("credit_days")),
        "transport": str(body.get("transport") or "").strip() or None,
        "lr_no": str(body.get("lr_no") or "").strip() or None,
        "eway_bill_no": str(body.get("eway_bill_no") or "").strip() or None,
    }
    this_due = 0.0
    if editing_id > 0:
        r = conn.execute("SELECT COALESCE(due_amount,0) FROM sales WHERE id=?",
                         (editing_id,)).fetchone()
        this_due = _f(r[0]) if r else 0.0
    warnings = []
    warn = credit_warning(p, grand, cash + online, this_bill_due=this_due)
    if warn:
        warnings.append(warn)

    try:
        with dss._SALE_WRITE_LOCK:
            if editing_id > 0:
                update_existing_bill(
                    conn, editing_id, medicines,
                    discount_pct=0, rounding=inv["round_off"], cash_paid=cash,
                    online_paid=online, customer_name=p["name"], customer_phone="",
                    doctor_name="", previous_due=p["total_due"], discount_rs=None,
                    bill_date=bill_date, wholesale=header,
                )
                sale_id = editing_id
                row = conn.execute("SELECT bill_no FROM sales WHERE id=?", (sale_id,)).fetchone()
                from core.fy_serial import display_sales_bill_no

                bill_no = display_sales_bill_no(row[0]) if row else str(sale_id)
            else:
                bill_no, sale_id = save_new_bill(
                    conn=conn, customer_id=p["id"], medicines=medicines, discount_pct=0,
                    discount_rs=None, rounding=inv["round_off"], cash_paid=cash,
                    online_paid=online, doctor_name="", doctor_phone="",
                    previous_due=p["total_due"], bill_date=bill_date, wholesale=header,
                )
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": f"Failed to save sale: {exc}"}

    try:
        remember_contact(conn, p, body)
    except Exception as exc:  # the bill is saved; a contact that did not stick is not fatal
        log.warning("wholesale sale %s: party contact not updated: %s", sale_id, exc)
    after = party(conn, p["id"]) or p
    return {
        "ok": True,
        "bill_no": bill_no,
        "sale_id": int(sale_id),
        "customer_id": p["id"],
        "merged_counter": False,
        "customer_due": after.get("total_due", 0.0),
        "customer_credit": after.get("total_credit", 0.0),
        "next_bill_hint": dss._next_hint(conn),
        "pdf_path": None,
        "pdf_dir": "",
        "invoice_mode": WHOLESALE,
        "grand_total": grand,
        "due_date": header["due_date"],
        "warnings": warnings,
    }


# ── reading a saved invoice back ──────────────────────────────────────────────

def line_from_row(r: dict, ups: int) -> dict[str, Any]:
    """A stored line back in invoice terms: packs, free, pack rate, disc %."""
    ups = max(1, int(ups or 1))
    units = _f(r.get("qty"))
    free = _f(r.get("free_qty"))
    packs = round(units / ups - free, 4)
    taxable = _d(r.get("taxable"))
    off = _d(r.get("item_discount"))
    scheme_pct = _d(r.get("scheme_pct"))
    gross = taxable + off
    if scheme_pct > 0 and scheme_pct < HUNDRED:
        scheme_amt = _paise(taxable * scheme_pct / (HUNDRED - scheme_pct))
    else:
        scheme_amt = ZERO
    disc_amt = max(ZERO, off - scheme_amt)
    disc_pct = float(_paise(disc_amt * HUNDRED / gross)) if gross > 0 else 0.0
    rate = round(float(gross) / packs, 4) if packs > 0 else 0.0
    return {
        "qty": int(round(packs)),
        "free": int(round(free)),
        "rate": round(rate, 2) if abs(rate - round(rate, 2)) < 0.0051 else rate,
        "disc_pct": disc_pct,
        "scheme_pct": float(scheme_pct),
    }


def load_sale(conn, sale_id: Any) -> dict[str, Any]:
    """A saved wholesale invoice: header, party and lines as the screen edits them, and
    the invoice figures as stored."""
    sid = _int(sale_id)
    cur = conn.execute("SELECT * FROM sales WHERE id=?", (sid,))
    s = _row_dict(cur, cur.fetchone())
    if not s or int(s.get("deleted") or 0):
        return {"ok": False, "error": "Bill not found."}
    if str(s.get("invoice_mode") or "").lower() != WHOLESALE:
        return {"ok": False, "code": "retail_bill",
                "error": "This is a retail bill: open it in retail mode."}
    cur = conn.execute(
        "SELECT si.*, m.name AS m_name, m.type AS m_type, m.unit AS m_unit, "
        "m.batch_no AS m_batch, m.expiry_date AS m_expiry, m.hsn_code AS m_hsn, "
        "m.mrp AS m_mrp, m.stock_qty AS m_stock "
        "FROM sales_items si LEFT JOIN medicines m ON m.id = si.medicine_id "
        "WHERE si.sale_id=? AND COALESCE(si.deleted,0)=0 ORDER BY si.id", (sid,))
    rows = [_row_dict(cur, r) for r in cur.fetchall()]
    lines = []
    for r in rows:
        ups = units_per_pack(r.get("m_type"), r.get("m_unit"))
        back = line_from_row(r, ups)
        lines.append({
            "id": int(r.get("medicine_id") or 0),
            "name": str(r.get("m_name") or ""),
            "batch": str(r.get("batch_no") or r.get("m_batch") or ""),
            "expiry": str(r.get("expiry_date") or r.get("m_expiry") or ""),
            "hsn_code": str(r.get("hsn_code") or r.get("m_hsn") or ""),
            "type": str(r.get("m_type") or ""),
            "unit": str(r.get("m_unit") or "1"),
            "units_per_pack": ups,
            "stock_packs": round(_f(r.get("m_stock")) / ups, 2),
            "rate_tier": str(r.get("rate_tier") or ""),
            "gst_percent": _f(r.get("gst_percent")),
            "mrp": _f(r.get("m_mrp")),
            "taxable": _f(r.get("taxable")),
            "gst_amt": _f(r.get("gst_amt")),
            "amount": _f(r.get("amount")),
            **back,
        })
    p = party(conn, s.get("customer_id")) or {"id": int(s.get("customer_id") or 0), "name": ""}
    from core.fy_serial import display_sales_bill_no

    return {
        "ok": True,
        "sale_id": sid,
        "bill_no": display_sales_bill_no(s.get("bill_no")),
        "bill_date": str(s.get("bill_date") or "")[:10],
        "party": p,
        "customer_id": int(s.get("customer_id") or 0),
        "buyer_gstin": str(s.get("buyer_gstin") or ""),
        "buyer_dl": str(s.get("buyer_dl") or ""),
        "place_of_supply": str(s.get("place_of_supply") or ""),
        "due_date": str(s.get("due_date") or ""),
        "transport": str(s.get("transport") or ""),
        "lr_no": str(s.get("lr_no") or ""),
        "eway_bill_no": str(s.get("eway_bill_no") or ""),
        "taxable_total": _f(s.get("taxable_total")),
        "cgst_total": _f(s.get("cgst_total")),
        "sgst_total": _f(s.get("sgst_total")),
        "igst_total": _f(s.get("igst_total")),
        "rounding": _f(s.get("rounding")),
        "total_amount": _f(s.get("total_amount")),
        "cash_paid": _f(s.get("cash_paid")),
        "online_paid": _f(s.get("online_paid")),
        "due_amount": _f(s.get("due_amount")),
        "items": lines,
    }


def invoice_context(conn, sale_id: Any) -> dict[str, Any]:
    """Everything the tax invoice prints, from the saved bill. The line figures and the
    totals are the ones stored with it; the HSN summary is summed from the lines."""
    sale = load_sale(conn, sale_id)
    if not sale.get("ok"):
        raise ValueError(sale.get("error") or f"Sale id {sale_id} not found")
    shop = shop_profile(conn)
    pos = sale["place_of_supply"] or sale["party"].get("state_code") or shop["state"]
    intra = is_intra_state(pos, shop["state"])
    lines = []
    hsn: dict[tuple[str, float], dict[str, Decimal]] = {}
    for it in sale["items"]:
        gst = _d(it["gst_amt"])
        cgst, sgst = split_half(gst) if intra else (ZERO, ZERO)
        igst = ZERO if intra else gst
        lines.append({**it, "cgst": float(cgst), "sgst": float(sgst), "igst": float(igst)})
        key = (it["hsn_code"], float(it["gst_percent"]))
        h = hsn.setdefault(key, {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO})
        h["taxable"] += _d(it["taxable"])
        h["cgst"] += cgst
        h["sgst"] += sgst
        h["igst"] += igst
    summary = [{
        "hsn_code": code, "gst_percent": pct, "taxable": float(h["taxable"]),
        "cgst_rate": pct / 2 if intra else 0.0, "cgst": float(h["cgst"]),
        "sgst_rate": pct / 2 if intra else 0.0, "sgst": float(h["sgst"]),
        "igst_rate": 0.0 if intra else pct, "igst": float(h["igst"]),
        "tax": float(h["cgst"] + h["sgst"] + h["igst"]),
    } for (code, pct), h in sorted(hsn.items())]
    party_ = dict(sale["party"])
    return {
        **sale,
        "shop": shop,
        "buyer": {
            "name": party_.get("legal_name") or party_.get("name") or "",
            "address": party_.get("address") or "",
            "phone": party_.get("phone") or "",
            "gstin": sale["buyer_gstin"],
            "dl_numbers": sale["buyer_dl"],
            "state_code": party_.get("state_code") or state_from_gstin(sale["buyer_gstin"]),
            "state_name": state_name(party_.get("state_code")
                                     or state_from_gstin(sale["buyer_gstin"])),
        },
        "intra_state": intra,
        "place_of_supply": pos,
        "place_of_supply_name": state_name(pos),
        "lines": lines,
        "hsn_summary": summary,
        "grand_total": sale["total_amount"],
        "round_off": sale["rounding"],
        "amount_in_words": amount_in_words(sale["total_amount"]),
    }


def invoice_paper(settings: Optional[dict]) -> str:
    """A5 when the print slot is set to A5 (printed landscape, like the retail A5 bill),
    else A4 portrait."""
    paper = str((settings or {}).get("paper_size") or "").strip().upper()
    return "A5" if paper == "A5" else "A4"


def paper_mm(paper: str) -> tuple[float, float]:
    return (210.0, 148.0) if str(paper).upper() == "A5" else (210.0, 297.0)


def render_invoice_html(conn, sale_id: Any, *, paper: str = "A4") -> str:
    from bill_templates.wholesale_invoice import render_wholesale_invoice_html

    return render_wholesale_invoice_html(invoice_context(conn, sale_id), paper=paper)


def today_iso() -> str:
    return date.today().isoformat()
