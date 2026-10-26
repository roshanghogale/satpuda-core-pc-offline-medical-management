"""Doctor Margin Report rule (owner, 17 Oct 2026; Sales History -> Export).

The PC copy of server-live src/services/doctorMargin.js (the reference); the phone has
DoctorMargin.kt. tests/fixtures/doctor_margin_vectors.json holds the cases all three must
agree on, compared after ``rounded()``. Written line for line as the reference: the same
order of the bills and lines, the same sums left to right, JavaScript's Number() / String()
/ sort order, and nothing rounded until ``rounded()``.

Input, already read from the books (core/doctor_margin_report.py on this PC):
  sales:     [{id, bill_no, date, patient, doctor, total_amount,
               items: [{medicine_id, name, type, batch, qty, amount, cost_price}],
               returns: [{medicine_id, qty}]}]     -- deleted / draft bills left out
  purchases: [{medicine_id, qty, free_qty, item_amount, units}]
             units = sale units in one purchased pack (tablets per strip for strip
             medicines, else 1)
  opts:      {mode: 'regular'|'injection', pct, types: [..] | None, doctor: '' | name}

The rules:
 - Scheme-adjusted effective purchase rate of a batch (medicine row) = what its purchase
   lines cost (item_amount, after discount, with GST -- the sale amount is MRP with GST)
   / every unit received, free ones included: (qty + free_qty) x units. 10 bought + 10 free
   for Rs 100 = Rs 5 a unit. No purchase line: the cost the bill recorded at sale time
   (cost_price, "estimated"); none either: no cost ("unavailable"), flagged.
 - A line's eligible sale amount is its amount after line discount, times the bill's
   total / the sum of its lines (the bill discount and rounding shared out, never counted
   twice), less what came back on a return of that bill (in proportion to qty).
 - Regular report: every type except injections (or the types chosen, injections never);
   commission = eligible sale amount x pct / 100; store remaining = profit - commission.
 - Injection report: injection types only; doctor margin = profit when positive, else 0
   and the line is flagged; the percentage is not used.
 - A loss (profit < 0) is flagged "loss" for review in both reports.
 - Doctor = the name on the bill; a bill without one is "(No doctor)", never guessed.
 - Summary groups by doctor, medicine row (= batch) and purchase rate; average selling
   rate and purchase rate are weighted by quantity.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from functools import cmp_to_key
from typing import Any

NO_DOCTOR = "(No doctor)"

_MONEY_KEYS = ("sale_rate", "avg_sale_rate", "purchase_rate", "amount", "cost", "profit",
               "commission", "remaining")


# ── JavaScript's Number / String / Math / sort, for what this rule meets ──────────────────

def _num(v: Any) -> float:
    """Number(v): numbers, numeric text, None (-> 0), anything else NaN."""
    t = type(v)
    if t is float:  # the common cases first (a big report calls this millions of times)
        return v
    if t is int:
        return float(v)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return math.nan


def _n0(v: Any) -> float:
    """Number(v) || 0."""
    x = _num(v)
    return 0.0 if (x != x or x == 0) else x


def _js_str(v: Any) -> str:
    """String(v)."""
    if type(v) is str:
        return v
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v == int(v) and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    return str(v)


def _or(v: Any, default: Any = "") -> Any:
    """v || default (0, '', None, False and NaN are falsy)."""
    if v is None or v is False or v == "" or (isinstance(v, (int, float)) and (v != v or v == 0)):
        return default
    return v


def _js_max(a: float, b: float) -> float:
    if a != a or b != b:
        return math.nan
    return a if a >= b else b


def _js_min(a: float, b: float) -> float:
    if a != a or b != b:
        return math.nan
    return a if a <= b else b


def _key(x: float) -> Any:
    """A Map key: NaN finds NaN (SameValueZero), as in a JavaScript Map."""
    return "NaN" if x != x else x


def _u16(s: str) -> bytes:
    """Strings compared by UTF-16 code units, as JavaScript's < and sort() do."""
    return s.encode("utf-16-be", "surrogatepass")


def _cmp_str(a: Any, b: Any) -> int:
    x, y = _u16(_js_str(a)), _u16(_js_str(b))
    return -1 if x < y else 1 if x > y else 0


