/** Which recorded phrase (phrases.json key) goes with a reply, and which sound.
 *
 * Pages answer with a screen line that carries names and amounts; only the
 * fixed part is said aloud. The key is picked from the command (intent, args)
 * and, where a page decided the outcome, from the words of its answer.
 */
import type { VoiceCommand } from './voiceClient'
import type { VoiceResult } from './voiceBus'
import { presetRange } from './voiceBus'
import { SETTINGS_TABS } from '../pages/settings/settingsConfig'
import type { Cue } from './sayClip'

/** `skipMissing`: a clip not recorded yet is left out and the rest still play (spoken amounts). */
export type Said = { key: string | string[]; cue?: Cue; skipMissing?: boolean }

export const pageKey = (page: string) => `page_${page}`

/** The phrase for a Settings tab / section, resolved the way SettingsPage resolves it. */
export function settingsKey(tab: unknown, section: unknown): string | null {
  const wantTab = String(tab || '').trim().toLowerCase()
  const wantSec = String(section || '').trim().toLowerCase()
  const has = (t: (typeof SETTINGS_TABS)[number], id: string) =>
    Boolean(
      t.sections?.some((x) => x.id === id) ||
        t.toggles?.some((x) => x.id === id) ||
        t.nestedTabs?.some((x) => x.id === id),
    )
  const t =
    SETTINGS_TABS.find((x) => x.id === wantTab) ||
    (!wantTab && wantSec ? SETTINGS_TABS.find((x) => has(x, wantSec)) : undefined)
  if (!t) return null
  if (wantSec && has(t, wantSec)) return `settings_${t.id}_${wantSec}`
  return `settings_${t.id}`
}

// ── Amounts said aloud: "बारा हजार तीनशे चाळीस रुपये" ──
// Marathi numbers below a hundred are each their own word (num_1..num_99); the rest is
// built the Indian way: crore, lakh, hazar, then the hundreds ("तीनशे", "एकशे", "शंभर").

/** 1..99 as its clip; 0 or less as nothing. */
const below100 = (n: number): string[] => (n > 0 ? [`num_${n}`] : [])

/** The clips for a whole number: 12340 -> num_12, num_1000, num_300, num_40. */
export function numberKeys(value: number): string[] {
  let n = Math.round(Math.abs(Number(value) || 0))
  if (!n) return ['num_0']
  const out: string[] = []
  const crore = Math.floor(n / 10000000)
  if (crore) out.push(...(crore < 100 ? below100(crore) : numberKeys(crore)), 'num_crore')
  n %= 10000000
  const lakh = Math.floor(n / 100000)
  if (lakh) out.push(...below100(lakh), 'num_lakh')
  n %= 100000
  const thousand = Math.floor(n / 1000)
  if (thousand) out.push(...below100(thousand), 'num_1000')
  n %= 1000
  const hundred = Math.floor(n / 100)
  const rest = n % 100
  // 100 alone is "शंभर"; 140 is "एकशे चाळीस"; 300 is "तीनशे".
  if (hundred === 1) out.push(rest ? 'num_1xx' : 'num_100')
  else if (hundred) out.push(`num_${hundred}00`)
  out.push(...below100(rest))
  return out
}

/** An amount in rupees (paise dropped): the number, then "रुपये". */
export function amountKeys(rupees: number): string[] {
  return [...numberKeys(rupees), 'rupaye']
}

/** "N paryay aahet, number sanga". */
export function optionsKey(n: number): string {
  if (n <= 1) return 'options_1'
  return n <= 8 ? `options_${n}` : 'options_many'
}

const CONFIRM_KEYS: Record<string, string> = {
  save_bill: 'confirm_save_bill',
  print_bill: 'confirm_print_bill',
  new_bill: 'confirm_new_bill',
  clear_bill: 'confirm_clear_bill',
  add_medicine: 'confirm_add_medicine',
  one_more: 'confirm_add_medicine',
  remove_medicine: 'confirm_remove',
  remove_last: 'confirm_remove',
  remove_line: 'confirm_remove',
  set_customer: 'confirm_customer',
  set_doctor: 'confirm_doctor',
  discount: 'confirm_discount',
  cash: 'confirm_payment',
  online: 'confirm_payment',
  udhari: 'confirm_udhari',
  navigate: 'confirm_navigate',
}
export const confirmKey = (intent: string) => CONFIRM_KEYS[intent] || 'confirm_generic'

const ACTIONS = new Set([
  'recent_bills', 'tools', 'sales_return', 'add_no_stock', 'new_tab', 'gst_slab', 'import_bill',
  'export', 'expired_return', 'refresh', 'medicine_details', 'print_all', 'bill_details',
  'bill_preview', 'load_bulk',
])

