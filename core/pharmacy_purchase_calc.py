"""
Indian pharmacy supplier invoice calculation engine.

Pipeline (GST law compliant):
  1. Line amount = rate × billed_qty  (free qty is bonus — never reduces billed qty)
  2. Gross = Σ line amounts
  3. Group lines by GST slab
  4. Cash/product discount split proportionally across slabs BEFORE GST
  5. GST on post-discount taxable per slab; intra-state → CGST + SGST
  6. Net = gross − discounts + total GST + round_off
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _line_goods_amount(item: Dict[str, Any]) -> float:
    """Pre-GST goods value: rate × billed qty (free items not deducted)."""
    locked = item.get("import_taxable")
    if locked is not None and item.get("import_lock_values"):
        return round(_f(locked), 4)
    qty = _f(item.get("qty"))
    rate = _f(item.get("rate"))
    amount = _f(item.get("amount"))
    if amount > 0 and qty > 0 and abs(amount - qty * rate) <= max(0.05, amount * 0.01):
        return round(amount, 4)
    if amount > 0 and qty <= 0:
        return round(amount, 4)
    return round(qty * rate, 4)


def _gst_slab_key(item: Dict[str, Any]) -> float:
    return round(_f(item.get("gst_pct", item.get("gst_value", 0))), 4)


def calc_pharmacy_purchase_bill(
    items: Sequence[Dict[str, Any]],
    cash_discount: float = 0.0,
    product_discount: float = 0.0,
    round_off: Optional[float] = None,
    net_payable: Optional[float] = None,
    supply_type: str = "intra",
) -> Dict[str, Any]:
    """
    Full supplier bill calculation for Indian pharmacy purchases.

    Returns summary + per-item taxable/gst after proportional slab discounts.
    When net_payable is supplied (from printed bill), it is authoritative.
    """
    working = [dict(i) for i in items]
    cash_discount = round(max(0.0, _f(cash_discount)), 2)
    product_discount = round(max(0.0, _f(product_discount)), 2)
    total_bill_discount = round(cash_discount + product_discount, 2)

    # Step 1 — line goods amounts (rate × billed qty)
    for item in working:
        goods = _line_goods_amount(item)
        item["_goods_amount"] = round(goods, 2)
        item["billed_qty"] = _f(item.get("qty"))
        item["base"] = round(goods, 2)

    # Step 3 — gross
    gross_total = round(sum(i["_goods_amount"] for i in working), 2)

    # Step 4 — group by GST slab
    slabs: Dict[float, List[Dict[str, Any]]] = {}
    for item in working:
        key = _gst_slab_key(item)
        slabs.setdefault(key, []).append(item)

    slab_totals: Dict[float, float] = {
        k: round(sum(i["_goods_amount"] for i in grp), 2) for k, grp in slabs.items()
    }

    # Step 5 — proportional discount per slab (before GST)
    slab_discounts: Dict[float, float] = {}
    remaining_disc = total_bill_discount
    slab_keys = sorted(slab_totals.keys(), key=lambda k: slab_totals[k], reverse=True)
    last_key = slab_keys[-1] if slab_keys else None
    for key in slab_keys:
        group_gross = slab_totals[key]
        if gross_total > 0 and total_bill_discount > 0:
            if key is last_key:
                disc = round(remaining_disc, 2)
            else:
                disc = round(total_bill_discount * group_gross / gross_total, 2)
                remaining_disc = round(remaining_disc - disc, 2)
        else:
            disc = 0.0
        slab_discounts[key] = disc

    # Steps 5–6 — item taxable, GST, CGST/SGST
    total_cgst = 0.0
    total_sgst = 0.0
    total_gst = 0.0
    taxable_total = 0.0

    for key, grp in slabs.items():
        slab_gross = slab_totals[key]
        slab_disc = slab_discounts.get(key, 0.0)
        slab_taxable = round(max(0.0, slab_gross - slab_disc), 2)
        gst_rate = key
        slab_gst = round(slab_taxable * gst_rate / 100, 2)

        if supply_type.lower() == "inter":
            item_igst = slab_gst
            item_cgst = 0.0
            item_sgst = 0.0
        else:
            item_cgst = round(slab_gst / 2, 2)
            item_sgst = round(slab_gst - item_cgst, 2)
            item_igst = 0.0

        total_cgst += item_cgst
        total_sgst += item_sgst
        total_gst += slab_gst
        taxable_total += slab_taxable

        # Allocate slab discount + GST down to items proportionally
        rem_disc = slab_disc
        rem_taxable = slab_taxable
        last_item = grp[-1] if grp else None
        for item in grp:
            share = item["_goods_amount"] / slab_gross if slab_gross > 0 else 0.0
            if item is last_item:
                item_disc = rem_disc
                item_taxable = rem_taxable
            else:
                item_disc = round(slab_disc * share, 2)
                item_taxable = round(slab_taxable * share, 2)
                rem_disc = round(rem_disc - item_disc, 2)
                rem_taxable = round(rem_taxable - item_taxable, 2)

            item_gst = round(item_taxable * gst_rate / 100, 2)
            if supply_type.lower() == "inter":
                item_c = 0.0
                item_s = 0.0
            else:
                item_c = round(item_gst / 2, 2)
                item_s = round(item_gst - item_c, 2)

            item["cash_disc_share"] = round(item_disc, 2)
            item["discount_amt"] = round(item_disc, 2)
            item["overall_discount_amt"] = round(item_disc, 2)
            item["taxable"] = round(item_taxable, 2)
            item["gst_amt"] = item_gst
            item["cgst_amt"] = item_c
            item["sgst_amt"] = item_s
            item["item_amount"] = round(item_taxable + item_gst, 2)
            item["amount"] = item["item_amount"]
            item["_taxable_before_overall"] = round(item["_goods_amount"], 2)

    total_cgst = round(total_cgst, 2)
    total_sgst = round(total_sgst, 2)
    total_gst = round(total_gst, 2)
    taxable_total = round(taxable_total, 2)

    pre_round = round(gross_total - total_bill_discount + total_gst, 2)

    if net_payable is not None and _f(net_payable) > 0:
        total_amount = round(_f(net_payable), 2)
        if round_off is not None:
            rounding = round(_f(round_off), 2)
        else:
            rounding = round(total_amount - pre_round, 2)
    else:
        if round_off is not None:
            rounding = round(_f(round_off), 2)
        else:
            rounding = round(round(pre_round) - pre_round, 2)
        total_amount = round(pre_round + rounding, 2)

    validation = validate_pharmacy_bill(
        gross_total=gross_total,
        cash_discount=cash_discount,
        product_discount=product_discount,
        taxable_total=taxable_total,
        total_cgst=total_cgst,
        total_sgst=total_sgst,
        net_payable=total_amount,
        items=working,
    )

    return {
        "gross_total": gross_total,
        "gross_subtotal": gross_total,
        "subtotal": taxable_total,
        "product_discount": product_discount,
        "cash_discount": cash_discount,
        "discount_amount": total_bill_discount,
        "overall_discount": total_bill_discount,
        "taxable_total": taxable_total,
        "total_gst": total_gst,
        "cgst": total_cgst,
        "sgst": total_sgst,
        "pre_round_total": pre_round,
        "rounding": rounding,
        "total_amount": total_amount,
        "net_payable": total_amount,
        "slab_totals": slab_totals,
        "slab_discounts": slab_discounts,
        "items": working,
        "validation": validation,
        "supply_type": supply_type,
    }


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
    """Run post-calculation validation checks."""
    flags: List[str] = []
    total_disc = round(cash_discount + product_discount, 2)
    expected_taxable = round(gross_total - total_disc, 2)
    if abs(expected_taxable - taxable_total) > 0.05:
        flags.append(
            "Taxable mismatch: {:.2f} vs {:.2f}".format(taxable_total, expected_taxable)
        )

    calc_net = round(gross_total - total_disc + total_cgst + total_sgst, 2)
    rounding_implied = round(net_payable - calc_net, 2)

    for item in items:
        mrp = _f(item.get("mrp"))
        rate = _f(item.get("rate"))
        if mrp > 0 and rate > mrp + 0.01:
            flags.append("{}: rate {:.2f} > MRP {:.2f}".format(
                str(item.get("name", "?"))[:30], rate, mrp
            ))
        qty = _f(item.get("qty"))
        free = _f(item.get("free_qty"))
        if free > qty and qty > 0:
            flags.append("{}: free qty {:.2f} >= billed qty {:.2f}".format(
                str(item.get("name", "?"))[:30], free, qty
            ))

    return {
        "flags": flags,
        "expected_taxable": expected_taxable,
        "calc_net_before_round": calc_net,
        "implied_round_off": rounding_implied,
        "gst_split_ok": abs(total_cgst - total_sgst) <= 0.02,
    }


def items_from_pharmacy_calc(calc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(calc.get("items") or [])
