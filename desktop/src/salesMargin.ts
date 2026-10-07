/**
 * Classic's sales margin, to the paisa: a port of core/margin_utils.py.
 *
 * The sales screen used to show the margin the engine worked out when a line
 * was built and then only add those figures up. So a discount changed after
 * that -- a merged line, an edited line, the OVERALL discount -- never reached
 * the margin, while Classic recomputes it from the line's cost every time the
 * bill repaints. These are Classic's formulas, applied at the same moments.
 *
 *   per-unit divisor  d   = tablets per strip for strip-counted types, else 1
 *                           (margin_div from the engine = resolve_tablets_per_stripe)
 *   line gross        G   = round2(max(0, (list_mrp - purchase_rate) / d * qty))
 *   line net (Rs)     N   = round2(max(0, G - item_discount))
 *   line MRP value    V   = round2(round2(list_mrp / d) * qty)
 *   line margin %         = round2(N / V * 100)
 *   bill margin (Rs)      = round2(max(0, sum G - sum item_discount - overall_discount))
 *   bill margin %         = round2(bill margin / round2(sum V) * 100)
 *
 * GST is not taken out anywhere: MRP includes GST and so does the purchase rate
 * used here (the inventory rate). The overall discount is NOT spread over lines;
 * it comes off the bill total once, in rupees (the % field is first turned into
 * rupees of the subtotal). Rounding never touches the margin.
 */

export type MarginLine = {
  qty?: number | string | null
  list_mrp?: number | string | null
  mrp?: number | string | null
  purchase_rate?: number | string | null
  medicine_discount?: number | string | null
  type?: string | null
  unit?: string | null
  name?: string | null
  /** Pack divisor the engine used (core.margin_utils.margin_unit_divisor). */
  margin_div?: number | string | null
}

/** core.margin_utils._safe_float */
function num(v: unknown, dflt = 0): number {
  if (v === null || v === undefined || v === '') return dflt
  if (typeof v === 'boolean') return v ? 1 : 0
  if (typeof v === 'number') return Number.isFinite(v) ? v : dflt
  const s = String(v).replace(/,/g, '').replace(/₹/g, '').trim()
  if (!s) return dflt
  const f = Number(s)
  return Number.isFinite(f) ? f : dflt
}

/** Python's round(x, 2): half-to-even on the double's exact value (see demoCalc). */
function round2(x: number): number {
  if (!Number.isFinite(x)) return 0
  if (Math.abs(x) >= 1e15) return x
  const neg = x < 0
  const a = Math.abs(x)
  const exact = a.toFixed(20)
  const dot = exact.indexOf('.')
  const tail = exact.slice(dot + 3)
  let out: number
  if (/^50*$/.test(tail)) {
    const lower = Number(Number(exact.slice(0, dot + 3) || '0').toFixed(2))
    const asInt = Math.round(lower * 100)
    out = asInt % 2 === 0 ? lower : Number((lower + 0.01).toFixed(2))
  } else {
    out = Number(a.toFixed(2))
  }
  return neg ? -out : out
}

/** A line whose purchase rate never reached the screen (a resumed draft) has no
 *  known cost. Classic would read it as a zero cost and show the whole MRP as
 *  margin; here it is left out of the margin instead of inventing one. */
export function hasCost(med: MarginLine): boolean {
  const p = med.purchase_rate
  return p !== null && p !== undefined && p !== '' && Number.isFinite(num(p, NaN))
}

export function marginDivisor(
  med: MarginLine,
  fallback?: (med: MarginLine) => number,
): number {
  const d = num(med.margin_div, 0)
  if (d >= 1) return Math.floor(d)
  const f = fallback ? num(fallback(med), 1) : 1
  return f >= 1 ? Math.floor(f) : 1
}

function listMrp(med: MarginLine): number {
  return num(med.list_mrp, num(med.mrp))
}

/** core.margin_utils.line_mrp_value (denominator of margin %). */
export function lineMrpValue(med: MarginLine, fallback?: (m: MarginLine) => number): number {
  const qty = num(med.qty)
  if (qty <= 0) return 0
  const d = marginDivisor(med, fallback)
  const perUnit = d > 1 ? round2(listMrp(med) / d) : round2(listMrp(med))
  return round2(perUnit * qty)
}

/** core.margin_utils.line_gross_margin: (MRP - purchase rate) x qty, before any discount. */
export function lineGrossMargin(med: MarginLine, fallback?: (m: MarginLine) => number): number {
  const qty = num(med.qty)
  if (qty <= 0) return 0
  const d = marginDivisor(med, fallback)
  const spread = listMrp(med) - num(med.purchase_rate)
  const value = d > 1 ? (spread / d) * qty : spread * qty
  return round2(Math.max(0, value))
}

/** core.margin_utils.line_net_margin */
export function lineNetMargin(med: MarginLine, fallback?: (m: MarginLine) => number): number {
  return round2(Math.max(0, lineGrossMargin(med, fallback) - num(med.medicine_discount)))
}

/** core.margin_utils.line_margin_percent */
export function lineMarginPercent(med: MarginLine, fallback?: (m: MarginLine) => number): number {
  const base = lineMrpValue(med, fallback)
  if (base <= 0) return 0
  return round2((lineNetMargin(med, fallback) / base) * 100)
}

