/** How every expiry box in the app behaves.
 *
 * The Purchase page has put the slash in for you since the beginning. The
 * phone loader did not, so shops typed `0428` there, and the engine -- which
 * tested for a "/" -- dropped the date on the floor: the medicine imported and
 * its expiry column was empty. core/expiry_text.py now reads whatever gets
 * through; this is the other half, so no box in the product asks a shop to
 * type punctuation the field can type itself.
 *
 * Everything the boxes produce is MM/YY, which is what the engine's
 * expiry_display returns and what every label in the product asks for.
 */

/** What the box shows after each keystroke. `deleting` lets a backspace past
 *  the slash actually remove it instead of putting it straight back. */
export function formatExpiryMmYy(text: string, deleting = false): string {
  const t = (text || '').trim()
  const iso = t.match(/^(\d{4})-(\d{2})/)
  if (iso) return `${iso[2]}/${iso[1].slice(2)}`
  const raw = t.replace(/\D/g, '').slice(0, 4)
  if (!raw) return ''
  if (raw.length === 1) return raw
  if (raw.length === 2) return deleting ? raw : `${raw}/`
  return `${raw.slice(0, 2)}/${raw.slice(2)}`
}

/** "" when the text names a month and a year; otherwise what is wrong with it. */
export function expiryProblem(text: string): string {
  const t = (text || '').trim()
  if (!t) return ''
  const m = formatExpiryMmYy(t).match(/^(\d{2})\/(\d{2})$/)
  if (!m) return 'Expiry is MM/YY, e.g. 04/28'
  const month = Number(m[1])
  return month >= 1 && month <= 12 ? '' : 'Expiry month must be 01-12'
}
