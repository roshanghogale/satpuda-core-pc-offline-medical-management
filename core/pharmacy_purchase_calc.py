"""
Centralized deterministic purchase invoice calculation engine (Indian pharmacy).

Pipeline:
  1. Line Evaluation        — qty × rate, item discount → Net_Line_Amount
  2. Slab Grouping          — group by gst_percent, spark-allocate bill discount
  3. Tax Extraction Matrix  — Mode A (tax-inclusive) | Mode B (tax-exclusive)
  4. Matrix Verification    — validate slab totals vs invoice summary

All monetary math uses Decimal (half-up to 2 dp; Mode A CGST/SGST round-up).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple

_TWO = Decimal("0.01")
_ZERO = Decimal("0")


def _d(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_TWO, rounding=ROUND_HALF_UP)


def _round_up2(value: Decimal) -> Decimal:
    if value <= _ZERO:
        return _ZERO
    return value.quantize(_TWO, rounding=ROUND_CEILING)


def _f(value: Decimal) -> float:
    return float(_round2(value))


def _gst_slab_key(item: Dict[str, Any]) -> Decimal:
    return _round2(_d(item.get("gst_pct", item.get("gst_percent", item.get("gst_value", 0)))))


def _item_disc_percent(item: Dict[str, Any]) -> Decimal:
    return _d(item.get("item_disc_percent", item.get("discount_pct", item.get("item_discount", 0))))


def _line_is_tax_inclusive(item: Dict[str, Any], bill_inclusive: bool) -> bool:
    if "is_tax_inclusive" in item:
        return bool(item.get("is_tax_inclusive"))
    return bill_inclusive


def gst_method_to_tax_mode(gst_calc_method: str) -> str:
    """
    Map UI gst_calc_method to tax extraction mode.

    discount_after_gst  → Mode A (tax-inclusive / Swami Samarth style)
    discount_before_gst → Mode B (tax-exclusive / Jai Ganesh style)
    """
    method = (gst_calc_method or "discount_after_gst").strip()
    if method == "discount_before_gst":
        return "exclusive"
    return "inclusive"


def tax_mode_to_gst_method(tax_mode: str) -> str:
    mode = (tax_mode or "inclusive").strip().lower()
    if mode in ("exclusive", "tax_exclusive", "mode_b", "b"):
        return "discount_before_gst"
    return "discount_after_gst"


# ── STEP 1: LINE ITEM EVALUATION ─────────────────────────────────────────────

def evaluate_line_items(
    items: Sequence[Dict[str, Any]],
    bill_tax_inclusive: bool = False,
) -> List[Dict[str, Any]]:
    """Compute Gross_Line_Amount, Line_Discount, Net_Line_Amount per row."""
    working: List[Dict[str, Any]] = []
    for raw in items:
        row = dict(raw)
        qty = _d(row.get("qty"))
        rate = _d(row.get("rate"))
        disc_pct = _item_disc_percent(row)
        gst_pct = _gst_slab_key(row)
        inclusive = _line_is_tax_inclusive(row, bill_tax_inclusive)

        gross_line = _round2(qty * rate)
        line_discount = _round2(gross_line * disc_pct / Decimal("100"))
        net_line = _round2(gross_line - line_discount)

        row["qty"] = _f(qty)
        row["rate"] = _f(rate)
        row["free_qty"] = _f(_d(row.get("free_qty")))
        row["discount_pct"] = _f(disc_pct)
        row["item_disc_percent"] = _f(disc_pct)
        row["gst_pct"] = _f(gst_pct)
        row["gst_percent"] = _f(gst_pct)
        row["is_tax_inclusive"] = inclusive
        row["base"] = _f(gross_line)
        row["gross_line_amount"] = _f(gross_line)
        row["line_discount"] = _f(line_discount)
        row["discount_amt"] = _f(line_discount)
        row["net_line_amount"] = _f(net_line)
        row["_goods_amount"] = _f(net_line)
        row["billed_qty"] = _f(qty)
        working.append(row)
    return working


# ── STEP 2: SLAB GROUPING & DISCOUNT SPARK-ALLOCATION ────────────────────────

def allocate_slab_discounts(
    items: Sequence[Dict[str, Any]],
    global_cash_discount: Decimal,
) -> Tuple[Dict[Decimal, Decimal], Dict[Decimal, Decimal], Dict[Decimal, Decimal], Decimal]:
    """
    Group Net_Line_Amount by gst_percent; proportionally allocate bill discount.

    Returns (slab_gross, slab_discount, slab_taxable_basis, total_gross).
    """
    global_cash_discount = _round2(max(_ZERO, global_cash_discount))
    slabs: Dict[Decimal, Decimal] = {}
    for item in items:
        key = _gst_slab_key(item)
        slabs[key] = _round2(slabs.get(key, _ZERO) + _d(item.get("net_line_amount", item.get("_goods_amount", 0))))

    total_gross = _round2(sum(slabs.values(), _ZERO))
    slab_discounts: Dict[Decimal, Decimal] = {k: _ZERO for k in slabs}
    slab_basis: Dict[Decimal, Decimal] = dict(slabs)

    if total_gross > _ZERO and global_cash_discount > _ZERO:
        remaining = global_cash_discount
        keys = sorted(slabs.keys(), key=lambda k: slabs[k], reverse=True)
        last_key = keys[-1]
        for key in keys:
            if key is last_key:
                disc = _round2(remaining)
            else:
                weight = slabs[key] / total_gross
                disc = _round2(weight * global_cash_discount)
                remaining = _round2(remaining - disc)
            slab_discounts[key] = disc
            slab_basis[key] = _round2(max(_ZERO, slabs[key] - disc))

    return slabs, slab_discounts, slab_basis, total_gross


# ── STEP 3: DUAL-MODE TAX EXTRACTION MATRIX ──────────────────────────────────

def _extract_slab_tax_inclusive(
    taxable_basis: Decimal,
    gst_rate: Decimal,
    supply_type: str,
) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    """Mode A — rate includes GST; extract tax with CGST/SGST round-up."""
    if taxable_basis <= _ZERO:
        return _ZERO, _ZERO, _ZERO, _ZERO
    if gst_rate <= _ZERO:
        # 0% GST slab: entire basis is taxable, no tax component to extract.
        return _round2(taxable_basis), _ZERO, _ZERO, _ZERO
    divisor = Decimal("1") + gst_rate / Decimal("100")
    real_taxable = _round2(taxable_basis / divisor)
    total_slab_gst = _round2(taxable_basis - real_taxable)
    if supply_type.lower() == "inter":
        return real_taxable, _ZERO, _ZERO, total_slab_gst
    half = _round_up2(total_slab_gst / Decimal("2"))
    cgst = half
    sgst = half
    return real_taxable, cgst, sgst, _round2(cgst + sgst)


def _extract_slab_tax_exclusive(
    taxable_basis: Decimal,
    gst_rate: Decimal,
    supply_type: str,
) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    """Mode B — rate is base price; GST computed on taxable basis."""
    real_taxable = _round2(taxable_basis)
    if real_taxable <= _ZERO or gst_rate <= _ZERO:
        return real_taxable, _ZERO, _ZERO, _ZERO
    half_rate = gst_rate / Decimal("2")
    if supply_type.lower() == "inter":
        igst = _round2(real_taxable * gst_rate / Decimal("100"))
        return real_taxable, _ZERO, _ZERO, igst
    cgst = _round2(real_taxable * half_rate / Decimal("100"))
    sgst = _round2(real_taxable * half_rate / Decimal("100"))
    return real_taxable, cgst, sgst, _round2(cgst + sgst)


def extract_slab_taxes(
    slab_gross: Dict[Decimal, Decimal],
    slab_basis: Dict[Decimal, Decimal],
    items: Sequence[Dict[str, Any]],
    bill_tax_mode: str,
    supply_type: str = "intra",
) -> List[Dict[str, Any]]:
    """
    Apply Mode A or Mode B per slab; supports mixed inclusive/exclusive lines
    within the same GST % by splitting slab basis proportionally.
    """
    breakdown: List[Dict[str, Any]] = []
    items_by_slab: Dict[Decimal, List[Dict[str, Any]]] = {}
    for item in items:
        items_by_slab.setdefault(_gst_slab_key(item), []).append(item)

    for gst_rate in sorted(slab_basis.keys()):
        basis_total = slab_basis[gst_rate]
        grp = items_by_slab.get(gst_rate, [])
        slab_g = slab_gross.get(gst_rate, _ZERO)
        if basis_total <= _ZERO or not grp:
            breakdown.append({
                "gst_pct": _f(gst_rate),
                "taxable_basis": _f(basis_total),
                "taxable": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "total_gst": 0.0,
                "tax_mode": bill_tax_mode,
            })
            continue

        inc_net = _ZERO
        exc_net = _ZERO
        for item in grp:
            net = _d(item.get("net_line_amount", item.get("_goods_amount", 0)))
            if item.get("is_tax_inclusive"):
                inc_net += net
            else:
                exc_net += net
        inc_net = _round2(inc_net)
        exc_net = _round2(exc_net)
        slab_net = _round2(inc_net + exc_net) or slab_g

        inc_basis = _round2(basis_total * inc_net / slab_net) if slab_net > _ZERO else _ZERO
        exc_basis = _round2(basis_total - inc_basis)

        taxable = cgst = sgst = total_gst = _ZERO
        modes_used: List[str] = []

        if inc_basis > _ZERO:
            t, c, s, g = _extract_slab_tax_inclusive(inc_basis, gst_rate, supply_type)
            taxable += t
            cgst += c
            sgst += s
            total_gst += g
            modes_used.append("inclusive")

        if exc_basis > _ZERO:
            t, c, s, g = _extract_slab_tax_exclusive(exc_basis, gst_rate, supply_type)
            taxable += t
            cgst += c
            sgst += s
            total_gst += g
            modes_used.append("exclusive")

        tax_mode = modes_used[0] if len(modes_used) == 1 else "mixed"
        breakdown.append({
            "gst_pct": _f(gst_rate),
            "taxable_basis": _f(basis_total),
            "taxable": _f(_round2(taxable)),
            "cgst": _f(_round2(cgst)),
            "sgst": _f(_round2(sgst)),
            "total_gst": _f(_round2(total_gst)),
            "tax_mode": tax_mode,
        })
    return breakdown


def _allocate_items_in_slab(
    grp: List[Dict[str, Any]],
    slab_gross: Decimal,
    slab_disc: Decimal,
    slab_taxable: Decimal,
    slab_gst: Decimal,
    supply_type: str,
) -> None:
    """Distribute slab discount and tax to line items (display / persistence)."""
    rem_disc = slab_disc
    rem_taxable = slab_taxable
    rem_gst = slab_gst
    last_item = grp[-1] if grp else None
    for item in grp:
        share = (
            _d(item.get("net_line_amount", item.get("_goods_amount", 0))) / slab_gross
            if slab_gross > _ZERO else _ZERO
        )
        if item is last_item:
            item_disc = rem_disc
            item_taxable = rem_taxable
            item_gst = rem_gst
        else:
            item_disc = _round2(slab_disc * share)
            item_taxable = _round2(slab_taxable * share)
            item_gst = _round2(slab_gst * share)
            rem_disc = _round2(rem_disc - item_disc)
            rem_taxable = _round2(rem_taxable - item_taxable)
            rem_gst = _round2(rem_gst - item_gst)

        if supply_type.lower() == "inter":
            item_c = _ZERO
            item_s = _ZERO
        else:
            item_c = _round2(item_gst / Decimal("2"))
            item_s = _round2(item_gst - item_c)

        # Safety: 0% GST lines — full net goods value is taxable + amount.
        net_goods = _d(item.get("net_line_amount", item.get("_goods_amount", 0)))
        if net_goods > _ZERO and item_taxable <= _ZERO and item_gst <= _ZERO:
            item_taxable = _round2(net_goods - item_disc)

        item["cash_disc_share"] = _f(item_disc)
        item["overall_discount_amt"] = _f(item_disc)
        item["taxable"] = _f(item_taxable)
        item["gst_amt"] = _f(item_gst)
        item["cgst_amt"] = _f(item_c)
        item["sgst_amt"] = _f(item_s)
        item["item_amount"] = _f(_round2(item_taxable + item_gst))
        item["amount"] = item["item_amount"]
        item["_taxable_before_overall"] = item.get("net_line_amount", item.get("_goods_amount", 0))


# ── STEP 4: GLOBAL SUMMARY + MATRIX VERIFICATION ─────────────────────────────

def verify_invoice_matrix(
    gross_total: Decimal,
    global_discount: Decimal,
    taxable_total: Decimal,
    total_cgst: Decimal,
    total_sgst: Decimal,
    net_payable: Decimal,
    tax_mode: str,
    slab_breakdown: Sequence[Dict[str, Any]],
    items: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Dynamic matrix verification — flag slab/summary drift."""
    flags: List[str] = []
    inclusive = tax_mode == "inclusive"

    slab_taxable = _round2(sum(_d(s.get("taxable", 0)) for s in slab_breakdown))
    if abs(slab_taxable - taxable_total) > Decimal("0.02"):
        flags.append(
            "Slab taxable {:.2f} vs summary {:.2f}".format(_f(slab_taxable), _f(taxable_total))
        )

    slab_cgst = _round2(sum(_d(s.get("cgst", 0)) for s in slab_breakdown))
    slab_sgst = _round2(sum(_d(s.get("sgst", 0)) for s in slab_breakdown))
    if abs(slab_cgst - total_cgst) > Decimal("0.02"):
        flags.append("CGST matrix drift")
    if abs(slab_sgst - total_sgst) > Decimal("0.02"):
        flags.append("SGST matrix drift")

    total_gst = _round2(total_cgst + total_sgst)
    if inclusive:
        expected_pre = _round2(gross_total - global_discount)
    else:
        expected_pre = _round2(taxable_total + total_gst)
    calc_net = _round2(expected_pre)
    implied_round = _round2(net_payable - calc_net)
    if abs(implied_round) > Decimal("1.00") and abs(net_payable - calc_net) > Decimal("0.05"):
        flags.append("Net payable drift {:.2f}".format(_f(net_payable - calc_net)))

    for item in items:
        mrp = _d(item.get("mrp"))
        rate = _d(item.get("rate"))
        if mrp > _ZERO and rate > mrp + Decimal("0.01"):
            flags.append("{}: rate > MRP".format(str(item.get("name", "?"))[:30]))
        qty = _d(item.get("qty"))
        free = _d(item.get("free_qty"))
        if free > qty and qty > _ZERO:
            flags.append("{}: free qty >= billed qty".format(str(item.get("name", "?"))[:30]))

    return {
        "flags": flags,
        "slab_taxable_sum": _f(slab_taxable),
        "implied_round_off": _f(implied_round),
        "gst_split_ok": abs(total_cgst - total_sgst) <= Decimal("0.02"),
        "tax_mode": tax_mode,
    }


