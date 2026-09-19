/**
 * Strips and tablets side by side on the return screens (owner, 2026-09-11).
 *
 * A strip count may be fractional (2.5 strips of 10 is 25 tablets -- the exact
 * shelf-to-bill conversion, never rounded down to whole strips), but one whole
 * tablet is the smallest unit: 2.55 strips (25.5 tablets) is never offered.
 * The operator sees the tablet count before saving.
 *
 * `unit` is what the quantity counts: a purchase bill (and so a purchase
 * return, Return expired) counts STRIPS; a sale counts a strip-counted
 * medicine in TABLETS.
 */
export type QtyUnit = 'strip' | 'tablet'

function fmt(n: number) {
  return String(Math.round(n * 1000) / 1000)
}

function stripSize(tps: unknown) {
  return Math.max(1, Math.floor(Number(tps) || 1))
}

/** "2.5 strips = 25 tablets" (strips) / "25 tablets = 2.5 strips" (tablets);
 *  '' for anything that is not a strip-counted medicine. */
export function tabletCountNote(
  qty: unknown,
  isTablet: boolean | undefined,
  tps: unknown,
  unit: QtyUnit = 'strip',
): string {
  const q = Number(qty)
  if (!isTablet || !Number.isFinite(q) || q <= 0) return ''
  const t = stripSize(tps)
  if (unit === 'tablet') {
    return t > 1 ? `${fmt(q)} tablets = ${fmt(q / t)} strips` : `${fmt(q)} tablets`
  }
  return `${fmt(q)} ${q === 1 ? 'strip' : 'strips'} = ${fmt(q * t)} tablets`
}

/** A quantity that is not a whole number of tablets, or null. */
export function partTabletProblem(
  name: string,
  qty: unknown,
  isTablet: boolean | undefined,
  tps: unknown,
  unit: QtyUnit = 'strip',
): string | null {
  const q = Number(qty)
  if (!isTablet || !Number.isFinite(q) || q <= 0) return null
  const tablets = unit === 'tablet' ? q : q * stripSize(tps)
  if (Math.abs(tablets - Math.round(tablets)) <= 1e-6) return null
  const shown =
    unit === 'tablet' ? `${fmt(q)} tablets` : `${fmt(q)} strips = ${fmt(tablets)} tablets`
  return `${name || 'This medicine'}: ${shown}. One tablet is the smallest unit — return whole tablets only.`
}
