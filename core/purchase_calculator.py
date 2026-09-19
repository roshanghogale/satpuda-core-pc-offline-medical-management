"""
core/purchase_calculator.py
───────────────────────────
Single entry point for ALL purchase calculations (save, edit, import, web).

Delegates to core.pharmacy_purchase_calc.compute_purchase_invoice:
  Step 1 — Line evaluation (qty × rate − item discount)
  Step 2 — GST slab grouping + proportional bill discount
  Step 3 — Tax extraction matrix (Mode A inclusive | Mode B exclusive)
  Step 4 — Matrix verification + payment totals

gst_calc_method mapping:
  discount_after_gst  → Mode A (rates include GST — Swami Samarth style)
  discount_before_gst → Mode B (rates exclude GST — Jai Ganesh style)
"""

from core.calc_engine import auto_round


class PurchaseCalculator:
    """
    Input
    -----
    items            : list of dicts (qty, rate, discount_pct, gst_pct, …)
    overall_discount : rupee bill-level discount (default 0)
    gst_calc_method  : 'discount_before_gst' | 'discount_after_gst'
    rounding         : manual rounding adjustment (default 0)
    previous_due / previous_credit / cash_paid / online_paid : payment fields

    Output
    ------
    gross_subtotal   : Σ (qty × rate − item discount) before bill discount
    subtotal         : taxable after bill discount (matches bill SUB TOTAL)
    total_gst/cgst/sgst : from GST slab table
    total_amount     : subtotal + GST + rounding
    items            : per-line taxable / gst_amt after slab allocation
    """

    def __init__(
        self,
        items: list,
        overall_discount: float = 0.0,
        rounding: float = 0.0,
        previous_due: float = 0.0,
        previous_credit: float = 0.0,
        cash_paid: float = 0.0,
        online_paid: float = 0.0,
        amount_paid: float | None = None,
        expenditure: float = 0.0,
        gst_calc_method: str = "discount_before_gst",
    ):
        self.items            = items
        self.overall_discount = round(float(overall_discount or 0), 2)
        self.rounding         = round(float(rounding or 0), 2)
        self.previous_due     = round(float(previous_due or 0), 2)
        self.previous_credit  = round(float(previous_credit or 0), 2)
        self.cash_paid        = round(float(cash_paid or 0), 2)
        self.online_paid      = round(float(online_paid or 0), 2)
        if amount_paid is not None:
            self.amount_paid = round(float(amount_paid or 0), 2)
        else:
            self.amount_paid = round(self.cash_paid + self.online_paid, 2)
        self.expenditure      = round(float(expenditure or 0), 2)
        method = (gst_calc_method or "discount_before_gst").strip()
        self.gst_calc_method = (
            method if method in ("discount_before_gst", "discount_after_gst")
            else "discount_before_gst"
        )

    @staticmethod
    def normalize_items(items: list) -> None:
        """Clear stale line totals so slab GST is always computed from qty/rate/GST."""
        for item in items:
            if item.get('_preserve_line_totals'):
                gst = float(item.get('gst_pct', item.get('gst_value', 0)) or 0)
                item['gst_pct'] = gst
                item['discount_pct'] = float(
                    item.get('discount_pct', item.get('item_discount', 0)) or 0
                )
                continue
            for key in (
                'import_lock_values', 'import_taxable', 'import_gst_amt',
                'import_item_amount', 'taxable', 'gst_amt', 'cgst_amt', 'sgst_amt',
                'overall_discount_amt', 'cash_disc_share', '_goods_amount',
                '_taxable_before_overall', '_gst_before_overall', '_gst_pct_for_calc',
                'base', 'discount_amt',
            ):
                item.pop(key, None)
            gst = float(item.get('gst_pct', item.get('gst_value', 0)) or 0)
            item['gst_pct'] = gst
            item['discount_pct'] = float(
                item.get('discount_pct', item.get('item_discount', 0)) or 0
            )
            base = float(item.get('qty', 0) or 0) * float(item.get('rate', 0) or 0)
            stored = float(item.get('item_amount', item.get('amount', 0)) or 0)
            if stored and base and abs(stored - base) > max(0.05, base * 0.02):
                item.pop('amount', None)
                item.pop('item_amount', None)

    def calculate(self) -> dict:
        from core.pharmacy_purchase_calc import compute_purchase_invoice, gst_method_to_tax_mode

        self.normalize_items(self.items)

        bill_inclusive = gst_method_to_tax_mode(self.gst_calc_method) == "inclusive"
        prep = []
        for item in self.items:
            row = dict(item)
            if "is_tax_inclusive" not in row:
                row["is_tax_inclusive"] = bill_inclusive
            prep.append(row)

        rounding = self.rounding
        calc = compute_purchase_invoice(
            items=prep,
            global_cash_discount=self.overall_discount,
            product_discount=0.0,
            round_off=rounding if rounding else None,
            net_payable=None,
            gst_calc_method=self.gst_calc_method,
        )

        if not rounding:
            rounding = auto_round(float(calc.get('pre_round_total', 0) or 0))
            if rounding:
                calc = compute_purchase_invoice(
                    items=prep,
                    global_cash_discount=self.overall_discount,
                    product_discount=0.0,
                    round_off=rounding,
                    net_payable=None,
                    gst_calc_method=self.gst_calc_method,
                )

        worked = calc.get('items') or prep
        for orig, new in zip(self.items, worked):
            for key in (
                'taxable', 'gst_amt', 'cgst_amt', 'sgst_amt', 'item_amount', 'amount',
                'overall_discount_amt', 'discount_amt', 'base', '_goods_amount',
                '_taxable_before_overall', 'cash_disc_share',
            ):
                if key in new:
                    orig[key] = new[key]

        summary = {
            'gross_subtotal':   calc['gross_total'],
            'subtotal':         calc['taxable_total'],
            'total_gst':        calc['total_gst'],
            'cgst':             calc['cgst'],
            'sgst':             calc['sgst'],
            'discount_amount':  calc['discount_amount'],
            'pre_round_total':  calc['pre_round_total'],
            'total_amount':     calc['total_amount'],
            'rounding':         calc['rounding'],
            'slab_breakdown':   calc.get('slab_breakdown') or [],
            'tax_mode':         calc.get('tax_mode'),
            'validation':       calc.get('validation') or {},
        }
        payment = self._calc_payment(summary['total_amount'])
        inputs = {
            'overall_discount': self.overall_discount,
            'rounding':         calc['rounding'],
            'expenditure':      self.expenditure,
            'previous_due':     self.previous_due,
            'previous_credit':  self.previous_credit,
            'cash_paid':        self.cash_paid,
            'online_paid':      self.online_paid,
            'amount_paid':      self.amount_paid,
            'gst_calc_method':  self.gst_calc_method,
        }
        return {**summary, **payment, **inputs, 'items': self.items}

    def _calc_payment(self, bill_total: float) -> dict:
        """Payment outcome for a purchase bill.

        need_to_pay / due use previous credit (reduces what to collect now).
        current_credit is overpayment beyond (bill + previous due) only —
        leftover previous_credit must NOT appear as this bill's credit when
        Amount Paid equals Total.
        """
        eps = 0.01
        final_amount = round(bill_total + self.expenditure, 2)
        need_to_pay = round(
            final_amount + self.previous_due - self.previous_credit, 2,
        )
        due = round(max(0.0, need_to_pay - self.amount_paid), 2)
        if due < eps:
            due = 0.0
        # Overpay past bill + previous due only (ignore previous_credit here).
        overpay = round(
            self.amount_paid - (final_amount + self.previous_due), 2,
        )
        current_credit = overpay if overpay > eps else 0.0
        total_due = due
        bill_cleared = 1 if due < eps else 0
        account_cleared = 1 if total_due < eps else 0
        return {
            'need_to_pay':     need_to_pay,
            'final_amount':    final_amount,
            'due':             due,
            'current_credit':  current_credit,
            'total_due':       total_due,
            'bill_cleared':    bill_cleared,
            'account_cleared': account_cleared,
            'due_amount':      due,
            'credit_amount':   current_credit,
        }


