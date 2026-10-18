/** Rate A on every wholesale purchase line (owner, 18 Oct 2026).
 *
 * A wholesale bill sells at Rate A / B / C, so a purchase line needs at least Rate A. Lines
 * typed by hand cannot be added without it; lines read by Import Bill come in with it empty
 * (a supplier bill prints MRP and its own rate, not the shop's Rate A), are shown red, and
 * Save waits until each has one. The engine refuses the same save (core/wholesale_rates.py,
 * code `rate_a_required`).
 */
import { suggestedRates, type WholesaleMargins } from './mode'

type RatedLine = { name?: string; medicine?: string; rate?: number; rateA?: number; rateB?: number; rateC?: number }

/** A line with a name and no Rate A above zero. */
export function lineNeedsRateA(it: RatedLine): boolean {
  const name = String(it.name ?? it.medicine ?? '').trim()
  return Boolean(name) && !(Number(it.rateA) > 0)
}

/** Indexes of the lines that still need Rate A. */
export function rateAMissing(items: readonly RatedLine[]): number[] {
  const out: number[] = []
  items.forEach((it, i) => {
    if (lineNeedsRateA(it)) out.push(i)
  })
  return out
}

/** "3 lines need Rate A: X, Y, Z" (at most three names, then "and N more"). */
export function rateAMissingText(items: readonly RatedLine[], rows: readonly number[]): string {
  const names = rows.map((i) => String(items[i]?.name ?? items[i]?.medicine ?? '').trim())
  const shown = names.slice(0, 3).join(', ') + (names.length > 3 ? ` and ${names.length - 3} more` : '')
  return `${rows.length} line${rows.length === 1 ? '' : 's'} need${rows.length === 1 ? 's' : ''} Rate A: ${shown}.`
}

/** The quick action: every line without Rate A gets Rate A / B / C = its purchase rate + the
 *  shop's margins. A rate already on a line is kept; a line with no purchase rate is left. */
export function fillRatesFromMargins<T extends RatedLine>(
  items: readonly T[],
  margins: WholesaleMargins,
): { items: T[]; filled: number; noRate: number } {
  let filled = 0
  let noRate = 0
  const next = items.map((it) => {
    if (!lineNeedsRateA(it)) return it
    const pr = Number(it.rate) || 0
    if (pr <= 0) {
      noRate += 1
      return it
    }
    const s = suggestedRates(pr, margins)
    filled += 1
    return {
      ...it,
      rateA: s.rate_a || undefined,
      rateB: Number(it.rateB) > 0 ? it.rateB : s.rate_b || undefined,
      rateC: Number(it.rateC) > 0 ? it.rateC : s.rate_c || undefined,
    }
  })
  return { items: next, filled, noRate }
}
