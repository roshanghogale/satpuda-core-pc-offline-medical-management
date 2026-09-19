/** Which party a typed name means.
 *
 *  The same ordering rule as core/payments/PartyNamePick.kt on Android, which
 *  was written when the Payment and Ledger party fields there stopped being
 *  read-only pickers over the WHOLE list -- 1778 customers on the owner's store
 *  -- and became search fields. A search field hands back TEXT, so the row the
 *  outstanding-due figure comes from has to be recovered from it. The `<select>`
 *  these fields used to be never had to: it carried the id on the option.
 *
 *  Duplicate names are real in this shop's data (two CHANDU customers). When the
 *  text alone cannot separate them the row carrying the balance is the one the
 *  shop means; ties go to the lowest id so the answer does not wander between
 *  two identical rows.
 */

export type PartyChoice = {
  id: number
  name: string
  due: number
}

/**
 * The party `typed` names, or null while the text names nobody yet -- which is
 * every keystroke of a half-typed name.
 *
 * `pickedId` is the row the shop actually clicked, when there was one. It wins
 * over the balance rule: picking the second CHANDU has to go on showing the
 * second CHANDU's due, exactly as the old `<select>` did. Callers must drop it
 * as soon as the text is edited by hand, or a stale pick outlives the name it
 * belonged to.
 */
export function resolveParty<T extends PartyChoice>(
  parties: readonly T[],
  typed: string,
  pickedId?: number | null,
): T | null {
  const key = typed.trim().toLowerCase()
  if (!key) return null
  const hits = parties.filter(
    (p) => (p.name || '').trim().toLowerCase() === key,
  )
  if (!hits.length) return null
  if (pickedId != null) {
    const clicked = hits.find((p) => p.id === pickedId)
    if (clicked) return clicked
  }
  return hits
    .slice()
    .sort((a, b) => (b.due || 0) - (a.due || 0) || a.id - b.id)[0]
}