def _calc_header_match_score(
    items: list,
    target_cgst: float,
    target_total: float,
    overall_discount: float = 0.0,
    gst_calc_method: str = "discount_before_gst",
) -> tuple:
    calc = PurchaseCalculator(
        items=items,
        overall_discount=overall_discount,
        rounding=0,
        gst_calc_method=gst_calc_method,
    ).calculate()
    cgst_err = abs(float(calc.get('cgst') or 0) - float(target_cgst or 0))
    total_err = abs(float(calc.get('total_amount') or 0) - float(target_total or 0))
    return cgst_err + total_err * 0.05, calc


def reconcile_items_gst_with_header(
    items: list,
    target_cgst: float,
    target_total: float,
    overall_discount: float = 0.0,
    gst_calc_method: str = "discount_before_gst",
) -> list:
    """
    Fix stale per-line gst_pct when DB lines disagree with saved purchase header.

    Old bills often have correct cgst/total on purchases but wrong gst_pct on lines
    (e.g. exempt item saved as 5%). Tries 0% GST on lines that match stored header.
    """
    if not items:
        return items
    target_cgst = float(target_cgst or 0)
    target_total = float(target_total or 0)
    if target_cgst <= 0 and target_total <= 0:
        return [dict(i) for i in items]

    working = [dict(i) for i in items]
    err, _ = _calc_header_match_score(
        working, target_cgst, target_total, overall_discount, gst_calc_method,
    )
    if err < 0.06:
        return working

    # Lines with no GST in stored amount → 0% GST
    for item in working:
        qty = float(item.get('qty') or 0)
        rate = float(item.get('rate') or 0)
        disc = float(item.get('discount_pct', item.get('item_discount', 0)) or 0)
        base = round(qty * rate * (1 - disc / 100), 2)
        stored_gst = float(item.get('gst_amt') or 0)
        stored_amt = float(item.get('item_amount') or item.get('amount') or 0)
        if stored_gst <= 0.001 and base > 0 and stored_amt > 0:
            if abs(stored_amt - base) <= max(0.05, base * 0.01):
                item['gst_pct'] = 0.0
    err, _ = _calc_header_match_score(
        working, target_cgst, target_total, overall_discount, gst_calc_method,
    )
    if err < 0.06:
        return working

    best = working
    best_err = err
    for i, item in enumerate(working):
        if float(item.get('gst_pct') or 0) <= 0:
            continue
        trial = [dict(x) for x in working]
        trial[i] = dict(trial[i], gst_pct=0.0)
        trial_err, _ = _calc_header_match_score(
            trial, target_cgst, target_total, overall_discount, gst_calc_method,
        )
        if trial_err < best_err:
            best_err = trial_err
            best = trial
    if best_err < err:
        return best

    return working
