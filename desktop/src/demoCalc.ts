/**
 * The billing arithmetic, in the demo.
 *
 * A recorded answer cannot do this job. `POST /api/sales/calc` takes the bill
 * the prospect is building — quantities and discounts they are typing right
 * now — so a canned reply leaves BILLING SUMMARY frozen at ₹0.00 whatever they
 * do, which is what the demo used to show. The money panel is the one thing a
 * pharmacy owner watches.
 *
 * So this is a port of core/calc_engine.calc_bill_summary and
 * calc_payment_result, plus core/desktop_sales_service._sale_rounding_amount.
 * It is NOT an approximation written from the description: it is checked
 * against 360 answers recorded from the real engine over a randomised grid of
 * bills (see tests/test_demo_calc_matches_the_engine.py), and every one of them
 * has to match to the paisa or the test fails. If the engine's rules change,
 * that test is what says so.
 *
 * Only reachable in the demo build; the shipped app calls the engine.
 */

export type DemoCalcItem = Record<string, unknown>

/** core.calc_engine._money — never throws, blank is zero, "₹1,200" is 1200. */
function money(v: unknown, dflt = 0): number {
  if (v === null || v === undefined || v === '') return dflt
  if (typeof v === 'boolean') return v ? 1 : 0
  if (typeof v === 'number') return Number.isNaN(v) ? dflt : v
  const s = String(v).trim().replace(/,/g, '').replace(/₹/g, '').trim()
  if (!s) return dflt
  const f = Number(s)
  return Number.isNaN(f) ? dflt : f
}

/**
 * Python's round(x, n) — round-half-to-EVEN on the double's EXACT value.
 *
 * Two traps, and this bill total shows both. JavaScript's toFixed(2) agrees
 * with Python almost everywhere, because both round on the exact binary value:
 * 1.075 is really 1.07499999999999995559, so both give 1.07, and 0.525 is
 * really 0.52500000000000002220, so both give 0.53. Where they part is an
 * EXACT tie — 0.125 is exactly representable, and there Python goes to the
 * even neighbour (0.12) while toFixed goes away from zero (0.13).
 *
 * So: read the exact decimal expansion, and only when the tail is exactly a 5
 * followed by nothing but zeros do we break the tie ourselves. Multiplying by
 * 100 first is what got this wrong: 0.525 * 100 is exactly 52.5 in binary, so
 * the scaled value LOOKS like a tie when the real number is not one.
 */
function roundHalfEven(x: number, dp: number): number {
  if (!Number.isFinite(x)) return 0
  if (Math.abs(x) >= 1e15) return x
  const neg = x < 0
  const a = Math.abs(x)
  const exact = a.toFixed(20)
  const dot = exact.indexOf('.')
  const tail = exact.slice(dot + 1 + dp)
  let out: number
  if (/^50*$/.test(tail)) {
    const head = Number(exact.slice(0, dot + 1 + dp) || '0')
    const step = Math.pow(10, -dp)
    const lower = Number(head.toFixed(dp))
    const asInt = Math.round(lower * Math.pow(10, dp))
    out = asInt % 2 === 0 ? lower : Number((lower + step).toFixed(dp))
  } else {
    out = Number(a.toFixed(dp))
  }
  return neg ? -out : out
}

function round2(x: number): number {
  return roundHalfEven(x, 2)
}

/** core.calc_engine.auto_round — the adjustment to the nearest whole rupee. */
function autoRound(amount: number): number {
  return round2(Math.floor(amount + 0.5) - amount)
}

export type DemoBillSummary = {
  subtotal: number
  discount_amount: number
  discount_pct: number
  pre_round_total: number
  total_amount: number
}

/** core.calc_engine.calc_bill_summary */
function billSummary(
  items: DemoCalcItem[],
  discountPct: number,
  rounding: number,
  discountRs: number | null,
): DemoBillSummary {
  const subtotal = round2(items.reduce((a, i) => a + money(i.amount), 0))
  let discountAmount: number
  let actualPct: number
  if (discountRs !== null) {
    discountAmount = round2(Math.min(money(discountRs), subtotal))
    actualPct = subtotal > 0 ? round4((discountAmount / subtotal) * 100) : 0
  } else {
    discountAmount = round2((subtotal * money(discountPct)) / 100)
    actualPct = money(discountPct)
  }
  const preRound = round2(subtotal - discountAmount)
  return {
    subtotal,
    discount_amount: discountAmount,
    discount_pct: actualPct,
    pre_round_total: preRound,
    total_amount: round2(preRound + money(rounding)),
  }
}

