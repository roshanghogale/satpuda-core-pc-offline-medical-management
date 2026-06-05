"""
Centralized Calculation Engine
All monetary calculations for billing, purchase, and bill-edit pages live here.
"""
import math


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
    subtotal = round(sum(i['amount'] for i in items), 2)
    if discount_rs is not None:
        discount_amount = round(min(discount_rs, subtotal), 2)
        actual_pct = round(discount_amount / subtotal * 100, 4) if subtotal > 0 else 0.0
    else:
        discount_amount = round(subtotal * discount_pct / 100, 2)
        actual_pct = discount_pct
    pre_round = round(subtotal - discount_amount, 2)
    total_amount = round(pre_round + rounding, 2)
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
    ERP-grade payment outcome.

    need_to_pay = total_amount + previous_due - previous_credit
    due         = max(0, need_to_pay - amount_paid)
    credit      = max(0, amount_paid - need_to_pay)

    Returns: amount_paid, due_amount, credit_amount, need_to_pay
    """
    amount_paid  = round(cash_paid + online_paid, 2)
    need_to_pay  = round(total_amount + previous_due - previous_credit, 2)
    raw_balance  = round(need_to_pay - amount_paid, 2)

    due_amount    = round(max(0.0, raw_balance), 2)
    credit_amount = round(max(0.0, -raw_balance), 2)

    return {
        'amount_paid':   amount_paid,
        'due_amount':    due_amount,
        'credit_amount': credit_amount,
        'need_to_pay':   need_to_pay,
        # kept for UI display compat
        'current_bill_due':       due_amount,
        'remaining_previous_due': 0.0,
        'total_due':              due_amount,
    }


# ── Purchase summary ──────────────────────────────────────────────────────────

def calc_purchase_summary(items: list, overall_discount: float = 0,
                          rounding: float = 0,
                          gst_method: str = "discount_before_gst") -> dict:
    """
    Compute purchase page totals.

    items: list of dicts with 'qty', 'rate', 'gst_value', 'item_discount' keys.
    Returns: subtotal_no_gst, total_gst, cgst, sgst, discount_amount,
             total_amount (after overall discount + rounding)
    """
    gross_subtotal = 0.0
    prepared = []

    for item in items:
        qty = float(item.get('qty', 0))
        rate = float(item.get('rate', 0))
        gst_pct = float(item.get('gst_pct', item.get('gst_value', 0)))
        item_disc = float(item.get('discount_pct', item.get('item_discount', 0)))

        base = qty * rate
        taxable_before_overall = base * (1 - item_disc / 100)
        gross_subtotal += taxable_before_overall
        prepared.append((item, taxable_before_overall, gst_pct))

    discount_amount = round(min(max(float(overall_discount or 0), 0.0), gross_subtotal), 4)
    remaining_discount = discount_amount
    taxable_rows = [row for row in prepared if row[1] > 0]
    last_taxable = taxable_rows[-1] if taxable_rows else None
    total_gst = 0.0

    for row in prepared:
        item, taxable_before_overall, gst_pct = row
        if gross_subtotal > 0 and taxable_before_overall > 0:
            if row is last_taxable:
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

    subtotal_no_gst = round(gross_subtotal - discount_amount, 2)
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

    Returns: net_amount, due_amount, credit_amount, total_due
    """
    net_amount = round(total_amount + previous_due - previous_credit, 2)
    total_required = total_amount + previous_due
    final = round(total_required - amount_paid, 2)

    if final > 0:
        due_amount = round(max(0.0, total_amount - amount_paid), 2)
        credit_amount = 0.0
        total_due = final
    elif final < 0:
        due_amount = 0.0
        credit_amount = round(abs(final), 2)
        total_due = 0.0
    else:
        due_amount = credit_amount = total_due = 0.0

    return {
        'net_amount': net_amount,
        'due_amount': round(due_amount, 2),
        'credit_amount': credit_amount,
        'total_due': round(total_due, 2),
    }