/** A filter the page applied, from what it set (its answer names the option it picked). */
function filterKey(a: Record<string, any>, say: string): string {
  const f = String(a.filter || '')
  const s = say.toLowerCase()
  const all = /:\s*all\s*$/i.test(say)
  if (f === 'clear') {
    if (/ledger/.test(s)) return 'ledger_reset'
    if (/shodh/.test(s)) return 'search_cleared'
    return 'filters_cleared'
  }
  if (f === 'stock') {
    if (/out of stock/.test(s)) return 'filter_out_of_stock'
    if (/low stock/.test(s)) return 'filter_low_stock'
    if (/in stock/.test(s)) return 'filter_in_stock'
    return 'filter_all_stock'
  }
  if (f === 'expiry') {
    if (/near expiry/.test(s)) return 'filter_near_expiry'
    if (/expired/.test(s)) return 'filter_expired'
    return 'filter_all_expiry'
  }
  if (f === 'type') return all ? 'filter_type_all' : 'filter_type_set'
  if (f === 'schedule') return all ? 'filter_schedule_all' : 'filter_schedule_set'
  if (f === 'sort') return 'sort_set'
  if (f === 'range') {
    const k = presetRange(a.value)?.key
    return k === 'all' || !k ? 'filter_year' : `filter_${k}`
  }
  if (f === 'due') {
    if (/due only/.test(s)) return 'filter_due_only'
    if (/credit only/.test(s)) return 'filter_credit_only'
    if (/paid/.test(s)) return 'filter_paid'
    return 'filter_due_all'
  }
  if (f === 'tab') {
    if (/bulk/.test(s)) return 'returns_tab_bulk'
    if (/write-off/.test(s)) return 'returns_tab_disposal'
    if (/purchase return/.test(s)) return 'returns_tab_purchase'
    return 'returns_tab_sales'
  }
  if (f === 'disposal_mode') return /purchase return/.test(s) ? 'mode_return' : 'mode_writeoff'
  if (f === 'refund') {
    if (/khatyat/.test(s)) return 'refund_ledger'
    if (/online/.test(s)) return 'refund_online'
    return 'refund_cash'
  }
  if (f === 'party') return 'ledger_party_set'
  return 'action_done'
}

/** What a page said it could not do, by the words of its answer. First match wins. */
const FAILS: [RegExp, string, Cue][] = [
  [/stock nahi/i, 'stock_nahi', 'warn'],
  [/bill madhe nahi$/i, 'not_in_bill', 'warn'],
  [/line nahit$/i, 'choice_out_of_range', 'warn'],
  [/kadhayla kahich nahi/i, 'nothing_to_remove', 'warn'],
  [/tari save karu/i, 'confirm_save_bill', 'warn'],
  [/kahich nahi|rikama/i, 'bill_empty', 'warn'],
  [/save zala nahi/i, 'bill_not_saved', 'error'],
  [/grahakache naav/i, 'say_customer_name', 'warn'],
  [/discount kiti/i, 'say_discount_amount', 'warn'],
  [/ledger\? naav/i, 'say_ledger_name', 'warn'],
  [/voice var nahi/i, 'action_not_possible', 'warn'],
  [/divas samajle nahi/i, 'date_not_understood', 'error'],
  [/^parat kasa/i, 'refund_not_understood', 'error'],
  [/prakar ya dukanat nahi/i, 'type_not_found', 'warn'],
  [/^schedule .* yaadit nahi/i, 'schedule_not_found', 'warn'],
  [/^sort /i, 'sort_not_found', 'warn'],
  [/tab nahi$/i, 'tab_not_found', 'warn'],
  [/^mode /i, 'mode_not_found', 'warn'],
  [/filter .*nahi$/i, 'filter_not_found', 'warn'],
  [/inventory madhe nahi|yaadit aushadh nahi/i, 'medicine_not_found', 'warn'],
  [/yaadit bill nahi/i, 'bill_not_found', 'warn'],
  [/import chalu/i, 'import_running', 'warn'],
  [/vibhag nahi|^settings madhe/i, 'settings_not_found', 'warn'],
]

/** The phrase (and sound) for a page's answer to `cmd`. `r` null: the page is not there yet. */
export function resultSaid(cmd: VoiceCommand, r: VoiceResult | null): Said {
  if (!r) return { key: 'page_not_ready', cue: 'warn' }
  const a = cmd.args || {}
  const say = String(r.say || '')
  if (!r.ok) {
    for (const [re, key, cue] of FAILS) if (re.test(say)) return { key, cue }
    if (cmd.intent === 'add_medicine' || cmd.intent === 'one_more') return { key: 'medicine_not_added', cue: 'warn' }
    return { key: 'work_failed', cue: 'warn' }
  }
  switch (cmd.intent) {
    case 'new_bill':
      return { key: /tab/i.test(say) ? 'new_bill_tab' : 'new_bill' }
    case 'add_medicine':
      return { key: 'medicine_added' }
    case 'one_more':
      return { key: 'one_more_added' }
    case 'remove_medicine':
    case 'remove_last':
    case 'remove_line':
      return { key: 'medicine_removed' }
    case 'set_customer':
      return { key: a.new ? 'customer_new_set' : 'customer_set' }
    case 'set_doctor':
      return { key: a.new ? 'doctor_new_set' : 'doctor_set' }
    case 'discount':
      return { key: 'discount_given' }
    case 'cash':
    case 'online': {
      const zero = a.amount != null && a.amount !== '' && Number(a.amount) === 0
      return { key: `${cmd.intent}_${zero ? 'cleared' : 'set'}` }
    }
    case 'udhari':
      return { key: a.keep_paid ? 'rest_udhari' : 'udhari_set' }
    case 'save_bill':
      return { key: 'bill_saved' }
    case 'clear_bill':
      return { key: 'bill_cleared' }
    case 'print_bill':
      return { key: 'print_sent' }
    case 'search':
      return { key: /ledger/i.test(say) ? 'ledger_party_set' : 'search_done' }
    case 'open_settings':
      return { key: settingsKey(a.tab, a.section) || 'page_settings' }
    case 'inventory_filter':
    case 'page_filter':
      return { key: filterKey(a, say) }
    case 'page_action': {
      const act = String(a.action || '')
      if (act === 'refresh' && /ledger/i.test(say)) return { key: 'ledger_refreshed' }
      return { key: ACTIONS.has(act) ? `action_${act}` : 'action_done' }
    }
    default:
      return { key: 'action_done' }
  }
}
