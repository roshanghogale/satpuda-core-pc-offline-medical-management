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
    overall_discount : rupee amount deducted from taxable value before GST
                       (default 0)
    rounding         : manual rounding adjustment (default 0)
    previous_due     : outstanding due from previous purchases (default 0)
    previous_credit  : credit balance from previous overpayments (default 0)
    amount_paid      : amount paid now (default 0)

    Output  (all keys present in the returned dict)
    ------
    Per-item (mutates each item dict, adds computed keys):
        base           = qty × rate
        discount_amt   = base × discount_pct / 100
        taxable        = base minus item discount minus proportional overall_discount
        gst_amt        = taxable x gst_pct / 100
        item_amount    = taxable + gst_amt

    Summary:
        subtotal       = Σ taxable
        total_gst      = Σ gst_amt
        cgst           = total_gst / 2
        sgst           = total_gst / 2
        total_amount   = subtotal + total_gst + rounding

    Payment:
        need_to_pay    = total_amount + previous_due − previous_credit
        final_amount   = total_amount
        due            = max(0, need_to_pay - amount_paid)
        current_credit = max(0, amount_paid - need_to_pay)
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
            if item.get('import_lock_values'):
                taxable = round(float(item.get('import_taxable', item.get('qty', 0) * item.get('rate', 0)) or 0), 4)
                gst_amt = round(float(item.get('import_gst_amt', taxable * float(item.get('gst_pct', 0) or 0) / 100) or 0), 4)
                item_amount = round(float(item.get('import_item_amount', taxable + gst_amt) or 0), 2)

                item['base'] = round(taxable, 2)
                item['discount_amt'] = 0.0
                item['_taxable_before_overall'] = taxable
                item['_gst_pct_for_calc'] = float(item.get('gst_pct', item.get('gst_value', 0)) or 0)
                item['overall_discount_amt'] = 0.0
                item['taxable'] = round(taxable, 2)
                item['gst_amt'] = round(gst_amt, 2)
                item['item_amount'] = item_amount
                item['amount'] = item_amount
                continue

            qty          = float(item.get('qty', 0) or 0)
            rate         = float(item.get('rate', 0) or 0)
            disc_pct     = float(item.get('discount_pct',
                                  item.get('item_discount', 0)) or 0)
            gst_pct      = float(item.get('gst_pct',
                                  item.get('gst_value', 0)) or 0)

            base         = round(qty * rate, 4)
            discount_amt = round(base * disc_pct / 100, 4)
            taxable      = round(base - discount_amt, 4)

            item['base']         = round(base, 2)
            item['discount_amt'] = round(discount_amt, 2)
            item['_taxable_before_overall'] = taxable
            item['_gst_pct_for_calc'] = gst_pct
            item['overall_discount_amt'] = 0.0
            item['taxable']      = round(taxable, 2)
            item['gst_amt']      = round(taxable * gst_pct / 100, 2)
            item['item_amount']  = round(taxable + item['gst_amt'], 2)
            # keep legacy key 'amount' in sync so existing save code works
            item['amount']       = item['item_amount']

    # ── summary ───────────────────────────────────────────────────────────

    def _calc_summary(self) -> dict:
        gross_subtotal = round(
            sum(i.get('_taxable_before_overall', i.get('taxable', 0)) for i in self.items),
            4,
        )
        has_locked_imports = any(bool(i.get('import_lock_values')) for i in self.items)
        discount_amount = 0.0 if has_locked_imports else round(
            min(max(self.overall_discount, 0.0), gross_subtotal),
            4,
        )
        remaining_discount = discount_amount
        taxable_items = [
            i for i in self.items
            if float(i.get('_taxable_before_overall', i.get('taxable', 0)) or 0) > 0
        ]
        last_taxable = taxable_items[-1] if taxable_items else None

        for item in self.items:
            taxable_before = float(
                item.get('_taxable_before_overall', item.get('taxable', 0)) or 0
            )
            if gross_subtotal > 0 and taxable_before > 0:
                if item is last_taxable:
                    overall_disc = remaining_discount
                else:
                    overall_disc = round(discount_amount * taxable_before / gross_subtotal, 4)
                    remaining_discount = round(remaining_discount - overall_disc, 4)
            else:
                overall_disc = 0.0

            taxable = round(max(0.0, taxable_before - overall_disc), 4)
            gst_pct = float(item.get('_gst_pct_for_calc', item.get('gst_pct', 0)) or 0)
            gst_amt = round(taxable * gst_pct / 100, 4)
            item_amount = round(taxable + gst_amt, 2)

            item['overall_discount_amt'] = round(overall_disc, 2)
            item['taxable'] = round(taxable, 2)
            item['gst_amt'] = round(gst_amt, 2)
            item['item_amount'] = item_amount
            item['amount'] = item_amount

        subtotal   = round(gross_subtotal - discount_amount, 2)
        total_gst  = round(sum(i.get('gst_amt', 0) for i in self.items), 2)
        cgst       = round(total_gst / 2, 2)
        sgst       = round(total_gst / 2, 2)
        pre_round_total = round(subtotal + total_gst, 2)
        total_amount = round(pre_round_total + self.rounding, 2)
        return {
            'gross_subtotal':   round(gross_subtotal, 2),
            'subtotal':         subtotal,
            'total_gst':        total_gst,
            'cgst':             cgst,
            'sgst':             sgst,
            'discount_amount':  round(discount_amount, 2),
            'pre_round_total':  pre_round_total,
            'total_amount':     total_amount,
        }

    # ── payment ───────────────────────────────────────────────────────────

    def _calc_payment(self, total_amount: float) -> dict:
        need_to_pay    = round(total_amount + self.previous_due
                               - self.previous_credit, 2)
        final_amount   = total_amount
        due            = round(max(0.0, need_to_pay - self.amount_paid), 2)
        current_credit = round(max(0.0, self.amount_paid - need_to_pay), 2)
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