def compute_purchase_invoice(
    items: Sequence[Dict[str, Any]],
    global_cash_discount: float = 0.0,
    product_discount: float = 0.0,
    round_off: Optional[float] = None,
    net_payable: Optional[float] = None,
    supply_type: str = "intra",
    tax_mode: Optional[str] = None,
    gst_calc_method: str = "discount_after_gst",
) -> Dict[str, Any]:
    """
    Full 3-step purchase invoice pipeline.

    Parameters
    ----------
    global_cash_discount : bill-level cash discount (₹)
    product_discount     : additional product discount (₹); summed unless deduped by caller
    tax_mode             : 'inclusive' (Mode A) | 'exclusive' (Mode B); derived from
                           gst_calc_method when omitted
    """
    mode = (tax_mode or gst_method_to_tax_mode(gst_calc_method)).strip().lower()
    if mode not in ("inclusive", "exclusive"):
        mode = gst_method_to_tax_mode(gst_calc_method)

    bill_inclusive = mode == "inclusive"
    cash_disc = _round2(_d(global_cash_discount))
    prod_disc = _round2(_d(product_discount))
    total_bill_discount = _round2(cash_disc + prod_disc)

    # Step 1
    working = evaluate_line_items(items, bill_tax_inclusive=bill_inclusive)

    # Step 2
    slab_gross, slab_discounts, slab_basis, gross_total = allocate_slab_discounts(
        working, total_bill_discount,
    )

    # Step 3
    tax_rows = extract_slab_taxes(
        slab_gross, slab_basis, working, mode, supply_type,
    )

    total_cgst = _round2(sum(_d(r["cgst"]) for r in tax_rows))
    total_sgst = _round2(sum(_d(r["sgst"]) for r in tax_rows))
    total_gst = _round2(sum(_d(r["total_gst"]) for r in tax_rows))
    taxable_total = _round2(sum(_d(r["taxable"]) for r in tax_rows))

    # Item allocation + slab_breakdown (UI / GST slab dialog)
    slabs: Dict[Decimal, List[Dict[str, Any]]] = {}
    for item in working:
        slabs.setdefault(_gst_slab_key(item), []).append(item)

    slab_breakdown: List[Dict[str, Any]] = []
    for row in tax_rows:
        gst_rate = _d(row["gst_pct"])
        grp = slabs.get(gst_rate, [])
        slab_g = slab_gross.get(gst_rate, _ZERO)
        slab_d = slab_discounts.get(gst_rate, _ZERO)
        slab_t = _d(row["taxable"])
        slab_gst = _d(row["total_gst"])
        _allocate_items_in_slab(grp, slab_g, slab_d, slab_t, slab_gst, supply_type)
        slab_breakdown.append({
            "gst_pct": row["gst_pct"],
            "gross": _f(slab_g),
            "discount": _f(slab_d),
            "taxable": row["taxable"],
            "taxable_basis": row["taxable_basis"],
            "cgst": row["cgst"],
            "sgst": row["sgst"],
            "total_gst": row["total_gst"],
            "tax_mode": row["tax_mode"],
        })

    # Global summary — mixed mode uses taxable + GST; pure inclusive uses gross − discount
    has_exclusive = any(not i.get("is_tax_inclusive") for i in working)
    has_inclusive = any(i.get("is_tax_inclusive") for i in working)
    if has_inclusive and not has_exclusive:
        pre_round = _round2(gross_total - total_bill_discount)
    else:
        pre_round = _round2(taxable_total + total_gst)

    if net_payable is not None and _d(net_payable) > _ZERO:
        total_amount = _round2(_d(net_payable))
        if round_off is not None:
            rounding = _round2(_d(round_off))
        else:
            rounding = _round2(total_amount - pre_round)
    else:
        if round_off is not None:
            rounding = _round2(_d(round_off))
        else:
            rounded = pre_round.quantize(_TWO, rounding=ROUND_HALF_UP)
            if pre_round != rounded:
                rounded = Decimal(int(pre_round + Decimal("0.5")))
            rounding = _round2(rounded - pre_round)
        total_amount = _round2(pre_round + rounding)

    validation = verify_invoice_matrix(
        gross_total=gross_total,
        global_discount=total_bill_discount,
        taxable_total=taxable_total,
        total_cgst=total_cgst,
        total_sgst=total_sgst,
        net_payable=total_amount,
        tax_mode=mode,
        slab_breakdown=slab_breakdown,
        items=working,
    )

    gst_method = tax_mode_to_gst_method(mode)

    return {
        "gross_total": _f(gross_total),
        "gross_subtotal": _f(gross_total),
        "subtotal": _f(taxable_total),
        "taxable_total": _f(taxable_total),
        "product_discount": _f(prod_disc),
        "cash_discount": _f(cash_disc),
        "discount_amount": _f(total_bill_discount),
        "overall_discount": _f(total_bill_discount),
        "total_gst": _f(total_gst),
        "cgst": _f(total_cgst),
        "sgst": _f(total_sgst),
        "pre_round_total": _f(pre_round),
        "rounding": _f(rounding),
        "total_amount": _f(total_amount),
        "net_payable": _f(total_amount),
        "slab_totals": {float(k): _f(v) for k, v in slab_gross.items()},
        "slab_discounts": {float(k): _f(v) for k, v in slab_discounts.items()},
        "slab_breakdown": slab_breakdown,
        "items": working,
        "validation": validation,
        "supply_type": supply_type,
        "tax_mode": mode,
        "gst_calc_method": gst_method,
    }


