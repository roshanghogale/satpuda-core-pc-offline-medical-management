/**
 * Line rules shared by the Sales and Purchase screens (owner, 2026-09-11).
 *
 * * PURCHASE: the same name AND the same batch is one line (qty added); a
 *   different batch or name stays a separate line.
 * * EDIT AFTER A RETURN: a saved bill opened for edit carries what was already
 *   returned per medicine. No line may go below that, nor be removed -- the
 *   refund was paid on those units. The engine refuses the save too; this says
 *   so at the keystroke, in plain words.
 */

/** Same name and same batch, ignoring case and spaces at the ends. */
export function samePurchaseLine(
  a: { name?: string; medicine?: string; batch?: string },
  b: { name?: string; medicine?: string; batch?: string },
): boolean {
  const n = (x: { name?: string; medicine?: string }) =>
    String(x.name || x.medicine || '').trim().toUpperCase()
  const bt = (x: { batch?: string }) => String(x.batch || '').trim().toUpperCase()
  return n(a) !== '' && n(a) === n(b) && bt(a) === bt(b)
}

/** Why these lines cannot stand on a bill that has returns against it, or
 *  null. `idOf` reads a line's medicine id. */
export function belowReturnedProblem<T>(
  lines: T[],
  returnedByMed: Record<string, number> | undefined,
  idOf: (line: T) => number | null | undefined,
  nameOf: (line: T) => string,
  originalLines?: T[],
): string | null {
  if (!returnedByMed) return null
  const kept: Record<string, number> = {}
  for (const l of lines) {
    const id = Number(idOf(l) || 0)
    if (id > 0) kept[id] = (kept[id] || 0) + (Number((l as { qty?: unknown }).qty) || 0)
  }
  for (const [id, back] of Object.entries(returnedByMed)) {
    const returned = Number(back) || 0
    if (returned <= 0) continue
    const left = kept[id] || 0
    if (left + 1e-6 >= returned) continue
    const named =
      [...lines, ...(originalLines || [])].find((l) => String(idOf(l) || '') === id)
    const label = named ? nameOf(named) : 'This medicine'
    return left <= 0
      ? `${label}: ${returned} already returned against this bill, so its line cannot be removed. Delete that return first if the bill really changed.`
      : `${label}: ${returned} already returned against this bill, so the line cannot go below ${returned}.`
  }
  return null
}
