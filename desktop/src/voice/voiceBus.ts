/** Where a spoken command goes once it is understood.
 *
 * Every page stays mounted once visited (App keeps them alive), so a page
 * registers ONE handler for as long as it exists and the voice bar hands each
 * command to the page it belongs to. A page answers with a line for the shop
 * to read: what it did, or why it could not.
 *
 * The handler is looked up at call time, never captured, so the page always
 * sees its own latest state.
 */
import type { VoiceCommand } from './voiceClient'
import { DAY_PRESETS } from '../components/DayPresets'

export type VoiceResult = {
  /** One line, in the shop's words: "Dolo 650 × 2 patta jodla". */
  say: string
  ok: boolean
  /** Why it could not, when the app gave a code ("doctor_required", "customer_required"). */
  code?: string
}

export type VoiceHandler = (cmd: VoiceCommand) => Promise<VoiceResult | null>

const handlers = new Map<string, () => VoiceHandler>()

/** Register a page. Pass a GETTER so the page's newest closure is used. */
export function registerVoicePage(page: string, get: () => VoiceHandler): () => void {
  handlers.set(page, get)
  return () => {
    if (handlers.get(page) === get) handlers.delete(page)
  }
}

export async function sendToPage(page: string, cmd: VoiceCommand): Promise<VoiceResult | null> {
  const get = handlers.get(page)
  if (!get) return null
  return get()(cmd)
}

export function pageIsListening(page: string): boolean {
  return handlers.has(page)
}

/** Which page each intent belongs to. Global ones are handled by the bar. */
export const INTENT_PAGE: Record<string, string> = {
  new_bill: 'sales',
  clear_bill: 'sales',
  add_medicine: 'sales',
  one_more: 'sales',
  remove_last: 'sales',
  remove_medicine: 'sales',
  remove_line: 'sales',
  set_customer: 'sales',
  set_doctor: 'sales',
  discount: 'sales',
  cash: 'sales',
  online: 'sales',
  udhari: 'sales',
  save_bill: 'sales',
  print_bill: 'sales',
  inventory_filter: 'inventory',
  give_regulars: 'sales',
}

/** Commands that change the open SALE bill. Said on any page but Sales or Home they
 *  are refused, never carried to Sales behind the shop's back. */
export const SALE_BILL_INTENTS = new Set([
  'add_medicine', 'one_more', 'remove_last', 'remove_medicine', 'remove_line', 'save_bill', 'clear_bill',
  'print_bill', 'cash', 'online', 'discount', 'udhari', 'set_customer', 'set_doctor', 'give_regulars',
])

/** Commands a page with its own form answers itself, before the Sales routing above. */
export const PAGE_INTENTS: Record<string, readonly string[]> = {
  returns: ['add_medicine', 'remove_last', 'remove_line', 'remove_medicine', 'save_bill', 'load_bill'],
  payment: ['payment', 'save_bill', 'set_customer'],
  reorder: ['add_medicine'],
}

/** Commands that only one page has: said elsewhere they are refused with its name. */
export const LOCAL_INTENT_PAGE: Record<string, string> = {
  payment: 'payment',
  load_bill: 'returns',
}

// ── Where the shop is, more exactly than the page ──
// A page with tabs says which one is showing: Returns its tab ("sales"), Settings
// "<tab>/<toggle>" ("payment/customer", "reorder/by_supplier"). The bar reads it to
// tell the service where it is and to route a command to the form on screen.
const places = new Map<string, string>()
const placeListeners = new Set<() => void>()

export function setVoicePlace(page: string, where: string) {
  if (places.get(page) === where) return
  places.set(page, where)
  placeListeners.forEach((f) => f())
}

export function voicePlace(page: string): string {
  return places.get(page) || ''
}

export function onVoicePlace(f: () => void): () => void {
  placeListeners.add(f)
  return () => {
    placeListeners.delete(f)
  }
}

// ── Page filters and page actions ("expired dakhav", "aajche bill", "export ughad") ──
//
// page_filter {page, filter, value} and page_action {page, action} name their
// page in the args, so they are routed by args.page rather than by INTENT_PAGE.
// Each page answers them by setting the SAME state its own dropdowns and
// buttons set, so what voice did is exactly what the mouse would have done.

/** Pages whose voice handler lives inside a Settings tab: open that tab first. */
export const SETTINGS_HOSTED: Record<string, { tab: string; section?: string }> = {
  ledger: { tab: 'ledger' },
  alerts: { tab: 'alerts' },
}

/** Lower-case, letters and digits only: "Out of Stock" -> "outofstock". */
export function norm(v: unknown): string {
  return String(v ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')
}

const ALL_WORDS = new Set(['', 'all', 'any', 'none', 'clear', 'sagla', 'sagle', 'sarv', 'sarva', 'kadha'])

/** "All", "sagla", null: the filter is being taken off. */
export function isAllValue(v: unknown): boolean {
  return v == null || ALL_WORDS.has(norm(v))
}

/** The option a spoken value means: exact, then prefix, then contained. */
export function pickOption(value: unknown, options: readonly string[]): string | null {
  const want = norm(value)
  if (!want) return null
  const opts = options.filter(Boolean)
  return (
    opts.find((o) => norm(o) === want) ||
    opts.find((o) => norm(o).startsWith(want)) ||
    opts.find((o) => want.startsWith(norm(o)) && norm(o).length >= 2) ||
    opts.find((o) => norm(o).includes(want)) ||
    null
  )
}

/** Marathi-Latin words for the date presets. */
export const RANGE_WORDS: Record<string, string> = {
  today: 'Aaj',
  yesterday: 'Kaal',
  week: 'Gele 7 divas',
  month: 'Ha mahina',
  all: 'Purna varsh (default)',
}

const RANGE_ALIASES: Record<string, string> = {
  today: 'today', aaj: 'today', aajche: 'today', aajcha: 'today',
  yesterday: 'yesterday', kaal: 'yesterday', kalche: 'yesterday', kalcha: 'yesterday',
  week: 'week', last7days: 'week', '7days': 'week', athavda: 'week', hafta: 'week',
  month: 'month', thismonth: 'month', mahina: 'month', yamahinyache: 'month',
  all: 'all', fy: 'all', year: 'all', varsh: 'all', default: 'all', clear: 'all', '': 'all',
}

/** A spoken date preset as the From/To pair the page's own preset buttons set.
 *  'all' is the empty pair, which the engine answers with the default FY window. */
export function presetRange(value: unknown): { key: string; from: string; to: string } | null {
  const key = RANGE_ALIASES[norm(value)]
  if (!key) return null
  if (key === 'all') return { key, from: '', to: '' }
  const p = DAY_PRESETS.find((x) => x.key === key)
  if (!p) return null
  const [from, to] = p.range()
  return { key, from, to }
}
