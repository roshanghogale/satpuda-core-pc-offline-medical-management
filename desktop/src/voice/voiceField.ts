/** Dropdowns and search boxes the voice bar can drive by name.
 *
 * A field says what it is with `data-voice-field="customer"` on its input (a
 * space-separated list: "customer search" answers to both). Its component puts
 * `data-voice-wrap` on the element that holds the input and its list, and
 * `data-voice-label` on every row of the list (the text the row stands for).
 *
 * The bar focuses the input, asks the component to open its list with
 * VOICE_OPEN_EVENT (voice mode keeps lists from opening BY THEMSELVES; this is
 * voice asking), types the way the keyboard would, and picks a row by clicking
 * it -- so a spoken pick is the same pick a mouse makes.
 */

/** Dispatched on a voice field's input: "open your list now". */
export const VOICE_OPEN_EVENT = 'satpuda-voice-open'
/** Dispatched on a voice field's input: "close your list" (what Escape does in the field,
 *  without an Escape key that a popup or the page's own key handlers would also see). */
export const VOICE_CLOSE_EVENT = 'satpuda-voice-close'

export const VOICE_FIELDS = [
  'customer', 'doctor', 'medicine', 'supplier', 'search', 'village', 'address', 'customer_phone', 'doctor_phone',
] as const

/** Dispatched on window when a doctor / customer is chosen (mouse, keyboard or voice): {field, name}. */
export const VOICE_PICKED_EVENT = 'satpuda-voice-picked'

/** A page says "this doctor / customer was just chosen" -- a medicine held back for one goes on. */
export function voicePicked(field: 'doctor' | 'customer', name: string) {
  window.dispatchEvent(new CustomEvent(VOICE_PICKED_EVENT, { detail: { field, name } }))
}

const MAX_BADGES = 30
const MAX_OPTIONS = 60

type FieldEl = HTMLInputElement | HTMLTextAreaElement

function shown(el: HTMLElement): boolean {
  if (!el.getClientRects().length) return false
  const st = window.getComputedStyle(el)
  return st.visibility !== 'hidden' && st.display !== 'none'
}

function usable(el: Element): el is FieldEl {
  return (
    (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) &&
    !el.disabled &&
    !el.readOnly &&
    !el.closest('.vb-panel') &&
    shown(el)
  )
}

/** The field called `name` on the page being shown (hidden pages stay mounted but have no box). */
export function findVoiceField(name: string): FieldEl | null {
  const want = String(name || '').trim().toLowerCase()
  if (!want) return null
  const found = Array.from(document.querySelectorAll(`[data-voice-field~="${CSS.escape(want)}"]`)).filter(usable)
  if (found.length) return found[0]
  // A page's main filter box is its search box.
  if (want === 'search') {
    const primary = Array.from(document.querySelectorAll('[data-page-filter="primary"]')).filter(usable)
    if (primary.length) return primary[0]
  }
  return null
}

/** Focus the field and ask its list to open. */
export function openVoiceField(el: FieldEl) {
  el.focus()
  el.dispatchEvent(new CustomEvent(VOICE_OPEN_EVENT, { bubbles: false }))
}

/** Set the text the React way, so the component's onChange runs and its list filters. */
export function setFieldText(el: FieldEl, text: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  if (setter) setter.call(el, text)
  else el.value = text
  el.dispatchEvent(new Event('input', { bubbles: true }))
  try {
    el.setSelectionRange(text.length, text.length)
  } catch {
    /* some inputs have no caret */
  }
}

function wrapOf(el: FieldEl): HTMLElement | null {
  return el.closest<HTMLElement>('[data-voice-wrap]')
}

/** The rows of the field's open list, in screen order. */
export function optionEls(el: FieldEl): HTMLElement[] {
  const wrap = wrapOf(el)
  if (!wrap) return []
  return Array.from(wrap.querySelectorAll<HTMLElement>('[data-voice-label]')).filter(shown)
}

export function optionText(o: HTMLElement): string {
  return (o.getAttribute('data-voice-label') || o.textContent || '').replace(/\s+/g, ' ').trim()
}

/** What the list shows now: at most 60 texts, in order. */
export function fieldOptions(el: FieldEl): string[] {
  return optionEls(el).slice(0, MAX_OPTIONS).map(optionText)
}