function round4(x: number): number {
  return roundHalfEven(x, 4)
}

/** core.calc_engine.calc_payment_result */
function paymentResult(
  totalAmount: number,
  cashPaid: number,
  onlinePaid: number,
  previousDue: number,
  previousCredit: number,
) {
  const eps = 0.01
  const amountPaid = round2(money(cashPaid) + money(onlinePaid))
  const prevDue = round2(money(previousDue))
  const prevCredit = round2(money(previousCredit))
  const prevNet = round2(Math.max(0, prevDue - prevCredit))
  const total = round2(money(totalAmount))

  let dueAmount = round2(Math.max(0, total - amountPaid))
  if (dueAmount < eps) dueAmount = 0

  let combined = round2(prevNet + total - amountPaid)
  if (Math.abs(combined) < eps) combined = 0
  const totalDue = round2(Math.max(0, combined))

  const overpay = round2(amountPaid - (total + prevDue))
  const creditAmount = overpay > eps ? overpay : 0

  // The credit-aware figures. New keys only: every field below is left exactly
  // as it was, because calc_payment_result is a WRITE path as well as a preview
  // and sales.due_amount is read back by the exports, the due reminders and the
  // home dashboard. See the comment in core/calc_engine.py.
  let prevBalance = round2(prevDue - prevCredit)
  if (Math.abs(prevBalance) < eps) prevBalance = 0
  const spareCredit = round2(Math.max(0, -prevBalance))
  const unpaid = round2(Math.max(0, total - amountPaid))
  let creditApplied = round2(Math.min(spareCredit, unpaid))
  if (creditApplied < eps) creditApplied = 0
  let netTotalDue = round2(Math.max(0, prevBalance + total - amountPaid))
  if (netTotalDue < eps) netTotalDue = 0

  return {
    amount_paid: amountPaid,
    due_amount: dueAmount,
    credit_amount: creditAmount,
    need_to_pay: round2(prevNet + total),
    current_bill_due: dueAmount,
    remaining_previous_due: round2(
      Math.max(0, prevNet - Math.max(0, amountPaid - total)),
    ),
    total_due: totalDue,
    credit_applied: creditApplied,
    net_total_due: netTotalDue,
  }
}

/** core.desktop_sales_service.calc_sale, without the party-due lookup the
 *  live preview already skips (the page sends previous_due itself). */
export function demoSalesCalc(body: Record<string, unknown>): Record<string, unknown> {
  const rawItems = Array.isArray(body.items) ? (body.items as DemoCalcItem[]) : []
  const items = rawItems
    .filter((it) => it && typeof it === 'object')
    .map((it) => ({
      id: it.id,
      amount: money(it.amount),
      medicine_discount: money(
        it.medicine_discount !== undefined ? it.medicine_discount : it.disc,
      ),
      qty: money(it.qty),
      rate: money(it.rate),
      original_amount: money(
        it.original_amount !== undefined
          ? it.original_amount
          : money(it.qty) * money(it.rate),
      ),
    }))

  const discPct = money(body.discount_pct)
  const discRsRaw = body.discount_rs
  const discRs =
    discRsRaw === null || discRsRaw === undefined || discRsRaw === ''
      ? null
      : money(discRsRaw)

  const auto = body.auto_rounding === undefined ? true : Boolean(body.auto_rounding)
  const rounding = auto
    ? round2(autoRound(billSummary(items, discPct, 0, discRs).pre_round_total))
    : round2(money(body.rounding))

  const summary = billSummary(items, discPct, rounding, discRs)

  const isDue = String(body.payment_mode || 'Cash').trim().toLowerCase() === 'due'
  const cash = isDue ? 0 : money(body.cash_paid)
  const online = isDue ? 0 : money(body.online_paid)
  const prevDue = money(body.previous_due)
  const prevCredit = money(body.previous_credit)

  return {
    ok: true,
    summary,
    payment: paymentResult(summary.total_amount, cash, online, prevDue, prevCredit),
    cash_paid: cash,
    online_paid: online,
    previous_due: prevDue,
    previous_credit: prevCredit,
    rounding,
    is_due: isDue,
    warnings: [],
    gst_label: 'Included in MRP',
  }
}
