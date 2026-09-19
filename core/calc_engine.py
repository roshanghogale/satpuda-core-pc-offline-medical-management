"""
Centralized Calculation Engine
All monetary calculations for billing, purchase, and bill-edit pages live here.
"""
import math
from typing import Any


def _money(v: Any, default: float = 0.0) -> float:
    """Coerce cash/due/qty-like values; never raise on blank/invalid."""
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        try:
            fv = float(v)
            return default if fv != fv else fv
        except (TypeError, ValueError):
            return default
    try:
        s = str(v).strip().replace(",", "").replace("₹", "").strip()
        if not s:
            return default
        fv = float(s)
        return default if fv != fv else fv
    except (TypeError, ValueError):
        return default


# ── Item-level ────────────────────────────────────────────────────────────────

def calc_item_amount(qty: float, rate: float, discount_pct: float = 0) -> float:
    """Base amount for one line item (no GST)."""
    return round(qty * rate * (1 - discount_pct / 100), 2)


def calc_item_gst(qty: float, rate: float, gst_pct: float,
                  item_discount_pct: float = 0) -> float:
    """GST amount for one line item."""
    base = calc_item_amount(qty, rate, item_discount_pct)
    return round(base * gst_pct / 100, 2)


def calc_item_total(qty: float, rate: float, gst_pct: float = 0,
                    item_discount_pct: float = 0,
                    gst_method: str = "discount_before_gst") -> float:
    """
    Full line-item total including GST.
    gst_method: 'discount_before_gst' | 'discount_after_gst'
    """
    base = qty * rate
    if gst_method == "discount_before_gst":
        net = base * (1 - item_discount_pct / 100)
        return round(net + net * gst_pct / 100, 2)
    else:
        gst_price = base + base * gst_pct / 100
        return round(gst_price * (1 - item_discount_pct / 100), 2)


# ── Auto-rounding ─────────────────────────────────────────────────────────────

def auto_round(amount: float) -> float:
    """Return the rounding adjustment to reach the nearest integer (half-up)."""
    rounded = math.floor(amount + 0.5)
    return round(rounded - amount, 2)


# ── Bill / Sales summary ──────────────────────────────────────────────────────

def calc_bill_summary(items: list, discount_pct: float = 0,
                      rounding: float = 0,
                      discount_rs: float = None) -> dict:
    """
    Compute billing page totals.

    items: list of dicts with 'amount' key (already discounted per-item).
    discount_rs: overall discount in rupees (takes priority over discount_pct if provided).
    Returns: subtotal, discount_amount, discount_pct, total_amount
    """
    subtotal = round(sum(_money(i.get("amount")) for i in items), 2)
    if discount_rs is not None:
        discount_amount = round(min(_money(discount_rs), subtotal), 2)
        actual_pct = round(discount_amount / subtotal * 100, 4) if subtotal > 0 else 0.0
    else:
        discount_amount = round(subtotal * _money(discount_pct) / 100, 2)
        actual_pct = _money(discount_pct)
    pre_round = round(subtotal - discount_amount, 2)
    total_amount = round(pre_round + _money(rounding), 2)
    return {
        'subtotal': subtotal,
        'discount_amount': discount_amount,
        'discount_pct': actual_pct,
        'pre_round_total': pre_round,
        'total_amount': total_amount,
    }