/** The shortcut number a row stands for (a voice-opened list with an empty box puts
 *  its numbered shortcuts first and marks them with data-voice-shortcut). */
export function rowShortcut(o: HTMLElement): number | null {
  const v = o.getAttribute('data-voice-shortcut')
  const n = v == null || v === '' ? NaN : Number(v)
  return Number.isFinite(n) && n >= 1 ? n : null
}

/** True when the open list is numbered by shortcut, not by position. */
export function shortcutNumbered(el: FieldEl | null): boolean {
  return !!el && optionEls(el).some((o) => rowShortcut(o) != null)
}

/** Put a "1", "2"... badge on each row (CSS draws it from data-voice-n). A list showing
 *  its shortcuts first badges only those rows, each with ITS shortcut number; the rows
 *  below them have no number (they are picked by name). */
export function numberOptions(el: FieldEl | null) {
  clearOptionNumbers()
  if (!el) return
  const rows = optionEls(el)
  if (rows.some((o) => rowShortcut(o) != null)) {
    rows.forEach((o) => {
      const n = rowShortcut(o)
      if (n != null) o.setAttribute('data-voice-n', String(n))
    })
    return
  }
  rows.slice(0, MAX_BADGES).forEach((o, i) => o.setAttribute('data-voice-n', String(i + 1)))
}

export function clearOptionNumbers() {
  document.querySelectorAll('[data-voice-n]').forEach((o) => o.removeAttribute('data-voice-n'))
}

/** Lower-case words; "Doctor" / "Dr." are the same word, as on the doctor list. */
const simple = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9ऀ-ॿ]+/g, ' ')
    .replace(/\b(doctor|dr)\b/g, 'dr')
    .replace(/\s+/g, ' ')
    .trim()

const TITLES = new Set(['dr', 'sir', 'madam', 'mam', 'saheb', 'sahab', 'bhau', 'tai'])

/** The row whose text is exactly `text` (case and spaces ignored), or null. */
export function findExactOption(el: FieldEl, text: string): HTMLElement | null {
  const want = String(text || '').trim().replace(/\s+/g, ' ').toUpperCase()
  if (!want) return null
  return optionEls(el).find((o) => optionText(o).toUpperCase() === want) || null
}

/** The row a spoken pick means: number n, or the text: exact, then starts-with, then contains.
 *  n is the row's shortcut number when the list is numbered by shortcut, else its position (1-based). */
export function findOption(el: FieldEl, pick: { n?: number; text?: string }): HTMLElement | null {
  const rows = optionEls(el)
  const n = Number(pick.n)
  if (Number.isFinite(n) && n >= 1) {
    const k = Math.round(n)
    if (rows.some((o) => rowShortcut(o) != null)) return rows.find((o) => rowShortcut(o) === k) || null
    return rows[k - 1] || null
  }
  const want = simple(String(pick.text || ''))
  if (!want) return null
  const texts = rows.map((o) => simple(optionText(o)))
  const tight = (s: string) => s.replace(/ /g, '')
  let i = texts.findIndex((t) => t === want)
  if (i < 0) i = texts.findIndex((t) => tight(t) === tight(want))
  if (i < 0) i = texts.findIndex((t) => t.startsWith(want))
  if (i < 0) i = texts.findIndex((t) => tight(t).startsWith(tight(want)))
  if (i < 0) i = texts.findIndex((t) => t.includes(want))
  if (i < 0) i = texts.findIndex((t) => tight(t).includes(tight(want)))
  if (i < 0) {
    // Every spoken word somewhere in the row: "joshi sir" finds "DR JOSHI SIR".
    const ws = want.split(' ').filter((w) => w.length > 1)
    if (ws.length) i = texts.findIndex((t) => ws.every((w) => t.includes(w)))
    // Then without the titles, which a list may spell another way or leave out.
    const core = ws.filter((w) => !TITLES.has(w))
    if (i < 0 && core.length && core.length < ws.length) i = texts.findIndex((t) => core.every((w) => t.includes(w)))
  }
  return i >= 0 ? rows[i] : null
}

/** Close the list and leave the field (the Escape-and-blur of the keyboard). */
export function closeVoiceField(el: FieldEl | null) {
  if (!el) return
  el.dispatchEvent(new CustomEvent(VOICE_CLOSE_EVENT, { bubbles: false }))
  if (document.activeElement === el) el.blur()
  clearOptionNumbers()
}
