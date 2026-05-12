"""
core/purchase_calculator.py
───────────────────────────
Single source of truth for ALL purchase calculations.
Used by: purchase.py, purchase_history.py (edit),
         import_purchases.py, import_from_mobile.py,
         web purchase entry (via import_purchases).

No UI code here. No DB code here. Pure calculation only.
"""


class PurchaseCalculator:
    """
    Input
    -----
    items          : list of dicts, each with:
                       qty            (strips for tablet/bolus, units for others)
                       rate           (per strip or per unit — purchase rate)
                       discount_pct   (item-level discount %)
                       gst_pct        (GST %)
                       free_qty       (free strips/units, default 0)
    overall_discount : rupee amount deducted from the bill total (default 0)
    rounding         : manual rounding adjustment (default 0)
    previous_due     : outstanding due from previous purchases (default 0)
    previous_credit  : credit balance from previous overpayments (default 0)
    amount_paid      : amount paid now (default 0)

    Output  (all keys present in the returned dict)
    ------
    Per-item (mutates each item dict, adds computed keys):
        base           = qty × rate
        discount_amt   = base × discount_pct / 100
        taxable        = base − discount_amt
        gst_amt        = taxable × gst_pct / 100
        item_amount    = taxable + gst_amt

    Summary:
        subtotal       = Σ taxable
        total_gst      = Σ gst_amt
        cgst           = total_gst / 2
        sgst           = total_gst / 2
        total_amount   = subtotal + total_gst

    Payment:
        need_to_pay    = total_amount + previous_due − previous_credit
        final_amount   = need_to_pay − overall_discount + rounding
        due            = max(0, final_amount − amount_paid)
        current_credit = max(0, amount_paid − final_amount)
        total_due      = due
        bill_cleared   = 1 if due == 0 else 0
        account_cleared= 1 if total_due == 0 else 0
    """

    def __init__(
        self,
        items: list,
        overall_discount: float = 0.0,
        rounding: float = 0.0,
        previous_due: float = 0.0,
        previous_credit: float = 0.0,
        amount_paid: float = 0.0,
    ):
        self.items            = items
        self.overall_discount = round(float(overall_discount or 0), 2)
        self.rounding         = round(float(rounding or 0), 2)
        self.previous_due     = round(float(previous_due or 0), 2)
        self.previous_credit  = round(float(previous_credit or 0), 2)
        self.amount_paid      = round(float(amount_paid or 0), 2)

    # ── public API ────────────────────────────────────────────────────────

    def calculate(self) -> dict:
        """Run all calculations and return a single result dict."""
        self._calc_items()
        summary = self._calc_summary()
        payment = self._calc_payment(summary['total_amount'])
        # Include all inputs so save_purchase can access them directly
        inputs = {
            'overall_discount': self.overall_discount,
            'rounding':         self.rounding,
            'previous_due':     self.previous_due,
            'previous_credit':  self.previous_credit,
            'amount_paid':      self.amount_paid,
        }
        return {**summary, **payment, **inputs, 'items': self.items}

    # ── item-level ────────────────────────────────────────────────────────

    def _calc_items(self):
        for item in self.items:
            qty          = float(item.get('qty', 0) or 0)
            rate         = float(item.get('rate', 0) or 0)
            disc_pct     = float(item.get('discount_pct',
                                  item.get('item_discount', 0)) or 0)
            gst_pct      = float(item.get('gst_pct',
                                  item.get('gst_value', 0)) or 0)

            base         = round(qty * rate, 4)
            discount_amt = round(base * disc_pct / 100, 4)
            taxable      = round(base - discount_amt, 4)
            gst_amt      = round(taxable * gst_pct / 100, 4)
            item_amount  = round(taxable + gst_amt, 2)

            item['base']         = round(base, 2)
            item['discount_amt'] = round(discount_amt, 2)
            item['taxable']      = round(taxable, 2)
            item['gst_amt']      = round(gst_amt, 2)
            item['item_amount']  = item_amount
            # keep legacy key 'amount' in sync so existing save code works
            item['amount']       = item_amount

    # ── summary ───────────────────────────────────────────────────────────

    def _calc_summary(self) -> dict:
        subtotal   = round(sum(i.get('taxable', 0) for i in self.items), 2)
        total_gst  = round(sum(i.get('gst_amt', 0) for i in self.items), 2)
        cgst       = round(total_gst / 2, 2)
        sgst       = round(total_gst / 2, 2)
        total_amount = round(subtotal + total_gst, 2)
        return {
            'subtotal':     subtotal,
            'total_gst':    total_gst,
            'cgst':         cgst,
            'sgst':         sgst,
            'total_amount': total_amount,
        }

    # ── payment ───────────────────────────────────────────────────────────

    def _calc_payment(self, total_amount: float) -> dict:
        need_to_pay    = round(total_amount + self.previous_due
                               - self.previous_credit, 2)
        final_amount   = round(need_to_pay - self.overall_discount
                               + self.rounding, 2)
        due            = round(max(0.0, final_amount - self.amount_paid), 2)
        current_credit = round(max(0.0, self.amount_paid - final_amount), 2)
        # `final_amount` already includes previous_due and previous_credit, so
        # adding previous_due here would double-count the old balance.
        total_due      = due
        bill_cleared   = 1 if due == 0 else 0
        account_cleared = 1 if total_due == 0 else 0
        return {
            'need_to_pay':     need_to_pay,
            'final_amount':    final_amount,
            'due':             due,
            'current_credit':  current_credit,
            'total_due':       total_due,
            'bill_cleared':    bill_cleared,
            'account_cleared': account_cleared,
            # legacy aliases kept so existing DB save code works unchanged
            'due_amount':      due,
            'credit_amount':   current_credit,
        }