def calc_payment_result(total_amount: float, cash_paid: float,
                        online_paid: float, previous_due: float = 0,
                        previous_credit: float = 0) -> dict:
    """
    Sales payment outcome.

    due_amount  = max(0, total_amount - amount_paid)   # this bill only
    total_due   = max(0, prev_net + total_amount - amount_paid)
    credit      = max(0, amount_paid - total_amount - previous_due)
                # overpayment only — leftover previous_credit is NOT bill credit

    prev_net = max(0, previous_due - previous_credit)

    Returns: amount_paid, due_amount, credit_amount, need_to_pay, total_due
    """
    eps = 0.01
    amount_paid = round(_money(cash_paid) + _money(online_paid), 2)
    prev_due = round(_money(previous_due), 2)
    prev_credit = round(_money(previous_credit), 2)
    prev_net = round(max(0.0, prev_due - prev_credit), 2)
    total = round(_money(total_amount), 2)

    due_amount = round(max(0.0, total - amount_paid), 2)
    if due_amount < eps:
        due_amount = 0.0

    combined = round(prev_net + total - amount_paid, 2)
    if abs(combined) < eps:
        combined = 0.0
    total_due = round(max(0.0, combined), 2)

    # Bill credit = paid beyond bill + previous due (not leftover prev credit).
    overpay = round(amount_paid - (total + prev_due), 2)
    credit_amount = overpay if overpay > eps else 0.0

    need_to_pay = round(prev_net + total, 2)

    # ── What the counter should actually collect ────────────────────────────
    #
    # prev_net above is CLAMPED at zero, so credit is only ever allowed to
    # cancel a previous due. A customer with no due and Rs 500 standing credit
    # therefore reads as owing the whole new bill, and the credit sits there for
    # ever -- which is the report: "the amount becomes the credit for that
    # customer", and then the credit never comes off anything.
    #
    # These are NEW keys, and every field above is left byte-identical on
    # purpose. calc_payment_result is not only a preview: billing_service saves
    # and edits bills through it (:318, :941), core/server_crud.py:281 does too,
    # and sales.due_amount is read back by the daily and monthly exports, the
    # due-reminder list and the home dashboard's cleared/pending counts. Making
    # the stored figures credit-aware silently changes all of those, and offline
    # nothing ever repairs a bill written with due_amount=0. So the stored
    # bookkeeping keeps meaning "this bill alone", and the SCREEN gets the
    # figure the shop needs.
    #
    # No credit is spent by writing anything. core.customer_service
    # recalculate_customer_due -- and the server's own partyDueCascade -- derive
    # the balance from the raw transactions, so credit is consumed by the
    # ABSENCE of a payment. Reducing it here as well would spend it twice.
    prev_balance = round(prev_due - prev_credit, 2)
    if abs(prev_balance) < eps:
        prev_balance = 0.0
    spare_credit = round(max(0.0, -prev_balance), 2)
    # Credit is only used up by what the cash and online payments did NOT cover.
    # A customer with Rs 500 credit who hands over the full Rs 400 anyway has
    # spent no credit at all, and it must still be there for the next bill.
    unpaid = round(max(0.0, total - amount_paid), 2)
    credit_applied = round(min(spare_credit, unpaid), 2)
    if credit_applied < eps:
        credit_applied = 0.0
    net_total_due = round(max(0.0, prev_balance + total - amount_paid), 2)
    if net_total_due < eps:
        net_total_due = 0.0

    return {
        'amount_paid':   amount_paid,
        'due_amount':    due_amount,
        'credit_amount': credit_amount,
        'need_to_pay':   need_to_pay,
        'current_bill_due': due_amount,
        'remaining_previous_due': round(max(0.0, prev_net - max(0.0, amount_paid - total)), 2),
        'total_due': total_due,
        # Credit already on the customer's account that this bill uses up.
        'credit_applied': credit_applied,
        # What is still to be collected once that credit is counted. Same as
        # total_due whenever the customer has no spare credit.
        'net_total_due': net_total_due,
    }


# ── Purchase summary ──────────────────────────────────────────────────────────

def calc_purchase_summary(items: list, overall_discount: float = 0,
                          rounding: float = 0,
                          gst_method: str = "discount_after_gst") -> dict:
    """
    Deprecated — delegates to PurchaseCalculator (slab GST engine).

    Use core.purchase_calculator.PurchaseCalculator for all purchase math.
    """
    from core.purchase_calculator import PurchaseCalculator

    calc = PurchaseCalculator(
        items=items,
        overall_discount=overall_discount,
        rounding=rounding,
        gst_calc_method=gst_method,
    ).calculate()
    return {
        'gross_subtotal': calc.get('gross_subtotal', 0),
        'subtotal_no_gst': calc.get('subtotal', 0),
        'subtotal': calc.get('subtotal', 0),
        'total_gst': calc.get('total_gst', 0),
        'cgst': calc.get('cgst', 0),
        'sgst': calc.get('sgst', 0),
        'discount_amount': calc.get('discount_amount', 0),
        'pre_round_total': calc.get('pre_round_total', 0),
        'total_amount': calc.get('total_amount', 0),
        'rounding': calc.get('rounding', 0),
        'slab_breakdown': calc.get('slab_breakdown', []),
    }