def calc_pharmacy_purchase_bill(
    items: Sequence[Dict[str, Any]],
    cash_discount: float = 0.0,
    product_discount: float = 0.0,
    round_off: Optional[float] = None,
    net_payable: Optional[float] = None,
    supply_type: str = "intra",
    gst_calc_method: str = "discount_after_gst",
    tax_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Backward-compatible entry — delegates to compute_purchase_invoice."""
    return compute_purchase_invoice(
        items=items,
        global_cash_discount=cash_discount,
        product_discount=product_discount,
        round_off=round_off,
        net_payable=net_payable,
        supply_type=supply_type,
        tax_mode=tax_mode,
        gst_calc_method=gst_calc_method,
    )


def validate_pharmacy_bill(
    gross_total: float,
    cash_discount: float,
    product_discount: float,
    taxable_total: float,
    total_cgst: float,
    total_sgst: float,
    net_payable: float,
    items: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Legacy validation wrapper."""
    return verify_invoice_matrix(
        gross_total=_d(gross_total),
        global_discount=_round2(_d(cash_discount) + _d(product_discount)),
        taxable_total=_d(taxable_total),
        total_cgst=_d(total_cgst),
        total_sgst=_d(total_sgst),
        net_payable=_d(net_payable),
        tax_mode="exclusive",
        slab_breakdown=[],
        items=items,
    )


def items_from_pharmacy_calc(calc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(calc.get("items") or [])