def _to_fixed6(x: float) -> str:
    """Number.prototype.toFixed(6): the exact value, a half away from zero."""
    if x != x:
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    return str(Decimal(x).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _math_round(x: float) -> float:
    """Math.round: the nearest whole number, a half towards +infinity."""
    if not math.isfinite(x):
        return x
    f = math.floor(x)
    return float(f + 1 if x - f >= 0.5 else f)


def r2(v: Any) -> float:
    """Math.round(Number(v) * 100) / 100 -- money to the paisa, for showing / comparing."""
    return _math_round(_n0(v) * 100) / 100


def is_injection(med_type: Any) -> bool:
    return "inj" in _js_str(_or(med_type, "")).lower()


# ── The rule ─────────────────────────────────────────────────────────────────────────────

def effective_rates(purchases: Any) -> dict[Any, dict[str, Any]]:
    """medicine_id -> {rate, basis: 'scheme'} from purchase lines."""
    acc: dict[Any, dict[str, float]] = {}
    for p in purchases or []:
        mid = _num(p.get("medicine_id"))
        if not (mid > 0):
            continue
        units = _js_max(1.0, _n0(p.get("units")) or 1.0)
        got = (_n0(p.get("qty")) + _n0(p.get("free_qty"))) * units
        if not (got > 0):
            continue
        a = acc.get(mid) or {"cost": 0.0, "units": 0.0}
        a["cost"] += _n0(p.get("item_amount"))
        a["units"] += got
        acc[mid] = a
    out: dict[Any, dict[str, Any]] = {}
    for mid, a in acc.items():
        if a["units"] > 0:
            out[mid] = {"rate": a["cost"] / a["units"], "basis": "scheme"}
    return out


def _clean_id(x: float) -> Any:
    return int(x) if math.isfinite(x) and x == int(x) else x


def _total(rows: list[dict[str, Any]]) -> dict[str, float]:
    t = {"qty": 0.0, "amount": 0.0, "cost": 0.0, "commission": 0.0}
    for r in rows:
        t["qty"] += r["qty"]
        t["amount"] += r["amount"]
        t["cost"] += r["cost"]
        t["commission"] += r["commission"]
    t["profit"] = t["amount"] - t["cost"]
    t["remaining"] = t["amount"] - t["cost"] - t["commission"]
    return t


def doctor_margin(sales: Any, purchases: Any, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    opts = opts or {}
    mode = "injection" if opts.get("mode") == "injection" else "regular"
    raw_pct = opts.get("pct")
    pct = _js_min(100.0, _js_max(0.0, _num(10 if raw_pct is None else raw_pct)))
    raw_types = opts.get("types")
    types = (
        {_js_str(t).strip().lower() for t in raw_types}
        if isinstance(raw_types, (list, tuple)) and len(raw_types) else None
    )
    inj_types = {t for t in types if is_injection(t)} if types and any(is_injection(t) for t in types) else None
    want_doctor = _js_str(_or(opts.get("doctor"), "")).strip().upper()
    rates = effective_rates(purchases)

    rows: list[dict[str, Any]] = []
    for s in sales or []:
        doctor = _js_str(_or(s.get("doctor"), "")).strip().upper() or NO_DOCTOR
        if want_doctor and doctor.upper() != want_doctor:
            continue
        items = s.get("items") or []
        line_sum = 0.0
        for it in items:
            line_sum = line_sum + _n0(it.get("amount"))
        factor = _n0(s.get("total_amount")) / line_sum if line_sum > 0 else 0.0
        back: dict[Any, float] = {}
        for r in s.get("returns") or []:
            k = _key(_num(r.get("medicine_id")))
            back[k] = (back.get(k) or 0.0) + _n0(r.get("qty"))
        for it in items:
            med_type = _js_str(_or(it.get("type"), ""))
            inj = is_injection(med_type)
            if (not inj) if mode == "injection" else inj:
                continue
            if mode == "regular" and types and med_type.strip().lower() not in types:
                continue
            # Injection report: every injection type, or only the injection types ticked.
            if mode == "injection" and inj_types and med_type.strip().lower() not in inj_types:
                continue
            mid = _num(it.get("medicine_id"))
            k = _key(mid)
            sold = _n0(it.get("qty"))
            if not (sold > 0):
                continue
            returned = _js_min(sold, _n0(back.get(k)))
            if returned > 0:
                back[k] = _n0(back.get(k)) - returned
            qty = sold - returned
            if not (qty > 0):
                continue
            amount = _n0(it.get("amount")) * factor * (qty / sold)
            rate = 0.0
            basis = "unavailable"
            scheme = rates.get(k)
            if scheme:
                rate = scheme["rate"]
                basis = "scheme"
            elif _n0(it.get("cost_price")) > 0:
                rate = _num(it.get("cost_price"))
                basis = "estimated"
            cost = qty * rate
            profit = amount - cost
            commission = amount * pct / 100 if mode == "regular" else _js_max(0.0, profit)
            flags = []
            if basis != "scheme":
                flags.append(basis)
            if profit < 0:
                flags.append("loss")
            rows.append({
                "date": _js_str(_or(s.get("date"), ""))[:10],
                "patient": _or(s.get("patient"), ""),
                "doctor": doctor,
                "bill_no": _or(s.get("bill_no"), ""),
                "medicine_id": _clean_id(mid),
                "name": _or(it.get("name"), ""),
                "type": med_type,
                "batch": _or(it.get("batch"), ""),
                "qty": qty,
                "sale_rate": amount / qty,
                "purchase_rate": rate,
                "basis": basis,
                "amount": amount,
                "cost": cost,
                "profit": profit,
                "pct": pct if mode == "regular" else 100,
                "commission": commission,
                "remaining": profit - commission,
                "flags": flags,
            })

    # Doctor, then date, then bill number, each by UTF-16 code units (JavaScript's sort), as a
    # key worked out once per row: a comparison function encoding six strings for every
    # comparison was most of a year's report on a big store (5 lakh lines: 17 s of 26). The
    # same comparisons in the same stable sort give the same order.
    rows.sort(key=lambda r: (_u16(_js_str(r["doctor"])), _u16(_js_str(r["date"])),
                             _u16(_js_str(r["bill_no"]))))

    groups: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        gk = (r["doctor"], _js_str(r["medicine_id"]), _js_str(r["batch"]), _to_fixed6(r["purchase_rate"]))
        g = groups.get(gk) or {
            "doctor": r["doctor"], "medicine_id": r["medicine_id"], "name": r["name"], "type": r["type"],
            "batch": r["batch"], "basis": r["basis"], "qty": 0.0, "amount": 0.0, "cost": 0.0,
            "commission": 0.0, "flags": [],
        }
        g["qty"] += r["qty"]
        g["amount"] += r["amount"]
        g["cost"] += r["cost"]
        g["commission"] += r["commission"]
        for f in r["flags"]:
            if f not in g["flags"]:
                g["flags"].append(f)
        groups[gk] = g

    summary = [{
        "doctor": g["doctor"], "medicine_id": g["medicine_id"], "name": g["name"], "type": g["type"],
        "batch": g["batch"], "basis": g["basis"],
        "qty": g["qty"], "avg_sale_rate": g["amount"] / g["qty"], "purchase_rate": g["cost"] / g["qty"],
        "amount": g["amount"], "cost": g["cost"], "profit": g["amount"] - g["cost"],
        "pct": pct if mode == "regular" else 100, "commission": g["commission"],
        "remaining": g["amount"] - g["cost"] - g["commission"],
        "flags": sorted(g["flags"], key=_u16),
    } for g in groups.values()]

    def sum_cmp(a: dict[str, Any], b: dict[str, Any]) -> int:
        c = _cmp_str(a["doctor"], b["doctor"]) or _cmp_str(a["name"], b["name"]) or _cmp_str(a["batch"], b["batch"])
        if c:
            return c
        d = _num(a["medicine_id"]) - _num(b["medicine_id"])
        return -1 if d < 0 else 1 if d > 0 else 0  # NaN counts as equal, as in sort()

    if all(math.isfinite(_num(s["medicine_id"])) for s in summary):
        # sum_cmp as a key (see rows above). Only when every id is a finite number: a NaN
        # "equals" everything in sum_cmp, which no key can say.
        summary.sort(key=lambda s: (_u16(_js_str(s["doctor"])), _u16(_js_str(s["name"])),
                                    _u16(_js_str(s["batch"])), _num(s["medicine_id"])))
    else:
        summary.sort(key=cmp_to_key(sum_cmp))

    names = sorted({r["doctor"] for r in rows}, key=_u16)
    doctors = [{"doctor": d, **_total([r for r in rows if r["doctor"] == d])} for d in names]
    return {
        "mode": mode,
        "pct": pct if mode == "regular" else 100,
        "rows": rows,
        "summary": summary,
        "doctors": doctors,
        "totals": _total(rows),
    }


def rounded(rep: dict[str, Any]) -> dict[str, Any]:
    """The same report with money rounded to paise (qty to 3 decimals) for showing / comparing."""
    def m(o: dict[str, Any]) -> dict[str, Any]:
        out = dict(o)
        for k in _MONEY_KEYS:
            if k in out:
                out[k] = r2(out[k])
        if "qty" in out:
            out["qty"] = _math_round(out["qty"] * 1000) / 1000
        return out

    return {
        **rep,
        "rows": [m(r) for r in rep["rows"]],
        "summary": [m(r) for r in rep["summary"]],
        "doctors": [m(r) for r in rep["doctors"]],
        "totals": m(rep["totals"]),
    }