def _calc_purchase_summary_legacy(items: list, overall_discount: float = 0,
                          rounding: float = 0,
                          gst_method: str = "discount_after_gst") -> dict:
    """Legacy per-line proportional discount — kept for reference only."""
    gross_subtotal = 0.0
    gross_with_gst = 0.0
    prepared = []
    after_gst = (gst_method or "discount_after_gst") == "discount_after_gst"

    for item in items:
        qty = float(item.get('qty', 0))
        rate = float(item.get('rate', 0))
        gst_pct = float(item.get('gst_pct', item.get('gst_value', 0)))
        item_disc = float(item.get('discount_pct', item.get('item_discount', 0)))

        base = qty * rate
        taxable_before_overall = base * (1 - item_disc / 100)
        gross_subtotal += taxable_before_overall
        gst_before = taxable_before_overall * gst_pct / 100
        gross_with_gst += taxable_before_overall + gst_before
        prepared.append((item, taxable_before_overall, gst_pct, gst_before))

    discount_base = gross_with_gst if after_gst else gross_subtotal
    discount_amount = round(min(max(float(overall_discount or 0), 0.0), discount_base), 4)
    remaining_discount = discount_amount
    if after_gst:
        discount_rows = [
            row for row in prepared
            if (row[1] + row[3]) > 0
        ]
        last_row = discount_rows[-1] if discount_rows else None
    else:
        discount_rows = [row for row in prepared if row[1] > 0]
        last_row = discount_rows[-1] if discount_rows else None
    total_gst = 0.0

    for row in prepared:
        item, taxable_before_overall, gst_pct, gst_before = row
        if after_gst:
            line_total = taxable_before_overall + gst_before
            if discount_base > 0 and line_total > 0:
                if row is last_row:
                    item_discount = remaining_discount
                else:
                    item_discount = round(
                        discount_amount * line_total / discount_base, 4)
                    remaining_discount = round(remaining_discount - item_discount, 4)
            else:
                item_discount = 0.0
            if line_total > 0:
                taxable_share = round(item_discount * taxable_before_overall / line_total, 4)
                gst_share = round(item_discount - taxable_share, 4)
            else:
                taxable_share = gst_share = 0.0
            taxable = round(max(0.0, taxable_before_overall - taxable_share), 4)
            gst_amt = round(max(0.0, gst_before - gst_share), 4)
        else:
            if gross_subtotal > 0 and taxable_before_overall > 0:
                if row is last_row:
                    item_discount = remaining_discount
                else:
                    item_discount = round(
                        discount_amount * taxable_before_overall / gross_subtotal, 4)
                    remaining_discount = round(remaining_discount - item_discount, 4)
            else:
                item_discount = 0.0
            taxable = round(max(0.0, taxable_before_overall - item_discount), 4)
            gst_amt = round(taxable * gst_pct / 100, 4)

        total_gst += round(gst_amt, 2)

        item['taxable'] = round(taxable, 2)
        item['gst_amt'] = round(gst_amt, 2)
        item['amount'] = round(taxable + gst_amt, 2)

    subtotal_no_gst = round(
        sum(item['taxable'] for item in items), 2
    ) if after_gst else round(gross_subtotal - discount_amount, 2)
    total_amount = round(subtotal_no_gst + total_gst + rounding, 2)
    cgst = sgst = round(total_gst / 2, 2)

    return {
        'subtotal_no_gst': round(subtotal_no_gst, 2),
        'total_gst': round(total_gst, 2),
        'cgst': cgst,
        'sgst': sgst,
        'discount_amount': round(discount_amount, 2),
        'total_amount': total_amount,
    }


# ── Return calculations ───────────────────────────────────────────────────────

def calc_return_refund(items: list, discount_pct: float = 0) -> dict:
    """
    Compute refund total for a sales or purchase return.

    For sales returns each item dict should carry:
      'qty'    — units being returned
      'rate'   — sales_items.rate  (pre-GST per-unit rate)
      'amount' — sales_items.amount (what customer actually paid for the full qty,
                 i.e. qty*rate - item_discount_rs).  Optional but preferred.

    When 'amount' is present the effective per-unit price is amount/orig_qty so
    the refund correctly reflects the discount the customer received.
    When only 'rate' is present (purchase returns) qty*rate is used.

    Returns: subtotal, discount_amount, refund_amount
    """
    subtotal = 0.0
    for i in items:
        qty  = float(i['qty'])
        rate = float(i['rate'])
        # Use effective_rate = amount / orig_qty when available (sales returns)
        if i.get('amount') and i.get('orig_qty') and float(i['orig_qty']) > 0:
            effective_rate = float(i['amount']) / float(i['orig_qty'])
        else:
            effective_rate = rate
        subtotal += qty * effective_rate
    subtotal = round(subtotal, 2)
    discount_amount = round(subtotal * discount_pct / 100, 2)
    refund_amount   = round(subtotal - discount_amount, 2)
    return {
        'subtotal':        subtotal,
        'discount_amount': discount_amount,
        'refund_amount':   refund_amount,
    }


def calc_purchase_payment(total_amount: float, amount_paid: float,
                          previous_due: float = 0,
                          previous_credit: float = 0) -> dict:
    """
    Compute purchase payment outcome.

    Bill credit is overpayment beyond (total + previous_due) only.
    previous_credit reduces due / need_to_pay, never becomes bill credit
    when Amount Paid equals Total.

    Returns: net_amount, due_amount, credit_amount, total_due
    """
    eps = 0.01
    total = round(_money(total_amount), 2)
    paid = round(_money(amount_paid), 2)
    prev_due = round(_money(previous_due), 2)
    prev_credit = round(_money(previous_credit), 2)
    net_amount = round(total + prev_due - prev_credit, 2)
    due_amount = round(max(0.0, net_amount - paid), 2)
    if due_amount < eps:
        due_amount = 0.0
    overpay = round(paid - (total + prev_due), 2)
    credit_amount = overpay if overpay > eps else 0.0
    total_due = due_amount

    return {
        'net_amount': net_amount,
        'due_amount': due_amount,
        'credit_amount': credit_amount,
        'total_due': total_due,
    }
