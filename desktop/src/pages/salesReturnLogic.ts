/**
 * The money rules of a sales return, in one place.
 *
 * Returns -> Sales and the Sales Return popup on the Sales page both use these,
 * so the two screens cannot drift apart. These are moved out of ReturnsPage.tsx
 * without changing them: the quantity limit, the discount as a PERCENTAGE, the
 * refund estimate and the settle wording are the ones that screen already had
 * right.
 */
import type { LoadedSalesReturnBill, ReturnLineItem } from '../pagesApi'
import { partTabletProblem } from './tabletCount'

export type RefundSettle = 'ledger' | 'cash' | 'online'

export type SalesReturnLine = {
  medicine_id: number
  name: string
  batch: string
  qty: number
  rate: number
  amount: number
  orig_qty?: number
  orig_amount?: number
  type?: string
  is_tablet?: boolean
  tablets_per_stripe?: number
}

export type SalesReturnAlert = { title: string; message: string }

export function returnMoney(n: number) {
  return `₹${n.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function parseBillSearch(q: string) {
  let s = (q || '').trim()
  if (!s) return ''
  for (const sep of [' — ', ' – ', ' --- ', ' -- ', ' - ']) {
    if (s.includes(sep)) {
      s = s.split(sep)[0]!.trim()
      break
    }
  }
  const paren = s.lastIndexOf('(')
  if (paren > 0 && s.endsWith(')')) s = s.slice(0, paren).trim()
  return s
}

export function matchLabeledBill<T extends { label: string }>(
  bills: T[],
  query: string,
): T | undefined {
  const q = query.trim().toLowerCase()
  if (!q) return undefined
  const exact = bills.find((b) => b.label.toLowerCase() === q)
  if (exact) return exact
  const parsed = parseBillSearch(query).toLowerCase()
  if (!parsed) return undefined
  // The bill NUMBER first. The list is newest first, so a prefix test in the
  // same pass as the number test loaded bill 123 for "12" whenever 123 was the
  // newer bill -- a refund against the wrong bill, and the wrong customer.
  const byNumber = bills.find((b) => parseBillSearch(b.label).toLowerCase() === parsed)
  if (byNumber) return byNumber
  // A prefix only when it names one bill; with two candidates it is a guess.
  const prefixed = bills.filter((b) => b.label.toLowerCase().startsWith(parsed))
  return prefixed.length === 1 ? prefixed[0] : undefined
}

/** A bill's rupee discount as the percentage of the bill it actually was.
 *  The return screens work in percent; bills store rupees. */
export function discountAsPct(discountRs: unknown, billTotal: unknown): number {
  const rs = Number(discountRs) || 0
  const total = Number(billTotal) || 0
  if (rs <= 0) return 0
  // bill_total is what remained AFTER the discount, so the gross is the sum.
  const gross = total + rs
  if (gross <= 0) return 0
  return Math.round((rs / gross) * 10000) / 100
}

/** What is still returnable on a bill line: the engine's remaining (after
 *  earlier saved returns) less what is already on this return for the same
 *  medicine -- across every line of it, not just the one picked. */
export function salesRemainingQty(
  lines: SalesReturnLine[],
  medicineId: number,
  baseRemaining: number,
) {
  const used = lines
    .filter((r) => r.medicine_id === medicineId)
    .reduce((s, r) => s + r.qty, 0)
  return Math.max(0, baseRemaining - used)
}

export function salesRefundPreview(lines: SalesReturnLine[], discPct: unknown) {
  const subtotal = lines.reduce((sum, it) => {
    const origQty = Number(it.orig_qty) || 0
    const origAmt = Number(it.orig_amount) || 0
    const rate = origQty > 0 && origAmt > 0 ? origAmt / origQty : it.rate || 0
    return sum + it.qty * rate
  }, 0)
  const pct = Number(discPct) || 0
  return Math.max(0, Math.round((subtotal - (subtotal * pct) / 100) * 100) / 100)
}

export function salesSettleHint(
  refund: number,
  previousDue: number,
  settle: RefundSettle,
): string {
  const money = returnMoney
  const due = Number(previousDue || 0)
  const dueCut = Math.min(due, refund)
  const payout = Math.max(0, refund - dueCut)
  if (!refund) return ''
  if (settle === 'ledger') {
    if (dueCut && payout) {
      return `${money(dueCut)} reduces customer due; ${money(payout)} stays as credit.`
    }
    if (dueCut) return `${money(dueCut)} will reduce this customer’s due. Stock goes back to inventory.`
    return `${money(refund)} will be added as customer credit. Stock goes back to inventory.`
  }
  if (payout > 0 && dueCut > 0) {
    return `Give ${money(payout)} ${settle} now. ${money(dueCut)} still reduces due. Stock goes back.`
  }
  if (payout > 0) {
    return `Give ${money(payout)} ${settle} now. Stock goes back to inventory.`
  }
  return `Customer still has due covering this return — ${money(dueCut)} reduces due. No cash to give.`
}

export type SalesReturnAdd =
  | { ok: true; line: SalesReturnLine }
  | { ok: false; alert: SalesReturnAlert }

/** Add one medicine to the return: same checks, same order, same wording as
 *  the Returns tab has always used. */
export function checkSalesReturnAdd(
  bill: LoadedSalesReturnBill | null,
  lines: SalesReturnLine[],
  pickId: number | null,
  pickName: string,
  qtyText: string,
): SalesReturnAdd {
  if (!bill?.items?.length) {
    return { ok: false, alert: { title: 'Load bill', message: 'Load a sales bill first.' } }
  }
  const name = pickName.trim()
  const item: ReturnLineItem | undefined =
    (pickId != null
      ? bill.items.find((it) => Number(it.medicine_id) === pickId)
      : undefined) ||
    bill.items.find((it) => it.name.toLowerCase() === name.toLowerCase())
  if (!item) {
    return {
      ok: false,
      alert: { title: 'Medicine', message: 'Pick a medicine from the loaded bill.' },
    }
  }
  const qty = Number(qtyText) || 0
  const remaining = salesRemainingQty(lines, item.medicine_id, item.remaining_qty)
  if (qty <= 0) {
    return { ok: false, alert: { title: 'Return qty', message: 'Enter a valid return quantity.' } }
  }
  if (qty > remaining) {
    return {
      ok: false,
      alert: {
        title: 'Return qty',
        message: `Cannot return more than ${remaining} for ${item.name}.`,
      },
    }
  }
  // A sale counts a strip-counted medicine in tablets: whole tablets only.
  const part = partTabletProblem(item.name, qty, item.is_tablet, item.tablets_per_stripe, 'tablet')
  if (part) {
    return { ok: false, alert: { title: 'Return qty', message: part } }
  }
  if (lines.some((r) => r.medicine_id === item.medicine_id)) {
    return {
      ok: false,
      alert: {
        title: 'Duplicate',
        message: `${item.name} is already in the return list. Remove it first to change qty.`,
      },
    }
  }
  const amount = Math.round(qty * (item.rate || 0) * 100) / 100
  return {
    ok: true,
    line: {
      medicine_id: item.medicine_id,
      name: item.name,
      batch: item.batch,
      qty,
      rate: item.rate || 0,
      amount,
      orig_qty: item.orig_qty,
      orig_amount: item.amount,
      type: item.type,
      is_tablet: item.is_tablet,
      tablets_per_stripe: item.tablets_per_stripe,
    },
  }
}

export function checkSalesReturnSave(
  bill: LoadedSalesReturnBill | null,
  lines: SalesReturnLine[],
  discPct: unknown,
): SalesReturnAlert | null {
  if (!bill?.sale_id) {
    return { title: 'Load bill', message: 'Search and load a sales bill first.' }
  }
  if (!lines.length) {
    return { title: 'Return qty', message: 'Enter return quantity for at least one item.' }
  }
  // The engine takes this box as a percentage and does not clamp it: -50 paid
  // back 150% of the goods, and 150 saved a NEGATIVE refund that added to the
  // customer's due -- while the estimate here showed a harmless figure.
  const raw = String(discPct ?? '').trim()
  const pct = raw === '' ? 0 : Number(raw)
  if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
    return { title: 'Discount %', message: 'Discount % must be between 0 and 100.' }
  }
  return null
}

/** The save request. `discount` is the PERCENTAGE box, as the engine reads it. */
export function salesReturnBody(
  bill: LoadedSalesReturnBill,
  lines: SalesReturnLine[],
  discPct: unknown,
  reason: string,
  settle: RefundSettle,
) {
  return {
    sale_id: bill.sale_id,
    customer_id: bill.customer_id,
    customer_name: bill.customer || '',
    bill_no: bill.bill_no || '',
    items: lines.map((it) => ({
      medicine_id: it.medicine_id,
      name: it.name,
      qty: it.qty,
      rate: it.rate,
      orig_qty: it.orig_qty,
      orig_amount: it.orig_amount,
      type: it.type,
      is_tablet: it.is_tablet,
    })),
    discount: Number(discPct) || 0,
    reason: reason.trim(),
    settle_mode: settle,
  }
}

export function salesReturnSavedMessage(
  res: {
    return_no?: string
    refund_amount?: number
    refund_payout?: number
    settle_mode?: string
  },
  settleAsked: RefundSettle,
) {
  const money = returnMoney
  const payout = Number(res.refund_payout || 0)
  const settle = String(res.settle_mode || settleAsked)
  let msg = `Saved ${res.return_no} — refund ${money(res.refund_amount || 0)}`
  if (settle === 'cash' || settle === 'online') {
    msg += payout ? ` · given ${settle} ${money(payout)}` : ' · due reduced (no cash to give)'
  } else {
    msg += ' · due/credit updated · stock restored'
  }
  return msg
}

export function salesBillInfo(loaded: LoadedSalesReturnBill) {
  const money = returnMoney
  return (
    `Loaded ${loaded.bill_no} — ${loaded.customer} (${loaded.bill_date})` +
    ` | Bill ${money(loaded.bill_total || 0)}` +
    ` | Paid ${money(loaded.bill_paid || 0)}` +
    ` | Bill due ${money(loaded.bill_due || 0)}` +
    ` | Customer due ${money(loaded.previous_due || 0)}` +
    (loaded.previous_credit ? ` | Credit ${money(loaded.previous_credit)}` : '')
  )
}
