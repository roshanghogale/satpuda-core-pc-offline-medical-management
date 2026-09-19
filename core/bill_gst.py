"""GST on a printed sale bill.

A sale line's amount is what the customer pays for it, tax included, so the tax is
backed out of it. bill_context used to back it out of each line BEFORE the bill
discount -- ``round(amt * pct / (100 + pct), 2)`` -- and then print a taxable value of
grand total (AFTER the discount and the round-off) minus that tax. On a discounted bill
the GST printed was more than was charged and the taxable value was short by the same
amount: 258.00 of 12% lines less an 8.00 discount printed GST 27.64 on 222.36, when the
tax in the 250.00 the customer paid was 26.78 on 223.22.

The bill discount is spread over the lines in proportion to their amounts first, then
each line's tax comes out of what is left of it:

    taxable = round(net * 100 / (100 + rate))
    tax     = net - taxable
    CGST    = round(tax / 2)
    SGST    = tax - CGST

All in Decimal, rounding half-up. Python's round() on a float rounds the binary value
half-to-even, so half of 1.97 came out 0.98 where half of 1.95 came out 0.98 too, and the
CGST/SGST split flipped from line to line. Android does the same sum in
core/bill/BillGst.kt; the two must stay identical (its BillGstTest checks the PC's own
figures from the bill_golden fixture).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
ZERO = Decimal("0.00")


def to_paise(value) -> Decimal:
    """A stored money or rate figure as an exact two-place Decimal.

    round(x, 2) first -- the figure mac2 stored -- so 41.4 is 41.40 and not the binary
    value just under it. Android: BigDecimal(x).setScale(2, HALF_EVEN), the same digits.
    """
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return Decimal(repr(round(number, 2))).quantize(CENT, rounding=ROUND_HALF_UP)


def _half_up(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def split_tax(tax) -> tuple[Decimal, Decimal]:
    """(CGST, SGST) of one tax figure: CGST is half of it rounded up, SGST the rest."""
    tax = tax if isinstance(tax, Decimal) else to_paise(tax)
    cgst = _half_up(tax / 2)
    return cgst, tax - cgst


@dataclass(frozen=True)
class LineGst:
    amount: Decimal    # the line as sold, tax included
    discount: Decimal  # its share of the bill discount
    net: Decimal       # amount - discount: what the tax is taken out of
    rate: Decimal
    taxable: Decimal
    tax: Decimal
    cgst: Decimal
    sgst: Decimal


@dataclass(frozen=True)
class BillGst:
    lines: tuple[LineGst, ...]
    net: Decimal
    taxable: Decimal
    tax: Decimal
    cgst: Decimal
    sgst: Decimal


def printed_bill_gst(lines: Iterable[tuple], discount=0, *, gst_enabled: bool = True) -> BillGst:
    """The GST a sale bill prints, from its (line amount, GST %) pairs and bill discount.

    With GST off every line counts as 0%: nothing is backed out, and the taxable value
    is what the lines come to after the discount.
    """
    pairs = [
        (to_paise(amount), to_paise(rate) if gst_enabled else ZERO)
        for amount, rate in lines
    ]
    base = sum((amount for amount, _ in pairs if amount > 0), ZERO)
    disc = min(max(to_paise(discount), ZERO), base)
    shares = [
        _half_up(disc * amount / base) if amount > 0 and base > 0 else ZERO
        for amount, _ in pairs
    ]
    leftover = disc - sum(shares, ZERO)
    if leftover:
        # Rounding each share can leave a paisa or two over or under; the biggest line
        # takes it, so the shares always add back to the discount printed on the bill.
        biggest = max(range(len(pairs)), key=lambda i: pairs[i][0])
        shares[biggest] += leftover

    out = []
    for (amount, rate), share in zip(pairs, shares):
        net = amount - share
        taxable = _half_up(net * HUNDRED / (HUNDRED + rate)) if rate > 0 else net
        tax = net - taxable
        cgst, sgst = split_tax(tax)
        out.append(LineGst(amount, share, net, rate, taxable, tax, cgst, sgst))

    def total(name: str) -> Decimal:
        return sum((getattr(line, name) for line in out), ZERO)

    return BillGst(
        tuple(out), total("net"), total("taxable"), total("tax"), total("cgst"), total("sgst"),
    )


def gst_strip_figures(ctx) -> tuple[float, float, float]:
    """(taxable, CGST, SGST) for a bill's GST strip.

    A context from _build_bill_context carries all three. One put together elsewhere (a
    supplier document, a test) may carry only gst_amount: that is split the same way, and
    its taxable value falls back to what the strip always printed, total minus GST.
    """
    tax = to_paise(getattr(ctx, "gst_amount", 0))
    cgst = to_paise(getattr(ctx, "cgst_amount", 0))
    sgst = to_paise(getattr(ctx, "sgst_amount", 0))
    if cgst + sgst != tax:
        cgst, sgst = split_tax(tax)
    taxable = to_paise(getattr(ctx, "taxable_amount", 0))
    if taxable <= 0:
        taxable = max(ZERO, to_paise(getattr(ctx, "grand_total", 0)) - tax)
    return float(taxable), float(cgst), float(sgst)