/** Line margin as shown in the Margin column, or null when the cost is unknown. */
export function lineMargin(
  med: MarginLine,
  fallback?: (m: MarginLine) => number,
): { rs: number; pct: number } | null {
  if (!hasCost(med)) return null
  return { rs: lineNetMargin(med, fallback), pct: lineMarginPercent(med, fallback) }
}

/** core.margin_utils.total_net_margin / total_margin_percent. */
export function billMargin(
  items: MarginLine[],
  overallDiscount: unknown,
  fallback?: (m: MarginLine) => number,
): { rs: number; pct: number; gross: number } {
  const known = items.filter(hasCost)
  const gross = round2(known.reduce((s, m) => s + lineGrossMargin(m, fallback), 0))
  const itemDisc = known.reduce((s, m) => s + num(m.medicine_discount), 0)
  const rs = round2(Math.max(0, gross - itemDisc - num(overallDiscount)))
  const base = round2(known.reduce((s, m) => s + lineMrpValue(m, fallback), 0))
  const pct = base > 0 ? round2((rs / base) * 100) : 0
  return { rs, pct, gross }
}

/** billing_form._on_disc_pct_change: the overall % in rupees of the subtotal,
 *  round(subtotal * pct / 100, 2) with Python's rounding. Math.round(x*100)/100
 *  gave a paisa more on about 1 bill in 17 (1.5% of ₹1 = 0.02, Classic 0.01),
 *  and the engine bills discount_rs, not the %. */
export function overallDiscFromPct(subtotal: unknown, pct: unknown): number {
  return round2((num(subtotal) * num(pct)) / 100)
}

/** core.margin_utils.check_item_discount_loss */
export function itemDiscountLoss(
  med: MarginLine,
  fallback?: (m: MarginLine) => number,
): string | null {
  if (!hasCost(med)) return null
  const gross = lineGrossMargin(med, fallback)
  const disc = num(med.medicine_discount)
  if (disc <= gross + 0.001) return null
  const name = med.name || 'Medicine'
  return (
    `${name}: discount ₹${disc.toFixed(2)} is more than margin ₹${gross.toFixed(2)} ` +
    '(MRP − purchase rate).\nYou are selling below cost rate.'
  )
}

/** core.margin_utils.check_overall_discount_loss */
export function overallDiscountLoss(
  items: MarginLine[],
  overallDiscount: unknown,
  fallback?: (m: MarginLine) => number,
): string | null {
  const known = items.filter(hasCost)
  if (!known.length) return null
  const gross = round2(known.reduce((s, m) => s + lineGrossMargin(m, fallback), 0))
  const itemDisc = known.reduce((s, m) => s + num(m.medicine_discount), 0)
  const remaining = round2(gross - itemDisc)
  const od = num(overallDiscount)
  if (od <= remaining + 0.001) return null
  return (
    `Overall discount ₹${od.toFixed(2)} is more than remaining margin ₹${remaining.toFixed(2)} ` +
    `(total MRP−rate margin ₹${gross.toFixed(2)} minus item discounts).\n` +
    'You are selling below cost rate.'
  )
}

/** core.margin_utils.confirm_discount_loss — the Margin Warning body. */
export function discountLossPrompt(msgs: string[], question = 'Apply this discount anyway?'): string {
  return (
    'Discount is larger than margin — you are selling below purchase rate (loss).\n\n' +
    msgs.join('\n\n') +
    (question ? `\n\n${question}` : '')
  )
}

/** core.margin_utils.validate_bill_discounts, minus the dialog: every message. */
export function billDiscountLossMessages(
  items: MarginLine[],
  overallDiscount: unknown,
  fallback?: (m: MarginLine) => number,
): string[] {
  const msgs: string[] = []
  for (const m of items) {
    const msg = itemDiscountLoss(m, fallback)
    if (msg) msgs.push(msg)
  }
  const od = overallDiscountLoss(items, overallDiscount, fallback)
  if (od) msgs.push(od)
  return msgs
}

/**
 * Round to the paisa exactly as the engine (Python round) and the Android app (Money.r2) do:
 * the exact binary value is rounded, and an exact half paisa goes to the even paisa.
 * `Math.round(x * 100) / 100` rounds the SCALED value half-up, so 15 x 0.693 (10.394999...)
 * came out 10.40 on the screen where the bill saved 10.39 (shared bill vectors, L1).
 */
export function pyRound2(x: number): number {
  if (!Number.isFinite(x)) return 0
  const neg = x < 0
  const exact = Math.abs(x).toFixed(100)
  const dot = exact.indexOf('.')
  const ip = exact.slice(0, dot)
  const fp = exact.slice(dot + 1)
  const rest = fp.slice(2)
  let cents = Number(ip) * 100 + Number(fp.slice(0, 2))
  const tie = rest[0] === '5' && /^0*$/.test(rest.slice(1))
  const above = rest[0] > '5' || (rest[0] === '5' && !tie)
  if (above || (tie && cents % 2 === 1)) cents += 1
  const r = cents / 100
  return neg ? -r : r
}
